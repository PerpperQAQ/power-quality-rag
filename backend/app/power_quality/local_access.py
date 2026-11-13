"""Opt-in loopback identity. Credentials never travel in URLs or responses."""
import fcntl
import hashlib
import ipaddress
import os
import secrets
import stat
import time
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from starlette.responses import HTMLResponse, JSONResponse
from ..config import settings
from ..db import Record
from ..state import record

COOKIE = 'pq_local_session'
ACTOR = {'id': 'pq-local-operator', 'role': 'manager'}


def credential():
    path = settings.data_dir / 'pq-operator.key'
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise RuntimeError('LOCAL_CREDENTIAL_PERMISSIONS')
        value = os.read(fd, 256).decode().strip()
        if not value:
            value = secrets.token_urlsafe(48)
            os.write(fd, value.encode()); os.fsync(fd)
        if len(value) < 40 or len(value) > 128:
            raise RuntimeError('LOCAL_CREDENTIAL_INVALID')
        return value
    finally:
        os.close(fd)


def boundary(scope, headers):
    try:
        if any(k == 'forwarded' or k.startswith('x-forwarded-') for k in headers):
            return False
        if not ipaddress.ip_address(scope['client'][0]).is_loopback:
            return False
        host = headers.get('host', '')
        parsed = urlsplit('http://' + host)
        if parsed.hostname not in {'localhost', '127.0.0.1', '::1'} or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
            return False
        if parsed.port != scope['server'][1]:
            return False
        if headers.get('origin') not in {None, f"{scope.get('scheme', 'http')}://{host}"}:
            return False
        if headers.get('sec-fetch-site') not in {None, 'same-origin', 'none'}:
            return False
        return True
    except (ValueError, KeyError, TypeError):
        return False


class LocalAccessMiddleware:
    def __init__(self, app, factory):
        self.app, self.factory = app, factory

    async def __call__(self, scope, receive, send):
        if not settings.power_quality_local_mode or scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        if scope['type'] == 'websocket':
            return await send({'type': 'websocket.close', 'code': 1008})
        pairs = [(k.decode().lower(), v.decode()) for k, v in scope['headers']]
        headers = dict(pairs)
        async def reply(code, error):
            await JSONResponse({'error': {'code': error, 'message': '本机访问验证失败。'}}, status_code=code,
                               headers={'Cache-Control': 'no-store'})(scope, receive, send)
        if any(sum(k == name for k, v in pairs) > 1 for name in ('host', 'origin', 'authorization', 'cookie')) or not boundary(scope, headers):
            return await reply(403, 'LOCAL_BOUNDARY_REQUIRED')
        path, method = scope['path'], scope['method']
        if path == '/api/health' and method == 'GET':
            return await JSONResponse({'status': 'ok', 'authentication': 'local-single-operator'})(scope, receive, send)
        if path == '/local-session' and method == 'GET':
            return await HTMLResponse('<!doctype html><meta charset="utf-8"><title>本机访问</title><form><label>本机凭证 <input type="password" id="key" autocomplete="off"></label><button>登录</button></form><p id="status"></p><script>document.querySelector("form").onsubmit=async e=>{e.preventDefault();let k=document.querySelector("#key");let r=await fetch("/api/pq-session",{method:"POST",headers:{Authorization:"Bearer "+k.value}});k.value="";document.querySelector("#status").textContent=r.ok?"已登录":"登录失败"}</script>', headers={'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer'})(scope, receive, send)
        bearer = headers.get('authorization', '')
        valid_key = bearer.startswith('Bearer ') and secrets.compare_digest(bearer[7:], credential())
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get('cookie', ''))
            token = cookies[COOKIE].value if COOKIE in cookies else ''
        except Exception:
            token = ''
        sid = hashlib.sha256(token.encode()).hexdigest()
        with self.factory() as session:
            row = record(session, 'pqLocalSessions', sid) if token else None
            valid_session = bool(row and not row.data.get('revoked') and row.data['expires_at'] > time.time())
        if path == '/api/pq-session' and method == 'POST':
            if not valid_key:
                return await reply(401, 'LOCAL_CREDENTIAL_REQUIRED')
            value = secrets.token_urlsafe(48)
            with self.factory() as session, session.begin():
                session.add(Record(collection='pqLocalSessions', id=hashlib.sha256(value.encode()).hexdigest(), session_id=ACTOR['id'], data={'expires_at': time.time()+3600, 'revoked': False}))
            response = JSONResponse({'authenticated': True}, headers={'Cache-Control': 'no-store'})
            response.set_cookie(COOKIE, value, max_age=3600, httponly=True, samesite='strict', path='/')
            return await response(scope, receive, send)
        if not valid_key and not valid_session:
            return await reply(401, 'LOCAL_CREDENTIAL_REQUIRED')
        if path == '/api/pq-session' and method == 'DELETE':
            if token:
                with self.factory() as session, session.begin():
                    row = record(session, 'pqLocalSessions', sid)
                    if row: row.data = {**row.data, 'revoked': True}
            response = JSONResponse({'authenticated': False}, headers={'Cache-Control': 'no-store'})
            response.delete_cookie(COOKIE, path='/')
            return await response(scope, receive, send)
        scope.setdefault('state', {})['identity'] = dict(ACTOR)
        async def no_cache(message):
            if message['type'] == 'http.response.start':
                message.setdefault('headers', []).extend([(b'cache-control', b'no-store'), (b'x-frame-options', b'DENY')])
            await send(message)
        await self.app(scope, receive, no_cache)
