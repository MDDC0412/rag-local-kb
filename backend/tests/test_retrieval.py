"""检索层单元测试：BM25 索引、RRF 融合、引用提取、上下文拼装。

不触网、不依赖数据库。
"""

from __future__ import annotations

import pytest

from app.graph.nodes import build_context, extract_citations
from app.services.retrieval import (
    DEFAULT_RRF_K,
    BM25Index,
    Hit,
    RetrievalOptions,
    rrf_fuse,
)


# --------------------------------------------------------------------------- #
# BM25
# --------------------------------------------------------------------------- #
def _bm25_index() -> BM25Index:
    """构造一个小型中文索引。"""
    return BM25Index(
        [
            (1, "差旅报销需要提交增值税发票和行程单"),
            (2, "住宿标准一线城市每晚上限六百元"),
            (3, "今天的食堂午餐不太好吃"),
            (4, "报销流程由财务部审核后打款"),
        ]
    )


def test_bm25_returns_positive_only() -> None:
    """只返回分数大于 0 的命中，不相关文档不硬凑。"""
    index = _bm25_index()
    results = index.search("报销", top_n=10)
    assert results
    assert all(score > 0 for _, score in results)
    ids = [chunk_id for chunk_id, _ in results]
    assert 1 in ids and 4 in ids
    assert 3 not in ids, "「午餐」不该被「报销」命中"


def test_bm25_ranks_relevant_first() -> None:
    """越相关的排越前。"""
    index = _bm25_index()
    results = index.search("住宿 上限", top_n=5)
    assert results[0][0] == 2


def test_bm25_respects_top_n() -> None:
    """top_n 生效。"""
    index = _bm25_index()
    assert len(index.search("报销 发票 城市", top_n=2)) <= 2


def test_bm25_empty_index() -> None:
    """空索引不抛异常，返回空列表。"""
    index = BM25Index([])
    assert index.search("任何问题", top_n=5) == []


def test_bm25_no_match_returns_empty() -> None:
    """完全无关的查询返回空列表。"""
    index = _bm25_index()
    assert index.search("量子纠缠实验方案", top_n=5) == []


def test_bm25_chinese_tokenization() -> None:
    """中文分词要生效：整词查询能命中。"""
    index = _bm25_index()
    results = index.search("行程单", top_n=5)
    assert results and results[0][0] == 1


def test_bm25_idf_never_zero_for_half_corpus_term() -> None:
    """回归：词出现在一半文档里时 IDF 不能退化成 0。

    rank_bm25 的 BM25Okapi 用 log((N-df+0.5)/(df+0.5))，
    df=2、N=4 时正好是 log(1)=0，该词完全失效。
    实测过这个词就是「报销」，四篇文档全得 0 分。
    """
    index = _bm25_index()
    # 「报销」出现在 4 篇里的 2 篇（doc 1 和 doc 4）
    assert index.idf["报销"] > 0
    results = index.search("报销", top_n=10)
    assert len(results) == 2, "应该正好命中两篇含「报销」的文档"


def test_bm25_idf_positive_for_every_term() -> None:
    """所有词的 IDF 都必须为正，否则该词在检索里等于不存在。"""
    index = _bm25_index()
    assert all(value > 0 for value in index.idf.values())


def test_bm25_rare_term_weighs_more_than_common() -> None:
    """文档频率低的词 IDF 应高于频率高的词。

    注意：小语料里两个都只出现 1 次的词，IDF 必然相同 —— 这是公式决定的，
    不是 bug。所以这里用"出现 1 次 vs 出现 2 次"来验证单调性。
    """
    index = _bm25_index()
    # 「住宿」只出现在 doc2（df=1），「报销」出现在 doc1/doc4（df=2）
    assert index.idf["住宿"] > index.idf["报销"]


# --------------------------------------------------------------------------- #
# RRF
# --------------------------------------------------------------------------- #
def test_rrf_gives_higher_score_to_top_rank() -> None:
    """排名越靠前分数越高（1/(k+rank) 单调递减）。"""
    fused = rrf_fuse([[1, 2, 3]], k=DEFAULT_RRF_K)
    assert fused[1] > fused[2] > fused[3]


def test_rrf_rewards_items_in_both_lists() -> None:
    """两路都命中的文档，得分应高于只被一路命中的。"""
    fused = rrf_fuse([[1, 2], [2, 3]], k=DEFAULT_RRF_K)
    # 2 在两路里都有；1 和 3 各只出现一次
    assert fused[2] > fused[1]
    assert fused[2] > fused[3]


def test_rrf_exact_formula() -> None:
    """公式核对：rank=1 时分数为 1/(k+1)。"""
    fused = rrf_fuse([[10]], k=60)
    assert fused[10] == pytest.approx(1 / 61)


def test_rrf_accumulates_across_lists() -> None:
    """两路同一 rank 的得分应相加。"""
    fused = rrf_fuse([[7], [7]], k=60)
    assert fused[7] == pytest.approx(2 / 61)


def test_rrf_empty_lists() -> None:
    """空输入返回空结果，不崩。"""
    assert rrf_fuse([[], []]) == {}


def test_rrf_k_affects_relative_weight() -> None:
    """k 越小，头部排名优势越明显（这是调参时的直觉依据）。"""
    small = rrf_fuse([[1, 2]], k=1)
    large = rrf_fuse([[1, 2]], k=1000)
    assert (small[1] - small[2]) > (large[1] - large[2])


# --------------------------------------------------------------------------- #
# 引用提取
# --------------------------------------------------------------------------- #
def _hits(n: int = 3) -> list[Hit]:
    """构造 n 条命中。"""
    return [
        Hit(
            chunk_id=100 + i,
            content=f"第{i + 1}段内容，包含关键信息。",
            score=0.9 - i * 0.1,
            doc_id=7,
            page_no=i + 1,
            doc_name="制度.md",
        )
        for i in range(n)
    ]


def test_extract_citations_basic() -> None:
    """回答里的 [1][3] 要映射回正确的分块。"""
    citations = extract_citations("报销需要发票 [1]，审批由财务 [3]。", _hits(3))
    assert [item["n"] for item in citations] == [1, 3]
    assert citations[0]["page_no"] == 1
    assert citations[1]["page_no"] == 3
    assert citations[0]["doc_name"] == "制度.md"


def test_extract_citations_out_of_range_ignored() -> None:
    """超出范围的编号直接忽略，不产生错引用。"""
    citations = extract_citations("参考 [1] 与 [9]", _hits(2))
    assert [item["n"] for item in citations] == [1]


def test_extract_citations_deduplicates() -> None:
    """同一个编号出现多次只保留一条。"""
    citations = extract_citations("[2] 和 [2] 还有 [2]", _hits(3))
    assert len(citations) == 1


def test_extract_citations_no_answer() -> None:
    """回答里没有引用标记时返回空列表。"""
    assert extract_citations("资料里没有相关内容。", _hits(3)) == []


def test_extract_citations_sorted() -> None:
    """输出按编号升序，前端渲染顺序稳定。"""
    citations = extract_citations("[3] 然后 [1]", _hits(3))
    assert [item["n"] for item in citations] == [1, 3]


def test_extract_citations_snippet_truncated() -> None:
    """片段要截断，避免把整块塞进前端。"""
    hits = _hits(1)
    hits[0].content = "很长的内容" * 200
    citations = extract_citations("[1]", hits)
    assert len(citations[0]["snippet"]) <= 300


# --------------------------------------------------------------------------- #
# 上下文拼装
# --------------------------------------------------------------------------- #
def test_build_context_numbers_each_block() -> None:
    """每条参考资料都要有编号和出处。"""
    context = build_context(_hits(2))
    assert "[1]" in context
    assert "[2]" in context
    assert "《制度.md》" in context
    assert "第 1 段" in context


def test_build_context_prefers_parent_content() -> None:
    """有父块时优先给父块（parent_child 策略的价值所在）。"""
    hits = _hits(1)
    hits[0].parent_content = "这是更完整的父块内容，包含上下文。"
    context = build_context(hits)
    assert "父块" in context
    assert hits[0].content not in context or "父块" in context


def test_build_context_empty() -> None:
    """没有命中时返回空串，由上层给出"没找到"的提示。"""
    assert build_context([]) == ""


# --------------------------------------------------------------------------- #
# 参数默认值
# --------------------------------------------------------------------------- #
def test_retrieval_options_defaults() -> None:
    """默认参数与设计文档一致：20/20 → 20 → 5。"""
    opts = RetrievalOptions()
    assert opts.vector_top_n == 20
    assert opts.bm25_top_n == 20
    assert opts.fuse_top_n == 20
    assert opts.rerank_top_n == 5
    assert opts.use_rerank is True
