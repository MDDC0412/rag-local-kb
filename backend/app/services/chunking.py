"""分块：把解析后的文本切成适合检索的小段。

三种策略
--------
- fixed     : 定长切（按 token），带 overlap。基线对照用，会把句子切断。
- recursive : 依次按段落 → 换行 → 中文句末标点 → 逗号 → 空格 递归切，
              超长再硬切。**默认策略**，实际效果最好。
- llm       : 把一段文本交给大模型按语义切，输出 JSON 数组。
              慢且花钱，但对结构混乱的文档效果最好；失败自动回退 recursive。

统一约束：极小尾块（< 30 token）并入前一块，避免产生无意义的碎片。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.core.errors import PROVIDER_UNAVAILABLE, AppError
from app.services.tokenizer import count_tokens

logger = logging.getLogger(__name__)

# 中文友好的分隔符优先级
SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", "，", ",", " ", ""]

MIN_CHUNK_TOKENS = 30

DEFAULT_CHUNK_SIZE = 512
DEFAULT_OVERLAP = 64

STRATEGY_LABEL = {
    "fixed": "定长切分",
    "recursive": "递归切分",
    "llm": "智能切分",
}


@dataclass(slots=True)
class ChunkDraft:
    """未入库的分块草稿。"""

    seq: int
    content: str
    token_count: int = 0
    char_count: int = 0
    page_no: int = 0
    section_path: str = ""

    def __post_init__(self) -> None:
        self.token_count = count_tokens(self.content)
        self.char_count = len(self.content)


@dataclass(slots=True)
class ChunkResult:
    """一次切分的产物。"""

    chunks: list[ChunkDraft] = field(default_factory=list)
    strategy: str = "recursive"
    chunk_size: int = DEFAULT_CHUNK_SIZE
    overlap: int = DEFAULT_OVERLAP
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.chunks)

    @property
    def total_tokens(self) -> int:
        return sum(chunk.token_count for chunk in self.chunks)

    @property
    def avg_tokens(self) -> int:
        return int(self.total_tokens / self.count) if self.count else 0


def _merge_tiny(chunks: list[ChunkDraft]) -> list[ChunkDraft]:
    """把过短的尾块并入前一块并重新编号。"""
    if not chunks:
        return []
    merged: list[ChunkDraft] = []
    for chunk in chunks:
        if merged and chunk.token_count < MIN_CHUNK_TOKENS and merged[-1].page_no == chunk.page_no:
            prev = merged[-1]
            prev.content = f"{prev.content}\n{chunk.content}".strip()
            prev.__post_init__()
        else:
            merged.append(chunk)
    for index, chunk in enumerate(merged):
        chunk.seq = index
    return merged


def _build_chunks(
    pieces: list[tuple[str, int]], strategy: str, size: int, overlap: int
) -> ChunkResult:
    """把 (文本, 页码) 片段列表组装成 ChunkDraft 列表。"""
    result = ChunkResult(strategy=strategy, chunk_size=size, overlap=overlap)
    for text, page_no in pieces:
        text = text.strip()
        if not text:
            continue
        result.chunks.append(ChunkDraft(seq=len(result.chunks), content=text, page_no=page_no))
    result.chunks = _merge_tiny(result.chunks)
    return result


def _split_fixed_from_pages(
    pages: list[tuple[str, int]], size: int, overlap: int
) -> list[tuple[str, int]]:
    """按 token 定长切，带 overlap。"""
    from langchain_text_splitters import TokenTextSplitter

    splitter = TokenTextSplitter(
        encoding_name="cl100k_base",
        chunk_size=max(64, size),
        chunk_overlap=max(0, min(overlap, size // 2)),
        disallowed_special=(),
    )
    pieces: list[tuple[str, int]] = []
    for text, page_no in pages:
        for part in splitter.split_text(text):
            if part.strip():
                pieces.append((part, page_no))
    return pieces


def _split_recursive_from_pages(
    pages: list[tuple[str, int]], size: int, overlap: int
) -> list[tuple[str, int]]:
    """递归分隔符切分。"""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        separators=SEPARATORS,
        chunk_size=max(64, size),
        chunk_overlap=max(0, min(overlap, size // 2)),
        keep_separator=True,
        is_separator_regex=False,
        # 长度函数换成 token 计数，保证"块大小"和检索预算是一个单位。
        # 注意：这个类不接受 disallowed_special（那是 TokenTextSplitter 的参数）。
        length_function=count_tokens,
    )
    pieces: list[tuple[str, int]] = []
    for text, page_no in pages:
        for part in splitter.split_text(text):
            if part.strip():
                pieces.append((part, page_no))
    return pieces


def split_local(
    pages: list[tuple[str, int]],
    *,
    strategy: str = "recursive",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> ChunkResult:
    """本地切分（不调用模型）。"""
    if strategy not in ("fixed", "recursive"):
        raise AppError("CHUNK_STRATEGY_INVALID", f"未知的本地切分策略：{strategy}")
    if chunk_size < 64:
        raise AppError("CHUNK_SIZE_INVALID", "块大小至少 64 token，否则切出来没有意义")
    if overlap >= chunk_size:
        raise AppError("CHUNK_OVERLAP_INVALID", "重叠不能大于等于块大小，否则会无限循环")

    if strategy == "fixed":
        pieces = _split_fixed_from_pages(pages, chunk_size, overlap)
    else:
        pieces = _split_recursive_from_pages(pages, chunk_size, overlap)

    result = _build_chunks(pieces, strategy, chunk_size, overlap)
    if result.count == 0:
        result.warnings.append("没有切出任何内容，请确认文档里有正文。")
    return result


# --------------------------------------------------------------------------- #
# LLM 智能切分
# --------------------------------------------------------------------------- #
_LLM_INPUT_CHARS = 3000  # 单次送模型的字符上限


def _parse_llm_json(raw: str) -> list[str]:
    """从模型输出里抠出 JSON 数组，容忍 ```json 包裹与前后废话。"""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("模型输出里找不到 JSON 数组")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("模型返回的不是数组")
    return [str(item).strip() for item in data if str(item).strip()]


async def split_llm(
    pages: list[tuple[str, int]],
    *,
    chat_model,
    prompt_template: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = 0,
) -> ChunkResult:
    """用 LLM 按语义切分；失败或产出异常时回退到递归切分。"""
    result = ChunkResult(strategy="llm", chunk_size=chunk_size, overlap=overlap)
    if not pages:
        result.warnings.append("没有可切分的内容。")
        return result

    pieces: list[tuple[str, int]] = []
    failed_segments = 0

    for text, page_no in pages:
        # 长文本按段切开分别送模型，避免超出上下文
        segments = _split_for_llm(text)
        for segment in segments:
            try:
                prompt = prompt_template.format(text=segment)
                response = await chat_model.ainvoke(prompt)
                content = (
                    response.content if isinstance(response.content, str) else str(response.content)
                )
                blocks = _parse_llm_json(content)
                if not blocks:
                    raise ValueError("模型返回空数组")
                for block in blocks:
                    # 模型可能不守规矩地给出超长块，这里再兜一次
                    if count_tokens(block) > chunk_size * 2:
                        for sub in _split_recursive_from_pages(
                            [(block, page_no)], chunk_size, overlap
                        ):
                            pieces.append(sub)
                    else:
                        pieces.append((block, page_no))
            except Exception as exc:
                failed_segments += 1
                logger.warning("LLM 切分失败（第 %s 段）：%s", failed_segments, exc)
                pieces.extend(
                    _split_recursive_from_pages([(segment, page_no)], chunk_size, overlap)
                )

    if failed_segments:
        result.warnings.append(
            f"有 {failed_segments} 段智能切分失败或输出不合规，这些段落已自动回退为递归切分。"
        )
    result = _build_chunks(pieces, "llm", chunk_size, overlap)
    result.strategy = "llm"
    if result.count == 0:
        raise AppError(PROVIDER_UNAVAILABLE, "智能切分没有产出任何内容，请改用递归切分")
    return result


def _split_for_llm(text: str) -> list[str]:
    """把一段文本按约 3000 字符切开，尽量在段落边界。"""
    text = text.strip()
    if len(text) <= _LLM_INPUT_CHARS:
        return [text] if text else []

    segments: list[str] = []
    buffer = ""
    for paragraph in text.split("\n\n"):
        if not buffer:
            buffer = paragraph
        elif len(buffer) + len(paragraph) + 2 <= _LLM_INPUT_CHARS:
            buffer = f"{buffer}\n\n{paragraph}"
        else:
            segments.append(buffer)
            buffer = paragraph
    if buffer:
        segments.append(buffer)
    return segments
