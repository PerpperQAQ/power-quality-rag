"""Read-only candidate retrieval. No provider, job, search or generation path.

The optional vector is an existing corpus chunk probe, never a query embedding.
Only a completed immutable candidate build can be inspected through this API.
"""
import math
import re

from sqlalchemy import select, text

from ..db import Chunk
from ..security import AppError
from ..services import tokenize
from . import chunking, parsing, service

VERSION = "pq-scoped-retrieval-v1"


def norm(value):
    return re.sub(r"\s+", "", value).casefold()


def load(session, identifier):
    build = service.value(session, "pqBuilds", identifier)
    if build["status"] != "completed" or not build["usage_complete"] or not build["smoke_passed"]:
        raise AppError("CANDIDATE_NOT_READY", "候选构建尚未完成。", 409)
    snap = service.snapshot_view(session, build["snapshot_id"])
    if snap["build_id"] != identifier or snap["build_status"] != "ready" or snap["manifest_hash"] != build["manifest_hash"]:
        raise AppError("BUILD_SNAPSHOT_MISMATCH", "构建与快照不一致。", 409)
    revisions = [service.revision_view(session, m["revision_id"]) for m in snap["manifest"]]
    plan = chunking.plan(snap, revisions, 900)
    if plan["build_fingerprint"] != build["build_fingerprint"]:
        raise AppError("BUILD_INPUT_CHANGED", "候选构建输入指纹不一致。", 409)
    expected = {c["chunk_id"]: c for d in plan["documents"] for c in d["chunks"]}
    rows = session.scalars(select(Chunk).where(Chunk.payload["build_id"].astext == identifier)).all()
    if len(rows) != len(expected) or {r.payload.get("chunk_id") for r in rows} != set(expected):
        raise AppError("CANDIDATE_INTEGRITY_ERROR", "候选向量清单不完整。", 409)
    for row in rows:
        c = expected[row.payload["chunk_id"]]
        if (row.id != "pqvec-"+parsing.fingerprint([identifier, c["chunk_id"]])[:48]
                or row.model != chunking.MODEL or row.dimension != 1024 or len(row.embedding) != 1024
                or row.enabled or row.kb_id != c["industry_id"] or row.content != c["rendered_excerpt"]
                or any(row.payload.get(k) != v for k, v in c.items())):
            raise AppError("CANDIDATE_INTEGRITY_ERROR", "候选向量模型、维度或来源映射不一致。", 409)
    catalog = []
    for revision in revisions:
        p = revision["parsed"]
        aliases = list(p["aliases"])
        # v1 parser retained these raw fields even where its aliases list was
        # empty. Derive search aliases without editing the immutable revision.
        for field in p["fields"]:
            if field["section_id"] == "s1" and field["name"] in {"常见别名", "常见别名/现场叫法"}:
                aliases += [a.strip() for a in re.split(r"[、；;,，]", field["raw"]) if a.strip()]
        catalog.append({"entry_id": p["entry_id"], "revision_id": revision["revision_id"],
                        "industry_id": p["industry_id"], "industry_name": p["industry_name"],
                        "name": p["canonical_name"], "aliases": sorted(set(aliases)),
                        "source_relative_path": p["source_relative_path"],
                        "chunks": [{"chunk_id": c["chunk_id"], "section_id": c["section_id"]} for c in expected.values() if c["entry_id"] == p["entry_id"]]})
    return build, snap, plan, catalog, rows


def catalog_view(session, identifier):
    build, snap, _, catalog, _ = load(session, identifier)
    return {"build_id": identifier, "snapshot_id": snap["id"], "manifest_hash": snap["manifest_hash"],
            "build_fingerprint": build["build_fingerprint"], "entries": catalog, "queryable": False,
            "mode": "candidate_diagnostic_only", "model_calls": 0, "external_calls": 0}


def resolve(catalog, request):
    industries, entries = set(request.industry_ids), set(request.entry_ids)
    byid = {c["entry_id"]: c for c in catalog}
    known_industries = {c["industry_id"] for c in catalog}
    if industries-known_industries or entries-set(byid):
        raise AppError("SCOPE_NOT_IN_SNAPSHOT", "指定行业或对象不在当前候选快照中。", 400)
    if industries and any(byid[e]["industry_id"] not in industries for e in entries):
        raise AppError("SCOPE_CONFLICT", "对象不属于所选行业；未扩大检索范围。", 409)
    candidates = [c for c in catalog if (not industries or c["industry_id"] in industries) and (not entries or c["entry_id"] in entries)]
    matches = []
    for c in candidates:
        names = [c["name"], *c["aliases"]]
        if request.object_name:
            found = any(norm(n) == norm(request.object_name) for n in names)
        else:
            q = norm(request.query)
            found = any((re.search(r"(?<![a-z0-9])"+re.escape(norm(n))+r"(?![a-z0-9])", q) if re.fullmatch(r"[a-z0-9]+", norm(n)) else norm(n) in q) for n in names if n)
        if found:
            matches.append(c)
    if request.object_name and not matches:
        return [], "object_not_found", []
    if matches and not entries:
        candidates = matches
    if request.object_name and entries:
        candidates = matches
    if len(matches) > 1 and not entries and request.scope_mode != "compare":
        return [], "needs_clarification", matches
    if len(entries) > 1 and request.scope_mode != "compare":
        raise AppError("COMPARE_MODE_REQUIRED", "多个明确对象请使用compare模式。", 400)
    return candidates, "resolved", matches


def preview(session, identifier, request, *, query_vector=None):
    # Internal runner only. The HTTP schema deliberately has no vector field.
    if query_vector is not None:
        if (request.vector_probe_chunk_id or len(query_vector) != 1024
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in query_vector)
                or sum(v*v for v in query_vector) <= 0):
            raise AppError("INVALID_QUERY_VECTOR", "问题向量必须是有限非零的1024维向量，且不能同时使用探针。", 400)
    # One MVCC snapshot covers source validation, both SQL lanes and citations.
    if not session.in_transaction():
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    build, snap, plan, catalog, rows = load(session, identifier)
    chosen, resolution, matches = resolve(catalog, request)
    result = {"retrieval_version": VERSION, "build_id": identifier, "snapshot_id": snap["id"],
              "manifest_hash": snap["manifest_hash"], "build_fingerprint": build["build_fingerprint"],
              "mode": "hybrid_corpus_probe" if request.vector_probe_chunk_id else "keyword_only",
              "query": request.query, "queryable": False, "model_calls": 0, "external_calls": 0,
              "rerank_status": "not_requested", "web_status": "not_attempted", "query_embedding": "not_performed",
              "scope": {"mode": request.scope_mode, "resolution": resolution,
                        "industry_ids": sorted({c["industry_id"] for c in chosen}),
                        "entry_ids": sorted(c["entry_id"] for c in chosen), "inherited_from_previous_turn": False},
              "clarification_candidates": [{k:v for k,v in c.items() if k != "chunks"} for c in matches] if resolution == "needs_clarification" else [],
              "candidates": [], "trace": {"filter_before_top_k": True, "groups": []},
              "evidence_sufficiency": "not_assessed", "status": resolution}
    if resolution != "resolved":
        return result
    eligible_entries = {c["entry_id"] for c in chosen}
    allowed = [r for r in rows if r.payload["entry_id"] in eligible_entries]
    probe = None
    vector = query_vector
    if query_vector is not None:
        result.update(mode="hybrid_query_vector", query_embedding="provided_by_bounded_runner")
    if request.vector_probe_chunk_id:
        probe = next((r for r in rows if r.payload["chunk_id"] == request.vector_probe_chunk_id), None)
        if probe is None:
            raise AppError("PROBE_NOT_IN_BUILD", "探针必须是本候选构建的已有分块。", 400)
        vector = [float(v) for v in probe.embedding]
        if any(not math.isfinite(v) for v in vector) or sum(v*v for v in vector) <= 0:
            raise AppError("INVALID_PROBE_VECTOR", "探针向量不合法。", 409)
        result["vector_probe"] = {"chunk_id": request.vector_probe_chunk_id,
                                  "kind": "stored_document_vector_not_query_embedding",
                                  "industry_id": probe.kb_id, "outside_selected_scope": probe not in allowed}
    # The legacy tokenizer retains standalone underscores from URLs. These are
    # punctuation, not useful query terms; otherwise a nonsense query can hit
    # nearly every source URL merely through its separators.
    terms = list(dict.fromkeys(t for t in tokenize(request.query).split() if any(c.isalnum() for c in t)))[:60]
    grouping = "entry_id" if request.entry_ids or len(matches) > 1 else "industry_id"
    groups = {}
    for row in allowed:
        key = row.payload[grouping] if request.scope_mode == "compare" else "all"
        groups.setdefault(key, []).append(row)
    if len(groups) > request.top_k or len(groups) > request.candidate_k:
        raise AppError("COMPARE_BUDGET_TOO_SMALL", "topK/candidateK不足以保留各比较方。", 400)
    sources = {s["source_id"]: s for d in plan["documents"] for s in d["sources"]}
    ranked_groups, byrow = {}, {r.id:r for r in allowed}
    for index, (group, members) in enumerate(sorted(groups.items())):
        limit = request.candidate_k//len(groups)+(index < request.candidate_k % len(groups))
        params = {"ids": [r.id for r in members], "limit": limit}
        text_rows, vector_rows = [], []
        if terms:
            params["terms"] = " | ".join(terms)
            text_rows = session.execute(text("WITH eligible AS MATERIALIZED (SELECT id,search_vector FROM chunks WHERE id=ANY(:ids)) SELECT id,ts_rank(search_vector,to_tsquery('simple',:terms)) AS score FROM eligible WHERE search_vector @@ to_tsquery('simple',:terms) ORDER BY score DESC,id LIMIT :limit"), params).all()
        if vector is not None:
            params["vector"] = "["+",".join(str(v) for v in vector)+"]"
            vector_rows = session.execute(text("WITH eligible AS MATERIALIZED (SELECT id,embedding FROM chunks WHERE id=ANY(:ids)) SELECT id,1-(embedding <=> CAST(:vector AS vector)) AS score FROM eligible ORDER BY embedding <=> CAST(:vector AS vector),id LIMIT :limit"), params).all()
        fused = {}
        for lane, items, weight in (("keyword", text_rows, 0.5 if vector is not None else 1.0), ("vector", vector_rows, 0.5)):
            for rank, (rid, score) in enumerate(items, 1):
                if not math.isfinite(float(score)):
                    raise AppError("INVALID_RETRIEVAL_SCORE", "候选分数异常。", 409)
                item = fused.setdefault(rid, {"rrf_score": 0.0, "keyword_score": None, "vector_score": None,
                                              "keyword_rank": None, "vector_rank": None})
                item[lane+"_score"], item[lane+"_rank"] = float(score), rank
                item["rrf_score"] += weight/(60+rank)
        ranked, seen = [], set()
        for rid in sorted(fused, key=lambda k: (-fused[k]["rrf_score"], k)):
            p = byrow[rid].payload
            key = (p["entry_id"], p["revision_id"], p["section_id"], parsing.digest(byrow[rid].content.encode()))
            if key in seen:
                continue
            seen.add(key); ranked.append((rid, fused[rid]))
            if len(ranked) == limit:
                break
        ranked_groups[group] = ranked
        result["trace"]["groups"].append({"group": group, "eligible_chunks": len(members), "candidate_limit": limit,
                                            "keyword_ids": [byrow[rid].payload["chunk_id"] for rid,_ in text_rows],
                                            "vector_ids": [byrow[rid].payload["chunk_id"] for rid,_ in vector_rows],
                                            "fused_ids": [byrow[rid].payload["chunk_id"] for rid,_ in ranked],
                                            "missing_evidence": not bool(ranked)})
    # Round robin preserves each comparison side within the same total budget.
    selected = []
    for i in range(request.candidate_k):
        for group in sorted(ranked_groups):
            if i < len(ranked_groups[group]):
                selected.append((group, *ranked_groups[group][i]))
    result["trace"].update(eligible_chunks=len(allowed), candidate_count=len(selected), keyword_terms=terms,
                           vector_algorithm="exact_cosine" if vector is not None else "not_run",
                           dedup_key="entry/revision/section/excerpt_hash",
                           ranked_candidate_ids=[byrow[rid].payload["chunk_id"] for _,rid,_ in selected])
    for group, rid, scores in selected[:request.top_k]:
        p = byrow[rid].payload
        result["candidates"].append({"chunk_id": p["chunk_id"], "group": group, "scores": scores,
            **{k:p[k] for k in ("entry_id", "revision_id", "industry_id", "section_id", "section_path", "source_relative_path",
                                "source_spans", "rendered_excerpt", "source_associations", "context", "evidence_anchor")},
            "sources": [sources[sid] for sid in p["source_ids"]]})
    result["status"] = "candidates" if result["candidates"] else "no_match"
    return result
