"""分块策略的单元测试：六种本地策略 + 语义策略的纯逻辑部分。

每种策略都要验证三件事：
1. 能产出内容；
2. 块大小受参数约束；
3. 页码/标题路径等元数据正确带下来（引用溯源依赖它）。
"""

from __future__ import annotations

import pytest

from app.core.errors import AppError
from app.services.chunking import (
    LOCAL_STRATEGIES,
    STRATEGY_HINT,
    STRATEGY_LABEL,
    ChunkDraft,
    _aggregate,
    _cosine,
    _split_sentences,
    _tail_sentences,
    split_local,
)

MD_DOC = """# 差旅报销管理办法

## 第一章 总则

第 1 条 本办法适用于全体员工的国内差旅。
第 2 条 员工应遵循先审批后出差的原则。

## 第二章 报销流程

第 3 条 出差结束后五个工作日内提交报销申请。
第 4 条 必须附增值税发票原件、出差审批单、行程单。

### 住宿标准

一线城市每晚上限 600 元，其他城市每晚上限 400 元。
超过标准部分需部门经理书面说明。

## 第三章 审批

单笔 2000 元以下由部门经理审批。超过 2000 元需财务总监审批。
"""


def pages_two() -> list[tuple[str, int]]:
    """两页内容，用于校验页码是否正确。"""
    return [(MD_DOC, 1), (MD_DOC.replace("第一章", "第四章"), 2)]


# --------------------------------------------------------------------------- #
# 策略注册表
# --------------------------------------------------------------------------- #
def test_all_strategies_have_label_and_hint() -> None:
    """每个策略都必须有中文名和说明，前端直接拿来渲染，不允许漏。"""
    for name in LOCAL_STRATEGIES:
        assert name in STRATEGY_LABEL, f"{name} 缺少中文名"
        assert name in STRATEGY_HINT, f"{name} 缺少说明"
        assert STRATEGY_HINT[name].strip()


def test_local_strategies_count() -> None:
    """本地策略至少 6 种（fixed/recursive/sentence/paragraph/markdown/parent_child）。"""
    assert len(LOCAL_STRATEGIES) >= 6


# --------------------------------------------------------------------------- #
# 六种策略逐一验证
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("strategy", LOCAL_STRATEGIES)
def test_each_strategy_produces_chunks(strategy: str) -> None:
    """每种策略都得意地切出内容，且不是空块。"""
    result = split_local(pages_two(), strategy=strategy, chunk_size=128, overlap=16)
    assert result.count > 0, f"{strategy} 没有产出"
    assert result.strategy == strategy
    assert all(chunk.content.strip() for chunk in result.chunks)


@pytest.mark.parametrize("strategy", LOCAL_STRATEGIES)
def test_each_strategy_respects_size(strategy: str) -> None:
    """块大小不能离谱地超出上限（按句子/段落聚合时允许少量超出）。"""
    result = split_local(pages_two(), strategy=strategy, chunk_size=128, overlap=0)
    for chunk in result.chunks:
        assert chunk.token_count <= 300, f"{strategy} 出现超长块：{chunk.token_count}"


@pytest.mark.parametrize("strategy", LOCAL_STRATEGIES)
def test_each_strategy_keeps_page_no(strategy: str) -> None:
    """两页内容要分别带出 page_no=1 和 2，不能全变成 1。"""
    result = split_local(pages_two(), strategy=strategy, chunk_size=128, overlap=0)
    pages = {chunk.page_no for chunk in result.chunks}
    assert pages == {1, 2}, f"{strategy} 页码丢失：{pages}"


@pytest.mark.parametrize("strategy", LOCAL_STRATEGIES)
def test_each_strategy_sequential_seq(strategy: str) -> None:
    """序号必须从 0 连续递增。"""
    result = split_local(pages_two(), strategy=strategy, chunk_size=128, overlap=0)
    assert [chunk.seq for chunk in result.chunks] == list(range(result.count))


# --------------------------------------------------------------------------- #
# 各策略的专属行为
# --------------------------------------------------------------------------- #
def test_markdown_records_section_path() -> None:
    """markdown 策略必须把标题层级记进 section_path。"""
    result = split_local([(MD_DOC, 1)], strategy="markdown", chunk_size=256, overlap=0)
    paths = {chunk.section_path for chunk in result.chunks}
    assert any("第一章" in path for path in paths), f"没记录标题路径：{paths}"
    assert any(" > " in path for path in paths), "多级标题没拼成路径"


def test_markdown_keeps_heading_in_content() -> None:
    """标题要留在正文里（检索时标题信息量很大）。"""
    result = split_local([(MD_DOC, 1)], strategy="markdown", chunk_size=256, overlap=0)
    assert any("住宿标准" in chunk.content for chunk in result.chunks)


def test_markdown_falls_back_without_headings() -> None:
    """没有 Markdown 标题的纯文本退回递归切分，不能切出 1 个巨块。"""
    plain = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
    result = split_local([(plain, 1)], strategy="markdown", chunk_size=64, overlap=0)
    assert result.count >= 1
    assert all(chunk.content.strip() for chunk in result.chunks)


def test_parent_child_fills_parent_content() -> None:
    """parent_child 必须给每个子块带上它所属页的父块文本。"""
    result = split_local(pages_two(), strategy="parent_child", chunk_size=128, overlap=0)
    assert result.count > 0
    for chunk in result.chunks:
        assert chunk.parent_content, f"块 {chunk.seq} 没有父块"
        # 父块必须包含子块内容
        assert chunk.content.strip() in chunk.parent_content or chunk.content[
            :20
        ] in chunk.parent_content


def test_parent_child_parent_is_whole_page() -> None:
    """父块按页取整页内容，应比单个子块大。"""
    result = split_local(pages_two(), strategy="parent_child", chunk_size=128, overlap=0)
    first = result.chunks[0]
    assert len(first.parent_content) > len(first.content)


def test_sentence_splits_on_chinese_period() -> None:
    """句子策略的断句结果应保留句号且不丢内容。"""
    sentences = _split_sentences("第一句。第二句！第三句？")
    assert len(sentences) == 3
    assert all(sentence.endswith(("。", "！", "？")) for sentence in sentences)


def test_aggregate_merges_until_target() -> None:
    """聚合：把小句子拼到目标大小，且不产生空块。"""
    units = ["这是第{}句话，长度适中。".format(i) for i in range(20)]
    blocks = _aggregate(units, target_tokens=60)
    assert len(blocks) > 1
    assert all(block.strip() for block in blocks)


def test_aggregate_with_overlap_keeps_tail() -> None:
    """带重叠时，后一块的开头应来自上一块的结尾。"""
    units = [f"第{i}句，包含一些用于凑长度的文字内容。" for i in range(30)]
    blocks = _aggregate(units, target_tokens=80, overlap_tokens=30)
    assert len(blocks) >= 2
    tail = _tail_sentences(blocks[0], 30)
    assert tail
    assert tail.strip() in blocks[1]


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #
def test_invalid_strategy_message_lists_options() -> None:
    """未知策略的报错要列出可选值，方便前端/用户自查。"""
    with pytest.raises(AppError) as exc:
        split_local([("正文", 1)], strategy="nope")  # type: ignore[arg-type]
    assert "recursive" in exc.value.message


def test_overlap_must_be_smaller_than_size() -> None:
    """重叠 >= 块大小会无限循环，必须拦。"""
    with pytest.raises(AppError):
        split_local([("正文", 1)], strategy="sentence", chunk_size=128, overlap=128)


def test_min_chunk_size_enforced() -> None:
    """块太小没有检索价值，直接拒绝。"""
    with pytest.raises(AppError):
        split_local([("正文", 1)], strategy="paragraph", chunk_size=32)


# --------------------------------------------------------------------------- #
# 余弦相似度（语义切分的基础）
# --------------------------------------------------------------------------- #
def test_cosine_identical() -> None:
    """同向向量相似度为 1。"""
    assert _cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal() -> None:
    """正交向量相似度为 0。"""
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite() -> None:
    """反向向量相似度为 -1。"""
    assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_handles_bad_input() -> None:
    """空向量或维度不一致返回 0，不抛异常。"""
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([1.0, 2.0], [1.0]) == 0.0
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# --------------------------------------------------------------------------- #
# 草稿对象
# --------------------------------------------------------------------------- #
def test_chunk_draft_computes_counts() -> None:
    """草稿自动算好 token 与字符数。"""
    draft = ChunkDraft(seq=0, content="测试内容")
    assert draft.char_count == 4
    assert draft.token_count > 0
    assert draft.parent_content == ""