"""文档与知识库接口。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NOT_FOUND, AppError
from app.db.models import KnowledgeBase
from app.db.session import get_session
from app.schemas.document import (
    ChunkMergeRequest,
    ChunkPreviewItem,
    ChunkPreviewRequest,
    ChunkPreviewResponse,
    ChunkUpdateRequest,
    DocumentDetail,
    DocumentFacets,
    DocumentListOut,
    DocumentOut,
    FilterPreviewRequest,
    FilterPreviewResponse,
    ParseRequest,
    ParseResponse,
    StatsOut,
    UploadResult,
)
from app.schemas.job import JobEnqueued
from app.services import documents as doc_service
from app.services import jobs as job_service
from app.services import tagging as tagging_service
from app.workers.queue import get_queue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["documents"])

DEFAULT_KB_ID = 1


# --------------------------------------------------------------------------- #
# 知识库
# --------------------------------------------------------------------------- #
@router.get("/kbs", response_model=list[dict])
async def list_kbs(session: AsyncSession = Depends(get_session)) -> list[dict]:
    """列出知识库（本期只有一个，但接口按多库设计）。"""
    rows = (await session.execute(select(KnowledgeBase).order_by(KnowledgeBase.id))).scalars().all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "embedding_provider_id": row.embedding_provider_id,
        }
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# 上传
# --------------------------------------------------------------------------- #
@router.post("/kbs/{kb_id}/documents", response_model=UploadResult, status_code=201)
async def upload_documents(
    kb_id: int,
    files: list[UploadFile] = File(...),
    session: AsyncSession = Depends(get_session),
) -> UploadResult:
    """上传一个或多个文档；重复文件（同库同哈希）会被识别并跳过。"""
    if not files:
        raise AppError("NO_FILE", "没有收到文件", status_code=400)

    created: list[DocumentOut] = []
    duplicates: list[str] = []
    warnings: list[str] = []

    for upload in files:
        filename = upload.filename or "未命名"
        content = await upload.read()
        doc, is_duplicate = await doc_service.save_upload(
            session, kb_id=kb_id, filename=filename, content=content
        )
        if is_duplicate:
            duplicates.append(f"{filename}（已存在，文档 #{doc.id}）")
            continue

        try:
            page_count, parse_warnings = await doc_service.parse_into_db(session, doc)
            warnings.extend(f"{filename}：{msg}" for msg in parse_warnings)
            if page_count == 0:
                warnings.append(f"{filename}：没有解析出任何内容")
        except AppError as exc:
            # 解析失败不阻断其它文件，但要让用户看到
            warnings.append(f"{filename}：{exc.message}")

        created.append(DocumentOut.model_validate(doc))

    return UploadResult(documents=created, duplicates=duplicates, warnings=warnings)


@router.post("/documents/{document_id}/parse", response_model=ParseResponse)
async def reparse_document(
    document_id: int,
    payload: ParseRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> ParseResponse:
    """重新解析（切换解析方式或文件被替换时用）。"""
    doc = await doc_service.get_document(session, document_id)
    mode = (payload.mode if payload else "local") or "local"

    if mode == "llm":
        from app.services.llm_parse import parse_with_llm

        result = await parse_with_llm(
            session, doc, provider_id=payload.llm_provider_id if payload else None
        )
        # LLM 解析结果同样落库，供后续切分
        await doc_service.replace_pages(session, doc, result.pages)
        return ParseResponse(
            document_id=doc.id,
            page_count=result.page_count,
            char_count=result.char_count,
            mode="llm",
            warnings=result.warnings,
            pages=[
                {"page_no": p.page_no, "text": p.text, "char_count": p.char_count}
                for p in result.pages
            ],
        )

    page_count, warnings = await doc_service.parse_into_db(session, doc)
    return ParseResponse(
        document_id=doc.id,
        page_count=page_count,
        char_count=doc.char_count,
        mode="local",
        warnings=warnings,
    )


# --------------------------------------------------------------------------- #
# 列表与详情
# --------------------------------------------------------------------------- #
@router.get("/kbs/{kb_id}/documents", response_model=DocumentListOut)
async def list_documents(
    kb_id: int,
    status: str | None = Query(default=None, description="按状态过滤"),
    q: str | None = Query(default=None, description="关键词，匹配文件名或摘要"),
    category_id: int | None = Query(default=None),
    tag_id: int | None = Query(default=None),
    sort: str = Query(default="recent", description="recent/oldest/name/size/chars"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> DocumentListOut:
    """分页 + 搜索 + 筛选的文档列表。

    分页在数据库里做（limit/offset），不是查出来再切 ——
    文档上百篇时全量查询会把摘要和标签一起拉进内存，正是这一页要解决的问题。
    """
    rows, total = await doc_service.list_documents(
        session,
        kb_id,
        status=status,
        keyword=q,
        category_id=category_id,
        tag_id=tag_id,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    items = [DocumentOut.model_validate(row) for row in rows]

    tag_map = await tagging_service.document_tag_map(session, [row.id for row in rows])
    category_ids = {row.category_id for row in rows if row.category_id}
    category_names = {
        cid: await tagging_service.document_category_name(session, cid) for cid in category_ids
    }
    for item, row in zip(items, rows, strict=True):
        item.tags = tag_map.get(row.id, [])
        item.category = category_names.get(row.category_id) if row.category_id else None
    return DocumentListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/kbs/{kb_id}/document-facets", response_model=DocumentFacets)
async def document_facets(
    kb_id: int, session: AsyncSession = Depends(get_session)
) -> DocumentFacets:
    """筛选器候选值：状态（带数量）、分类、标签。"""
    data = await doc_service.document_facets(session, kb_id)
    return DocumentFacets(**data)


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document_detail(
    document_id: int, session: AsyncSession = Depends(get_session)
) -> DocumentDetail:
    """文档详情 + 全部分块 + 统计。"""
    doc = await doc_service.get_document(session, document_id)
    chunks = await doc_service.list_chunks(session, document_id)
    stats = await doc_service.document_stats(session, doc.kb_id)

    payload = DocumentOut.model_validate(doc)
    tag_map = await tagging_service.document_tag_map(session, [document_id])
    payload.tags = tag_map.get(document_id, [])
    payload.category = await tagging_service.document_category_name(session, doc.category_id)

    return DocumentDetail(
        document=payload,
        chunks=[
            ChunkPreviewItem(
                id=chunk.id,
                seq=chunk.seq,
                content=chunk.content,
                token_count=chunk.token_count,
                char_count=chunk.char_count,
                page_no=chunk.page_no,
                is_confirmed=chunk.is_confirmed,
            )
            for chunk in chunks
        ],
        stats=stats,
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(document_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """删除文档（向量、分块、解析页、文件一起清）。"""
    await doc_service.delete_document(session, document_id)


# --------------------------------------------------------------------------- #
# 分块预览与编辑
# --------------------------------------------------------------------------- #
@router.post("/documents/{document_id}/chunks/preview", response_model=ChunkPreviewResponse)
async def preview_chunks(
    document_id: int,
    payload: ChunkPreviewRequest,
    session: AsyncSession = Depends(get_session),
) -> ChunkPreviewResponse:
    """按指定策略切分并可选落库（is_confirmed=0）。

    分块**之前**可以先过滤：``filter_raw`` 走规则（页眉页脚/页码/碎片行，零成本），
    ``filter_ai`` 再让模型判断一次哪些行没有检索价值（只调 1 次）。

    strategy=semantic 会真的调用向量模型，属于"要花钱"的策略；
    它失败会回退递归切分。原来的「智能切分」（逐段调大模型）已移除：
    75k 字的文档要 30 次串行请求、十几分钟，代价与收益完全不成比例。
    """
    if payload.strategy == "semantic":
        result = await doc_service.build_semantic_preview(
            session,
            document_id,
            chunk_size=payload.chunk_size,
            overlap=payload.overlap,
            threshold=payload.semantic_threshold,
            filter_raw=payload.filter_raw,
            filter_ai=payload.filter_ai,
        )
    else:
        result = await doc_service.build_preview(
            session,
            document_id,
            strategy=payload.strategy,
            chunk_size=payload.chunk_size,
            overlap=payload.overlap,
            filter_raw=payload.filter_raw,
            filter_ai=payload.filter_ai,
        )

    chunk_ids: list[int] = []
    if payload.save and result.chunks:
        await doc_service.save_drafts(session, document_id, result.chunks)
        saved = await doc_service.list_chunks(session, document_id)
        chunk_ids = [chunk.id for chunk in saved if not chunk.is_confirmed]

    items = [
        ChunkPreviewItem(
            id=chunk_ids[index] if index < len(chunk_ids) else None,
            seq=draft.seq,
            content=draft.content,
            token_count=draft.token_count,
            char_count=draft.char_count,
            page_no=draft.page_no,
        )
        for index, draft in enumerate(result.chunks)
    ]
    return ChunkPreviewResponse(
        document_id=document_id,
        strategy=result.strategy,
        chunk_size=result.chunk_size,
        overlap=result.overlap,
        chunks=items,
        count=result.count,
        total_tokens=result.total_tokens,
        avg_tokens=result.avg_tokens,
        warnings=result.warnings,
    )


@router.get("/chunk-strategies")
async def list_chunk_strategies() -> dict:
    """列出所有分块策略及说明，前端用来渲染下拉项，避免前后端各写一份文案。"""
    from app.services.chunking import (
        API_STRATEGIES,
        LOCAL_STRATEGIES,
        STRATEGY_HINT,
        STRATEGY_LABEL,
    )

    items = []
    for name in (*LOCAL_STRATEGIES, *API_STRATEGIES):
        items.append(
            {
                "value": name,
                "label": STRATEGY_LABEL.get(name, name),
                "hint": STRATEGY_HINT.get(name, ""),
                "needs_api": name in API_STRATEGIES,
            }
        )
    return {"items": items}


@router.post("/documents/{document_id}/filter/preview", response_model=FilterPreviewResponse)
async def filter_preview(
    document_id: int,
    payload: FilterPreviewRequest,
    session: AsyncSession = Depends(get_session),
) -> FilterPreviewResponse:
    """过滤预览：分块**之前**的清洗，只看效果不落库。

    - `filter_raw`：规则清洗（页眉页脚、页码、碎片行、乱码），零成本；
    - `filter_ai`：再让模型判断一次哪些行没有检索价值（**只调 1 次**）。

    这是替代原来"智能切分"的地方：同样是让模型参与，但只做**判定**、
    只调一次，而不是对全文逐段调用。
    """
    data = await doc_service.build_filter_preview(
        session,
        document_id,
        filter_raw=payload.filter_raw,
        filter_ai=payload.filter_ai,
        llm_provider_id=payload.llm_provider_id,
    )
    return FilterPreviewResponse(**data)


@router.put("/chunks/{chunk_id}", response_model=ChunkPreviewItem)
async def update_chunk(
    chunk_id: int, payload: ChunkUpdateRequest, session: AsyncSession = Depends(get_session)
) -> ChunkPreviewItem:
    """修改一块的文字（仅未入库状态）。"""
    chunk = await doc_service.update_chunk_content(session, chunk_id, payload.content)
    return ChunkPreviewItem(
        id=chunk.id,
        seq=chunk.seq,
        content=chunk.content,
        token_count=chunk.token_count,
        char_count=chunk.char_count,
        page_no=chunk.page_no,
        is_confirmed=chunk.is_confirmed,
    )


@router.delete("/chunks/{chunk_id}", status_code=204)
async def delete_chunk(chunk_id: int, session: AsyncSession = Depends(get_session)) -> None:
    """删除一块（仅未入库状态）。"""
    await doc_service.delete_chunk(session, chunk_id)


@router.post("/chunks/merge", response_model=ChunkPreviewItem)
async def merge_chunks(
    payload: ChunkMergeRequest, session: AsyncSession = Depends(get_session)
) -> ChunkPreviewItem:
    """合并相邻分块。"""
    chunk = await doc_service.merge_chunks(session, payload.chunk_ids)
    return ChunkPreviewItem(
        id=chunk.id,
        seq=chunk.seq,
        content=chunk.content,
        token_count=chunk.token_count,
        char_count=chunk.char_count,
        page_no=chunk.page_no,
        is_confirmed=chunk.is_confirmed,
    )


# --------------------------------------------------------------------------- #
# 入库（异步：排队后立刻返回任务，前端轮询 /api/jobs/{id}）
# --------------------------------------------------------------------------- #
@router.post("/documents/{document_id}/index", response_model=JobEnqueued)
async def index_document(
    document_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JobEnqueued:
    """确认入库：把未确认的分块排进队列向量化，立刻返回任务。

    不在这里等 embedding 跑完：几十块文档要几十秒，接口会超时，
    而且用户看不到任何进度。真正的活在 ``workers/handlers.py``。
    """
    doc = await doc_service.get_document(session, document_id)
    chunks = await doc_service.list_chunks(session, document_id)
    pending = [chunk for chunk in chunks if not chunk.is_confirmed]
    if not pending:
        raise AppError(
            "NO_CHUNKS_TO_INDEX",
            "没有待入库的分块，请先生成分块预览并保存",
            status_code=400,
        )

    # 用户连点两次不该产生两份活：同文档已有 pending/running 的任务就直接复用
    job, created = await job_service.create_job(session, "index", document_id)
    if created:
        await get_queue(request.app).enqueue(job.id)
    logger.info("文档 #%s 入库任务 #%s（%s）", doc.id, job.id, "新建" if created else "复用")
    return JobEnqueued(job=job, deduplicated=not created)


@router.post("/documents/{document_id}/reindex", response_model=JobEnqueued)
async def reindex_document(
    document_id: int,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> JobEnqueued:
    """整篇重建向量（换向量模型后必须走这条）：排队执行。"""
    await doc_service.get_document(session, document_id)
    job, created = await job_service.create_job(session, "reindex", document_id)
    if created:
        await get_queue(request.app).enqueue(job.id)
    return JobEnqueued(job=job, deduplicated=not created)


@router.get("/kbs/{kb_id}/stats", response_model=StatsOut)
async def kb_stats(kb_id: int, session: AsyncSession = Depends(get_session)) -> StatsOut:
    """知识库统计。"""
    data = await doc_service.document_stats(session, kb_id)
    return StatsOut(**data)


@router.get("/documents/{document_id}/pages")
async def document_pages(document_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """原文分页（预览时左侧展示对照用）。"""
    from app.db.models import DocumentPage

    doc = await doc_service.get_document(session, document_id)
    rows = (
        (
            await session.execute(
                select(DocumentPage)
                .where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_no)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise AppError(NOT_FOUND, "这篇文档还没有解析结果", status_code=404)
    return {
        "document_id": doc.id,
        "filename": doc.filename,
        "pages": [
            {"page_no": row.page_no, "text": row.text, "char_count": row.char_count} for row in rows
        ],
    }
