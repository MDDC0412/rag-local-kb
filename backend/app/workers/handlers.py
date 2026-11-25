"""任务处理器：把 task_jobs 里的类型映射成真正的活。

每个处理器都必须是**幂等**的：进程重启后任务会被重新入队，
同一个任务可能被执行第二次，所以写库前一律先清掉旧结果（见 DESIGN §8.4）。

处理器签名统一为 ``async def handler(session, job, report) -> str``：
返回一句给人看的结果摘要，失败直接抛异常由队列决定重试还是放弃。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import TaskJob
from app.services import documents as doc_service

logger = logging.getLogger(__name__)


async def _index_target(session: AsyncSession, job: TaskJob, report) -> str:
    """向量化入库：先清该文档旧向量再写，保证重复执行不产生孤儿。

    重入时若文档已经入库完成、没有待办分块，说明上次已经做完了
    （任务在「写完库、还没标 done」之间被进程重启打断），直接算成功，
    否则会拿 ``NO_CHUNKS_TO_INDEX`` 误报失败 —— 文档其实是好的。
    """
    document_id = _require_target(job)
    done = await _already_indexed(session, document_id)
    if done is not None:
        return done

    await report(5)
    count = await doc_service.index_document(session, document_id, on_progress=_bridge(report))
    await report(100)
    return f"已入库 {count} 块"


async def _already_indexed(session: AsyncSession, document_id: int) -> str | None:
    """如果这篇文档已经是「无待办分块 + 已入库」状态，返回说明；否则 None。"""
    doc = await doc_service.get_document(session, document_id)
    if doc.status != "indexed":
        return None
    chunks = await doc_service.list_chunks(session, document_id)
    if not chunks or any(not chunk.is_confirmed for chunk in chunks):
        return None
    logger.info("文档 #%s 已是入库完成状态，跳过重复执行", document_id)
    return f"文档已是入库状态（{len(chunks)} 块），无需重复入库"


async def _reindex_target(session: AsyncSession, job: TaskJob, report) -> str:
    """整篇重建向量：先把已确认分块打回未确认并清旧向量，再重新入库。"""
    document_id = _require_target(job)
    await report(3)
    await doc_service.reset_document_index(session, document_id)
    count = await doc_service.index_document(session, document_id, on_progress=_bridge(report))
    await report(100)
    return f"已重建 {count} 块向量"


async def _parse_target(session: AsyncSession, job: TaskJob, report) -> str:
    """本地规则解析（「重切不重解析」的前提）。"""
    document_id = _require_target(job)
    await report(10)
    doc = await doc_service.get_document(session, document_id)
    page_count, warnings = await doc_service.parse_into_db(session, doc)
    await report(100)
    suffix = f"，{len(warnings)} 条提示" if warnings else ""
    return f"已解析 {page_count} 段{suffix}"


def _require_target(job: TaskJob) -> int:
    """任务必须有目标文档，否则是脏数据。"""
    if job.target_id is None:
        raise AppError(
            "JOB_BAD_TARGET", f"任务 #{job.id}（{job.type}）缺少目标文档", status_code=400
        )
    return job.target_id


def _bridge(report):
    """把同步的 embedding 进度回调桥成异步写库，按 5% 步长去重避免频繁提交。"""

    state = {"last": 0}

    async def on_progress(done: int, total: int) -> None:
        percent = 5 + int(done * 90 / total) if total else 95
        if percent - state["last"] >= 5:
            state["last"] = percent
            await report(percent)

    return on_progress


HANDLERS = {
    "index": _index_target,
    "reindex": _reindex_target,
    "parse": _parse_target,
}


def get_handler(job_type: str):
    """按任务类型取处理器；没注册的类型返回 None（队列会判为不可执行）。"""
    return HANDLERS.get(job_type)
