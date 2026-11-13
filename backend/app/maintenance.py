"""Coordinate online backups with API mutations and durable worker jobs."""
import fcntl
from contextlib import contextmanager
from fastapi.responses import JSONResponse
from .config import settings, storage_instance


class MaintenanceBusy(Exception):
    pass


@contextmanager
def mutation_guard():
    path = settings.data_dir / ".maintenance.lock"
    with path.open("a+b") as stream:
        path.chmod(0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise MaintenanceBusy() from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class MaintenanceMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PATCH", "DELETE", "PUT"}:
            return await self.app(scope, receive, send)
        instance=dict(scope.get("headers",[])).get(b"x-state-instance",b"").decode("ascii",errors="ignore")
        if instance and instance!=storage_instance():
            response=JSONResponse({"error":{"code":"STATE_REPLACED","message":"资料已切换至恢复副本，请刷新资料后重新操作。"}},status_code=409)
            return await response(scope,receive,send)
        try:
            with mutation_guard():
                return await self.app(scope, receive, send)
        except MaintenanceBusy:
            response = JSONResponse(
                {"error": {"code": "BACKUP_IN_PROGRESS", "message": "正在保存本机备份，请稍后重试。已保存内容不受影响。"}},
                status_code=503, headers={"Retry-After": "5"},
            )
            await response(scope, receive, send)
