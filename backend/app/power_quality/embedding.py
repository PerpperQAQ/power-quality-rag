"""Budgeted, one-shot candidate build. No retry, publication, or query calls.

Each attempt is committed BEFORE provider submission. An interrupted/in-flight
build cannot be resumed by this executor; its unknown submission needs review.
The caller supplies an explicitly approved authorization and an audited provider.
"""
import math
from decimal import Decimal

from sqlalchemy import select, text

from ..db import Chunk, bump, revision_lock
from ..security import AppError
from ..state import now, record
from . import chunking, parsing, service

MAX_TOKENS_PER_ITEM = 8192


def cost(tokens, authorization):
    return Decimal(tokens) * Decimal(authorization["cny_per_million_tokens"]) / Decimal(1_000_000)


def valid_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def prepare(session, snapshot, plan, authorization):
    revision_lock(session)
    if (authorization["manifest_hash"] != snapshot["manifest_hash"]
            or authorization["build_fingerprint"] != plan["build_fingerprint"]
            or plan["config"]["embedding_model"] != "text-embedding-v4"
            or plan["config"]["embedding_dimensions"] != 1024
            or plan["config"]["automatic_retries"] != 0 or not plan["input_plan_valid"]):
        raise AppError("APPROVAL_PLAN_MISMATCH", "构建计划与审批指纹不一致。", 409)
    if (not 0 < authorization["max_attempts"] <= 25 or not 0 < authorization["max_input_tokens"] <= 200000
            or not Decimal("0") < Decimal(authorization["max_cost_cny"]) <= Decimal("0.10")
            or Decimal(authorization["cny_per_million_tokens"]) != Decimal("0.5")):
        raise AppError("APPROVAL_LIMIT_INVALID", "调用上限不符合本轮授权。", 409)
    identifier = "pqbuild-"+parsing.fingerprint(authorization["id"])[:40]
    old = record(session, "pqBuilds", identifier)
    if old:
        if old.data["authorization"] != authorization or old.data["snapshot_id"] != snapshot["id"]:
            raise AppError("APPROVAL_REUSED", "此额度已绑定另一快照，不能重置使用。", 409)
        return service.value(session, "pqBuilds", identifier)
    if snapshot["build_status"] != "not_started":
        raise AppError("BUILD_ALREADY_STARTED", "快照已有构建，不重复提交。", 409)
    data = {"id": identifier, "snapshot_id": snapshot["id"], "authorization": authorization,
            "build_fingerprint": plan["build_fingerprint"], "manifest_hash": plan["manifest_hash"],
            "status": "prepared", "created_at": now(), "attempt_ids": [], "observed_input_tokens": 0,
            "usage_complete": True, "observed_cost_cny_at_public_price": "0", "smoke_passed": False,
            "completed_batches": 0, "stored_chunks": 0, "queryable": False, "stop_reason": None}
    service.insert_immutable(session, "pqBuilds", identifier, data)
    row = record(session, "pqSnapshots", snapshot["id"])
    row.data = {**row.data, "build_id": identifier, "build_status": "prepared", "queryable": False}
    bump(session)
    return data


def read_build(session, identifier):
    return service.value(session, "pqBuilds", identifier)


def _update(session, build, **updates):
    build = {**build, **updates, "updated_at": now()}
    record(session, "pqBuilds", build["id"]).data = build
    snap = record(session, "pqSnapshots", build["snapshot_id"])
    snap.data = {**snap.data, "build_status": "ready" if build["status"] == "completed" else build["status"],
                 "build_id": build["id"], "queryable": False}
    bump(session)
    return build


def claim(session, identifier, plan):
    revision_lock(session)
    build = read_build(session, identifier)
    if build["status"] != "prepared":
        return False
    snapshot = service.snapshot_view(session, build["snapshot_id"])
    if plan["snapshot_id"] != snapshot["id"] or plan["build_fingerprint"] != build["build_fingerprint"]:
        raise AppError("BUILD_PLAN_CHANGED", "计划指纹已变化。", 409)
    # Recompute from persisted originals before trusting the caller's text.
    revisions = [service.revision_view(session, m["revision_id"]) for m in snapshot["manifest"]]
    if chunking.plan(snapshot, revisions, plan["config"]["target_characters"]) != plan:
        raise AppError("BUILD_PLAN_CHANGED", "输入文本或映射与固定快照不一致。", 409)
    _update(session, build, status="running")
    return True


def reserve(session, identifier, task):
    revision_lock(session)
    build = read_build(session, identifier)
    auth = build["authorization"]
    if build["status"] != "running" or not build["usage_complete"]:
        return None
    if any(service.value(session, "pqEmbedAttempts", aid)["status"] == "submission_unknown" for aid in build["attempt_ids"]):
        _update(session, build, status="stopped", stop_reason="SUBMISSION_UNKNOWN")
        return None
    if task["kind"] == "build" and not build["smoke_passed"]:
        _update(session, build, status="stopped", stop_reason="SMOKE_NOT_PASSED")
        return None
    maximum = len(task["chunks"]) * MAX_TOKENS_PER_ITEM
    projected = build["observed_input_tokens"] + maximum
    if (len(build["attempt_ids"]) >= auth["max_attempts"] or projected > auth["max_input_tokens"]
            or cost(projected, auth) > Decimal(auth["max_cost_cny"])):
        _update(session, build, status="stopped", stop_reason="BUDGET_BEFORE_SUBMISSION")
        return None
    texts = [c["embedding_text"] for c in task["chunks"]]
    if not 0 < len(texts) <= 10 or any(len(t.encode()) > 8000 for t in texts):
        _update(session, build, status="stopped", stop_reason="INPUT_LIMIT")
        return None
    aid = identifier+f"-{len(build['attempt_ids'])+1:02}"
    attempt = {"id": aid, "build_id": identifier, "ordinal": len(build["attempt_ids"])+1,
               "kind": task["kind"], "batch_number": task["batch_number"], "status": "submission_unknown",
               "reserved_at": now(), "model": chunking.MODEL, "input_hash": parsing.fingerprint(texts),
               "chunk_ids": [c["chunk_id"] for c in task["chunks"]], "item_count": len(texts),
               "input_utf8_bytes": sum(len(t.encode()) for t in texts), "reserved_max_tokens": maximum,
               "observed_input_tokens": None, "observed_cost_cny_at_public_price": None,
               "request_id": None, "error_code": None}
    service.insert_immutable(session, "pqEmbedAttempts", aid, attempt)
    _update(session, build, attempt_ids=[*build["attempt_ids"], aid])
    return attempt


def validate_result(result, count):
    if not valid_count(result.input_tokens) or result.input_tokens == 0:
        return "USAGE_UNKNOWN_OR_INVALID"
    if result.model != chunking.MODEL or len(result.attempts) != 1:
        return "PROVIDER_ATTEMPT_OR_MODEL_MISMATCH"
    vectors = result.data
    if not isinstance(vectors, list) or len(vectors) != count:
        return "VECTOR_COUNT_MISMATCH"
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != 1024:
            return "VECTOR_DIMENSION_MISMATCH"
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
            return "VECTOR_INVALID"
        norm = sum(v*v for v in vector)
        if not math.isfinite(norm) or norm <= 0:
            return "VECTOR_INVALID_NORM"
    return None


def outcome(session, identifier, attempt, task, result=None, error=None, transport=None):
    revision_lock(session)
    build = read_build(session, identifier)
    current = service.value(session, "pqEmbedAttempts", attempt["id"])
    if current["status"] != "submission_unknown":
        raise AppError("ATTEMPT_ALREADY_FINAL", "调用已结算，不能再次写入。", 409)
    tokens = result.input_tokens if result is not None and valid_count(result.input_tokens) and result.input_tokens > 0 else None
    total = build["observed_input_tokens"]+(tokens if tokens is not None else 0)
    auth = build["authorization"]
    if tokens is not None and (tokens > attempt["reserved_max_tokens"] or total > auth["max_input_tokens"]
                              or cost(total, auth) > Decimal(auth["max_cost_cny"])):
        error = "USAGE_OVER_BUDGET_OR_PROVIDER_LIMIT"
    request_id = (getattr(result, "request_id", None) or (transport or {}).get("request_id_header"))
    usage = result.usage if result is not None else None
    row = record(session, "pqEmbedAttempts", attempt["id"])
    row.data = {**current, "status": "failed" if error else "succeeded", "completed_at": now(),
                "observed_input_tokens": tokens, "observed_cost_cny_at_public_price": str(cost(tokens, auth)) if tokens is not None else None,
                "request_id": request_id, "error_code": error, "usage": usage, "transport": transport or {}}
    updates = {"observed_input_tokens": total, "usage_complete": build["usage_complete"] and tokens is not None,
               "observed_cost_cny_at_public_price": str(cost(total, auth))}
    if error:
        return _update(session, build, **updates, status="failed", stop_reason=error)
    service.insert_immutable(session, "pqEmbedResults", attempt["id"], {
        "attempt_id": attempt["id"], "input_hash": attempt["input_hash"], "vectors": result.data,
        "vector_sha256": parsing.fingerprint(result.data), "usage": usage, "transport": transport or {}})
    if task["kind"] == "smoke":
        updates["smoke_passed"] = True
    else:
        from ..services import tokenize
        for chunk, vector in zip(task["chunks"], result.data, strict=True):
            cid = "pqvec-"+parsing.fingerprint([identifier, chunk["chunk_id"]])[:48]
            doc_id = "pqdoc-"+parsing.fingerprint([identifier, chunk["revision_id"]])[:48]
            session.add(Chunk(id=cid, doc_id=doc_id, kb_id=chunk["industry_id"], model=chunking.MODEL,
                              dimension=1024, enabled=False, embedding=vector, content=chunk["rendered_excerpt"],
                              lexemes=tokenize(chunk["embedding_text"]),
                              payload={**chunk, "domain": "power_quality", "build_id": identifier, "enabled": False,
                                       "attempt_id": attempt["id"], "vector_sha256": parsing.fingerprint(vector)}))
        updates["stored_chunks"] = build["stored_chunks"]+len(task["chunks"])
        updates["completed_batches"] = build["completed_batches"]+1
    return _update(session, build, **updates)


def finalize(session, identifier, plan):
    revision_lock(session)
    build = read_build(session, identifier)
    if build["status"] != "running" or not build["usage_complete"] or not build["smoke_passed"]:
        raise AppError("BUILD_INCOMPLETE", "构建或调用台账不完整。", 409)
    expected = {c["chunk_id"]: c for d in plan["documents"] for c in d["chunks"]}
    rows = session.scalars(select(Chunk).where(Chunk.payload["build_id"].astext == identifier)).all()
    if (build["completed_batches"] != len(plan["batches"]) or len(rows) != len(expected)
            or {r.payload["chunk_id"] for r in rows} != set(expected)):
        raise AppError("BUILD_INCOMPLETE", "向量条目不完整。", 409)
    for row in rows:
        chunk = expected[row.payload["chunk_id"]]
        if (row.dimension != 1024 or len(row.embedding) != 1024 or row.enabled
                or row.model != chunking.MODEL or row.kb_id != chunk["industry_id"]
                or row.payload["embedding_sha256"] != chunk["embedding_sha256"]
                or row.content != chunk["rendered_excerpt"]):
            raise AppError("INDEX_INTEGRITY_ERROR", "持久化向量与来源映射不一致。", 409)
        receipt = service.value(session, "pqEmbedResults", row.payload["attempt_id"])
        attempt = service.value(session, "pqEmbedAttempts", row.payload["attempt_id"])
        vector = receipt["vectors"][attempt["chunk_ids"].index(chunk["chunk_id"])]
        if any(not math.isfinite(float(a)) or abs(float(a)-b) > 1e-6 for a, b in zip(row.embedding, vector, strict=True)):
            raise AppError("INDEX_VECTOR_MISMATCH", "向量回读不一致。", 409)
    session.execute(text("CREATE INDEX IF NOT EXISTS pq_candidate_1024_hnsw ON chunks USING hnsw ((embedding::vector(1024)) vector_cosine_ops) WHERE dimension=1024 AND NOT enabled AND payload->>'domain'='power_quality'"))
    return _update(session, build, status="completed", completed_at=now(),
                   validation={"readback_chunks": len(rows), "dimension": 1024, "all_disabled": True,
                               "vector_roundtrip_tolerance": 1e-6, "candidate_index": "pq_candidate_1024_hnsw"})


async def execute(factory, identifier, plan, provider, progress=None):
    with factory.begin() as session:
        started = claim(session, identifier, plan)
    if not started:
        with factory() as session:
            return read_build(session, identifier)
    lookup = {c["chunk_id"]: c for d in plan["documents"] for c in d["chunks"]}
    smoke = max(lookup.values(), key=lambda c: c["input_utf8_bytes"])
    tasks = [{"kind": "smoke", "batch_number": 0, "chunks": [smoke]}]+[
        {"kind": "build", "batch_number": b["batch_number"], "chunks": [lookup[cid] for cid in b["chunk_ids"]]}
        for b in plan["batches"]]
    for task in tasks:
        with factory.begin() as session:
            attempt = reserve(session, identifier, task)
        if attempt is None:
            break
        result, error = None, None
        try:
            result = await provider.embed([c["embedding_text"] for c in task["chunks"]], chunking.MODEL)
            error = validate_result(result, len(task["chunks"]))
        except BaseException as exc:
            # Never echo arbitrary exception strings, headers or credentials.
            error = getattr(exc, "code", None) or type(exc).__name__
            known = getattr(exc, "usage", [])
            if len(known) == 1 and valid_count(known[0].get("input_tokens")):
                from ..providers import ProviderResult
                result = ProviderResult(None, input_tokens=known[0]["input_tokens"], model=chunking.MODEL,
                                        kind="embedding", request_id=known[0].get("request_id"), attempts=known)
        transport = getattr(provider, "last_transport", {})
        try:
            with factory.begin() as session:
                state = outcome(session, identifier, attempt, task, result, error, transport)
        except Exception:
            # Response received, DB write failed: keep known usage, stop. The
            # reservation remains unknown if even this transaction cannot commit.
            with factory.begin() as session:
                state = outcome(session, identifier, attempt, task, result, "PERSISTENCE_FAILURE", transport)
        if progress:
            progress(state, attempt, task)
        if state["status"] != "running":
            break
    with factory.begin() as session:
        state = read_build(session, identifier)
        if state["status"] == "running":
            try:
                with session.begin_nested():
                    state = finalize(session, identifier, plan)
            except Exception:
                state = _update(session, read_build(session, identifier), status="failed", stop_reason="FINAL_VALIDATION_FAILED")
    return state
