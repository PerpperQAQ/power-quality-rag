"""Application drafts and immutable publication snapshots for the local workbench."""
import copy
from fastapi import APIRouter, Depends
from sqlalchemy import select
from .db import Record, bump, revision_lock
from .security import AppError, require, safe_id
from .platform_audit import write_audit
from .services import uid
from .state import data_of, now, put, record, text_field


def create_platform_conversation(session, app_id, version_id, session_id, purpose=None):
    version=record(session,"platformVersions",safe_id(version_id))
    if not version or version.data.get("appId")!=app_id:raise AppError("VERSION_MISSING","应用发布版本不可用。",409)
    assistant=copy.deepcopy(version.data["assistantSnapshot"])
    for kb_id in assistant.get("kbIds",[]):data_of(session,"knowledgeBases",kb_id)
    identifier=uid("conversation")
    value={"id":identifier,"assistantId":assistant["id"],"title":"新对话","messages":[],"updatedAt":now(),"configVersion":assistant["configVersion"],"kbIds":assistant["kbIds"],"assistantSnapshot":assistant,"delivery":copy.deepcopy(version.data.get("delivery")),"platformAppId":app_id,"platformAppName":version.data["name"],"platformVersionId":version_id,"platformVersionNumber":version.data["number"],**({"purpose":purpose} if purpose else {})}
    put(session,"conversations",identifier,value,session_id);session.flush()
    return value


def normalize_draft(body,session):
    name=text_field(body,"name",120,True);description=text_field(body,"description",2000)
    assistant_id=safe_id(body.get("assistantId"));assistant=data_of(session,"assistants",assistant_id)
    if not assistant.get("enabled"):raise AppError("ASSISTANT_DISABLED","请选择已启用的助手。",409)
    kind=body.get("type","simple")
    if not isinstance(kind,str) or kind not in {"simple","agent","workflow"}:raise AppError("TYPE_NOT_AVAILABLE","该应用类型的运行器尚未接入。",409)
    skills=body.get("skills",[])
    if not isinstance(skills,list) or len(skills)>4 or any(not isinstance(x,str) for x in skills):raise AppError("INVALID_SKILL","最多选择 4 个有效 Skill。")
    for skill_id in skills:
        if skill_id=="answer_with_citations":continue
        row=record(session,"platformSkills",safe_id(skill_id))
        if not row or not row.data.get("enabled"):raise AppError("INVALID_SKILL","所选 Skill 不存在或已停用。",409)
    tool_ids=body.get("toolIds",[])
    if not isinstance(tool_ids,list) or len(tool_ids)>4 or any(not isinstance(x,str) for x in tool_ids):raise AppError("INVALID_TOOL","最多选择 4 个有效工具。")
    for tool_id in tool_ids:
        tool=record(session,"platformRemoteTools",safe_id(tool_id))
        if not tool or not tool.data.get("enabled"):raise AppError("INVALID_TOOL","所选工具不存在或已停用。",409)
    mcp_ids=body.get("mcpToolIds",[])
    if not isinstance(mcp_ids,list) or len(mcp_ids)>4 or any(not isinstance(x,str) for x in mcp_ids):raise AppError("INVALID_TOOL","最多选择 4 个 MCP 工具。")
    for mcp_id in mcp_ids:
        tool=record(session,"platformMcpTools",safe_id(mcp_id))
        if not tool or not tool.data.get("enabled"):raise AppError("INVALID_TOOL","所选 MCP 工具不存在或已停用。",409)
    result={"name":name,"description":description,"assistantId":assistant_id,"type":kind,"skills":list(dict.fromkeys(skills)),"toolIds":list(dict.fromkeys(tool_ids)),"mcpToolIds":list(dict.fromkeys(mcp_ids))}
    if kind=="workflow":
        from .workflow_runtime import DEFAULT_STEPS,validate_steps
        if body.get("graph") is not None:
            from .workflow_graph import validate_graph
            result["graph"]=validate_graph(body["graph"])
        else:result["steps"]=validate_steps(body.get("steps",DEFAULT_STEPS))
    from .delivery_config import validate_delivery,effective_assistant
    if 'delivery' in body:result['delivery']=validate_delivery(body['delivery'])
    if 'assistantConfig' in body:
        result['assistantConfig']=copy.deepcopy(body['assistantConfig']);effective_assistant(session,result)
    return result
def public_app(row,role):
    app=copy.deepcopy(row.data)
    if role=="user":
        app.pop("draft",None)
    return app
def get_app(session,identifier):
    app=record(session,"platformApps",safe_id(identifier))
    if not app:raise AppError("NOT_FOUND","应用不存在。",404)
    return app
def snapshot_version(session, app_id, draft, number, debug=False):
    from .delivery_config import effective_assistant
    assistant=effective_assistant(session,draft)
    version_id=uid("appversion")
    from .agent_runtime import SKILLS
    skill_definitions={}
    for skill_id in draft["skills"]:
        if skill_id in SKILLS:skill_definitions[skill_id]=copy.deepcopy(SKILLS[skill_id])
        else:
            skill=record(session,"platformSkills",skill_id)
            if not skill or not skill.data.get("enabled"):raise AppError("INVALID_SKILL","所选 Skill 不存在或已停用。",409)
            skill_definitions[skill_id]={k:skill.data[k] for k in ("name","version","instructions")}
    tool_definitions={}
    for tool_id in draft["toolIds"]:
        tool=record(session,"platformRemoteTools",tool_id)
        if not tool or not tool.data.get("enabled"):raise AppError("INVALID_TOOL","所选工具不存在或已停用。",409)
        tool_definitions[tool_id]={**{k:tool.data[k] for k in ("name","version","description","endpoint")},"requiresCredential":bool(tool.data.get("credentialConfigured"))}
    mcp_definitions={}
    for tool_id in draft["mcpToolIds"]:
        tool=record(session,"platformMcpTools",tool_id)
        if not tool or not tool.data.get("enabled"):raise AppError("INVALID_TOOL","所选 MCP 工具不存在或已停用。",409)
        mcp_definitions[tool_id]={**{k:copy.deepcopy(tool.data[k]) for k in ("name","version","description","endpoint","toolName","inputSchema")},"requiresCredential":bool(tool.data.get("credentialConfigured"))}
    workflow_references={}
    if draft.get("graph"):
        from .workflow_references import resolve_references
        workflow_references=resolve_references(session,draft["graph"],app_id)
    version={"id":version_id,"appId":app_id,"number":number,"name":draft["name"],"description":draft["description"],"type":draft["type"],"skills":draft["skills"],"skillDefinitions":skill_definitions,"toolIds":draft["toolIds"],"toolDefinitions":tool_definitions,"mcpToolDefinitions":mcp_definitions,"steps":draft.get("steps",[]),"graph":draft.get("graph"),"workflowReferences":workflow_references,"assistantSnapshot":assistant,"createdAt":now(),"debug":debug}
    if "delivery" in draft:version["delivery"]=copy.deepcopy(draft["delivery"])
    return version


def router_for(context, session_factory):
    router=APIRouter(prefix="/api/platform/apps")
    @router.get("")
    def list_apps(ctx=Depends(context)):
        with session_factory()() as session:
            rows=session.scalars(select(Record).where(Record.collection=="platformApps")).all()
            return {"items":[public_app(r,ctx[0]) for r in sorted(rows,key=lambda r:r.data.get("updatedAt",""),reverse=True) if ctx[0]!="user" or r.data.get("publishedVersionId")]}
    @router.post("")
    def create_app(body:dict,ctx=Depends(context)):
        require(ctx[0],"manager")
        with session_factory().begin() as session:
            revision_lock(session);draft=normalize_draft(body,session);identifier=uid("app")
            value={"id":identifier,"draft":draft,"draftRevision":1,"type":draft["type"],"publishedVersionId":None,"createdAt":now(),"updatedAt":now()}
            put(session,"platformApps",identifier,value);write_audit(session,ctx,"app.create","application",identifier);bump(session)
        return {"item":value}
    @router.patch("/{app_id}")
    def edit_app(app_id:str,body:dict,ctx=Depends(context)):
        require(ctx[0],"manager")
        with session_factory().begin() as session:
            revision_lock(session);row=get_app(session,app_id)
            if body.get("draftRevision") is not None and body["draftRevision"]!=row.data.get("draftRevision",1):raise AppError("DRAFT_CONFLICT","草稿已被其他窗口修改，请重新加载后再保存。",409)
            draft=normalize_draft({**row.data["draft"],**body},session)
            if draft["type"]!=row.data["type"]:raise AppError("TYPE_CHANGE_FORBIDDEN","应用类型创建后不可更改，请新建应用。",409)
            row.data={**row.data,"draft":draft,"draftRevision":row.data.get("draftRevision",1)+1,"updatedAt":now()};value=copy.deepcopy(row.data);write_audit(session,ctx,"app.edit","application",app_id);bump(session)
        return {"item":value}
    @router.post("/{app_id}/publish")
    def publish_app(app_id:str,ctx=Depends(context)):
        require(ctx[0],"manager")
        with session_factory().begin() as session:
            revision_lock(session);row=get_app(session,app_id);draft=normalize_draft(row.data["draft"],session)
            previous=list(session.scalars(select(Record).where(Record.collection=="platformVersions",Record.data["appId"].astext==app_id)))
            number=max([v.data.get("number",0) for v in previous if not v.data.get("debug")],default=0)+1
            version=snapshot_version(session,app_id,draft,number)
            from .delivery_quality import enforce_gate
            enforce_gate(session,app_id,version)
            version_id=version["id"]
            put(session,"platformVersions",version_id,version)
            row.data={**row.data,"publishedVersionId":version_id,"publishedName":version["name"],"publishedDescription":version["description"],"publishedNumber":number,"publishedDelivery":copy.deepcopy(version.get("delivery")),"updatedAt":now()}
            value=copy.deepcopy(row.data);write_audit(session,ctx,"app.publish","application",app_id);bump(session)
        return {"item":value,"version":version}
    @router.get("/{app_id}/versions")
    def versions(app_id:str,ctx=Depends(context)):
        require(ctx[0],"manager")
        with session_factory()() as session:
            get_app(session,app_id)
            rows=session.scalars(select(Record).where(Record.collection=="platformVersions",Record.data["appId"].astext==app_id)).all()
            return {"items":sorted([copy.deepcopy(r.data) for r in rows if not r.data.get("debug")],key=lambda v:v["number"],reverse=True)}
    @router.post("/{app_id}/rollback/{version_id}")
    def rollback(app_id:str,version_id:str,ctx=Depends(context)):
        require(ctx[0],"manager")
        with session_factory().begin() as session:
            revision_lock(session);row=get_app(session,app_id);version=record(session,"platformVersions",safe_id(version_id))
            if not version or version.data.get("appId")!=app_id or version.data.get("debug"):raise AppError("NOT_FOUND","应用版本不存在。",404)
            data=version.data
            row.data={**row.data,"publishedVersionId":version_id,"publishedName":data["name"],"publishedDescription":data["description"],"publishedNumber":data["number"],"publishedDelivery":copy.deepcopy(data.get("delivery")),"updatedAt":now()};value=copy.deepcopy(row.data);write_audit(session,ctx,"app.rollback","application",app_id);bump(session)
        return {"item":value}
    @router.post("/{app_id}/unpublish")
    def unpublish(app_id:str,ctx=Depends(context)):
        require(ctx[0],"manager")
        with session_factory().begin() as session:
            revision_lock(session);row=get_app(session,app_id)
            row.data={**row.data,"publishedVersionId":None,"updatedAt":now()};value=copy.deepcopy(row.data);write_audit(session,ctx,"app.unpublish","application",app_id);bump(session)
        return {"item":value}
    @router.post("/{app_id}/debug-conversations")
    def debug_conversation(app_id:str,ctx=Depends(context)):
        require(ctx[0],"manager")
        with session_factory().begin() as session:
            revision_lock(session);row=get_app(session,app_id);draft=normalize_draft(row.data["draft"],session)
            version=snapshot_version(session,app_id,draft,0,debug=True);put(session,"platformVersions",version["id"],version)
            value=create_platform_conversation(session,app_id,version["id"],ctx[1],purpose="debug");write_audit(session,ctx,"app.debug","application",app_id);bump(session)
        return {"conversation":value}
    @router.post("/{app_id}/conversations")
    def create_conversation(app_id:str,ctx=Depends(context)):
        with session_factory().begin() as session:
            revision_lock(session);row=get_app(session,app_id);version_id=row.data.get("publishedVersionId")
            if not version_id:raise AppError("NOT_PUBLISHED","该应用未发布。",409)
            value=create_platform_conversation(session,app_id,version_id,ctx[1]);bump(session)
        return {"conversation":value}
    return router
