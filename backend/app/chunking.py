"""Source-aware structural chunking, followed by real LangChain splitters.

No LLM calls, inferred headings, or silent document truncation. All entrypoints
(preview and ingestion) share this implementation and embedding safety budget.
"""
import copy
import json
import re
import uuid
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from .model_catalog import embedding_input_budget
from .security import AppError

METHODS = json.loads(Path(__file__).with_name("chunking_methods.json").read_text())
METHOD_LABELS = {m["value"]: m["label"] for m in METHODS}
STRATEGIES = set(METHOD_LABELS)
_NUM = r"[零〇一二三四五六七八九十百千万两\d]+"
_ARTICLE = re.compile(rf"^\s*(第{_NUM}条(?:之{_NUM})?|Article\s+\d+[A-Za-z]?)", re.I)
_QUESTION = re.compile(r"^\s*(?:(?:Q|Question|问(?:题)?)\s*[:：]|\d+[.、)]\s*(?:问(?:题)?\s*[:：]))", re.I)
_MD_TITLE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")


def _unique(values):
    return list(dict.fromkeys(values))


def _table_cells(line):
    return [s.strip().replace(r"\|", "|") for s in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _expand_tables(units):
    """Recognize explicit Markdown table syntax in text/OCR, preserving prose."""
    result, fenced = [], False
    for index, unit in enumerate(units):
        if unit.get("kind") == "table":
            result.append(unit)
            continue
        lines = unit["text"].splitlines()
        i, prose = 0, []
        def flush():
            if prose:
                result.append({**unit, "text": "\n".join(prose)})
                prose.clear()
        while i < len(lines):
            if re.match(r"^\s*(`{3,}|~{3,})", lines[i]): fenced = not fenced
            cells = _table_cells(lines[i + 1]) if i + 1 < len(lines) else []
            if not fenced and "|" in lines[i] and cells and all(re.fullmatch(r":?-{3,}:?", x) for x in cells):
                flush()
                headers = _table_cells(lines[i])
                table_id = f"markdown-{index}-{i}"
                i += 2
                row_count = 0
                while i < len(lines) and "|" in lines[i] and lines[i].strip():
                    values = _table_cells(lines[i])
                    text = "；".join(f"{headers[c] if c < len(headers) and headers[c] else f'列 {c+1}'}：{values[c] if c < len(values) else ''}" for c in range(max(len(headers), len(values))))
                    result.append({**unit, "text": text, "kind": "table", "table_id": table_id,
                                   "table_header": False,"table_headers":headers,"table_rows":[values], "location": f"{unit['location']} · 表格数据行 {row_count+1}"})
                    row_count += 1
                    i += 1
                if not row_count:
                    result.append({**unit, "text": "表头：" + " | ".join(headers), "kind": "table", "table_id": table_id, "table_header": True})
                continue
            prose.append(lines[i])
            i += 1
        flush()
    return result


def _block(parts, path=None, context="", note="", atomic=False):
    source_path=parts[0].get("section_path",[]) if parts else []
    return {"parts": parts, "path": path or source_path, "context": context, "note": note, "atomic": atomic or bool(parts and all(p.get("kind")=="table" for p in parts))}


def _prepare_table_rows(units, method, size, budget):
    """Group a real table without separating it from its owner or breaking cells."""
    counts={}
    for u in units:
        if u.get("kind")=="table" and not u.get("table_header"):
            counts[u.get("table_id")]=counts.get(u.get("table_id"),0)+1
    result=[]
    for u in units:
        if u.get("kind")!="table":result.append(u);continue
        if u.get("table_header") and counts.get(u.get("table_id")):continue
        prior=result[-1] if result else None
        prefix=("章节："+" / ".join(u.get("section_path",[])))[:min(300,size//3)]
        candidate=(prior["text"]+"\n"+u["text"]) if prior else ""
        if method!="table" and prior and prior.get("kind")=="table" and prior.get("table_id")==u.get("table_id") and prior.get("section_path")==u.get("section_path") and len(prefix)+1+len(candidate)<=size and len((prefix+"\n"+candidate).encode("utf-8"))<=budget:
            prior["text"]=candidate
            prior["table_rows"]=[*prior.get("table_rows",[]),*u.get("table_rows",[])]
            prior["_sourceLocations"]=[*prior.get("_sourceLocations",[prior["location"]]),u["location"]]
        else:result.append(copy.deepcopy(u))
    return result


def _general(units):
    blocks, pending = [], []
    for unit in units:
        if unit.get("kind", "text") in {"text", "ocr"}:
            if pending and pending[-1].get("section_path")!=unit.get("section_path"):
                blocks.append(_block(pending));pending=[]
            pending.append(unit)
        else:
            if pending: blocks.append(_block(pending)); pending = []
            blocks.append(_block([unit]))
    if pending: blocks.append(_block(pending))
    return blocks


def _heading(line, strategy, word_level=None):
    md = _MD_TITLE.match(line)
    if strategy in {"heading", "book", "paper"} and word_level:
        return int(word_level), line.strip()
    if md and strategy in {"heading", "book", "paper"}:
        return len(md[1]), md[2].strip()
    if len(line.strip()) > 120:
        return None
    chapter = re.match(rf"^\s*第{_NUM}([章节篇卷部])[^。；]*$|^\s*(Chapter|Part|Book)\s+[\dIVXLC]+\b.*$", line, re.I)
    if chapter and strategy in {"book", "heading", "laws"}:
        return (2 if chapter[1] == "节" else 1), line.strip()
    if strategy in {"heading", "paper"}:
        number = re.match(r"^\s*(\d+(?:\.\d+){0,5})(?:[.、]\s*|\s+)(?=[^\d\s])\S.*$", line)
        if number: return number[1].count(".") + 1, line.strip()
    if strategy == "paper" and re.fullmatch(r"\s*(?:摘要|关键词|引言|绪论|背景|相关工作|研究方法|方法|实验|结果|讨论|结论|参考文献|致谢|Abstract|Keywords?|Introduction|Background|Related Work|Methods?|Materials and Methods|Experiments?|Results?|Discussion|Conclusions?|References|Acknowledg(?:e)?ments?)\s*[:：]?\s*", line, re.I):
        return 1, line.strip()
    return None


def _structured(units, strategy):
    blocks, pending, levels, context, found, fenced, source_path = [], [], {}, "", 0, False, []
    def flush():
        nonlocal pending
        if pending:
            path = [levels[k] for k in sorted(levels)]
            blocks.append(_block(pending, path, context))
            pending = []
    for unit in units:
        if pending and pending[-1].get("section_path")!=unit.get("section_path"):
            flush()
        if unit.get("section_path",[])!=source_path:
            source_path=unit.get("section_path",[])
            levels={i+1:value for i,value in enumerate(source_path)}
            context="章节："+" / ".join(source_path) if source_path else ""
        if unit.get("kind", "text") in {"table", "vision"}:
            flush()
            blocks.append(_block([unit], [levels[k] for k in sorted(levels)], context))
            continue
        for index, line in enumerate(unit["text"].splitlines()):
            part = {**unit, "text": line}
            if re.match(r"^\s*(`{3,}|~{3,})", line): fenced = not fenced
            title = None if fenced else _heading(line, strategy, unit.get("heading_level") if index == 0 else None)
            article = _ARTICLE.match(line) if strategy == "laws" and not fenced else None
            question = strategy == "qa" and not fenced and (_QUESTION.match(line) or 1 < len(line.strip()) <= 240 and line.rstrip().endswith(("?", "？")))
            if title:
                flush()
                level, text = title
                levels = {k: v for k, v in levels.items() if k < level}
                levels[level] = text
                context = "章节：" + " / ".join(levels[k] for k in sorted(levels))
                found += 1
            elif article or question:
                flush()
                context = ("条款：" + article[1]) if article else "问题：" + line.strip()
                found += 1
            pending.append(part)
    flush()
    if not found:
        # A true fallback must actually regroup the prose, not leave each line
        # or heading as a misleading isolated pseudo question.
        blocks=_general(units)
        for block in blocks: block["note"] = "未识别到该方法的结构标记，已使用通用递归切分；请检查原文标题或问答格式。"
    blocks=[b for i,b in enumerate(blocks) if not (i+1<len(blocks) and b["path"] and all(p.get("heading_level") for p in b["parts"]) and "\n".join(p["text"] for p in b["parts"]).strip()==b["path"][-1] and blocks[i+1]["path"]==b["path"])]
    return blocks


def _join(parts):
    text, spans = "", []
    for part in parts:
        if not part["text"].strip(): continue
        if text: text += "\n"
        start = len(text)
        text += part["text"]
        spans.append((start, len(text), part))
    return text, spans


def split_units(units, doc, kb):
    parsing = {**(kb.get("parsing") or {}), **(doc.get("parsing") or {})}
    method = parsing.get("strategy", "general")
    if method not in STRATEGIES: raise AppError("INVALID_CHUNK_METHOD", "请选择已实现的切分方法。")
    if method=="semantic":raise AppError("SEMANTIC_BUILD_REQUIRED","语义切分需要向量模型调用，请在正式构建中执行；本地预览可先选择通用递归。",409)
    if method=="auto":
        from .import_preview import recommended
        method=recommended(doc.get("name","file.txt"))
    if method=="parent_child":
        parent_doc={**doc,"parsing":{**parsing,"strategy":"heading"},"chunkSizeOverride":min(8000,(doc.get("chunkSizeOverride") or kb.get("chunkSize",600))*3),"overlapOverride":0}
        parents=split_units(units,parent_doc,kb);children=[]
        for parent in parents:
            child_doc={**doc,"parsing":{**parsing,"strategy":"general"}}
            child_units=[{"text":parent["content"],"location":parent["location"],"section_path":parent.get("sectionPath",[]),"provenance":parent.get("provenance","native")} ]
            for child in split_units(child_units,child_doc,kb):
                child.update(parentId=parent["id"],parentContent=parent["content"],parentLocation=parent["location"],chunkMethod="parent_child");children.append(child)
        return children
    size = doc.get("chunkSizeOverride") or kb.get("chunkSize", 600)
    overlap = doc.get("overlapOverride")
    if overlap is None: overlap = kb.get("overlap", 60)
    if isinstance(size, bool) or not isinstance(size, int) or not 100 <= size <= 8000 or isinstance(overlap, bool) or not isinstance(overlap, int) or not 0 <= overlap < size:
        raise AppError("INVALID_CHUNK_SIZE", "分块长度须为 100–8000 字符，重叠长度须小于分块长度。")
    delimiter = parsing.get("delimiter", r"\n").replace(r"\n", "\n").replace(r"\t", "\t")
    if method == "delimiter" and not delimiter: raise AppError("EMPTY_DELIMITER", "自定义分隔切分需要填写非空分隔符。")
    budget = embedding_input_budget(kb.get("embedding", "text-embedding-v4"))
    units = [copy.deepcopy(u) for u in units if isinstance(u.get("text"), str) and u["text"].strip()]
    if not units: raise AppError("EMPTY_DOCUMENT", "没有提取到可切分内容。")
    # Whole-document retains original serialization; other methods expand and
    # protect recognized table rows before organizing prose boundaries.
    if method!="one": units = _expand_tables(units)
    if method!="one":units=_prepare_table_rows(units,method,size,budget)
    if method in {"heading", "book", "paper", "laws", "qa"}:
        blocks = _structured(units, method)
    elif method=="one":blocks=[_block(units,atomic=True)]
    elif method in {"fixed","delimiter"}:blocks=_general(units)
    elif method == "page":
        pages = {}
        for unit in units:
            page = re.match(r"^第\s*(\d+)\s*页", unit["location"])
            if not page: raise AppError("PAGE_BOUNDARIES_REQUIRED", "逐页切分需要真实 PDF 页码；请导入 PDF，或在文本预览中用 ===PAGE=== 分页。")
            pages.setdefault(page[1], []).append(unit)
        blocks = [block for parts in pages.values() for block in _general(parts)]
    elif method == "table":
        if not any(u.get("kind") == "table" for u in units):
            raise AppError("NO_STRUCTURED_TABLE", "没有识别到带表头的结构化表格；请选择通用递归，或检查 Excel/CSV、原生表格及 Markdown 表格格式。")
        table_counts = {}
        for u in units:
            if u.get("kind") == "table": table_counts[u.get("table_id", u["location"])] = table_counts.get(u.get("table_id", u["location"]), 0) + 1
        blocks = []
        for u in units:
            if u.get("table_header") and table_counts.get(u.get("table_id"), 0) > 1: continue
            blocks.append(_block([u], atomic=u.get("kind") == "table"))
    else:
        blocks = _general(units)
    separators = _unique([x for x in ["\n\n", delimiter, "\n", "。", "！", "？", "；", ". ", " ", ""] if x] + [""])
    chunks = []
    for block in blocks:
        source, spans = _join(block["parts"])
        if not source.strip(): continue
        context = block["context"][:min(300, size // 3)]
        if block["path"] and not context: context = ("章节：" + " / ".join(block["path"]))[:min(300, size // 3)]
        prefix = context + "\n" if context else ""
        note = block["note"]
        if block["atomic"]:
            if len(source) > size or len(source.encode("utf-8")) > budget:
                label = "整篇文档" if method == "one" else "表格单行"
                raise AppError("ATOMIC_CHUNK_TOO_LARGE", f"{label}超过 {size} 字符或向量输入预算。请提高长度上限或拆分原资料；当前内容没有被截断。")
            pieces = [(source, 0)]
        else:
            body_size = max(1, size - len(prefix))
            splitter = RecursiveCharacterTextSplitter(chunk_size=body_size, chunk_overlap=min(overlap, body_size-1),
                         separators=[""] if method == "fixed" else separators, is_separator_regex=False, add_start_index=True)
            ranges = [(0, len(source))]
            if method == "delimiter":
                ends = [m.end() for m in re.finditer(re.escape(delimiter), source)]
                boundaries = _unique([0, *ends, len(source)])
                ranges = list(zip(boundaries, boundaries[1:]))
            pieces = []
            for start, end in ranges:
                docs = splitter.split_documents([Document(page_content=source[start:end])])
                pieces.extend((d.page_content, start + max(0, d.metadata.get("start_index", 0))) for d in docs)
        for piece, start in pieces:
            touched = [p for a, b, p in spans if b > start and a < start + len(piece)] or block["parts"]
            content = prefix + piece if prefix and not piece.startswith(context) else piece
            # Encoding safety can require smaller pieces for legacy embeddings.
            final = [content]
            byte_note = ""
            if len(content.encode("utf-8")) > budget:
                budget_body = budget - len(prefix.encode("utf-8"))
                byte_splitter = RecursiveCharacterTextSplitter(chunk_size=budget_body, chunk_overlap=min(overlap, budget_body-1), separators=separators, length_function=lambda s: len(s.encode("utf-8")), is_separator_regex=False)
                final = [prefix + x for x in byte_splitter.split_text(piece)]
                byte_note = "为满足当前向量模型输入预算，该片段进一步细分。"
            for content in final:
                if not content.strip(): continue
                if len(chunks) >= 10000: raise AppError("TOO_MANY_CHUNKS", "单份文件超过 10000 个分块，请拆分文件或提高块长度。")
                locations = _unique([v for p in touched for v in [*p.get("section_sources",[]),*p.get("_sourceLocations",[p["location"]])]])
                table_parts=[p for p in touched if p.get("table_headers") and p.get("table_rows")]
                structured_table={"headers":table_parts[0]["table_headers"],"rows":[row for p in table_parts for row in p["table_rows"]]} if table_parts and block["atomic"] and all(p.get("kind")=="table" and not p.get("table_header") for p in touched) else None
                chunks.append({"id": f"chunk-{uuid.uuid4().hex}", "content": content, "location": " / ".join(locations[:6]) + (f" / 共 {len(locations)} 个来源位置" if len(locations)>6 else ""),
                    "sourceLocations": locations, "sectionPath": block["path"], "chunkMethod": method,
                    "splitNote": "；".join(x for x in [note, byte_note] if x), "enabled": True, "keywords": [], "questions": [], "manual": False,
                    "provenance": ",".join(_unique([p.get("provenance", "native") for p in touched])),**({"table":structured_table} if structured_table else {})})
    if not chunks: raise AppError("EMPTY_DOCUMENT", "切分后没有可检索内容。")
    return chunks
