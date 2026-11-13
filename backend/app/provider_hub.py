"""OpenAI-compatible chat/embedding providers, encrypted keys and qualified IDs."""
import asyncio
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit
from fastapi import APIRouter,Depends
from sqlalchemy import select
from .db import Record,Secret,bump,revision_lock
from .security import AppError,cipher,require,safe_id,get_secret
from .state import record,put,text_field,data_of,now
from .services import uid
from .providers import TongyiProvider,ProviderError,_usage
from .remote_tools import validate_endpoint

def http_json(endpoint,token,path,payload=None):
    u=urlsplit(validate_endpoint(endpoint));addresses=socket.getaddrinfo(u.hostname,443,type=socket.SOCK_STREAM);ips=list(dict.fromkeys(a[4][0] for a in addresses))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):raise AppError('PROVIDER_ADDRESS_FORBIDDEN','服务商地址必须为公网 HTTPS 地址。',403)
    class PinnedHTTPS(http.client.HTTPSConnection):
        def connect(self):self.sock=self._context.wrap_socket(socket.create_connection((ips[0],443),timeout=self.timeout),server_hostname=self.host)
    conn=PinnedHTTPS(u.hostname,443,timeout=60,context=ssl.create_default_context())
    try:
        conn.request('POST' if payload is not None else 'GET',u.path.rstrip('/')+path,body=json.dumps(payload).encode() if payload is not None else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Accept':'application/json'})
        response=conn.getresponse();raw=response.read(4_000_001)
        if not 200<=response.status<300:raise ProviderError('provider_http_error',f'服务商返回 HTTP {response.status}。',response.status)
        if len(raw)>4_000_000:raise ProviderError('provider_output_limit','服务商返回内容过大。')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError()
        return value
    except ProviderError:raise
    except Exception:raise ProviderError('provider_request_failed','服务商连接失败或响应格式无效。') from None
    finally:conn.close()

class Compatible:
    def __init__(self,config,key):self.config=config;self.key=key
    async def aclose(self):pass
    async def request(self,path,payload=None):
        try:return await asyncio.to_thread(http_json,self.config['endpoint'],self.key,path,payload)
        except AppError:raise
        except ProviderError:raise
        except Exception:raise ProviderError('provider_network_error','服务商连接失败。') from None
    def payload(self,messages,model,generation):
        generation=generation or {};return {'model':model,'messages':messages,**({'temperature':generation['temperature']} if generation.get('temperature') is not None else {}),**({'max_tokens':generation['maxTokens']} if generation.get('maxTokens') is not None else {})}
    async def chat(self,messages,model,generation=None):
        data=await self.request('/chat/completions',self.payload(messages,model,generation));result=_usage(data,model,'llm',None)
        try:
            choice=data['choices'][0];value=choice['message']['content']
            if not isinstance(value,str) or not value.strip() or choice.get('finish_reason')!='stop':raise ValueError()
            result.data=value;return result
        except (KeyError,IndexError,ValueError,TypeError):raise ProviderError('invalid_chat_response','服务商未返回完整文本回答。',usage=[result.usage]) from None
    async def agent_step(self,messages,model,tools,generation=None):
        data=await self.request('/chat/completions',{**self.payload(messages,model,generation),'tools':tools});result=_usage(data,model,'llm',None)
        try:
            choice=data['choices'][0];reply=choice['message'];calls=reply.get('tool_calls') or []
            if choice.get('finish_reason') not in {'stop','tool_calls'} or not isinstance(calls,list) or len(calls)>8:raise ValueError()
            for call in calls:
                if call.get('type')!='function' or not isinstance(call.get('id'),str) or not isinstance(call['function']['arguments'],str) or not isinstance(call['function']['name'],str):raise ValueError()
            result.data={'role':'assistant','content':reply.get('content') or '', 'tool_calls':calls};return result
        except (KeyError,IndexError,ValueError,TypeError):raise ProviderError('invalid_agent_response','服务商工具调用格式无效。',usage=[result.usage]) from None
    async def embed(self,texts,model):
        data=await self.request('/embeddings',{'model':model,'input':texts});result=_usage(data,model,'embedding',None)
        try:
            rows=sorted(data['data'],key=lambda x:x['index'])
            if [r['index'] for r in rows]!=list(range(len(texts))):raise ValueError()
            result.data=[r['embedding'] for r in rows];return result
        except (KeyError,ValueError,TypeError):raise ProviderError('invalid_embeddings','服务商向量数量或格式无效。',usage=[result.usage]) from None

class ProviderRouter:
    def __init__(self,configs,keys,base,base_key):self.configs=configs;self.keys=keys;self.base=base;self.base_key=base_key;self.clients={}
    def route(self,model):
        if model.startswith('ext:'):
            try:_,identifier,raw=model.split(':',2);config=self.configs[identifier];key=self.keys[identifier]
            except (KeyError,ValueError):raise AppError('PROVIDER_UNAVAILABLE','服务商不存在、已停用或凭据未配置。',409) from None
            if identifier not in self.clients:self.clients[identifier]=Compatible(config,key)
            return self.clients[identifier],raw
        if not self.base_key:raise AppError('MODEL_NOT_CONFIGURED','请先配置通义凭据。',409)
        if 'tongyi' not in self.clients:self.clients['tongyi']=TongyiProvider(self.base,self.base_key)
        return self.clients['tongyi'],model
    def qualify(self,result,model):
        result.model=model
        for attempt in result.attempts:attempt['model']=model
        return result
    async def chat(self,messages,model,generation=None):
        client,raw=self.route(model);return self.qualify(await client.chat(messages,raw,generation),model)
    async def stream_chat(self,messages,model,generation=None):
        client,raw=self.route(model)
        if hasattr(client,'stream_chat'):
            async for result in client.stream_chat(messages,raw,generation):yield self.qualify(result,model)
        else:
            result=self.qualify(await client.chat(messages,raw,generation),model)
            if not result.attempts:result.attempts=[result.usage]
            yield result
    async def agent_step(self,messages,model,tools,generation=None):
        client,raw=self.route(model);return self.qualify(await client.agent_step(messages,raw,tools,generation),model)
    async def embed(self,texts,model):
        client,raw=self.route(model);return self.qualify(await client.embed(texts,raw),model)
    async def rerank(self,query,documents,model):
        client,raw=self.route(model)
        if not hasattr(client,'rerank'):raise AppError('UNSUPPORTED_CAPABILITY','此兼容服务商未接入重排协议。',409)
        return self.qualify(await client.rerank(query,documents,raw),model)
    async def recognize(self,image,mime,model,vision=False):
        client,raw=self.route(model)
        if not hasattr(client,'recognize'):raise AppError('UNSUPPORTED_CAPABILITY','此兼容服务商未接入图像解析协议。',409)
        return self.qualify(await client.recognize(image,mime,raw,vision),model)
    async def aclose(self):
        for client in self.clients.values():await client.aclose()

def build_provider(session):
    rows=session.scalars(select(Record).where(Record.collection=='deliveryProviders')).all();configs={};keys={}
    for row in rows:
        if not row.data.get('enabled'):continue
        secret=session.get(Secret,row.id)
        if secret:
            try:keys[row.id]=cipher().decrypt(secret.ciphertext.encode()).decode();configs[row.id]=row.data
            except Exception:raise AppError('SECRET_UNREADABLE','服务商密钥无法解密。',409) from None
    base_key=get_secret(session) if session.get(Secret,'tongyi') else None
    return ProviderRouter(configs,keys,data_of(session,'provider','tongyi'),base_key)

def ensure_model(session,model):
    if model.startswith('ext:'):build_provider(session).route(model)
    else:get_secret(session)

def router_for(context,session_factory):
    router=APIRouter(prefix='/api/delivery/providers')
    @router.get('')
    def listing(ctx=Depends(context)):
        require(ctx[0],'admin')
        with session_factory()() as session:return {'items':[r.data for r in session.scalars(select(Record).where(Record.collection=='deliveryProviders')).all()]}
    @router.post('')
    def save(body:dict,ctx=Depends(context)):
        require(ctx[0],'admin');endpoint=validate_endpoint(text_field(body,'endpoint',1000,True).rstrip('/'));key=text_field(body,'key',2048,True)
        if len(key)<8 or any(ord(c)<32 for c in key):raise AppError('INVALID_KEY','服务商密钥无效。')
        with session_factory().begin() as session:
            revision_lock(session);identifier='prov-'+uid('')[1:];value={'id':identifier,'name':text_field(body,'name',100,True),'endpoint':endpoint,'enabled':True,'createdAt':now()};session.add(Secret(id=identifier,ciphertext=cipher().encrypt(key.encode()).decode()));put(session,'deliveryProviders',identifier,value);bump(session)
        return {'item':value}
    @router.post('/{provider_id}/discover')
    async def discover(provider_id:str,ctx=Depends(context)):
        require(ctx[0],'admin')
        with session_factory()() as session:client,_=build_provider(session).route('ext:'+safe_id(provider_id)+':catalog')
        data=await client.request('/models');items=data.get('data',[])
        return {'models':[x['id'] for x in items[:2000] if isinstance(x,dict) and isinstance(x.get('id'),str)]}
    @router.post('/{provider_id}/models')
    def add_model(provider_id:str,body:dict,ctx=Depends(context)):
        require(ctx[0],'admin');raw=text_field(body,'model',100,True);cap=body.get('capability')
        if cap not in {'llm','embedding'}:raise AppError('UNSUPPORTED_CAPABILITY','兼容协议支持语言模型与向量模型。')
        with session_factory().begin() as session:
            revision_lock(session);data_of(session,'deliveryProviders',safe_id(provider_id));model='ext:'+provider_id+':'+raw;identifier='extmodel-'+hashlib.sha256((model+cap).encode()).hexdigest()[:32];value={'id':identifier,'model':model,'capability':cap,'enabled':True};put(session,'catalog',identifier,value);bump(session)
        return {'item':value}
    return router
