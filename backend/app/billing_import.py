"""Explicitly mapped supplier CSV imports; never infer per-request billing."""
import csv
import io
import re
import hashlib
from decimal import Decimal, InvalidOperation
from .security import AppError

MAX_BYTES=5*1024*1024
MAX_ROWS=10000

def parse_csv(raw):
    if not raw or len(raw)>MAX_BYTES:raise AppError('BILL_SIZE','账单文件为空或超过 5 MB 限制。')
    text=None
    for encoding in ('utf-8-sig','gb18030'):
        try:text=raw.decode(encoding);break
        except UnicodeError:continue
    if text is None or '\x00' in text:raise AppError('BILL_ENCODING','账单请使用 UTF-8 或 GB18030 编码的 CSV。')
    try:
        rows=list(csv.reader(io.StringIO(text,newline=''),strict=True))
    except csv.Error:raise AppError('BILL_FORMAT','CSV 格式无效。') from None
    if not rows or len(rows)>MAX_ROWS+1:raise AppError('BILL_ROWS','账单须有表头且最多 10000 行。')
    headers=[x.strip() for x in rows[0]]
    if not 1<=len(headers)<=100 or any(not x or len(x)>100 for x in headers) or len(set(headers))!=len(headers):raise AppError('BILL_HEADERS','账单列名不能为空、重复或超过长度限制。')
    body=[row for row in rows[1:] if any(cell.strip() for cell in row)]
    if not body or any(len(row)!=len(headers) or any(len(cell)>2000 for cell in row) for row in body):raise AppError('BILL_FORMAT','账单数据为空、列数不一致或单元格过长。')
    return headers,body

def preview(raw):
    headers,rows=parse_csv(raw)
    return {'headers':headers,'rowCount':len(rows),'samples':rows[:3]}

def summarize(raw,amount_column,period_column,period):
    headers,rows=parse_csv(raw)
    if amount_column not in headers or (period_column and period_column not in headers) or not re.fullmatch(r'20\d\d-(0[1-9]|1[0-2])',period or ''):raise AppError('BILL_MAPPING','请选择金额列、有效账期及可选账期列。')
    index=headers.index(amount_column);pindex=headers.index(period_column) if period_column else None;total=Decimal(0)
    for i,row in enumerate(rows,2):
        if pindex is not None and row[pindex].strip()!=period:raise AppError('BILL_PERIOD_MISMATCH',f'第 {i} 行账期与选择的账期不一致。')
        amount=row[index].strip()
        if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d{1,12})?',amount):raise AppError('BILL_AMOUNT',f'第 {i} 行金额无效，请检查所选列。')
        try:value=Decimal(amount.replace(',',''))
        except InvalidOperation:raise AppError('BILL_AMOUNT',f'第 {i} 行金额无效。') from None
        if not value.is_finite() or abs(value)>Decimal('1000000000000'):raise AppError('BILL_AMOUNT',f'第 {i} 行金额超出范围。')
        total+=value
    return {'sha256':hashlib.sha256(raw).hexdigest(),'rowCount':len(rows),'amount':str(total),'period':period,'amountColumn':amount_column,'periodColumn':period_column or None}
