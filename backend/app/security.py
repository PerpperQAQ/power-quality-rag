import os
import re
from pathlib import Path
from urllib.parse import urlparse
from cryptography.fernet import Fernet
from .config import settings
from .db import Secret

class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)

def cipher():
    path = settings.data_dir / ".encryption-key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream: stream.write(Fernet.generate_key())
    except FileExistsError: pass
    os.chmod(path, 0o600)
    return Fernet(path.read_bytes())

def store_secret(session, key):
    if not isinstance(key, str) or len(key.strip()) < 8 or len(key) > 2048:
        raise AppError("INVALID_KEY", "请输入有效的通义 API Key。")
    session.merge(Secret(id="tongyi", ciphertext=cipher().encrypt(key.strip().encode()).decode()))

def get_secret(session):
    secret = session.get(Secret, "tongyi")
    if not secret: raise AppError("MODEL_NOT_CONFIGURED", "请管理员先保存通义 API Key 并测试模型。", 409)
    try: return cipher().decrypt(secret.ciphertext.encode()).decode()
    except Exception: raise AppError("SECRET_UNREADABLE", "密钥无法解密，请恢复本机主密钥或重新保存 API Key。", 409)

def validate_provider(data):
    endpoint = data.get("endpoint", "https://dashscope.aliyuncs.com").rstrip("/")
    from .providers import normalize_endpoint, ProviderError
    try: normalize_endpoint({"endpoint":endpoint})
    except ProviderError as error: raise AppError("INVALID_ENDPOINT",error.message)
    return {"name": str(data.get("name", "通义千问"))[:100], "region": str(data.get("region", "cn-beijing"))[:100], "endpoint":endpoint}

def safe_id(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,150}",value):
        raise AppError("INVALID_ID", "资源标识不正确。")
    return value

def require(role, minimum):
    if role not in {"admin","manager","user"}: raise AppError("INVALID_ROLE", "请选择有效角色。", 403)
    if minimum == "admin" and role != "admin" or minimum == "manager" and role == "user":
        raise AppError("FORBIDDEN", "当前演示角色没有此操作权限。角色是自选的，不能作为身份认证。", 403)
