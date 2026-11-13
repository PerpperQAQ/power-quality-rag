"""Minimal local operation audit; never store credentials or request bodies."""
from uuid import uuid4
from .state import now, put

def write_audit(session,ctx,action,object_type,object_id):
    identifier="audit-"+uuid4().hex
    value={"id":identifier,"action":action,"objectType":object_type,"objectId":object_id,"actorRole":ctx[0],"actorSessionId":ctx[1],"createdAt":now()}
    put(session,"platformAudit",identifier,value)
