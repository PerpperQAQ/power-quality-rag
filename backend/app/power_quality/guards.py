"""Keep immutable domain records out of legacy mutable ingestion endpoints."""
from ..security import AppError


def require_generic_mutable(data):
    if data and (data.get("domain") == "power_quality" or data.get("pqRevisionId") or data.get("pqSnapshotId")):
        raise AppError("DOMAIN_IMMUTABLE", "电能质量快照原件不可通过通用接口修改；请重新预览并确认快照。", 409)
