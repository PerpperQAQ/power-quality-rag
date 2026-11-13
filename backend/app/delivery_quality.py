"""Draft evaluation suites and publication gates tied to exact dependencies."""
import copy
import hashlib
import json
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from .db import Record, Chunk, bump, revision_lock
from .security import AppError, require, safe_id
from .state import record, put, now, text_field
from .services import uid
from .platform_audit import write_audit

class Criteria(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected:str=Field(default='',max_length=10000)
    mustContain:list[str]=Field(default_factory=list,max_length=30)
    forbidden:list[str]=Field(default_factory=list,max_length=30)
    requireCitations:bool=False
    validJson:bool=False
    maxChars:int|None=Field(default=None,ge=1,le=50000)
    minChars:int|None=Field(default=None,ge=1,le=50000)

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()

def fingerprint(session,version):
    config={k:v for k,v in version.items() if k not in {'id','createdAt','number','debug'}}
    kb_ids=version['assistantSnapshot'].get('kbIds',[])
    docs=[r.data for r in session.scalars(select(Record).where(Record.collection=='documents')).all() if r.data.get('kbId') in kb_ids]
    # Document/index revisions and exact chunk contents invalidate old evidence.
    chunks=[]
    for c in session.scalars(select(Chunk).where(Chunk.kb_id.in_(kb_ids))).all():
        chunks.append({col.name:getattr(c,col.name) for col in Chunk.__table__.columns if col.name!='embedding'})
    return digest({'config':config,'documents':sorted(docs,key=lambda d:d['id']),'chunks':sorted(chunks,key=lambda d:str(d.get('id')))})

def score(log,criteria):
    if log.get('status') not in {'completed','no_evidence'}:return None,[]
    output=log.get('output') or '';checks=[]
    def add(name,ok):checks.append({'rule':name,'passed':bool(ok)})
    if criteria.get('expected'):add('参考答案',criteria['expected'].strip() in output)
    for word in criteria.get('mustContain',[]):add('必须包含：'+word,word in output)
    for word in criteria.get('forbidden',[]):add('不得包含：'+word,word not in output)
    if criteria.get('requireCitations'):add('引用来源',bool(log.get('citations')))
    if criteria.get('validJson'):
        try:json.loads(output);valid=True
        except (ValueError,TypeError):valid=False
        add('有效 JSON',valid)
    if criteria.get('maxChars') is not None:add('最大字数',len(output)<=criteria['maxChars'])
    if criteria.get('minChars') is not None:add('最小字数',len(output)>=criteria['minChars'])
    return (all(c['passed'] for c in checks) if checks else None),checks

def suite_fields(body):
    name=text_field(body,'name',120,True);raw=body.get('cases',[])
    if not isinstance(raw,list) or not 1<=len(raw)<=20:raise AppError('INVALID_CASES','评测集需要 1–20 个案例。')
    cases=[]
    for entry in raw:
        if not isinstance(entry,dict):raise AppError('INVALID_CASES','案例格式无效。')
        try:criteria=Criteria.model_validate(entry.get('criteria',{})).model_dump()
        except ValidationError:raise AppError('INVALID_CRITERIA','评测标准格式无效。') from None
        if any(not x.strip() or len(x)>500 for x in criteria['mustContain']+criteria['forbidden']):raise AppError('INVALID_CRITERIA','规则文本需要 1–500 字。')
        if score({'status':'completed','output':''},criteria)[0] is None:raise AppError('INVALID_CRITERIA','每个案例至少设置一项可执行标准。')
        inputs=entry.get('inputs',{})
        if not isinstance(inputs,dict):raise AppError('INVALID_INPUTS','案例输入需要对象。')
        cases.append({'caseId':safe_id(entry['caseId']) if entry.get('caseId') else uid('case'),'query':text_field(entry,'query',10000,True),'inputs':inputs,'criteria':criteria,'expectedAnswer':criteria['expected']})
    if len({c['caseId'] for c in cases})!=len(cases):raise AppError('INVALID_CASES','案例编号不能重复。')
    if type(body.get('gateEnabled',False)) is not bool:raise AppError('INVALID_GATE','门禁开关无效。')
    return {'name':name,'cases':cases,'gateEnabled':body.get('gateEnabled',False)}

def gate_state(session,app_id,version):
    suites=session.scalars(select(Record).where(Record.collection=='deliverySuites',Record.data['appId'].astext==app_id)).all()
    enabled=[r for r in suites if r.data.get('gateEnabled')];current=fingerprint(session,version) if enabled else None;issues=[]
    for suite in enabled:
        runs=session.scalars(select(Record).where(Record.collection=='evaluationRuns',Record.data['suiteId'].astext==suite.id)).all()
        latest=max(runs,key=lambda r:r.data.get('createdAt',''),default=None)
        if not latest or latest.data.get('configurationFingerprint')!=current or latest.data.get('suiteHash')!=digest(suite.data):issues.append(suite.data['name']+'：缺少当前配置的评测');continue
        for case in latest.data['cases']:
            log=record(session,'logs',case['runId'])
            if not log or score(log.data,case['criteria'])[0] is not True:issues.append(suite.data['name']+'：尚未全部通过');break
    return {'enabledSuites':len(enabled),'passed':not issues,'issues':issues,'fingerprint':current}

def enforce_gate(session,app_id,version):
    state=gate_state(session,app_id,version)
    if not state['passed']:raise AppError('EVALUATION_GATE','发布评测未通过：'+'；'.join(state['issues']),409)

def router_for(context,session_factory):
    router=APIRouter(prefix='/api/delivery')
    @router.get('/apps/{app_id}/suites')
    def suites(app_id:str,ctx=Depends(context)):
        require(ctx[0],'manager')
        with session_factory()() as session:
            from .platform_apps import get_app,snapshot_version
            app=get_app(session,app_id)
            rows=session.scalars(select(Record).where(Record.collection=='deliverySuites',Record.data['appId'].astext==app_id)).all()
            return {'items':[r.data for r in rows],'gate':gate_state(session,app_id,snapshot_version(session,app_id,app.data['draft'],0,True))}
    @router.post('/apps/{app_id}/suites')
    def create(app_id:str,body:dict,ctx=Depends(context)):
        require(ctx[0],'manager')
        with session_factory().begin() as session:
            from .platform_apps import get_app
            revision_lock(session);get_app(session,app_id);identifier=uid('suite');value={**suite_fields(body),'id':identifier,'appId':app_id,'revision':1}
            put(session,'deliverySuites',identifier,value);write_audit(session,ctx,'suite.create','evaluation',identifier);bump(session)
        return {'item':value}
    @router.put('/suites/{suite_id}')
    def update(suite_id:str,body:dict,ctx=Depends(context)):
        require(ctx[0],'manager')
        with session_factory().begin() as session:
            revision_lock(session);row=record(session,'deliverySuites',safe_id(suite_id))
            if not row:raise AppError('NOT_FOUND','评测集不存在。',404)
            if body.get('revision')!=row.data['revision']:raise AppError('DRAFT_CONFLICT','评测集已修改，请重新加载。',409)
            row.data={**row.data,**suite_fields(body),'revision':row.data['revision']+1};value=copy.deepcopy(row.data);write_audit(session,ctx,'suite.edit','evaluation',suite_id);bump(session)
        return {'item':value}
    @router.get('/apps/{app_id}/runs')
    def history(app_id:str,ctx=Depends(context)):
        require(ctx[0],'manager')
        from .main import evaluation_run_view
        with session_factory()() as session:
            query=select(Record).where(Record.collection=='evaluationRuns',Record.data['appId'].astext==app_id)
            if ctx[0]!='admin':query=query.where(Record.session_id==ctx[1])
            rows=session.scalars(query.order_by(Record.data['createdAt'].astext.desc()).limit(50)).all()
            return {'items':[evaluation_run_view(session,r) for r in rows]}
    @router.post('/suites/{suite_id}/runs')
    def run(suite_id:str,body:dict,ctx=Depends(context)):
        require(ctx[0],'manager');request_id=safe_id(body.get('requestId'));run_id='eval-'+digest(ctx[1]+':delivery:'+request_id)[:32]
        from .platform_apps import get_app,snapshot_version,create_platform_conversation
        from .worker import create_chat
        from .main import evaluation_run_view
        with session_factory().begin() as session:
            revision_lock(session);suite=record(session,'deliverySuites',safe_id(suite_id))
            if not suite:raise AppError('NOT_FOUND','评测集不存在。',404)
            existing=record(session,'evaluationRuns',run_id)
            if existing:
                if existing.data.get('suiteId')!=suite_id:raise AppError('IDEMPOTENCY_CONFLICT','请求编号已经使用。',409)
                return {'run':evaluation_run_view(session,existing)}
            app=get_app(session,suite.data['appId']);version=snapshot_version(session,app.id,app.data['draft'],0,True)
            stamp=fingerprint(session,version);put(session,'platformVersions',version['id'],version);cases=[]
            for case in suite.data['cases']:
                conversation=create_platform_conversation(session,app.id,version['id'],ctx[1],'evaluation_draft')
                create_chat(session,conversation['id'],case['query'],False,ctx[1],case['inputs']);session.flush()
                row=record(session,'conversations',conversation['id']);log_id=row.data['messages'][-1]['runId']
                cases.append({**case,'runId':log_id,'conversationId':conversation['id']})
            value={'id':run_id,'appId':app.id,'appName':version['name'],'versionId':version['id'],'versionNumber':0,'createdAt':now(),'suiteId':suite_id,'suiteHash':digest(suite.data),'configurationFingerprint':stamp,'cases':cases}
            put(session,'evaluationRuns',run_id,value,ctx[1]);write_audit(session,ctx,'suite.run','evaluation',run_id);session.flush();bump(session)
            return {'run':evaluation_run_view(session,record(session,'evaluationRuns',run_id))}
    return router
