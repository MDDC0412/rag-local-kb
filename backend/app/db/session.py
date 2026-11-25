"""异步引擎与会话工厂（SQLite + aiosqlite）。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db.models import Base

_settings = get_settings()

engine = create_async_engine(
    f"sqlite+aiosqlite:///{_settings.db_path}",
    echo=False,
    future=True,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """建表（开发期用 create_all，改表写 scripts/migrate_*.py）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite 默认不开启外键约束，显式打开
        await conn.exec_driver_sql("PRAGMA foreign_keys=ON")


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每个请求一个 session。"""
    async with SessionLocal() as session:
        yield session
