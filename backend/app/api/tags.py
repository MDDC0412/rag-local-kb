"""标签与分类接口：列表、文档标签编辑、手动触发打标签。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_session
from app.schemas.job import JobEnqueued
from app.services import documents as doc_service
from app.services import jobs as job_service
from app.services import tagging as tagging_service
from app.workers.queue import get_queue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["tags"])


class TagOut(BaseModel):
    """标签及其使用次数。"""

    id: int
    name: str
    color: str
    usage: int


class CategoryOut(BaseModel):
    """分类及文档数。"""

    id: int
    name: str
    description: str
    usage: int


class DocumentTagsOut(BaseModel):
    """某文档当前的标签。"""

    document_id: int
    tags: list[str]


class DocumentTagsIn(BaseModel):
    """手动设置标签（整体覆盖）。"""

    tags: list[str] = Field(default_factory=list, max_length=32)


@router.get("/tags", response_model=list[TagOut])
async def list_tags(session: AsyncSession = Depends(get_session)) -> list[TagOut]:
    """列出全部标签，用得多的排前面。"""
    rows = await tagging_service.list_tags(session)
    return [TagOut(id=row.id, name=row.name, color=row.color, usage=usage) for row, usage in rows]


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(session: AsyncSession = Depends(get_session)) -> list[CategoryOut]:
    """列出全部分类（受控词表）。"""
    rows = await tagging_service.list_categories(session)
    return [
        CategoryOut(id=row.id, name=row.name, description=row.description, usage=usage)
        for row, usage in rows
    ]


@router.get("/documents/{document_id}/tags", response_model=DocumentTagsOut)
async def get_document_tags(
    document_id: int, session: AsyncSession = Depends(get_session)
) -> DocumentTagsOut:
    """取一篇文档的标签。"""
    await doc_service.get_document(session, document_id)
    mapping = await tagging_service.document_tag_map(session, [document_id])
    return DocumentTagsOut(document_id=document_id, tags=mapping.get(document_id, []))


@router.put("/documents/{document_id}/tags", response_model=DocumentTagsOut)
async def put_document_tags(
    document_id: int,
    payload: DocumentTagsIn,
    session: AsyncSession = Depends(get_session),
) -> DocumentTagsOut:
    """整体替换一篇文档的标签（用户手动改）。"""
    saved = await tagging_service.set_document_tags(session, document_id, payload.tags)
    return DocumentTagsOut(document_id=document_id, tags=saved)


@router.post("/documents/{document_id}/tag", response_model=JobEnqueued)
async def trigger_tagging(
    document_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JobEnqueued:
    """手动触发打标签（自动流程是入库完成后排队；这里给用户重跑用）。"""
    await doc_service.get_document(session, document_id)

    job, created = await job_service.create_job(session, "tag", document_id)
    if not created and job.status == "running":
        raise AppError("TAG_IN_PROGRESS", "这篇文档正在打标签，请稍等", status_code=409)
    if created:
        await get_queue(request.app).enqueue(job.id)
    return JobEnqueued(job=job, deduplicated=not created)
