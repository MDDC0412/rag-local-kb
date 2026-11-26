"""文档列表的分页、搜索、筛选与排序测试。

这一页要解决的是"库里有上百篇文档怎么找"。所以测试盯的是三件容易错的事：

1. **total 必须是过滤后的命中数**，不是全库文档数 —— 否则搜索后页数算错；
2. **翻页不能重复或漏项** —— 排序键相同（同一秒上传）时也要有稳定顺序；
3. **搜索的边界**：`%`、`_` 是 SQL 通配符，用户输进来不能被当成通配符展开。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Category, Document, DocumentTag, KnowledgeBase, Tag
from app.services import documents as doc_service


def _doc(
    doc_id: int,
    filename: str,
    *,
    kb_id: int = 1,
    status: str = "indexed",
    summary: str = "",
    size: int = 100,
    char_count: int = 1000,
    category_id: int | None = None,
    created_at: datetime | None = None,
) -> Document:
    """造一条文档记录。"""
    return Document(
        id=doc_id,
        kb_id=kb_id,
        filename=filename,
        stored_path=f"/tmp/{doc_id}.md",
        file_hash=f"hash{doc_id}",
        size=size,
        status=status,
        page_count=1,
        char_count=char_count,
        summary=summary,
        category_id=category_id,
        created_at=created_at or (datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=doc_id)),
    )


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """内存库 + 一个知识库 + 若干文档。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with maker() as s:
        s.add(KnowledgeBase(id=1, name="测试库", description=""))
        # 25 篇文档：文件名带编号，状态交替，便于验证分页与筛选
        for index in range(1, 26):
            s.add(
                _doc(
                    index,
                    f"制度文件{index:02d}.md",
                    status="indexed" if index % 2 else "parsed",
                    summary=f"这是第 {index} 份文档的摘要" if index % 3 == 0 else "",
                    size=100 * index,
                    char_count=1000 * index,
                )
            )
        # 一篇别的库的文档，不能被查出来
        s.add(_doc(99, "别的库.md", kb_id=2))
        await s.commit()
        yield s
    await engine.dispose()


# --------------------------------------------------------------------------- #
# 分页
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_first_page_returns_page_size(session: AsyncSession) -> None:
    """第一页返回 page_size 条，total 是全量。"""
    rows, total = await doc_service.list_documents(session, 1, page=1, page_size=10)

    assert len(rows) == 10
    assert total == 25


@pytest.mark.asyncio
async def test_last_page_returns_remainder(session: AsyncSession) -> None:
    """最后一页只返回剩下的，不能补空行。"""
    rows, total = await doc_service.list_documents(session, 1, page=3, page_size=10)

    assert len(rows) == 5
    assert total == 25


@pytest.mark.asyncio
async def test_page_beyond_end_is_empty_but_total_kept(session: AsyncSession) -> None:
    """页数超界返回空列表，但 total 仍是命中数（前端据此纠正页码）。"""
    rows, total = await doc_service.list_documents(session, 1, page=99, page_size=10)

    assert rows == []
    assert total == 25


@pytest.mark.asyncio
async def test_pages_do_not_overlap_or_skip(session: AsyncSession) -> None:
    """逐页翻完，所有 id 恰好出现一次 —— 不重不漏。

    这是分页最容易出的 bug：排序键不唯一时，SQLite 对相同键的返回顺序
    可能在不同查询间变化，导致同一篇文档出现在两页。
    """
    seen: list[int] = []
    for page in range(1, 4):
        rows, _ = await doc_service.list_documents(session, 1, page=page, page_size=10)
        seen.extend(row.id for row in rows)

    assert len(seen) == 25
    assert len(set(seen)) == 25, "同一篇文档不能出现在两页"


@pytest.mark.asyncio
async def test_documents_at_same_timestamp_still_paginate_stably(session: AsyncSession) -> None:
    """创建时间完全相同的文档，翻页仍然不重不漏（靠 id 兜底排序）。"""
    same = datetime(2026, 5, 1, tzinfo=UTC)
    for index in range(200, 215):
        session.add(_doc(index, f"同秒{index}.md", created_at=same))
    await session.commit()

    seen: list[int] = []
    for page in range(1, 5):
        rows, _ = await doc_service.list_documents(session, 1, page=page, page_size=10)
        seen.extend(row.id for row in rows)

    assert len(seen) == len(set(seen)), "同秒创建的文档也不能重复出现"


@pytest.mark.asyncio
async def test_page_size_is_capped(session: AsyncSession) -> None:
    """page_size 传超大值会被夹到上限，防止一次拉爆内存。"""
    rows, _ = await doc_service.list_documents(session, 1, page=1, page_size=9999)

    assert len(rows) <= doc_service.MAX_PAGE_SIZE


@pytest.mark.asyncio
async def test_invalid_page_is_clamped(session: AsyncSession) -> None:
    """page 传 0 或负数当作第 1 页，不报错也不返回古怪结果。"""
    rows, _ = await doc_service.list_documents(session, 1, page=0, page_size=5)

    assert len(rows) == 5


@pytest.mark.asyncio
async def test_other_kb_documents_are_excluded(session: AsyncSession) -> None:
    """只返回本知识库的文档。"""
    rows, total = await doc_service.list_documents(session, 1, page=1, page_size=100)

    assert total == 25
    assert all(row.kb_id == 1 for row in rows)


# --------------------------------------------------------------------------- #
# 搜索
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_search_by_filename(session: AsyncSession) -> None:
    """按文件名关键词搜索。"""
    rows, total = await doc_service.list_documents(session, 1, keyword="制度文件01", page_size=100)

    assert total == 1
    assert rows[0].filename == "制度文件01.md"


@pytest.mark.asyncio
async def test_search_is_case_insensitive(session: AsyncSession) -> None:
    """英文文件名大小写不敏感。"""
    session.add(_doc(300, "AnnualReport.PDF"))
    await session.commit()

    rows, total = await doc_service.list_documents(
        session, 1, keyword="annualreport", page_size=100
    )

    assert total == 1
    assert rows[0].filename == "AnnualReport.PDF"


@pytest.mark.asyncio
async def test_search_matches_summary(session: AsyncSession) -> None:
    """摘要里命中也算 —— 用户往往记得"内容"而不是文件名。"""
    rows, total = await doc_service.list_documents(session, 1, keyword="第 3 份文档", page_size=100)

    assert total >= 1
    assert any("第 3 份文档" in row.summary for row in rows)


@pytest.mark.asyncio
async def test_search_total_is_filtered_not_global(session: AsyncSession) -> None:
    """⚠️ total 必须是**过滤后**的命中数。

    如果前端拿全库总数算页数，搜索 1 篇会显示 3 页 —— 翻过去全是空的。
    """
    _, total = await doc_service.list_documents(session, 1, keyword="制度文件01", page_size=10)

    assert total == 1, "搜索命中 1 篇时 total 必须是 1，不是 25"


@pytest.mark.asyncio
async def test_search_no_result(session: AsyncSession) -> None:
    """搜不到时返回空列表和 0。"""
    rows, total = await doc_service.list_documents(session, 1, keyword="完全不存在的名字")

    assert rows == []
    assert total == 0


@pytest.mark.asyncio
async def test_search_blank_keyword_is_ignored(session: AsyncSession) -> None:
    """只输入空格等于没搜索，不能返回空。"""
    _, total = await doc_service.list_documents(session, 1, keyword="   ")

    assert total == 25


@pytest.mark.asyncio
async def test_search_percent_is_literal(session: AsyncSession) -> None:
    """⚠️ 用户输入 `%` 不能被当成 SQL 通配符。

    如果直接拼进 LIKE，`%` 会匹配所有文档 —— 搜一个百分号却搜出全库。
    """
    session.add(_doc(301, "折扣100%说明.md"))
    await session.commit()

    rows, total = await doc_service.list_documents(session, 1, keyword="100%", page_size=100)

    assert total == 1
    assert rows[0].filename == "折扣100%说明.md"


@pytest.mark.asyncio
async def test_search_underscore_is_literal(session: AsyncSession) -> None:
    """`_` 在 LIKE 里是"任意一个字符"，也必须当普通字符处理。"""
    session.add(_doc(302, "a_b.md"))
    session.add(_doc(303, "axb.md"))
    await session.commit()

    rows, total = await doc_service.list_documents(session, 1, keyword="a_b", page_size=100)

    assert total == 1
    assert rows[0].filename == "a_b.md"


# --------------------------------------------------------------------------- #
# 筛选
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_filter_by_status(session: AsyncSession) -> None:
    """按状态筛选，total 同步变化。"""
    rows, total = await doc_service.list_documents(session, 1, status="parsed", page_size=100)

    assert total == 12  # 偶数号 2..24
    assert all(row.status == "parsed" for row in rows)


@pytest.mark.asyncio
async def test_filter_by_category(session: AsyncSession) -> None:
    """按分类筛选。"""
    session.add(Category(id=1, name="制度规范", description=""))
    (await session.get(Document, 1)).category_id = 1
    (await session.get(Document, 2)).category_id = 1
    await session.commit()

    _, total = await doc_service.list_documents(session, 1, category_id=1)

    assert total == 2


@pytest.mark.asyncio
async def test_filter_by_tag_does_not_duplicate_rows(session: AsyncSession) -> None:
    """⚠️ 按标签筛选不能让同一篇文档重复出现。

    用 join 实现时，一篇文档有 3 个标签就会变成 3 行，
    分页和计数全部算错。必须用 exists 子查询。
    """
    session.add(Tag(id=1, name="差旅"))
    session.add(Tag(id=2, name="报销"))
    session.add(DocumentTag(document_id=1, tag_id=1))
    session.add(DocumentTag(document_id=1, tag_id=2))
    await session.commit()

    rows, total = await doc_service.list_documents(session, 1, tag_id=1, page_size=100)

    assert total == 1, "一篇文档挂两个标签，按其中一个筛也只能算 1 篇"
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_search_and_filter_combine(session: AsyncSession) -> None:
    """搜索与筛选是**与**关系，不是或。"""
    rows, total = await doc_service.list_documents(
        session, 1, keyword="制度文件", status="parsed", page_size=100
    )

    assert total == 12
    assert all(row.status == "parsed" for row in rows)


# --------------------------------------------------------------------------- #
# 排序
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_sort_recent_is_default(session: AsyncSession) -> None:
    """默认按上传时间倒序 —— 刚传的文档应在最上面。"""
    rows, _ = await doc_service.list_documents(session, 1, page=1, page_size=5)

    assert rows[0].id == 25


@pytest.mark.asyncio
async def test_sort_oldest(session: AsyncSession) -> None:
    """按上传时间正序。"""
    rows, _ = await doc_service.list_documents(session, 1, sort="oldest", page_size=5)

    assert rows[0].id == 1


@pytest.mark.asyncio
async def test_sort_by_size(session: AsyncSession) -> None:
    """按体积倒序，最大的在前。"""
    rows, _ = await doc_service.list_documents(session, 1, sort="size", page_size=5)

    assert rows[0].size == max(row.size for row in rows)


@pytest.mark.asyncio
async def test_unknown_sort_falls_back_to_default(session: AsyncSession) -> None:
    """前端传了未知排序值时退回默认，不报错。"""
    rows, _ = await doc_service.list_documents(session, 1, sort="乱写的", page_size=5)

    assert rows[0].id == 25


# --------------------------------------------------------------------------- #
# 筛选器候选
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_facets_lists_status_counts(session: AsyncSession) -> None:
    """状态候选要带数量，用户能直接看到"有多少篇还没入库"。"""
    data = await doc_service.document_facets(session, 1)
    by_value = {item["value"]: item["count"] for item in data["statuses"]}

    assert by_value["indexed"] == 13
    assert by_value["parsed"] == 12


@pytest.mark.asyncio
async def test_facets_lists_categories_and_tags(session: AsyncSession) -> None:
    """分类与标签候选只统计本库的文档。"""
    session.add(Category(id=1, name="制度规范", description=""))
    session.add(Tag(id=1, name="差旅"))
    (await session.get(Document, 1)).category_id = 1
    session.add(DocumentTag(document_id=1, tag_id=1))
    await session.commit()

    data = await doc_service.document_facets(session, 1)

    assert data["categories"] == [{"id": 1, "name": "制度规范", "count": 1}]
    assert data["tags"] == [{"id": 1, "name": "差旅", "count": 1}]


@pytest.mark.asyncio
async def test_facets_excludes_other_kb(session: AsyncSession) -> None:
    """别的库的文档不参与统计。"""
    data = await doc_service.document_facets(session, 1)
    total = sum(item["count"] for item in data["statuses"])

    assert total == 25, "本库 25 篇，不能把 kb=2 的那篇算进来"


@pytest.mark.asyncio
async def test_facets_pass_schema_validation(session: AsyncSession) -> None:
    """⚠️ 候选数据必须能通过响应模型的校验。

    只测 service 不够：service 返回普通 dict，类型写错（例如把 count 当成 str）
    要等到真发请求时才 500。这个测试就是替接口层把这道关。
    """
    from app.schemas.document import DocumentFacets, DocumentListOut, DocumentOut

    data = await doc_service.document_facets(session, 1)
    facets = DocumentFacets(**data)
    assert facets.statuses and isinstance(facets.statuses[0].count, int)

    rows, total = await doc_service.list_documents(session, 1, page=1, page_size=2)
    payload = DocumentListOut(
        items=[DocumentOut.model_validate(row) for row in rows],
        total=total,
        page=1,
        page_size=2,
    )
    assert len(payload.items) == 2 and payload.total == 25


# --------------------------------------------------------------------------- #
# 常量自洽
# --------------------------------------------------------------------------- #
def test_sort_orders_are_known_keys() -> None:
    """排序白名单必须与前端下拉项一一对应。"""
    assert set(doc_service.SORT_ORDERS) == {"recent", "oldest", "name", "size", "chars"}


def test_page_size_default_is_sane() -> None:
    """默认每页 20 条，上限 100 条。"""
    assert doc_service.DEFAULT_PAGE_SIZE == 20
    assert doc_service.MAX_PAGE_SIZE == 100
