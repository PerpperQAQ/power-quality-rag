"""Transactional fixed snapshots, isolated from ingestion and model execution.

No provider, worker, parsing-OCR, HTTP or search imports belong in this module.
All writes are serialized by the existing revision row and committed by routes.
"""
import copy
import uuid
from pathlib import Path

from sqlalchemy import select

from ..config import settings
from ..db import Record, bump, revision_lock
from ..security import AppError
from ..state import now, record
from . import chunking, parsing


def source_root() -> Path:
    root = settings.power_quality_source_root
    if root is None:
        raise AppError("APF_SOURCE_UNCONFIGURED", "未配置本机 APF 来源目录；可使用带原文的预览请求。", 409)
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise AppError("SOURCE_UNAVAILABLE", "APF 来源目录不存在或是符号链接。", 409)
    return root.resolve()


def value(session, collection, identifier):
    row = record(session, collection, identifier)
    if row is None:
        raise AppError("NOT_FOUND", "未找到指定领域资源。", 404)
    return copy.deepcopy(row.data)


def insert_immutable(session, collection, identifier, data):
    old = record(session, collection, identifier)
    if old is not None:
        if old.data != data:
            raise AppError("IMMUTABLE_CONFLICT", "不可变原件或快照与已有内容不一致。", 409)
        return
    session.add(Record(collection=collection, id=identifier, session_id="", data=copy.deepcopy(data)))
    session.flush()


def request_key(actor, operation, request_id):
    return "pqq-" + parsing.fingerprint([actor["session_id"], operation, request_id])[:48]


def prior_request(session, actor, operation, request_id, payload):
    key = request_key(actor, operation, request_id)
    old = record(session, "pqRequests", key)
    if old is None:
        return key, None
    if old.data["payload_hash"] != parsing.fingerprint(payload):
        raise AppError("IDEMPOTENCY_CONFLICT", "相同请求 ID 已用于不同内容。", 409)
    return key, old.data["result_id"]


def save_request(session, key, payload, result_id):
    insert_immutable(session, "pqRequests", key, {"payload_hash": parsing.fingerprint(payload), "result_id": result_id})


def manifest_item(item):
    parsed = item["parsed"]
    return {"entry_id": parsed["entry_id"], "revision_id": item["revision_id"],
            "source_relative_path": parsed["source_relative_path"], "sha256": parsed["sha256"],
            "parsed_hash": item["parsed_hash"], "industry_id": parsed["industry_id"], "kind": parsed["kind"]}


def verify_item(item):
    try:
        parsed = item["parsed"]
        raw_hash = parsing.digest(item["original_text"].encode("utf-8"))
        parsed_hash = parsing.fingerprint(parsed)
        expected_id = "pqr-" + parsing.fingerprint([parsed["entry_id"], raw_hash, parsed_hash])[:48]
        valid = raw_hash == parsed["sha256"] and parsed_hash == item["parsed_hash"] and expected_id == item["revision_id"]
    except (KeyError, TypeError, UnicodeError):
        valid = False
    if not valid:
        raise AppError("SNAPSHOT_INTEGRITY_ERROR", "保存的原件或解析指纹不一致，请保留证据并重新核对。", 409)


def verify_import(data):
    for item in data["items"]:
        verify_item(item)
    manifest = [manifest_item(v) for v in data["items"]]
    expected = parsing.fingerprint({"items": manifest, "excluded": data["excluded"], "parser_version": data["parser_version"]})
    if manifest != data["manifest"] or expected != data["manifest_hash"]:
        raise AppError("SNAPSHOT_INTEGRITY_ERROR", "预览清单指纹不一致。", 409)


def import_view(session, identifier):
    data = value(session, "pqImports", identifier)
    verify_import(data)
    return {k: copy.deepcopy(data[k]) for k in ("id", "status", "created_at", "parser_version", "manifest", "manifest_hash", "excluded", "snapshot_id")} | {
        "items": [{"revision_id": v["revision_id"], "parsed": v["parsed"], "source_mode": v["source_mode"]} for v in data["items"]],
        "can_confirm": data["status"] == "previewed" and bool(data["items"]) and all(v["parsed"]["can_confirm"] for v in data["items"]),
        "model_calls": 0, "external_calls": 0, "queryable": False,
    }


def preview(session, request, actor):
    revision_lock(session)
    payload = request.model_dump(by_alias=True)
    key, prior = prior_request(session, actor, "preview", request.request_id, payload)
    if prior:
        return import_view(session, prior)
    items, excluded, seen, total = [], [], set(), 0
    local_root = None
    for file in request.files:
        path = parsing.canonical_path(file.relative_path)
        if path in seen:
            raise AppError("MANIFEST_CONFLICT", "同一批次来源路径重复（含 Unicode 规范化重复）。", 409)
        seen.add(path)
        info = parsing.identity(path)
        if info is None:
            excluded.append({"path": path, "reason": "outside_candidate_allowlist", "submitted_sha256": file.sha256})
            continue
        if file.content is None:
            local_root = local_root or source_root()
            raw = parsing.read_source(local_root, path)
            mode = "local_apf"
        else:
            try:
                raw = file.content.encode("utf-8")
            except UnicodeError:
                raise AppError("SOURCE_ENCODING", "来源不是有效 UTF-8 文本。") from None
            mode = "uploaded_text"
        total += len(raw)
        if total > parsing.MAX_BATCH_BYTES:
            raise AppError("SOURCE_LIMIT", "预览原件总量超过 16 MiB。", 413)
        if parsing.digest(raw) != file.sha256:
            raise AppError("SOURCE_HASH_MISMATCH", "来源 SHA256 与提交清单不一致，请重新预览。", 409)
        parsed = parsing.parse(path, raw, local_root if mode == "local_apf" else None)
        parsed_hash = parsing.fingerprint(parsed)
        revision_id = "pqr-" + parsing.fingerprint([info["entry_id"], parsed["sha256"], parsed_hash])[:48]
        items.append({"revision_id": revision_id, "parsed_hash": parsed_hash, "parsed": parsed,
                      "source_mode": mode, "original_text": raw.decode("utf-8")})
    items.sort(key=lambda v: v["parsed"]["source_relative_path"])
    excluded.sort(key=lambda v: v["path"])
    manifest = [manifest_item(item) for item in items]
    identifier = "pqi-" + uuid.uuid4().hex
    data = {"id": identifier, "status": "previewed", "created_at": now(), "parser_version": parsing.PARSER_VERSION, "items": items,
            "manifest": manifest, "manifest_hash": parsing.fingerprint({"items": manifest, "excluded": excluded, "parser_version": parsing.PARSER_VERSION}),
            "excluded": excluded, "snapshot_id": None, "source_root_hash": parsing.fingerprint(str(local_root)) if local_root else None,
            "actor": actor}
    insert_immutable(session, "pqImports", identifier, data)
    save_request(session, key, payload, identifier)
    bump(session)
    return import_view(session, identifier)


def confirm(session, identifier, request, actor):
    revision_lock(session)
    payload = {"import_id": identifier, **request.model_dump(by_alias=True)}
    key, prior = prior_request(session, actor, "confirm", request.request_id, payload)
    if prior:
        return snapshot_view(session, prior)
    data = value(session, "pqImports", identifier)
    verify_import(data)
    if request.manifest_hash != data["manifest_hash"]:
        raise AppError("REVIEW_STALE", "确认指纹不是当前预览清单。", 409)
    if data["status"] == "confirmed":
        save_request(session, key, payload, data["snapshot_id"])
        bump(session)
        return snapshot_view(session, data["snapshot_id"])
    if data["status"] != "previewed":
        raise AppError("IMPORT_NOT_CONFIRMABLE", "该批次已取消，不能继续确认。", 409)
    if data["parser_version"] != parsing.PARSER_VERSION:
        raise AppError("PREVIEW_PARSER_CHANGED", "解析版本已变化，请重新预览；历史快照仍保留原版本。", 409)
    if not data["items"] or not all(v["parsed"]["can_confirm"] for v in data["items"]):
        raise AppError("SOURCE_REVIEW_REQUIRED", "没有可确认条目或存在结构错误，请修正后重新预览。", 409)
    if (data["excluded"] or any(v["parsed"]["issues"] for v in data["items"])) and not request.acknowledge_warnings:
        raise AppError("WARNINGS_NOT_ACKNOWLEDGED", "请先核对并确认清单中的警告与排除项。", 409)
    if data["source_root_hash"]:
        root = source_root()
        if parsing.fingerprint(str(root)) != data["source_root_hash"]:
            raise AppError("SOURCE_ROOT_CHANGED", "来源目录已变化，请重新预览。", 409)
        for item in data["items"]:
            if item["source_mode"] == "local_apf":
                raw = parsing.read_source(root, item["parsed"]["source_relative_path"])
                if parsing.digest(raw) != item["parsed"]["sha256"]:
                    raise AppError("SOURCE_CHANGED", "APF 原件在预览后变化，请重新预览和确认。", 409)
                reparsed = parsing.parse(item["parsed"]["source_relative_path"], raw, root)
                if parsing.fingerprint(reparsed) != item["parsed_hash"]:
                    raise AppError("SOURCE_METADATA_CHANGED", "来源解析或本地引用状态在预览后变化，请重新预览。", 409)
    for item in data["items"]:
        parsed = item["parsed"]
        entry = {k: parsed[k] for k in ("entry_id", "equipment_id", "industry_id", "kind", "source_relative_path", "external_key", "source_system")}
        insert_immutable(session, "pqEntries", parsed["entry_id"], entry)
        revision = {k: item[k] for k in ("revision_id", "parsed_hash", "parsed", "original_text")}
        insert_immutable(session, "pqRevisions", item["revision_id"], revision)
    snapshot_id = "pqsnap-" + uuid.uuid4().hex
    confirmation_id = "pqconfirm-" + uuid.uuid4().hex
    confirmation = {"id": confirmation_id, "import_id": identifier, "snapshot_id": snapshot_id,
                    "manifest_hash": data["manifest_hash"], "actor": actor, "confirmed_at": now(),
                    "acknowledge_warnings": request.acknowledge_warnings, "scope": "fixed_manifest_not_expert_content_approval"}
    snapshot = {"id": snapshot_id, "import_id": identifier, "confirmation_id": confirmation_id,
                "manifest": data["manifest"], "manifest_hash": data["manifest_hash"], "excluded": data["excluded"],
                "parser_version": data["parser_version"], "created_at": confirmation["confirmed_at"],
                "build_status": "not_started", "build_id": None, "queryable": False}
    insert_immutable(session, "pqConfirmations", confirmation_id, confirmation)
    insert_immutable(session, "pqSnapshots", snapshot_id, snapshot)
    record(session, "pqImports", identifier).data = {**data, "status": "confirmed", "snapshot_id": snapshot_id}
    save_request(session, key, payload, snapshot_id)
    bump(session)
    return snapshot_view(session, snapshot_id)


def revision_view(session, identifier):
    data = value(session, "pqRevisions", identifier)
    verify_item(data)
    return data


def snapshot_view(session, identifier):
    data = value(session, "pqSnapshots", identifier)
    expected = parsing.fingerprint({"items": data["manifest"], "excluded": data["excluded"], "parser_version": data["parser_version"]})
    confirmation = value(session, "pqConfirmations", data["confirmation_id"])
    if expected != data["manifest_hash"] or confirmation["manifest_hash"] != expected or confirmation["snapshot_id"] != identifier:
        raise AppError("SNAPSHOT_INTEGRITY_ERROR", "快照与确认指纹不一致。", 409)
    for item in data["manifest"]:
        revision = revision_view(session, item["revision_id"])
        if manifest_item(revision) != item:
            raise AppError("SNAPSHOT_INTEGRITY_ERROR", "快照原件映射不一致。", 409)
    from .releases import active
    published = active(session)
    return {**data, "queryable": bool(published["queryable"] and published["active_snapshot_id"] == identifier),
            "activation_supported": True, "confirmation": confirmation, "model_calls": 0, "external_calls": 0}


def chunk_preview(session, identifier, request):
    snapshot = snapshot_view(session, identifier)
    if snapshot["manifest_hash"] != request.manifest_hash:
        raise AppError("REVIEW_STALE", "分块请求与固定快照指纹不一致。", 409)
    revisions = [revision_view(session, item["revision_id"]) for item in snapshot["manifest"]]
    return chunking.plan(snapshot, revisions, request.target_characters)


def cancel(session, identifier, request, actor):
    revision_lock(session)
    payload = {"import_id": identifier, **request.model_dump(by_alias=True)}
    key, prior = prior_request(session, actor, "cancel", request.request_id, payload)
    if prior:
        return import_view(session, prior)
    data = value(session, "pqImports", identifier)
    verify_import(data)
    if data["status"] == "confirmed":
        raise AppError("IMPORT_ALREADY_CONFIRMED", "已确认快照不能用取消预览来撤销。", 409)
    record(session, "pqImports", identifier).data = {**data, "status": "canceled"}
    save_request(session, key, payload, identifier)
    bump(session)
    return import_view(session, identifier)


def current_state(session):
    current = record(session, "pqState", "active")
    active_id = current.data.get("active_snapshot_id") if current else None
    from .releases import active
    published = active(session)
    return {**published, "activation_supported": True,
            "snapshots": [{"id": r.id, "manifest_hash": r.data["manifest_hash"], "build_status": r.data["build_status"],
                           "queryable": bool(published["queryable"] and active_id == r.id)}
                          for r in session.scalars(select(Record).where(Record.collection == "pqSnapshots").order_by(Record.id))],
            "model_calls": 0, "external_calls": 0}


def request_activation(session, identifier, request):
    revision_lock(session)
    data = snapshot_view(session, identifier)
    if current_state(session)["active_snapshot_id"] != request.expected_active_snapshot_id:
        raise AppError("RELEASE_CONFLICT", "当前有效版本已变化，请刷新后再核对。", 409)
    if data["build_status"] != "ready":
        raise AppError("BUILD_NOT_READY", "快照尚无可用索引；确认不会触发构建或生效。", 409)
    raise AppError("INDEX_PIPELINE_UNAVAILABLE", "本阶段没有真实索引构建/验证链路，不能启用该快照。", 409)
