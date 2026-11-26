"""文档业务：上传去重、解析落库、分块预览、确认入库、删除。

状态机
------
uploaded → parsing → parsed →（预览分块，is_confirmed=0）
         → indexing → indexed
任何一步失败 → failed（error_msg 存人话原因，可重试）

关键取舍：解析结果先落 document_pages，之后「调参重切」只读库不重解析，
既不重复花钱，也让预览可以反复试。
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import uuid
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.errors import (
    EMPTY_FILE,
    FILE_TOO_LARGE,
    NOT_FOUND,
    PROVIDER_NOT_FOUND,
    AppError,
    message_for,
)
from app.db.models import Chunk, Document, DocumentPage, KnowledgeBase, ProviderConfig
from app.providers.embed import EmbeddingClient
from app.providers.registry import to_spec
from app.services import indexing
from app.services.chunking import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    ChunkDraft,
    ChunkResult,
    split_local,
)
from app.services.parsing import (
    MAX_FILE_SIZE,
    PageText,
    detect_mime,
    ensure_supported,
    parse_document,
)
from app.services.tokenizer import count_tokens

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 上传
# --------------------------------------------------------------------------- #
def _hash_file(path: Path) -> str:
    """流式计算 sha256，避免大文件一次性读进内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


async def save_upload(
    session: AsyncSession,
    *,
    kb_id: int,
    filename: str,
    content: bytes,
) -> tuple[Document, bool]:
    """保存上传文件，返回 (文档, 是否重复)。

    重复判定：同一知识库内 sha256 相同。重复时返回已有文档，不重复占磁盘。
    """
    if len(content) == 0:
        raise AppError(
            EMPTY_FILE, message_for(EMPTY_FILE, f"「{filename}」是空文件"), status_code=400
        )
    if len(content) > MAX_FILE_SIZE:
        raise AppError(
            FILE_TOO_LARGE,
            f"「{filename}」超过 50MB 上限（{len(content) / 1024 / 1024:.1f}MB）。"
            f"请拆分后再上传，或压缩其中的图片。",
            status_code=413,
        )

    suffix = ensure_supported(filename)
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise AppError(NOT_FOUND, f"知识库 #{kb_id} 不存在", status_code=404)

    settings = get_settings()
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = settings.uploads_dir / stored_name
    stored_path.write_bytes(content)

    file_hash = _hash_file(stored_path)
    existing = (
        await session.execute(
            select(Document).where(Document.kb_id == kb_id, Document.file_hash == file_hash)
        )
    ).scalar_one_or_none()
    if existing is not None:
        stored_path.unlink(missing_ok=True)
        logger.info("重复文件 %s，复用已有文档 #%s", filename, existing.id)
        return existing, True

    doc = Document(
        kb_id=kb_id,
        filename=filename,
        stored_path=str(stored_path),
        file_hash=file_hash,
        size=len(content),
        mime=detect_mime(stored_path),
        status="uploaded",
        parse_mode="local",
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    logger.info("上传文档 #%s %s（%.1f KB）", doc.id, filename, len(content) / 1024)
    return doc, False


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
async def parse_into_db(session: AsyncSession, document: Document) -> tuple[int, list[str]]:
    """解析文档并写入 document_pages，返回 (页数, 警告)。"""
    path = Path(document.stored_path)
    if not path.exists():
        raise AppError(NOT_FOUND, f"原始文件已丢失：{document.filename}", status_code=400)

    document.status = "parsing"
    await session.commit()

    try:
        result = parse_document(path, document.filename)
    except AppError:
        document.status = "failed"
        await session.commit()
        raise
    except Exception as exc:
        document.status = "failed"
        document.error_msg = f"解析失败：{type(exc).__name__}"
        await session.commit()
        logger.exception("解析文档 %s 失败", document.id)
        raise AppError(
            "PARSE_FAILED", f"解析「{document.filename}」失败，文件可能已损坏", status_code=400
        ) from exc

    # 覆盖旧结果（可能来自上一次不同解析方式）
    await session.execute(delete(DocumentPage).where(DocumentPage.document_id == document.id))
    for page in result.pages:
        session.add(
            DocumentPage(
                document_id=document.id,
                page_no=page.page_no,
                text=page.text,
                char_count=page.char_count,
            )
        )

    document.page_count = result.page_count
    document.char_count = result.char_count
    document.parse_mode = result.mode
    document.status = "parsed"
    document.error_msg = ""
    await session.commit()
    return result.page_count, result.warnings


async def replace_pages(session: AsyncSession, document: Document, pages: list[PageText]) -> None:
    """用新解析结果覆盖 document_pages（LLM 解析后调用）。"""
    await session.execute(delete(DocumentPage).where(DocumentPage.document_id == document.id))
    for page in pages:
        session.add(
            DocumentPage(
                document_id=document.id,
                page_no=page.page_no,
                text=page.text,
                char_count=page.char_count,
            )
        )
    document.page_count = len(pages)
    document.char_count = sum(page.char_count for page in pages)
    document.parse_mode = "llm"
    document.status = "parsed"
    document.error_msg = ""
    await session.commit()


async def load_pages(session: AsyncSession, document_id: int) -> list[tuple[str, int]]:
    """读回解析结果，供分块使用。"""
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
        raise AppError(
            "DOC_NOT_PARSED",
            "这篇文档还没有解析结果，请先执行解析",
            status_code=400,
        )
    return [(row.text, row.page_no) for row in rows]


# --------------------------------------------------------------------------- #
# 过滤（分块之前的清洗）
# --------------------------------------------------------------------------- #
async def build_filter_preview(
    session: AsyncSession,
    document_id: int,
    *,
    filter_raw: bool = True,
    filter_ai: bool = False,
    llm_provider_id: int | None = None,
) -> dict:
    """只看过滤效果：留下多少、删了哪些行、为什么删。

    这是给用户"先看一眼再决定要不要用"的地方，所以**不落库、不切分**。
    智能过滤最多调 1 次模型，且失败时回报警告而不是报错。
    """
    from app.services import filtering

    pages = await load_pages(session, document_id)
    raw_texts = [(page_no, text) for text, page_no in pages]
    raw_chars = sum(len(text) for _, text in raw_texts)

    warnings: list[str] = []
    if not filter_raw and not filter_ai:
        result = filtering.FilterResult(
            pages=raw_texts, mode="none", warnings=["没有启用任何过滤，原文将直接进入分块。"]
        )
    elif filter_ai:
        drop, _removals, ai_warnings = await ai_filter_lines(
            session, raw_texts, provider_id=llm_provider_id
        )
        warnings.extend(ai_warnings)
        result = filtering.filter_with_ai_lines(raw_texts, drop)
        warnings.extend(result.warnings)
    else:
        result = filtering.filter_raw(raw_texts)
        warnings.extend(result.warnings)

    return {
        "document_id": document_id,
        "mode": result.mode,
        "raw_chars": raw_chars,
        "kept_chars": result.kept_chars,
        "removed_count": result.removed_count,
        "removals": [
            {
                "page_no": item.page_no,
                "line_no": item.line_no,
                "text": item.text,
                "reason": item.reason,
            }
            for item in result.removals[:200]  # 只回前 200 条，避免超长文档把响应撑爆
        ],
        "warnings": warnings,
        "pages": [{"page_no": page_no, "text": text} for page_no, text in result.pages],
    }


async def ai_filter_lines(
    session: AsyncSession, pages: list[tuple[int, str]], *, provider_id: int | None = None
) -> tuple[set[tuple[int, int]], list, list[str]]:
    """调一次模型，拿到"该删哪些行"的行号集合。

    单独抽出来是因为**两处都要用**：过滤预览、以及带过滤的分块。
    智能过滤失败不抛异常（可选增强），只回警告。
    """
    from app.services import filtering
    from app.services.llm_parse import _get_llm_spec, _load_template

    try:
        spec = await _get_llm_spec(session, provider_id)
        from app.providers.llm import build_chat_model

        model = build_chat_model(spec, temperature=0.0, streaming=False)
        template = await _load_template(session, "filter", filtering.ai_prompt_fallback())
    except Exception as exc:
        logger.warning("智能过滤准备失败，跳过：%s", exc)
        return set(), [], [f"智能过滤不可用（{type(exc).__name__}），已只做原文过滤。"]

    return await filtering.ai_filter(pages, chat_model=model, prompt_template=template)


async def filtered_pages(
    session: AsyncSession,
    document_id: int,
    *,
    filter_raw: bool,
    filter_ai: bool,
) -> tuple[list[tuple[str, int]], list[str]]:
    """按开关给出"过滤后的分页文本"，供分块使用。

    注意返回值是 ``[(text, page_no)]``（与 split_local 的入参顺序一致），
    而 filter 模块内部用 ``[(page_no, text)]``（便于按页号报删除行号）。
    """
    from app.services import filtering

    pages = await load_pages(session, document_id)
    if not filter_raw and not filter_ai:
        return pages, []

    raw_texts = [(page_no, text) for text, page_no in pages]
    warnings: list[str] = []

    if filter_ai:
        drop, _removals, ai_warnings = await ai_filter_lines(session, raw_texts)
        warnings.extend(ai_warnings)
        result = filtering.filter_with_ai_lines(raw_texts, drop)
    else:
        result = filtering.filter_raw(raw_texts)
    warnings.extend(result.warnings)

    kept = [(text, page_no) for page_no, text in result.pages]
    if not kept:
        # 全被删掉说明规则太激进，宁可退回原文也不要给用户一个空文档
        warnings.append("过滤后没有内容留下，已退回未过滤的原文。")
        return pages, warnings
    return kept, warnings


# --------------------------------------------------------------------------- #
# 分块
# --------------------------------------------------------------------------- #
async def build_preview(
    session: AsyncSession,
    document_id: int,
    *,
    strategy: str = "recursive",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    filter_raw: bool = False,
    filter_ai: bool = False,
) -> ChunkResult:
    """生成分块预览（纯计算，不落库）。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise AppError(NOT_FOUND, f"文档 #{document_id} 不存在", status_code=404)
    pages, filter_warnings = await filtered_pages(
        session, document_id, filter_raw=filter_raw, filter_ai=filter_ai
    )
    result = split_local(pages, strategy=strategy, chunk_size=chunk_size, overlap=overlap)
    result.warnings = [*filter_warnings, *result.warnings]
    return result


async def build_semantic_preview(
    session: AsyncSession,
    document_id: int,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = 0,
    threshold: float = 0.55,
    filter_raw: bool = False,
    filter_ai: bool = False,
) -> ChunkResult:
    """语义切分：需要真实调用向量模型。

    与本地策略不同，这个会产生 API 费用与延迟，
    所以刻意单独成一个函数，让调用点一眼能看出"这条路径要花钱"。
    """
    from app.services.chunking import split_semantic

    doc = await session.get(Document, document_id)
    if doc is None:
        raise AppError(NOT_FOUND, f"文档 #{document_id} 不存在", status_code=404)
    pages, filter_warnings = await filtered_pages(
        session, document_id, filter_raw=filter_raw, filter_ai=filter_ai
    )
    spec = await resolve_embedding_spec(session, doc.kb_id)
    client = EmbeddingClient(spec)

    result = await split_semantic(
        pages,
        embed_fn=client.embed,
        chunk_size=chunk_size,
        overlap=overlap,
        threshold=threshold,
    )
    result.warnings = [*filter_warnings, *result.warnings]
    return result


async def save_drafts(session: AsyncSession, document_id: int, drafts: list[ChunkDraft]) -> int:
    """把预览结果写入 chunks（is_confirmed=0）。

    重新切分时必须**整体替换**该文档的分块，包括已入库的那些：
    否则旧的「已入库 #1」会和新的「未入库 #1」并存，界面上出现两份，
    检索时还会命中已被替换掉的旧内容。內容变了索引就得重建，
    所以这里顺带清掉该文档的旧向量，并把状态退回 parsed。
    """
    doc = await session.get(Document, document_id)
    if doc is None:
        raise AppError(NOT_FOUND, f"文档 #{document_id} 不存在", status_code=404)

    had_confirmed = (
        await session.execute(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.document_id == document_id, Chunk.is_confirmed == 1)
        )
    ).scalar_one()
    if had_confirmed:
        with contextlib.suppress(Exception):
            indexing.delete_document_vectors(document_id)
        doc.status = "parsed"
        doc.indexed_at = None

    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    for index, draft in enumerate(drafts):
        session.add(
            Chunk(
                document_id=document_id,
                kb_id=doc.kb_id,
                seq=index,
                content=draft.content,
                token_count=count_tokens(draft.content),
                char_count=len(draft.content),
                page_no=draft.page_no,
                section_path=draft.section_path,
                parent_content=draft.parent_content,
                is_confirmed=0,
            )
        )
    if doc.status in ("uploaded", "parsing"):
        doc.status = "parsed"
    await session.commit()
    return len(drafts)


async def list_chunks(session: AsyncSession, document_id: int) -> list[Chunk]:
    """按序号列出某文档的分块（未确认+已确认）。"""
    rows = (
        (
            await session.execute(
                select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.seq)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def update_chunk_content(session: AsyncSession, chunk_id: int, content: str) -> Chunk:
    """手工修改某一块的文字（用户可在预览里改）。"""
    chunk = await session.get(Chunk, chunk_id)
    if chunk is None:
        raise AppError(NOT_FOUND, f"分块 #{chunk_id} 不存在", status_code=404)
    if chunk.is_confirmed:
        raise AppError(
            "CHUNK_LOCKED",
            "这块已经入库，改动会导致向量与文字不一致；请重新生成预览后再改",
            status_code=409,
        )
    chunk.content = content.strip()
    chunk.token_count = count_tokens(chunk.content)
    chunk.char_count = len(chunk.content)
    await session.commit()
    await session.refresh(chunk)
    return chunk


async def delete_chunk(session: AsyncSession, chunk_id: int) -> None:
    """删除一块（仅未确认状态）。"""
    chunk = await session.get(Chunk, chunk_id)
    if chunk is None:
        raise AppError(NOT_FOUND, f"分块 #{chunk_id} 不存在", status_code=404)
    if chunk.is_confirmed:
        raise AppError("CHUNK_LOCKED", "已入库的分块不能单独删除，请重建索引", status_code=409)
    await session.delete(chunk)
    await session.commit()


async def merge_chunks(session: AsyncSession, chunk_ids: list[int]) -> Chunk:
    """把相邻的几块合并成一块，用第一块承载结果。"""
    if len(chunk_ids) < 2:
        raise AppError("MERGE_INVALID", "至少选择两块才能合并", status_code=400)

    rows = (
        (await session.execute(select(Chunk).where(Chunk.id.in_(chunk_ids)).order_by(Chunk.seq)))
        .scalars()
        .all()
    )
    if len(rows) != len(chunk_ids):
        raise AppError(NOT_FOUND, "部分分块不存在", status_code=404)
    if any(row.is_confirmed for row in rows):
        raise AppError("CHUNK_LOCKED", "已入库的分块不能合并", status_code=409)

    first = rows[0]
    first.content = "\n\n".join(row.content for row in rows)
    first.token_count = count_tokens(first.content)
    first.char_count = len(first.content)
    for row in rows[1:]:
        await session.delete(row)
    await session.commit()
    await session.refresh(first)
    return first


# --------------------------------------------------------------------------- #
# 入库
# --------------------------------------------------------------------------- #
async def resolve_embedding_spec(session: AsyncSession, kb_id: int):
    """取知识库绑定的向量供应商；没绑就用默认的。"""
    kb = await session.get(KnowledgeBase, kb_id)
    if kb is None:
        raise AppError(NOT_FOUND, f"知识库 #{kb_id} 不存在", status_code=404)
    if kb.embedding_provider_id:
        row = await session.get(ProviderConfig, kb.embedding_provider_id)
        if row is None:
            raise AppError(
                PROVIDER_NOT_FOUND,
                "知识库绑定的向量供应商已被删除，请到「模型供应商」重新选择",
                status_code=400,
            )
        return to_spec(row)

    row = (
        await session.execute(
            select(ProviderConfig)
            .where(ProviderConfig.kind == "embedding", ProviderConfig.enabled.is_(True))
            .order_by(ProviderConfig.is_default.desc(), ProviderConfig.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise AppError(
            PROVIDER_NOT_FOUND,
            "还没有可用的向量模型，请先到「模型供应商」添加一个",
            status_code=400,
        )
    return to_spec(row)


async def index_document(
    session: AsyncSession,
    document_id: int,
    *,
    on_progress=None,
) -> int:
    """确认入库：把该文档未确认的分块向量化写入 Chroma。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise AppError(NOT_FOUND, f"文档 #{document_id} 不存在", status_code=404)

    drafts = (
        (
            await session.execute(
                select(Chunk)
                .where(Chunk.document_id == document_id, Chunk.is_confirmed == 0)
                .order_by(Chunk.seq)
            )
        )
        .scalars()
        .all()
    )
    if not drafts:
        raise AppError(
            "NO_CHUNKS_TO_INDEX",
            "没有待入库的分块，请先生成分块预览并保存",
            status_code=400,
        )

    spec = await resolve_embedding_spec(session, doc.kb_id)
    doc.status = "indexing"
    await session.commit()

    # 先清掉该文档的旧向量，避免同一份文档重复入库时留下孤儿
    indexing.delete_document_vectors(document_id)

    items = [
        indexing.VectorItem(
            chunk_id=chunk.id,
            text=chunk.content,
            document_id=document_id,
            kb_id=doc.kb_id,
            page_no=chunk.page_no,
            seq=chunk.seq,
        )
        for chunk in drafts
        if chunk.content.strip()
    ]
    if not items:
        doc.status = "failed"
        doc.error_msg = "所有分块内容都是空的，没有可入库的数据"
        await session.commit()
        raise AppError("NO_INDEXABLE_CONTENT", "所有分块内容都是空的", status_code=400)

    try:
        await indexing.embed_and_store(items, spec, on_progress=on_progress)
    except AppError as exc:
        doc.status = "failed"
        doc.error_msg = exc.message
        await session.commit()
        raise
    except Exception as exc:
        doc.status = "failed"
        doc.error_msg = f"入库失败：{type(exc).__name__}"
        await session.commit()
        logger.exception("文档 %s 入库失败", document_id)
        raise AppError("INDEX_FAILED", "入库失败，请稍后重试", status_code=400) from exc

    for chunk in drafts:
        chunk.is_confirmed = 1
        chunk.vector_id = f"c{chunk.id}"
    doc.status = "indexed"
    doc.error_msg = ""
    doc.indexed_at = func.now()
    await session.commit()
    logger.info("文档 #%s 入库完成，%s 块", document_id, len(items))
    return len(items)


# --------------------------------------------------------------------------- #
# 查询与删除
# --------------------------------------------------------------------------- #
async def reset_document_index(session: AsyncSession, document_id: int) -> int:
    """把文档打回「未入库」并清掉向量，返回受影响的分块数。

    换向量模型后必须走这条：旧向量是另一个模型算的，和新向量不在一个空间里
    比较，留着只会污染检索。清完再调 ``index_document`` 重建。
    """
    doc = await session.get(Document, document_id)
    if doc is None:
        raise AppError(NOT_FOUND, f"文档 #{document_id} 不存在", status_code=404)

    result = await session.execute(
        update(Chunk).where(Chunk.document_id == document_id).values(is_confirmed=0, vector_id="")
    )
    doc.status = "parsed"
    doc.indexed_at = None
    doc.error_msg = ""
    await session.commit()

    indexing.delete_document_vectors(document_id)
    logger.info("文档 #%s 已重置索引，%s 块待重建", document_id, result.rowcount or 0)
    return result.rowcount or 0


async def list_documents(
    session: AsyncSession, kb_id: int, *, status: str | None = None
) -> list[Document]:
    """列出知识库下的文档。"""
    stmt = select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc())
    if status:
        stmt = stmt.where(Document.status == status)
    return list((await session.execute(stmt)).scalars().all())


async def get_document(session: AsyncSession, document_id: int) -> Document:
    """取文档，不存在则报错。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise AppError(NOT_FOUND, f"文档 #{document_id} 不存在", status_code=404)
    return doc


async def delete_document(session: AsyncSession, document_id: int) -> None:
    """删除文档：向量、分块、解析页、物理文件、记录，五处都要清。"""
    doc = await get_document(session, document_id)

    indexing.delete_document_vectors(document_id)

    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    await session.execute(delete(DocumentPage).where(DocumentPage.document_id == document_id))
    stored = Path(doc.stored_path)
    try:
        stored.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("删除物理文件失败 %s：%s", stored, exc)

    await session.delete(doc)
    await session.commit()
    logger.info("已删除文档 #%s", document_id)


async def document_stats(session: AsyncSession, kb_id: int) -> dict[str, int]:
    """统计：文档数、分块数、已入库分块数、向量数。"""
    doc_count = (
        await session.execute(
            select(func.count()).select_from(Document).where(Document.kb_id == kb_id)
        )
    ).scalar_one()
    chunk_total = (
        await session.execute(select(func.count()).select_from(Chunk).where(Chunk.kb_id == kb_id))
    ).scalar_one()
    chunk_confirmed = (
        await session.execute(
            select(func.count())
            .select_from(Chunk)
            .where(Chunk.kb_id == kb_id, Chunk.is_confirmed == 1)
        )
    ).scalar_one()
    return {
        "documents": int(doc_count),
        "chunks": int(chunk_total),
        "indexed_chunks": int(chunk_confirmed),
        "vectors": indexing.count_vectors(),
    }


def to_page_texts(pages: list[tuple[str, int]]) -> list[PageText]:
    """把 (text, page_no) 转成 PageText，方便在 LLM 解析里复用。"""
    return [PageText(page_no=page_no, text=text) for text, page_no in pages]
