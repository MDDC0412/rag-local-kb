"""任务队列与 task_jobs 状态机的测试。

重点覆盖三个曾经会咬人的点：
1. 连点两次「确认入库」不能产生两份活（去重）。
2. 进程重启后 running 的任务必须被打回 pending，否则任务永远卡住。
3. 两个 worker 抢同一条任务时只能有一个领到（claim 的原子性）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import AppError
from app.db.models import Base, TaskJob
from app.services import jobs as job_service


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """每个测试一个独立的内存库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_job_then_dedupe(session: AsyncSession) -> None:
    """同类型同目标重复创建时复用已有任务，不产生第二份活。"""
    first, created = await job_service.create_job(session, "index", 7)
    assert created is True

    second, created_again = await job_service.create_job(session, "index", 7)
    assert created_again is False
    assert second.id == first.id


@pytest.mark.asyncio
async def test_create_job_dedupe_is_per_type(session: AsyncSession) -> None:
    """去重只约束同类型：index 和 tag 可以同时挂在同一篇文档上。"""
    index_job, _ = await job_service.create_job(session, "index", 7)
    tag_job, created = await job_service.create_job(session, "tag", 7)
    assert created is True
    assert tag_job.id != index_job.id


@pytest.mark.asyncio
async def test_finished_job_does_not_block_new_one(session: AsyncSession) -> None:
    """已完成的任务不算「进行中」，新任务应正常创建。"""
    first, _ = await job_service.create_job(session, "index", 7)
    await job_service.mark_done(session, first.id)

    again, created = await job_service.create_job(session, "index", 7)
    assert created is True
    assert again.id != first.id


@pytest.mark.asyncio
async def test_claim_is_exclusive(session: AsyncSession) -> None:
    """同一条 pending 任务只能被领走一次。"""
    job, _ = await job_service.create_job(session, "index", 1)

    first = await job_service.claim(session, job.id)
    second = await job_service.claim(session, job.id)

    assert first is not None
    assert first.status == "running"
    assert first.attempts == 1
    assert second is None


@pytest.mark.asyncio
async def test_requeue_interrupted_resets_running_jobs(session: AsyncSession) -> None:
    """重启恢复：running → pending，进度清零，待执行的任务保持不动。"""
    running, _ = await job_service.create_job(session, "index", 1)
    await job_service.claim(session, running.id)
    waiting, _ = await job_service.create_job(session, "index", 2)

    count = await job_service.requeue_interrupted(session)

    assert count == 1
    refreshed = await session.get(TaskJob, running.id)
    assert refreshed is not None
    assert refreshed.status == "pending"
    assert refreshed.progress == 0
    assert refreshed.started_at is None

    untouched = await session.get(TaskJob, waiting.id)
    assert untouched is not None
    assert untouched.status == "pending"


@pytest.mark.asyncio
async def test_retry_then_fail_after_max_attempts(session: AsyncSession) -> None:
    """失败先退回 pending 重试，重试次数用尽才落 failed。"""
    job, _ = await job_service.create_job(session, "index", 1)

    for attempt in range(1, job_service.MAX_ATTEMPTS + 1):
        claimed = await job_service.claim(session, job.id)
        assert claimed is not None
        assert claimed.attempts == attempt
        if attempt < job_service.MAX_ATTEMPTS:
            await job_service.mark_retry(session, job.id, "供应商连接失败")
            back = await session.get(TaskJob, job.id)
            assert back is not None
            assert back.status == "pending"
            assert back.error_msg == "供应商连接失败"

    await job_service.mark_failed(session, job.id, "重试用尽")
    final = await session.get(TaskJob, job.id)
    assert final is not None
    assert final.status == "failed"
    assert final.finished_at is not None


@pytest.mark.asyncio
async def test_progress_only_applies_to_running(session: AsyncSession) -> None:
    """进度只对 running 生效，避免已结束的任务被改动。"""
    job, _ = await job_service.create_job(session, "index", 1)
    await job_service.set_progress(session, job.id, 40)
    pending = await session.get(TaskJob, job.id)
    assert pending is not None
    assert pending.progress == 0

    await job_service.claim(session, job.id)
    await job_service.set_progress(session, job.id, 40)
    await session.refresh(job)
    assert job.progress == 40


@pytest.mark.asyncio
async def test_progress_is_clamped(session: AsyncSession) -> None:
    """进度超界要夹紧，不能写进 120% 这种值。"""
    job, _ = await job_service.create_job(session, "index", 1)
    await job_service.claim(session, job.id)

    await job_service.set_progress(session, job.id, 250)
    await session.refresh(job)
    assert job.progress == 100

    await job_service.set_progress(session, job.id, -5)
    await session.refresh(job)
    assert job.progress == 0


@pytest.mark.asyncio
async def test_get_missing_job_raises(session: AsyncSession) -> None:
    """查不存在的任务报 404，而不是返回 None 让上层崩掉。"""
    with pytest.raises(AppError) as exc:
        await job_service.get_job(session, 999)
    assert exc.value.code == "NOT_FOUND"
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_list_jobs_filters(session: AsyncSession) -> None:
    """列表按状态与类型过滤，最新的在前。"""
    first, _ = await job_service.create_job(session, "index", 1)
    await job_service.claim(session, first.id)
    await job_service.mark_done(session, first.id)
    await job_service.create_job(session, "tag", 2)

    pending = await job_service.list_jobs(session, status="pending")
    assert [row.type for row in pending] == ["tag"]

    done = await job_service.list_jobs(session, status="done")
    assert [row.id for row in done] == [first.id]

    tagged = await job_service.list_jobs(session, job_type="tag")
    assert len(tagged) == 1


@pytest.mark.asyncio
async def test_handlers_registered_for_every_job_type() -> None:
    """task_jobs.type 里写的类型都必须有处理器，否则任务永远跑不动。"""
    from app.workers.handlers import get_handler

    for job_type in ("parse", "index", "reindex"):
        assert get_handler(job_type) is not None
    assert get_handler("不存在的类型") is None


@pytest.mark.asyncio
async def test_manual_retry_resets_attempts(session: AsyncSession) -> None:
    """手动重试必须清零尝试次数，否则领到手立刻又被判失败。"""
    job, _ = await job_service.create_job(session, "index", 1)
    for attempt in range(1, job_service.MAX_ATTEMPTS + 1):
        await job_service.claim(session, job.id)
        if attempt < job_service.MAX_ATTEMPTS:
            await job_service.mark_retry(session, job.id, "供应商连接失败")
    await job_service.mark_failed(session, job.id, "重试用尽")

    await job_service.reset_for_retry(session, job.id)
    await session.refresh(job)
    assert job.status == "pending"
    assert job.attempts == 0
    assert job.error_msg == ""

    claimed = await job_service.claim(session, job.id)
    assert claimed is not None
    assert claimed.attempts == 1


def test_retryable_classification() -> None:
    from app.workers.queue import _is_retryable

    assert _is_retryable(AppError("PROVIDER_UNAVAILABLE", "供应商连接失败")) is True
    assert _is_retryable(AppError("NO_CHUNKS_TO_INDEX", "没有待入库的分块")) is False
    assert _is_retryable(AppError("PROVIDER_NOT_FOUND", "没有可用的向量模型")) is False
    assert _is_retryable(RuntimeError("意外的底层异常")) is True
