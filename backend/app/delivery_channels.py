"""Revocable application-only access. Bearer credentials never authorize state APIs."""
import hashlib
import secrets
from datetime import datetime,timedelta,timezone
from fastapi import APIRouter,Depends,Request
from sqlalchemy import select,func
from .db import Record,Job,bump,revision_lock
from .security import AppError,require,safe_id
from .state import record,put,now,text_field
from .services import uid
from .platform_audit import write_audit

def hash_token(token):return hashlib.sha256(token.encode()).hexdigest()
def public(row):return {k:v for k,v in row.data.items() if k!='tokenHash'}
def authorize(session,request,scope):
    auth=request.headers.get('Authorization','')
    if not auth.startswith('Bearer ') or not 20<=len(auth[7:])<=200:raise AppError('CHANNEL_AUTH','访问凭据无效。',401)
    hashed=hash_token(auth[7:]);row=session.scalar(select(Record).where(Record.collection=='deliveryChannels',Record.data['tokenHash'].astext==hashed))
    if not row or row.data.get('revoked') or row.data['expiresAt']<=now() or scope not in row.data['scopes']:raise AppError('CHANNEL_AUTH','访问凭据已过期、撤销或权限不足。',401)
    visitor=request.headers.get('X-Visitor-ID','')
    if row.data['kind']=='share' and (len(visitor)<32 or len(visitor)>128 or not visitor.isalnum()):raise AppError('VISITOR_REQUIRED','缺少访客会话标识。',400)
    session_id='channel-'+hash_token(row.id+':'+(visitor if row.data['kind']=='share' else 'api'))[:40]
    app=record(session,'platformApps',row.data['appId'])
    if not app or not app.data.get('publishedVersionId'):raise AppError('APP_UNPUBLISHED','应用已下线。',409)
    return row,app,session_id

def router_for(context,session_factory):
    router=APIRouter()
    @router.get('/api/delivery/apps/{app_id}/channels')
    def listing(app_id:str,ctx=Depends(context)):
        require(ctx[0],'manager')
        with session_factory()() as session:return {'items':[public(r) for r in session.scalars(select(Record).where(Record.collection=='deliveryChannels',Record.data['appId'].astext==app_id)).all()]}
    @router.post('/api/delivery/apps/{app_id}/channels')
    def create(app_id:str,body:dict,ctx=Depends(context)):
        require(ctx[0],'manager');kind=body.get('kind','share');days=body.get('days',7);limit=body.get('maxRuns',100)
        if kind not in {'share','api'} or type(days) is not int or not 1<=days<=90 or type(limit) is not int or not 1<=limit<=10000:raise AppError('INVALID_CHANNEL','渠道类型、有效天数或调用限额无效。')
        with session_factory().begin() as session:
            from .platform_apps import get_app
            revision_lock(session);get_app(session,app_id);identifier=uid('channel');token=secrets.token_urlsafe(36)
            value={'id':identifier,'appId':app_id,'name':text_field(body,'name',120,True),'kind':kind,'scopes':['describe','run','read'],'expiresAt':(datetime.now(timezone.utc)+timedelta(days=days)).isoformat(),'createdAt':now(),'revoked':False,'maxRuns':limit,'usedRuns':0,'tokenHash':hash_token(token)}
            put(session,'deliveryChannels',identifier,value);write_audit(session,ctx,'channel.create','application',app_id);bump(session)
        return {'item':{k:v for k,v in value.items() if k!='tokenHash'},'token':token}
    @router.delete('/api/delivery/channels/{channel_id}')
    def revoke(channel_id:str,ctx=Depends(context)):
        require(ctx[0],'manager')
        with session_factory().begin() as session:
            revision_lock(session);row=record(session,'deliveryChannels',safe_id(channel_id))
            if not row:raise AppError('NOT_FOUND','渠道不存在。',404)
            row.data={**row.data,'revoked':True};write_audit(session,ctx,'channel.revoke','application',row.data['appId']);bump(session)
        return {'revoked':True}
    @router.get('/api/channel/application')
    def describe(request:Request):
        with session_factory()() as session:
            _,app,_=authorize(session,request,'describe');version=record(session,'platformVersions',app.data['publishedVersionId']).data
            delivery=version.get('delivery') or {}
            return {'name':version['name'],'description':version['description'],'version':version['number'],'inputFields':delivery.get('inputFields',[]),'opening':delivery.get('opening',''),'suggestions':delivery.get('suggestions',[])}
    @router.post('/api/channel/runs')
    def run(request:Request,body:dict):
        from .platform_apps import create_platform_conversation
        from .worker import create_chat
        with session_factory().begin() as session:
            revision_lock(session);channel,app,sid=authorize(session,request,'run');request_id=safe_id(body.get('requestId'));identifier='channelrun-'+hash_token(sid+':'+request_id)[:32]
            existing=record(session,'deliveryChannelRuns',identifier)
            if existing:return {'id':identifier,'conversationId':existing.data['conversationId']}
            if channel.data['usedRuns']>=channel.data['maxRuns']:raise AppError('CHANNEL_QUOTA','该渠道调用额度已用完。',429)
            active=session.scalar(select(func.count()).select_from(Job).where(Job.session_id==sid,Job.status.in_(['queued','running'])))
            if active>=3:raise AppError('CHANNEL_BUSY','同时最多运行三个请求。',429)
            cid=body.get('conversationId')
            if cid:
                convo=record(session,'conversations',safe_id(cid))
                if not convo or convo.session_id!=sid or convo.data.get('platformAppId')!=app.id:raise AppError('NOT_FOUND','会话不存在。',404)
            else:cid=create_platform_conversation(session,app.id,app.data['publishedVersionId'],sid,'channel')['id']
            create_chat(session,cid,text_field(body,'query',10000,True),False,sid,body.get('inputs'));session.flush();job_id=record(session,'conversations',cid).data['messages'][-1]['runId']
            put(session,'deliveryChannelRuns',identifier,{'id':identifier,'channelId':channel.id,'conversationId':cid,'jobId':job_id},sid)
            channel.data={**channel.data,'usedRuns':channel.data['usedRuns']+1};bump(session)
            return {'id':identifier,'conversationId':cid}
    @router.get('/api/channel/runs/{run_id}')
    def result(run_id:str,request:Request):
        with session_factory()() as session:
            channel,_,sid=authorize(session,request,'read');row=record(session,'deliveryChannelRuns',safe_id(run_id))
            if not row or row.session_id!=sid or row.data['channelId']!=channel.id:raise AppError('NOT_FOUND','运行不存在。',404)
            log=record(session,'logs',row.data['jobId']);job=session.get(Job,row.data['jobId'])
            if not log:raise AppError('NOT_FOUND','运行记录不存在。',404)
            return {'id':run_id,'status':job.status if job else log.data['status'],'output':log.data.get('output',''),'citations':[{k:c.get(k) for k in ('name','location','content')} for c in log.data.get('citations',[])],'error':log.data.get('error')}
    return router
