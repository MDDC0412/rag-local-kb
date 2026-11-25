"""文档解析：把 pdf / docx / md / txt 变成「按页或按节的纯文本」。

设计要点
--------
1. 解析结果按「页」（pdf）或「节」（md/docx 的段落分块）落库，供后续重切时复用，
   避免每次调参都重新解析（大 PDF 解析要几百毫秒，LLM 解析要花钱）。
2. 扫描件检测：一页几乎没抽出文字就记为可能为扫描件，最后按比例给出提示，
   而不是让用户对着空白向量库发呆。
3. 不引入 unstructured / docx2txt：前者依赖巨大且装不稳，后者丢表格。
   docx 用 python-docx 手写 40 行，保留表格为 Markdown。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.errors import UNSUPPORTED_FILE_TYPE, AppError
from app.services.tokenizer import count_tokens

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown", ".txt"}
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

# 一页少于这个字符数就当成"没抽到正文"
_MIN_PAGE_CHARS = 12


@dataclass(slots=True)
class PageText:
    """一页/一节的文本。"""

    page_no: int
    text: str
    char_count: int = 0
    token_count: int = 0

    def __post_init__(self) -> None:
        self.char_count = len(self.text)
        self.token_count = count_tokens(self.text)


@dataclass(slots=True)
class ParseResult:
    """一次解析的产物。"""

    pages: list[PageText] = field(default_factory=list)
    mode: str = "local"
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def char_count(self) -> int:
        return sum(page.char_count for page in self.pages)

    @property
    def full_text(self) -> str:
        return "\n\n".join(page.text for page in self.pages if page.text.strip())


def detect_mime(path: Path) -> str:
    """按扩展名给出 MIME，够用且不引入 mimetypes 的平台差异。"""
    suffix = path.suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".txt": "text/plain",
    }.get(suffix, "application/octet-stream")


def ensure_supported(filename: str) -> str:
    """校验扩展名，返回小写后缀；不支持则抛人话错误。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        allowed = "、".join(sorted(ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS))
        raise AppError(
            UNSUPPORTED_FILE_TYPE,
            f"不支持「{suffix or '无扩展名'}」格式，目前支持：{allowed}",
            status_code=400,
        )
    return suffix


# --------------------------------------------------------------------------- #
# 各格式解析
# --------------------------------------------------------------------------- #
def _clean_page_text(text: str) -> str:
    """去掉页眉页脚产生的孤立页码、连续空行、行尾空白。"""
    if not text:
        return ""
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned: list[str] = []
    for line in lines:
        # 纯页码行（如 "12"、"- 3 -"、"第 5 页"）丢弃
        if re.fullmatch(r"[\s\-—–]*\d{1,4}[\s\-—–]*", line):
            continue
        if re.fullmatch(r"\s*第\s*\d{1,4}\s*页\s*", line):
            continue
        cleaned.append(line)
    # 连续 3 个以上空行压成 1 个
    joined = "\n".join(cleaned)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


def parse_pdf(path: Path) -> ParseResult:
    """按页解析 PDF，并检测疑似扫描件。"""
    import pymupdf

    result = ParseResult(mode="local")
    empty_pages = 0
    total_pages = 0
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise AppError(
                "PDF_ENCRYPTED", "这个 PDF 有密码保护，请先解除密码再上传", status_code=400
            )
        # page_count 必须在 with 块内取出：文件关闭后再访问会抛 "document closed"
        total_pages = doc.page_count
        for index in range(total_pages):
            page = doc.load_page(index)
            text = _clean_page_text(page.get_text("text"))
            if len(text) < _MIN_PAGE_CHARS:
                empty_pages += 1
            result.pages.append(PageText(page_no=index + 1, text=text))

    if empty_pages and empty_pages == total_pages:
        result.warnings.append(
            "整份 PDF 都没能提取到文字，可能是扫描件或图片版。"
            "本项目不做 OCR，建议先用其他工具转成文字版。"
        )
    elif empty_pages > total_pages * 0.5:
        result.warnings.append(
            f"有 {empty_pages}/{total_pages} 页没有提取到文字，疑似扫描页，这些页不会进入检索。"
        )
    return result


def _docx_block_to_text(block) -> str:
    """把 docx 的一个块转成文本；表格转 Markdown。"""
    from docx.table import Table

    if isinstance(block, Table):
        rows: list[str] = []
        for row in block.rows:
            cells = [cell.text.strip().replace("|", "/").replace("\n", " ") for cell in row.cells]
            rows.append("| " + " | ".join(cells) + " |")
        if len(rows) >= 1:
            header_sep = "| " + " | ".join(["---"] * len(block.rows[0].cells)) + " |"
            rows.insert(1, header_sep)
        return "\n".join(rows)
    text = getattr(block, "text", "") or ""
    return text.strip()


def parse_docx(path: Path) -> ParseResult:
    """按「每 1 个标题层级切换」为界分段解析 docx，保留表格。"""
    from docx import Document as DocxDocument
    from docx.table import Table

    doc = DocxDocument(str(path))
    result = ParseResult(mode="local")

    # 段落与表格按文档顺序遍历
    body = doc.element.body
    blocks: list[object] = []
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            from docx.text.paragraph import Paragraph

            blocks.append(Paragraph(child, doc))
        elif tag == "tbl":
            blocks.append(Table(child, doc))

    # 以标题（Heading 样式）为界切成节；无标题时退化为按长度切
    sections: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        joined = _clean_page_text("\n\n".join(part for part in buffer if part))
        if joined:
            sections.append(joined)
        buffer.clear()

    for block in blocks:
        text = _docx_block_to_text(block)
        if not text:
            continue
        style = getattr(block, "style", None)
        style_name = getattr(style, "name", "") if style is not None else ""
        if style_name.startswith("Heading") and buffer:
            flush()
        buffer.append(text)
    flush()

    if not sections:
        result.warnings.append("这个 Word 文档里没有可提取的文字（可能全是图片）。")
        return result

    for index, section in enumerate(sections, start=1):
        result.pages.append(PageText(page_no=index, text=section))
    return result


def parse_markdown(path: Path) -> ParseResult:
    """按二级标题切节，保留标题本身（检索时标题很有信息量）。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    result = ParseResult(mode="local")
    if not raw.strip():
        result.warnings.append("文件是空的。")
        return result

    # 按 ## 切分；没有 ## 就整篇当一节
    parts = re.split(r"(?m)^(?=##\s)", raw)
    sections = [part.strip() for part in parts if part.strip()]
    if len(sections) <= 1:
        sections = [raw.strip()]

    for index, section in enumerate(sections, start=1):
        result.pages.append(PageText(page_no=index, text=_clean_page_text(section)))
    return result


def parse_text(path: Path) -> ParseResult:
    """纯文本：按双换行分段，每段一页。"""
    raw = path.read_text(encoding="utf-8", errors="replace")
    result = ParseResult(mode="local")
    if not raw.strip():
        result.warnings.append("文件是空的。")
        return result
    sections = [part.strip() for part in re.split(r"\n\s*\n", raw) if part.strip()]
    for index, section in enumerate(sections, start=1):
        result.pages.append(PageText(page_no=index, text=_clean_page_text(section)))
    return result


_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".txt": parse_text,
}


def parse_document(path: Path, filename: str | None = None) -> ParseResult:
    """按扩展名分派解析。"""
    suffix = ensure_supported(filename or path.name)
    parser = _PARSERS[suffix]
    result = parser(path)
    logger.info(
        "解析完成 %s：%s 段，%s 字", filename or path.name, result.page_count, result.char_count
    )
    return result
