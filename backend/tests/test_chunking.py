"""分块与解析的单元测试（不触网、不依赖数据库）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import AppError
from app.services.chunking import (
    MIN_CHUNK_TOKENS,
    _merge_tiny,
    split_local,
)
from app.services.parsing import (
    PageText,
    _clean_page_text,
    ensure_supported,
    parse_markdown,
    parse_text,
)
from app.services.tokenizer import count_tokens, truncate_to_tokens


# --------------------------------------------------------------------------- #
# token 计数
# --------------------------------------------------------------------------- #
def test_count_tokens_nonzero_for_chinese() -> None:
    """中文必须有 token，且比字符数略少（1 汉字约 0.6~1 token）。"""
    text = "差旅报销需要提交发票和行程单"
    tokens = count_tokens(text)
    assert 0 < tokens <= len(text)


def test_count_tokens_empty() -> None:
    """空文本返回 0。"""
    assert count_tokens("") == 0
    assert count_tokens("   ") > 0  # 空白也算一个 token


def test_truncate_to_tokens() -> None:
    """按 token 截断后长度不超过限制。"""
    text = "这是一段用于测试截断的文本。" * 50
    limited = truncate_to_tokens(text, 20)
    assert count_tokens(limited) <= 20
    assert len(limited) < len(text)


def test_truncate_noop_when_short() -> None:
    """未超限时原样返回。"""
    assert truncate_to_tokens("短文本", 100) == "短文本"


# --------------------------------------------------------------------------- #
# 文本清洗
# --------------------------------------------------------------------------- #
def test_clean_removes_page_numbers() -> None:
    """孤立页码行被丢弃，正文保留。"""
    raw = "第一段正文\n12\n第二段正文\n- 3 -\n第三段"
    cleaned = _clean_page_text(raw)
    assert "12" not in cleaned.splitlines()
    assert "第一段正文" in cleaned
    assert "第三段" in cleaned


def test_clean_drops_chinese_page_marker() -> None:
    """「第 5 页」这种页脚也丢弃。"""
    cleaned = _clean_page_text("正文\n第 5 页\n更多正文")
    assert "第 5 页" not in cleaned


def test_clean_collapses_blank_lines() -> None:
    """连续空行压成一个空行。"""
    cleaned = _clean_page_text("A\n\n\n\n\nB")
    assert cleaned == "A\n\nB"


# --------------------------------------------------------------------------- #
# 扩展名校验
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["a.pdf", "b.DOCX", "c.md", "d.TXT", "e.markdown"])
def test_ensure_supported_accepts_known(name: str) -> None:
    """已知扩展名（大小写不敏感）通过。"""
    assert ensure_supported(name).startswith(".")


@pytest.mark.parametrize("name", ["a.exe", "b.zip", "noext", "c.png"])
def test_ensure_supported_rejects_unknown(name: str) -> None:
    """未知扩展名报人话错误。"""
    with pytest.raises(AppError) as exc:
        ensure_supported(name)
    assert exc.value.code == "UNSUPPORTED_FILE_TYPE"
    assert "支持" in exc.value.message


# --------------------------------------------------------------------------- #
# 文本解析
# --------------------------------------------------------------------------- #
def test_parse_text_splits_by_blank_line(tmp_path: Path) -> None:
    """纯文本按空行分段。"""
    path = tmp_path / "a.txt"
    path.write_text("第一段\n\n第二段\n\n第三段", encoding="utf-8")
    result = parse_text(path)
    assert result.page_count == 3
    assert result.pages[0].page_no == 1
    assert "第二段" in result.pages[1].text


def test_parse_text_empty_file(tmp_path: Path) -> None:
    """空文件给出警告而不是崩溃。"""
    path = tmp_path / "e.txt"
    path.write_text("   \n\n  ", encoding="utf-8")
    result = parse_text(path)
    assert result.page_count == 0
    assert result.warnings


def test_parse_markdown_by_heading(tmp_path: Path) -> None:
    """Markdown 按二级标题切节，标题保留在正文里。"""
    path = tmp_path / "a.md"
    path.write_text(
        "# 总标题\n\n## 第一节\n内容一\n\n## 第二节\n内容二",
        encoding="utf-8",
    )
    result = parse_markdown(path)
    assert result.page_count >= 2
    joined = result.full_text
    assert "第一节" in joined and "第二节" in joined


def test_parse_markdown_without_heading(tmp_path: Path) -> None:
    """没有二级标题时整篇当一节。"""
    path = tmp_path / "b.md"
    path.write_text("只有一段普通文字。", encoding="utf-8")
    result = parse_markdown(path)
    assert result.page_count == 1


# --------------------------------------------------------------------------- #
# 本地切分
# --------------------------------------------------------------------------- #
def _long_text(sentences: int = 60) -> str:
    return "\n\n".join(f"第{i}条：这是用于测试切分的中文句子，长度适中。" for i in range(sentences))


def test_split_recursive_respects_chunk_size() -> None:
    """递归切分后每块不超过设定上限（允许极小超出，因为按句子边界切）。"""
    pages = [(_long_text(), 1)]
    result = split_local(pages, strategy="recursive", chunk_size=200, overlap=0)
    assert result.count > 1
    for chunk in result.chunks:
        assert chunk.token_count <= 260, f"块 {chunk.seq} 超长：{chunk.token_count}"
        assert chunk.content.strip()


def test_split_fixed_basics() -> None:
    """定长切分也受块大小约束。"""
    pages = [(_long_text(), 1)]
    result = split_local(pages, strategy="fixed", chunk_size=128, overlap=16)
    assert result.count > 1
    assert all(chunk.token_count <= 160 for chunk in result.chunks)


def test_split_seq_numbers_sequential() -> None:
    """序号从 0 连续递增，前端依赖这个顺序。"""
    pages = [(_long_text(), 2)]
    result = split_local(pages, strategy="recursive", chunk_size=150, overlap=0)
    assert [chunk.seq for chunk in result.chunks] == list(range(result.count))


def test_split_keeps_page_no() -> None:
    """页码必须带下来，引用溯源全靠它。"""
    pages = [("第一页的内容，足够长以便切分。", 7), ("第二页的内容，同样足够长。", 9)]
    result = split_local(pages, strategy="recursive", chunk_size=512, overlap=0)
    assert {chunk.page_no for chunk in result.chunks} == {7, 9}


def test_split_rejects_bad_params() -> None:
    """非法参数给人话错误，而不是静默出错。"""
    pages = [("正文", 1)]
    with pytest.raises(AppError) as exc:
        split_local(pages, strategy="recursive", chunk_size=512, overlap=512)
    assert exc.value.code == "CHUNK_OVERLAP_INVALID"

    with pytest.raises(AppError) as exc2:
        split_local(pages, strategy="recursive", chunk_size=32, overlap=0)
    assert exc2.value.code == "CHUNK_SIZE_INVALID"


def test_split_unknown_strategy() -> None:
    """未知策略报错。"""
    with pytest.raises(AppError) as exc:
        split_local([("正文", 1)], strategy="semantic")  # type: ignore[arg-type]
    assert exc.value.code == "CHUNK_STRATEGY_INVALID"


def test_merge_tiny_absorbs_short_tail() -> None:
    """极小尾块并入前一块（同页），并重新编号。"""
    from app.services.chunking import ChunkDraft

    chunks = [
        ChunkDraft(seq=0, content="这是一段足够长的正文。" * 10, page_no=1),
        ChunkDraft(seq=1, content="短", page_no=1),
    ]
    assert chunks[1].token_count < MIN_CHUNK_TOKENS
    merged = _merge_tiny(chunks)
    assert len(merged) == 1
    assert merged[0].seq == 0
    assert "短" in merged[0].content


def test_merge_tiny_keeps_different_pages() -> None:
    """跨页的短块不合并，否则页码就错了。"""
    from app.services.chunking import ChunkDraft

    chunks = [
        ChunkDraft(seq=0, content="正文" * 20, page_no=1),
        ChunkDraft(seq=1, content="短", page_no=2),
    ]
    merged = _merge_tiny(chunks)
    assert len(merged) == 2


def test_empty_pages_warn() -> None:
    """没有内容时给出警告。"""
    result = split_local([("   ", 1)], strategy="recursive")
    assert result.count == 0
    assert result.warnings


def test_page_text_counts() -> None:
    """PageText 自动算好字符数与 token 数。"""
    page = PageText(page_no=1, text="测试文本")
    assert page.char_count == 4
    assert page.token_count > 0
