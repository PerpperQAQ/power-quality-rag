"""Closed request schemas. Build states and source metadata are server-owned."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

Hash = Annotated[StrictStr, Field(pattern=r"^[a-f0-9]{64}$")]
RequestId = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_.:-]{1,100}$")]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False)


class SourceFile(Request):
    relative_path: Annotated[StrictStr, Field(alias="relativePath", min_length=1, max_length=600)]
    sha256: Hash
    content: Annotated[StrictStr | None, Field(max_length=2 * 1024 * 1024)] = None


class Preview(Request):
    request_id: RequestId = Field(alias="requestId")
    files: Annotated[list[SourceFile], Field(min_length=1, max_length=1000)]


class Confirm(Request):
    request_id: RequestId = Field(alias="requestId")
    manifest_hash: Hash = Field(alias="manifestHash")
    acknowledge_warnings: StrictBool = Field(alias="acknowledgeWarnings", default=False)


class Cancel(Request):
    request_id: RequestId = Field(alias="requestId")


class Activate(Request):
    request_id: RequestId = Field(alias="requestId")
    expected_active_snapshot_id: StrictStr | None = Field(alias="expectedActiveSnapshotId")


class ChunkPreview(Request):
    manifest_hash: Hash = Field(alias="manifestHash")
    target_characters: Literal[600, 900] = Field(alias="targetCharacters", default=900)


class RetrievalPreview(Request):
    query: Annotated[StrictStr, Field(min_length=1, max_length=2000)]
    industry_ids: Annotated[list[StrictStr], Field(max_length=9)] = Field(alias="industryIds", default_factory=list)
    entry_ids: Annotated[list[StrictStr], Field(max_length=12)] = Field(alias="entryIds", default_factory=list)
    object_name: Annotated[StrictStr | None, Field(max_length=200)] = Field(alias="objectName", default=None)
    scope_mode: Literal["auto", "compare"] = Field(alias="scopeMode", default="auto")
    vector_probe_chunk_id: StrictStr | None = Field(alias="vectorProbeChunkId", default=None)
    top_k: Annotated[StrictInt, Field(ge=1, le=20)] = Field(alias="topK", default=5)
    candidate_k: Annotated[StrictInt, Field(ge=1, le=30)] = Field(alias="candidateK", default=20)

    @model_validator(mode="after")
    def bounds(self):
        self.query = self.query.strip()
        if not self.query or self.candidate_k < self.top_k or self.object_name is not None and not self.object_name.strip():
            raise ValueError("empty query/object or candidateK below topK")
        return self


class EvidencePlan(Request):
    request_id: RequestId = Field(alias='requestId')
    retrieval: RetrievalPreview
    reason: Literal['missing_source', 'missing_model_manual', 'public_conditions']
