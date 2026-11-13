import copy
import math
import re
from datetime import datetime, timezone
from sqlalchemy import and_, case, delete, or_, select
from .db import Chunk, Job, Record, Revision, Secret, bump
from .security import AppError, require, safe_id, validate_provider
from .model_catalog import DEFAULT_VISION, catalog_rows, selectable
from .chunking import STRATEGIES
from .config import storage_instance
from .power_quality.guards import require_generic_mutable

CAPABILITIES = ("llm", "embedding", "rerank", "ocr", "vision")
DEFAULT_PARSING = {"strategy":"general","pdfParser":"auto","delimiter":"\\n","imageContext":1,"understandImages":True,"tableHtml":True,"autoMetadata":False,"metadataModel":""}
DEFAULT_RETRIEVAL = {"threshold":0.2,"vectorWeight":0.3,"rerankModel":"","fileIds":[],"metadataKey":"","metadataValue":""}
DEFAULT_GENERATION = {"temperature":None,"topP":None,"maxTokens":None,"presencePenalty":None,"frequencyPenalty":None}
MODELS = [
    ("llm","语言模型","qwen-plus"),("embedding","向量模型","text-embedding-v4"),
    ("rerank","重排模型","qwen3-rerank"),("ocr","文字识别","qwen-vl-ocr"),("vision","视觉理解",DEFAULT_VISION)]
COLLECTIONS = {"knowledgeBases","documents","assistants","conversations","models","catalog","provider","logs"}

def now(): return datetime.now(timezone.utc).isoformat()

def record(session, collection, identifier):
    return session.get(Record, (collection, identifier))

def data_of(session, collection, identifier):
    row = record(session,collection,identifier)
    if not row: raise AppError("NOT_FOUND", "未找到指定资源。",404)
    return copy.deepcopy(row.data)

def put(session, collection, identifier, value, session_id=""):
    session.merge(Record(collection=collection,id=identifier,session_id=session_id,data=copy.deepcopy(value)))

def bootstrap(session):
    if not session.scalar(select(Record).where(Record.collection=="models").limit(1)):
        for cap,label,model in MODELS:
            put(session,"models",cap,{"capability":cap,"label":label,"description":"真实通义 API 连接；未测试前不标记可用。","model":model,"status":"unconfigured"})
            put(session,"catalog",f"default-{cap}",{"id":f"default-{cap}","capability":cap,"model":model,"enabled":True})
        put(session,"provider","tongyi",{"name":"通义千问","region":"cn-beijing","endpoint":"https://dashscope.aliyuncs.com"})
        session.commit()
    migrated=False
    existing={(r.data["capability"],r.data["model"]) for r in session.scalars(select(Record).where(Record.collection=="catalog"))}
    for item in catalog_rows():
        if (item["capability"],item["model"]) not in existing:
            put(session,"catalog",item["id"],item)
            existing.add((item["capability"],item["model"]))
            migrated=True
    vision=record(session,"models","vision")
    # One-time repair of the original failing seed. Explicit later selections,
    # working models, business bindings and provider credentials are preserved.
    if vision and vision.data.get("model")=="qwen-vl-plus" and vision.data.get("status") in {"failed","unconfigured"} and not vision.data.get("visionUpgrade20261026") and catalog_enabled(session,"vision",DEFAULT_VISION):
        vision.data={**vision.data,"model":DEFAULT_VISION,"status":"unconfigured","lastTest":None,"lastError":None,
                     "visionUpgrade20261026":True,"description":"已升级为新版视觉模型，请测试连接后使用。"}
        migrated=True
    for row in session.scalars(select(Record).where(Record.collection=="knowledgeBases")):
        parsing=row.data.get("parsing") or copy.deepcopy(DEFAULT_PARSING)
        if "autoMetadata" not in (row.data.get("parsing") or {}):
            row.data={**row.data,"parsing":{**parsing,"autoMetadata":False,"metadataModel":parsing.get("metadataModel","")}}
            migrated=True
    for row in session.scalars(select(Record).where(Record.collection=="documents")):
        if "manualMetadata" not in row.data:
            row.data={**row.data,"manualMetadata":copy.deepcopy(row.data.get("metadata",{}))}
            migrated=True
        if "enabledChunks" not in row.data:
            row.data={**row.data,"enabledChunks":sum(bool(s.get("enabled")) for s in row.data.get("segments",[]))}
            migrated=True
    if migrated:bump(session);session.commit()

def visible_records(role, session_id):
    chats=and_(Record.collection=="conversations",Record.session_id==session_id)
    log_access=True if role=="admin" else or_(Record.data["kind"].astext=="ingestion",and_(Record.session_id==session_id,or_(Record.data["kind"].astext=="chat",and_(Record.data["kind"].astext=="model",Record.data["purpose"].astext=="evaluation_judge")))) if role=="manager" else and_(Record.session_id==session_id,Record.data["kind"].astext=="chat")
    return or_(Record.collection.notin_(["conversations","logs","evaluationCases","platformApps","platformVersions","evaluationRuns","evaluationJudgeBatches","platformSkills","platformRemoteTools","platformMcpTools","platformLogPolicy","platformAuth","platformUsers","platformSessions","platformBudget","platformPricing","platformAlerts","platformBills","platformAudit","deliverySuites","deliveryChannels","deliveryChannelRuns","deliveryProviders"]),chats,and_(Record.collection=="logs",log_access))


def document_summary():
    # A generated stored column keeps summaries atomic with the original data
    # without repeatedly decompressing every document's complete chunk JSON.
    return Record.summary


def public_document(value):
    value=copy.deepcopy(value)
    for private in ("filePath","sha256","_buildToken"):value.pop(private,None)
    return value


def snapshot(session, role, session_id, compact=False):
    require(role,"user")
    state = {"version":1,"role":role,**{key:[] for key in COLLECTIONS if key!="provider"}}
    # Share-lock the revision first: the state payload and its CAS revision are
    # read from one stable publication boundary, including background writes.
    revision=session.scalar(select(Revision).where(Revision.id==1).with_for_update(read=True).execution_options(populate_existing=True)).value
    if compact:
        projection=case((Record.collection=="documents",document_summary()),else_=Record.data).label("data")
        rows=session.execute(select(Record.collection,Record.id,Record.session_id,projection).where(visible_records(role,session_id))).all()
    else:rows = session.scalars(select(Record).where(visible_records(role,session_id)).execution_options(populate_existing=True)).all()
    for row in rows:
        if row.collection == "provider":
            state["provider"] = {**row.data,"configured":session.get(Secret,"tongyi") is not None,"secretSaved":session.get(Secret,"tongyi") is not None}
        elif row.collection == "conversations":
            if row.session_id == session_id: state["conversations"].append(copy.deepcopy(row.data))
        elif row.collection == "logs":
            if role == "admin" or (role == "manager" and (row.data["kind"] == "ingestion" or row.session_id==session_id and (row.data["kind"] == "chat" or row.data["kind"] == "model" and row.data.get("purpose") == "evaluation_judge"))) or (role == "user" and row.session_id == session_id and row.data["kind"] == "chat"):
                state["logs"].append(copy.deepcopy(row.data))
        elif row.collection in state:
            value = copy.deepcopy(row.data)
            if row.collection == "documents":
                value=public_document(value)
            if row.collection == "assistants" and role == "user" and not value.get("enabled",True): continue
            state[row.collection].append(value)
    for key in ("documents","conversations","logs"):
        state[key].sort(key=lambda item:item.get("createdAt",item.get("updatedAt","")),reverse=True)
    order = {cap:i for i,cap in enumerate(CAPABILITIES)}
    state["models"].sort(key=lambda item:order[item["capability"]])
    return {"state":state,"revision":revision,"instance":storage_instance()}

def text_field(value, key, limit=10000, required=False):
    result = value.get(key,"")
    if not isinstance(result,str) or len(result)>limit or required and not result.strip(): raise AppError("INVALID_FIELD", f"{key} 内容无效。")
    return result.strip() if required else result

def integer(value,key,low,high,default):
    x=value.get(key,default)
    if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or int(x)!=x or x<low or x>high: raise AppError("INVALID_FIELD",f"{key} 超出允许范围。")
    return int(x)

def number(value,key,low,high,default):
    x=value.get(key,default)
    if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) or x<low or x>high: raise AppError("INVALID_FIELD",f"{key} 超出允许范围。")
    return float(x)

def parsing_config(value):
    if not isinstance(value,dict):raise AppError("INVALID_PARSER","解析规则必须为对象。")
    value={**DEFAULT_PARSING,**(value or {})}
    if value["strategy"] not in STRATEGIES or value["pdfParser"] not in {"auto","text","cloud"}: raise AppError("INVALID_PARSER","切分方法或 PDF 解析方式无效。")
    if value["strategy"]=="delimiter" and not value.get("delimiter",""): raise AppError("EMPTY_DELIMITER","自定义切分需要填写分隔符。")
    return {"strategy":value["strategy"],"pdfParser":value["pdfParser"],"delimiter":text_field(value,"delimiter",50),"imageContext":integer(value,"imageContext",0,8,1),"understandImages":bool(value["understandImages"]),"tableHtml":bool(value["tableHtml"]),"autoMetadata":bool(value.get("autoMetadata",False)),"metadataFallback":bool(value.get("metadataFallback",False)),"metadataModel":text_field(value,"metadataModel",160)}

def retrieval_config(value):
    if value is not None and not isinstance(value,dict):raise AppError("INVALID_FILTER","检索规则必须为对象。")
    value={**DEFAULT_RETRIEVAL,**(value or {})}
    file_ids=value.get("fileIds",[])
    if not isinstance(file_ids,list) or len(file_ids)>1000: raise AppError("INVALID_FILTER","文件筛选无效。")
    return {"threshold":number(value,"threshold",0,1,.2),"vectorWeight":number(value,"vectorWeight",0,1,.3),"rerankModel":text_field(value,"rerankModel",160),"fileIds":[safe_id(i) for i in file_ids],"metadataKey":text_field(value,"metadataKey",100),"metadataValue":text_field(value,"metadataValue",500)}

def generation_config(value):
    if value is not None and not isinstance(value,dict):raise AppError("INVALID_FIELD","生成参数必须为对象。")
    value=value or {}
    result={}
    for key,low,high in (("temperature",0,2),("topP",.01,1),("maxTokens",64,8192),("presencePenalty",-2,2),("frequencyPenalty",-2,2)):
        result[key]=None if value.get(key) is None else number(value,key,low,high,None)
    if result["maxTokens"] is not None: result["maxTokens"]=int(result["maxTokens"])
    return result

def catalog_enabled(session,cap,model):
    return selectable(model) and any(r.data.get("capability")==cap and r.data.get("model")==model and r.data.get("enabled") for r in session.scalars(select(Record).where(Record.collection=="catalog")))

def validate_kb(session,value,old):
    size=integer(value,"chunkSize",100,8000,600); overlap=integer(value,"overlap",0,7999,60)
    if overlap>=size: raise AppError("INVALID_OVERLAP","重叠长度必须小于分块长度。")
    model=text_field(value,"embedding",160,True)
    if not catalog_enabled(session,"embedding",model): raise AppError("MODEL_DISABLED","请选择已启用的向量模型。")
    if old and model != old.get("embedding") and session.scalar(select(Chunk.id).where(Chunk.kb_id==old["id"]).limit(1)):
        raise AppError("INDEX_MODEL_LOCKED","已有索引的知识库不能直接切换向量模型，请新建知识库后重建。",409)
    raw_parsing=value.get("parsing")
    if raw_parsing is not None and not isinstance(raw_parsing,dict):raise AppError("INVALID_PARSER","解析规则必须为对象。")
    raw_parsing=copy.deepcopy(raw_parsing or {})
    if not old:raw_parsing.setdefault("autoMetadata",True)
    config=parsing_config(raw_parsing)
    if config["metadataModel"] and not catalog_enabled(session,"llm",config["metadataModel"]):raise AppError("MODEL_DISABLED","请选择已启用的元数据提取语言模型。")
    version=(old or {}).get("parsingVersion",1)
    if old and (config!=old.get("parsing") or size!=old.get("chunkSize") or overlap!=old.get("overlap")): version+=1
    return {"id":safe_id(value["id"]),"name":text_field(value,"name",100,True),"description":text_field(value,"description",2000),"color":text_field(value,"color",40),"icon":text_field(value,"icon",80),"embedding":model,"chunkSize":size,"overlap":overlap,"updatedAt":now(),"parsing":config,"parsingVersion":version}

def validate_assistant(session,value,old,allow_empty=False):
    kb_ids=value.get("kbIds",[])
    if not isinstance(kb_ids,list) or (not kb_ids and not allow_empty): raise AppError("NO_KB","请至少关联一个知识库。")
    models={data_of(session,"knowledgeBases",safe_id(k))["embedding"] for k in kb_ids}
    if len(models)>1: raise AppError("INCOMPATIBLE_EMBEDDING","同一助手关联的知识库必须使用相同向量模型。")
    model=text_field(value,"model",160,True)
    if not catalog_enabled(session,"llm",model): raise AppError("MODEL_DISABLED","请选择已启用的语言模型。")
    top=integer(value,"topK",1,30,5);candidate=integer(value,"candidateK",1,100,30)
    if top>candidate: raise AppError("INVALID_TOPK","引用片段数不能大于候选片段数。")
    retrieval=retrieval_config(value.get("retrieval"))
    if value.get("useRerank") and not catalog_enabled(session,"rerank",retrieval["rerankModel"]): raise AppError("NO_RERANK_MODEL","请选择已启用的重排模型。")
    return {"id":safe_id(value["id"]),"name":text_field(value,"name",100,True),"description":text_field(value,"description",2000),"kbIds":kb_ids,"enabled":bool(value.get("enabled",True)),"model":model,"greeting":text_field(value,"greeting",2000),"prompt":text_field(value,"prompt",20000),"topK":top,"candidateK":candidate,"useRerank":bool(value.get("useRerank",False)),"configVersion":(old or {}).get("configVersion",0)+1,"retrievalVersion":integer(value,"retrievalVersion",1,9999999,1),"color":text_field(value,"color",40),"retrieval":retrieval,"generation":generation_config(value.get("generation")),"noEvidenceReply":text_field(value,"noEvidenceReply",3000),"showQuote":bool(value.get("showQuote",True)),"multiTurn":bool(value.get("multiTurn",True)),"keywordAnalysis":bool(value.get("keywordAnalysis",False))}

def log_record(identifier,kind,query,name,model,config_version=1,kb_id=None):
    return {"id":identifier,"kind":kind,"query":query,"assistantName":name,"model":model,"status":"running","inputTokens":None,"outputTokens":None,"tokens":None,"duration":0,"createdAt":now(),"stages":[],"citations":[],"configVersion":config_version,**({"knowledgeBaseId":kb_id} if kb_id else {})}

def check_kb_jobs(session,kb_id):
    if any(job.payload.get("kb",{}).get("id")==kb_id for job in session.scalars(select(Job).where(Job.status.in_(["queued","running"])))):
        raise AppError("BUSY","知识库有构建或元数据任务正在处理，请完成或取消后再修改设置。",409)

def check_job(session,entity_id):
    if session.scalar(select(Job.id).where(Job.entity_id==entity_id,Job.status.in_(["queued","running"])).limit(1)):
        raise AppError("BUSY","资源正在处理，请完成或取消任务后重试。",409)

async def patch_state(session,body,role,session_id,compact=False):
    changes=body.get("changes",{})
    if not isinstance(changes,dict) or set(changes)-COLLECTIONS or "logs" in changes: raise AppError("INVALID_PATCH","仅允许修改指定业务资源，日志由服务端生成。")
    rev=session.get(Revision,1,with_for_update=True)
    if body.get("baseRevision")!=rev.value: raise AppError("REVISION_CONFLICT","数据已更新，请刷新后重试。",409)
    # Resolve dependencies in a stable order; one transaction publishes every change.
    for collection in ("provider","catalog","models","knowledgeBases","documents","assistants","conversations"):
        if collection not in changes: continue
        require(role,"admin" if collection in {"provider","catalog","models"} else "user" if collection=="conversations" else "manager")
        change=changes[collection]
        if not isinstance(change,dict) or not isinstance(change.get("upsert",[]),list) or not isinstance(change.get("delete",[]),list): raise AppError("INVALID_PATCH","修改格式不正确。")
        if len(change.get("upsert",[]))+len(change.get("delete",[]))>1000: raise AppError("PATCH_TOO_LARGE","单次修改资源过多。")
        for value in change.get("upsert",[]):
            if not isinstance(value,dict):raise AppError("INVALID_PATCH","待保存的资源必须为对象。")
            identifier="tongyi" if collection=="provider" else value.get("capability") if collection=="models" else safe_id(value.get("id"))
            old_row=record(session,collection,identifier);old=copy.deepcopy(old_row.data) if old_row else None
            if collection=="provider":
                updated=validate_provider(value)
                if old!=updated:
                    for row in session.scalars(select(Record).where(Record.collection=="models")):
                        row.data={**row.data,"status":"unconfigured","lastTest":None}
            elif collection=="catalog":
                cap=value.get("capability"); model=text_field(value,"model",160,True)
                if cap not in CAPABILITIES: raise AppError("INVALID_CAPABILITY","模型能力无效。")
                if not re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}",model): raise AppError("INVALID_MODEL","模型 ID 无效。")
                duplicate=any(r.id!=identifier and r.data["capability"]==cap and r.data["model"]==model for r in session.scalars(select(Record).where(Record.collection=="catalog")))
                if duplicate: raise AppError("DUPLICATE_MODEL","该能力下已存在此模型。",409)
                if value.get("enabled",True) and not selectable(model): raise AppError("MODEL_PROTOCOL_UNSUPPORTED","该模型需要专用接口，暂不能用于当前知识库链路。")
                if old and not value.get("enabled",True) and model_in_use(session,cap,model): raise AppError("MODEL_IN_USE","正在使用的模型不能停用。",409)
                updated={"id":identifier,"model":model,"capability":cap,"enabled":bool(value.get("enabled",True))}
            elif collection=="models":
                if identifier not in CAPABILITIES: raise AppError("INVALID_CAPABILITY","模型能力无效。")
                model=text_field(value,"model",160,True)
                if not catalog_enabled(session,identifier,model): raise AppError("MODEL_DISABLED","请选择已启用模型。")
                updated={**(old or {}),"capability":identifier,"model":model,"status":old.get("status","unconfigured") if old and old.get("model")==model else "unconfigured"}
                if not old or old.get("model")!=model:
                    updated.pop("lastTest",None)
                    updated.pop("lastError",None)
            elif collection=="knowledgeBases":
                require_generic_mutable(old)
                if old: check_kb_jobs(session,identifier)
                updated=validate_kb(session,value,old)
            elif collection=="documents":
                if not old: raise AppError("UPLOAD_REQUIRED","请通过上传接口导入真实文件。")
                require_generic_mutable(old)
                check_job(session,identifier)
                updated=copy.deepcopy(old)
                for key in ("enabled","parsing","chunkSizeOverride","overlapOverride"):
                    if key in value: updated[key]=copy.deepcopy(value[key])
                updated["enabled"]=bool(updated["enabled"])
                from .services import effective_metadata
                manual=copy.deepcopy(old.get("manualMetadata",old.get("metadata",{})))
                if "manualMetadata" in value:
                    manual=copy.deepcopy(value["manualMetadata"])
                elif "metadata" in value and value["metadata"]!=old.get("metadata",{}):
                    requested=value["metadata"]
                    if not isinstance(requested,dict):raise AppError("INVALID_METADATA","元数据必须为文本字段映射。")
                    for key in set(requested)|set(old.get("metadata",{})):
                        if requested.get(key,"")!=old.get("metadata",{}).get(key,""):manual[key]=requested.get(key,"")
                if not isinstance(manual,dict) or len(manual)>50 or any(not isinstance(k,str) or not isinstance(v,str) or len(k)>100 or len(v)>10000 for k,v in manual.items()): raise AppError("INVALID_METADATA","手动元数据最多 50 个文本字段。")
                updated["manualMetadata"]=manual
                updated["metadata"]=effective_metadata(old.get("metadataExtraction"),manual)
                if updated.get("parsing") is not None: updated["parsing"]=parsing_config(updated["parsing"])
                size=updated.get("chunkSizeOverride") or data_of(session,"knowledgeBases",old["kbId"])["chunkSize"]
                overlap=updated.get("overlapOverride")
                if not isinstance(size,int) or size<100 or size>8000 or overlap is not None and (not isinstance(overlap,int) or overlap<0 or overlap>=size): raise AppError("INVALID_OVERLAP","分块长度或重叠长度无效。")
                if any(updated.get(k)!=old.get(k) for k in ("parsing","chunkSizeOverride","overlapOverride")): updated["indexParsingVersion"]=0
                supplied_segments=value.get("segments",old.get("segments",[])) if not value.get("detailsOmitted") else old.get("segments",[])
                if supplied_segments!=old.get("segments",[]) or updated["metadata"]!=old.get("metadata",{}):
                    from .services import update_segments
                    updated=await update_segments(session,updated,supplied_segments)
                updated["updatedAt"]=now()
            elif collection=="assistants": updated=validate_assistant(session,value,old)
            else:
                if old_row and old_row.session_id!=session_id: raise AppError("FORBIDDEN","无法访问其他浏览器会话。",403)
                if old:
                    updated={**old,"title":text_field(value,"title",200,True),"updatedAt":now()}
                else:
                    assistant=data_of(session,"assistants",safe_id(value.get("assistantId")))
                    if not assistant.get("enabled"): raise AppError("ASSISTANT_DISABLED","此助手已停用。",409)
                    updated={"id":identifier,"assistantId":assistant["id"],"title":text_field(value,"title",200,True),"messages":[],"updatedAt":now(),"configVersion":assistant["configVersion"],"kbIds":assistant["kbIds"],"assistantSnapshot":assistant}
            put(session,collection,identifier,updated,session_id if collection=="conversations" else "")
            session.flush()
        for identifier in change.get("delete",[]):
            identifier=safe_id(identifier)
            row=record(session,collection,identifier)
            if not row: continue
            if collection in {"knowledgeBases","documents"}:require_generic_mutable(row.data)
            if collection in {"provider","models"}: raise AppError("DELETE_FORBIDDEN","默认配置不可删除。")
            if collection=="conversations":
                if row.session_id!=session_id: raise AppError("FORBIDDEN","无法访问其他浏览器会话。",403)
                cancel_jobs(session,identifier)
            elif collection=="catalog":
                if model_in_use(session,row.data["capability"],row.data["model"]): raise AppError("MODEL_IN_USE","正在使用的模型不可删除。",409)
            elif collection=="knowledgeBases":
                for doc in list(session.scalars(select(Record).where(Record.collection=="documents"))):
                    if doc.data["kbId"]==identifier:
                        require_generic_mutable(doc.data)
                        cancel_jobs(session,doc.id);session.execute(delete(Chunk).where(Chunk.doc_id==doc.id));session.delete(doc)
                for assistant in session.scalars(select(Record).where(Record.collection=="assistants")):
                    if identifier in assistant.data["kbIds"]: assistant.data={**assistant.data,"kbIds":[k for k in assistant.data["kbIds"] if k!=identifier],"enabled":False}
            elif collection=="documents":
                cancel_jobs(session,identifier);session.execute(delete(Chunk).where(Chunk.doc_id==identifier))
            session.delete(row)
    rev.value+=1
    session.commit()
    return snapshot(session,role,session_id,compact=compact)

def model_in_use(session,cap,model):
    for row in session.scalars(select(Record).where(Record.collection.in_(["models","knowledgeBases","assistants","documents"]))):
        if row.collection=="models" and row.data.get("capability")==cap and row.data.get("model")==model:return True
        if cap=="embedding" and row.collection=="knowledgeBases" and row.data.get("embedding")==model:return True
        if cap=="llm" and row.collection in {"knowledgeBases","documents"} and (row.data.get("parsing") or {}).get("metadataModel")==model:return True
        if row.collection=="assistants" and (cap=="llm" and row.data.get("model")==model or cap=="rerank" and row.data.get("retrieval",{}).get("rerankModel")==model):return True
    return False

def cancel_jobs(session,entity_id):
    for job in session.scalars(select(Job).where(Job.entity_id==entity_id,Job.status.in_(["queued","running"]))):
        job.status="canceled";job.lease_until=None
        log=record(session,"logs",job.id)
        if log:
            stages=copy.deepcopy(log.data.get("stages",[]))
            for stage in stages:
                if stage.get("status")=="running":stage["status"]="canceled"
            events=copy.deepcopy(log.data.get("traceEvents",[]))
            for event in events:
                if event.get("status")=="running":event["status"]="canceled"
            log.data={**log.data,"status":"canceled","stages":stages,"traceEvents":events,"error":"用户取消；已发生的 API 用量仍会记录。"}
