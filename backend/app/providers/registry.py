"""从数据库行构造运行时 ProviderSpec，并解析默认供应商。"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import PROVIDER_NOT_FOUND, AppError
from app.core.security import SecretBox
from app.db.models import ProviderConfig
from app.providers.base import ProviderSpec

logger = logging.getLogger(__name__)

_box: SecretBox | None = None


def secret_box() -> SecretBox:
    """全局加解密器（懒加载）。"""
    global _box
    if _box is None:
        _box = SecretBox(get_settings().secret_path)
    return _box


def _parse_params(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def to_spec(row: ProviderConfig) -> ProviderSpec:
    """数据库行 → 运行时描述（解密 key）。"""
    return ProviderSpec(
        kind=row.kind,
        name=row.name,
        base_url=row.base_url,
        api_key=secret_box().decrypt(row.api_key_enc),
        model=row.model,
        protocol=row.protocol,
        params=_parse_params(row.params_json),
    )


async def get_provider(session: AsyncSession, provider_id: int) -> ProviderConfig:
    """按 id 取供应商，不存在则报错。"""
    row = await session.get(ProviderConfig, provider_id)
    if row is None:
        raise AppError(PROVIDER_NOT_FOUND, f"供应商 #{provider_id} 不存在", status_code=404)
    return row


async def resolve_spec(
    session: AsyncSession, kind: str, provider_id: int | None = None
) -> ProviderSpec:
    """取指定供应商；未指定时取该 kind 的默认（或第一个启用的）。"""
    if provider_id is not None:
        return to_spec(await get_provider(session, provider_id))

    stmt = (
        select(ProviderConfig)
        .where(ProviderConfig.kind == kind, ProviderConfig.enabled.is_(True))
        .order_by(ProviderConfig.is_default.desc(), ProviderConfig.id.asc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise AppError(
            PROVIDER_NOT_FOUND,
            f"还没有可用的「{kind}」供应商，请先到「设置 → 模型供应商」里添加",
            status_code=400,
        )
    return to_spec(row)
