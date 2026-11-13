"""Run-scoped external evidence. HTTP cannot grant budget or inject evidence.

This iteration exposes planning and cancellation. Execution is fixture-only,
via an explicitly injected in-process transport; there is no network transport.
"""
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field
from ..db import bump, revision_lock
from ..state import now, record
from ..security import AppError
from . import parsing, releases, retrieval, service

TOPICS = {'missing_source': '公开原始资料 来源', 'missing_model_manual': '厂家 型号 技术手册', 'public_conditions': '通用工况 适用条件'}


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(max_length=2000)
    title: str = Field(min_length=1, max_length=500)
    page_date: str | None
    edition: str | None
    excerpt: str = Field(max_length=12000)
    support_scope: str = Field(min_length=1, max_length=1000)
    status: Literal['body_available', 'summary_only', 'body_unavailable', 'expired', 'conflict']


def view(session, identifier):
    data = service.value(session, 'pqEvidenceRuns', identifier)
    return {**data, 'evidence': [service.value(session, 'pqExternalEvidence', eid) for eid in data['evidence_ids']]}


def plan(session, request, actor):
    revision_lock(session)
    payload = request.model_dump(by_alias=True)
    key, prior = service.prior_request(session, actor, 'evidence-plan', request.request_id, payload)
    if prior: return view(session, prior)
    current = releases.active(session)
    if not current['release']:
        raise AppError('NO_ACTIVE_SNAPSHOT', '尚无生效快照。', 409)
    result = releases.preview(session, request.retrieval)
    catalog = retrieval.catalog_view(session, result['build_id'])['entries']
    scope = result['scope']['entry_ids']
    chosen = [c for c in catalog if c['entry_id'] in scope]
    resolved = result['scope']['resolution'] == 'resolved' and len(chosen) == 1
    public_query = (chosen[0]['name'] + ' ' + TOPICS[request.reason]) if resolved else None
    identifier = 'pqevidence-' + parsing.fingerprint(key)[:40]
    data = {'id': identifier, 'release_id': current['release']['id'], 'snapshot_id': result['snapshot_id'],
            'scope': result['scope'], 'trigger_reason': request.reason, 'public_query': public_query,
            'status': 'waiting_authorization' if resolved else 'planned',
            'clarification_required': not resolved, 'retrieval': result,
            'evidence_sufficiency': 'not_semantically_assessed', 'created_at': now(),
            'attempts': [], 'evidence_ids': [], 'cancel_requested': False, 'error_code': None,
            'model_calls': 0, 'external_calls': 0, 'fixture_only': False}
    service.insert_immutable(session, 'pqEvidenceRuns', identifier, data)
    service.save_request(session, key, payload, identifier)
    bump(session)
    return view(session, identifier)


def change(session, data, **updates):
    data = {**data, **updates, 'updated_at': now()}
    record(session, 'pqEvidenceRuns', data['id']).data = data
    bump(session)
    return data


def cancel(session, identifier):
    revision_lock(session)
    data = service.value(session, 'pqEvidenceRuns', identifier)
    if data['status'] in {'completed', 'failed', 'canceled'}:
        return view(session, identifier)
    change(session, data, status='submission_unknown' if data['status'] == 'submission_unknown' else 'canceled', cancel_requested=True)
    return view(session, identifier)


def reserve_fixture(session, identifier, *, max_attempts):
    revision_lock(session)
    data = service.value(session, 'pqEvidenceRuns', identifier)
    if data['status'] == 'submission_unknown':
        raise AppError('SUBMISSION_UNKNOWN', '已有未知提交，先核查，不重复发送。', 409)
    if data['status'] not in {'waiting_authorization'} or data['cancel_requested'] or data['clarification_required']:
        raise AppError('RUN_NOT_EXECUTABLE', '当前运行不能执行。', 409)
    if max_attempts != 1 or len(data['attempts']) >= max_attempts:
        raise AppError('BUDGET_BEFORE_SUBMISSION', '执行次数额度不足。', 409)
    attempt = {'ordinal': 1, 'status': 'submission_unknown', 'reserved_at': now(), 'query_hash': parsing.fingerprint(data['public_query']),
               'provider': 'in_process_fixture', 'reserved_attempts': 1, 'real_calls': 0}
    return change(session, data, status='submission_unknown', attempts=[attempt], fixture_only=True)


def settle_fixture(session, identifier, items=None, error=None):
    revision_lock(session)
    data = service.value(session, 'pqEvidenceRuns', identifier)
    if data['status'] != 'submission_unknown' or not data['fixture_only']:
        raise AppError('ATTEMPT_ALREADY_FINAL', '运行不能重复结算。', 409)
    validated = []
    if not error:
        try:
            if not isinstance(items, list) or len(items) > 5: raise ValueError()
            for item in items:
                entry = EvidenceItem.model_validate(item).model_dump()
                url = urlsplit(entry['url'])
                if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password: raise ValueError()
                if entry['status'] == 'body_available' and not entry['excerpt'].strip(): raise ValueError()
                validated.append(entry)
        except (ValueError, TypeError):
            error = 'INVALID_EVIDENCE_RESPONSE'
    ids = []
    for index, item in enumerate(validated if not error else []):
        eid = identifier + '-' + str(index+1)
        service.insert_immutable(session, 'pqExternalEvidence', eid, {**item, 'id': eid, 'run_id': identifier,
            'fetched_at': now(), 'content_hash': parsing.digest(item['excerpt'].encode()), 'fixture_only': True,
            'citation_eligible': False, 'content_trust': 'untrusted_data', 'support_verified': False})
        ids.append(eid)
    attempt = {**data['attempts'][0], 'status': 'failed' if error else 'succeeded', 'completed_at': now(), 'error_code': error}
    change(session, data, status='canceled' if data['cancel_requested'] else ('failed' if error else 'completed'),
           attempts=[attempt], evidence_ids=ids, error_code=error)
    return view(session, identifier)


class FixtureTransport:
    """Explicit offline test dependency, never chosen by an HTTP field."""
    def __init__(self, items=None, fail=False):
        self.items, self.fail, self.calls = items, fail, 0

    def fetch(self, query):
        self.calls += 1
        if self.fail: raise RuntimeError('fixture failure')
        return self.items


def execute_fixture(factory, identifier, transport, *, max_attempts=1):
    if type(transport) is not FixtureTransport:
        raise AppError('REAL_CALLS_NOT_AUTHORIZED', '当前没有真实取证额度。', 409)
    with factory() as session, session.begin():
        data = reserve_fixture(session, identifier, max_attempts=max_attempts)
    try:
        items = transport.fetch(data['public_query'])
        error = None
    except Exception:
        items, error = None, 'FIXTURE_TRANSPORT_FAILED'
    with factory() as session, session.begin():
        return settle_fixture(session, identifier, items, error)
