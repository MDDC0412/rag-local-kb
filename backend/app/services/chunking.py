"""分块：把解析后的文本切成适合检索的小段。

六种策略
--------
| 策略 | 边界依据 | 需要调 API | 适合 |
|---|---|---|---|
| fixed        | 固定 token 数 | 否 | 基线对照 |
| recursive    | 段落 → 换行 → 中文句末 → 逗号 | 否 | **默认**，通用 |
| sentence     | 句号/问号/感叹号断句后聚合 | 否 | 合同、制度（条款必须完整） |
| paragraph    | 空行分段后聚合 | 否 | 报告、笔记 |
| markdown     | # / ## / ### 标题层级 | 否 | 技术文档、说明书 |
| semantic     | 相邻句子向量相似度骤降处断开 | **是** | 结构混乱的文档 |
| llm          | 大模型按语义切并返回 JSON | **是** | 精选文档 |
| parent_child | 小块检索 + 父块回填 | 否 | 长文档（提升上下文完整度） |

统一约束
--------
- 极小尾块（< 30 token）并入前一块，**仅限同页**，否则页码会错；
- 每块都带 page_no，引用溯源全靠它；
- 所有"聚合类"策略都会把过小的片段合并到目标大小，避免产生大量碎片。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.core.errors import AppError
from app.services.tokenizer import count_tokens

logger = logging.getLogger(__name__)

# 中文友好的分隔符优先级（递归切分用）
SEPARATORS = ["\n\n", "\n", "。", "！", "？", "；", ".", "!", "?", ";", "，", ",", " ", ""]

# 句子边界：中文句末标点 + 英文，保留标点本身
_SENTENCE_RE = re.compile(r"[^。！？；!?;\.]+[。！？；!?;\.]?")

MIN_CHUNK_TOKENS = 30

DEFAULT_CHUNK_SIZE = 512
DEFAULT_OVERLAP = 64
DEFAULT_SEMANTIC_THRESHOLD = 0.55  # 相邻句子相似度低于此值就断开

LOCAL_STRATEGIES = ("fixed", "recursive", "sentence", "paragraph", "markdown", "parent_child")
API_STRATEGIES = ("semantic", "llm")

STRATEGY_LABEL = {
    "fixed": "定长切分",
    "recursive": "递归切分",
    "sentence": "句子聚合",
    "paragraph": "段落聚合",
    "markdown": "标题分节",
    "semantic": "语义切分",
    "llm": "智能切分",
    "parent_child": "父子块",
}

STRATEGY_HINT = {
    "fixed": "按固定 token 数硬切，会把句子切断，留作对照实验用。",
    "recursive": "优先在段落和句末标点处断开，通用性最好。",
    "sentence": "先把文本拆成完整句子再聚合，条款不会被拦腰截断。",
    "paragraph": "以空行分段为单位聚合，适合作者本来分好段的文档。",
    "markdown": "按 #/##/### 标题切分，并把标题路径记进每块。",
    "semantic": "逐句算相似度，话题转折处断开。每 200 句约 7 次向量请求。",
    "llm": "由大模型判断语义边界，效果最好但最慢、最贵。",
    "parent_child": "用小块检索提高命中率，命中后把所属父块交给模型，上下文更完整。",
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
    parent_content: str = ""  # 仅 parent_child 策略使用

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


# --------------------------------------------------------------------------- #
# 通用工具
# --------------------------------------------------------------------------- #
def _merge_tiny(chunks: list[ChunkDraft]) -> list[ChunkDraft]:
    """把过短的尾块并入前一块（仅同页），并重新编号。"""
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
    pieces: list[tuple[str, int, str]],
    strategy: str,
    size: int,
    overlap: int,
    parents: dict[int, str] | None = None,
) -> ChunkResult:
    """把 (文本, 页码, 标题路径) 片段列表组装成 ChunkDraft 列表。"""
    result = ChunkResult(strategy=strategy, chunk_size=size, overlap=overlap)
    for text, page_no, section in pieces:
        text = text.strip()
        if not text:
            continue
        result.chunks.append(
            ChunkDraft(
                seq=len(result.chunks),
                content=text,
                page_no=page_no,
                section_path=section,
            )
        )
    result.chunks = _merge_tiny(result.chunks)

    if parents:
        for chunk in result.chunks:
            chunk.parent_content = parents.get(chunk.page_no, "")
    return result


def _split_sentences(text: str) -> list[str]:
    """把文本切成句子，保留标点。"""
    parts = [match.group(0).strip() for match in _SENTENCE_RE.finditer(text)]
    return [part for part in parts if part]


def _aggregate(units: list[str], target_tokens: int, overlap_tokens: int = 0) -> list[str]:
    """把若干小单元（句子/段落）聚合到目标 token 大小。

    overlap_tokens > 0 时，相邻块之间保留末尾若干 token 的内容重叠。
    """
    if not units:
        return []
    target = max(64, target_tokens)
    blocks: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            blocks.append("".join(buffer).strip())
            buffer.clear()

    for unit in units:
        buffer.append(unit)
        if count_tokens("".join(buffer)) >= target:
            flush()

            # 构造重叠：把上一块末尾的一句话塞进下一块开头
            if overlap_tokens > 0 and blocks:
                tail = _tail_sentences(blocks[-1], overlap_tokens)
                if tail:
                    buffer.append(tail)
    flush()
    return [block for block in blocks if block.strip()]


def _tail_sentences(text: str, limit_tokens: int) -> str:
    """取文本末尾不超过 limit_tokens 的完整句子，用于块间重叠。"""
    sentences = _split_sentences(text)
    if not sentences:
        return ""
    picked: list[str] = []
    total = 0
    for sentence in reversed(sentences):
        cost = count_tokens(sentence)
        if total + cost > limit_tokens:
            break
        picked.insert(0, sentence)
        total += cost
    return "".join(picked)


# --------------------------------------------------------------------------- #
# 各策略实现（返回 (文本, 页码, 标题路径) 列表）
# --------------------------------------------------------------------------- #
def _pieces_fixed(
    pages: list[tuple[str, int]], size: int, overlap: int
) -> list[tuple[str, int, str]]:
    """按 token 定长切，带 overlap。"""
    from langchain_text_splitters import TokenTextSplitter

    splitter = TokenTextSplitter(
        encoding_name="cl100k_base",
        chunk_size=max(64, size),
        chunk_overlap=max(0, min(overlap, size // 2)),
        disallowed_special=(),
    )
    out: list[tuple[str, int, str]] = []
    for text, page_no in pages:
        for part in splitter.split_text(text):
            if part.strip():
                out.append((part, page_no, ""))
    return out


def _pieces_recursive(
    pages: list[tuple[str, int]], size: int, overlap: int
) -> list[tuple[str, int, str]]:
    """递归分隔符切分（默认策略）。"""
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(
        separators=SEPARATORS,
        chunk_size=max(64, size),
        chunk_overlap=max(0, min(overlap, size // 2)),
        keep_separator=True,
        is_separator_regex=False,
        # 长度函数换成 token 计数，保证块大小和检索预算是同一个单位。
        # 这个类不接受 disallowed_special（那是 TokenTextSplitter 的参数）。
        length_function=count_tokens,
    )
    out: list[tuple[str, int, str]] = []
    for text, page_no in pages:
        for part in splitter.split_text(text):
            if part.strip():
                out.append((part, page_no, ""))
    return out


def _pieces_sentence(
    pages: list[tuple[str, int]], size: int, overlap: int
) -> list[tuple[str, int, str]]:
    """句子聚合：先断句，再按目标大小聚合。"""
    out: list[tuple[str, int, str]] = []
    for text, page_no in pages:
        sentences = _split_sentences(text)
        # 一句话就超长（无标点的长段落）时退回递归切
        long_sentences: list[str] = []
        for sentence in sentences:
            if count_tokens(sentence) > size * 1.5:
                long_sentences.extend(
                    part for part, _, _ in _pieces_recursive([(sentence, page_no)], size, overlap)
                )
            else:
                long_sentences.append(sentence)
        for block in _aggregate(long_sentences, size, overlap):
            out.append((block, page_no, ""))
    return out


def _pieces_paragraph(
    pages: list[tuple[str, int]], size: int, overlap: int
) -> list[tuple[str, int, str]]:
    """段落聚合：以空行为界，段落过长时递归再切。"""
    out: list[tuple[str, int, str]] = []
    for text, page_no in pages:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        flat: list[str] = []
        for paragraph in paragraphs:
            if count_tokens(paragraph) > size * 1.5:
                flat.extend(
                    part for part, _, _ in _pieces_recursive([(paragraph, page_no)], size, overlap)
                )
            else:
                flat.append(paragraph)
        for block in _aggregate(flat, size, overlap):
            out.append((block, page_no, ""))
    return out


def _pieces_markdown(
    pages: list[tuple[str, int]], size: int, overlap: int
) -> list[tuple[str, int, str]]:
    """按标题层级切分，并记录标题路径。

    没有 Markdown 标题的纯文本会自动退回递归切分。
    """
    from langchain_text_splitters import MarkdownHeaderTextSplitter

    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3"), ("####", "h4")],
        strip_headers=False,  # 标题留在正文里，检索时标题很有信息量
    )
    out: list[tuple[str, int, str]] = []
    for text, page_no in pages:
        try:
            sections = splitter.split_text(text)
        except Exception as exc:
            logger.warning("Markdown 切分失败，退回递归切分：%s", exc)
            out.extend(_pieces_recursive([(text, page_no)], size, overlap))
            continue

        found_heading = any(section.metadata for section in sections)
        if not found_heading:
            out.extend(_pieces_recursive([(text, page_no)], size, overlap))
            continue

        for section in sections:
            path = " > ".join(
                str(section.metadata[key])
                for key in ("h1", "h2", "h3", "h4")
                if section.metadata.get(key)
            )
            body = section.page_content.strip()
            if not body:
                continue
            if count_tokens(body) > size * 1.5:
                for part, _, _ in _pieces_recursive([(body, page_no)], size, overlap):
                    out.append((part, page_no, path))
            else:
                out.append((body, page_no, path))
    return out


def _pieces_parent_child(
    pages: list[tuple[str, int]], size: int, overlap: int
) -> tuple[list[tuple[str, int, str]], dict[int, str]]:
    """父子块：父块 = 整页/整节，子块 = 该页内的小块。

    返回 (子块列表, {page_no: 父块文本})。
    注意：父块按页取，同一页的多段会共用一个父块文本，
    这是刻意的取舍 —— 逐块构造父块会让"父块"退化成"稍微大一点的块"。
    """
    children: list[tuple[str, int, str]] = []
    parents: dict[int, str] = {}
    for text, page_no in pages:
        parents[page_no] = text
        children.extend(_pieces_recursive([(text, page_no)], size, overlap))
    return children, parents


# --------------------------------------------------------------------------- #
# 本地切分入口
# --------------------------------------------------------------------------- #
def split_local(
    pages: list[tuple[str, int]],
    *,
    strategy: str = "recursive",
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> ChunkResult:
    """本地切分（不调用模型）。"""
    if strategy not in LOCAL_STRATEGIES:
        raise AppError(
            "CHUNK_STRATEGY_INVALID",
            f"未知的本地切分策略：{strategy}（可选：{'、'.join(LOCAL_STRATEGIES)}）",
        )
    if chunk_size < 64:
        raise AppError("CHUNK_SIZE_INVALID", "块大小至少 64 token，否则切出来没有意义")
    if overlap >= chunk_size:
        raise AppError("CHUNK_OVERLAP_INVALID", "重叠不能大于等于块大小，否则会无限循环")

    parents: dict[int, str] | None = None
    if strategy == "fixed":
        pieces = _pieces_fixed(pages, chunk_size, overlap)
    elif strategy == "sentence":
        pieces = _pieces_sentence(pages, chunk_size, overlap)
    elif strategy == "paragraph":
        pieces = _pieces_paragraph(pages, chunk_size, overlap)
    elif strategy == "markdown":
        pieces = _pieces_markdown(pages, chunk_size, overlap)
    elif strategy == "parent_child":
        pieces, parents = _pieces_parent_child(pages, chunk_size, overlap)
    else:
        pieces = _pieces_recursive(pages, chunk_size, overlap)

    result = _build_chunks(pieces, strategy, chunk_size, overlap, parents=parents)
    if result.count == 0:
        result.warnings.append("没有切出任何内容，请确认文档里有正文。")
    return result


# --------------------------------------------------------------------------- #
# 语义切分（需要 embedding）
# --------------------------------------------------------------------------- #
async def split_semantic(
    pages: list[tuple[str, int]],
    *,
    embed_fn,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = 0,
    threshold: float = DEFAULT_SEMANTIC_THRESHOLD,
    batch_size: int = 32,
) -> ChunkResult:
    """语义切分：相邻句子的向量相似度低于阈值处断开。

    为什么值得做：递归切分只看字符，会把"讲完 A 话题、紧接着讲 B 话题"的
    两段合成一块，检索时这块对两个话题都"半相关"，得分都不高。
    """
    result = ChunkResult(strategy="semantic", chunk_size=chunk_size, overlap=overlap)
    if not pages:
        result.warnings.append("没有可切分的内容。")
        return result

    pieces: list[tuple[str, int, str]] = []
    cut_count = 0
    for text, page_no in pages:
        sentences = _split_sentences(text)
        if len(sentences) <= 1:
            pieces.extend(_pieces_recursive([(text, page_no)], chunk_size, overlap))
            continue

        # 批量向量化（按批，避免一次请求过大）
        vectors: list[list[float]] = []
        for start in range(0, len(sentences), batch_size):
            batch = sentences[start : start + batch_size]
            vectors.extend(await embed_fn(batch))

        # 相邻相似度
        blocks: list[str] = []
        buffer: list[str] = [sentences[0]]
        for index in range(1, len(sentences)):
            similarity = _cosine(vectors[index - 1], vectors[index])
            over_size = count_tokens("".join(buffer)) >= chunk_size
            if similarity < threshold or over_size:
                blocks.append("".join(buffer).strip())
                buffer = [sentences[index]]
                if similarity < threshold:
                    cut_count += 1
            else:
                buffer.append(sentences[index])
        if buffer:
            blocks.append("".join(buffer).strip())

        for block in blocks:
            if not block:
                continue
            if count_tokens(block) > chunk_size * 2:
                for part, _, _ in _pieces_recursive([(block, page_no)], chunk_size, overlap):
                    pieces.append((part, page_no, ""))
            else:
                pieces.append((block, page_no, ""))

    result = _build_chunks(pieces, "semantic", chunk_size, overlap)
    result.strategy = "semantic"
    if cut_count:
        result.warnings.append(f"按语义边界断开了 {cut_count} 处。")
    if result.count == 0:
        result.warnings.append("语义切分没有产出内容，建议改用递归切分。")
    return result


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；维度不一致或空向量返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


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

    pieces: list[tuple[str, int, str]] = []
    failed_segments = 0

    for text, page_no in pages:
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
                        for sub, _, _ in _pieces_recursive([(block, page_no)], chunk_size, overlap):
                            pieces.append((sub, page_no, ""))
                    else:
                        pieces.append((block, page_no, ""))
            except Exception as exc:
                failed_segments += 1
                logger.warning("LLM 切分失败（第 %s 段）：%s", failed_segments, exc)
                pieces.extend(_pieces_recursive([(segment, page_no)], chunk_size, overlap))

    if failed_segments:
        result.warnings.append(
            f"有 {failed_segments} 段智能切分失败或输出不合规，这些段落已自动回退为递归切分。"
        )
    result = _build_chunks(pieces, "llm", chunk_size, overlap)
    result.strategy = "llm"
    if result.count == 0:
        raise AppError("LLM_CHUNK_EMPTY", "智能切分没有产出任何内容，请改用递归切分")
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
