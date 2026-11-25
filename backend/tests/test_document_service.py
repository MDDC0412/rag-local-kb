"""文档业务的数据库层测试：用临时 SQLite，不触碰真实数据库。

重点覆盖「重新切分必须整体替换」这个曾经出过的 bug：
已入库的旧分块如果没被清掉，会和新分块并存，界面上出现两份 #1。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import AppError
from app.db.models import Base, Chunk, Document, KnowledgeBase
from app.services import documents as doc_service
from app.services.chunking import ChunkDraft


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


@pytest_asyncio.fixture
async def doc(session: AsyncSession) -> Document:
    """建一个知识库和一篇文档。"""
    kb = KnowledgeBase(name="test", description="")
    session.add(kb)
    await session.flush()
    document = Document(
        kb_id=kb.id,
        filename="a.txt",
        stored_path="/tmp/nonexistent.txt",
        file_hash="x" * 64,
        status="parsed",
    )
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


def drafts(*texts: str) -> list[ChunkDraft]:
    """构造若干草稿。"""
    return [ChunkDraft(seq=i, content=text, page_no=1) for i, text in enumerate(texts)]


@pytest.mark.asyncio
async def test_save_drafts_creates_chunks(session: AsyncSession, doc: Document) -> None:
    """首次保存生成对应数量的分块，且都是未确认状态。"""
    count = await doc_service.save_drafts(session, doc.id, drafts("第一块内容", "第二块内容"))
    assert count == 2

    chunks = await doc_service.list_chunks(session, doc.id)
    assert len(chunks) == 2
    assert all(chunk.is_confirmed == 0 for chunk in chunks)
    assert [chunk.seq for chunk in chunks] == [0, 1]


@pytest.mark.asyncio
async def test_save_drafts_replaces_previous_pending(session: AsyncSession, doc: Document) -> None:
    """再次切分替换掉上一次的分块，不会叠加。"""
    await doc_service.save_drafts(session, doc.id, drafts("老块一", "老块二", "老块三"))
    await doc_service.save_drafts(session, doc.id, drafts("新块一", "新块二"))

    chunks = await doc_service.list_chunks(session, doc.id)
    assert len(chunks) == 2
    assert all("新块" in chunk.content for chunk in chunks)


@pytest.mark.asyncio
async def test_save_drafts_replaces_confirmed_chunks(session: AsyncSession, doc: Document) -> None:
    """回归：已入库的分块也必须被清掉，不能与新的未入库分块并存。"""
    await doc_service.save_drafts(session, doc.id, drafts("已入库的旧内容"))
    chunks = await doc_service.list_chunks(session, doc.id)
    chunks[0].is_confirmed = 1
    chunks[0].vector_id = f"c{chunks[0].id}"
    doc.status = "indexed"
    await session.commit()

    # 重新切分成 3 块
    await doc_service.save_drafts(session, doc.id, drafts("新一", "新二", "新三"))

    chunks = await doc_service.list_chunks(session, doc.id)
    assert len(chunks) == 3, "旧分块没被清掉，会和新分块重复"
    assert all(chunk.is_confirmed == 0 for chunk in chunks)
    assert not any("已入库的旧内容" in chunk.content for chunk in chunks)

    await session.refresh(doc)
    assert doc.status == "parsed", "内容变了，状态必须退回 parsed 等重新入库"


@pytest.mark.asyncio
async def test_save_drafts_resets_seq(session: AsyncSession, doc: Document) -> None:
    """重新切分后序号从 0 连续编号。"""
    await doc_service.save_drafts(session, doc.id, drafts(*[f"块{i}" for i in range(5)]))
    await doc_service.save_drafts(session, doc.id, drafts("a", "b"))

    chunks = await doc_service.list_chunks(session, doc.id)
    assert [chunk.seq for chunk in chunks] == [0, 1]


@pytest.mark.asyncio
async def test_update_chunk_content_recomputes_tokens(session: AsyncSession, doc: Document) -> None:
    """手改内容后 token 与字符数要重算。"""
    await doc_service.save_drafts(session, doc.id, drafts("原始内容"))
    chunk = (await doc_service.list_chunks(session, doc.id))[0]

    updated = await doc_service.update_chunk_content(
        session, chunk.id, "改成了一个明显更长的内容" * 3
    )
    assert updated.char_count == len(updated.content)
    assert updated.token_count > 0


@pytest.mark.asyncio
async def test_update_confirmed_chunk_rejected(session: AsyncSession, doc: Document) -> None:
    """已入库的分块不允许手改，避免文字与向量不一致。"""
    await doc_service.save_drafts(session, doc.id, drafts("内容"))
    chunk = (await doc_service.list_chunks(session, doc.id))[0]
    chunk.is_confirmed = 1
    await session.commit()

    with pytest.raises(AppError) as exc:
        await doc_service.update_chunk_content(session, chunk.id, "改了")
    assert exc.value.code == "CHUNK_LOCKED"


@pytest.mark.asyncio
async def test_merge_chunks_combines_content(session: AsyncSession, doc: Document) -> None:
    """合并后用第一块承载全部内容，其余删除。"""
    await doc_service.save_drafts(session, doc.id, drafts("第一段", "第二段", "第三段"))
    chunks = await doc_service.list_chunks(session, doc.id)
    ids = [chunk.id for chunk in chunks[:2]]

    merged = await doc_service.merge_chunks(session, ids)
    assert "第一段" in merged.content and "第二段" in merged.content

    remaining = await doc_service.list_chunks(session, doc.id)
    assert len(remaining) == 2


@pytest.mark.asyncio
async def test_merge_requires_two(session: AsyncSession, doc: Document) -> None:
    """只选一块不允许合并。"""

    await doc_service.save_drafts(session, doc.id, drafts("a"))
    chunk = (await doc_service.list_chunks(session, doc.id))[0]

    with pytest.raises(AppError) as exc:
        await doc_service.merge_chunks(session, [chunk.id])
    assert exc.value.code == "MERGE_INVALID"


@pytest.mark.asyncio
async def test_delete_pending_chunk(session: AsyncSession, doc: Document) -> None:
    """未入库的分块可以删除。"""
    await doc_service.save_drafts(session, doc.id, drafts("a", "b"))
    chunks = await doc_service.list_chunks(session, doc.id)
    await doc_service.delete_chunk(session, chunks[0].id)

    remaining = await doc_service.list_chunks(session, doc.id)
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_delete_confirmed_chunk_rejected(session: AsyncSession, doc: Document) -> None:
    """已入库的分块不能单独删。"""

    await doc_service.save_drafts(session, doc.id, drafts("a"))
    chunk = (await doc_service.list_chunks(session, doc.id))[0]
    chunk.is_confirmed = 1
    await session.commit()

    with pytest.raises(AppError) as exc:
        await doc_service.delete_chunk(session, chunk.id)
    assert exc.value.code == "CHUNK_LOCKED"


@pytest.mark.asyncio
async def test_index_without_chunks_fails(session: AsyncSession, doc: Document) -> None:
    """没有分块时入库报人话错误。"""

    with pytest.raises(AppError) as exc:
        await doc_service.index_document(session, doc.id)
    assert exc.value.code == "NO_CHUNKS_TO_INDEX"


@pytest.mark.asyncio
async def test_chunk_count_matches_drafts(session: AsyncSession, doc: Document) -> None:
    """分块数量与草稿数量一致（防止静默丢块）。"""
    count = await doc_service.save_drafts(session, doc.id, drafts(*[f"内容{i}" for i in range(7)]))
    rows = (
        await session.execute(Chunk.__table__.select().where(Chunk.document_id == doc.id))
    ).all()
    assert count == 7
    assert len(rows) == 7
