"""Operational summaries from accessible, real user chat records."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .db import Record
from .security import AppError, require
from .state import now, record

TZ = ZoneInfo('Asia/Shanghai')

def summary(session, ctx, days):
    require(ctx[0], 'manager')
    if days not in (7, 30, 90):
        raise AppError('INVALID_PERIOD', '统计范围请选择 7、30 或 90 天。')
    today = datetime.now(TZ).date()
    start = today - timedelta(days=days - 1)
    end = today + timedelta(days=1)
    rows = session.scalars(select(Record).where(Record.collection == 'logs', Record.data['kind'].astext == 'chat'))
    logs=[]
    for row in rows:
        value=row.data
        if ctx[0] != 'admin' and row.session_id != ctx[1]:continue
        if value.get('purpose') in ('debug','evaluation','evaluation_draft') or not value.get('conversationId'):continue
        try:
            timestamp=datetime.fromisoformat(value['createdAt'].replace('Z','+00:00'))
            if timestamp.tzinfo is None:continue
            day=timestamp.astimezone(TZ).date()
        except (KeyError,ValueError,TypeError,OverflowError):continue
        if start <= day < end:logs.append((row,day))
    ids={row.id for row,_ in logs}
    cases={}
    if ids:
        for row in session.scalars(select(Record).where(Record.collection=='evaluationCases')):
            if ctx[0]!='admin' and row.session_id!=ctx[1]:continue
            if row.data.get('runId') in ids:cases[row.data['runId']]=row.data.get('label')
    daily={str(start+timedelta(days=i)):{'date':str(start+timedelta(days=i)),'calls':0,'failed':0,'noEvidence':0,'negative':0} for i in range(days)}
    groups={};statuses={'completed':0,'no_evidence':0,'failed':0,'canceled':0,'running':0,'other':0};positive=negative=known_tokens=known_count=unknown_count=0;durations=[];conversations=set();issues=[]
    for row,day in logs:
        v=row.data;status=v.get('status');bucket=status if status in statuses else 'other';statuses[bucket]+=1
        item=daily[str(day)];item['calls']+=1
        if status=='failed':item['failed']+=1
        if status=='no_evidence':item['noEvidence']+=1
        feedback=v.get('feedback') if isinstance(v.get('feedback'),dict) else {};vote=feedback.get('value') if status in ('completed','no_evidence') else None
        if vote=='positive':positive+=1
        if vote=='negative':negative+=1;item['negative']+=1
        tokens=v.get('tokens')
        if isinstance(tokens,int) and not isinstance(tokens,bool) and tokens>=0:known_tokens+=tokens;known_count+=1
        else:unknown_count+=1
        duration=v.get('duration')
        if status in ('completed','no_evidence','failed','canceled') and isinstance(duration,(float,int)) and not isinstance(duration,bool) and 0<=duration<=86400:durations.append(float(duration))
        conversations.add(v['conversationId'])
        key=v.get('applicationId') or ('assistant:'+str(v.get('assistantName') or '未命名助手'))
        group=groups.setdefault(key,{'id':key,'name':v.get('applicationName') or v.get('assistantName') or '未命名助手','calls':0,'failed':0,'noEvidence':0,'positive':0,'negative':0})
        group['calls']+=1;group['failed']+=int(status=='failed');group['noEvidence']+=int(status=='no_evidence');group['positive']+=int(vote=='positive');group['negative']+=int(vote=='negative')
        reasons=[]
        if status=='failed':reasons.append('运行失败')
        if status=='no_evidence':reasons.append('依据不足')
        if vote=='negative':reasons.append('用户反馈未解决')
        if reasons:issues.append({'id':row.id,'createdAt':v.get('createdAt'),'query':v.get('query',''),'name':v.get('applicationName') or v.get('assistantName') or '未命名助手','reasons':reasons,'caseLabel':cases.get(row.id)})
    issues.sort(key=lambda x:(x['createdAt'] or '',x['id']),reverse=True)
    groups_sorted=sorted(groups.values(),key=lambda x:(-x['calls'],x['name'],x['id']))
    eligible=statuses['completed']+statuses['no_evidence']
    return {'period':{'days':days,'start':str(start),'end':str(today),'timezone':'Asia/Shanghai'},'scope':'all' if ctx[0]=='admin' else 'own','total':len(logs),'conversations':len(conversations),'statuses':statuses,'feedback':{'positive':positive,'negative':negative,'eligible':eligible},'usage':{'knownTokens':known_tokens,'knownCalls':known_count,'unknownCalls':unknown_count},'averageDuration':round(sum(durations)/len(durations),2) if durations else None,'durationCount':len(durations),'trend':list(daily.values()),'applications':groups_sorted[:20],'applicationCount':len(groups_sorted),'issues':issues[:20],'issueCount':len(issues),'caseCounts':{'good':sum(x=='good' for x in cases.values()),'bad':sum(x=='bad' for x in cases.values())}}

def save_feedback(session,ctx,identifier,value):
    require(ctx[0],'user')
    row=record(session,'logs',identifier)
    if not row or row.session_id!=ctx[1] or row.data.get('kind')!='chat' or not row.data.get('conversationId') or row.data.get('purpose') in ('debug','evaluation','evaluation_draft'):
        raise AppError('NOT_FOUND','未找到可反馈的对话记录。',404)
    if row.data.get('status') not in ('completed','no_evidence'):
        raise AppError('FEEDBACK_UNAVAILABLE','仅已完成的回答可以提交反馈。',409)
    if value not in ('positive','negative',None):raise AppError('INVALID_FEEDBACK','反馈选项无效。')
    data={**row.data}
    if value is None:data.pop('feedback',None)
    else:data['feedback']={'value':value,'updatedAt':now()}
    row.data=data
    return data.get('feedback')
