"""Fixed Tavily protocol, currently callable only via HTTP MockTransport.

Live transport is deliberately unavailable until a new per-request budget and
submission ledger are approved. Search snippets are never promoted to bodies.
"""
import ipaddress
import math
from urllib.parse import urlsplit
import httpx
from ..security import AppError

ENDPOINTS={'search':'https://api.tavily.com/search','extract':'https://api.tavily.com/extract'}


def public_url(value):
    if not isinstance(value,str) or len(value)>2000:raise ValueError('url')
    u=urlsplit(value)
    if u.scheme!='https' or u.username or u.password or not u.hostname or u.port not in {None,443}:
        raise ValueError('url')
    host=u.hostname.lower().rstrip('.')
    if '.' not in host or host.endswith(('.localhost','.local','.internal')) or host=='localhost':raise ValueError('host')
    try:ipaddress.ip_address(host)
    except ValueError:pass
    else:raise ValueError('ip_literal')
    return value


def search_request(public_query):
    if not isinstance(public_query,str) or not 1<=len(public_query)<=250:raise AppError('PUBLIC_QUERY_INVALID','公开查询无效。',400)
    return {'query':public_query,'search_depth':'basic','max_results':3,'topic':'general','include_answer':False,
            'include_raw_content':False,'include_images':False,'auto_parameters':False,'include_usage':True}


def extract_request(urls,search_urls):
    try:
        if not isinstance(urls,list) or not 1<=len(urls)<=2 or len(set(urls))!=len(urls) or not set(urls).issubset(search_urls):raise ValueError()
        urls=[public_url(u) for u in urls]
    except (ValueError,TypeError):raise AppError('EXTRACT_URL_INVALID','正文URL必须来自本次已核对搜索结果。',400) from None
    return {'urls':urls,'extract_depth':'basic','format':'markdown','include_images':False,'include_usage':True,'timeout':20}


def parse(operation,data,requested_urls=()):
    try:
        if not isinstance(data,dict) or not isinstance(data.get('results'),list) or len(data['results'])>3:raise ValueError()
        usage=data.get('usage',{});credits=usage.get('credits') if isinstance(usage,dict) else None
        known=isinstance(credits,(int,float)) and not isinstance(credits,bool) and math.isfinite(credits) and credits>=0
        items=[];seen=set()
        for r in data['results']:
            url=public_url(r['url'])
            if url in seen or operation=='extract' and url not in requested_urls:raise ValueError()
            seen.add(url)
            body=r.get('raw_content','') if operation=='extract' else r.get('content','')
            if not isinstance(body,str) or len(body)>500000:raise ValueError()
            title=r.get('title') or urlsplit(url).hostname
            if not isinstance(title,str) or len(title)>500:raise ValueError()
            items.append({'url':url,'title':title,'page_date':None,'edition':None,'excerpt':body[:12000],
                'support_scope':'供应商提取内容，适用型号、版次和支持主张待核',
                'status':('body_available' if body.strip() else 'body_unavailable') if operation=='extract' else 'summary_only'})
        failures=data.get('failed_results',[]) if operation=='extract' else []
        if not isinstance(failures,list) or len(failures)>len(requested_urls):raise ValueError()
        for r in failures:
            url=public_url(r['url'])
            if url not in requested_urls or url in seen:raise ValueError()
            seen.add(url);items.append({'url':url,'title':urlsplit(url).hostname,'page_date':None,'edition':None,
                'excerpt':'','support_scope':'正文未获取','status':'body_unavailable'})
        if operation=='extract' and seen!=set(requested_urls):raise ValueError()
        rid=data.get('request_id')
        if rid is not None and (not isinstance(rid,str) or len(rid)>200):raise ValueError()
        return {'items':items,'request_id':rid,'credits':credits if known else None,'usage_known':known,
                'source_kind':'search_snippet' if operation=='search' else 'provider_extracted_text',
                'original_bytes_available':False,'claim_support_verified':False,'canonical_url_verified':False}
    except (ValueError,KeyError,TypeError):raise AppError('WEB_RESPONSE_INVALID','外部响应结构或来源关联无效。',502) from None


def fixture_call(operation,body,transport):
    if operation not in ENDPOINTS or type(transport) is not httpx.MockTransport:
        raise AppError('REAL_CALLS_NOT_AUTHORIZED','真实取证尚未获额度。',409)
    try:
        with httpx.Client(transport=transport,timeout=25,follow_redirects=False,trust_env=False) as client:
            response=client.post(ENDPOINTS[operation],json=body)
        if response.status_code!=200 or len(response.content)>1024*1024:raise ValueError()
        result=parse(operation,response.json(),body.get('urls',()))
    except (httpx.HTTPError,ValueError):raise AppError('WEB_TRANSPORT_FAILED','外部取证失败；不自动重试。',502) from None
    return {**result,'fixture_only':True,'real_calls':0,'fixture_http_attempts':1}
