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
    # 外网代理：留空=自动读取 Windows 系统代理；填 direct=不走代理；填 http://host:port=强制指定
    app_http_proxy: str = ""

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
        """数据根目录（uploads / chroma / app.db / logs / .secret 都在其下）。

        相对路径一律相对**仓库根**解析，不相对进程 CWD：
        否则从 backend/ 启动时 "../data" 会跑到项目外面去（踩过这个坑）。
        """
        raw = self.app_data_dir.strip()
        if not raw:
            path = REPO_ROOT / "data"
        else:
            candidate = Path(raw)
            if candidate.is_absolute():
                path = candidate
            else:
                # 允许 "data" 或 "./data" 这类写法，统一挂到仓库根下
                normalized = Path(*[p for p in candidate.parts if p not in (".", "..")])
                path = REPO_ROOT / normalized
        path.mkdir(parents=True, exist_ok=True)
        return path.resolve()

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

    @property
    def http_proxy(self) -> str | None:
        """外网代理地址；默认直连（返回 None）。

        踩过的坑：httpx 的 ``trust_env=True``（默认）在 Windows 上会去读**注册表里的系统代理**。
        本机注册表指向 127.0.0.1:7890，但那个代理自身 TLS 握手是坏的，于是所有请求都失败在
        一个和代码无关的地方。所以这里一律「显式才算数」，默认直连。

        需要走代理时（例如直连 OpenAI 官方被拦），在 .env 里写：
            APP_HTTP_PROXY=http://127.0.0.1:7890
        """
        raw = (self.app_http_proxy or "").strip()
        if not raw or raw.lower() == "direct":
            return None
        if not raw.startswith(("http://", "https://", "socks5://")):
            raw = f"http://{raw}"
        return raw


@lru_cache
def get_settings() -> Settings:
    """单例配置。"""
    return Settings()
