"""APF source identities and lossless, deterministic Markdown structure.

This is a source preview parser, not an embedding chunker. It never fetches a
URL, imports the ingestion worker, or infers a measured field observation.
"""
import hashlib
import json
import os
import posixpath
import re
import stat
import unicodedata
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from ..security import AppError

PARSER_VERSION = "apf-source-v1"
SCHEMA_VERSION = 1
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_BATCH_BYTES = 16 * 1024 * 1024
INDUSTRIES = {
    "01-商业综合体园区配电": "pq-industry-01",
    "02-光伏新能源储能充电桩": "pq-industry-02",
    "03-机械装备机加工": "pq-industry-03",
    "04-水暖厨卫阀门": "pq-industry-04",
    "05-石材陶瓷建材加工": "pq-industry-05",
    "06-鞋服轻纺制鞋机械": "pq-industry-06",
    "07-食品日用轻工包装印刷": "pq-industry-07",
    "08-消防器材": "pq-industry-08",
    "09-钢铁冶金轧钢连铸": "pq-industry-09",
}
NAMESPACE = uuid.UUID("58838060-02eb-49d5-99de-26cafca7dce0")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fingerprint(value) -> str:
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode())


def canonical_path(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 600
            or value.startswith("/") or "\\" in value or "%" in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise AppError("SOURCE_PATH_INVALID", "来源必须是无越界、无编码歧义的规范相对路径。")
    return unicodedata.normalize("NFC", value)


def identity(path: str) -> dict | None:
    path = canonical_path(path)
    parts = path.split("/")
    if len(parts) < 3 or parts[0] != "processed" or parts[1] not in INDUSTRIES:
        return None
    card = len(parts) == 4 and parts[2] == "cards" and parts[3].endswith(".md") and len(parts[3]) > 3
    overview = len(parts) == 3 and parts[2] == "行业总览.md"
    if not card and not overview:
        return None
    entry_id = "pqe-" + str(uuid.uuid5(NAMESPACE, "apf:" + path))
    return {"entry_id": entry_id, "equipment_id": entry_id if card else None,
            "industry_id": INDUSTRIES[parts[1]], "industry_name": parts[1][3:],
            "kind": "card" if card else "industry_overview", "source_relative_path": path,
            "source_system": "apf", "external_key": path}


def read_source(root: Path, relative: str) -> bytes:
    """Open each component relative to a trusted root; reject symlink races."""
    relative = canonical_path(relative)
    fd = None
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in relative.split("/")[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        leaf = os.open(relative.split("/")[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(leaf, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise AppError("SOURCE_PATH_INVALID", "来源必须是普通文件。")
            if info.st_size > MAX_FILE_BYTES:
                raise AppError("SOURCE_TOO_LARGE", "单个来源超过 2 MiB。", 413)
            content = stream.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise AppError("SOURCE_TOO_LARGE", "单个来源超过 2 MiB。", 413)
            return content
    except OSError:
        raise AppError("SOURCE_UNAVAILABLE", "来源不存在、不可读或包含符号链接。", 409) from None
    finally:
        if fd is not None:
            os.close(fd)


def scan(root: Path) -> dict:
    processed = root / "processed"
    if processed.is_symlink() or not processed.is_dir():
        raise AppError("SOURCE_UNAVAILABLE", "APF processed 目录不存在或是符号链接。", 409)
    included, excluded, seen, total = [], [], set(), 0
    for directory, dirs, files in os.walk(processed, followlinks=False):
        for name in sorted(dirs[:]):
            p = Path(directory) / name
            if p.is_symlink() or name.startswith("."):
                dirs.remove(name)
                excluded.append({"path": str(p.relative_to(root)), "reason": "symlink_or_hidden_directory"})
        for name in sorted(files):
            path = str((Path(directory) / name).relative_to(root))
            try:
                normalized = canonical_path(path)
                info = identity(normalized)
            except AppError:
                excluded.append({"path": path, "reason": "invalid_path"})
                continue
            if normalized in seen:
                raise AppError("MANIFEST_CONFLICT", "来源路径经 Unicode 规范化后重复。", 409)
            seen.add(normalized)
            if not info:
                excluded.append({"path": path, "reason": "outside_candidate_allowlist"})
                continue
            # A non-canonical on-disk spelling must not silently open another file.
            if path != normalized:
                excluded.append({"path": path, "reason": "noncanonical_disk_path"})
                continue
            try:
                raw = read_source(root, path)
            except AppError as error:
                excluded.append({"path": path, "reason": error.code})
                continue
            total += len(raw)
            if total > MAX_BATCH_BYTES or len(included) >= 1000:
                raise AppError("SOURCE_LIMIT", "候选来源总量超过当前预览上限。", 413)
            included.append({**info, "sha256": digest(raw), "bytes": len(raw), "name": Path(path).stem})
    included.sort(key=lambda v: v["source_relative_path"])
    names = {}
    for item in included:
        if item["kind"] == "card":
            names.setdefault(item["name"], []).append(item["entry_id"])
    return {"schema_version": SCHEMA_VERSION, "included": included, "excluded": sorted(excluded, key=lambda v: v["path"]),
            "counts": {"candidates": len(included), "cards": sum(v["kind"] == "card" for v in included),
                       "overviews": sum(v["kind"] == "industry_overview" for v in included), "excluded": len(excluded)},
            "same_name_groups": [{"name": k, "entry_ids": v} for k, v in sorted(names.items()) if len(v) > 1],
            "manifest_hash": fingerprint(included), "model_calls": 0, "external_calls": 0}


def cells(line: str) -> list[str]:
    """Keep Obsidian alias pipes and escaped pipes inside one table cell."""
    output, current, wiki, code, i = [], [], False, False, 0
    line = line.strip().strip("|")
    while i < len(line):
        if line[i:i+2] == "\\|":
            current.append("|"); i += 2; continue
        if line[i:i+2] == "[[":
            wiki = True
        elif line[i:i+2] == "]]":
            wiki = False
        if line[i] == "`":
            code = not code
        if line[i] == "|" and not wiki and not code:
            output.append("".join(current).strip()); current = []
        else:
            current.append(line[i])
        i += 1
    output.append("".join(current).strip())
    return output


def link_target(raw: str, path: str, root: Path | None) -> dict:
    raw = raw.strip().strip("<>")
    if raw.startswith(("待", "暂无", "未提供")):
        return {"kind": "placeholder", "target": raw, "link_status": "missing"}
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return {"kind": "unsupported", "target": raw, "link_status": "blocked"}
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        return {"kind": "web", "target": raw, "link_status": "present_unchecked"}
    if parsed.scheme:
        return {"kind": "unsupported", "target": raw, "link_status": "blocked"}
    target = unquote(raw).split("#", 1)[0].split("|", 1)[0]
    if not target:
        return {"kind": "local", "target": raw, "link_status": "anchor_only"}
    if "\\" in target or any(ord(c) < 32 for c in target):
        return {"kind": "local", "target": raw, "link_status": "blocked"}
    if target.startswith("/"):
        if root is None or not target.startswith(str(root) + "/"):
            return {"kind": "local", "target": raw, "link_status": "outside_source_root"}
        target = target[len(str(root))+1:]
    elif not target.startswith(("processed/", "raw-research/", "docs/", "data/")):
        target = posixpath.join(posixpath.dirname(path), target)
    target = posixpath.normpath(target)
    try:
        target = canonical_path(target)
    except AppError:
        return {"kind": "local", "target": raw, "link_status": "blocked"}
    if raw.endswith("/"):
        return {"kind": "local", "target": raw, "resolved_path": target, "link_status": "directory_reference"}
    if not PurePosixPath(target).suffix:
        target += ".md"
    status = "unchecked"
    if root is not None:
        try:
            # Only local file existence/readability is checked. No source content
            # is promoted into evidence or copied into the snapshot by this probe.
            read_source(root, target)
            status = "local_available"
        except AppError:
            status = "unresolved"
    return {"kind": "local", "target": raw, "resolved_path": target, "link_status": status}


def parse(path: str, raw: bytes, root: Path | None = None) -> dict:
    info = identity(path)
    if not info:
        raise AppError("SOURCE_NOT_CANDIDATE", "文件不在九行业正式卡片/行业总览白名单内。")
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise AppError("SOURCE_SIZE_INVALID", "来源为空或超过单文件上限。")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AppError("SOURCE_ENCODING", "当前确定性导入仅接受 UTF-8 Markdown。") from None
    if "\x00" in text:
        raise AppError("SOURCE_ENCODING", "Markdown 不能包含 NUL 字符。")
    lines = text.splitlines()
    issues, sections, headings, fields, tables, links = [], [], [], [], [], []
    active, fence, line_index = [], None, 0
    while line_index < len(lines):
        line = lines[line_index]
        fence_match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_match:
            marker = fence_match[1][0]
            if fence is None: fence = marker
            elif fence == marker: fence = None
            line_index += 1; continue
        if fence:
            line_index += 1; continue
        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line.lstrip("\ufeff"))
        if heading:
            level, title = len(heading[1]), heading[2]
            while active and active[-1]["level"] >= level:
                active.pop()
            number = re.match(r"^(\d+(?:\.\d+)*)(?:[.、]\s*|\s+)", title)
            sid = "s" + number[1] if number else "h" + str(line_index+1)
            if any(v["section_id"] == sid for v in headings):
                issues.append({"code": "DUPLICATE_SECTION", "severity": "error", "line": line_index+1})
            item = {"section_id": sid, "level": level, "title": title,
                    "line_start": line_index+1, "section_path": [v["title"] for v in active] + [title]}
            headings.append(item); active.append(item)
        field = re.match(r"^\s*-\s+([^：:]+)[：:]\s*(.*)$", line)
        if field:
            fields.append({"name": field[1], "raw": field[2], "line": line_index+1,
                           "section_id": active[-1]["section_id"] if active else None})
        for match in re.finditer(r"\[\[([^\]]+)\]\]|(?<!!)\[[^\]]*\]\(([^)]+)\)", line):
            value = (match[1].split("|", 1)[0] if match[1] else match[2])
            links.append({"raw": match[0], "line": line_index+1, **link_target(value, path, root)})
        nxt = cells(lines[line_index+1]) if line_index+1 < len(lines) else []
        if "|" in line and nxt and all(re.fullmatch(r":?-{3,}:?", cell) for cell in nxt):
            headers, rows, start = cells(line), [], line_index+1
            table_section = active[-1]["section_id"] if active else None
            line_index += 2
            while line_index < len(lines) and lines[line_index].strip().startswith("|"):
                values = cells(lines[line_index])
                if len(values) != len(headers):
                    issues.append({"code": "TABLE_WIDTH_MISMATCH", "severity": "warning", "line": line_index+1})
                rows.append({"values": values, "line": line_index+1, "raw": lines[line_index]})
                for match in re.finditer(r"\[\[([^\]]+)\]\]|(?<!!)\[[^\]]*\]\(([^)]+)\)", lines[line_index]):
                    value = match[1].split("|", 1)[0] if match[1] else match[2]
                    links.append({"raw": match[0], "line": line_index+1, **link_target(value, path, root)})
                line_index += 1
            tables.append({"table_id": f"table-{start}", "section_id": table_section,
                           "headers": headers, "rows": rows, "line_start": start, "line_end": line_index})
            continue
        line_index += 1
    for index, heading in enumerate(headings):
        end = next((v["line_start"]-1 for v in headings[index+1:] if v["level"] <= heading["level"]), len(lines))
        sections.append({**heading, "line_end": end, "text": "\n".join(lines[heading["line_start"]-1:end])})
    titles = [v["title"] for v in headings if v["level"] == 1]
    if len(titles) != 1:
        issues.append({"code": "TITLE_MISSING_OR_MULTIPLE", "severity": "error"})
    if info["kind"] == "card":
        missing = [i for i in range(1, 12) if not any(v["section_id"] == f"s{i}" and v["level"] == 2 for v in headings)]
        if missing:
            issues.append({"code": "CARD_SECTIONS_MISSING", "severity": "error", "sections": missing})
    first_fields = {}
    for field in (v for v in fields if v["section_id"] == "s1"):
        if field["name"] in first_fields and field["raw"] != first_fields[field["name"]]["raw"]:
            issues.append({"code": "CONFLICTING_SOURCE_FIELD", "severity": "error", "field": field["name"], "line": field["line"]})
        else:
            first_fields[field["name"]] = field
    def value(name): return first_fields.get(name, {}).get("raw")
    confidence = value("资料可信度")
    status_raw = value("现场验证状态")
    status = "unseen" if status_raw == "未见过" else "unknown"
    if info["kind"] == "card" and status != "unseen":
        issues.append({"code": "FIELD_STATUS_REVIEW_REQUIRED", "severity": "warning", "raw": status_raw})
    sources = []
    for table in tables:
        if table["section_id"] != "s10": continue
        for row in table["rows"]:
            data = dict(zip(table["headers"], row["values"]))
            target = data.get("链接", "")
            web = re.search(r"https?://[^\s|<>]+", target)
            if web:
                relation = link_target(web[0].rstrip(")"), path, root)
            else:
                local = next((v for v in links if v["line"] == row["line"]), None)
                if local:
                    relation = {k: v for k, v in local.items() if k not in {"line", "raw"}}
                elif ".md" in target or target.startswith(("raw-research/", "/")):
                    relation = link_target(target.strip("`"), path, root)
                else:
                    relation = {"kind": "placeholder", "target": target, "link_status": "missing"}
            sources.append({"source_id": "pqs-" + fingerprint([info["entry_id"], row["line"], data])[:32],
                            "raw_fields": data, "line": row["line"], "name": data.get("来源"),
                            "supports_raw": data.get("支持的信息"), "confidence_raw": data.get("可信度"),
                            "support_relation": "card_bibliography", **relation})
    source_state = "http_recorded" if any(v["kind"] == "web" for v in sources) else "local_reference_only" if any(v["kind"] == "local" for v in sources) else "placeholder_or_missing"
    if info["kind"] == "card" and source_state != "http_recorded":
        issues.append({"code": "SOURCE_GAP", "severity": "warning", "state": source_state})
    for link in links + sources:
        if link["link_status"] in {"unresolved", "blocked", "outside_source_root"}:
            issues.append({"code": "SOURCE_LINK_UNRESOLVED", "severity": "warning", "line": link["line"], "target": link["target"], "status": link["link_status"]})
    observations = [r for t in tables if t["section_id"] == "s11" for r in t["rows"]]
    for row in observations:
        row["record_state"] = "recorded_unverified" if re.search(r"\d{4}-\d{2}-\d{2}", row["values"][0]) else "placeholder_or_undated"
    recorded = [r for r in observations if r["record_state"] == "recorded_unverified"]
    has_inference = any("推定" in r["raw"] for r in recorded)
    has_user_lead = any("用户" in r["raw"] for r in recorded)
    if recorded:
        issues.append({"code": "FIELD_NOTES_REQUIRE_REVIEW", "severity": "warning", "rows": len(recorded)})
    name = titles[0] if titles else Path(path).stem
    object_type = "measurement_point" if Path(path).stem == "园区并网点PCC" else "unknown"
    return {**info, "schema_version": SCHEMA_VERSION, "parser_version": PARSER_VERSION,
            "sha256": digest(raw), "bytes": len(raw), "canonical_name": name,
            "aliases_raw": value("常见别名"), "aliases": [v.strip() for v in re.split(r"[、；;,，]", value("常见别名") or "") if v.strip()],
            "subscene_raw": value("所属行业"), "object_type": object_type,
            "object_type_origin": "explicit_pcc_name_rule" if object_type != "unknown" else "unresolved",
            "source_confidence_raw": confidence, "source_confidence": {"高": "high", "中": "medium", "低": "low", "中/高": "medium_high"}.get(confidence, "unknown"),
            "field_verification_raw": status_raw, "field_verification_status": status,
            "field_notes": observations, "evidence_basis": ["public_material"] + (["user_lead"] if has_user_lead else []) + (["process_inference"] if has_inference else []),
            "field_notes_verified": False, "source_state": source_state, "sources": sources,
            "fields": fields, "sections": sections, "tables": tables, "links": links,
            "issues": issues, "can_confirm": not any(v["severity"] == "error" for v in issues)}
