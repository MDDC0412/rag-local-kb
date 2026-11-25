"""供应商 CRUD 与连通性测试。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PROVIDER_INVALID, AppError
from app.core.logging import mask_secret
from app.db.models import ProviderConfig
from app.db.session import get_session
from app.providers import base as provider_base
from app.providers.base import ProviderSpec
from app.providers.registry import get_provider, secret_box
from app.schemas.provider import (
    ProviderIn,
    ProviderOut,
    ProviderTestIn,
    ProviderTestOut,
    ProviderUpdate,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/providers", tags=["providers"])

DEFAULT_PROTOCOL = {
    "llm": "openai",
    "embedding": "openai",
    "rerank": "siliconflow_rerank",
}


def _to_out(row: ProviderConfig) -> ProviderOut:
    """行 → 响应（密钥打码）。"""
    plain = secret_box().decrypt(row.api_key_enc)
    try:
        params = json.loads(row.params_json or "{}")
    except json.JSONDecodeError:
        params = {}
    return ProviderOut(
        id=row.id,
        kind=row.kind,
        name=row.name,
        base_url=row.base_url,
        model=row.model,
        protocol=row.protocol,
        params=params,
        is_default=row.is_default,
        enabled=row.enabled,
        has_api_key=bool(plain),
        api_key_masked=mask_secret(plain),
        created_at=row.created_at,
    )


async def _clear_defaults(session: AsyncSession, kind: str, keep_id: int | None = None) -> None:
    """同 kind 下只保留一个默认。"""
    stmt = update(ProviderConfig).where(ProviderConfig.kind == kind).values(is_default=False)
    if keep_id is not None:
        stmt = stmt.where(ProviderConfig.id != keep_id)
    await session.execute(stmt)


@router.get("", response_model=list[ProviderOut])
async def list_providers(
    kind: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[ProviderOut]:
    """列出供应商，可按 kind 过滤。"""
    stmt = select(ProviderConfig).order_by(ProviderConfig.kind, ProviderConfig.id)
    if kind:
        stmt = stmt.where(ProviderConfig.kind == kind)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_out(row) for row in rows]


@router.post("", response_model=ProviderOut, status_code=201)
async def create_provider(
    payload: ProviderIn, session: AsyncSession = Depends(get_session)
) -> ProviderOut:
    """新建供应商；is_default 为真时清掉同 kind 的其它默认。"""
    protocol = payload.protocol or DEFAULT_PROTOCOL.get(payload.kind, "openai")
    if payload.is_default:
        await _clear_defaults(session, payload.kind)

    row = ProviderConfig(
        kind=payload.kind,
        name=payload.name.strip(),
        base_url=payload.base_url,
        api_key_enc=secret_box().encrypt(payload.api_key),
        model=payload.model.strip(),
        protocol=protocol,
        params_json=json.dumps(payload.params, ensure_ascii=False),
        is_default=payload.is_default,
        enabled=payload.enabled,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    logger.info("新增供应商 #%s %s(%s) model=%s", row.id, row.name, row.kind, row.model)
    return _to_out(row)


@router.put("/{provider_id}", response_model=ProviderOut)
async def update_provider(
    provider_id: int, payload: ProviderUpdate, session: AsyncSession = Depends(get_session)
) -> ProviderOut:
    """更新供应商；api_key 传空则保持不变。"""
    row = await get_provider(session, provider_id)

    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.base_url is not None:
        row.base_url = payload.base_url.rstrip("/")
    if payload.model is not None:
        row.model = payload.model.strip()
    if payload.protocol is not None:
        row.protocol = payload.protocol
    if payload.params is not None:
        row.params_json = json.dumps(payload.params, ensure_ascii=False)
    if payload.enabled is not None:
        row.enabled = payload.enabled
    if payload.api_key:
        row.api_key_enc = secret_box().encrypt(payload.api_key)
    if payload.is_default is not None:
        row.is_default = payload.is_default
        if payload.is_default:
            await _clear_defaults(session, row.kind, keep_id=row.id)

    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.delete("/{provider_id}", status_code=204)
async def delete_provider(provider_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """删除供应商。"""
    row = await get_provider(session, provider_id)
    await session.delete(row)
    await session.commit()


@router.post("/{provider_id}/test", response_model=ProviderTestOut)
async def test_provider(
    provider_id: int, session: AsyncSession = Depends(get_session)
) -> ProviderTestOut:
    """已保存供应商的连通性测试。"""
    row = await get_provider(session, provider_id)
    spec = ProviderSpec(
        kind=row.kind,
        name=row.name,
        base_url=row.base_url,
        api_key=secret_box().decrypt(row.api_key_enc),
        model=row.model,
        protocol=row.protocol,
        params=json.loads(row.params_json or "{}"),
    )
    return await _run_probe(spec)


@router.post("/test", response_model=ProviderTestOut)
async def test_provider_draft(payload: ProviderTestIn) -> ProviderTestOut:
    """未保存草稿的连通性测试（填表时就能点"测试"）。"""
    protocol = payload.protocol or DEFAULT_PROTOCOL.get(payload.kind, "openai")
    spec = ProviderSpec(
        kind=payload.kind,
        name=payload.name,
        base_url=payload.base_url.rstrip("/"),
        api_key=payload.api_key,
        model=payload.model,
        protocol=protocol,
        params=payload.params,
    )
    return await _run_probe(spec)


@router.get("/{provider_id}/models", response_model=list[str])
async def list_models(provider_id: int, session: AsyncSession = Depends(get_session)) -> list[str]:
    """拉取该供应商可用模型列表。"""
    row = await get_provider(session, provider_id)
    spec = ProviderSpec(
        kind=row.kind,
        name=row.name,
        base_url=row.base_url,
        api_key=secret_box().decrypt(row.api_key_enc),
        model=row.model,
        protocol=row.protocol,
        params=json.loads(row.params_json or "{}"),
    )
    result = await provider_base.probe_llm(spec)
    return result["models"]


async def _run_probe(spec: ProviderSpec) -> ProviderTestOut:
    """执行探测并转成统一响应；失败也返回结构化结果而不是 500。"""
    if not spec.base_url:
        raise AppError(PROVIDER_INVALID, "请先填写 base_url")
    if not spec.api_key:
        raise AppError(PROVIDER_INVALID, "请先填写 API Key")

    result = await provider_base.probe(spec)
    latency = int(result.get("latency_ms", 0))

    if spec.kind == "llm":
        message = f"连接成功，发现 {result['model_count']} 个模型（{latency} ms）"
        if result.get("model_available") is False:
            message += f"；但当前模型 {spec.model} 不在列表中，请确认模型名"
        return ProviderTestOut(
            ok=True,
            kind=spec.kind,
            latency_ms=latency,
            message=message,
            models=result.get("models", []),
            model_count=result.get("model_count", 0),
            model_available=result.get("model_available"),
        )

    if spec.kind == "embedding":
        return ProviderTestOut(
            ok=True,
            kind=spec.kind,
            latency_ms=latency,
            message=f"连接成功，向量维度 {result['dim']}（{latency} ms）",
            dim=result.get("dim"),
        )

    sanity = result.get("sanity_passed")
    message = f"重排接口可用（{latency} ms）"
    if sanity is False:
        message += "；但相关性排序异常，请检查 protocol 是否选对"
    return ProviderTestOut(
        ok=True,
        kind=spec.kind,
        latency_ms=latency,
        message=message,
        results=result.get("results", []),
        sanity_passed=sanity,
        protocol=result.get("protocol"),
    )
