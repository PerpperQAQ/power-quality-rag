"""Encrypt tool bearer tokens and bind them to their configured endpoint."""
import json
import re
from .db import Secret
from .security import AppError,cipher,safe_id

TOKEN=re.compile(r'^[A-Za-z0-9\-._~+/]+=*$')
def validate_token(value):
    if value is None or value=='':return None
    if not isinstance(value,str) or not 8<=len(value)<=2048 or not TOKEN.fullmatch(value):raise AppError('INVALID_TOOL_CREDENTIAL','Bearer 凭据须为 8–2048 位有效字符。')
    return value

def identifier(tool_id):
    value='x-'+safe_id(tool_id)
    if len(value)>40:raise AppError('INVALID_TOOL','工具标识过长。')
    return value

def store(session,tool_id,endpoint,token):
    token=validate_token(token)
    if token is None:raise AppError('INVALID_TOOL_CREDENTIAL','请输入有效的 Bearer 凭据。')
    data=json.dumps({'endpoint':endpoint,'token':token},ensure_ascii=False).encode()
    session.merge(Secret(id=identifier(tool_id),ciphertext=cipher().encrypt(data).decode()))

def clear(session,tool_id):
    row=session.get(Secret,identifier(tool_id))
    if row:session.delete(row)

def get(session,tool_id,endpoint):
    row=session.get(Secret,identifier(tool_id))
    if not row:raise AppError('TOOL_CREDENTIAL_MISSING','工具凭据不存在或已删除，请检查工具配置。',409)
    try:data=json.loads(cipher().decrypt(row.ciphertext.encode()))
    except Exception:raise AppError('TOOL_CREDENTIAL_UNREADABLE','工具凭据无法解密。',409) from None
    if not isinstance(data,dict) or data.get('endpoint')!=endpoint:raise AppError('TOOL_CREDENTIAL_ENDPOINT_MISMATCH','工具地址与凭据绑定地址不一致，请重新发布或更新凭据。',409)
    token=validate_token(data.get('token'))
    if not token:raise AppError('TOOL_CREDENTIAL_UNREADABLE','工具凭据无法解密。',409)
    return token
