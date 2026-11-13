import asyncio
import contextlib
import copy
import hashlib
import os
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from fastapi import Depends, FastAPI, File, Form, Header, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from sqlalchemy import Text, func, select, text
from .config import settings, storage_instance
from .db import Job, Record, SessionLocal, bump, initialize, revision_lock
from .security import AppError, require, safe_id, store_secret
from .services import new_provider, result_usage, result_usages, retrieve, sum_usage, uid
from .state import bootstrap, cancel_jobs, data_of, document_summary, integer, log_record, now, parsing_config, patch_state, public_document, put, record, snapshot, text_field, visible_records
from .worker import create_build, create_chat, normalize_usage, public_error, worker_loop
from .providers import ProviderError
from .parsing import ParsingError
from .model_catalog import MODEL_INFO
from .chunking import split_units
from .maintenance import MaintenanceMiddleware, mutation_guard
from .platform_audit import write_audit
from . import log_policy
from . import local_auth
from . import tool_credentials
from . import insights
from .power_quality.guards import require_generic_mutable

@asynccontextmanager
async def lifespan(app):
    with mutation_guard():
        initialize()
        with SessionLocal() as session:bootstrap(session)
    stop=asyncio.Event();task=asyncio.create_task(worker_loop(stop)) if settings.worker_in_api else None
    yield
    stop.set()
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):await task

app=FastAPI(title="知序企业知识库 API",version="0.8.0",lifespan=lifespan)
app.add_middleware(MaintenanceMiddleware)
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origins,allow_methods=["GET","POST","PATCH","PUT","DELETE"],allow_headers=["Content-Type","X-Role","X-Session-ID","X-State-View","X-State-Instance"],expose_headers=["Content-Disposition"])

@app.exception_handler(AppError)
async def app_error(request,error):return JSONResponse({"error":{"code":error.code,"message":error.message,"runId":getattr(error,"run_id",None)}},status_code=error.status)

@app.exception_handler(ProviderError)
async def provider_error(request,error):return JSONResponse({"error":{"code":error.code,"message":error.message,"runId":getattr(error,"run_id",None)}},status_code=502)

@app.exception_handler(ParsingError)
async def parsing_error(request,error):return JSONResponse({"error":{"code":error.code,"message":error.message}},status_code=400)

@app.exception_handler(Exception)
async def unhandled_error(request,error):
    # Provider errors are deliberately sanitized by the provider adapter.
    return JSONResponse({"error":{"code":getattr(error,"code","INTERNAL_ERROR"),"message":public_error(error)}},status_code=502 if hasattr(error,"code") else 500)

@app.exception_handler(RequestValidationError)
async def invalid_request(request,error):return JSONResponse({"error":{"code":"INVALID_REQUEST","message":"请求字段无效，请检查输入。"}},status_code=422)

class AccountTransitionLock:
    """Drain in-flight API requests while account mode is enabled."""
    def __init__(self):self.condition=asyncio.Condition();self.readers=0;self.writer=False;self.waiting=0
    @asynccontextmanager
    async def hold(self,exclusive=False):
        async with self.condition:
            if exclusive:
                self.waiting+=1
                try:
                    await self.condition.wait_for(lambda:not self.writer and self.readers==0)
                    self.writer=True
                finally:self.waiting-=1
            else:
                await self.condition.wait_for(lambda:not self.writer and self.waiting==0)
                self.readers+=1
        try:yield
        finally:
            async with self.condition:
                if exclusive:self.writer=False
                else:self.readers-=1
                self.condition.notify_all()

account_transition_lock=AccountTransitionLock()

@app.middleware("http")
async def local_account_guard(request:Request,call_next):
    if settings.power_quality_local_mode:return await call_next(request)
    if not request.url.path.startswith('/api/') or request.url.path=='/api/health' or request.url.path.startswith('/api/channel/'):return await call_next(request)
    async with account_transition_lock.hold(exclusive=request.url.path=='/api/auth/setup'):
        with SessionLocal() as session:
            config=record(session,"platformAuth","local");enabled=bool(config and config.data.get("enabled"))
            if enabled:
                try:local_auth.check_origin(request)
                except AppError as e:return JSONResponse({"error":{"code":e.code,"message":e.message}},status_code=e.status)
                request.state.identity=local_auth.active_identity(session,request.cookies.get(local_auth.COOKIE,''))
                if not request.state.identity and request.url.path not in {'/api/auth/status','/api/auth/login'}:
                    return JSONResponse({"error":{"code":"AUTH_REQUIRED","message":"请先登录。"}},status_code=401)
        return await call_next(request)

def context(request:Request,x_role:Annotated[str,Header()]="user",x_session_id:Annotated[str,Header()]=""):
    identity=getattr(request.state,"identity",None)
    if identity:return identity["role"],identity["id"]
    require(x_role,"user")
    if not x_session_id or len(x_session_id)>150 or any(not(c.isalnum() or c in "-_.:") for c in x_session_id):raise AppError("SESSION_REQUIRED","缺少浏览器会话标识。",400)
    return x_role,x_session_id

def state_view(x_state_view:Annotated[str,Header()]="full"):
    if x_state_view not in {"full","compact"}:raise AppError("INVALID_STATE_VIEW","状态视图必须为 full 或 compact。")
    return x_state_view=="compact"

@app.get("/api/health")
def health():
    with SessionLocal() as session:
        session.execute(text("SELECT 1"));config=record(session,"platformAuth","local");authentication="local-accounts" if config and config.data.get("enabled") else "demo-role-unverified"
    return {"status":"ok","database":"postgresql+pgvector","worker":"embedded" if settings.worker_in_api else "external","authentication":authentication, "backupCoordination":"file-lock-v1", "storageInstance":hashlib.sha256(str(settings.data_dir).encode()).hexdigest()[:16],"processId":os.getpid()}

@app.get("/api/runtime")
def runtime_info(ctx=Depends(context)):
    require(ctx[0],"admin")
    if not settings.runtime_home:return {"managed":False}
    try:
        value=json.loads((settings.runtime_home/"status.json").read_text())
    except (OSError,ValueError):value={"status":"starting"}
    for name in ("backup-state.json","recovery-state.json"):
        try:value.update(json.loads((settings.runtime_home/name).read_text()))
        except (OSError,ValueError):pass
    return {"managed":True,**value}

@app.post("/api/runtime/backup")
def request_runtime_backup(ctx=Depends(context)):
    require(ctx[0],"admin")
    if not settings.runtime_home:raise AppError("RUNTIME_UNMANAGED","请使用本机启动入口启用备份管理。",409)
    requests=settings.runtime_home/"requests";requests.mkdir(exist_ok=True,mode=0o700)
    path=requests/"backup.request"
    path.write_text(now());path.chmod(0o600)
    return {"status":"requested"}

@app.get("/api/state")
def get_state(sinceRevision:int|None=None,instance:str|None=None,ctx=Depends(context),compact=Depends(state_view)):
    with SessionLocal() as session:
        if sinceRevision is not None:
            from .db import Revision
            revision=session.scalar(select(Revision).where(Revision.id==1).with_for_update(read=True)).value
            if sinceRevision==revision and (instance is None or instance==storage_instance()):return {"unchanged":True,"revision":revision,"role":ctx[0]}
        return snapshot(session,*ctx,compact=compact)

@app.post("/api/chunking/preview")
async def preview_chunking(body:dict,ctx=Depends(context)):
    require(ctx[0],"manager")
    source=text_field(body,"text",20000,True)
    raw_parsing=body.get("parsing")
    parsing=parsing_config({} if raw_parsing is None else raw_parsing)
    size=integer(body,"chunkSize",100,8000,600)
    overlap=integer(body,"overlap",0,7999,60)
    embedding=text_field(body,"embedding",160) or "text-embedding-v4"
    if parsing["strategy"]=="page":
        import re
        pages=re.split(r"\f|(?m:^\s*===PAGE===\s*$)",source.replace(r"\f","\f"))
        units=[{"text":value,"location":f"第 {i+1} 页（预览）","kind":"text","provenance":"preview_text"} for i,value in enumerate(pages)]
    else:units=[{"text":source,"location":"粘贴文本（预览）","kind":"text","provenance":"preview_text"}]
    chunks=await asyncio.to_thread(split_units,units,{}, {"parsing":parsing,"chunkSize":size,"overlap":overlap,"embedding":embedding})
    return {"count":len(chunks),"chunks":chunks[:30],"warnings":list(dict.fromkeys(c["splitNote"] for c in chunks if c.get("splitNote"))),"truncated":len(chunks)>30,"modelCalls":0}

@app.patch("/api/state")
async def update_state(body:dict,ctx=Depends(context),compact=Depends(state_view)):
    with SessionLocal() as session:
        try:return await patch_state(session,body,*ctx,compact=compact)
        except Exception as error:
            session.rollback()
            audit=getattr(error,"audit",None)
            if audit:
                identifier=uid("chunk-edit")
                usages=[*getattr(error,"prior_usage",[]),*[normalize_usage(v) for v in getattr(error,"usage",[])]]
                log=log_record(identifier,"ingestion",audit["query"],audit["name"],audit["model"],kb_id=audit["kbId"])
                log.update({"status":"failed","error":public_error(error),"usage":usages,**sum_usage(usages)})
                put(session,"logs",identifier,log,ctx[1]);bump(session);session.commit();error.run_id=identifier
            raise

@app.get("/api/{collection}")
def list_records(collection:str,page:int=1,pageSize:int=20,kbId:str="",query:str="",kind:str="",status:str="",ctx=Depends(context)):
    allowed={"knowledgeBases","documents","assistants","conversations","logs","models","catalog"}
    if collection not in allowed:raise AppError("NOT_FOUND","接口不存在。",404)
    if page<1 or pageSize<1 or pageSize>100:raise AppError("INVALID_PAGE","分页大小范围为 1–100。")
    with SessionLocal() as session:
        search_value=Record.summary if collection=="documents" else Record.data
        filters=[Record.collection==collection,visible_records(*ctx)]
        if kbId:filters.append(search_value["kbId"].astext==safe_id(kbId))
        if kind:filters.append(search_value["kind"].astext==kind)
        if status:filters.append(search_value["status"].astext==status)
        if query:
            if len(query)>500:raise AppError("INVALID_QUERY","搜索内容最多 500 字符。")
            filters.append(search_value.cast(Text).ilike("%"+query.replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%",escape="\\"))
        if collection=="assistants" and ctx[0]=="user":filters.append(Record.data["enabled"].astext=="true")
        total=session.scalar(select(func.count()).select_from(Record).where(*filters))
        value=document_summary() if collection=="documents" else Record.data
        order=func.coalesce(search_value["createdAt"].astext,search_value["updatedAt"].astext,"")
        items=session.scalars(select(value).where(*filters).order_by(order.desc(),Record.id).offset((page-1)*pageSize).limit(pageSize)).all()
        return {"items":items,"total":total,"page":page,"pageSize":pageSize}

@app.get("/api/insights/summary")
def operational_insights(days:int=7,ctx=Depends(context)):
    with SessionLocal() as session:return insights.summary(session,ctx,days)

@app.put("/api/insights/feedback/{run_id}")
def operational_feedback(run_id:str,body:dict,ctx=Depends(context)):
    if set(body)!={"value"}:raise AppError("INVALID_FEEDBACK","请提供一个反馈选项。")
    with SessionLocal.begin() as session:
        revision_lock(session)
        feedback=insights.save_feedback(session,ctx,safe_id(run_id),body["value"])
        bump(session)
    return {"feedback":feedback}

@app.post("/api/provider/secret")
def save_key(body:dict,ctx=Depends(context)):
    role,session_id=ctx;require(role,"admin")
    with SessionLocal.begin() as session:
        revision_lock(session)
        store_secret(session,body.get("apiKey"))
        for row in session.scalars(select(Record).where(Record.collection=="models")):
            row.data={**row.data,"status":"unconfigured","lastTest":None}
        bump(session)
    return {"saved":True}

@app.post("/api/models/{capability}/test")
async def test_model(capability:str,ctx=Depends(context),compact=Depends(state_view)):
    role,session_id=ctx;require(role,"admin")
    if capability not in {"llm","embedding","rerank","ocr","vision"}:raise AppError("INVALID_CAPABILITY","模型能力无效。")
    started=time.monotonic()
    with SessionLocal() as session:
        revision_lock(session)
        model=data_of(session,"models",capability)
        identifier=uid("model-test");log=log_record(identifier,"model",f"{model['label']}连接测试","模型连接",model["model"])
        put(session,"logs",identifier,log,session_id);bump(session);session.commit()
    provider=None
    try:
        with SessionLocal() as session:provider=new_provider(session)
        if capability=="llm":
            max_tokens=8192 if MODEL_INFO.get(model["model"],{}).get("thinking")=="always" else 64
            result=await provider.chat([{"role":"user","content":"请只回复：连接成功"}],model["model"],{"maxTokens":max_tokens})
        elif capability=="embedding":result=await provider.embed(["企业知识库连接测试"],model["model"])
        elif capability=="rerank":result=await provider.rerank("如何报销",["员工提交发票后申请报销。","今天是星期一。"],model["model"])
        else:
            from io import BytesIO
            from PIL import Image,ImageDraw
            image=Image.new("RGB",(360,120),"white");ImageDraw.Draw(image).text((20,40),"KNOWLEDGE 123",fill="black")
            buffer=BytesIO();image.save(buffer,format="PNG")
            result=await provider.recognize(buffer.getvalue(),"image/png",model["model"],vision=capability=="vision")
        status="ready";error=None;usage=result_usage(result);usages=result_usages(result)
    except Exception as error_obj:
        status="failed";error=public_error(error_obj)
        usages=[normalize_usage(value) for value in getattr(error_obj,"usage",[])]
        usage=sum_usage(usages) if usages else {"inputTokens":None,"outputTokens":None,"tokens":None}
    finally:
        if provider:await provider.aclose()
    with SessionLocal.begin() as session:
        revision_lock(session)
        current=record(session,"models",capability)
        if current.data["model"]==model["model"]:current.data={**current.data,"status":status,"lastTest":now(),"lastError":error}
        row=record(session,"logs",identifier);row.data={**row.data,"status":"completed" if status=="ready" else "failed","duration":round(time.monotonic()-started,3),"error":error,**usage,"usage":usages,"stages":[{"name":"通义真实 API 请求","ms":round((time.monotonic()-started)*1000),"status":"ok" if status=="ready" else "failed"}]}
        bump(session)
    with SessionLocal() as session:return snapshot(session,*ctx,compact=compact)

@app.post("/api/knowledge-bases/{kb_id}/feishu-preview")
async def preview_feishu_import(kb_id:str,body:dict,ctx=Depends(context)):
    require(ctx[0],"manager");safe_id(kb_id)
    with SessionLocal() as session:data_of(session,"knowledgeBases",kb_id)
    from .feishu_import import preview
    try:return await asyncio.wait_for(preview(body.get("url"),body.get("appId"),body.get("appSecret"),body.get("includeChildren",False)),timeout=90)
    except TimeoutError:raise AppError("FEISHU_TIMEOUT","飞书预览超时，请减少节点范围后重试。",504) from None

@app.post("/api/knowledge-bases/{kb_id}/web-preview")
async def preview_web_import(kb_id:str,body:dict,ctx=Depends(context)):
    require(ctx[0],"manager");safe_id(kb_id)
    with SessionLocal() as session:data_of(session,"knowledgeBases",kb_id)
    from .remote_tools import fetch_webpage
    from urllib.parse import urlsplit
    url=body.get("url");content=await fetch_webpage(url)
    return {"filename":(urlsplit(url).hostname or "webpage")[:120]+".md","content":content}

@app.post("/api/knowledge-bases/{kb_id}/documents")
async def upload(kb_id:str,files:Annotated[list[UploadFile],File()],autoBuild:Annotated[bool,Form()]=True,fileStrategies:Annotated[str,Form()]="{}",ctx=Depends(context),compact=Depends(state_view)):
    role,session_id=ctx;require(role,"manager");safe_id(kb_id)
    if len(files)>30:raise AppError("TOO_MANY_FILES","一次最多上传 30 份文件。")
    try:strategies=json.loads(fileStrategies)
    except ValueError:raise AppError("INVALID_CHUNK_METHOD","逐文件配置无效。") from None
    from .chunking import STRATEGIES
    if not isinstance(strategies,dict) or any(not isinstance(v,str) or v not in STRATEGIES for v in strategies.values()):raise AppError("INVALID_CHUNK_METHOD","逐文件配置无效。")
    added=skipped=0
    allowed={"doc","docx","pdf","md","txt","html","htm","png","jpg","jpeg","webp","xls","xlsx","csv"}
    written=[]
    with SessionLocal() as session:
        try:
            revision_lock(session)
            kb=data_of(session,"knowledgeBases",kb_id)
            require_generic_mutable(kb)
            for upload_file in files:
                filename=Path((upload_file.filename or "").replace("\\","/")).name
                suffix=Path(filename).suffix.lower()[1:]
                if suffix not in allowed or not filename or len(filename)>250:raise AppError("UNSUPPORTED_FORMAT","支持 DOC/DOCX/PDF/MD/TXT/HTML/PNG/JPG/WEBP/XLS/XLSX/CSV 文件。")
                identifier=uid("doc");path=settings.data_dir/"files"/f"{identifier}.{suffix}"
                digest=hashlib.sha256();size=0
                with path.open("wb") as stream:
                    written.append(path)
                    while chunk:=await upload_file.read(1024*1024):
                        size+=len(chunk)
                        if size>settings.max_upload_mb*1024*1024:raise AppError("FILE_TOO_LARGE",f"文件超过 {settings.max_upload_mb} MB。",413)
                        stream.write(chunk);digest.update(chunk)
                if size==0:raise AppError("EMPTY_FILE","不能上传空文件。")
                # Validation includes signature and archive bomb bounds before paid calls.
                from .parsing import _validate_file
                _validate_file(path,suffix)
                duplicate=any(row.data.get("kbId")==kb_id and row.data.get("sha256")==digest.hexdigest() for row in session.scalars(select(Record).where(Record.collection=="documents")))
                if duplicate:path.unlink();written.remove(path);skipped+=1;continue
                doc={"id":identifier,"kbId":kb_id,"name":filename,"format":suffix.upper(),"size":size,"status":"uploaded","enabled":True,"chunks":0,"enabledChunks":0,"updatedAt":now(),"category":"","content":"","location":"尚未解析","provenance":"原始上传文件","progress":0,"segments":[],"metadata":{},"manualMetadata":{},"metadataExtraction":{"status":"not_started" if kb.get("parsing",{}).get("autoMetadata",False) else "disabled","model":kb.get("parsing",{}).get("metadataModel","") or data_of(session,"models","llm")["model"],"version":0,"facts":[],"keywords":[],"questions":[],"coverage":{"totalUnits":0,"processedUnits":0}},"filePath":str(path),"sha256":digest.hexdigest()}
                if filename in strategies:doc["parsing"]={**kb.get("parsing",{}),"strategy":strategies[filename]}
                put(session,"documents",identifier,doc);session.flush();added+=1
                if autoBuild:
                    try:create_build(session,identifier,session_id)
                    except AppError as error:
                        doc.update({"error":error.message,"status":"uploaded"});put(session,"documents",identifier,doc)
            bump(session);session.commit()
        except Exception:
            session.rollback()
            for path in written:path.unlink(missing_ok=True)
            raise
        return {"added":added,"skipped":skipped,**snapshot(session,*ctx,compact=compact)}

@app.post("/api/documents/{doc_id}/build")
def build(doc_id:str,ctx=Depends(context),compact=Depends(state_view)):
    role,session_id=ctx;require(role,"manager")
    with SessionLocal.begin() as session:revision_lock(session);create_build(session,safe_id(doc_id),session_id);bump(session)
    with SessionLocal() as session:return snapshot(session,*ctx,compact=compact)

@app.post("/api/documents/{doc_id}/metadata/extract")
def extract_doc_metadata(doc_id:str,body:dict,ctx=Depends(context),compact=Depends(state_view)):
    role,session_id=ctx;require(role,"manager")
    preserve=body.get("preserveManual",True)
    if not isinstance(preserve,bool):raise AppError("INVALID_FIELD","preserveManual 必须为布尔值。")
    with SessionLocal.begin() as session:
        revision_lock(session)
        create_build(session,safe_id(doc_id),session_id,metadata_only=True,preserve_manual=preserve)
        bump(session)
    with SessionLocal() as session:return snapshot(session,*ctx,compact=compact)

@app.post("/api/documents/{doc_id}/cancel")
def cancel_doc(doc_id:str,ctx=Depends(context),compact=Depends(state_view)):
    require(ctx[0],"manager")
    with SessionLocal.begin() as session:
        revision_lock(session)
        doc=data_of(session,"documents",safe_id(doc_id))
        require_generic_mutable(doc)
        if doc.get("status")!="processing":raise AppError("NO_ACTIVE_JOB","当前文档没有正在运行的构建任务。",409)
        cancel_jobs(session,doc_id)
        doc.update({"status":"canceled","error":"任务已取消；已发布的旧索引继续可用。","updatedAt":now()})
        extraction=doc.get("metadataExtraction")
        if extraction and extraction.get("status")=="processing":doc["metadataExtraction"]={**extraction,"status":"failed","error":"本次提取已取消；上次成功结果与索引保留。"}
        put(session,"documents",doc_id,doc);bump(session)
    with SessionLocal() as session:return snapshot(session,*ctx,compact=compact)

@app.get("/api/documents/{doc_id}")
def document_detail(doc_id:str,ctx=Depends(context)):
    from .db import Revision
    with SessionLocal() as session:
        revision=session.scalar(select(Revision).where(Revision.id==1).with_for_update(read=True)).value
        return {"document":public_document(data_of(session,"documents",safe_id(doc_id))),"revision":revision,"instance":storage_instance()}

@app.get("/api/documents/{doc_id}/file")
def source_file(doc_id:str,ctx=Depends(context)):
    with SessionLocal() as session:doc=data_of(session,"documents",safe_id(doc_id))
    path=Path(doc["filePath"]).resolve()
    if not path.is_relative_to((settings.data_dir/"files").resolve()) or not path.is_file():raise AppError("FILE_MISSING","原文件不存在，请从备份恢复。",404)
    return FileResponse(path,filename=doc["name"],media_type="application/octet-stream")

@app.get("/api/documents/{doc_id}/preview")
def preview(doc_id:str,ctx=Depends(context)):
    with SessionLocal() as session:doc=data_of(session,"documents",safe_id(doc_id))
    return {"text":doc.get("content",""),"segments":doc.get("segments",[]),"provenance":doc.get("provenance","")}

@app.post("/api/retrieval")
async def debug_retrieval(body:dict,ctx=Depends(context)):
    role,session_id=ctx;require(role,"manager");identifier=uid("retrieval");started=time.monotonic()
    with SessionLocal.begin() as session:
        revision_lock(session)
        log=log_record(identifier,"chat",str(body.get("query",""))[:10000],"检索调试","通义向量检索")
        put(session,"logs",identifier,log,session_id);bump(session)
    try:
        with SessionLocal() as session:
            citations,usage,stages=await retrieve(session,body.get("query",""),body.get("kbIds",[]),body.get("topK",5),body.get("candidateK",30),bool(body.get("useRerank",False)),body.get("retrieval"))
        log.update({"status":"completed" if citations else "no_evidence","citations":citations,"duration":round(time.monotonic()-started,3),"stages":stages,**(sum_usage(usage) if usage else {"inputTokens":0,"outputTokens":0,"tokens":0}),"usage":usage,"retrievalSnapshot":body})
    except Exception as error:
        usages=[*getattr(error,"prior_usage",[]),*[normalize_usage(value) for value in getattr(error,"usage",[])]]
        log.update({"status":"failed","error":public_error(error),"duration":round(time.monotonic()-started,3),**(sum_usage(usages) if usages else {"inputTokens":None,"outputTokens":None,"tokens":None}),"usage":usages})
        with SessionLocal.begin() as session:revision_lock(session);put(session,"logs",identifier,log,session_id);bump(session)
        error.run_id=identifier
        raise
    with SessionLocal.begin() as session:revision_lock(session);put(session,"logs",identifier,log,session_id);bump(session)
    return {"citations":citations,"runId":identifier}

@app.post("/api/conversations/{conversation_id}/messages")
def send(conversation_id:str,body:dict,ctx=Depends(context),compact=Depends(state_view)):
    regenerate=body.get("regenerate",False)
    if not isinstance(regenerate,bool):raise AppError("INVALID_FIELD","regenerate 必须为布尔值。")
    query=text_field(body,"query",10000)
    with SessionLocal.begin() as session:revision_lock(session);create_chat(session,safe_id(conversation_id),query,regenerate,ctx[1],body.get("inputs"));bump(session)
    with SessionLocal() as session:return snapshot(session,*ctx,compact=compact)

@app.post("/api/conversations/{conversation_id}/stop")
def stop_chat(conversation_id:str,ctx=Depends(context),compact=Depends(state_view)):
    with SessionLocal.begin() as session:
        revision_lock(session)
        row=record(session,"conversations",safe_id(conversation_id))
        if not row:raise AppError("NOT_FOUND","会话不存在。",404)
        if row.session_id!=ctx[1]:raise AppError("FORBIDDEN","不能访问其他浏览器会话。",403)
        cancel_jobs(session,conversation_id);value=copy.deepcopy(row.data)
        for message in value["messages"]:
            if message["status"]=="generating":message.update({"status":"canceled","text":message.get("text") or "已停止生成。"})
        row.data=value;bump(session)
    with SessionLocal() as session:return snapshot(session,*ctx,compact=compact)

# Curated cases are immutable snapshots of observed conversation runs. They are
# stored separately from the state snapshot and never accepted through PATCH state.
def case_access(row,ctx):
    require(ctx[0],"manager")
    if not row or (ctx[0]!="admin" and row.session_id!=ctx[1]):
        raise AppError("NOT_FOUND","案例不存在或当前角色无法查看。",404)
    return row

def case_fields(body):
    label=body.get("label")
    if label not in {"good","bad"}:raise AppError("INVALID_LABEL","请选择 Good Case 或 Bad Case。")
    return label,text_field(body,"note",2000)

@app.get("/api/evaluation/cases")
def list_evaluation_cases(page:int=1,pageSize:int=20,label:str="all",query:str="",ctx=Depends(context)):
    require(ctx[0],"manager")
    if page<1 or pageSize<1 or pageSize>100 or label not in {"all","good","bad"} or len(query)>200:
        raise AppError("INVALID_FILTER","案例筛选条件不正确。")
    conditions=[Record.collection=="evaluationCases"]
    if ctx[0]!="admin":conditions.append(Record.session_id==ctx[1])
    if label!="all":conditions.append(Record.data["label"].astext==label)
    if query.strip():
        term="%"+query.strip().replace("\\","\\\\").replace("%","\\%").replace("_","\\_")+"%"
        conditions.append((Record.data["query"].astext.ilike(term,escape="\\")) | (Record.data["assistantName"].astext.ilike(term,escape="\\")) | (Record.data["applicationName"].astext.ilike(term,escape="\\")) | (Record.data["runId"].astext.ilike(term,escape="\\")))
    with SessionLocal() as session:
        total=session.scalar(select(func.count()).select_from(Record).where(*conditions))
        rows=session.scalars(select(Record).where(*conditions).order_by(Record.data["createdAt"].astext.desc(),Record.id.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
        return {"items":[copy.deepcopy(row.data) for row in rows],"total":total,"page":page,"pageSize":pageSize}

@app.post("/api/evaluation/cases")
def add_evaluation_case(body:dict,ctx=Depends(context)):
    require(ctx[0],"manager");run_id=safe_id(body.get("runId"));label,note=case_fields(body)
    with SessionLocal.begin() as session:
        revision_lock(session)
        log=case_access(record(session,"logs",run_id),ctx)
        if log.data.get("kind")!="chat" or log.data.get("status")=="running" or log.data.get("purpose") in {"evaluation","debug","evaluation_draft"}:
            raise AppError("INVALID_CASE","只能收集已结束的对话运行。",409)
        conversation_id=log.data.get("conversationId")
        message=None
        if conversation_id:
            conversation=record(session,"conversations",conversation_id)
            if conversation and conversation.session_id==log.session_id:
                message=next((m for m in conversation.data.get("messages",[]) if m.get("runId")==run_id),None)
        else:
            # Older logs did not save a conversation ID; only use a verified match.
            for conversation in session.scalars(select(Record).where(Record.collection=="conversations",Record.session_id==log.session_id)):
                message=next((m for m in conversation.data.get("messages",[]) if m.get("runId")==run_id),None)
                if message:conversation_id=conversation.id;break
        if not conversation_id:raise AppError("NOT_CONVERSATION","该记录没有可验证的对话关联，不能收集。",409)
        case_id="case-"+hashlib.sha256(run_id.encode()).hexdigest()[:32]
        if record(session,"evaluationCases",case_id):raise AppError("CASE_EXISTS","该对话运行已收录，可在评测集中修改分类和备注。",409)
        output=log.data.get("output")
        if output is None and message:
            candidate=message.get("text","")
            if candidate and candidate!=message.get("error") and candidate!="已停止生成。":output=candidate
        value={"id":case_id,"runId":run_id,"conversationId":conversation_id,"messageId":log.data.get("messageId") or (message or {}).get("id"),"label":label,"note":note,"expectedAnswer":text_field(body,"expectedAnswer",10000),"query":log.data.get("query",""),"output":output,"status":log.data.get("status"),"assistantName":log.data.get("assistantName",""),"applicationName":log.data.get("applicationName",""),"applicationId":log.data.get("applicationId"),"applicationVersionNumber":log.data.get("applicationVersionNumber"),"model":log.data.get("model",""),"tokens":log.data.get("tokens"),"citations":copy.deepcopy(log.data.get("citations",[])),"runCreatedAt":log.data.get("createdAt"),"createdAt":now(),"updatedAt":now()}
        put(session,"evaluationCases",case_id,value,log.session_id);write_audit(session,ctx,"case.create","case",case_id);bump(session)
    return {"item":value}

@app.patch("/api/evaluation/cases/{case_id}")
def update_evaluation_case(case_id:str,body:dict,ctx=Depends(context)):
    label,note=case_fields(body)
    with SessionLocal.begin() as session:
        revision_lock(session);row=case_access(record(session,"evaluationCases",safe_id(case_id)),ctx)
        row.data={**row.data,"label":label,"note":note,"expectedAnswer":text_field(body,"expectedAnswer",10000) if "expectedAnswer" in body else row.data.get("expectedAnswer",""),"updatedAt":now()};value=copy.deepcopy(row.data);write_audit(session,ctx,"case.edit","case",case_id);bump(session)
    return {"item":value}

@app.delete("/api/evaluation/cases/{case_id}")
def delete_evaluation_case(case_id:str,ctx=Depends(context)):
    with SessionLocal.begin() as session:
        revision_lock(session);row=case_access(record(session,"evaluationCases",safe_id(case_id)),ctx)
        session.delete(row);write_audit(session,ctx,"case.delete","case",case_id);bump(session)
    return {"deleted":True}

def evaluation_run_view(session,row):
    data=copy.deepcopy(row.data);items=[]
    run_ids=[case["runId"] for case in data.get("cases",[])]
    logs={item.id:item for item in session.scalars(select(Record).where(Record.collection=="logs",Record.id.in_(run_ids))).all()} if run_ids else {}
    for case in data.get("cases",[]):
        log=logs.get(case["runId"])
        if not log:
            items.append({**case,"status":"missing","output":None,"tokens":None,"matched":None});continue
        value=log.data;status=value.get("status","running");output=value.get("output")
        expected=case.get("expectedAnswer","")
        matched=None
        if status in {"completed","no_evidence"} and isinstance(output,str) and expected.strip():
            matched=(expected.strip() in output.strip()) if data.get("scoringMode")=="contains" else (output.strip()==expected.strip())
        from .delivery_quality import score
        passed,checks=score(value,case["criteria"]) if "criteria" in case else (None,[])
        items.append({"passed":passed,"checks":checks,"duration":value.get("duration"),"citations":value.get("citations",[]),**case,"status":status,"output":output,"tokens":value.get("tokens"),"matched":matched,"error":value.get("error")})
    batch_id=data.get("latestJudgeBatchId");batch=record(session,"evaluationJudgeBatches",batch_id) if batch_id else None
    data["judgeBatch"]=judge_batch_view(session,batch) if batch else None
    data.pop("requestHash",None);data["results"]=items
    data["status"]="running" if any(x["status"]=="running" for x in items) else "failed" if any(x["status"] in {"failed","missing"} for x in items) else "canceled" if any(x["status"]=="canceled" for x in items) else "completed"
    data["matchedCount"]=sum(x["matched"] is True for x in items);data["scoredCount"]=sum(x["matched"] is not None for x in items)
    return data

@app.post("/api/evaluation/runs")
def create_evaluation_run(body:dict,ctx=Depends(context)):
    require(ctx[0],"manager");app_id=safe_id(body.get("appId"));request_id=safe_id(body.get("requestId"));case_ids=body.get("caseIds")
    if not isinstance(case_ids,list) or not 1<=len(case_ids)<=20 or any(not isinstance(x,str) for x in case_ids) or len(set(case_ids))!=len(case_ids):raise AppError("INVALID_CASES","每次选择 1 至 20 个不重复案例。")
    scoring_mode=body.get("scoringMode","exact")
    if not isinstance(scoring_mode,str) or scoring_mode not in {"exact","contains"}:raise AppError("INVALID_SCORING","评测方式无效。")
    case_ids=[safe_id(x) for x in case_ids];fingerprint=hashlib.sha256(json.dumps({"appId":app_id,"caseIds":case_ids,**({"scoringMode":scoring_mode} if scoring_mode!="exact" else {})},sort_keys=True).encode()).hexdigest()
    run_id="eval-"+hashlib.sha256((ctx[1]+":"+request_id).encode()).hexdigest()[:32]
    from .platform_apps import create_platform_conversation
    with SessionLocal.begin() as session:
        revision_lock(session);existing=record(session,"evaluationRuns",run_id)
        if existing:
            case_access(existing,ctx)
            if existing.data.get("requestHash")!=fingerprint:raise AppError("IDEMPOTENCY_CONFLICT","同一请求编号不能用于不同评测。",409)
            return {"run":evaluation_run_view(session,existing)}
        app=record(session,"platformApps",app_id)
        if not app or not app.data.get("publishedVersionId"):raise AppError("NOT_PUBLISHED","请先发布所选应用。",409)
        version_id=app.data["publishedVersionId"];cases=[]
        for case_id in case_ids:
            case=case_access(record(session,"evaluationCases",case_id),ctx);query=case.data.get("query","")
            if not query.strip():raise AppError("INVALID_CASE","案例缺少有效输入，不能执行评测。",409)
            conversation=create_platform_conversation(session,app_id,version_id,ctx[1],purpose="evaluation")
            create_chat(session,conversation["id"],query,False,ctx[1]);session.flush()
            log_id=next(m["runId"] for m in data_of(session,"conversations",conversation["id"])["messages"] if m.get("role")=="assistant")
            cases.append({"caseId":case_id,"query":query,"expectedAnswer":case.data.get("expectedAnswer",""),"label":case.data.get("label"),"conversationId":conversation["id"],"runId":log_id})
        value={"id":run_id,"appId":app_id,"appName":app.data.get("publishedName",""),"versionId":version_id,"versionNumber":app.data.get("publishedNumber"),"createdAt":now(),"requestHash":fingerprint,"scoringMode":scoring_mode,"cases":cases}
        put(session,"evaluationRuns",run_id,value,ctx[1]);write_audit(session,ctx,"evaluation.start","evaluation",run_id);session.flush();bump(session)
        row=record(session,"evaluationRuns",run_id);result=evaluation_run_view(session,row)
    return {"run":result}

@app.get("/api/evaluation/runs/{run_id}")
def get_evaluation_run(run_id:str,ctx=Depends(context)):
    with SessionLocal() as session:return {"run":evaluation_run_view(session,case_access(record(session,"evaluationRuns",safe_id(run_id)),ctx))}

@app.post("/api/evaluation/runs/{run_id}/cancel")
def cancel_evaluation_run(run_id:str,ctx=Depends(context)):
    with SessionLocal.begin() as session:
        revision_lock(session);row=case_access(record(session,"evaluationRuns",safe_id(run_id)),ctx)
        for case in row.data.get("cases",[]):
            conversation=record(session,"conversations",case["conversationId"])
            if not conversation or conversation.session_id!=row.session_id:continue
            cancel_jobs(session,conversation.id);value=copy.deepcopy(conversation.data)
            for message in value.get("messages",[]):
                if message.get("status")=="generating":message.update({"status":"canceled","text":message.get("text") or "已停止生成。"})
            conversation.data=value
        write_audit(session,ctx,"evaluation.cancel","evaluation",run_id);bump(session);result=evaluation_run_view(session,row)
    return {"run":result}

@app.put("/api/evaluation/runs/{run_id}/results/{case_id}/review")
def review_evaluation_result(run_id:str,case_id:str,body:dict,ctx=Depends(context)):
    score=body.get("score");note=text_field(body,"note",2000)
    if isinstance(score,bool) or not isinstance(score,int) or not 0<=score<=5:raise AppError("INVALID_SCORE","人工评分必须为 0 至 5 的整数。")
    with SessionLocal.begin() as session:
        revision_lock(session);row=case_access(record(session,"evaluationRuns",safe_id(run_id)),ctx)
        value=copy.deepcopy(row.data);entry=next((c for c in value.get("cases",[]) if c["caseId"]==safe_id(case_id)),None)
        if not entry:raise AppError("NOT_FOUND","评测结果不存在。",404)
        log=record(session,"logs",entry["runId"])
        if not log or log.data.get("status") not in {"completed","no_evidence"}:raise AppError("RESULT_NOT_READY","仅可评价已完成的结果。",409)
        entry["review"]={"score":score,"note":note,"updatedAt":now()};row.data=value
        write_audit(session,ctx,"evaluation.review","evaluation",run_id);bump(session);result=evaluation_run_view(session,row)
    return {"run":result}

@app.post("/api/evaluation/runs/{run_id}/results/{case_id}/judge")
async def judge_evaluation_result(run_id:str,case_id:str,ctx=Depends(context)):
    """One explicit model call; record its usage even if the output cannot be parsed."""
    run_id=safe_id(run_id);case_id=safe_id(case_id);judge_id=uid("judge");started=time.monotonic()
    with SessionLocal.begin() as session:
        revision_lock(session);row=case_access(record(session,"evaluationRuns",run_id),ctx)
        value=copy.deepcopy(row.data);entry=next((item for item in value.get("cases",[]) if item["caseId"]==case_id),None)
        if not entry:raise AppError("NOT_FOUND","评测结果不存在。",404)
        log=record(session,"logs",entry["runId"])
        if not log or log.data.get("status") not in {"completed","no_evidence"} or not isinstance(log.data.get("output"),str) or not log.data["output"].strip():raise AppError("RESULT_NOT_READY","仅可评判已完成且有回答的结果。",409)
        if len(log.data["output"])>10000:raise AppError("JUDGE_INPUT_TOO_LONG","回答超过 1 万字，暂不能进行模型评判。",409)
        expected=entry.get("expectedAnswer","")
        if not expected.strip():raise AppError("EXPECTED_REQUIRED","请先为案例填写参考答案并重新运行评测。",409)
        previous=entry.get("judge") or {}
        if previous.get("status")=="queued":raise AppError("JUDGE_BUSY","该结果的模型评判正在排队。",409)
        if previous.get("status")=="running":
            from datetime import datetime,timezone
            prior_job=session.get(Job,previous.get("id",""))
            try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(previous["startedAt"].replace("Z","+00:00"))).total_seconds()
            except (ValueError,KeyError,TypeError):age=0
            if (prior_job and prior_job.status in {"queued","running"}) or age<120:raise AppError("JUDGE_BUSY","该结果的模型评判正在进行。",409)
            old_log=record(session,"logs",previous.get("id",""))
            if old_log and old_log.data.get("status")=="running":old_log.data={**old_log.data,"status":"failed","errorCode":"JUDGE_INTERRUPTED","error":"上次评判已中断，供应商用量未知。"}
            previous={**previous,"status":"failed","errorCode":"JUDGE_INTERRUPTED","error":"上次评判已中断，供应商用量未知。","completedAt":now()}
        model=data_of(session,"models","llm")["model"];provider=new_provider(session)
        if previous:entry["judgeHistory"]=(entry.get("judgeHistory",[])+[copy.deepcopy(previous)])[-20:]
        entry["judge"]={"id":judge_id,"status":"running","model":model,"startedAt":now()};row.data=value
        judge_log=log_record(judge_id,"model","评测结果模型辅助判断",value.get("appName",""),model)
        judge_log.update({"purpose":"evaluation_judge","evaluationRunId":run_id,"caseId":case_id})
        put(session,"logs",judge_id,judge_log,row.session_id);write_audit(session,ctx,"evaluation.judge","evaluation",run_id);bump(session)
        from .evaluation_judging import make_request
        request_data=make_request(entry,log.data)
    from .evaluation_judging import judge
    parsed,usages,failure=await judge(provider,model,request_data)
    with SessionLocal.begin() as session:
        revision_lock(session);row=record(session,"evaluationRuns",run_id)
        log=record(session,"logs",judge_id)
        active=False
        if row:
            value=copy.deepcopy(row.data);entry=next((item for item in value.get("cases",[]) if item["caseId"]==case_id),None)
            active=bool(entry and (entry.get("judge") or {}).get("id")==judge_id and entry["judge"].get("status")=="running" and log and log.data.get("status")=="running")
            if active:
                entry["judge"]={**entry["judge"],"status":"failed" if failure else "completed","completedAt":now(),**({"error":failure["message"]} if failure else parsed)};row.data=value
        # A superseded request may still return billable usage, but cannot undo
        # an interruption or change the result belonging to a newer request.
        if log:log.data={**log.data,"duration":round(time.monotonic()-started,3),"usage":usages,**(sum_usage(usages) if usages else {"inputTokens":None,"outputTokens":None,"tokens":None}),**({"status":"failed" if failure else "completed","error":failure["message"] if failure else None,"errorCode":failure["code"] if failure else None} if active else {})}
        bump(session)
        updated=evaluation_run_view(session,row) if row else None
    return {"run":updated}

def judge_batch_view(session,batch):
    value=copy.deepcopy(batch.data)
    jobs={job.id:job for job in session.scalars(select(Job).where(Job.id.in_([x["jobId"] for x in value.get("items",[])]))).all()}
    items=[{**item,"status":jobs[item["jobId"]].status if item["jobId"] in jobs else "missing"} for item in value.get("items",[])]
    counts={status:sum(item["status"]==status for item in items) for status in ("queued","running","completed","failed","canceled","missing")}
    return {"id":batch.id,"runId":value["runId"],"createdAt":value["createdAt"],"items":items,"counts":counts,"status":"running" if counts["queued"] or counts["running"] else "completed"}

@app.post("/api/evaluation/runs/{run_id}/judge-batches")
def create_judge_batch(run_id:str,body:dict,ctx=Depends(context)):
    require(ctx[0],"manager");run_id=safe_id(run_id);request_id=safe_id(body.get("requestId"));case_ids=body.get("caseIds")
    if not isinstance(case_ids,list) or not 1<=len(case_ids)<=20 or any(not isinstance(x,str) for x in case_ids) or len(case_ids)!=len(set(case_ids)):raise AppError("INVALID_CASES","每次选择 1 至 20 个不重复案例。")
    case_ids=[safe_id(x) for x in case_ids];batch_id="judgebatch-"+hashlib.sha256((ctx[1]+":"+request_id).encode()).hexdigest()[:32]
    fingerprint=hashlib.sha256(json.dumps({"runId":run_id,"caseIds":case_ids},sort_keys=True).encode()).hexdigest()
    from .evaluation_judging import make_request
    from .security import get_secret
    with SessionLocal.begin() as session:
        revision_lock(session);run=case_access(record(session,"evaluationRuns",run_id),ctx)
        existing=record(session,"evaluationJudgeBatches",batch_id)
        if existing:
            case_access(existing,ctx)
            if existing.data.get("requestHash")!=fingerprint:raise AppError("IDEMPOTENCY_CONFLICT","同一请求编号不能用于不同评判。",409)
            return {"batch":judge_batch_view(session,existing),"run":evaluation_run_view(session,run)}
        value=copy.deepcopy(run.data);entries={item["caseId"]:item for item in value.get("cases",[])}
        if any(case_id not in entries for case_id in case_ids):raise AppError("NOT_FOUND","评测结果不存在。",404)
        active=session.scalar(select(Job.id).where(Job.entity_id==run_id,Job.kind=="eval_judge",Job.status.in_(["queued","running"])).limit(1))
        if active:raise AppError("JUDGE_BUSY","此评测已有模型评判任务，请等待完成或停止后续项目。",409)
        model=data_of(session,"models","llm")["model"]
        from .provider_hub import ensure_model
        ensure_model(session,model);plans=[]
        for case_id in case_ids:
            entry=entries[case_id];log=record(session,"logs",entry["runId"]);previous=entry.get("judge") or {}
            if previous.get("status")=="queued":raise AppError("JUDGE_BUSY","部分案例已有排队中的模型评判。",409)
            if previous.get("status")=="running":
                from datetime import datetime,timezone
                prior_job=session.get(Job,previous.get("id",""))
                try:age=(datetime.now(timezone.utc)-datetime.fromisoformat(previous["startedAt"].replace("Z","+00:00"))).total_seconds()
                except (ValueError,KeyError,TypeError):age=0
                if (prior_job and prior_job.status in {"queued","running"}) or age<120:raise AppError("JUDGE_BUSY","部分案例已有模型评判任务。",409)
                old_log=record(session,"logs",previous.get("id",""))
                if old_log and old_log.data.get("status")=="running":old_log.data={**old_log.data,"status":"failed","errorCode":"JUDGE_INTERRUPTED","error":"上次评判已中断，供应商用量未知。"}
                entry["judge"]={**previous,"status":"failed","errorCode":"JUDGE_INTERRUPTED","error":"上次评判已中断，供应商用量未知。","completedAt":now()}
            if not log or log.data.get("status") not in {"completed","no_evidence"} or not isinstance(log.data.get("output"),str) or not log.data["output"].strip():raise AppError("RESULT_NOT_READY","仅可评判已完成且有回答的结果。",409)
            if len(log.data["output"])>10000:raise AppError("JUDGE_INPUT_TOO_LONG","回答超过 1 万字，暂不能进行模型评判。",409)
            if not entry.get("expectedAnswer","").strip():raise AppError("EXPECTED_REQUIRED","请先为案例填写参考答案并重新运行评测。",409)
            plans.append((entry,make_request(entry,log.data)))
        items=[]
        for entry,request in plans:
            judge_id=uid("judge");previous=entry.get("judge")
            if previous:entry["judgeHistory"]=(entry.get("judgeHistory",[])+[copy.deepcopy(previous)])[-20:]
            entry["judge"]={"id":judge_id,"status":"queued","model":model,"batchId":batch_id,"queuedAt":now()}
            session.add(Job(id=judge_id,kind="eval_judge",entity_id=run_id,session_id=run.session_id,status="queued",payload={"caseId":entry["caseId"],"model":model,"request":request},attempts=0))
            log=log_record(judge_id,"model","评测结果模型辅助判断",value.get("appName",""),model)
            log.update({"purpose":"evaluation_judge","evaluationRunId":run_id,"caseId":entry["caseId"],"batchId":batch_id})
            put(session,"logs",judge_id,log,run.session_id);items.append({"caseId":entry["caseId"],"jobId":judge_id})
        value["latestJudgeBatchId"]=batch_id;run.data=value
        batch={"id":batch_id,"runId":run_id,"createdAt":now(),"requestHash":fingerprint,"items":items}
        put(session,"evaluationJudgeBatches",batch_id,batch,run.session_id);write_audit(session,ctx,"evaluation.judge_batch","evaluation",run_id);session.flush();bump(session)
        return {"batch":judge_batch_view(session,record(session,"evaluationJudgeBatches",batch_id)),"run":evaluation_run_view(session,run)}

@app.post("/api/evaluation/judge-batches/{batch_id}/cancel")
def cancel_judge_batch(batch_id:str,ctx=Depends(context)):
    from .evaluation_judging import update_entry
    with SessionLocal.begin() as session:
        revision_lock(session);batch=case_access(record(session,"evaluationJudgeBatches",safe_id(batch_id)),ctx)
        for item in batch.data.get("items",[]):
            job=session.get(Job,item["jobId"],with_for_update=True)
            if not job or job.status!="queued":continue
            job.status="canceled";job.lease_until=None;job.payload={k:v for k,v in job.payload.items() if k!="request"}
            log=record(session,"logs",job.id)
            if log:log.data={**log.data,"status":"canceled","error":"评判尚未开始，已取消。","inputTokens":0,"outputTokens":0,"tokens":0}
            update_entry(session,job.id,job.entity_id,item["caseId"],{"status":"canceled","error":"评判尚未开始，已取消。","completedAt":now()})
        write_audit(session,ctx,"evaluation.judge_cancel","evaluation",batch.data["runId"]);session.flush();bump(session)
        return {"batch":judge_batch_view(session,batch)}

@app.get("/api/evaluation/runs")
def list_evaluation_runs(page:int=1,pageSize:int=20,ctx=Depends(context)):
    require(ctx[0],"manager")
    if page<1 or not 1<=pageSize<=100:raise AppError("INVALID_PAGE","分页大小范围为 1–100。")
    with SessionLocal() as session:
        filters=[Record.collection=="evaluationRuns"]
        if ctx[0]!="admin":filters.append(Record.session_id==ctx[1])
        total=session.scalar(select(func.count()).select_from(Record).where(*filters))
        rows=session.scalars(select(Record).where(*filters).order_by(Record.data["createdAt"].astext.desc(),Record.id.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
        return {"items":[evaluation_run_view(session,row) for row in rows],"total":total,"page":page,"pageSize":pageSize}

@app.get("/api/platform/audit")
def list_platform_audit(page:int=1,pageSize:int=20,ctx=Depends(context)):
    require(ctx[0],"admin")
    if page<1 or pageSize<1 or pageSize>100:raise AppError("INVALID_PAGE","分页大小范围为 1–100。")
    with SessionLocal() as session:
        filters=[Record.collection=="platformAudit"]
        total=session.scalar(select(func.count()).select_from(Record).where(*filters))
        rows=session.scalars(select(Record).where(*filters).order_by(Record.data["createdAt"].astext.desc(),Record.id.desc()).offset((page-1)*pageSize).limit(pageSize)).all()
        return {"items":[copy.deepcopy(row.data) for row in rows],"total":total,"page":page,"pageSize":pageSize}

@app.get("/api/platform/log-policy")
def get_log_policy(ctx=Depends(context)):
    require(ctx[0],"admin")
    with SessionLocal() as session:
        policy=record(session,"platformLogPolicy","local")
        value=copy.deepcopy(policy.data) if policy else {"maskEnabled":False,"retentionDays":None}
        days=value.get("retentionDays");count=len(log_policy.retention_candidates(session,days)) if days else 0
        return {"policy":value,"eligibleCount":count}

@app.put("/api/platform/log-policy")
def update_log_policy(body:dict,ctx=Depends(context)):
    require(ctx[0],"admin");enabled=body.get("maskEnabled");days=body.get("retentionDays")
    if not isinstance(enabled,bool) or (days is not None and (isinstance(days,bool) or not isinstance(days,int) or not 1<=days<=3650)):raise AppError("INVALID_POLICY","日志保留天数须为 1–3650 的整数，或选择不设置。")
    with SessionLocal.begin() as session:
        revision_lock(session);value={"maskEnabled":enabled,"retentionDays":days,"updatedAt":now()};put(session,"platformLogPolicy","local",value);write_audit(session,ctx,"logs.policy","policy","local");bump(session)
    return {"policy":value}

@app.post("/api/platform/log-policy/cleanup")
def apply_log_retention(ctx=Depends(context)):
    require(ctx[0],"admin")
    with SessionLocal.begin() as session:
        policy=record(session,"platformLogPolicy","local");days=policy.data.get("retentionDays") if policy else None
        if not days:raise AppError("RETENTION_NOT_CONFIGURED","请先设置日志保留天数。",409)
        count=log_policy.cleanup(session,ctx,days)
    return {"deleted":count}

@app.post("/api/platform/bills/preview")
async def preview_bill(file:Annotated[UploadFile,File()],ctx=Depends(context)):
    require(ctx[0],"admin")
    from .billing_import import MAX_BYTES,preview
    return preview(await file.read(MAX_BYTES+1))

@app.post("/api/platform/bills")
async def import_bill(file:Annotated[UploadFile,File()],period:Annotated[str,Form()],amountColumn:Annotated[str,Form()],periodColumn:Annotated[str,Form()]="",scope:Annotated[str,Form()]="",ctx=Depends(context)):
    require(ctx[0],"admin")
    from .billing_import import MAX_BYTES,summarize
    if not scope.strip() or len(scope)>300:raise AppError("BILL_SCOPE","请填写账单来源及统计范围，最多 300 字。")
    raw=await file.read(MAX_BYTES+1);value=summarize(raw,amountColumn,periodColumn,period)
    filename=Path((file.filename or "账单.csv").replace("\\","/")).name[:160]
    with SessionLocal.begin() as session:
        revision_lock(session)
        duplicate=session.scalar(select(Record.id).where(Record.collection=="platformBills",Record.data["sha256"].astext==value["sha256"]).limit(1))
        if duplicate:raise AppError("BILL_DUPLICATE","该文件已导入，请勿重复计算。",409)
        identifier=uid("bill");value.update({"id":identifier,"filename":filename,"scope":scope.strip(),"currency":"CNY","createdAt":now()})
        put(session,"platformBills",identifier,value,ctx[1]);write_audit(session,ctx,"bill.import","bill",identifier);bump(session)
    return {"item":value}

@app.get("/api/platform/bills")
def list_bills(ctx=Depends(context)):
    require(ctx[0],"admin")
    with SessionLocal() as session:
        rows=session.scalars(select(Record).where(Record.collection=="platformBills").order_by(Record.data["createdAt"].astext.desc()).limit(100)).all()
        return {"items":[{k:v for k,v in row.data.items() if k!="sha256"} for row in rows]}

@app.delete("/api/platform/bills/{bill_id}")
def delete_bill(bill_id:str,ctx=Depends(context)):
    require(ctx[0],"admin")
    with SessionLocal.begin() as session:
        revision_lock(session);row=record(session,"platformBills",safe_id(bill_id))
        if not row:raise AppError("NOT_FOUND","账单批次不存在。",404)
        session.delete(row);write_audit(session,ctx,"bill.delete","bill",bill_id);bump(session)
    return {"deleted":True}

@app.get("/api/platform/alerts")
def list_platform_alerts(ctx=Depends(context)):
    require(ctx[0],"admin")
    with SessionLocal() as session:
        rows=session.scalars(select(Record).where(Record.collection=="platformAlerts").order_by(Record.data["createdAt"].astext.desc()).limit(24)).all()
        return {"items":[copy.deepcopy(row.data) for row in rows]}

@app.get("/api/platform/budget")
def get_platform_budget(ctx=Depends(context)):
    require(ctx[0],"manager")
    with SessionLocal() as session:
        row=record(session,"platformBudget","local")
        return {"budget":copy.deepcopy(row.data) if row else None}

@app.put("/api/platform/budget")
def put_platform_budget(body:dict,ctx=Depends(context)):
    require(ctx[0],"admin")
    from decimal import Decimal, InvalidOperation
    raw=body.get("monthlyLimit")
    if not isinstance(raw,str) or len(raw)>20:raise AppError("INVALID_BUDGET","预算请输入有效金额。")
    try:value=Decimal(raw)
    except InvalidOperation:raise AppError("INVALID_BUDGET","预算请输入有效金额。") from None
    if not value.is_finite() or value<=0 or value>100000000 or value.as_tuple().exponent < -2:raise AppError("INVALID_BUDGET","预算须大于 0、最多两位小数。")
    with SessionLocal.begin() as session:
        revision_lock(session);budget={"monthlyLimit":str(value),"updatedAt":now()};put(session,"platformBudget","local",budget);write_audit(session,ctx,"budget.edit","budget","local");bump(session)
    return {"budget":budget}

@app.get("/api/platform/prices")
def list_platform_prices(ctx=Depends(context)):
    require(ctx[0],"manager")
    with SessionLocal() as session:
        rows=session.scalars(select(Record).where(Record.collection=="platformPricing")).all()
        return {"items":sorted([copy.deepcopy(row.data) for row in rows],key=lambda x:x["effectiveAt"],reverse=True)}

@app.post("/api/platform/prices")
def add_platform_price(body:dict,ctx=Depends(context)):
    from decimal import Decimal,InvalidOperation
    from datetime import datetime,timezone
    require(ctx[0],"admin");model=text_field(body,"model",160,True);kind=body.get("kind");effective=text_field(body,"effectiveAt",50,True)
    if kind not in {"llm","embedding","rerank","ocr","vision"}:raise AppError("INVALID_PRICE","请选择受支持的调用能力。")
    try:
        stamp=datetime.fromisoformat(effective.replace("Z","+00:00"))
        if stamp.tzinfo is None:raise ValueError()
        effective=stamp.astimezone(timezone.utc).isoformat()
        rates=[]
        for name in ("inputRate","outputRate"):
            value=body.get(name)
            if isinstance(value,bool) or not isinstance(value,(str,int,float)):raise ValueError()
            amount=Decimal(str(value))
            if not amount.is_finite() or amount<0 or amount>10000000 or amount.as_tuple().exponent < -6:raise ValueError()
            rates.append(str(amount))
        cached_rate=body.get("cachedInputRate")
        if cached_rate not in (None,""):
            if isinstance(cached_rate,bool) or not isinstance(cached_rate,(str,int,float)):raise ValueError()
            cached_rate=Decimal(str(cached_rate))
            if not cached_rate.is_finite() or cached_rate<0 or cached_rate>10000000 or cached_rate.as_tuple().exponent < -6:raise ValueError()
            cached_rate=str(cached_rate)
        else:cached_rate=None
    except (ValueError,InvalidOperation,TypeError,OverflowError):raise AppError("INVALID_PRICE","价格或生效时间无效；单价须为非负数且最多 6 位小数。") from None
    with SessionLocal.begin() as session:
        revision_lock(session)
        duplicate=session.scalar(select(Record.id).where(Record.collection=="platformPricing",Record.data["model"].astext==model,Record.data["kind"].astext==kind,Record.data["effectiveAt"].astext==effective).limit(1))
        if duplicate:raise AppError("PRICE_EXISTS","该模型和能力在同一时间已有价格规则，请使用不同生效时间。",409)
        identifier=uid("price");value={"id":identifier,"model":model,"kind":kind,"currency":"CNY","unit":"million_tokens","inputRate":rates[0],"outputRate":rates[1],"cachedInputRate":cached_rate,"effectiveAt":effective,"createdAt":now()};put(session,"platformPricing",identifier,value);write_audit(session,ctx,"price.create","price",identifier);bump(session)
    return {"item":value}

@app.get("/api/platform/skills")
def list_platform_skills(ctx=Depends(context)):
    require(ctx[0],"manager")
    with SessionLocal() as session:
        rows=session.scalars(select(Record).where(Record.collection=="platformSkills")).all()
        return {"items":[copy.deepcopy(r.data) for r in rows]}

def validate_skill(body):
    name=text_field(body,"name",120,True);description=text_field(body,"description",1000);instructions=text_field(body,"instructions",20000,True)
    enabled=body.get("enabled",True)
    if not isinstance(enabled,bool):raise AppError("INVALID_SKILL","Skill 状态无效。")
    return {"name":name,"description":description,"instructions":instructions,"enabled":enabled}

@app.post("/api/platform/skills")
def create_platform_skill(body:dict,ctx=Depends(context)):
    require(ctx[0],"manager");fields=validate_skill(body)
    with SessionLocal.begin() as session:
        revision_lock(session);identifier=uid("skill");value={"id":identifier,**fields,"version":1,"createdAt":now(),"updatedAt":now()};put(session,"platformSkills",identifier,value);write_audit(session,ctx,"skill.create","skill",identifier);bump(session)
    return {"item":value}

@app.patch("/api/platform/skills/{skill_id}")
def update_platform_skill(skill_id:str,body:dict,ctx=Depends(context)):
    require(ctx[0],"manager");fields=validate_skill(body)
    with SessionLocal.begin() as session:
        revision_lock(session);row=record(session,"platformSkills",safe_id(skill_id))
        if not row:raise AppError("NOT_FOUND","Skill 不存在。",404)
        row.data={**row.data,**fields,"version":row.data.get("version",0)+1,"updatedAt":now()};value=copy.deepcopy(row.data);write_audit(session,ctx,"skill.edit","skill",skill_id);bump(session)
    return {"item":value}

def remote_tool_fields(body):
    from .remote_tools import validate_endpoint
    name=text_field(body,"name",100,True);description=text_field(body,"description",500,True);endpoint=validate_endpoint(body.get("endpoint"))
    enabled=body.get("enabled",True)
    if not isinstance(enabled,bool):raise AppError("INVALID_TOOL","工具状态无效。")
    return {"name":name,"description":description,"endpoint":endpoint,"enabled":enabled}

@app.get("/api/platform/remote-tools")
def list_remote_tools(ctx=Depends(context)):
    require(ctx[0],"manager")
    with SessionLocal() as session:
        rows=session.scalars(select(Record).where(Record.collection=="platformRemoteTools")).all()
        return {"items":[copy.deepcopy(r.data) for r in rows]}

@app.post("/api/platform/remote-tools")
def create_remote_tool(body:dict,ctx=Depends(context)):
    require(ctx[0],"admin");fields=remote_tool_fields(body);token=tool_credentials.validate_token(body.get("bearerToken"))
    with SessionLocal.begin() as session:
        revision_lock(session);identifier=uid("tool");value={"id":identifier,**fields,"credentialConfigured":bool(token),"version":1,"createdAt":now(),"updatedAt":now()}
        if token:tool_credentials.store(session,identifier,fields["endpoint"],token)
        put(session,"platformRemoteTools",identifier,value);write_audit(session,ctx,"tool.create","tool",identifier);bump(session)
    return {"item":value}

@app.patch("/api/platform/remote-tools/{tool_id}")
def update_remote_tool(tool_id:str,body:dict,ctx=Depends(context)):
    require(ctx[0],"admin");fields=remote_tool_fields(body)
    with SessionLocal.begin() as session:
        revision_lock(session);row=record(session,"platformRemoteTools",safe_id(tool_id))
        if not row:raise AppError("NOT_FOUND","工具不存在。",404)
        token=tool_credentials.validate_token(body.get("bearerToken"));clear=body.get("clearCredential",False)
        if not isinstance(clear,bool) or token and clear:raise AppError("INVALID_TOOL_CREDENTIAL","不能同时更换和删除凭据。")
        if row.data.get("credentialConfigured") and fields["endpoint"]!=row.data.get("endpoint") and not (token or clear):raise AppError("TOOL_CREDENTIAL_ENDPOINT_MISMATCH","修改地址时请重新填写或删除凭据。",409)
        if token:tool_credentials.store(session,tool_id,fields["endpoint"],token)
        elif clear:tool_credentials.clear(session,tool_id)
        configured=bool(token) or bool(row.data.get("credentialConfigured") and not clear)
        row.data={**row.data,**fields,"credentialConfigured":configured,"version":row.data.get("version",0)+1,"updatedAt":now()};value=copy.deepcopy(row.data);write_audit(session,ctx,"tool.edit","tool",tool_id);bump(session)
    return {"item":value}

def validate_mcp_access(endpoint,ctx):
    from .mcp_tools import is_local_endpoint
    if is_local_endpoint(endpoint):
        with SessionLocal() as session:
            config=record(session,"platformAuth","local")
            if not config or not config.data.get("enabled"):raise AppError("MCP_LOCAL_AUTH_REQUIRED","使用本机 MCP 前，请先启用本机账号认证。",403)
        require(ctx[0],"admin")

@app.post("/api/platform/mcp/discover")
async def discover_mcp_tools(body:dict,ctx=Depends(context)):
    require(ctx[0],"admin")
    from .mcp_tools import discover
    endpoint=body.get("endpoint");validate_mcp_access(endpoint,ctx);token=tool_credentials.validate_token(body.get("bearerToken"))
    return {"items":await discover(endpoint,token)}

@app.get("/api/platform/mcp-tools")
def list_mcp_tools(ctx=Depends(context)):
    require(ctx[0],"manager")
    with SessionLocal() as session:
        rows=session.scalars(select(Record).where(Record.collection=="platformMcpTools")).all()
        return {"items":[copy.deepcopy(r.data) for r in rows]}

@app.post("/api/platform/mcp-tools")
async def add_mcp_tool(body:dict,ctx=Depends(context)):
    require(ctx[0],"admin")
    from .mcp_tools import discover
    endpoint=body.get("endpoint");validate_mcp_access(endpoint,ctx);tool_name=text_field(body,"toolName",100,True);token=tool_credentials.validate_token(body.get("bearerToken"))
    found=await discover(endpoint,token);selected=next((t for t in found if t["name"]==tool_name),None)
    if not selected:raise AppError("MCP_TOOL_UNAVAILABLE","未找到可用的只读工具。",409)
    name=text_field(body,"name",100,True)
    with SessionLocal.begin() as session:
        revision_lock(session);identifier=uid("mcp");value={"id":identifier,"name":name,"description":selected["description"],"endpoint":endpoint,"toolName":tool_name,"inputSchema":selected["inputSchema"],"enabled":True,"credentialConfigured":bool(token),"version":1,"createdAt":now(),"updatedAt":now()}
        if token:tool_credentials.store(session,identifier,endpoint,token)
        put(session,"platformMcpTools",identifier,value);write_audit(session,ctx,"mcp.create","tool",identifier);bump(session)
    return {"item":value}

@app.patch("/api/platform/mcp-tools/{tool_id}")
def set_mcp_tool_enabled(tool_id:str,body:dict,ctx=Depends(context)):
    require(ctx[0],"admin");enabled=body.get("enabled")
    if not isinstance(enabled,bool):raise AppError("INVALID_TOOL","工具状态无效。")
    with SessionLocal.begin() as session:
        revision_lock(session);row=record(session,"platformMcpTools",safe_id(tool_id))
        if not row:raise AppError("NOT_FOUND","工具不存在。",404)
        row.data={**row.data,"enabled":enabled,"updatedAt":now()};value=copy.deepcopy(row.data);write_audit(session,ctx,"mcp.enable" if enabled else "mcp.disable","tool",tool_id);bump(session)
    return {"item":value}

@app.put("/api/platform/mcp-tools/{tool_id}/credential")
def update_mcp_credential(tool_id:str,body:dict,ctx=Depends(context)):
    require(ctx[0],"admin");token=tool_credentials.validate_token(body.get("bearerToken"));clear=body.get("clearCredential",False)
    if not isinstance(clear,bool) or bool(token)==clear:raise AppError("INVALID_TOOL_CREDENTIAL","请选择更换或删除凭据。")
    with SessionLocal.begin() as session:
        revision_lock(session);row=record(session,"platformMcpTools",safe_id(tool_id))
        if not row:raise AppError("NOT_FOUND","MCP 工具不存在。",404)
        if token:tool_credentials.store(session,tool_id,row.data["endpoint"],token)
        else:tool_credentials.clear(session,tool_id)
        row.data={**row.data,"credentialConfigured":bool(token),"updatedAt":now()};write_audit(session,ctx,"mcp.credential","tool",tool_id);bump(session)
    return {"saved":True,"credentialConfigured":bool(token)}

@app.get("/api/platform/tools")
def list_platform_tools(ctx=Depends(context)):
    require(ctx[0],"manager")
    return {"items":[{"id":"knowledge_search","name":"知识库检索","type":"tool","description":"在应用配置的知识库范围内检索。"},{"id":"answer_with_citations","name":"引用回答规范","type":"skill","description":"约束依据不足和来源引用的内置 Skill。"}]}

from .platform_apps import router_for as platform_apps_router
app.include_router(platform_apps_router(context, lambda: SessionLocal))
app.include_router(local_auth.router_for(context, lambda: SessionLocal))


from .delivery_quality import router_for as delivery_quality_router
app.include_router(delivery_quality_router(context, lambda: SessionLocal))

from .delivery_studio import router_for as delivery_studio_router
app.include_router(delivery_studio_router(context, lambda: SessionLocal))

from .delivery_channels import router_for as delivery_channels_router
app.include_router(delivery_channels_router(context, lambda: SessionLocal))

from .provider_hub import router_for as provider_hub_router
app.include_router(provider_hub_router(context, lambda: SessionLocal))

from .import_preview import router_for as import_preview_router
app.include_router(import_preview_router(context, lambda: SessionLocal))

from .power_quality.routes import build_router as power_quality_router
app.include_router(power_quality_router(context, lambda: SessionLocal()))

# In production the API serves the exact built frontend; Vite uses an /api proxy.
dist=Path(__file__).resolve().parents[2]/"dist"
if dist.is_dir():app.mount("/",StaticFiles(directory=dist,html=True),name="frontend")

# Outermost guard also protects documentation, pages, artifacts and logs.
from .power_quality.local_access import LocalAccessMiddleware
app.add_middleware(LocalAccessMiddleware, factory=lambda: SessionLocal())
