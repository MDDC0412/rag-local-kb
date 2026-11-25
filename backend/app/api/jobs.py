"""任务查询接口：前端轮询进度、看错误、重试。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_session
from app.schemas.job import JobEnqueued, JobOut
from app.services import jobs as job_service
from app.workers.queue import get_queue

router = APIRouter(prefix="/api", tags=["jobs"])


@router.get("/jobs", response_model=list[JobOut])
async def list_jobs(
    status: str | None = Query(default=None, description="pending/running/done/failed"),
    type: str | None = Query(default=None, description="parse/index/reindex/tag"),
    target_id: int | None = Query(default=None, description="文档 id"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[JobOut]:
    """按条件列任务，最新的在前（前端用来显示进度与失败原因）。"""
    rows = await job_service.list_jobs(
        session, status=status, job_type=type, target_id=target_id, limit=limit
    )
    return [JobOut.model_validate(row) for row in rows]


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: int, session: AsyncSession = Depends(get_session)) -> JobOut:
    """取单个任务。"""
    return JobOut.model_validate(await job_service.get_job(session, job_id))


@router.post("/jobs/{job_id}/retry", response_model=JobEnqueued)
async def retry_job(
    job_id: int, request: Request, session: AsyncSession = Depends(get_session)
) -> JobEnqueued:
    """重跑一个失败的任务：重置尝试次数后重新排队。

    只允许重试 failed 的旧任务；运行中的任务重试会变成两份活，因此拒绝。
    """
    job = await job_service.get_job(session, job_id)
    if job.status in job_service.ACTIVE_STATUSES:
        raise AppError("JOB_ALREADY_ACTIVE", "这个任务还在队列里，不用重试", status_code=409)
    if job.status == "done":
        raise AppError("JOB_ALREADY_DONE", "这个任务已经完成了，无需重试", status_code=409)

    await job_service.reset_for_retry(session, job_id)
    await session.refresh(job)
    await get_queue(request.app).enqueue(job.id)
    return JobEnqueued(job=job, deduplicated=False)
