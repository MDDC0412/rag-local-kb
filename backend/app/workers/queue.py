"""asyncio 任务队列：把 task_jobs 里的活交给 2 个常驻 worker 慢慢做。

为什么不用 Celery/Redis：单机自用、任务量在个位数到几十的量级，
一个进程内的 ``asyncio.Queue`` 就是最合适的方案（也是 DESIGN §2.2 排除项）。

三条硬规矩
----------
1. 队列只是**调度**，事实存在 ``task_jobs`` 表里：进程重启后能接上没做完的活。
2. 处理器必须幂等：重启恢复会把 ``running`` 打回 ``pending`` 再跑一次。
3. 每个任务全包 try/except，异常写 ``error_msg``，绝不让一个任务崩掉 worker。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.core.errors import AppError
from app.db.models import TaskJob
from app.db.session import SessionLocal
from app.services import jobs as job_service
from app.workers import handlers

logger = logging.getLogger(__name__)

WORKER_COUNT = 2

# 这些错误码重试也没用（数据不满足前提、供应商配置缺失、维度冲突），直接落 failed
NOT_RETRYABLE_CODES = frozenset(
    {
        "NOT_FOUND",
        "JOB_BAD_TARGET",
        "JOB_MISSING",
        "NO_CHUNKS_TO_INDEX",
        "NO_INDEXABLE_CONTENT",
        "PROVIDER_NOT_FOUND",
        "EMBEDDING_DIM_MISMATCH",
        "CHUNK_LOCKED",
        "PARSE_FAILED",
        "NO_TEXT_FOR_TAGGING",
    }
)


def _is_retryable(error: Exception) -> bool:
    """判断失败是否值得重试：网络类、供应商超时可重试，配置/数据问题不能。"""
    if isinstance(error, AppError):
        return error.code not in NOT_RETRYABLE_CODES
    return True


class JobQueue:
    """进程内任务队列。整个应用只有一个实例（``app.state.job_queue``）。"""

    def __init__(self, worker_count: int = WORKER_COUNT) -> None:
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._worker_count = worker_count
        self._workers: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        """启动 worker，并把上次没做完的任务重新排队。"""
        if self._workers:
            return
        self._stopping.clear()
        recovered = await self._recover()
        self._workers = [
            asyncio.create_task(self._run_worker(index), name=f"job-worker-{index}")
            for index in range(self._worker_count)
        ]
        logger.info("任务队列已启动：%s 个 worker，恢复 %s 个任务", self._worker_count, recovered)

    async def stop(self) -> None:
        """停 worker。正在跑的任务会被取消，重启后由恢复逻辑接上。"""
        self._stopping.set()
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            with contextlib.suppress(asyncio.CancelledError):
                await worker
        self._workers.clear()
        logger.info("任务队列已停止")

    async def enqueue(self, job_id: int) -> None:
        """把任务 id 放进队列（不碰数据库，状态由调用方负责）。"""
        await self._queue.put(job_id)

    async def _recover(self) -> int:
        """把中断的任务打回 pending 并重新入队，返回恢复条数。"""
        async with SessionLocal() as session:
            recovered = await job_service.requeue_interrupted(session)
            pending = await job_service.list_pending_ids(session)

        for job_id in pending:
            await self._queue.put(job_id)
        return max(recovered, len(pending))

    async def _run_worker(self, index: int) -> None:
        """常驻循环：取任务 → 领任务 → 交给处理器。"""
        logger.info("worker-%s 开始工作", index)
        while not self._stopping.is_set():
            try:
                job_id = await self._queue.get()
            except asyncio.CancelledError:
                raise
            try:
                await self._execute(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 兜底：任何漏网的异常都不能让 worker 退出
                logger.exception("worker-%s 处理任务 #%s 时发生未捕获异常", index, job_id)
            finally:
                self._queue.task_done()

    async def _execute(self, job_id: int) -> None:
        """执行单个任务，处理重试与状态流转。"""
        async with SessionLocal() as session:
            job = await job_service.claim(session, job_id)
            if job is None:
                logger.info("任务 #%s 已被处理或已取消，跳过", job_id)
                return

        handler = handlers.get_handler(job.type)
        if handler is None:
            async with SessionLocal() as session:
                await job_service.mark_failed(
                    session, job_id, f"未知的任务类型「{job.type}」，请检查任务数据"
                )
            logger.warning("任务 #%s 的类型「%s」没有处理器", job_id, job.type)
            return

        logger.info("任务 #%s（%s）开始执行，第 %s 次尝试", job_id, job.type, job.attempts)
        try:
            message = await self._run_handler(handler, job)
        except asyncio.CancelledError:
            logger.warning("任务 #%s 被中断，重启后会重新排队", job_id)
            raise
        except AppError as exc:
            await self._on_error(job, exc, exc.message)
        except Exception as exc:
            logger.exception("任务 #%s 执行失败", job_id)
            await self._on_error(job, exc, f"{type(exc).__name__}：{exc}")
        else:
            async with SessionLocal() as session:
                await job_service.mark_done(session, job_id)
            logger.info("任务 #%s 完成：%s", job_id, message)
            await self._after_success(job)

    async def _after_success(self, job: TaskJob) -> None:
        """收尾：入库/重建成功后自动排一个打标签任务（DESIGN §6.1）。"""
        if job.type not in ("index", "reindex") or job.target_id is None:
            return
        try:
            async with SessionLocal() as session:
                tag_job, created = await job_service.create_job(session, "tag", job.target_id)
            if created:
                await self._queue.put(tag_job.id)
                logger.info("文档 #%s 入库完成，已排入打标签任务 #%s", job.target_id, tag_job.id)
        except Exception:
            # 打标签是增强功能，排不上队也不该影响入库结果
            logger.exception("为文档 #%s 创建打标签任务失败", job.target_id)

    async def _run_handler(self, handler, job: TaskJob) -> str:
        """给处理器一个自己的 session 和一个写进度的回调。"""
        async with SessionLocal() as session:
            # 处理器可能跑很久，进度要能实时写出去，所以另开一个 session 写
            async def report(percent: int) -> None:
                async with SessionLocal() as progress_session:
                    await job_service.set_progress(progress_session, job.id, percent)

            live_job = await session.get(TaskJob, job.id)
            if live_job is None:
                raise AppError("JOB_MISSING", f"任务 #{job.id} 已不存在", status_code=404)
            return await handler(session, live_job, report)

    async def _on_error(self, job: TaskJob, error: Exception, message: str) -> None:
        """决定重试还是放弃：参数错误立刻放弃，其它失败重试到上限。"""
        async with SessionLocal() as session:
            if not _is_retryable(error) or job.attempts >= job_service.MAX_ATTEMPTS:
                await job_service.mark_failed(session, job.id, message)
                logger.error(
                    "任务 #%s（%s）失败（已试 %s 次）：%s", job.id, job.type, job.attempts, message
                )
                return
            await job_service.mark_retry(session, job.id, message)
        logger.warning("任务 #%s（%s）失败，稍后重试：%s", job.id, job.type, message)
        await asyncio.sleep(min(2 ** (job.attempts - 1), 8))
        await self._queue.put(job.id)


def get_queue(app) -> JobQueue:
    """从 FastAPI 应用上取队列实例（路由里用）。"""
    queue: JobQueue | None = getattr(app.state, "job_queue", None)
    if queue is None:
        raise AppError("QUEUE_NOT_READY", "后台任务队列还没启动，请稍后重试", status_code=503)
    return queue
