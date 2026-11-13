from pathlib import Path
import hashlib
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://zhixu:zhixu@127.0.0.1:5433/zhixu"
    data_dir: Path = Path(__file__).resolve().parents[1] / "data"
    worker_in_api: bool = True
    cors_origins: list[str] = ["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:4173", "http://localhost:4173"]
    max_upload_mb: int = 50
    chat_timeout_seconds: float = 90
    runtime_home: Path | None = None
    # Opt-in, trusted local source root. The API never accepts a filesystem root
    # from a request and never writes back to this directory.
    power_quality_source_root: Path | None = None
    power_quality_local_mode: bool = False
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[1] / ".env", extra="ignore")

settings = Settings()
settings.data_dir = settings.data_dir.resolve()
for folder in (settings.data_dir, settings.data_dir / "files", settings.data_dir / "jobs"):
    folder.mkdir(parents=True, exist_ok=True)

def storage_instance():
    return hashlib.sha256(str(settings.data_dir).encode()).hexdigest()[:16]
