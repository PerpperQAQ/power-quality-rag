import copy
import math
import re
import time
import uuid
import unicodedata
from collections import defaultdict
import jieba
from sqlalchemy import delete, func, select, text
from .db import Chunk, Record
from .security import AppError, get_secret
from .model_catalog import embedding_input_budget
from .state import DEFAULT_RETRIEVAL, catalog_enabled, data_of, document_summary, log_record, now, put, record, retrieval_config


def uid(prefix): return f"{prefix}-{uuid.uuid4().hex}"

def new_provider(session):
    if session.scalar(select(Record.id).where(Record.collection=="deliveryProviders").limit(1)):
        from .provider_hub import build_provider
        return build_provider(session)
    from .providers import TongyiProvider
    return TongyiProvider(data_of(session,"provider","tongyi"),get_secret(session))

def tokenize(value):
    return " ".join(token.lower() for token in jieba.cut(value) if re.fullmatch(r"[\w\u4e00-\u9fff]+",token) and token.strip())

def result_usage(result):
    return {"inputTokens":getattr(result,"input_tokens",None),"outputTokens":getattr(result,"output_tokens",None),"tokens":getattr(result,"total_tokens",None),"model":getattr(result,"model",""),"kind":getattr(result,"kind",""),"cachedTokens":getattr(result,"cached_tokens",None)}

def result_usages(result):
    values=getattr(result,"attempts",[])
    return [{"inputTokens":v.get("input_tokens"),"outputTokens":v.get("output_tokens"),"tokens":v.get("total_tokens"),"model":v.get("model",""),"kind":v.get("kind",""),"requestId":v.get("request_id"),"cachedTokens":v.get("cached_tokens")} for v in values] if values else [result_usage(result)]

def sum_usage(usages):
    def total(key):
        values=[item.get(key) for item in usages]
        if not values or any(value is None for value in values): return None
        return sum(values)
    return {key:total(key) for key in ("inputTokens","outputTokens","tokens")}

def validate_vectors(values,expected):
    if not isinstance(values,list) or len(values)!=expected or not values: raise AppError("INVALID_EMBEDDING","向量模型返回数量与输入不一致。",502)
    dimension=len(values[0])
    if dimension<1 or dimension>8192 or any(not isinstance(v,list) or len(v)!=dimension or any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in v) for v in values): raise AppError("INVALID_EMBEDDING","向量维度或内容不合法。",502)
    return dimension

async def embed_texts(session,texts,model):
    if not catalog_enabled(session,"embedding",model): raise AppError("MODEL_DISABLED","知识库向量模型未启用。",409)
    limit=embedding_input_budget(model)
    if any(len(value.encode("utf-8"))>limit for value in texts):
        raise AppError("EMBEDDING_INPUT_LIMIT","分块及检索增强内容超过该向量模型的保守输入预算，请减小分块长度或使用 text-embedding-v3/v4 后重建。",409)
    provider=new_provider(session)
    try:
        result=await provider.embed(texts,model)
        validate_vectors(result.data,len(texts))
        return result
    finally: await provider.aclose()

def automatic_metadata(extraction):
    if not extraction:return {}
    if extraction.get("status")!="completed":extraction=extraction.get("lastSuccess",{})
    grouped=defaultdict(list)
    for fact in extraction.get("facts",[]):
        field=fact.get("field");value=fact.get("value","")
        if not isinstance(value,str) or not value:continue
        if field=="time":
            label=fact.get("label") or ""
            key=next((name for name in ("发布日期","生效日期","修订日期") if name in label),"时间")
        else:key={"version":"版本","subject":"主体信息","owner":"负责人"}.get(field)
        if key and value not in grouped[key]:grouped[key].append(value)
    for name,key in (("keywords","关键词"),("questions","可能的问题")):
        grouped[key].extend(value for value in extraction.get(name,[]) if isinstance(value,str) and value)
    return {key:"；".join(values) for key,values in grouped.items() if values}

def effective_metadata(extraction,manual):
    return {**automatic_metadata(extraction),**manual}

MAX_INDEX_BYTES=8000
INDEX_WARNING="部分检索增强内容因模型输入预算而缩短或略过；原文与完整提取结果仍完整保留。"

def index_text(segment,metadata=None,warnings=None,max_bytes=MAX_INDEX_BYTES):
    """Index a bounded augmentation without truncating the original citation.

    The UTF-8 byte ceiling conservatively bounds input for the supported v3/v4
    embedding models. Smaller or unknown profiles are checked before their API
    request and fail explicitly rather than silently discarding original text.
    """
    content=segment["content"]
    if len(content.encode("utf-8"))>max_bytes:
        raise AppError("EMBEDDING_INPUT_LIMIT","原文单个分块超过安全向量输入预算，请减小分块长度后重新构建。",409)
    parts=[content];remaining=max_bytes-len(content.encode("utf-8"));capped=False
    def add(value,allow_trim=False):
        nonlocal remaining,capped
        raw=("\n"+value).encode("utf-8")
        if len(raw)<=remaining:
            parts.append(value);remaining-=len(raw);return
        capped=True
        if allow_trim and remaining>10:
            piece=raw[:remaining].decode("utf-8",errors="ignore").lstrip("\n")
            if piece:parts.append(piece);remaining-=len(("\n"+piece).encode("utf-8"))
    keywords=list(dict.fromkeys([*segment.get("keywords",[]),*segment.get("aiKeywords",[])]))
    questions=list(dict.fromkeys([*segment.get("questions",[]),*segment.get("aiQuestions",[])]))
    # Per-chunk retrieval aids take precedence over repeated whole-document aids.
    for value in keywords:add("检索关键词："+value)
    for value in questions:add("潜在问题："+value)
    total_metadata_chars=0
    for key,value in sorted((metadata or {}).items()):
        if not value:continue
        if key in {"关键词","可能的问题"} and (keywords or questions):continue
        limited=str(value)[:600]
        if len(str(value))>600:capped=True
        field=f"{key}：{limited}"
        budget=2000-total_metadata_chars
        if budget<=0:capped=True;break
        if len(field)>budget:field=field[:budget];capped=True
        total_metadata_chars+=len(field)
        add("文档元数据："+field,allow_trim=True)
    if capped and warnings is not None and INDEX_WARNING not in warnings:warnings.append(INDEX_WARNING)
    return "\n".join(parts)

def chunk_row(doc,kb,segment,vector,model):
    indexed=index_text(segment,doc.get("metadata",{}),max_bytes=embedding_input_budget(model))
    return Chunk(id=segment["id"],doc_id=doc["id"],kb_id=kb["id"],model=model,dimension=len(vector),enabled=bool(segment.get("enabled",True)),embedding=vector,content=segment["content"],lexemes=tokenize(indexed),payload={**copy.deepcopy(segment),"_indexText":indexed})


def canonical_excerpt(content):
    lines=[]
    for line in unicodedata.normalize("NFKC",content).splitlines():
        if re.fullmatch(r"\s*(?:```(?:markdown|md)?|[-*_]{3,})\s*",line):continue
        line=re.sub(r"^\s*#{1,6}\s+","",line)
        line=re.sub(r"^\s*[-+*]\s+","",line)
        line=re.sub(r"\*\*(.+?)\*\*",r"\1",line)
        lines.append(line)
    return re.sub(r"\s+","","\n".join(lines))


def distinct_candidates(ranked,chunks):
    selected=[];positions={}
    def priority(chunk):
        source=chunk.payload.get("provenance","")
        return 0 if "cloud_vision" in source else 1 if "cloud_ocr" in source else 2
    for identifier in ranked:
        chunk=chunks[identifier]
        key=(tuple(chunk.payload.get("sectionPath",[])),canonical_excerpt(chunk.content))
        if key not in positions:
            positions[key]=len(selected);selected.append(identifier)
        elif priority(chunk)>priority(chunks[selected[positions[key]]]):selected[positions[key]]=identifier
    return selected


def covering_context(query,ranked,chunks,top_k):
    """Keep complementary literal question terms inside the context budget.

    A multi-part question can rank several copies of one answer above the
    table answering its other half. Promote already-relevant candidates only
    when they add source-text coverage without losing existing coverage.
    """
    stop={"如何","什么","哪些","多少","为什么","怎么","可以","是否","能否","分别","资料","文档","查询","根据","告诉","介绍","知道","相关","the","and","what","when","which"}
    terms={t for t in tokenize(query).split() if len(t)>=2 and t not in stop and not t.isdecimal()}
    texts={i:unicodedata.normalize("NFKC",chunks[i].content).casefold() for i in ranked}
    coverage={i:{t for t in terms if t in texts[i]} for i in ranked}
    selected=list(ranked[:top_k])
    covered=set().union(*(coverage[i] for i in selected)) if selected else set()
    for identifier in ranked[top_k:]:
        if not coverage[identifier]-covered:continue
        for position in range(len(selected)-1,-1,-1):
            others=set().union(*(coverage[value] for index,value in enumerate(selected) if index!=position))
            replacement=others|coverage[identifier]
            if covered < replacement:
                selected[position]=identifier;covered=replacement;break
    return selected

async def update_segments(session,doc,segments):
    from .power_quality.guards import require_generic_mutable
    require_generic_mutable(doc)
    if not isinstance(segments,list) or len(segments)>5000: raise AppError("INVALID_CHUNKS","分块数据无效或过多。")
    clean=[];ids=set()
    old_by_id={item.id:item for item in session.scalars(select(Chunk).where(Chunk.doc_id==doc["id"]))}
    for raw in segments:
        if not isinstance(raw,dict):raise AppError("INVALID_CHUNK","分块必须为对象。")
        identifier=raw.get("id")
        if not isinstance(identifier,str) or len(identifier)>150 or identifier in ids: raise AppError("INVALID_CHUNK","分块标识无效或重复。")
        if identifier in old_by_id:
            pass
        elif session.get(Chunk,identifier): raise AppError("INVALID_CHUNK","分块标识已被其他文件使用。")
        ids.add(identifier)
        content=raw.get("content","")
        if not isinstance(content,str) or not content.strip() or len(content)>20000: raise AppError("INVALID_CHUNK","分块文本不能为空且最多 20000 字符。")
        for key in ("keywords","questions"):
            if not isinstance(raw.get(key,[]),list) or len(raw.get(key,[]))>50 or any(not isinstance(v,str) or len(v)>500 for v in raw.get(key,[])): raise AppError("INVALID_CHUNK","关键词或关联问题无效。")
        previous=old_by_id.get(identifier)
        automatic={key:copy.deepcopy(previous.payload[key]) for key in ("aiKeywords","aiQuestions","augmentationSource","sectionPath","sourceLocations","chunkMethod","splitNote","provenance","parentId","parentContent","parentLocation") if previous and key in previous.payload}
        if previous and previous.content!=content.strip():
            for key in ("parentId","parentContent","parentLocation"):automatic.pop(key,None)
        if previous and previous.content==content.strip() and previous.payload.get("table"):automatic["table"]=copy.deepcopy(previous.payload["table"])
        clean.append({"id":identifier,"content":content.strip(),"location":str(raw.get("location","手动分块"))[:500],"enabled":bool(raw.get("enabled",True)),"keywords":raw.get("keywords",[]),"questions":raw.get("questions",[]),"manual":bool(raw.get("manual",False)) or identifier not in old_by_id or old_by_id[identifier].content!=content.strip(),**automatic})
    kb=data_of(session,"knowledgeBases",doc["kbId"])
    index_warnings=list(doc.get("metadataExtraction",{}).get("warnings",[])) if doc.get("metadataExtraction") else []
    indexed={seg["id"]:index_text(seg,doc.get("metadata",{}),index_warnings,max_bytes=embedding_input_budget(kb["embedding"])) for seg in clean}
    changed=[seg for seg in clean if seg["id"] not in old_by_id or old_by_id[seg["id"]].payload.get("_indexText",old_by_id[seg["id"]].content)!=indexed[seg["id"]]]
    vectors={}
    usages=[]
    for start in range(0,len(changed),10):
        batch=changed[start:start+10]
        try:result=await embed_texts(session,[indexed[seg["id"]] for seg in batch],kb["embedding"])
        except Exception as error:
            error.prior_usage=usages
            error.audit={"query":doc["name"]+" · 手动分块更新","name":kb["name"],"model":kb["embedding"],"kbId":kb["id"]}
            raise
        existing_dimensions={row.dimension for row in old_by_id.values()}
        if existing_dimensions and existing_dimensions!={len(result.data[0])}:raise AppError("DIMENSION_CHANGED","向量维度发生变化，未保存分块修改。",409)
        usages.extend(result_usages(result));vectors.update({seg["id"]:vector for seg,vector in zip(batch,result.data)})
    for seg in clean:
        previous=old_by_id.get(seg["id"])
        vector=vectors.get(seg["id"],previous.embedding if previous else None)
        session.merge(chunk_row(doc,kb,seg,list(vector),kb["embedding"]))
    removed=set(old_by_id)-ids
    if removed: session.execute(delete(Chunk).where(Chunk.id.in_(removed)))
    if usages:
        log=log_record(uid("chunk-edit"),"ingestion",doc["name"]+" · 手动分块更新",kb["name"],kb["embedding"],kb_id=kb["id"])
        log.update({"status":"completed",**sum_usage(usages),"usage":usages})
        put(session,"logs",log["id"],log)
    if doc.get("metadataExtraction"):doc={**doc,"metadataExtraction":{**doc["metadataExtraction"],"warnings":index_warnings}}
    return {**doc,"segments":clean,"chunks":len(clean),"enabledChunks":sum(bool(s.get("enabled")) for s in clean)}

async def retrieve(session,query,kb_ids,top_k=5,candidate_k=30,use_rerank=False,retrieval=None):
    # Pin document filters and published chunks to one read snapshot across
    # asynchronous model calls, without loading every document's chunk JSON.
    if not session.in_transaction():session.connection(execution_options={"isolation_level":"REPEATABLE READ"})
    if not isinstance(query,str):raise AppError("INVALID_QUERY","问题必须为文本。")
    if not isinstance(kb_ids,list) or any(not isinstance(k,str) for k in kb_ids):raise AppError("INVALID_KBS","知识库范围必须为标识列表。")
    query=query.strip()
    if not query or len(query)>10000: raise AppError("INVALID_QUERY","请输入不超过 10000 字符的问题。")
    if not kb_ids: return [],[],[]
    if len(kb_ids)>100: raise AppError("TOO_MANY_KBS","单次检索知识库过多。")
    config=retrieval_config(retrieval)
    if isinstance(top_k,bool) or isinstance(candidate_k,bool) or not isinstance(top_k,int) or not isinstance(candidate_k,int) or top_k<1 or top_k>30 or candidate_k<top_k or candidate_k>100: raise AppError("INVALID_TOPK","候选数和引用数范围不正确。")
    kbs={k:data_of(session,"knowledgeBases",k) for k in kb_ids}
    models={kb["embedding"] for kb in kbs.values()}
    if len(models)!=1: raise AppError("INCOMPATIBLE_EMBEDDING","联合检索需要使用相同的向量模型。")
    model=next(iter(models))
    docs={identifier:value for identifier,value in session.execute(select(Record.id,document_summary()).where(Record.collection=="documents",Record.summary["kbId"].astext.in_(kb_ids))) if value.get("enabled") and (value.get("status")=="ready" or value.get("previousReady"))}
    if config["fileIds"]: docs={k:v for k,v in docs.items() if k in config["fileIds"]}
    if config["metadataKey"]:
        docs={k:v for k,v in docs.items() if config["metadataKey"] in v.get("metadata",{}) and config["metadataValue"] in [item.strip() for item in v["metadata"][config["metadataKey"]].split("；")]}
    if not docs: return [],[],[{"name":"筛选可检索文件","ms":0,"status":"ok"}]
    if not session.scalar(select(Chunk.id).where(Chunk.doc_id.in_(docs),Chunk.enabled).limit(1)):
        return [],[],[{"name":"筛选可检索分块","ms":0,"status":"ok"}]
    started=time.monotonic(); embed=await embed_texts(session,[query],model);vector=embed.data[0]
    dimensions=set(session.scalars(select(Chunk.dimension).where(Chunk.kb_id.in_(kb_ids)).distinct()))
    if dimensions and dimensions!={len(vector)}:
        error=AppError("DIMENSION_CHANGED","当前向量模型维度与已发布索引不一致，请重建为兼容的知识库。",409)
        error.prior_usage=result_usages(embed)
        raise error
    usages=result_usages(embed); stages=[{"name":"问题向量化","ms":round((time.monotonic()-started)*1000),"status":"ok"}]
    parameters={"docs":list(docs),"model":model,"dimension":len(vector),"vector":"["+",".join(str(float(x)) for x in vector)+"]","limit":candidate_k}
    started=time.monotonic()
    exact_sql="SELECT id, 1-(embedding <=> CAST(:vector AS vector)) AS score FROM chunks WHERE doc_id=ANY(:docs) AND enabled AND model=:model AND dimension=:dimension ORDER BY embedding <=> CAST(:vector AS vector), id LIMIT :limit"
    eligible=sum(d.get("enabledChunks",d.get("chunks",0)) for d in docs.values())
    dimension=len(vector)
    use_hnsw=eligible>5000 and dimension<=2000 and session.scalar(text("SELECT to_regclass(:name) IS NOT NULL"),{"name":f"chunks_cosine_{dimension}_hnsw"})
    vector_mode="精确检索"
    if use_hnsw:
        vector_mode="HNSW 大范围候选"
        session.execute(text("SET LOCAL hnsw.ef_search=100"));session.execute(text("SET LOCAL hnsw.iterative_scan='strict_order'"))
        distance=f"embedding::vector({dimension}) <=> CAST(:vector AS vector({dimension}))"
        sql=f"WITH nearest AS MATERIALIZED (SELECT id,{distance} AS distance FROM chunks WHERE doc_id=ANY(:docs) AND COALESCE((payload->>'enabled')::boolean,true) AND model=:model AND dimension={dimension} ORDER BY {distance} LIMIT :limit) SELECT id,1-distance AS score FROM nearest ORDER BY distance,id"
        vector_rows=session.execute(text(sql),parameters).all()
        if len(vector_rows)<min(candidate_k,eligible):
            vector_rows=session.execute(text(exact_sql),parameters).all();vector_mode="HNSW 候选不足，精确回退"
    else:vector_rows=session.execute(text(exact_sql),parameters).all()
    terms=list(dict.fromkeys(tokenize(query).split()))[:60]
    text_rows=[]
    if terms:
        parameters["query"]=" | ".join(terms)
        text_rows=session.execute(text("SELECT id,ts_rank(search_vector,to_tsquery('simple',:query)) AS score FROM chunks WHERE doc_id=ANY(:docs) AND enabled AND model=:model AND search_vector @@ to_tsquery('simple',:query) ORDER BY score DESC,id LIMIT :limit"),parameters).all()
    candidates={}
    # Weighted reciprocal rank fusion: text and vector scores have different scales.
    for rows,weight,key in ((vector_rows,config["vectorWeight"],"vectorScore"),(text_rows,1-config["vectorWeight"],"textScore")):
        if weight==0: continue
        for rank,(identifier,score) in enumerate(rows,1):
            item=candidates.setdefault(identifier,{"hybridScore":0,"vectorScore":None,"textScore":None})
            item[key]=max(0,float(score));item["hybridScore"]+=61*weight/(60+rank)
    ranked=sorted(candidates,key=lambda k:(-candidates[k]["hybridScore"],k))[:candidate_k]
    # Threshold measures evidence relevance, independently of rank fusion. A
    # rank alone is insufficient: the best candidate can still be unrelated.
    for identifier,item in candidates.items():
        item["relevanceScore"]=max(item.get("vectorScore") or 0, min(1,(item.get("textScore") or 0)*10))
    ranked=[i for i in ranked if candidates[i]["relevanceScore"]>=config["threshold"]]
    chunks={row.id:row for row in session.scalars(select(Chunk).where(Chunk.id.in_(ranked)))} if ranked else {}
    stages.append({"name":f"关键词与向量 RRF 融合（{vector_mode}）","ms":round((time.monotonic()-started)*1000),"status":"ok"})
    before=len(ranked);ranked=distinct_candidates(ranked,chunks)
    if before!=len(ranked):stages.append({"name":f"重复片段去重：{before} → {len(ranked)}","ms":0,"status":"ok"})
    if use_rerank and ranked:
        rerank_model=config["rerankModel"]
        if not catalog_enabled(session,"rerank",rerank_model): raise AppError("NO_RERANK_MODEL","请配置并启用重排模型。",409)
        provider=new_provider(session);started=time.monotonic()
        try: result=await provider.rerank(query,[chunks[i].content for i in ranked],rerank_model)
        except Exception as error:
            error.prior_usage=usages
            raise
        finally:await provider.aclose()
        usages.extend(result_usages(result));new_rank=[]
        for item in result.data:
            index=item.get("index");score=item.get("score")
            if not isinstance(index,int) or index<0 or index>=len(ranked) or not isinstance(score,(int,float)) or not math.isfinite(score): raise AppError("INVALID_RERANK","重排模型返回了无效排序。",502)
            identifier=ranked[index]
            if identifier in new_rank:continue
            candidates[identifier]["rerankScore"]=float(score);new_rank.append(identifier)
        ranked=sorted(new_rank,key=lambda i:-candidates[i]["rerankScore"])
        stages.append({"name":"通义重排","ms":round((time.monotonic()-started)*1000),"status":"ok"})
    chosen=covering_context(query,ranked,chunks,top_k)
    if chosen!=ranked[:top_k]:stages.append({"name":"补充组合问题的互补原文证据","ms":0,"status":"ok"})
    ranked=chosen
    citations=[]
    for identifier in ranked[:top_k]:
        chunk=chunks[identifier];doc=docs[chunk.doc_id];scores=candidates[identifier]
        citations.append({"id":identifier,"docId":doc["id"],"name":doc["name"],"format":doc["format"],"kbName":kbs[doc["kbId"]]["name"],"location":chunk.payload.get("location","原文"),"content":chunk.payload.get("parentContent",chunk.content),"provenance":"手动编辑片段" if chunk.payload.get("manual") else chunk.payload.get("provenance",doc.get("provenance","原文提取")),"score":scores.get("rerankScore",scores["hybridScore"]),**{k:v for k,v in scores.items() if v is not None}})
    return citations,usages,stages
