"""Limited MCP 2025-06-18 Streamable HTTP client for explicitly approved read-only tools."""
import asyncio
import http.client
import ipaddress
import json
import math
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit
from .remote_tools import validate_endpoint
from .security import AppError

VERSION='2025-06-18'
LIMIT=100_000

def validate_mcp_endpoint(endpoint):
    if not isinstance(endpoint,str) or len(endpoint)>1000:raise AppError('INVALID_TOOL_URL','MCP 地址无效。')
    try:
        u=urlsplit(endpoint)
        if u.scheme=='https':return validate_endpoint(endpoint)
        if u.scheme!='http' or u.hostname!='127.0.0.1' or u.port is None or not 1024<=u.port<=65535 or u.port in {4173,8000,5432,55432,55439} or u.username or u.password or u.query or u.fragment or u.path!='/mcp':raise ValueError()
        if any(ord(c)<33 or ord(c)>126 for c in endpoint):raise ValueError()
        return endpoint
    except ValueError:raise AppError('INVALID_TOOL_URL','本机 MCP 仅支持 127.0.0.1 的非保留端口和 /mcp 路径。') from None

def is_local_endpoint(endpoint):
    return urlsplit(validate_mcp_endpoint(endpoint)).scheme=='http'

def schema_subset(schema):
    if not isinstance(schema,dict) or schema.get('type')!='object' or any(k not in {'type','properties','required','additionalProperties','title','description','$schema'} for k in schema):raise AppError('MCP_SCHEMA_UNSUPPORTED','仅支持简单对象参数的 MCP 工具。')
    props=schema.get('properties',{});required=schema.get('required',[])
    if not isinstance(props,dict) or len(props)>16 or not isinstance(required,list) or any(not isinstance(x,str) or x not in props for x in required) or len(set(required))!=len(required):raise AppError('MCP_SCHEMA_UNSUPPORTED','MCP 参数结构无效。')
    result={}
    for name,value in props.items():
        if not isinstance(name,str) or not name or len(name)>100 or not isinstance(value,dict) or not isinstance(value.get('type'),str) or value.get('type') not in {'string','number','integer','boolean'} or any(k not in {'type','description','title','enum','minLength','maxLength','minimum','maximum'} for k in value):raise AppError('MCP_SCHEMA_UNSUPPORTED','MCP 参数只支持基本类型。')
        for key in ('description','title'):
            if key in value and (not isinstance(value[key],str) or len(value[key])>2000):raise AppError('MCP_SCHEMA_UNSUPPORTED','MCP 参数说明无效。')
        for key in ('minLength','maxLength'):
            if key in value and (isinstance(value[key],bool) or not isinstance(value[key],int) or not 0<=value[key]<=10000):raise AppError('MCP_SCHEMA_UNSUPPORTED','MCP 参数长度无效。')
        for key in ('minimum','maximum'):
            if key in value and (isinstance(value[key],bool) or not isinstance(value[key],(int,float)) or not math.isfinite(value[key])):raise AppError('MCP_SCHEMA_UNSUPPORTED','MCP 参数范围无效。')
        if 'enum' in value and (not isinstance(value['enum'],list) or len(value['enum'])>100 or any(not isinstance(v,(str,int,float,bool)) or isinstance(v,float) and not math.isfinite(v) for v in value['enum'])):raise AppError('MCP_SCHEMA_UNSUPPORTED','MCP 参数枚举无效。')
        result[name]=value
    return {'type':'object','properties':result,'required':required,'additionalProperties':False}

def validate_arguments(schema,args):
    if not isinstance(args,dict) or set(args)-set(schema['properties']) or not set(schema['required'])<=set(args):raise AppError('INVALID_TOOL_ARGUMENTS','MCP 工具参数不符合配置。')
    for name,value in args.items():
        spec=schema['properties'][name];kind=spec['type']
        good=(isinstance(value,str) if kind=='string' else isinstance(value,bool) if kind=='boolean' else isinstance(value,int) and not isinstance(value,bool) if kind=='integer' else isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value))
        if not good or ('enum' in spec and value not in spec['enum']):raise AppError('INVALID_TOOL_ARGUMENTS','MCP 参数类型或枚举无效。')
        if isinstance(value,str) and (len(value)>10000 or len(value)<spec.get('minLength',0) or len(value)>spec.get('maxLength',10000)):raise AppError('INVALID_TOOL_ARGUMENTS','MCP 文本参数长度无效。')
        if kind in {'number','integer'} and (value<spec.get('minimum',-1e100) or value>spec.get('maximum',1e100)):raise AppError('INVALID_TOOL_ARGUMENTS','MCP 数值参数超出范围。')
    if len(json.dumps(args,ensure_ascii=False))>20000:raise AppError('INVALID_TOOL_ARGUMENTS','MCP 参数过长。')

class Client:
    def __init__(self,endpoint,stop=None,token=None):
        self.endpoint=validate_mcp_endpoint(endpoint);self.url=urlsplit(endpoint);self.session=None;self.version=None;self.counter=0;self.stop=stop or threading.Event();self.token=token;self.deadline=time.monotonic()+25
    def check(self):
        if self.stop.is_set() or time.monotonic()>self.deadline:raise AppError('MCP_TIMEOUT','MCP 操作已取消或超时。',504)
    def _connection(self):
        self.check()
        if self.url.scheme=='http':return http.client.HTTPConnection('127.0.0.1',self.url.port,timeout=5)
        try:addresses=socket.getaddrinfo(self.url.hostname,443,type=socket.SOCK_STREAM)
        except OSError:raise AppError('MCP_DNS_ERROR','MCP 域名解析失败。',502) from None
        ips=list(dict.fromkeys(a[4][0] for a in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):raise AppError('MCP_ADDRESS_FORBIDDEN','MCP 地址必须解析到公网。',403)
        self.check()
        class Pinned(http.client.HTTPSConnection):
            def connect(inner):inner.sock=inner._context.wrap_socket(socket.create_connection((ips[0],443),timeout=inner.timeout),server_hostname=inner.host)
        return Pinned(self.url.hostname,443,timeout=5,context=ssl.create_default_context())
    def request(self,method,params=None,notify=False):
        self.check();self.counter+=1;request={'jsonrpc':'2.0','method':method}
        if params is not None:request['params']=params
        if not notify:request['id']=self.counter
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream','User-Agent':'ZhixuMCP/1.0'}
        if self.token:headers['Authorization']='Bearer '+self.token
        if self.session:headers['Mcp-Session-Id']=self.session
        if self.version:headers['MCP-Protocol-Version']=self.version
        conn=self._connection()
        try:
            self.check();conn.request('POST',self.url.path or '/',body=json.dumps(request,ensure_ascii=False).encode(),headers=headers);response=conn.getresponse()
            if response.status not in (200,202):raise AppError('MCP_HTTP_ERROR',f'MCP 服务返回 HTTP {response.status}。',502)
            session=response.getheader('Mcp-Session-Id')
            if session:
                if len(session)>200 or any(ord(c)<33 or ord(c)>126 for c in session):raise AppError('MCP_SESSION_INVALID','MCP 会话标识无效。',502)
                if self.session and session!=self.session:raise AppError('MCP_SESSION_INVALID','MCP 会话标识发生变化。',502)
                self.session=session
            if notify:
                if response.status!=202:raise AppError('MCP_PROTOCOL_ERROR','MCP 通知响应无效。',502)
                return None
            if response.status!=200:raise AppError('MCP_PROTOCOL_ERROR','MCP 请求没有返回结果。',502)
            content_type=response.getheader('Content-Type','').lower()
            if content_type.startswith('application/json'):
                raw=response.read(LIMIT+1)
                if len(raw)>LIMIT:raise AppError('MCP_OUTPUT_LIMIT','MCP 响应超过大小限制。',502)
                payload=json.loads(raw)
            elif content_type.startswith('text/event-stream'):
                total=0;parts=[];payload=None
                while True:
                    line=response.readline(LIMIT+1);total+=len(line)
                    if total>LIMIT:raise AppError('MCP_OUTPUT_LIMIT','MCP 响应超过大小限制。',502)
                    if not line:break
                    if line in (b'\n',b'\r\n'):
                        if parts:
                            item=json.loads(b'\n'.join(parts));parts=[]
                            if not isinstance(item,dict):raise AppError('MCP_PROTOCOL_ERROR','MCP 流格式无效。',502)
                            if item.get('id')==request['id']:payload=item;break
                            if 'method' in item and 'id' in item:raise AppError('MCP_UNSUPPORTED_REQUEST','不支持服务端发起的交互请求。',502)
                    elif line.startswith(b'data:'):parts.append(line[5:].lstrip().rstrip(b'\r\n'))
                if payload is None:raise AppError('MCP_PROTOCOL_ERROR','MCP 流未返回本次请求结果。',502)
            else:raise AppError('MCP_CONTENT_TYPE','MCP 响应类型无效。',502)
            if not isinstance(payload,dict) or payload.get('jsonrpc')!='2.0' or payload.get('id')!=request['id']:raise AppError('MCP_PROTOCOL_ERROR','MCP 响应编号无效。',502)
            if 'error' in payload:raise AppError('MCP_REMOTE_ERROR','MCP 服务报告请求失败。',502)
            if not isinstance(payload.get('result'),dict):raise AppError('MCP_PROTOCOL_ERROR','MCP 返回结果无效。',502)
            return payload['result']
        except AppError:raise
        except (OSError,ssl.SSLError,http.client.HTTPException,ValueError,UnicodeDecodeError):raise AppError('MCP_NETWORK_ERROR','MCP 连接失败、超时或响应格式无效。',502) from None
        finally:conn.close()
    def close(self):
        if not self.session or self.stop.is_set():return
        conn=None
        try:
            conn=self._connection();conn.timeout=2
            headers={'Mcp-Session-Id':self.session,'MCP-Protocol-Version':self.version or VERSION}
            if self.token:headers['Authorization']='Bearer '+self.token
            conn.request('DELETE',self.url.path or '/',headers=headers)
            response=conn.getresponse();response.read(1024)
        except Exception:pass
        finally:
            if conn:conn.close()
    def start(self):
        result=self.request('initialize',{'protocolVersion':VERSION,'capabilities':{},'clientInfo':{'name':'zhixu','version':'1.0'}})
        if result.get('protocolVersion')!=VERSION or not isinstance(result.get('capabilities'),dict) or 'tools' not in result['capabilities']:raise AppError('MCP_UNSUPPORTED','服务不支持所需 MCP 协议或工具能力。',502)
        self.version=VERSION;self.request('notifications/initialized',notify=True)
    def tools(self):
        tools=[];cursor=None;seen=set()
        for _ in range(5):
            data=self.request('tools/list',{'cursor':cursor} if cursor else {})
            entries=data.get('tools')
            if not isinstance(entries,list) or len(entries)>100:raise AppError('MCP_PROTOCOL_ERROR','MCP 工具列表无效。',502)
            for tool in entries:
                if not isinstance(tool,dict) or not isinstance(tool.get('name'),str) or not 1<=len(tool['name'])<=100 or tool['name'] in seen:raise AppError('MCP_PROTOCOL_ERROR','MCP 工具名称无效或重复。',502)
                seen.add(tool['name'])
                if not isinstance(tool.get('annotations'),dict) or tool['annotations'].get('readOnlyHint') is not True:continue
                try:schema=schema_subset(tool.get('inputSchema'))
                except AppError:continue
                description=tool.get('description','')
                if not isinstance(description,str):continue
                tools.append({'name':tool['name'],'description':description[:1000],'inputSchema':schema})
            cursor=data.get('nextCursor')
            if cursor is None:return tools
            if not isinstance(cursor,str) or len(cursor)>1000:raise AppError('MCP_PROTOCOL_ERROR','MCP 分页标识无效。',502)
        raise AppError('MCP_TOO_MANY_TOOLS','MCP 工具列表超过分页上限。',502)

def _discover(endpoint,token,stop):
    client=Client(endpoint,stop,token)
    try:client.start();return client.tools()
    finally:client.close()

def _call(endpoint,tool_name,schema,args,token,stop):
    validate_arguments(schema,args);client=Client(endpoint,stop,token)
    try:
        client.start();available=client.tools();tool=next((t for t in available if t['name']==tool_name),None)
        if not tool or tool['inputSchema']!=schema:raise AppError('MCP_TOOL_CHANGED','MCP 工具不可用或参数已变化，请重新配置并发布应用。',409)
        client.check();result=client.request('tools/call',{'name':tool_name,'arguments':args})
        if result.get('isError'):raise AppError('MCP_TOOL_ERROR','MCP 工具执行失败。',502)
        content=result.get('content',[])
        if not isinstance(content,list):raise AppError('MCP_PROTOCOL_ERROR','MCP 工具结果无效。',502)
        text='\n'.join(x.get('text','') for x in content if isinstance(x,dict) and x.get('type')=='text' and isinstance(x.get('text'),str))
        if not text:raise AppError('MCP_EMPTY_OUTPUT','MCP 工具没有返回文本结果。',502)
        if len(text)>50000:raise AppError('MCP_OUTPUT_LIMIT','MCP 工具结果超过 50000 字。',502)
        return text.replace(token,"[凭据已隐藏]") if token else text
    finally:client.close()

async def _run(func,*args):
    stop=threading.Event()
    try:return await asyncio.wait_for(asyncio.to_thread(func,*args,stop),timeout=30)
    except TimeoutError:stop.set();raise AppError('MCP_TIMEOUT','MCP 操作超时。',504) from None
    except asyncio.CancelledError:stop.set();raise
async def discover(endpoint,token=None):
    result=await _run(_discover,endpoint,token)
    if token and token in json.dumps(result,ensure_ascii=False):raise AppError('MCP_CREDENTIAL_EXPOSED','服务在工具描述中返回了凭据，请检查服务配置。',502)
    return result
async def call(endpoint,name,schema,args,token=None):return await _run(_call,endpoint,name,schema,args,token)
