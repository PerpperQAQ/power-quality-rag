"""Shared delivery policy checks, before any provider or external tool call."""
import copy
import json
from .db import Job, bump, revision_lock
from .state import record, now

def output_policy(job,answer,status,citations):
    config=job['payload'].get('delivery') or {};rules=config.get('guardrails',{})
    if rules.get('requireCitations') and not citations:return '未找到可核对的来源，无法确认该回答。','no_evidence'
    if len(answer)>rules.get('maxOutputChars',50000):return '回答超出应用字数限制，请缩小问题范围后重试。','no_evidence'
    if config.get('outputFormat')=='json' and status=='completed':
        try:json.loads(answer)
        except ValueError:return '模型未返回有效 JSON，请重试或调整提示词。','no_evidence'
    return answer,status

def preflight(job,factory):
    config=job['payload'].get('delivery') or {};rules=config.get('guardrails',{})
    text=job['payload']['query']+' '+json.dumps(job['payload'].get('inputValues',{}),ensure_ascii=False)
    event=None
    if any(x.casefold() in text.casefold() for x in rules.get('blockedTopics',[])):event='blocked_topic';answer='该问题超出此应用允许处理的范围。'
    elif rules.get('humanHandoff') and any(x in text for x in rules.get('handoffKeywords',[])):event='human_handoff';answer='该问题需要人工处理。请联系服务人员；本应用尚未连接外部客服坐席。'
    if not event:return False
    with factory.begin() as session:
        revision_lock(session);row=session.get(Job,job['id'],with_for_update=True)
        if not row or row.status!='running' or row.attempts!=job['attempt']:return True
        conversation=record(session,'conversations',job['entityId']);value=copy.deepcopy(conversation.data)
        for message in value['messages']:
            if message.get('runId')==job['id']:message.update(text=answer,status='no_evidence',policyEvent=event)
        value['updatedAt']=now();conversation.data=value;row.status='completed';row.lease_until=None
        log=record(session,'logs',job['id']);log.data={**log.data,'status':'no_evidence','output':answer,'policyEvent':event,'usage':[],'tokens':0,'inputTokens':0,'outputTokens':0,'duration':0,'traceEvents':[{'id':'delivery-policy','type':'policy','name':event,'status':'completed'}]};bump(session)
    return True
