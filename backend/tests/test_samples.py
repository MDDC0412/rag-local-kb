"""示例数据的测试：保证 `samples/` 里的演示文件真的能被解析。

为什么值得单独测：演示数据是**给用户第一次试用**的入口。
如果它解析不出来，用户第一印象就是这个系统坏了，
而问题其实只是「生成脚本改坏了」或「字体没嵌进去」。

这里只验证「能抽出文字、页数对、关键词在」，
不断言具体措辞 —— 文案可以改。
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "samples"

pytestmark = pytest.mark.skipif(
    not (SAMPLES / "tech_handbook_sample.pdf").exists(),
    reason="示例 PDF 不存在（跑 scripts/make_sample_pdf.py 生成）",
)


@pytest.fixture(scope="module")
def pdf_path() -> Path:
    """示例 PDF 的路径。"""
    return SAMPLES / "tech_handbook_sample.pdf"


@pytest.fixture(scope="module")
def parsed(pdf_path: Path):
    """解析一次，多个测试共用（解析有成本）。"""
    from app.services.parsing import parse_pdf

    return parse_pdf(pdf_path)


def test_sample_pdf_has_three_pages(parsed) -> None:
    """演示 PDF 应该是 3 页。"""
    assert parsed.page_count == 3


def test_sample_pdf_extracts_chinese_text(parsed) -> None:
    """每页都要抽出中文，不能是空白（字体没嵌进去就会这样）。"""
    for page in parsed.pages:
        assert page.text.strip(), f"第 {page.page_no} 页没抽出文字"
        assert any("\u4e00" <= char <= "\u9fff" for char in page.text), (
            f"第 {page.page_no} 页没有中文字符，字体可能没嵌入"
        )


def test_sample_pdf_not_detected_as_scanned(parsed) -> None:
    """不该被当成扫描件 —— 它是可复制文字的。"""
    assert not any("扫描" in warning for warning in parsed.warnings), (
        f"演示 PDF 被误判为扫描件：{parsed.warnings}"
    )


def test_sample_pdf_text_is_meaningful(parsed) -> None:
    """内容应该是有结构的规范文档，而不是乱码。"""
    full = "\n".join(page.text for page in parsed.pages)

    # 这些是文档里的结构性关键词，抽出乱码时基本不可能同时命中
    for keyword in ("接口", "错误", "重试", "鉴权"):
        assert keyword in full, f"示例 PDF 里找不到「{keyword}」，可能是乱码"


def test_sample_pdf_file_size_is_reasonable(pdf_path: Path) -> None:
    """字体必须子集化，否则整套中文字体会让文件涨到十几 MB。"""
    size_kb = pdf_path.stat().st_size / 1024

    assert size_kb < 1024, (
        f"示例 PDF 有 {size_kb:.0f}KB，可能忘了 doc.subset_fonts()，整个字体都被嵌进去了"
    )


def test_markdown_sample_exists_and_parses() -> None:
    """另一份演示数据（md）也要能被解析。"""
    from app.services.parsing import parse_markdown

    md = SAMPLES / "policy_sample.md"
    assert md.exists(), "policy_sample.md 不见了"

    result = parse_markdown(md)
    assert result.page_count >= 1
    assert "差旅" in "\n".join(page.text for page in result.pages)
