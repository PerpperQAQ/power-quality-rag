from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, Computed, DateTime, Float, Index, Integer, String, Text, create_engine, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from pgvector.sqlalchemy import Vector
from .config import settings

class Base(DeclarativeBase): pass

DOCUMENT_SUMMARY_SQL = """CASE WHEN collection='documents' THEN
    jsonb_set(data - ARRAY['content','segments','filePath','sha256','_buildToken'],
      '{metadataExtraction}',
      (coalesce(data->'metadataExtraction','{}'::jsonb) - ARRAY['facts','keywords','questions','lastSuccess'])
       || '{"facts":[],"keywords":[],"questions":[]}'::jsonb)
    || '{"content":"","segments":[],"detailsOmitted": true}'::jsonb
    ELSE NULL END"""

class Record(Base):
    __tablename__ = "records"
    collection: Mapped[str] = mapped_column(String(32), primary_key=True)
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(160), default="", index=True)
    data: Mapped[dict] = mapped_column(JSONB)
    summary: Mapped[dict | None] = mapped_column(JSONB,Computed(DOCUMENT_SUMMARY_SQL,persisted=True),nullable=True)

class Revision(Base):
    __tablename__ = "revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    value: Mapped[int] = mapped_column(BigInteger, default=0)

class Secret(Base):
    __tablename__ = "secrets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text)

class Chunk(Base):
    __tablename__ = "chunks"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(160), index=True)
    kb_id: Mapped[str] = mapped_column(String(160), index=True)
    model: Mapped[str] = mapped_column(String(160), index=True)
    dimension: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean,default=True,server_default=text("true"))
    embedding: Mapped[list] = mapped_column(Vector())
    content: Mapped[str] = mapped_column(Text)
    lexemes: Mapped[str] = mapped_column(Text)
    search_vector: Mapped[str] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', lexemes)", persisted=True))
    payload: Mapped[dict] = mapped_column(JSONB)

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(160), index=True)
    session_id: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda:datetime.now(timezone.utc))

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

def initialize():
    with engine.begin() as con:
        con.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)
    with engine.begin() as con:
        con.execute(text(f"ALTER TABLE records ADD COLUMN IF NOT EXISTS summary jsonb GENERATED ALWAYS AS ({DOCUMENT_SUMMARY_SQL}) STORED"))
        has_enabled=con.scalar(text("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='chunks' AND column_name='enabled')"))
        if not has_enabled:
            con.execute(text("ALTER TABLE chunks ADD COLUMN enabled boolean NOT NULL DEFAULT true"))
            con.execute(text("UPDATE chunks SET enabled=false WHERE NOT COALESCE((payload->>'enabled')::boolean,true)"))
        con.execute(text("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple',lexemes)) STORED"))
        con.execute(text("CREATE INDEX IF NOT EXISTS chunks_search_vector_idx ON chunks USING gin (search_vector)"))
        con.execute(text("CREATE INDEX IF NOT EXISTS chunks_kb_dimension_idx ON chunks (kb_id,dimension)"))
        con.execute(text("INSERT INTO revisions(id,value) VALUES(1,0) ON CONFLICT DO NOTHING"))
        # Separate expression indexes support each real vector dimension while
        # the table continues to accept multiple models/dimensions. Small file
        # scopes still use exact distance search in the retrieval service.
        dimensions={1024,*con.execute(text("SELECT DISTINCT dimension FROM chunks WHERE dimension BETWEEN 1 AND 2000")).scalars()}
        con.execute(text("SET LOCAL maintenance_work_mem='256MB'"))
        for dimension in sorted(dimensions):
            dimension=int(dimension)
            con.execute(text(f"CREATE INDEX IF NOT EXISTS chunks_cosine_{dimension}_hnsw ON chunks USING hnsw ((embedding::vector({dimension})) vector_cosine_ops) WHERE dimension={dimension} AND COALESCE((payload->>'enabled')::boolean,true)"))

def bump(session):
    rev = session.get(Revision, 1, with_for_update=True)
    rev.value += 1
    return rev.value


def revision_lock(session):
    """Acquire the revision row first so background publication and PATCH agree.

    This provides a consistent lock order for the local single-workspace store.
    """
    return session.get(Revision,1,with_for_update=True)
