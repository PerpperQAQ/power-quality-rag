"""Read-only, bounded Feishu custom-app document preview (no credential persistence)."""
import asyncio
import json
import re
from urllib.parse import urlsplit
import httpx
from .security import AppError

ID=re.compile(r'^[A-Za-z0-9_-]{5,150}$')
SPACE_ID=re.compile(r'^[0-9]{1,30}$')
MAX_BODY=2*1024*1024

def parse_url(value):
    if not isinstance(value,str) or len(value)>1000:raise AppError('FEISHU_URL','请输入飞书文档或知识库节点链接。')
    try:u=urlsplit(value);host=(u.hostname or '').lower();port=u.port
    except ValueError:raise AppError('FEISHU_URL','飞书链接无效。') from None
    parts=u.path.strip('/').split('/')
    if u.scheme!='https' or not (host=='feishu.cn' or host.endswith('.feishu.cn')) or u.username or u.password or port not in (None,443) or len(parts)!=2 or parts[0] not in {'wiki','docx'} or not ID.fullmatch(parts[1]):raise AppError('FEISHU_URL','仅支持飞书 HTTPS 文档或知识库节点链接。')
    return parts[0],parts[1],host

async def request_json(client,method,url,**kwargs):
    try:
        async with client.stream(method,url,**kwargs) as response:
            if response.status_code!=200:raise AppError('FEISHU_HTTP','飞书接口请求失败，请检查网络、应用权限和文档授权。',502)
            data=bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data)>MAX_BODY:raise AppError('FEISHU_RESPONSE_LIMIT','飞书接口返回内容超过限制。',502)
        result=json.loads(data)
        if not isinstance(result,dict):raise ValueError()
        code=result.get('code')
        if isinstance(code,bool) or not isinstance(code,int):raise AppError('FEISHU_DATA','飞书接口响应格式无效。',502)
        if code!=0:raise AppError('FEISHU_API',f"飞书接口返回错误码 {code}；请检查应用权限、文档授权和链接。",502)
        return result
    except AppError:raise
    except (ValueError,UnicodeError,httpx.HTTPError):raise AppError('FEISHU_NETWORK','无法读取飞书响应，请检查网络和授权。',502) from None

async def preview(url,app_id,app_secret,include_children=False):
    kind,token,host=parse_url(url)
    if not isinstance(app_id,str) or not re.fullmatch(r'cli_[A-Za-z0-9]{6,100}',app_id):raise AppError('FEISHU_CREDENTIAL','请输入有效的飞书应用 App ID。')
    if not isinstance(app_secret,str) or not 8<=len(app_secret)<=512 or any(ord(c)<33 or ord(c)>126 for c in app_secret):raise AppError('FEISHU_CREDENTIAL','请输入有效的飞书应用 App Secret。')
    if not isinstance(include_children,bool):raise AppError('INVALID_FIELD','包含子节点选项无效。')
    async with httpx.AsyncClient(timeout=12,follow_redirects=False,trust_env=False) as client:
        auth=await request_json(client,'POST','https://accounts.feishu.cn/oauth/v3/token',json={'grant_type':'client_credentials','client_id':app_id,'client_secret':app_secret})
        access=auth.get('access_token')
        if not isinstance(access,str) or not 8<=len(access)<=8192 or any(ord(c)<33 or ord(c)>126 for c in access):raise AppError('FEISHU_AUTH','飞书没有返回有效应用凭证，请检查自建应用配置。',502)
        headers={'Authorization':'Bearer '+access};items=[];skipped=[];truncated=False;seen=set()
        async def add_document(obj,title,source):
            if obj in seen:return
            if not isinstance(obj,str) or not ID.fullmatch(obj):raise AppError('FEISHU_DATA','飞书返回了无效文档标识。',502)
            seen.add(obj)
            if len(items)>=20:return
            data=await request_json(client,'GET',f'https://open.feishu.cn/open-apis/docx/v1/documents/{obj}/raw_content',headers=headers)
            content=(data.get('data') or {}).get('content')
            if not isinstance(content,str) or not content.strip():raise AppError('FEISHU_EMPTY','飞书文档没有可导入的纯文本。',422)
            if len(content)>200000:raise AppError('FEISHU_DOC_LIMIT','飞书文档超过 20 万字，请拆分后导入。',413)
            title=str(title or ('飞书文档-'+obj))[:120]
            items.append({'id':obj,'title':title,'content':content,'sourceUrl':source})
        if kind=='docx':
            await add_document(token,'飞书文档-'+token,f'https://{host}/docx/{token}')
        else:
            result=await request_json(client,'GET','https://open.feishu.cn/open-apis/wiki/v2/spaces/node_by_token',params={'token':token},headers=headers)
            root=(result.get('data') or {}).get('node')
            if not isinstance(root,dict) or not SPACE_ID.fullmatch(str(root.get('space_id',''))):raise AppError('FEISHU_DATA','飞书未返回有效知识空间信息。',502)
            space=str(root['space_id']);queue=[root];visited=set();pages=0
            while queue:
                node=queue.pop(0);node_token=node.get('node_token')
                if not isinstance(node_token,str) or not ID.fullmatch(node_token):raise AppError('FEISHU_DATA','飞书返回了无效知识节点。',502)
                if node_token in visited:continue
                visited.add(node_token)
                if len(visited)>100:truncated=True;break
                if node.get('obj_type')=='docx':
                    try:await add_document(node.get('obj_token'),node.get('title'),f'https://{host}/wiki/{node_token}')
                    except AppError as error:skipped.append({'title':str(node.get('title') or node_token)[:120],'reason':error.message})
                else:skipped.append({'title':str(node.get('title') or node_token)[:120],'reason':'仅支持新版云文档（docx）；此节点类型未导入。'})
                if len(items)>=20:
                    truncated=bool(queue or (include_children and node.get('has_child')));break
                if include_children and node.get('has_child'):
                    cursor=None
                    while True:
                        if pages>=10:truncated=True;break
                        params={'parent_node_token':node_token,'page_size':50}
                        if cursor:params['page_token']=cursor
                        page=await request_json(client,'GET',f'https://open.feishu.cn/open-apis/wiki/v2/spaces/{space}/nodes',params=params,headers=headers);pages+=1
                        data=page.get('data') or {};children=data.get('items',[])
                        if not isinstance(children,list) or any(not isinstance(c,dict) for c in children):raise AppError('FEISHU_DATA','飞书返回的节点列表无效。',502)
                        queue.extend(children)
                        if len(queue)>100:queue=queue[:100];truncated=True;break
                        if not data.get('has_more'):break
                        next_cursor=data.get('page_token')
                        if not isinstance(next_cursor,str) or not next_cursor or next_cursor==cursor:raise AppError('FEISHU_DATA','飞书分页信息无效。',502)
                        cursor=next_cursor
    return {'items':items,'skipped':skipped[:100],'truncated':truncated,'format':'plain_text'}
