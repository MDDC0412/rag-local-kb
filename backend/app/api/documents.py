"""文档与知识库接口。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Query, UploadFile
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
    DocumentOut,
    IndexResponse,
    ParseRequest,
    ParseResponse,
    StatsOut,
    UploadResult,
)
from app.services import documents as doc_service
from app.services.llm_parse import chunk_with_llm

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
@router.get("/kbs/{kb_id}/documents", response_model=list[DocumentOut])
async def list_documents(
    kb_id: int,
    status: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    """列出文档。"""
    rows = await doc_service.list_documents(session, kb_id, status=status)
    return [DocumentOut.model_validate(row) for row in rows]


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document_detail(
    document_id: int, session: AsyncSession = Depends(get_session)
) -> DocumentDetail:
    """文档详情 + 全部分块 + 统计。"""
    doc = await doc_service.get_document(session, document_id)
    chunks = await doc_service.list_chunks(session, document_id)
    stats = await doc_service.document_stats(session, doc.kb_id)
    return DocumentDetail(
        document=DocumentOut.model_validate(doc),
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

    strategy=semantic 会真的调用向量模型（每批 32 句一次请求），属于"要花钱"的策略；
    strategy=llm 会调用对话模型，代价更高。两者失败都会自动回退递归切分。
    """
    if payload.strategy == "semantic":
        result = await doc_service.build_semantic_preview(
            session,
            document_id,
            chunk_size=payload.chunk_size,
            overlap=payload.overlap,
            threshold=payload.semantic_threshold,
        )
    elif payload.strategy == "llm":
        result = await chunk_with_llm(
            session,
            document_id,
            chunk_size=payload.chunk_size,
            overlap=payload.overlap,
        )
    else:
        result = await doc_service.build_preview(
            session,
            document_id,
            strategy=payload.strategy,
            chunk_size=payload.chunk_size,
            overlap=payload.overlap,
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


@router.post("/documents/{document_id}/chunks/llm", response_model=ChunkPreviewResponse)
async def preview_chunks_llm(
    document_id: int,
    payload: ChunkPreviewRequest,
    session: AsyncSession = Depends(get_session),
) -> ChunkPreviewResponse:
    """用 LLM 做语义切分（失败自动回退递归切分）。"""
    from app.services.llm_parse import chunk_with_llm

    result = await chunk_with_llm(
        session, document_id, chunk_size=payload.chunk_size, overlap=payload.overlap
    )
    if payload.save and result.chunks:
        await doc_service.save_drafts(session, document_id, result.chunks)
        saved = await doc_service.list_chunks(session, document_id)
        chunk_ids = [chunk.id for chunk in saved if not chunk.is_confirmed]
    else:
        chunk_ids = []

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
        strategy="llm",
        chunk_size=result.chunk_size,
        overlap=result.overlap,
        chunks=items,
        count=result.count,
        total_tokens=result.total_tokens,
        avg_tokens=result.avg_tokens,
        warnings=result.warnings,
    )


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
# 入库
# --------------------------------------------------------------------------- #
@router.post("/documents/{document_id}/index", response_model=IndexResponse)
async def index_document(
    document_id: int, session: AsyncSession = Depends(get_session)
) -> IndexResponse:
    """确认入库：向量化未确认的分块并写入 Chroma。"""
    from app.services import indexing

    indexed = await doc_service.index_document(session, document_id)
    doc = await doc_service.get_document(session, document_id)
    return IndexResponse(
        document_id=document_id,
        indexed_chunks=indexed,
        vectors=indexing.count_vectors(),
        status=doc.status,
    )


@router.post("/documents/{document_id}/reindex", response_model=IndexResponse)
async def reindex_document(
    document_id: int, session: AsyncSession = Depends(get_session)
) -> IndexResponse:
    """整篇重新向量化（换向量模型后必须走这条）。"""
    from app.services import indexing

    doc = await doc_service.get_document(session, document_id)
    # 把已确认的分块打回未确认，再走一次入库
    chunks = await doc_service.list_chunks(session, document_id)
    for chunk in chunks:
        chunk.is_confirmed = 0
        chunk.vector_id = ""
    await session.commit()
    indexing.delete_document_vectors(document_id)

    indexed = await doc_service.index_document(session, document_id)
    return IndexResponse(
        document_id=document_id,
        indexed_chunks=indexed,
        vectors=indexing.count_vectors(),
        status=doc.status,
    )


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
