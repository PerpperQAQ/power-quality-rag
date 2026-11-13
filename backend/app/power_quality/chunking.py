"""Pure, offline candidate input planner. No providers, files, jobs or vectors.

The exact embedding_text and batch ordering are the future build contract.
Heading bodies and table rows are atomic: a soft character target never permits
truncating a condition. Source associations are recorded, not adjudicated.
"""
from .parsing import digest, fingerprint

CHUNKER_VERSION = "pq-heading-row-v1"
MODEL = "text-embedding-v4"
MAX_INPUT_BYTES = 8000
BATCH_SIZE = 10


def partitions(parsed, line_count):
    headings = sorted(parsed["sections"], key=lambda s: s["line_start"])
    stack, result = [], []
    if not headings or headings[0]["line_start"] > 1:
        result.append({"section_id": "preamble", "section_path": [], "level": 0,
                       "line_start": 0, "body_start": 1,
                       "body_end": headings[0]["line_start"]-1 if headings else line_count,
                       "ancestors": [], "root_section_id": None})
    for index, heading in enumerate(headings):
        while stack and stack[-1]["level"] >= heading["level"]:
            stack.pop()
        item = {**heading, "body_start": heading["line_start"]+1,
                "body_end": headings[index+1]["line_start"]-1 if index+1 < len(headings) else line_count,
                "ancestors": list(stack)}
        item["root_section_id"] = next((v["section_id"] for v in [*stack, item] if v["level"] == 2), None)
        result.append(item)
        stack.append(item)
    return result


def source_registry(revision):
    parsed = revision["parsed"]
    entries = []
    for source in parsed["sources"]:
        entries.append({**source, "entry_id": parsed["entry_id"], "revision_id": revision["revision_id"],
                        "origin": "card_bibliography", "claim_support_verified": False,
                        "content_fetched": False, "supported_sections": None})
    for link in parsed["links"]:
        if any(s["target"] == link["target"] and s["line"] == link["line"] for s in entries):
            continue
        entries.append({**link, "source_id": "pql-"+fingerprint([revision["revision_id"], link])[:32],
                        "entry_id": parsed["entry_id"], "revision_id": revision["revision_id"],
                        "origin": "inline_link", "claim_support_verified": False,
                        "content_fetched": False, "supported_sections": None})
    return entries


def revision_chunks(revision, config):
    parsed, original = revision["parsed"], revision["original_text"]
    lines = original.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1]+len(line))
    registry = source_registry(revision)
    chunks, excluded = [], []

    def span(start, end, role):
        while start <= end and not lines[start-1].strip():
            start += 1
        while end >= start and not lines[end-1].strip():
            end -= 1
        if start > end:
            return None
        raw = original[offsets[start-1]:offsets[end]]
        return {"line_start": start, "line_end": end, "char_start": offsets[start-1],
                "char_end": offsets[end], "offset_unit": "unicode_codepoints", "role": role,
                "text": raw, "sha256": digest(raw.encode("utf-8"))}

    def text_for(spans):
        # Each span stays exact in source_spans; joining is explicitly rendered.
        return "\n".join(v["text"].rstrip("\r\n") for v in sorted(spans, key=lambda s: s["line_start"]))

    def header(section):
        return (f"行业：{parsed['industry_name']}\n对象：{parsed['canonical_name']}\n"
                f"章节：{' / '.join(section['section_path'])}\n"
                f"现场验证：{parsed['field_verification_raw'] or '未知'}；现场记录未经核验\n")

    def emit(section, spans, kind, row_lines=(), table_id=None):
        spans = [s for s in spans if s]
        if not spans:
            return
        body = text_for(spans)
        embedding = header(section)+"\n"+body
        encoded = embedding.encode("utf-8")
        anchor = fingerprint([revision["revision_id"], section["section_id"], spans])
        associations = []
        for source in registry:
            in_excerpt = any(s["line_start"] <= source["line"] <= s["line_end"] for s in spans)
            if source["origin"] == "card_bibliography" or in_excerpt:
                associations.append({"source_id": source["source_id"],
                                     "relation": "explicit_link_in_excerpt" if in_excerpt else "card_bibliography",
                                     "claim_support_verified": False})
        issues = []
        if len(embedding) > config["target_characters"]:
            issues.append("ATOMIC_CONTENT_OVER_SOFT_TARGET")
        if len(encoded) > MAX_INPUT_BYTES:
            issues.append("INPUT_BYTE_BUDGET_EXCEEDED")
        chunk_id = "pqc-"+fingerprint([CHUNKER_VERSION, config, anchor, digest(encoded)])[:40]
        chunks.append({"chunk_id": chunk_id, "evidence_anchor": anchor,
                       "entry_id": parsed["entry_id"], "revision_id": revision["revision_id"],
                       "industry_id": parsed["industry_id"], "equipment_id": parsed["equipment_id"],
                       "source_relative_path": parsed["source_relative_path"], "raw_sha256": parsed["sha256"],
                       "section_id": section["section_id"], "section_path": section["section_path"],
                       "root_section_id": section["root_section_id"], "kind": kind,
                       "table_id": table_id, "table_row_lines": list(row_lines), "source_spans": spans,
                       "rendered_excerpt": body, "excerpt_is_joined_spans": True,
                       "embedding_text": embedding, "embedding_sha256": digest(encoded),
                       "input_characters": len(embedding), "input_utf8_bytes": len(encoded),
                       "source_ids": [s["source_id"] for s in associations], "source_associations": associations,
                       "context": {k: parsed[k] for k in (
                           "canonical_name", "object_type", "aliases", "source_state", "source_confidence_raw",
                           "field_verification_raw", "field_verification_status", "field_notes_verified", "evidence_basis")},
                       "field_record_state": "recorded_unverified" if section["root_section_id"] == "s11" else None,
                       "issues": issues, "input_eligible": len(encoded) <= MAX_INPUT_BYTES})

    for section in partitions(parsed, len(lines)):
        start, end = section["body_start"], section["body_end"]
        if parsed["kind"] == "card" and section["root_section_id"] == "s10":
            excluded.append({"section_id": section["section_id"], "line_start": section["line_start"],
                             "line_end": end, "reason": "source_registry_only"})
            continue
        if start > end:
            continue
        ancestors = [span(a["body_start"], a["body_end"], "ancestor_context") for a in section["ancestors"]]
        ancestors = [s for s in ancestors if s]
        tables = [t for t in parsed["tables"] if start <= t["line_start"] <= end]
        if not tables:
            own = span(start, end, "section_body")
            if own:
                emit(section, [*ancestors, own], "prose")
            continue
        # Repeat surrounding section prose so a condition before/after the table
        # cannot be detached from any row. Never reinterpret cell values.
        prose, cursor = [], start
        for table in tables:
            piece = span(cursor, table["line_start"]-1, "section_context")
            if piece:
                prose.append(piece)
            cursor = table["line_end"]+1
        piece = span(cursor, end, "section_context")
        if piece:
            prose.append(piece)
        for table in tables:
            table_header = span(table["line_start"], table["line_start"]+1, "table_header")
            common = [*ancestors, *prose, table_header]
            rows = table["rows"]
            if parsed["kind"] == "card" and section["root_section_id"] == "s11":
                for row in rows:
                    if row.get("record_state") != "recorded_unverified":
                        excluded.append({"section_id": section["section_id"], "line_start": row["line"],
                                         "line_end": row["line"], "reason": "field_row_placeholder_or_undated"})
                rows = [r for r in rows if r.get("record_state") == "recorded_unverified"]
            if not rows:
                excluded.append({"section_id": section["section_id"], "line_start": table["line_start"],
                                 "line_end": table["line_end"], "reason": "table_without_indexable_rows"})
                continue
            group = []
            for row in rows:
                candidate = [*common, *(span(r["line"], r["line"], "table_row") for r in [*group, row])]
                rendered = header(section)+"\n"+text_for(candidate)
                if group and (len(rendered) > config["target_characters"] or len(rendered.encode()) > MAX_INPUT_BYTES):
                    emit(section, [*common, *(span(r["line"], r["line"], "table_row") for r in group)],
                         "table_rows", [r["line"] for r in group], table["table_id"])
                    group = []
                group.append(row)
            if group:
                emit(section, [*common, *(span(r["line"], r["line"], "table_row") for r in group)],
                     "table_rows", [r["line"] for r in group], table["table_id"])
        if prose and not any(c["section_id"] == section["section_id"] for c in chunks):
            emit(section, [*ancestors, *prose], "prose")
    return {"revision_id": revision["revision_id"], "entry_id": parsed["entry_id"],
            "source_relative_path": parsed["source_relative_path"], "sources": registry,
            "chunks": chunks, "excluded": excluded}


def plan(snapshot, revisions, target_characters=900):
    if target_characters not in (600, 900):
        raise ValueError("target_characters must be 600 or 900")
    config = {"target_characters": target_characters, "overlap_characters": 0,
              "atomic_policy": "whole_heading_body_or_table_row_with_context",
              "embedding_model": MODEL, "embedding_dimensions": 1024,
              "max_input_utf8_bytes": MAX_INPUT_BYTES, "batch_size": BATCH_SIZE,
              "batch_scope": "per_revision", "automatic_retries": 0}
    documents = [revision_chunks(r, config) for r in revisions]
    batches = []
    for doc in documents:
        eligible = [c for c in doc["chunks"] if c["input_eligible"]]
        for i in range(0, len(eligible), BATCH_SIZE):
            batch = eligible[i:i+BATCH_SIZE]
            batches.append({"batch_number": len(batches)+1, "revision_id": doc["revision_id"],
                            "chunk_ids": [c["chunk_id"] for c in batch],
                            "input_characters": sum(c["input_characters"] for c in batch),
                            "input_utf8_bytes": sum(c["input_utf8_bytes"] for c in batch),
                            "input_hash": fingerprint([c["embedding_text"] for c in batch])})
    chunks = [c for d in documents for c in d["chunks"]]
    blocked = [c["chunk_id"] for c in chunks if not c["input_eligible"]]
    empty = [d["revision_id"] for d in documents if not d["chunks"]]
    budget = {"documents": len(documents), "chunks": len(chunks), "eligible_chunks": len(chunks)-len(blocked),
              "blocked_chunks": len(blocked), "planned_embedding_requests": len(batches),
              "input_characters": sum(b["input_characters"] for b in batches),
              "input_utf8_bytes": sum(b["input_utf8_bytes"] for b in batches),
              "max_item_utf8_bytes": max((c["input_utf8_bytes"] for c in chunks), default=0),
              "planning_token_units": sum(b["input_utf8_bytes"] for b in batches),
              "planning_token_method": "one_unit_per_utf8_byte_conservative_proxy_not_tokenizer",
              "actual_input_tokens": None, "actual_cost_cny": None,
              "soft_target_exceeded_chunks": sum("ATOMIC_CONTENT_OVER_SOFT_TARGET" in c["issues"] for c in chunks)}
    plan_hash = fingerprint({"manifest_hash": snapshot["manifest_hash"], "parser_version": snapshot["parser_version"],
                             "chunker_version": CHUNKER_VERSION, "config": config, "documents": documents, "batches": batches})
    return {"snapshot_id": snapshot["id"], "manifest_hash": snapshot["manifest_hash"],
            "parser_version": snapshot["parser_version"], "chunker_version": CHUNKER_VERSION,
            "build_fingerprint": plan_hash, "config": config, "documents": documents,
            "batches": batches, "budget": budget, "blocked_chunk_ids": blocked,
            "empty_revision_ids": empty,
            "input_plan_valid": bool(chunks) and not blocked and not empty, "execution_authorized": False,
            "queryable": False, "model_calls": 0, "external_calls": 0}
