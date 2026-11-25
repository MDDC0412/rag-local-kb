"""检索调试信息（「检索详情」面板）的回归测试。

这里盯的是一个曾经真实存在的 bug：
``RetrievalDebug.query`` 从没被赋值，图里存进 ``retrieval_json.query`` 的
永远是最初那个硬编码的空串 ``""``，于是前端弹窗里显示成「查询「」」。

根因是图里走的是分步节点（recall → fuse → rerank），**不经过** ``retrieve()``，
而只有 ``retrieve()`` 里补过 ``debug.query``；``fuse()`` 里则写死 ``query=""``。
修法是把 query 传进 ``fuse()``。下面的测试同时盯住这两条路径。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, Chunk, Document, KnowledgeBase
from app.graph.nodes import fuse_node
from app.services.retrieval import RetrievalOptions, fuse


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """内存库，带一个知识库和两个分块。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        kb = KnowledgeBase(name="t", description="")
        s.add(kb)
        await s.flush()
        doc = Document(
            kb_id=kb.id,
            filename="a.md",
            stored_path="/tmp/none.md",
            file_hash="h" * 64,
            status="indexed",
        )
        s.add(doc)
        await s.flush()
        for index in range(2):
            s.add(
                Chunk(
                    document_id=doc.id,
                    kb_id=kb.id,
                    seq=index,
                    content=f"第 {index} 块内容",
                    is_confirmed=1,
                )
            )
        await s.commit()
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_fuse_node_records_query_for_debug_panel(session: AsyncSession) -> None:
    """分步节点这条路（图里真正走的）必须把 query 记进 debug。

    这是用户看到「查询「」为空」的直接原因：图不经过 retrieve()。
    """
    state = {
        "question": "差旅住宿标准是多少",
        "rewritten": "",
        "kb_id": 1,
        "options": RetrievalOptions(),
        "expanded": False,
        "vector_hits": [(1, 0.9), (2, 0.5)],
        "bm25_hits": [(2, 1.2)],
    }

    updated = await fuse_node(state, session=session)  # type: ignore[arg-type]
    debug = updated["debug"]

    assert debug.query == "差旅住宿标准是多少"


@pytest.mark.asyncio
async def test_fuse_node_prefers_rewritten_query(session: AsyncSession) -> None:
    """开了查询改写时，详情里应显示真正拿去检索的那个改写后问题。"""
    state = {
        "question": "那个报销怎么弄",
        "rewritten": "差旅报销的提交流程是什么",
        "kb_id": 1,
        "options": RetrievalOptions(),
        "expanded": False,
        "vector_hits": [(1, 0.9)],
        "bm25_hits": [],
    }

    updated = await fuse_node(state, session=session)  # type: ignore[arg-type]
    assert updated["debug"].query == "差旅报销的提交流程是什么"


@pytest.mark.asyncio
async def test_fuse_records_query_directly(session: AsyncSession) -> None:
    """业务层 fuse() 自己也要能记 query，不依赖调用方事后补。"""
    hits, debug = await fuse(
        session,
        vector_hits=[(1, 0.9)],
        bm25_hits=[(2, 0.4)],
        options=RetrievalOptions(),
        query="住宿标准",
    )

    assert hits, "融合后不该为空"
    assert debug.query == "住宿标准"


@pytest.mark.asyncio
async def test_fuse_query_defaults_to_empty_without_argument(session: AsyncSession) -> None:
    """不传 query 时保持空串，不抛异常（旧调用方仍可用）。"""
    _hits, debug = await fuse(
        session,
        vector_hits=[(1, 0.9)],
        bm25_hits=[],
        options=RetrievalOptions(),
    )
    assert debug.query == ""


@pytest.mark.asyncio
async def test_rerank_node_skipped_path_records_top_score(session: AsyncSession) -> None:
    """关闭重排时 top_score 不能留着 0：详情里那个数字要有意义。

    这条和 query 是同一类 bug —— 属性只在 retrieve() 里赋值，图不经过它。
    """
    from app.graph.nodes import rerank_node

    state = {
        "question": "问题",
        "kb_id": 1,
        "options": RetrievalOptions(use_rerank=False),
        "hits": [],
    }
    updated = await rerank_node(state, rerank_spec=None)  # type: ignore[arg-type]
    # 没有命中时保持 0，但不该抛异常
    assert updated["hits"] == []


@pytest.mark.asyncio
async def test_rerank_node_skipped_sets_top_score_from_fused(
    session: AsyncSession,
) -> None:
    """有关闭重排但命中时，top_score 取融合分最高值。"""
    from app.graph.nodes import fuse_node, rerank_node
    from app.services.retrieval import Hit

    state = {
        "question": "差旅",
        "kb_id": 1,
        "options": RetrievalOptions(use_rerank=False),
        "vector_hits": [(1, 0.9), (2, 0.4)],
        "bm25_hits": [(1, 1.0)],
    }
    fused = await fuse_node(state, session=session)  # type: ignore[arg-type]
    hits = fused["hits"]
    assert hits and isinstance(hits[0], Hit)

    state.update({"hits": hits, "debug": fused["debug"]})
    updated = await rerank_node(state, rerank_spec=None)  # type: ignore[arg-type]

    debug = updated["debug"]
    assert debug is not None
    assert debug.top_score > 0, "关闭重排时 top_score 应取融合分，而不是 0"
    assert debug.top_score == pytest.approx(round(hits[0].score_fused, 6))
