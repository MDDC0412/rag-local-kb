"""提示词模板库与设置接口。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.prompt import (
    PromptKindGroup,
    PromptTemplateCreate,
    PromptTemplateOut,
    PromptTemplateUpdate,
    SettingsIn,
    SettingsOut,
)
from app.services import prompts as prompt_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["prompts"])


# --------------------------------------------------------------------------- #
# 提示词模板
# --------------------------------------------------------------------------- #
@router.get("/prompt-templates", response_model=list[PromptKindGroup])
async def list_templates(
    kind: str | None = Query(default=None, description="按类型过滤"),
    session: AsyncSession = Depends(get_session),
) -> list[PromptKindGroup]:
    """按 kind 分组列出所有提示词模板。"""
    groups = await prompt_service.grouped_templates(session)
    if kind:
        groups = [group for group in groups if group["kind"] == kind]
    return [PromptKindGroup.model_validate(group) for group in groups]


@router.post("/prompt-templates", response_model=PromptTemplateOut, status_code=201)
async def create_template(
    payload: PromptTemplateCreate, session: AsyncSession = Depends(get_session)
) -> PromptTemplateOut:
    """新建提示词模板。"""
    row = await prompt_service.create_template(session, payload)
    return PromptTemplateOut.model_validate(row)


@router.post("/prompt-templates/{template_id}/duplicate", response_model=PromptTemplateOut)
async def duplicate_template(
    template_id: int, session: AsyncSession = Depends(get_session)
) -> PromptTemplateOut:
    """另存为副本（内置模板要改，必须走这条）。"""
    row = await prompt_service.duplicate_template(session, template_id)
    return PromptTemplateOut.model_validate(row)


@router.put("/prompt-templates/{template_id}", response_model=PromptTemplateOut)
async def update_template(
    template_id: int,
    payload: PromptTemplateUpdate,
    session: AsyncSession = Depends(get_session),
) -> PromptTemplateOut:
    """修改模板。内置模板返回 409，需先另存副本。"""
    row = await prompt_service.update_template(session, template_id, payload)
    return PromptTemplateOut.model_validate(row)


@router.post("/prompt-templates/{template_id}/reset", response_model=PromptTemplateOut)
async def reset_template(
    template_id: int, session: AsyncSession = Depends(get_session)
) -> PromptTemplateOut:
    """把模板内容恢复成内置原文。"""
    row = await prompt_service.reset_template(session, template_id)
    return PromptTemplateOut.model_validate(row)


@router.delete("/prompt-templates/{template_id}", status_code=204)
async def delete_template(template_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """删除自定义模板（内置不可删）。"""
    await prompt_service.delete_template(session, template_id)


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #
@router.get("/settings", response_model=SettingsOut)
async def get_settings(session: AsyncSession = Depends(get_session)) -> SettingsOut:
    """读全部设置（缺的用默认值补齐）。"""
    return SettingsOut(
        items=await prompt_service.get_settings_map(session),
        defaults=prompt_service.default_settings(),
    )


@router.put("/settings", response_model=SettingsOut)
async def update_settings(
    payload: SettingsIn, session: AsyncSession = Depends(get_session)
) -> SettingsOut:
    """批量写设置。只接受白名单里的键。"""
    items = await prompt_service.update_settings(session, payload.items)
    return SettingsOut(items=items, defaults=prompt_service.default_settings())
