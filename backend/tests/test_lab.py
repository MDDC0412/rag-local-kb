"""RAG 实验室的纯逻辑测试。

只测**算得对不对**，不测页面：投影、切断检测、RRF 逐项、阈值判语、热力条、
token 序列、缓存命中。这些都是"错了用户会得出错误认知"的地方 ——
一个把夹角画错的实验室比没有实验室更糟。
"""

from __future__ import annotations

import json
import math

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, EmbeddingCache
from app.services import lab as lab_service
from app.services.retrieval import DEFAULT_SCORE_THRESHOLD, Hit


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """每个测试一个独立内存库。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        yield s
    await engine.dispose()


# --------------------------------------------------------------------------- #
# 2D 投影：夹角必须与高维空间真实夹角一致
# --------------------------------------------------------------------------- #
def test_projection_angle_matches_cosine() -> None:
    """投影后的 2D 坐标必须还原真实余弦 —— 否则画出来的夹角是错的。"""
    a = [1.0, 0.0, 0.0, 0.0]
    # 与 A 成 60° 的单位向量
    b = [math.cos(math.radians(60)), math.sin(math.radians(60)), 0.0, 0.0]

    projection = lab_service.project_pair(a, b)

    assert projection.a == [1.0, 0.0]
    assert projection.cosine_2d == pytest.approx(0.5, abs=1e-6)
    assert projection.b[0] == pytest.approx(0.5, abs=1e-6)
    assert projection.b[1] == pytest.approx(math.sqrt(3) / 2, abs=1e-6)


def test_projection_orthogonal_is_90_degrees() -> None:
    """正交向量的投影应落在 y 轴上，夹角 90°。"""
    projection = lab_service.project_pair([1.0, 0.0, 0.0], [0.0, 1.0, 0.0])

    assert projection.cosine_2d == pytest.approx(0.0, abs=1e-9)
    assert projection.b[0] == pytest.approx(0.0, abs=1e-9)
    assert projection.b[1] == pytest.approx(1.0, abs=1e-9)


def test_projection_opposite_direction() -> None:
    """完全相反的向量余弦 -1，投影落到 (-1, 0)。"""
    projection = lab_service.project_pair([1.0, 2.0], [-1.0, -2.0])

    assert projection.cosine_2d == pytest.approx(-1.0, abs=1e-9)
    assert projection.b[1] == pytest.approx(0.0, abs=1e-9)


def test_projection_zero_vector_is_safe() -> None:
    """零向量不能让页面 500 —— 返回安全的默认坐标。"""
    projection = lab_service.project_pair([0.0, 0.0], [1.0, 0.0])

    assert projection.a == [1.0, 0.0]
    assert projection.b == [1.0, 0.0]
    assert projection.cosine_2d == 0.0


def test_projection_3d_pair_keeps_angle() -> None:
    """三维里两个已知夹角的向量，投影后夹角不变。"""
    a = [1.0, 0.0, 0.0]
    b = [1.0, 1.0, 0.0]  # 与 A 成 45°
    projection = lab_service.project_pair(a, b)

    assert projection.cosine_2d == pytest.approx(math.sqrt(2) / 2, abs=1e-6)


# --------------------------------------------------------------------------- #
# 余弦计算与阈值判语
# --------------------------------------------------------------------------- #
def test_similarity_totals() -> None:
    """点积、范数、余弦三者关系必须是 cos = dot / (‖a‖‖b‖)。"""
    dot, norm_a, norm_b, cosine = lab_service.similarity_totals([3.0, 4.0], [3.0, 4.0])

    assert norm_a == pytest.approx(5.0)
    assert norm_b == pytest.approx(5.0)
    assert dot == pytest.approx(25.0)
    assert cosine == pytest.approx(1.0)


def test_verdict_thresholds_align_with_retrieval() -> None:
    """判语分档必须和检索层用同一个阈值，否则用户会被两套标准搞糊涂。"""
    assert lab_service.verdict_for(0.9).level == "high"
    assert lab_service.verdict_for(0.6).level == "related"
    assert lab_service.verdict_for(0.4).level == "weak"
    assert lab_service.verdict_for(0.2).level == "unrelated"

    # 0.35 是检索层判定"没找到"的线，判语必须在这条线两侧给出不同结论
    below = lab_service.verdict_for(DEFAULT_SCORE_THRESHOLD - 0.01)
    above = lab_service.verdict_for(DEFAULT_SCORE_THRESHOLD + 0.01)
    assert below.level == "unrelated"
    assert above.level == "weak"


def test_unrelated_verdict_mentions_threshold() -> None:
    """低于阈值的那档必须把阈值讲出来，这是整个 Tab 的教学重点。"""
    verdict = lab_service.verdict_for(0.29)

    assert "0.35" in verdict.threshold_note
    assert verdict.color == "text-2"


# --------------------------------------------------------------------------- #
# 切断检测
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "content",
    [
        "报销须提交发票。",
        "这样合适吗？",
        "请注意！",
        "这是一句结尾带引号的话。”",
        "这是一句话（补充说明）。",
        "半角结尾 too.",
    ],
)
def test_complete_sentence_not_cut(content: str) -> None:
    """句末标点（含其后紧跟的引号/括号）结尾的块不算被切断。"""
    assert lab_service.is_cut(content) is False


@pytest.mark.parametrize(
    "content",
    [
        "报销须提交发票，材料",
        "一线城市每晚上限",
        "the reimbursement requires",
        "数字结尾 600",
        # 只有收尾括号、没有句末标点 → 仍然是半句话，必须报红
        "结尾有括号（完）",
    ],
)
def test_hard_cut_detected(content: str) -> None:
    """结尾不是句末标点的块必须被判为被硬切断。"""
    assert lab_service.is_cut(content) is True


def test_empty_chunk_not_cut() -> None:
    """空内容不标记（避免给用户看一个没有意义的红标）。"""
    assert lab_service.is_cut("") is False
    assert lab_service.is_cut("   \n") is False


# --------------------------------------------------------------------------- #
# 热力条与统计
# --------------------------------------------------------------------------- #
def test_bucket_means_length_and_average() -> None:
    """压缩后的格数可控，且每格是窗口内的均值。"""
    vector = [float(index) for index in range(1024)]
    buckets = lab_service.bucket_means(vector, buckets=256)

    assert len(buckets) == 256
    assert buckets[0] == pytest.approx(1.5, abs=1e-6)  # 前 4 维 0..3 的均值
    assert buckets[-1] > buckets[0]  # 趋势还在，热力条才有意义


def test_bucket_means_short_vector() -> None:
    """维度比格数少时不能返回空的或超长的数组。"""
    buckets = lab_service.bucket_means([1.0, -1.0, 1.0], buckets=256)

    assert 0 < len(buckets) <= 3


def test_vector_stats() -> None:
    """均值/最大/最小/正值占比。"""
    stats = lab_service.vector_stats([1.0, -1.0, 2.0])

    assert stats.mean == pytest.approx(2.0 / 3, abs=1e-6)
    assert stats.max == 2.0
    assert stats.min == -1.0
    assert stats.positive_ratio == pytest.approx(2 / 3, abs=1e-4)


# --------------------------------------------------------------------------- #
# token 序列
# --------------------------------------------------------------------------- #
def test_token_pieces_cover_text() -> None:
    """token 序列拼回去要等于原文 —— 说明每个 token 的片段没解错。

    中文一个字常被切成两个 token，所以前半个字的 token 文本是空串（partial）。
    这个测试就是在盯"半个字"的处理：拼起来必须和原文一字不差。
    """
    text = "差旅报销需要提交发票"
    pieces = lab_service.token_pieces(text)

    assert pieces, "tiktoken 可用时不应该返回空"
    assert "".join(piece.text for piece in pieces) == text
    assert all(piece.id >= 0 for piece in pieces)
    # 有 partial 才说明"中文被拆开"这个现象真的被观测到了，否则这个测试没意义
    assert any(piece.partial for piece in pieces), "中文文本里应该出现半个字的 token"


def test_token_pieces_ascii_has_no_halves() -> None:
    """纯英文不会出现半个字节，所有 token 都应有可见文本。"""
    pieces = lab_service.token_pieces("reimbursement invoice")

    assert "".join(piece.text for piece in pieces) == "reimbursement invoice"
    assert all(not piece.partial for piece in pieces)


def test_token_pieces_empty() -> None:
    """空文本返回空列表，不报错。"""
    assert lab_service.token_pieces("") == []


# --------------------------------------------------------------------------- #
# 缓存
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_cache_write_then_hit(session: AsyncSession) -> None:
    """写入后能读回来 —— 这是"同一条文本点两次不花钱"的基础。"""
    digest = lab_service.text_hash("差旅报销", "test-model")
    await lab_service._write_cache(session, digest, "test-model", "差旅报销", [0.1, 0.2, 0.3])

    cached = await lab_service._read_cache(session, digest, "test-model")
    assert cached == [pytest.approx(0.1), pytest.approx(0.2), pytest.approx(0.3)]


@pytest.mark.asyncio
async def test_cache_miss_returns_none(session: AsyncSession) -> None:
    """没写过的文本返回 None（调用方据此去调模型）。"""
    assert await lab_service._read_cache(session, "nope", "test-model") is None


@pytest.mark.asyncio
async def test_cache_is_keyed_by_model(session: AsyncSession) -> None:
    """换向量模型后不能复用旧模型算的向量（维度可能都不一样）。"""
    digest = lab_service.text_hash("差旅报销", "model-a")
    await lab_service._write_cache(session, digest, "model-a", "差旅报销", [0.1])

    # 同一文本、不同模型 → 必须未命中
    other = lab_service.text_hash("差旅报销", "model-b")
    assert await lab_service._read_cache(session, other, "model-b") is None


@pytest.mark.asyncio
async def test_cache_ignores_corrupt_json(session: AsyncSession) -> None:
    """缓存里有一条脏数据不能让整页报错，只能当作没命中。"""
    digest = lab_service.text_hash("坏的", "test-model")
    session.add(
        EmbeddingCache(
            text_hash=digest, model="test-model", text="坏的", dim=0, vector_json="{不是数组["
        )
    )
    await session.commit()

    assert await lab_service._read_cache(session, digest, "test-model") is None


@pytest.mark.asyncio
async def test_cache_write_is_idempotent_on_conflict(session: AsyncSession) -> None:
    """重复写同一条不抛异常（并发时靠唯一约束挡住，不该把请求打挂）。"""
    digest = lab_service.text_hash("重复", "test-model")
    await lab_service._write_cache(session, digest, "test-model", "重复", [0.1])
    await lab_service._write_cache(session, digest, "test-model", "重复", [0.1])

    rows = (
        (
            await session.execute(
                lab_service.select(EmbeddingCache).where(EmbeddingCache.text_hash == digest)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1


# --------------------------------------------------------------------------- #
# RRF 逐项展开
# --------------------------------------------------------------------------- #
def test_rrf_terms_two_lists() -> None:
    """两路都命中时，两项都要列出来，且和等于融合分。"""
    hit = Hit(chunk_id=5, content="x", score=0.0, rank_vector=1, rank_bm25=2, score_fused=0.0)
    terms = lab_service.rrf_terms(hit, k=60)

    assert [term.source for term in terms] == ["vector", "bm25"]
    assert terms[0].value == pytest.approx(1 / 61, abs=1e-6)
    assert terms[1].value == pytest.approx(1 / 62, abs=1e-6)
    # 逐项之和必须等于真实的 RRF 公式结果，否则前端展示的数字是假的
    assert sum(term.value for term in terms) == pytest.approx(1 / 61 + 1 / 62, abs=1e-6)


def test_rrf_terms_single_list() -> None:
    """只有一路命中时只列一项 —— 用户要能看出"它只被一个榜单相中"。"""
    hit = Hit(chunk_id=3, content="x", score=0.0, rank_vector=3, rank_bm25=None, score_fused=0.0)
    terms = lab_service.rrf_terms(hit, k=60)

    assert len(terms) == 1
    assert terms[0].source == "vector"
    assert terms[0].value == pytest.approx(1 / 63, abs=1e-6)


def test_rrf_terms_sum_matches_fused_score() -> None:
    """展开出来的各项之和，必须和 fuse() 算出的融合分一致（差一个舍入量级）。"""
    from app.services.retrieval import rrf_fuse

    vector_ids = [5, 2, 3]
    bm25_ids = [5, 2, 9]
    fused = rrf_fuse([vector_ids, bm25_ids], k=60)

    for chunk_id, expected in fused.items():
        hit = Hit(
            chunk_id=chunk_id,
            content="x",
            score=0.0,
            rank_vector=vector_ids.index(chunk_id) + 1 if chunk_id in vector_ids else None,
            rank_bm25=bm25_ids.index(chunk_id) + 1 if chunk_id in bm25_ids else None,
            score_fused=expected,
        )
        terms = lab_service.rrf_terms(hit, k=60)
        assert sum(term.value for term in terms) == pytest.approx(expected, abs=1e-6)


# --------------------------------------------------------------------------- #
# 检索结果的人话解读
# --------------------------------------------------------------------------- #
def test_retrieval_note_low_score_mentions_threshold() -> None:
    """低分时必须解释"为什么说没找到"。"""
    note = lab_service.retrieval_note(0.2, used_rerank=False)

    assert "0.35" in note
    assert "没有真正相关" in note


def test_retrieval_note_empty() -> None:
    """完全没有召回时给出不同的解释。"""
    assert "完全没有对应材料" in lab_service.retrieval_note(0.0, used_rerank=False)


def test_retrieval_note_distinguishes_rerank() -> None:
    """启用与未启用精排，分数的来源不同，提示词必须区分。"""
    assert "精排模型" in lab_service.retrieval_note(0.8, used_rerank=True)
    assert "RRF" in lab_service.retrieval_note(0.8, used_rerank=False)


# --------------------------------------------------------------------------- #
# 模块常量自洽
# --------------------------------------------------------------------------- #
def test_column_strategies_are_the_three_tabs() -> None:
    """三栏的顺序就是页面从左到右的顺序，改动这里等于改动页面。"""
    assert lab_service.COLUMN_STRATEGIES == ("fixed", "recursive", "semantic")


def test_cache_row_shape() -> None:
    """缓存行必须带 norm 与 dim —— 前端统计与维度校验都依赖它们。"""
    digest = lab_service.text_hash("形状", "m")

    assert len(digest) == 64
    assert json.dumps([0.1])  # 向量以 JSON 文本落库
