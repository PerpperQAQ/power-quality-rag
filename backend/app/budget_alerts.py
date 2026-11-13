"""Durable, deduplicated local alerts for known estimated monthly spend."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from sqlalchemy import select
from .db import Record, bump, revision_lock
from .state import now, put, record

ZONE=ZoneInfo('Asia/Shanghai')

def timestamp(value):
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (TypeError, ValueError, AttributeError):return None

def calculate(logs,prices,start,end):
    total=Decimal(0);known=unknown=0
    for log in logs:
        t=timestamp(log.get('createdAt'))
        if not t or not start<=t<end:continue
        items=log.get('usage') or []
        if not items:
            if log.get('tokens')!=0:unknown+=1
            continue
        for item in items:
            eligible=[p for p in prices if p.get('model')==item.get('model') and p.get('kind')==item.get('kind') and timestamp(p.get('effectiveAt')) and timestamp(p['effectiveAt'])<=t]
            if not eligible:unknown+=1;continue
            rule=max(eligible,key=lambda p:timestamp(p['effectiveAt']))
            try:
                rates=[Decimal(rule['inputRate']),Decimal(rule['outputRate'])]
                nums=[item.get('inputTokens'),item.get('outputTokens')]
                if any(not x.is_finite() or x<0 for x in rates) or any(rate>0 and (isinstance(n,bool) or not isinstance(n,int) or n<0) for rate,n in zip(rates,nums)):raise ValueError()
                cached=item.get('cachedTokens');cache_rate=rule.get('cachedInputRate')
                if cache_rate is not None:
                    cache_rate=Decimal(cache_rate)
                    if not cache_rate.is_finite() or cache_rate<0 or isinstance(cached,bool) or not isinstance(cached,int) or cached<0 or isinstance(nums[0],bool) or not isinstance(nums[0],int) or cached>nums[0]:raise ValueError()
                    amount=(nums[0]-cached)*rates[0]+cached*cache_rate+rates[1]*(nums[1] if isinstance(nums[1],int) and not isinstance(nums[1],bool) and nums[1]>=0 else 0)
                else:
                    if isinstance(cached,int) and not isinstance(cached,bool) and cached>0:raise ValueError()
                    amount=sum(rate*(n if isinstance(n,int) and not isinstance(n,bool) and n>=0 else 0) for rate,n in zip(rates,nums))
                total+=amount/Decimal(1000000);known+=1
            except (KeyError,InvalidOperation,ValueError,TypeError):unknown+=1
    return total,known,unknown

def check(factory,current=None):
    current=(current or datetime.now(timezone.utc)).astimezone(ZONE)
    start=current.replace(day=1,hour=0,minute=0,second=0,microsecond=0)
    end=start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1)
    month=start.strftime('%Y-%m');identifier='budget-'+month
    with factory.begin() as session:
        revision_lock(session);budget=record(session,'platformBudget','local')
        if not budget or record(session,'platformAlerts',identifier):return None
        logs=[row.data for row in session.scalars(select(Record).where(Record.collection=='logs'))]
        prices=[row.data for row in session.scalars(select(Record).where(Record.collection=='platformPricing'))]
        total,known,unknown=calculate(logs,prices,start.astimezone(timezone.utc),end.astimezone(timezone.utc))
        limit=Decimal(budget.data['monthlyLimit'])
        if not known or total<limit:return None
        alert={'id':identifier,'kind':'monthly_budget','month':month,'createdAt':now(),'estimatedKnown':str(total),'limit':str(limit),'knownCount':known,'unknownCount':unknown}
        put(session,'platformAlerts',identifier,alert);bump(session)
        return alert

def check_failures(factory,current=None):
    """Alert once per Beijing clock hour once at least five failures are recorded."""
    current=(current or datetime.now(timezone.utc)).astimezone(ZONE)
    start=current.replace(minute=0,second=0,microsecond=0)
    identifier='failures-'+start.strftime('%Y-%m-%d-%H')
    with factory.begin() as session:
        revision_lock(session)
        if record(session,'platformAlerts',identifier):return None
        rows=session.scalars(select(Record).where(Record.collection=='logs',Record.data['status'].astext=='failed'))
        failed=[row.data for row in rows if (t:=timestamp(row.data.get('createdAt'))) and start.astimezone(timezone.utc)<=t<=current.astimezone(timezone.utc)]
        if len(failed)<5:return None
        alert={'id':identifier,'kind':'failure_spike','createdAt':now(),'hour':start.isoformat(),'count':len(failed),'threshold':5}
        put(session,'platformAlerts',identifier,alert);bump(session)
        return alert
