"""Registered, read-only HTTPS tools. DNS is resolved once and the socket is pinned."""
import asyncio
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit, urlencode
from .security import AppError

def validate_endpoint(endpoint):
    if not isinstance(endpoint,str) or len(endpoint)>1000:raise AppError('INVALID_TOOL_URL','工具地址无效。')
    try:
        u=urlsplit(endpoint)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment or u.port not in (None,443):raise ValueError()
        if any(ord(c)<33 or ord(c)>126 for c in endpoint):raise ValueError()
    except ValueError:raise AppError('INVALID_TOOL_URL','仅支持无凭据、无查询参数、443 端口的 HTTPS 地址。') from None
    return endpoint

def _fetch(endpoint,query,html=False,token=None):
    u=urlsplit(validate_endpoint(endpoint))
    try: addresses=socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM)
    except OSError:raise AppError('TOOL_DNS_ERROR','工具域名解析失败。',502) from None
    ips=list(dict.fromkeys(a[4][0] for a in addresses))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):raise AppError('TOOL_ADDRESS_FORBIDDEN','工具地址必须解析到公网，不能访问本机或内部网络。',403)
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):
            self.sock=self._context.wrap_socket(socket.create_connection((ips[0],443),timeout=self.timeout),server_hostname=self.host)
    conn=PinnedHTTPS(u.hostname,443,timeout=8,context=ssl.create_default_context())
    try:
        path=(u.path or '/')+('?' + urlencode({'query':query}) if query is not None else '')
        headers={'Accept':'text/html, text/plain' if html else 'application/json, text/plain','User-Agent':'ZhixuTool/1.0'}
        if token:headers['Authorization']='Bearer '+token
        conn.request('GET',path,headers=headers)
        response=conn.getresponse()
        if not 200<=response.status<300:raise AppError('TOOL_HTTP_ERROR',f'工具返回 HTTP {response.status}。',502)
        kind=response.getheader('Content-Type','').lower()
        if not (kind.startswith('text/plain') or (html and kind.startswith('text/html')) or (not html and kind.startswith('application/json'))):raise AppError('TOOL_CONTENT_TYPE','响应类型不受支持。',502)
        limit=1000000 if html else 50000;raw=response.read(limit+1)
        if len(raw)>limit:raise AppError('TOOL_OUTPUT_LIMIT','返回内容超过大小限制。',502)
        try:return raw.decode('utf-8')
        except UnicodeDecodeError:raise AppError('TOOL_ENCODING','工具返回的内容不是 UTF-8。',502) from None
    except AppError:raise
    except (OSError,ssl.SSLError,http.client.HTTPException):raise AppError('TOOL_NETWORK_ERROR','工具连接失败或超时。',502) from None
    finally:conn.close()

async def call_remote(endpoint,query,token=None):
    if not isinstance(query,str) or not query.strip() or len(query)>2000:raise AppError('INVALID_TOOL_ARGUMENTS','工具查询须为 1 至 2000 个字符。')
    try:
        result=await asyncio.wait_for(asyncio.to_thread(_fetch,endpoint,query,False,token),timeout=12)
        return result.replace(token,'[凭据已隐藏]') if token else result
    except TimeoutError:raise AppError('TOOL_TIMEOUT','工具调用超时。',504) from None

async def fetch_webpage(endpoint):
    from .html_text import extract_html
    try:raw=await asyncio.wait_for(asyncio.to_thread(_fetch,endpoint,None,True),timeout=12)
    except TimeoutError:raise AppError('TOOL_TIMEOUT','抓取网页超时。',504) from None
    text=extract_html(raw) if '<html' in raw.lower() or '<body' in raw.lower() else raw.strip()
    if not text:raise AppError('WEBPAGE_EMPTY','网页没有可导入的静态文本。',422)
    if len(text)>200000:raise AppError('WEBPAGE_TOO_LONG','网页文本超过 20 万字，请拆分后导入。',413)
    return '来源：'+endpoint+'\n\n'+text
