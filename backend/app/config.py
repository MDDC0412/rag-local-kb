"""全局配置：从 .env 读取，路径统一以仓库根目录为基准。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py -> app -> backend -> 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用配置。字段名对应 .env 中的大写键名（大小写不敏感）。"""

    model_config = SettingsConfigDict(
        # 用绝对路径，避免从 backend/ 启动时读不到仓库根的 .env
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_data_dir: str = ""
    app_log_level: str = "INFO"
    app_cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8080"

    # 开发种子：首次启动时写入数据库（加密），之后以数据库为准
    dev_llm_base_url: str = ""
    dev_llm_api_key: str = ""
    dev_llm_model: str = ""
    dev_embed_base_url: str = ""
    dev_embed_api_key: str = ""
    dev_embed_model: str = ""
    dev_rerank_base_url: str = ""
    dev_rerank_api_key: str = ""
    dev_rerank_model: str = ""

    # 检索默认参数
    retrieval_vector_top_n: int = 20
    retrieval_bm25_top_n: int = 20
    retrieval_fuse_top_n: int = 20
    retrieval_rerank_top_n: int = 5
    retrieval_rrf_k: int = 60
    chat_context_token_budget: int = 3000

    @property
    def data_dir(self) -> Path:
        """数据根目录（uploads / chroma / app.db / logs / .secret 都在其下）。"""
        raw = self.app_data_dir.strip()
        path = Path(raw) if raw else REPO_ROOT / "data"
        if not path.is_absolute():
            path = (REPO_ROOT / path).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def uploads_dir(self) -> Path:
        path = self.data_dir / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def chroma_dir(self) -> Path:
        path = self.data_dir / "chroma"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def logs_dir(self) -> Path:
        path = self.data_dir / "logs"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / ".secret"

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.app_cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """单例配置。"""
    return Settings()
