"""Mount domain endpoints in the real application with its existing context."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response

from ..security import AppError, require, safe_id
from . import parsing, retrieval, service, releases, evidence
from .schemas import Activate, Cancel, ChunkPreview, Confirm, Preview, RetrievalPreview, EvidencePlan


def build_router(context, session_factory):
    router = APIRouter(prefix="/api/power-quality", tags=["power-quality"])

    def operator(request: Request, ctx=Depends(context)):
        if not getattr(request.state, "identity", None):
            raise AppError("TRUSTED_IDENTITY_REQUIRED", "领域接口需要可信本机身份。", 401)
        require(ctx[0], "manager")
        return {"role": ctx[0], "session_id": ctx[1],
                "assurance": "local_account" if getattr(request.state, "identity", None) else "demo_role_unverified"}

    @router.get("/sources")
    def sources(actor=Depends(operator)):
        return parsing.scan(service.source_root())

    @router.post("/imports/preview")
    def preview(body: Preview, actor=Depends(operator)):
        with session_factory() as session, session.begin():
            return service.preview(session, body, actor)

    @router.get("/imports/{identifier}")
    def get_import(identifier: str, actor=Depends(operator)):
        with session_factory() as session:
            return service.import_view(session, safe_id(identifier))

    @router.post("/imports/{identifier}/confirm")
    def confirm(identifier: str, body: Confirm, actor=Depends(operator)):
        with session_factory() as session, session.begin():
            return service.confirm(session, safe_id(identifier), body, actor)

    @router.post("/imports/{identifier}/cancel")
    def cancel(identifier: str, body: Cancel, actor=Depends(operator)):
        with session_factory() as session, session.begin():
            return service.cancel(session, safe_id(identifier), body, actor)

    @router.get("/state")
    def state(actor=Depends(operator)):
        with session_factory() as session:
            return service.current_state(session)

    @router.get("/snapshots/{identifier}")
    def snapshot(identifier: str, actor=Depends(operator)):
        with session_factory() as session:
            return service.snapshot_view(session, safe_id(identifier))

    @router.get("/revisions/{identifier}")
    def revision(identifier: str, actor=Depends(operator)):
        with session_factory() as session:
            data = service.revision_view(session, safe_id(identifier))
            return {k: v for k, v in data.items() if k != "original_text"}

    @router.post("/snapshots/{identifier}/chunks/preview")
    def chunks(identifier: str, body: ChunkPreview, actor=Depends(operator)):
        with session_factory() as session:
            return service.chunk_preview(session, safe_id(identifier), body)

    @router.get("/revisions/{identifier}/artifact")
    def artifact(identifier: str, actor=Depends(operator)):
        with session_factory() as session:
            data = service.revision_view(session, safe_id(identifier))
            return Response(content=data["original_text"].encode("utf-8"), media_type="text/markdown; charset=utf-8",
                            headers={"ETag": '"'+data["parsed"]["sha256"]+'"', "Cache-Control": "no-store",
                                     "Content-Disposition": f'attachment; filename="{identifier}.md"'})

    @router.post("/snapshots/{identifier}/build")
    def build(identifier: str, actor=Depends(operator)):
        with session_factory() as session:
            service.snapshot_view(session, safe_id(identifier))
        raise AppError("BUILD_STAGE_NOT_ENABLED", "当前仅支持固定快照和无模型分块预览；真实构建需实施执行器并确认调用额度。", 409)

    @router.get("/builds/{identifier}/catalog")
    def build_catalog(identifier: str, actor=Depends(operator)):
        with session_factory() as session:
            return retrieval.catalog_view(session, safe_id(identifier))

    @router.post("/builds/{identifier}/retrieval/preview")
    def retrieval_preview(identifier: str, body: RetrievalPreview, actor=Depends(operator)):
        with session_factory() as session:
            return retrieval.preview(session, safe_id(identifier), body)

    @router.post("/snapshots/{identifier}/activate")
    def activate(identifier: str, body: Activate, actor=Depends(operator)):
        with session_factory() as session, session.begin():
            return releases.activate(session, safe_id(identifier), body, actor)

    @router.get("/active")
    def active(actor=Depends(operator)):
        with session_factory() as session:
            return releases.active(session)

    @router.post("/active/retrieval")
    def active_retrieval(body: RetrievalPreview, actor=Depends(operator)):
        with session_factory() as session:
            return releases.preview(session, body)

    @router.post("/evidence-runs")
    def plan_evidence(body: EvidencePlan, actor=Depends(operator)):
        with session_factory() as session, session.begin():
            return evidence.plan(session, body, actor)

    @router.get("/evidence-runs/{identifier}")
    def evidence_run(identifier: str, actor=Depends(operator)):
        with session_factory() as session:
            return evidence.view(session, safe_id(identifier))

    @router.post("/evidence-runs/{identifier}/cancel")
    def cancel_evidence(identifier: str, actor=Depends(operator)):
        with session_factory() as session, session.begin():
            return evidence.cancel(session, safe_id(identifier))

    @router.post("/evidence-runs/{identifier}/execute")
    def execute_evidence(identifier: str, actor=Depends(operator)):
        with session_factory() as session:
            evidence.view(session, safe_id(identifier))
        raise AppError("REAL_CALLS_NOT_AUTHORIZED", "当前没有真实取证额度。", 409)

    return router
