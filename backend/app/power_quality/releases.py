"""Atomic publication of validated domain data; generic chunks stay disabled."""
import math
from sqlalchemy import select
from ..db import Record, bump, revision_lock
from ..state import now, record
from ..security import AppError
from . import chunking, parsing, retrieval, service


def validate(session, identifier):
    snap = service.snapshot_view(session, identifier)
    if snap['build_status'] != 'ready':
        raise AppError('BUILD_NOT_READY', '快照尚无完整候选索引。', 409)
    if not snap.get('build_id'):
        raise AppError('INDEX_PIPELINE_UNAVAILABLE', '缺少可核对的构建记录。', 409)
    build, snap, plan, catalog, rows = retrieval.load(session, snap['build_id'])
    if (len(snap['manifest']) != 12 or len(rows) != 230 or len(build['attempt_ids']) != 25
            or build['completed_batches'] != len(plan['batches']) or build['stored_chunks'] != len(rows)
            or build['authorization']['build_fingerprint'] != plan['build_fingerprint']
            or build['authorization']['manifest_hash'] != snap['manifest_hash']):
        raise AppError('RELEASE_PLAN_MISMATCH', '首轮构建清单或调用台账不完整。', 409)
    expected = {c['chunk_id']: c for d in plan['documents'] for c in d['chunks']}
    tasks = [[max(expected.values(), key=lambda c: c['input_utf8_bytes'])['chunk_id']]] + [b['chunk_ids'] for b in plan['batches']]
    receipts = {}
    for ordinal, (aid, ids) in enumerate(zip(build['attempt_ids'], tasks, strict=True), 1):
        attempt = service.value(session, 'pqEmbedAttempts', aid)
        receipt = service.value(session, 'pqEmbedResults', aid)
        input_hash = parsing.fingerprint([expected[c]['embedding_text'] for c in ids])
        if (attempt['build_id'] != build['id'] or attempt['status'] != 'succeeded' or attempt['ordinal'] != ordinal
                or attempt['chunk_ids'] != ids or attempt['model'] != chunking.MODEL
                or attempt['input_hash'] != input_hash or receipt['input_hash'] != input_hash
                or receipt['attempt_id'] != aid or receipt['vector_sha256'] != parsing.fingerprint(receipt['vectors'])
                or len(receipt['vectors']) != len(ids)):
            raise AppError('RELEASE_RECEIPT_MISMATCH', '构建返回与固定输入不一致。', 409)
        receipts[aid] = dict(zip(ids, receipt['vectors'], strict=True))
    for row in rows:
        vector = [float(v) for v in row.embedding]
        original = receipts.get(row.payload.get('attempt_id'), {}).get(row.payload['chunk_id'])
        if (original is None or row.payload.get('domain') != 'power_quality' or row.payload.get('enabled') is not False
                or len(original) != 1024 or any(not math.isfinite(v) for v in vector)
                or not math.isfinite(sum(v*v for v in vector)) or sum(v*v for v in vector) <= 0
                or row.payload.get('vector_sha256') != parsing.fingerprint(original)
                or any(not isinstance(b, (int, float)) or isinstance(b, bool) or not math.isfinite(b) or abs(a-b) > 1e-6 for a,b in zip(vector, original, strict=True))):
            raise AppError('RELEASE_VECTOR_MISMATCH', '候选向量与原始返回不一致。', 409)
    inventory_hash = parsing.fingerprint(sorted((r.id, r.payload['chunk_id'], r.payload['vector_sha256']) for r in rows))
    return build, snap, plan, inventory_hash


def active(session):
    state = record(session, 'pqState', 'active')
    if not state or not state.data.get('release_id'):
        return {'release': None, 'queryable': False, 'active_snapshot_id': state.data.get('active_snapshot_id') if state else None}
    release = service.value(session, 'pqReleases', state.data['release_id'])
    if release['snapshot_id'] != state.data.get('active_snapshot_id'):
        raise AppError('RELEASE_INTEGRITY_ERROR', '生效关联异常。', 409)
    return {'release': release, 'queryable': True, 'active_snapshot_id': release['snapshot_id']}


def activate(session, identifier, request, actor):
    revision_lock(session)
    payload = {'snapshot_id': identifier, **request.model_dump(by_alias=True)}
    key, prior = service.prior_request(session, actor, 'activate', request.request_id, payload)
    if prior:
        return service.value(session, 'pqReleases', prior)
    current = active(session)
    if current['active_snapshot_id'] != request.expected_active_snapshot_id:
        raise AppError('RELEASE_CONFLICT', '当前有效版本已变化，请刷新核对。', 409)
    build, snap, plan, inventory = validate(session, identifier)
    if current['release'] and current['release']['snapshot_id'] == identifier:
        result = current['release']
        if result['inventory_hash'] != inventory or result['build_id'] != build['id']:
            raise AppError('RELEASE_INTEGRITY_ERROR', '已生效内容不能原位修改。', 409)
    else:
        rid = 'pqrelease-' + parsing.fingerprint([identifier, build['id'], inventory])[:48]
        old = record(session, 'pqReleases', rid)
        result = old.data if old else {'id': rid, 'snapshot_id': identifier, 'build_id': build['id'],
            'manifest_hash': snap['manifest_hash'], 'build_fingerprint': plan['build_fingerprint'],
            'inventory_hash': inventory, 'config': plan['config'], 'operator_id': actor['session_id'],
            'created_at': now(), 'queryable': True, 'generic_chunks_enabled': False}
        service.insert_immutable(session, 'pqReleases', rid, result)
        row = record(session, 'pqState', 'active')
        state = {'release_id': rid, 'active_snapshot_id': identifier}
        if row: row.data = state
        else: service.insert_immutable(session, 'pqState', 'active', state)
    service.save_request(session, key, payload, result['id'])
    bump(session)
    return result


def preview(session, request):
    if not session.in_transaction():
        session.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
    current = active(session)
    if not current['release']:
        raise AppError('NO_ACTIVE_SNAPSHOT', '尚无生效快照。', 409)
    release = current['release']
    build, _, _, inventory = validate(session, release['snapshot_id'])
    if build['id'] != release['build_id'] or inventory != release['inventory_hash']:
        raise AppError('RELEASE_INTEGRITY_ERROR', '生效内容与发布记录不一致。', 409)
    result = retrieval.preview(session, release['build_id'], request)
    return {**result, 'release_id': release['id'], 'queryable': True, 'publication': 'active_domain_release'}
