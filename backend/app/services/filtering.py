"""文本过滤：分块**之前**的清洗，决定"哪些文字值得留下"。

为什么不叫"切分"
----------------
切分回答的是"留下的文字怎么切"，过滤回答的是"哪些文字该留"。
这两件事经常被混在一起，于是有人用大模型去"顺便切一下"——
那就要对全文逐段调用模型，75k 字的文档要 30 次串行请求、十几分钟。
过滤只做**判定**，而且尽量不调模型；非调不可时也只调**一次**。

两级过滤
--------
| 级别 | 依据 | 调模型 | 效果 |
|---|---|---|---|
| 原文过滤 | 规则：位置 + 形态 + 重复 | 否 | 页眉页脚、纯页码、孤儿行、乱码、空行 |
| 智能过滤 | 全文概览交给模型判断一次 | **是（1 次）** | 目录、版权声明、免责声明、广告、无关附录 |

保守原则（很重要）
------------------
**宁可漏删，不可错删。** 知识库里丢一段正文，后果远大于多留一段噪声：
- 删除只允许在**页首/页尾**位置发生（页眉页脚就在那里）；
- 短行规则要求"两侧都有空行"才算孤儿行，贴着正文的短行一律保留；
- 智能过滤会同时告诉模型"拿不准就保留"，并且**只按行号删整行**，
  不允许模型改写内容 —— 模型一旦能改写，就是在替你改法律条文了。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 页首/页尾各看几行：页眉页脚通常就在这几行里，再往里找就该怀疑是正文了
HEAD_LINES = 3
TAIL_LINES = 2

# 一行里可见字符（非空白）少于这个数，才有资格被当成"孤儿碎片行"
# 定成 3 而不是 4：中文两个字太常见（"附"、"注"），但四字短语也常见，
# 定太低会把"综上所述"这种正文删掉。宁可只删 1-2 字的真碎片。
MIN_VISIBLE_CHARS = 3

# 页眉页脚的最大长度：超过这个长度基本是一句正文，不是页眉
MAX_HEADER_CHARS = 40

# 句末标点：以这些字符结尾说明是一句话，不是页眉
_SENTENCE_END = "。！？；!?;."

# 疑似乱码：替换符、控制字符，或"非 ASCII 符号占比过高"
_REPLACEMENT_CHARS = re.compile(r"[\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]")
_SYMBOL_RUN = re.compile(r"[^\w\u4e00-\u9fff\s，。；：！？、（）《》「」“”‘’—…·%\-./:+,()\[\]]")
# 纯页码/断页符：可能是数字，也可能是罗马数字、连字符包裹的数字
_PAGE_NUMBER = re.compile(r"^[\s\-—–·•]*(\d{1,4}|[ivxlcdm]{1,7})[\s\-—–·•]*$", re.IGNORECASE)
_HAS_WORD_CHAR = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")

# 删除原因，前端直接显示给用户，避免"它为什么删了我这条"
REASON_HEADER = "页眉/页脚"
REASON_PAGE_NUMBER = "页码"
REASON_ORPHAN = "孤儿碎片行"
REASON_GARBLE = "乱码/控制字符"
REASON_DUP_LINE = "同页重复行"
REASON_AI = "智能判断无检索价值"


@dataclass(slots=True)
class Removal:
    """一行被删掉的记录，带原因与位置，供前端展示与排查。"""

    page_no: int
    line_no: int
    text: str
    reason: str


@dataclass(slots=True)
class FilterResult:
    """过滤产物：清理后的各页文本 + 删了什么。"""

    pages: list[tuple[int, str]] = field(default_factory=list)
    removals: list[Removal] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: str = "raw"

    @property
    def removed_count(self) -> int:
        return len(self.removals)

    @property
    def kept_chars(self) -> int:
        return sum(len(text) for _, text in self.pages)


def _visible_len(text: str) -> int:
    """可见字符数（不含空白）。"""
    return len(text.replace(" ", "").replace("\t", ""))


def is_page_number(line: str) -> bool:
    """纯页码行的**形态**判定（不判断位置）。

    ⚠️ 单独用这个函数会误删正文：`600` 是住宿标准里的金额，`2023` 是年份。
    真正删除时必须用 ``_is_page_number_at_edge`` —— 只有页首/页尾的裸数字才算页码。
    """
    stripped = line.strip()
    if not stripped:
        return False
    # 带"第…页"标记的，位置无关，一定删
    if re.fullmatch(r"\s*第\s*\d{1,4}\s*页\s*", stripped):
        return True
    match = _PAGE_NUMBER.match(stripped)
    if not match:
        return False
    return len(match.group(1)) <= 3  # 4 位数字（如 2023）更像年份，不算页码


# 带连字符/破折号装饰的页码：`- 12 -`、`— 8 —`，形态明确，位置无关
_DASH_DECORATED_PAGE = re.compile(r"^[\s]*[-—–][\s]*\d{1,4}[\s]*[-—–][\s]*$")


def _is_page_number_at_edge(line: str, line_no: int, total_lines: int) -> bool:
    """页码判定：只有**页首/页尾**位置的裸数字才算页码。

    为什么必须看位置：`600` 是「每晚上限 600 元」里的金额，`12` 可能是条款序号，
    它们出现在正文中间时删掉就是丢数据。真页码一定在页的边缘。

    "边缘"取 `HEAD_LINES` 行，但**不超过全页行数的 1/5** ——
    8 行的短页如果按前 3 行算，第 3 行已经在正文中间了。
    """
    stripped = line.strip()
    # "- 12 -" 这种带连字符装饰的，形态本身就够明确，位置要求放宽
    if _DASH_DECORATED_PAGE.match(stripped):
        return True
    # "第 5 页" 有明确标记，也是位置无关
    if re.fullmatch(r"\s*第\s*\d{1,4}\s*页\s*", stripped):
        return True
    if not is_page_number(stripped):
        return False
    # 裸数字：按页高动态收紧"边缘"范围，最多占全页 1/5
    edge = max(1, min(HEAD_LINES, total_lines // 5))
    return line_no <= edge or line_no > total_lines - edge


def is_garble(line: str) -> bool:
    """疑似乱码：含替换符/控制字符，或非文字符号占比过高。"""
    if not line.strip():
        return False
    if _REPLACEMENT_CHARS.search(line):
        return True
    # 一行里连一个数字或汉字都没有，基本不可能是正文
    if not _HAS_WORD_CHAR.search(line):
        return True
    visible = _visible_len(line)
    if visible >= 8:
        symbols = len(_SYMBOL_RUN.findall(line))
        if symbols / max(1, visible) > 0.5:
            return True
    return False


def is_orphan_fragment(line: str, has_blank_before: bool, has_blank_after: bool) -> bool:
    """孤儿碎片行：极短，且前后都被空行包住。

    为什么要空行条件：法律条文里的"（一）管制；"这种短行是正文的一部分，
    它前后紧贴着别的行，不能被当碎片删掉。
    """
    if _visible_len(line) >= MIN_VISIBLE_CHARS:
        return False
    return has_blank_before and has_blank_after


def _looks_like_header(line: str) -> bool:
    """页眉页脚的**形态**特征，用来给"位置重复"再加一道闸。

    真页眉页脚通常：短、不含句末标点、不像一句完整的话。
    而正文句子通常以句号结尾、而且较长。

    为什么需要这道闸：如果一份文档每页的第一行恰好都是同一句正文
    （例如模板化生成的报告），只靠"位置重复"会把正文全删了。
    """
    stripped = line.strip()
    if not stripped:
        return False
    if len(stripped) > MAX_HEADER_CHARS:
        return False
    # 以句末标点结尾 → 是一句话，不是页眉
    return stripped[-1] not in _SENTENCE_END


def _page_header_keys(pages: list[tuple[int, str]]) -> dict[int, set[str]]:
    """找出"在页首/页尾位置**跨页重复**"的行，作为页眉页脚候选。

    四道防误删的闸，缺一不可（都是实测踩出来的）：

    1. **只看位置**：只在页首 HEAD_LINES 行、页尾 TAIL_LINES 行里找。
       刑法文档里"单位犯前款罪的……"在正文中重复 29 次，那不是页眉。
    2. **要求跨页**：至少出现在 2 个不同页的同一端位置。
    3. **页内容必须够多**：一页只有两三行时，"页首行"其实就是整页，
       拿它做重复判定会把正文当页眉删掉。
    4. **形态要像页眉**：短、且不以句末标点结尾（见 ``_looks_like_header``）。
    """
    head_seen: dict[str, list[int]] = {}
    tail_seen: dict[str, list[int]] = {}

    for page_no, text in pages:
        stripped = [
            (index, line.strip()) for index, line in enumerate(text.splitlines()) if line.strip()
        ]
        # 页内行数太少时，"页首/页尾"覆盖了整页，判定没有意义
        if len(stripped) <= HEAD_LINES + TAIL_LINES + 1:
            continue
        for _, line in stripped[:HEAD_LINES]:
            if _looks_like_header(line):
                head_seen.setdefault(line, []).append(page_no)
        for _, line in stripped[-TAIL_LINES:]:
            if _looks_like_header(line):
                tail_seen.setdefault(line, []).append(page_no)

    keys: dict[int, set[str]] = {page_no: set() for page_no, _ in pages}
    for collection in (head_seen, tail_seen):
        for line, page_nos in collection.items():
            if len(set(page_nos)) >= 2:
                for page_no in set(page_nos):
                    keys.setdefault(page_no, set()).add(line)
    return keys


def filter_raw(pages: list[tuple[int, str]]) -> FilterResult:
    """原文过滤：纯规则，不调任何接口。

    做四件事（都在**行**粒度，不动行内文字）：
    1. 删页首/页尾位置跨页重复的行 —— 页眉、页脚、水印；
    2. 删纯页码行；
    3. 删孤儿碎片行（极短且前后有空行）；
    4. 删乱码与控制字符行；多余的连续空行压成一个。

    行内文字一个字都不改：改字会悄悄变成"篡改原文"，
    而 RAG 的价值恰恰在于"引用的就是原文"。
    """
    result = FilterResult(mode="raw")
    headers = _page_header_keys(pages)

    for page_no, text in pages:
        raw_lines = text.splitlines()
        total_lines = len(raw_lines)
        kept: list[str] = []

        for line_no, line in enumerate(raw_lines, start=1):
            stripped = line.strip()

            if not stripped:
                if kept and kept[-1].strip():
                    kept.append("")
                continue

            reason = ""
            if stripped in headers.get(page_no, set()):
                reason = REASON_HEADER
            elif _is_page_number_at_edge(stripped, line_no, total_lines):
                reason = REASON_PAGE_NUMBER
            elif is_garble(stripped):
                reason = REASON_GARBLE
            else:
                before = line_no == 1 or not raw_lines[line_no - 2].strip()
                after = line_no == total_lines or not raw_lines[line_no].strip()
                if is_orphan_fragment(stripped, before, after):
                    reason = REASON_ORPHAN

            if reason:
                result.removals.append(
                    Removal(page_no=page_no, line_no=line_no, text=stripped[:120], reason=reason)
                )
                continue
            kept.append(stripped)

        # 收尾空行去掉，再压连续空行
        while kept and not kept[-1].strip():
            kept.pop()
        cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
        if cleaned.strip():
            result.pages.append((page_no, cleaned))

    kept_pages = [(page_no, text) for page_no, text in result.pages if text.strip()]
    if not kept_pages:
        result.warnings.append("原文过滤后没有一个字留下，请检查文档是否为空或全是页眉页脚。")
        return result

    result.pages = kept_pages
    if result.removed_count:
        result.warnings.append(
            f"原文过滤删掉了 {result.removed_count} 行（页眉页脚、页码、碎片行）。"
        )
    return result


# --------------------------------------------------------------------------- #
# 智能过滤：调一次模型，只让它回答"删哪几行"
# --------------------------------------------------------------------------- #
# 送模型的内容上限：只送**概览**，不送全文。超出就按页截断并提示。
AI_PREVIEW_CHARS = 12000
# 每行最多展示这么多字符，避免长条文把预算吃光
AI_LINE_CHARS = 100

AI_FILTER_SYSTEM = (
    "你是一个文档清洗助手。用户会给你一份文档的编号行列表（格式为 `页号:行号|内容`）。\n"
    "请找出**对知识库检索完全没有价值**的行，只输出这些行的编号。\n\n"
    "应当删除：目录页的条目、版权与免责声明、广告、封面页的出版信息、\n"
    "页眉页脚重复的水印文字、与正文无关的致谢或勘误。\n\n"
    "必须保留：所有正文、标题、条款、定义、数据、表格内容、注释。\n"
    "**拿不准的一律保留。** 删除正文的代价远大于多留一行噪声。\n\n"
    '只输出 JSON，形如 {"drop": [{"page": 1, "line": 3}, ...]}，没有要删的就输出 {"drop": []}。'
)


def ai_prompt_fallback() -> str:
    """提示词库里取不到模板时的兜底（与 prompts/defaults.py 的 filter 模板一致）。

    保留一份常量而不是直接依赖模板库，是为了让这两个模块能各自独立测试。
    """
    return """下面是一份文档的编号行列表，格式为 `页号:行号|内容`。

请找出**对知识库检索完全没有价值**的行，只输出这些行的编号。

应当删除：
- 目录页的条目、章节索引
- 版权声明、免责声明、出版信息
- 广告、推广、二维码提示
- 封面页的作者/出版机构/ISBN 等出版信息
- 与正文无关的致谢、勘误、订阅说明

必须保留：
- 全部正文、标题、条款、定义、数据、表格内容、注释与脚注
- 即使看起来重复，只要是条文或正文就保留

**拿不准的一律保留。** 删掉正文的代价远大于多留一行噪声。

只输出 JSON：{{"drop": [{{"page": 1, "line": 3}}, {{"page": 2, "line": 1}}]}}
没有要删的就输出：{{"drop": []}}
不要输出任何其它字符。

文档内容：
{text}"""


def build_preview(pages: list[tuple[int, str]]) -> tuple[str, dict[tuple[int, int], str], bool]:
    """把各页文本压成"页号:行号|内容"的概览，供模型判断。

    返回 (概览文本, {(页,行): 原文}, 是否被截断)。
    行号是**原文行号**（与 filter_raw 的 line_no 一致），这样模型给的编号能直接对上。
    """
    index: dict[tuple[int, int], str] = {}
    lines_out: list[str] = []
    used = 0
    truncated = False

    for page_no, text in pages:
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            entry = f"{page_no}:{line_no}|{stripped[:AI_LINE_CHARS]}"
            if used + len(entry) > AI_PREVIEW_CHARS:
                truncated = True
                break
            lines_out.append(entry)
            index[(page_no, line_no)] = stripped
            used += len(entry) + 1
        if truncated:
            break

    return "\n".join(lines_out), index, truncated


def parse_ai_response(raw: str, index: dict[tuple[int, int], str]) -> tuple[list[Removal], str]:
    """解析模型返回的删除清单；任何不合法的编号都丢弃。

    返回 (删除列表, 错误信息)。错误信息为空表示解析成功。
    """
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return [], "模型没有返回 JSON"

    import json

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return [], "模型返回的 JSON 不合法"

    if not isinstance(data, dict):
        return [], "模型返回的不是对象"
    items = data.get("drop")
    if items is None:
        return [], "模型返回里没有 drop 字段"
    if not isinstance(items, list):
        return [], "drop 不是数组"

    removals: list[Removal] = []
    seen: set[tuple[int, int]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            page_no = int(item.get("page"))
            line_no = int(item.get("line"))
        except (TypeError, ValueError):
            continue
        key = (page_no, line_no)
        # 编号必须在我们送出去的清单里，否则就是模型编的，一律不认
        if key not in index or key in seen:
            continue
        seen.add(key)
        removals.append(
            Removal(page_no=page_no, line_no=line_no, text=index[key][:120], reason=REASON_AI)
        )
    return removals, ""


async def ai_filter(
    pages: list[tuple[int, str]],
    *,
    chat_model,
    prompt_template: str,
) -> tuple[set[tuple[int, int]], list[Removal], list[str]]:
    """智能过滤：**整个文档只调一次**模型，让它指出该删哪些行。

    为什么只调一次：这是上一版智能切分翻车的地方 —— 那版把全文按 3000 字
    切段、逐段调模型，75k 字的文档就是 30 次串行请求、十几分钟。
    过滤只需要"看一眼整体"，一次调用绰绰有余。

    返回 (要删的行号集合, 被删记录, 警告)。失败时返回空集合并给出警告，
    调用方据此**保留原文**而不是报错 —— 过滤失败不该让用户没法用文档。
    """
    preview, index, truncated = build_preview(pages)
    if not preview.strip():
        return set(), [], ["这份文档没有可供智能过滤判断的内容，已保留原文。"]

    warnings: list[str] = []
    if truncated:
        warnings.append(
            f"文档较长，只把前 {AI_PREVIEW_CHARS} 字符交给模型判断；没送到的部分保持原文不变。"
        )

    prompt = prompt_template.format(text=preview)
    try:
        response = await chat_model.ainvoke(
            [{"role": "system", "content": AI_FILTER_SYSTEM}, {"role": "user", "content": prompt}]
        )
        content = response.content if isinstance(response.content, str) else str(response.content)
    except Exception as exc:
        logger.warning("智能过滤调用失败，保留原文：%s", exc)
        return set(), [], [f"智能过滤失败（{type(exc).__name__}），已保留原文继续。"]

    removals, error = parse_ai_response(content, index)
    if error:
        logger.warning("智能过滤输出不合规，保留原文：%s", error)
        return set(), [], [f"{error}，已保留原文继续。"]

    dropped = {(item.page_no, item.line_no) for item in removals}
    if dropped:
        warnings.append(f"智能过滤判断 {len(dropped)} 行没有检索价值（目录、声明、封面信息等）。")
    else:
        warnings.append("智能过滤认为整篇都值得保留，没有删除任何内容。")
    return dropped, removals, warnings


def filter_with_ai_lines(
    pages: list[tuple[int, str]], ai_drop: set[tuple[int, int]]
) -> FilterResult:
    """在**原文行号**上同时应用规则与智能删除，一次成型。

    为什么必须一趟处理：``filter_raw`` 会把行重排（压空行、删行），
    重排之后的"第 5 行"已经不是原文的第 5 行了。模型给的编号指向原文，
    所以两类删除必须在**同一趟遍历原文行**时一起判定，各自按原编号生效。
    """
    result = FilterResult(mode="raw+ai")
    headers = _page_header_keys(pages)

    for page_no, text in pages:
        raw_lines = text.splitlines()
        total_lines = len(raw_lines)
        kept: list[str] = []

        for line_no, line in enumerate(raw_lines, start=1):
            stripped = line.strip()

            if not stripped:
                if kept and kept[-1].strip():
                    kept.append("")
                continue

            reason = ""
            if (page_no, line_no) in ai_drop:
                reason = REASON_AI
            elif stripped in headers.get(page_no, set()):
                reason = REASON_HEADER
            elif _is_page_number_at_edge(stripped, line_no, total_lines):
                reason = REASON_PAGE_NUMBER
            elif is_garble(stripped):
                reason = REASON_GARBLE
            else:
                before = line_no == 1 or not raw_lines[line_no - 2].strip()
                after = line_no == total_lines or not raw_lines[line_no].strip()
                if is_orphan_fragment(stripped, before, after):
                    reason = REASON_ORPHAN

            if reason:
                result.removals.append(
                    Removal(page_no=page_no, line_no=line_no, text=stripped[:120], reason=reason)
                )
                continue
            kept.append(stripped)

        while kept and not kept[-1].strip():
            kept.pop()
        cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
        if cleaned.strip():
            result.pages.append((page_no, cleaned))

    if not result.pages:
        result.warnings.append("过滤后没有内容留下，已回退为过滤前的原文。")
    return result
