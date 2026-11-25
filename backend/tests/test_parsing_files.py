"""真实文件解析的集成测试：会真的用 pymupdf / python-docx 生成并解析文件。

这类测试不触网，属于纯本地逻辑，必须覆盖 —— 之前正是缺了这段，
才让"在 with 块外访问已关闭 PDF 的 page_count"这种错误溜过单元测试。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.parsing import parse_document, parse_docx, parse_pdf


def _make_pdf(path: Path, pages: list[tuple[str, str]]) -> None:
    """用 pymupdf 生成一份中文多页 PDF。"""
    import pymupdf

    doc = pymupdf.open()
    for title, body in pages:
        page = doc.new_page()
        page.insert_text((72, 80), title, fontsize=18, fontname="china-s")
        y = 130
        for line in body.split("\n"):
            page.insert_text((72, y), line, fontsize=11, fontname="china-s")
            y += 24
    doc.save(path)
    doc.close()


def _make_docx(path: Path, paragraphs: list[tuple[str, str]]) -> None:
    """生成一份带标题样式的 Word 文档。"""
    from docx import Document as DocxDocument

    doc = DocxDocument()
    for style, text in paragraphs:
        doc.add_paragraph(text, style=style)
    doc.save(path)


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def test_parse_pdf_real_file(tmp_path: Path) -> None:
    """真实 PDF 能按页解析出全部页面，且页码从 1 开始连续。"""
    path = tmp_path / "sample.pdf"
    _make_pdf(
        path,
        [
            ("差旅报销管理办法", "第 1 条 适用范围\n适用于全体员工。"),
            ("报销材料要求", "第 3 条 必需材料\n发票原件、出差审批单。"),
            ("审批与时限", "第 5 条 审批权限\n2000 元以下部门经理审批。"),
        ],
    )
    result = parse_pdf(path)
    assert result.page_count == 3
    assert [page.page_no for page in result.pages] == [1, 2, 3]
    assert "差旅报销" in result.full_text
    assert result.char_count > 0
    assert not result.warnings, f"不应有警告：{result.warnings}"


def test_parse_pdf_reports_page_count_after_close(tmp_path: Path) -> None:
    """回归：文档关闭后不能再访问 page_count（曾经因此抛 document closed）。"""
    path = tmp_path / "two.pdf"
    _make_pdf(path, [("标题一", "正文一"), ("标题二", "正文二")])
    result = parse_pdf(path)  # 内部在 with 块结束后仍要能给出正确页数
    assert result.page_count == 2


def test_parse_pdf_scanned_warning(tmp_path: Path) -> None:
    """整份都是空白页时给出「疑似扫描件」警告。"""
    path = tmp_path / "blank.pdf"
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    doc.new_page()
    doc.save(path)
    doc.close()

    result = parse_pdf(path)
    assert result.page_count == 2
    assert result.char_count == 0
    assert any("扫描件" in warning for warning in result.warnings)


def test_parse_document_dispatches_pdf(tmp_path: Path) -> None:
    """parse_document 按扩展名分派到 pdf 解析器。"""
    path = tmp_path / "d.pdf"
    _make_pdf(path, [("标题", "正文内容足够长以便通过最小字符数校验。")])
    result = parse_document(path, "d.pdf")
    assert result.page_count == 1
    assert result.mode == "local"


# --------------------------------------------------------------------------- #
# DOCX
# --------------------------------------------------------------------------- #
def test_parse_docx_splits_by_heading(tmp_path: Path) -> None:
    """Word 以 Heading 为界分节，正文都保留。"""
    path = tmp_path / "a.docx"
    _make_docx(
        path,
        [
            ("Heading 1", "第一章 总则"),
            ("Normal", "本办法适用于全体员工。"),
            ("Heading 1", "第二章 报销"),
            ("Normal", "需提交发票与行程单。"),
        ],
    )
    result = parse_docx(path)
    assert result.page_count >= 2
    joined = result.full_text
    assert "第一章" in joined
    assert "发票" in joined


def test_parse_docx_with_table(tmp_path: Path) -> None:
    """表格转成 Markdown，内容不丢。"""

    from docx import Document as DocxDocument

    path = tmp_path / "t.docx"
    doc = DocxDocument()
    doc.add_paragraph("住宿标准", style="Heading 1")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "城市级别"
    table.cell(0, 1).text = "上限"
    table.cell(1, 0).text = "一线"
    table.cell(1, 1).text = "600 元"
    doc.save(path)

    result = parse_docx(path)
    text = result.full_text
    assert "| 城市级别 | 上限 |" in text
    assert "| 一线 | 600 元 |" in text


def test_parse_docx_empty(tmp_path: Path) -> None:
    """空 Word 文档给出警告而不是崩溃。"""
    from docx import Document as DocxDocument

    path = tmp_path / "empty.docx"
    DocxDocument().save(path)
    result = parse_docx(path)
    assert result.page_count == 0
    assert result.warnings


# --------------------------------------------------------------------------- #
# 不支持 / 损坏
# --------------------------------------------------------------------------- #
def test_parse_document_rejects_unsupported(tmp_path: Path) -> None:
    """不支持的扩展名抛人话错误。"""
    from app.core.errors import AppError

    path = tmp_path / "x.zip"
    path.write_bytes(b"PK\x03\x04")
    with pytest.raises(AppError) as exc:
        parse_document(path, "x.zip")
    assert exc.value.code == "UNSUPPORTED_FILE_TYPE"


def test_parse_document_corrupted_pdf(tmp_path: Path) -> None:
    """损坏的 PDF 不应抛未捕获异常。"""
    path = tmp_path / "bad.pdf"
    path.write_bytes(b"not a real pdf at all")

    with pytest.raises(Exception):  # noqa: B017 - 上层会包成 AppError
        parse_pdf(path)