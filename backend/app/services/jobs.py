"""后台任务（task_jobs）的数据库操作：入队去重、状态流转、重启恢复。

任务真正的执行在 ``workers/queue.py``；这里只管数据库里的那一行记录，
让接口能查进度、让进程重启后能接上没做完的活。

状态机：``pending → running → done``，失败可退回 ``pending`` 重试，
超过 ``MAX_ATTEMPTS`` 次才落 ``failed``。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NOT_FOUND, AppError
from app.db.models import TaskJob

logger = logging.getLogger(__name__)

# 进行中的状态：这两个状态下同类型同目标不重复建任务
ACTIVE_STATUSES = ("pending", "running")

MAX_ATTEMPTS = 3

_ERROR_MSG_LIMIT = 500


def _now() -> datetime:
    """带时区的当前 UTC 时间。"""
    return datetime.now(UTC)


def _trim(message: str) -> str:
    """错误信息截断，避免把整段堆栈塞进数据库。"""
    text = (message or "").strip()
    return text[:_ERROR_MSG_LIMIT]


async def create_job(
    session: AsyncSession,
    job_type: str,
    target_id: int | None,
    *,
    dedupe: bool = True,
) -> tuple[TaskJob, bool]:
    """建一个待执行任务，返回 ``(任务, 是否新建)``。

    ``dedupe=True`` 时，同类型同目标已有 pending/running 的任务就直接复用它：
    用户连点两次「确认入库」不应该产生两份活。
    """
    if dedupe:
        existing = (
            await session.execute(
                select(TaskJob)
                .where(
                    TaskJob.type == job_type,
                    TaskJob.target_id == target_id,
                    TaskJob.status.in_(ACTIVE_STATUSES),
                )
                .order_by(TaskJob.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

    job = TaskJob(type=job_type, target_id=target_id, status="pending", progress=0, attempts=0)
    session.add(job)
    await session.commit()
    await session.refresh(job)
    logger.info("任务 #%s 已创建：%s target=%s", job.id, job_type, target_id)
    return job, True


async def get_job(session: AsyncSession, job_id: int) -> TaskJob:
    """取任务；不存在报 404。"""
    job = await session.get(TaskJob, job_id)
    if job is None:
        raise AppError(NOT_FOUND, f"任务 #{job_id} 不存在", status_code=404)
    return job


async def list_jobs(
    session: AsyncSession,
    *,
    status: str | None = None,
    job_type: str | None = None,
    target_id: int | None = None,
    limit: int = 50,
) -> list[TaskJob]:
    """按条件列任务，最新的在前。"""
    stmt = select(TaskJob)
    if status:
        stmt = stmt.where(TaskJob.status == status)
    if job_type:
        stmt = stmt.where(TaskJob.type == job_type)
    if target_id is not None:
        stmt = stmt.where(TaskJob.target_id == target_id)
    stmt = stmt.order_by(TaskJob.id.desc()).limit(max(1, min(limit, 200)))
    return list((await session.execute(stmt)).scalars().all())


async def claim(session: AsyncSession, job_id: int) -> TaskJob | None:
    """把一个 pending 任务领成 running，返回领到的任务。

    用带条件的 UPDATE 保证原子性：两个 worker 抢同一条时只有一个能领到。
    领不到（已被领走 / 已完成 / 已取消）返回 None。
    """
    result = await session.execute(
        update(TaskJob)
        .where(TaskJob.id == job_id, TaskJob.status == "pending")
        .values(
            status="running",
            started_at=_now(),
            progress=0,
            error_msg="",
            attempts=TaskJob.attempts + 1,
        )
    )
    await session.commit()
    if result.rowcount != 1:
        return None
    return await session.get(TaskJob, job_id)


async def set_progress(session: AsyncSession, job_id: int, percent: int) -> None:
    """更新进度（0–100），只对 running 的任务生效。"""
    value = max(0, min(100, int(percent)))
    await session.execute(
        update(TaskJob)
        .where(TaskJob.id == job_id, TaskJob.status == "running")
        .values(progress=value)
    )
    await session.commit()


async def mark_done(session: AsyncSession, job_id: int) -> None:
    """标记完成。"""
    await session.execute(
        update(TaskJob)
        .where(TaskJob.id == job_id)
        .values(status="done", progress=100, finished_at=_now(), error_msg="")
    )
    await session.commit()


async def mark_retry(session: AsyncSession, job_id: int, message: str) -> None:
    """失败但还能重试：退回 pending，保留错误信息给界面看。"""
    await session.execute(
        update(TaskJob)
        .where(TaskJob.id == job_id)
        .values(status="pending", started_at=None, progress=0, error_msg=_trim(message))
    )
    await session.commit()


async def reset_for_retry(session: AsyncSession, job_id: int) -> None:
    """用户手动重试：退回 pending 并**清零尝试次数**。

    自动重试用 ``mark_retry``（次数继续累加，到上限就放弃）；
    手动重试是用户的明确意愿，必须把次数清掉，否则领到手立刻又判失败。
    """
    await session.execute(
        update(TaskJob)
        .where(TaskJob.id == job_id)
        .values(status="pending", attempts=0, started_at=None, progress=0, error_msg="")
    )
    await session.commit()


async def mark_failed(session: AsyncSession, job_id: int, message: str) -> None:
    """彻底失败（重试次数用尽或错误不可重试）。"""
    await session.execute(
        update(TaskJob)
        .where(TaskJob.id == job_id)
        .values(status="failed", finished_at=_now(), error_msg=_trim(message))
    )
    await session.commit()


async def requeue_interrupted(session: AsyncSession) -> int:
    """进程重启时把 running 的任务打回 pending，返回受影响条数。

    上次进程是在任务跑到一半时被关掉的，这些任务没有启动者了，
    重置后重新入队（处理器必须幂等，先清旧数据再写）。
    """
    result = await session.execute(
        update(TaskJob)
        .where(TaskJob.status == "running")
        .values(status="pending", started_at=None, progress=0)
    )
    await session.commit()
    count = result.rowcount or 0
    if count:
        logger.warning("重启恢复：%s 个中断的任务已重新排队", count)
    return count


async def list_pending_ids(session: AsyncSession, limit: int = 200) -> list[int]:
    """列出所有待执行任务的 id（启动时重新入队用）。"""
    rows = (
        await session.execute(
            select(TaskJob.id).where(TaskJob.status == "pending").order_by(TaskJob.id).limit(limit)
        )
    ).scalars()
    return list(rows)
