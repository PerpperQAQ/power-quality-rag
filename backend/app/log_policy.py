"""Opt-in masking and retention for log records, not conversation or job data."""
import copy
import re
from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy import event
from .db import Job,Record,revision_lock,bump
from .state import record,now
from .platform_audit import write_audit

PATTERNS=[(re.compile(r'(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])'),'[邮箱已脱敏]'),(re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)'),'[手机号已脱敏]'),(re.compile(r'(?<!\d)\d{17}[0-9Xx](?!\w)'),'[证件号已脱敏]')]
SENSITIVE={'query','output','input','context','text','content','error','partial','note','inputValues'}

def redact_text(text):
    for pattern,replacement in PATTERNS:text=pattern.sub(replacement,text)
    return text

def redact(value,inside=False):
    if isinstance(value,str):return redact_text(value) if inside else value
    if isinstance(value,list):return [redact(x,inside) for x in value]
    if isinstance(value,dict):return {k:redact(v,inside or k in SENSITIVE) for k,v in value.items()}
    return value

@event.listens_for(Session,'before_flush')
def protect_log_records(session,flush_context,instances):
    logs=[row for row in session.new.union(session.dirty) if isinstance(row,Record) and row.collection=='logs']
    if not logs:return
    with session.no_autoflush:
        policy=session.get(Record,('platformLogPolicy','local'))
    if policy and policy.data.get('maskEnabled'):
        for row in logs:row.data=redact(row.data)

def retention_candidates(session,days):
    cutoff=datetime.now(timezone.utc)-timedelta(days=days)
    protected=set(session.scalars(select(Job.id).where(Job.status.in_(['queued','running']))).all())
    for row in session.scalars(select(Record).where(Record.collection=='evaluationCases')):
        if isinstance(row.data.get('runId'),str):protected.add(row.data['runId'])
    for row in session.scalars(select(Record).where(Record.collection=='evaluationRuns')):
        for case in row.data.get('cases',[]):
            if isinstance(case,dict) and isinstance(case.get('runId'),str):protected.add(case['runId'])
    for row in session.scalars(select(Record).where(Record.collection=='evaluationJudgeBatches')):
        for item in row.data.get('items',[]):
            if isinstance(item,dict) and isinstance(item.get('jobId'),str):protected.add(item['jobId'])
    for row in session.scalars(select(Record).where(Record.collection=='evaluationRuns')):
        for case in row.data.get('cases',[]):
            if not isinstance(case,dict):continue
            for judge in [case.get('judge'),*(case.get('judgeHistory') or [])]:
                if isinstance(judge,dict) and isinstance(judge.get('id'),str):protected.add(judge['id'])
    eligible=[]
    for row in session.scalars(select(Record).where(Record.collection=='logs')):
        if row.id in protected or row.data.get('status')=='running':continue
        try:
            created=datetime.fromisoformat(row.data['createdAt'].replace('Z','+00:00'))
            if created.tzinfo is None:continue
            if created<cutoff:eligible.append(row)
        except (KeyError,ValueError,TypeError,AttributeError):continue
    return eligible

def cleanup(session,ctx,days):
    revision_lock(session);rows=retention_candidates(session,days)
    for row in rows:session.delete(row)
    write_audit(session,ctx,'logs.retention','policy','local')
    if rows:bump(session)
    else:bump(session) # audit itself is a state change
    return len(rows)
