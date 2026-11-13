"""Bounded local document extraction; OCR/visual interpretation is cloud-only.

No document content is invented on parser failure. Atomic ingestion in the
service keeps the previous index whenever any required extraction step fails.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import pymupdf as fitz
import openpyxl
import xlrd
from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from PIL import Image, UnidentifiedImageError

from .providers import ProviderError, TongyiProvider
from .chunking import STRATEGIES

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_ZIP_BYTES = 200 * 1024 * 1024
MAX_PDF_PAGES = 200
MAX_TABLE_CELLS = 200_000
MAX_IMAGE_PIXELS = 20_000_000


class ParsingError(Exception):
    def __init__(self, code: str, message: str, usage: list[dict] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.usage = list(usage or [])


@dataclass
class ParsedUnit:
    text: str
    location: str
    kind: str = "text"
    provenance: str = "native"
    heading_level: int | None = None
    table_id: str | None = None
    table_header: bool = False
    table_headers: list[str] = field(default_factory=list)
    table_rows: list[list[str]] = field(default_factory=list)
    section_path: list[str] = field(default_factory=list)
    section_sources: list[str] = field(default_factory=list)


@dataclass
class ParsedDocument:
    text: str
    units: list[ParsedUnit]
    provenance: str
    usage: list[dict] = field(default_factory=list)


def _validate_file(path: Path, extension: str) -> None:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE_BYTES:
        raise ParsingError("file_size", "文件为空、不可读取或超过 50 MB 限制。")
    with path.open("rb") as source:
        signature = source.read(16)
    if extension == "pdf" and not signature.startswith(b"%PDF-"):
        raise ParsingError("invalid_signature", "PDF 文件内容与扩展名不一致。")
    image_signature = {
        "png": signature.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": signature.startswith(b"\xff\xd8\xff"),
        "jpeg": signature.startswith(b"\xff\xd8\xff"),
        "webp": signature.startswith(b"RIFF") and signature[8:12] == b"WEBP",
    }
    if extension in image_signature and not image_signature[extension]:
        raise ParsingError("invalid_signature", "图片文件内容与扩展名不一致。")
    if extension in {"doc", "xls"} and not signature.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ParsingError("invalid_signature", "旧版 Office 文件内容与扩展名不一致。")
    if extension in {"docx", "xlsx"}:
        if not zipfile.is_zipfile(path):
            raise ParsingError("invalid_signature", "Office 文件不是有效的 OOXML 文档。")
        try:
            with zipfile.ZipFile(path) as archive:
                infos = archive.infolist()
                if len(infos) > 10_000 or sum(item.file_size for item in infos) > MAX_ZIP_BYTES:
                    raise ParsingError("archive_limit", "Office 文档展开后过大，已中止解析。")
                required = "word/document.xml" if extension == "docx" else "xl/workbook.xml"
                if required not in archive.namelist() or any(item.flag_bits & 1 for item in infos):
                    raise ParsingError("invalid_signature", "Office 文档结构无效或包含加密内容。")
        except zipfile.BadZipFile:
            raise ParsingError("invalid_signature", "Office 文档已损坏。") from None


def _cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _table_units(rows: list[list[Any]], location: str, header_row: int = 1, table_html: bool = False) -> list[ParsedUnit]:
    """Repeat column names in each row so split retrieval retains row meaning."""
    nonempty = [(index + 1, [_cell(value) for value in row]) for index, row in enumerate(rows)
                if any(value is not None and str(value).strip() for value in row)]
    if not nonempty:
        return []
    first_index, headers = nonempty[0]
    headers = [value or f"列 {index + 1}" for index, value in enumerate(headers)]
    units = [ParsedUnit("表头：" + " | ".join(headers), f"{location} · 行 {first_index + header_row - 1}", "table", table_id=location, table_header=True,table_headers=headers)]
    for row_index, row in nonempty[1:]:
        width = max(len(headers), len(row))
        fields = [f"{headers[index] if index < len(headers) else f'列 {index + 1}'}：{row[index] if index < len(row) else ''}"
                  for index in range(width)]
        units.append(ParsedUnit("；".join(fields), f"{location} · 行 {row_index + header_row - 1}", "table", table_id=location,
                                table_headers=headers if table_html else [],table_rows=[row] if table_html else []))
    return units


def _prepare_image(data: bytes) -> tuple[bytes, str, bool]:
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                raise ParsingError("image_limit", "图片超过 2000 万像素限制，请缩小后重新导入。")
            if getattr(image, "n_frames", 1) > 1:
                raise ParsingError("animated_image", "首版不解析多帧或动态图，请拆分为单张图片。")
            image.load()
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
            if mime and len(data) < 7 * 1024 * 1024:
                return data, mime, False
            rgb = Image.new("RGB", image.size, "white")
            if image.mode == "RGBA":
                rgb.paste(image, mask=image.getchannel("A"))
            else:
                rgb.paste(image.convert("RGB"))
            for quality in (92, 85, 75):
                buffer = io.BytesIO()
                rgb.save(buffer, format="JPEG", quality=quality, optimize=True)
                if buffer.tell() < 7 * 1024 * 1024:
                    return buffer.getvalue(), "image/jpeg", True
            raise ParsingError("image_limit", "图片压缩后仍超过云端 7 MB 限制，请缩小后重新导入。")
    except ParsingError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ParsingError("invalid_image", "图片内容无效、格式不支持或已损坏。") from None


async def _recognize(data: bytes, location: str, parsing: dict, provider: TongyiProvider | None,
                     ocr_model: str, vision_model: str, usage: list[dict]) -> list[ParsedUnit]:
    if provider is None or not ocr_model:
        raise ParsingError("ocr_required", "该文档含需要识别的图片，请先配置云端 OCR 模型与 API Key。")
    if parsing.get("understandImages", False) and not vision_model:
        raise ParsingError("vision_required", "已启用图片理解，请先配置视觉模型。")
    image, mime, normalized = await asyncio.to_thread(_prepare_image, data)
    ocr = await provider.recognize(image, mime, ocr_model, vision=False)
    usage.append(ocr.usage)
    # A transport/model wrapper is not a source-code block. Unwrap only a single
    # outer Markdown fence so OCR tables are recognized by the normal chunker.
    recognized=ocr.data
    fenced=re.fullmatch(r"\s*```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*",recognized,re.I)
    if fenced and not re.search(r"^\s*```",fenced[1],re.M):recognized=fenced[1]
    suffix = ":normalized" if normalized else ""
    units = [ParsedUnit(recognized, location, "ocr", "cloud_ocr" + suffix)]
    if parsing.get("understandImages", False):
        vision = await provider.recognize(image, mime, vision_model, vision=True)
        usage.append(vision.usage)
        units.append(ParsedUnit("图片解读（模型生成，请核对原图）：\n" + vision.data,
                                location + " · 图片解读", "vision", "cloud_vision" + suffix))
    return units


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if b"\x00" in data[:2048] and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ParsingError("invalid_text", "文本文件包含二进制内容。")
    for encoding in ("utf-8-sig", "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    raise ParsingError("text_encoding", "文本编码无法识别，请转为 UTF-8 后重新导入。")


def _text_units(path: Path, text: str | None = None) -> list[ParsedUnit]:
    text = _read_text(path) if text is None else text
    lines, units, buffer, start, headings, fenced, level = text.splitlines(), [], [], 1, {}, False, None
    def flush(end):
        nonlocal buffer,level
        if buffer:
            units.append(ParsedUnit("\n".join(buffer),f"行 {start}–{end}",provenance="markdown_source",heading_level=level,
                                    section_path=[headings[k][0] for k in sorted(headings)],section_sources=[headings[k][1] for k in sorted(headings)]))
            buffer=[];level=None
    for index, line in enumerate(lines, 1):
        if re.match(r"^\s*(`{3,}|~{3,})",line):fenced=not fenced
        title=re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$",line) if not fenced else None
        if title:
            flush(index-1)
            level=len(title[1]);headings={k:v for k,v in headings.items() if k<level};headings[level]=(title[2],f"行 {index}–{index}")
        if line.strip():
            if not buffer:
                start = index
            buffer.append(line)
        elif buffer:flush(index-1)
    flush(len(lines))
    return units


def _excel_value(value, number_format="General"):
    if isinstance(value,bool) or not isinstance(value,(int,float)):return value
    fmt=re.sub(r'"[^"]*"|\\.',"",number_format.split(";")[0])
    if re.fullmatch(r"[0#]+(?:\.[0#]+)?%",fmt):
        decimals=len(fmt.split(".",1)[1].rstrip("%")) if "." in fmt else 0
        n=(Decimal(str(value))*100).quantize(Decimal(1).scaleb(-decimals),rounding=ROUND_HALF_UP)
        return f"{n:.{decimals}f}%"
    if re.fullmatch(r"0{2,100}",fmt) and value==int(value):return f"{int(value):0{len(fmt)}d}"
    return value


def _spreadsheet_units(path: Path, extension: str, table_html: bool = False) -> list[ParsedUnit]:
    units, cell_count = [], 0
    if extension == "csv":
        source = _read_text(path)
        try:
            dialect = csv.Sniffer().sniff(source[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = []
        for row in csv.reader(io.StringIO(source), dialect):
            cell_count += len(row)
            if cell_count > MAX_TABLE_CELLS:
                raise ParsingError("table_limit", "表格超过 20 万单元格限制，请拆分后重新导入。")
            rows.append(row)
        return _table_units(rows, "CSV", table_html=table_html)
    if extension == "xlsx":
        # data_only=False preserves formulas rather than silently losing uncached values.
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False, keep_links=False)
        try:
            for sheet in workbook.worksheets:
                if (sheet.max_row or 0) * (sheet.max_column or 0) + cell_count > MAX_TABLE_CELLS:
                    raise ParsingError("table_limit", "工作簿超过 20 万单元格限制，请拆分后重新导入。")
                rows = []
                for row in sheet.iter_rows():
                    cell_count += len(row)
                    if cell_count > MAX_TABLE_CELLS:
                        raise ParsingError("table_limit", "工作簿超过 20 万单元格限制，请拆分后重新导入。")
                    rows.append([_excel_value(cell.value,cell.number_format) for cell in row])
                units.extend(_table_units(rows, f"工作表 {sheet.title}", table_html=table_html))
        finally:
            workbook.close()
    else:
        workbook = xlrd.open_workbook(path, on_demand=True,formatting_info=True)
        try:
            for sheet in workbook.sheets():
                cell_count += sheet.nrows * sheet.ncols
                if cell_count > MAX_TABLE_CELLS:
                    raise ParsingError("table_limit", "工作簿超过 20 万单元格限制，请拆分后重新导入。")
                rows = []
                for row_index in range(sheet.nrows):
                    values = []
                    for column_index,cell in enumerate(sheet.row(row_index)):
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            values.append(xlrd.xldate_as_datetime(cell.value, workbook.datemode).isoformat(sep=" "))
                        elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                            values.append(bool(cell.value))
                        elif cell.ctype == xlrd.XL_CELL_ERROR:
                            values.append(xlrd.error_text_from_code.get(cell.value, "[单元格错误]"))
                        else:
                            xf=workbook.xf_list[sheet.cell_xf_index(row_index,column_index)]
                            fmt=workbook.format_map[xf.format_key].format_str
                            values.append(_excel_value(cell.value,fmt))
                    rows.append(values)
                units.extend(_table_units(rows, f"工作表 {sheet.name}", table_html=table_html))
        finally:
            workbook.release_resources()
    return units


def _docx_parts(path: Path) -> list[tuple[str, Any, str]]:
    document = Document(path)
    if document.element.xpath(".//*[local-name()='relIds' or local-name()='chart' or local-name()='txbxContent']"):
        raise ParsingError("office_graphic_requires_pdf", "Word 含 SmartArt、原生图表或文本框图形；请先导出 PDF 后导入，以完整保留文字和图形关系。")
    parts: list[tuple[str, Any, str]] = []
    paragraph_index, table_index, image_index = 0, 0, 0
    seen_images: set[str] = set()
    for element in document.element.body.iterchildren():
        if isinstance(element, CT_P):
            paragraph_index += 1
            paragraph = Paragraph(element, document)
            if paragraph.text.strip():
                style, level = paragraph.style, None
                for _ in range(10):
                    if style is None: break
                    match = re.fullmatch(r"(?:Heading|标题)\s*([1-6])", style.name or "", re.I)
                    if match:
                        level = int(match[1]); break
                    style = style.base_style
                parts.append((f"heading{level}" if level else "text", paragraph.text, f"段落 {paragraph_index}"))
            for blip in element.xpath(".//a:blip"):
                relationship = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                if relationship and relationship not in seen_images:
                    seen_images.add(relationship)
                    image_index += 1
                    parts.append(("image", document.part.related_parts[relationship].blob,
                                  f"段落 {paragraph_index} · 图片 {image_index}"))
        elif isinstance(element, CT_Tbl):
            table_index += 1
            table = Table(element, document)
            rows = [[""] * row.grid_cols_before + [cell.text for cell in row.cells] + [""] * row.grid_cols_after for row in table.rows]
            if sum(len(row) for row in rows) > MAX_TABLE_CELLS:
                raise ParsingError("table_limit", "Word 表格超过 20 万单元格限制。")
            parts.append(("table", rows, f"表格 {table_index}"))
            for nested_index, nested in enumerate(element.xpath(".//w:tbl"), 1):
                nested_table = Table(nested, document)
                parts.append(("table", [[cell.text for cell in row.cells] for row in nested_table.rows],
                              f"表格 {table_index} · 嵌套表格 {nested_index}"))
            # Images inside table cells must not disappear from the knowledge index.
            for blip in element.xpath(".//a:blip"):
                relationship = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                if relationship and relationship not in seen_images:
                    seen_images.add(relationship)
                    image_index += 1
                    parts.append(("image", document.part.related_parts[relationship].blob,
                                  f"表格 {table_index} · 图片 {image_index}"))
    # Repeating section headers/footers are real source content. Store each
    # linked part once, and keep the source label independent from body paragraphs.
    seen_parts: set[str] = set()
    for section_index, section in enumerate(document.sections, 1):
        for label, container in (("页眉", section.header), ("首页页眉", section.first_page_header),
                                 ("偶数页页眉", section.even_page_header), ("页脚", section.footer),
                                 ("首页页脚", section.first_page_footer), ("偶数页页脚", section.even_page_footer)):
            part_key = str(container.part.partname)
            if part_key in seen_parts:
                continue
            seen_parts.add(part_key)
            for paragraph_index, paragraph in enumerate(container.paragraphs, 1):
                if paragraph.text.strip():
                    parts.append(("text", paragraph.text, f"第 {section_index} 节{label} · 段落 {paragraph_index}"))
            for table_index, table in enumerate(container.tables, 1):
                parts.append(("table", [[cell.text for cell in row.cells] for row in table.rows],
                              f"第 {section_index} 节{label} · 表格 {table_index}"))
            for image_index, blip in enumerate(container._element.xpath(".//a:blip"), 1):
                relationship = blip.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed")
                if relationship:
                    parts.append(("image", container.part.related_parts[relationship].blob,
                                  f"第 {section_index} 节{label} · 图片 {image_index}"))
    return parts


def _convert_doc(path: Path, destination: Path) -> Path:
    libreoffice = shutil.which("libreoffice") or shutil.which("soffice")
    textutil = shutil.which("textutil")
    output = destination / "converted.docx"
    if libreoffice:
        command = [libreoffice, f"-env:UserInstallation={((destination / 'profile').as_uri())}", "--headless",
                   "--convert-to", "docx", "--outdir", str(destination), str(path)]
        expected = destination / (path.stem + ".docx")
    elif textutil:
        command = [textutil, "-convert", "docx", "-output", str(output), str(path)]
        expected = output
    else:
        raise ParsingError("doc_converter_missing", "旧版 .doc 需要系统的 LibreOffice 或 macOS textutil；当前环境不可用，请先转为 .docx。")
    try:
        result = subprocess.run(command, capture_output=True, timeout=60, check=False)
        if result.returncode or not expected.is_file():
            raise ParsingError("doc_conversion_failed", "旧版 Word 转换失败，文件可能损坏、受保护或包含不支持的内容。")
        _validate_file(expected, "docx")
        return expected
    except subprocess.TimeoutExpired:
        raise ParsingError("doc_conversion_timeout", "旧版 Word 转换超过 60 秒，请转为 .docx 后重新导入。") from None


async def _parse_word(path: Path, parsing: dict, provider, ocr_model, vision_model, usage) -> list[ParsedUnit]:
    parts = await asyncio.to_thread(_docx_parts, path)
    units, headings = [], {}
    for part_index, (kind, content, location) in enumerate(parts):
        start = len(units)
        if kind == "text" or kind.startswith("heading"):
            if kind.startswith("heading"):
                level = int(kind[-1])
                headings = {k:v for k,v in headings.items() if k < level}
                headings[level] = (content, location)
            units.append(ParsedUnit(content, location, heading_level=int(kind[-1]) if kind.startswith("heading") else None))
        elif kind == "table":
            units.extend(_table_units(content, location, table_html=parsing.get("tableHtml", False)))
        else:
            image_units = await _recognize(content, location, parsing, provider, ocr_model, vision_model, usage)
            count = parsing.get("imageContext", 0)
            before = [(text, label) for kind, text, label in parts[:part_index] if kind == "text" or kind.startswith("heading")][-count:] if count else []
            after = [(text, label) for kind, text, label in parts[part_index + 1:] if kind == "text" or kind.startswith("heading")][:count] if count else []
            _append_context(image_units, before + after)
            units.extend(image_units)
        for unit in units[start:]:
            unit.section_path = [headings[k][0] for k in sorted(headings)]
            unit.section_sources = [headings[k][1] for k in sorted(headings)]
    return units


def _append_context(units: list[ParsedUnit], context: list[tuple[str, str]]) -> None:
    if context:
        source = "\n".join(f"[{location}] {text}" for text, location in context)
        for unit in units:
            unit.text += "\n\n原文上下文（来自相邻文字，非图片识别或模型生成）：\n" + source


def _pdf_image_context(page, xref: int, count: int, location: str) -> list[tuple[str, str]]:
    if not count:
        return []
    rectangles = page.get_image_rects(xref)
    if not rectangles:
        return []
    image_center = rectangles[0].tl + (rectangles[0].br - rectangles[0].tl) * .5
    blocks = [block for block in page.get_text("blocks") if len(block) > 6 and block[6] == 0 and block[4].strip()]
    def distance(block):
        return ((block[0] + block[2]) * .5 - image_center.x) ** 2 + ((block[1] + block[3]) * .5 - image_center.y) ** 2
    selected = sorted(sorted(blocks, key=distance)[:count], key=lambda block: (block[1], block[0]))
    return [(block[4].strip(), f"{location} · 文字块 {blocks.index(block) + 1}") for block in selected]


def _native_pdf_units(page, location, table_html):
    """Read native text and tables once, in page order, without duplicate cells."""
    tables=list(page.find_tables().tables) if hasattr(page,"find_tables") else []
    bounds=[fitz.Rect(t.bbox) for t in tables]
    ordered=[]
    for index,block in enumerate(page.get_text("rawdict",sort=True)["blocks"],1):
        if block.get("type")!=0:continue
        lines=[]
        for line in block.get("lines",[]):
            chars=[]
            for span in line.get("spans",[]):
                for char in span.get("chars",[]):
                    r=fitz.Rect(char["bbox"]);center=(r.tl+r.br)*.5
                    if not any(center in rect for rect in bounds):chars.append(char["c"])
            if "".join(chars).strip():lines.append("".join(chars))
        if lines:ordered.append((block["bbox"][1],block["bbox"][0],[ParsedUnit("\n".join(lines),f"{location} · 文字块 {index}",provenance="native_pdf")]))
    for index,table in enumerate(tables,1):
        # Table.extract() can reorder Type3 punctuation into a separate line
        # (e.g. 0.6% -> 06%\n.). Read each cell in native text order instead.
        rows=[[page.get_textbox(fitz.Rect(cell)).strip() if cell else "" for cell in row.cells] for row in table.rows]
        if sum(len(r) for r in rows)>MAX_TABLE_CELLS:raise ParsingError("table_limit",f"{location} 的表格超过单元格限制。")
        ordered.append((table.bbox[1],table.bbox[0],_table_units(rows,f"{location} · 表格 {index}",table_html=table_html)))
    ordered.sort(key=lambda item:(round(item[0],1),item[1]))
    return [unit for _,_,group in ordered for unit in group],bounds


def _has_meaningful_vectors(page, table_bounds):
    for path in page.get_drawings():
        rect=fitz.Rect(path["rect"])
        if any((rect.tl+rect.br)*.5 in table for table in table_bounds):continue
        if path.get("type")=="f" and min(rect.width,rect.height)<36 and max(rect.width,rect.height)>200 and max(rect.width,rect.height)/max(1,min(rect.width,rect.height))>10:continue
        # Native PDFs contain borders, bullets, glyph outlines and small logos.
        # A stroked diagram or substantial filled chart region merits vision.
        if path.get("type") in {"s","fs"} and min(rect.width,rect.height)>=8:return True
        if path.get("type")=="f" and min(rect.width,rect.height)>=24 and 600<=rect.get_area()<page.rect.get_area()*.8:return True
    return False


async def _parse_pdf(path: Path, parsing: dict, provider, ocr_model, vision_model, usage) -> list[ParsedUnit]:
    mode = parsing.get("pdfParser", "auto")
    if mode not in {"auto", "text", "native", "cloud"}:
        raise ParsingError("invalid_parser", "PDF 解析方式无效。")
    units = []
    with fitz.open(path) as document:
        if document.needs_pass:
            raise ParsingError("encrypted_pdf", "PDF 已加密，请解除密码保护后重新导入。")
        if document.page_count > MAX_PDF_PAGES:
            raise ParsingError("pdf_limit", "PDF 超过 200 页限制，请拆分后重新导入。")
        for page_index in range(document.page_count):
            await asyncio.sleep(0)
            page = document[page_index]
            location = f"第 {page_index + 1} 页"
            native = page.get_text("text").strip()
            if mode == "cloud" or not native and mode == "auto":
                if page.rect.width * page.rect.height * 2.25 > MAX_IMAGE_PIXELS:
                    raise ParsingError("image_limit", f"{location} 渲染后超过 2000 万像素，请缩小页面尺寸后重新导入。")
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                units.extend(await _recognize(pixmap.tobytes("png"), location, parsing, provider, ocr_model, vision_model, usage))
                continue
            if not native and mode in {"text", "native"}:
                raise ParsingError("scanned_pdf", f"{location} 没有可提取的文字；请将 PDF 解析方式改为自动或云端 OCR。")
            table_bounds=[]
            if native:
                native_units,table_bounds=_native_pdf_units(page,location,parsing.get("tableHtml",False))
                units.extend(native_units)
            # Native-only is an explicit operator choice. Auto extracts embedded
            # raster images even on a page that also contains native text.
            if mode == "auto":
                seen: set[int] = set()
                for image_index, image in enumerate(page.get_images(full=True), 1):
                    xref = image[0]
                    if xref in seen:
                        continue
                    seen.add(xref)
                    extracted = document.extract_image(xref)
                    if not extracted or not extracted.get("image"):
                        raise ParsingError("pdf_image_failed", f"{location} 的图片无法提取，请改用云端 OCR 解析该文档。")
                    image_units = await _recognize(extracted["image"], f"{location} · 图片 {image_index}",
                                                   parsing, provider, ocr_model, vision_model, usage)
                    _append_context(image_units, _pdf_image_context(page, xref, parsing.get("imageContext", 0), location))
                    units.extend(image_units)
                # Flow charts can be vector paths rather than embedded raster
                # images. Interpreting the page retains arrows and connections
                # that native text extraction cannot represent.
                if parsing.get("understandImages", False) and _has_meaningful_vectors(page,table_bounds):
                    if provider is None or not vision_model:
                        raise ParsingError("vision_required", f"{location} 含矢量图形，已启用图片理解，请配置视觉模型。")
                    if page.rect.width * page.rect.height * 2.25 > MAX_IMAGE_PIXELS:
                        raise ParsingError("image_limit", f"{location} 渲染后超过 2000 万像素，请缩小页面尺寸后重新导入。")
                    pixmap = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    image, mime, normalized = await asyncio.to_thread(_prepare_image, pixmap.tobytes("png"))
                    interpretation = await provider.recognize(image, mime, vision_model, vision=True)
                    usage.append(interpretation.usage)
                    units.append(ParsedUnit("页面图形解读（模型生成，请核对原页）：\n" + interpretation.data,
                                            location + " · 矢量图形解读", "vision", "cloud_vision"))
    return units


async def parse_document(path: Path, filename: str, parsing: dict,
                         provider: TongyiProvider | None = None, ocr_model: str = "",
                         vision_model: str = "") -> ParsedDocument:
    path = Path(path)
    extension = Path(filename).suffix.lower().lstrip(".")
    supported = {"md", "txt", "html", "htm", "doc", "docx", "pdf", "png", "jpg", "jpeg", "webp", "xls", "xlsx", "csv"}
    if extension not in supported:
        raise ParsingError("unsupported_format", "不支持该文件类型，请使用 Word、PDF、Markdown、TXT、HTML、图片或 Excel/CSV。")
    usage: list[dict] = []
    try:
        if parsing.get("strategy", "general") not in STRATEGIES:
            raise ParsingError("invalid_parser", "分块策略无效。")
        if not isinstance(parsing.get("imageContext", 0), int) or not 0 <= parsing.get("imageContext", 0) <= 8:
            raise ParsingError("invalid_parser", "图片上下文段落数必须为 0 到 8。")
        await asyncio.to_thread(_validate_file, path, extension)
        if extension in {"md", "txt"}:
            units = await asyncio.to_thread(_text_units, path)
        elif extension in {"html", "htm"}:
            from .html_text import extract_html
            text = await asyncio.to_thread(_read_text, path)
            text = await asyncio.to_thread(extract_html, text)
            if len(text)>200000:raise ParsingError("html_too_long", "网页正文超过 20 万字，请拆分后导入。")
            units = _text_units(path, text)
            for unit in units:unit.provenance="html_source"
        elif extension in {"xls", "xlsx", "csv"}:
            units = await asyncio.to_thread(_spreadsheet_units, path, extension, parsing.get("tableHtml", False))
        elif extension == "docx":
            units = await _parse_word(path, parsing, provider, ocr_model, vision_model, usage)
        elif extension == "doc":
            with tempfile.TemporaryDirectory(prefix="langchina-doc-") as directory:
                converted = await asyncio.to_thread(_convert_doc, path, Path(directory))
                units = await _parse_word(converted, parsing, provider, ocr_model, vision_model, usage)
                for unit in units:
                    unit.provenance = "converted_doc:" + unit.provenance
        elif extension == "pdf":
            units = await _parse_pdf(path, parsing, provider, ocr_model, vision_model, usage)
        else:
            units = await _recognize(await asyncio.to_thread(path.read_bytes), "图片", parsing,
                                     provider, ocr_model, vision_model, usage)
        units = [unit for unit in units if unit.text and unit.text.strip()]
        if not units:
            raise ParsingError("empty_document", "未提取到可索引内容，请检查原文件或解析方式。")
        provenance = ",".join(sorted({unit.provenance for unit in units}))
        if extension == "pdf" and parsing.get("pdfParser") in {"text", "native"}:
            provenance += ";native_only_images_not_interpreted"
        if extension in {"xlsx", "xls", "csv"}:
            provenance += ";table_cells_only_no_formula_calculation"
        return ParsedDocument("\n\n".join(unit.text for unit in units), units, provenance, usage)
    except asyncio.CancelledError as error:
        error.usage = usage + getattr(error, "usage", [])
        raise
    except (ParsingError, ProviderError) as error:
        error.usage = usage + error.usage
        raise
    except Exception as error:
        if getattr(error, "abort_parsing", False):
            raise
        # Original parser errors may contain full XML, cell text or local paths.
        raise ParsingError("parse_failed", "文件解析失败，可能已损坏、受保护或包含不支持的内容；请检查原文件。", usage) from None


async def parse_file(path: Path, format: str, parsing: dict, provider=None, models: dict | None = None) -> list[dict]:
    """Compatibility helper for callers that consume serializable source units."""
    models = models or {}
    parsed = await parse_document(path, f"document.{format.lstrip('.')}", parsing, provider,
                                  models.get("ocr", ""), models.get("vision", ""))
    return [{"content": unit.text, "location": unit.location, "kind": unit.kind,
             "provenance": unit.provenance} for unit in parsed.units]
