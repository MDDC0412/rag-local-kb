"""过滤器的测试。

这一组的重点不是"能不能删"，而是**会不会误删正文** ——
知识库里丢一段正文，比多留一行噪声严重得多。所以每条规则都配一个反例。
"""

from __future__ import annotations

import pytest

from app.services import filtering
from app.services.filtering import (
    REASON_AI,
    REASON_HEADER,
    REASON_ORPHAN,
    REASON_PAGE_NUMBER,
    filter_raw,
    filter_with_ai_lines,
    is_garble,
    is_orphan_fragment,
    is_page_number,
)


# --------------------------------------------------------------------------- #
# 页码识别
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("line", ["12", "- 3 -", "第 5 页", "IV", "  vii  ", "— 88 —"])
def test_page_numbers(line: str) -> None:
    """各种页码写法都要认出来。"""
    assert is_page_number(line) is True


@pytest.mark.parametrize("line", ["第 5 条", "12 元", "2023 年", "第一章 总则", "住宿标准"])
def test_not_page_numbers(line: str) -> None:
    """看起来像数字但是正文的，绝不能当页码删。"""
    assert is_page_number(line) is False


def test_bare_number_form_is_ambiguous() -> None:
    """`600` 形态上是页码候选，但它可能是金额 —— 所以只有位置版才敢删。"""
    # 形态判定认为它是页码候选
    assert is_page_number("600") is True

    # 但位置版：出现在正文中间时绝不删
    lines = [
        "住宿标准如下。",
        "一线城市每晚上限 600 元。",
        "600",
        "其他城市每晚上限 400 元。",
        "以上标准自发布之日起执行。",
        "超支部分需部门经理审批。",
        "住宿费需凭发票报销。",
        "本办法由财务部解释。",
    ]
    result = filter_raw([(1, "\n".join(lines))])
    assert not [item for item in result.removals if item.reason == REASON_PAGE_NUMBER]
    assert "600" in result.pages[0][1]


def test_bare_number_at_page_edge_is_removed() -> None:
    """真正在页尾的裸数字才是页码。"""
    lines = ["正文第一行。", "正文第二行。", "正文第三行。", "正文第四行。", "12"]
    result = filter_raw([(1, "\n".join(lines))])

    removed = [item for item in result.removals if item.reason == REASON_PAGE_NUMBER]
    assert len(removed) == 1
    assert removed[0].text == "12"


# --------------------------------------------------------------------------- #
# 乱码识别
# --------------------------------------------------------------------------- #
def test_garble_replacement_char() -> None:
    """替换符 = 解码失败，删。"""
    assert is_garble("这是一段\ufffd\ufffd文字") is True


def test_garble_symbol_soup() -> None:
    """纯符号行没有检索价值。"""
    assert is_garble("◆◆◆◆◆◆◆◆◆◆") is True


@pytest.mark.parametrize(
    "line",
    [
        "第一条 为了惩罚犯罪，保护人民，根据宪法，制定本法。",
        "3.2 错误响应格式如下：",
        "（一）管制；",
        "600 元 / 晚",
        "a = b + c",
    ],
)
def test_not_garble(line: str) -> None:
    """正常中文、条款、数字、符号混排的正文都不能被当乱码删掉。"""
    assert is_garble(line) is False


# --------------------------------------------------------------------------- #
# 孤儿碎片行
# --------------------------------------------------------------------------- #
def test_orphan_requires_blank_on_both_sides() -> None:
    """极短行必须两侧都有空行才算碎片。"""
    assert is_orphan_fragment("附", has_blank_before=True, has_blank_after=True) is True


@pytest.mark.parametrize(
    "before,after",
    [(False, True), (True, False), (False, False)],
)
def test_orphan_not_removed_when_touching_text(before: bool, after: bool) -> None:
    """紧贴正文的短行可能是条款列表项，必须保留。"""
    assert (
        is_orphan_fragment("（一）管制；", has_blank_before=before, has_blank_after=after) is False
    )


def test_orphan_not_removed_when_long_enough() -> None:
    """字数够多就不是碎片，哪怕两侧都有空行。"""
    assert (
        is_orphan_fragment("这是一句正常长度的正文。", has_blank_before=True, has_blank_after=True)
        is False
    )


# --------------------------------------------------------------------------- #
# 页眉页脚：只能按位置判断，不能按全文重复
# --------------------------------------------------------------------------- #
def test_header_removed_by_position_repetition() -> None:
    """每页开头都出现的同一行 = 页眉，删。"""
    body = "正文第一句。\n正文第二句。\n正文第三句。\n正文第四句。\n正文第五句。\n正文第六句。"
    pages = [
        (1, f"产品手册\n第一章 概述\n{body}"),
        (2, f"产品手册\n第二章 安装\n{body}"),
        (3, f"产品手册\n第三章 使用\n{body}"),
    ]
    result = filter_raw(pages)

    removed = [item for item in result.removals if item.reason == REASON_HEADER]
    assert len(removed) == 3
    assert all(item.text == "产品手册" for item in removed)
    # 正文必须全部留下
    joined = "\n".join(text for _, text in result.pages)
    assert "第一章 概述" in joined
    assert "第二章 安装" in joined
    assert "正文第二句。" in joined


def test_repeated_body_sentence_not_removed_as_header() -> None:
    """⚠️ 每页同位置都是同一句**正文**时，不能当页眉删。

    形态闸的作用：页眉短、不以句号结尾；正文句子长、以句号结尾。
    模板化生成的文档（每页首行都是同一句正文）曾在这里被整段删掉。
    """
    body_sentence = "本页所有内容均依据公司现行制度编写并接受审计监督。"
    pages = [
        (
            1,
            f"{body_sentence}\n第一段正文。\n第二段正文。\n第三段正文。\n第四段正文。\n第五段正文。",
        ),
        (
            2,
            f"{body_sentence}\n第六段正文。\n第七段正文。\n第八段正文。\n第九段正文。\n第十段正文。",
        ),
        (
            3,
            f"{body_sentence}\n第十一段正文。\n第十二段正文。\n第十三段正文。\n第十四段正文。\n第十五段正文。",
        ),
    ]
    result = filter_raw(pages)

    assert not [item for item in result.removals if item.reason == REASON_HEADER]
    joined = "\n".join(text for _, text in result.pages)
    assert joined.count(body_sentence) == 3, "每页的这句话都必须保留"


def test_tiny_page_not_treated_as_header() -> None:
    """页内行数太少时，"页首行"其实就是整页，不能拿它做页眉判定。

    这是实测踩出来的：两页各 3 行，内容完全不同，
    但第一行都被当成"页首重复"删掉了 —— 那是正文。
    """
    pages = [
        (1, "第一条 开头。\n第二条 中间。\n第三条 结尾。"),
        (2, "第四条 开头。\n第五条 中间。\n第六条 结尾。"),
    ]
    result = filter_raw(pages)

    assert not [item for item in result.removals if item.reason == REASON_HEADER]
    joined = "\n".join(text for _, text in result.pages)
    for clause in ("第一条", "第三条", "第六条"):
        assert clause in joined


def test_body_repetition_is_never_removed() -> None:
    """⚠️ 正文里重复出现的条文绝不能删 —— 这是刑法文档踩过的点。

    "单位犯前款罪的……" 在同一份法条里出现 29 次，那是条文复述。
    规则只看页首/页尾**位置**上的重复，所以它在正文中不会被误删。
    """
    repeated = (
        "单位犯前款罪的，对单位判处罚金，并对其直接负责的主管人员和其他直接责任人员，"
        "依照前款的规定处罚。"
    )
    pages = [
        (1, f"第一条 开头。\n{repeated}\n第二条 中间。\n{repeated}\n第三条 结尾。"),
        (2, f"第四条 另一页。\n{repeated}\n第五条 收尾。"),
    ]
    result = filter_raw(pages)

    assert not [item for item in result.removals if item.reason == REASON_HEADER]
    kept = "\n".join(text for _, text in result.pages)
    assert kept.count(repeated) == 3, "正文中重复的条文一条都不能少"


def test_single_page_repetition_not_treated_as_header() -> None:
    """只有一页时不存在"跨页重复"，不能拿它当页眉。"""
    repeated = "重复出现的一句话"
    result = filter_raw([(1, f"{repeated}\n正文。\n{repeated}")])

    assert not [item for item in result.removals if item.reason == REASON_HEADER]


# --------------------------------------------------------------------------- #
# 整体效果
# --------------------------------------------------------------------------- #
def test_filter_removes_page_number_and_orphan() -> None:
    """页码和带空行包裹的碎片行都要删掉。"""
    text = "第一章 概述\n\n附\n\n这是正文段落。\n\n12"
    result = filter_raw([(1, text)])

    reasons = {item.reason for item in result.removals}
    assert REASON_PAGE_NUMBER in reasons
    assert REASON_ORPHAN in reasons
    assert "这是正文段落。" in result.pages[0][1]


def test_filter_keeps_clause_list_items() -> None:
    """条款列表项紧贴正文，绝不能被当碎片删。"""
    text = "刑罚分为：\n（一）管制；\n（二）拘役；\n（五）死刑。"
    result = filter_raw([(1, text)])

    assert result.removed_count == 0
    for item in ("（一）管制；", "（二）拘役；", "（五）死刑。"):
        assert item in result.pages[0][1]


def test_filter_never_rewrites_text() -> None:
    """只删整行，不能改写行内文字 —— 引用的必须是原文。"""
    text = "第一条　为了惩罚犯罪，保护人民，根据宪法，制定本法。"
    result = filter_raw([(1, text)])

    assert result.pages[0][1] == text


def test_filter_collapses_blank_lines() -> None:
    """连续空行压成一个，但段落分隔要保留。"""
    result = filter_raw([(1, "第一段\n\n\n\n第二段")])

    assert result.pages[0][1] == "第一段\n\n第二段"


def test_filter_drops_empty_pages() -> None:
    """被删空的页不再出现在结果里。"""
    result = filter_raw([(1, "12"), (2, "正文内容。")])

    assert [page_no for page_no, _ in result.pages] == [2]


def test_filter_all_removed_warns() -> None:
    """全删光要报警告，而不是静默返回空。"""
    result = filter_raw([(1, "12"), (2, "- 3 -")])

    assert result.pages == []
    assert result.warnings


def test_filter_counts_removals() -> None:
    """删除计数要能对上，前端据此显示"删了几行"。"""
    result = filter_raw([(1, "正文\n12\n\n- 4 -")])

    assert result.removed_count == len(result.removals)


# --------------------------------------------------------------------------- #
# 智能过滤：只按行号删，不信模型的内容
# --------------------------------------------------------------------------- #
def test_ai_removals_marked_with_reason() -> None:
    """模型删的行要标成智能原因，便于用户区分"谁删的"。"""
    pages = [(1, "目录\n第一章 …… 1\n正文内容。")]
    result = filter_with_ai_lines(pages, {(1, 1), (1, 2)})

    ai_items = [item for item in result.removals if item.reason == REASON_AI]
    assert len(ai_items) == 2
    assert "正文内容。" in result.pages[0][1]


def test_ai_and_raw_filters_combine_in_one_pass() -> None:
    """规则删除与智能删除要在**同一趟**按原文行号生效。

    如果先跑规则过滤再按模型给的行号删，行号早就错位了 ——
    规则会删掉一些行，后面的行号整体前移。
    """
    pages = [(1, "页眉\n目录条目\n正文第一段。\n12")]
    # 模型只要求删第 2 行（目录条目）；页眉与页码由规则删
    result = filter_with_ai_lines(pages, {(1, 2)})

    reasons = {item.reason for item in result.removals}
    assert REASON_AI in reasons or REASON_HEADER in reasons
    assert REASON_PAGE_NUMBER in reasons
    assert "正文第一段。" in result.pages[0][1]
    # 目录条目被智能删掉了
    assert "目录条目" not in result.pages[0][1]


def test_ai_ignores_out_of_range_line_numbers() -> None:
    """行号越界就忽略，不能因为模型乱给就把正文删了。"""
    pages = [(1, "第一行\n第二行")]
    result = filter_with_ai_lines(pages, {(1, 99), (9, 1)})

    assert "第一行" in result.pages[0][1]
    assert "第二行" in result.pages[0][1]


def test_preview_builds_stable_line_numbers() -> None:
    """概览里的行号必须与原文行号一致，否则模型给的行号对不上。"""
    pages = [(1, "甲\n\n乙\n丙")]
    preview, index, truncated = filtering.build_preview(pages)

    assert "1:1|甲" in preview
    assert "1:3|乙" in preview, "空行也占行号，乙是第 3 行"
    assert index[(1, 3)] == "乙"
    assert truncated is False


def test_preview_truncates_long_document() -> None:
    """超长文档只送概览，并且明确告知被截断。"""
    pages = [(1, "\n".join(f"第{i}行内容" for i in range(5000)))]
    preview, _, truncated = filtering.build_preview(pages)

    assert truncated is True
    assert len(preview) <= filtering.AI_PREVIEW_CHARS + 200


def test_parse_ai_response_accepts_valid_json() -> None:
    """标准输出能解析。"""
    index = {(1, 1): "目录", (1, 2): "正文"}
    removals, error = filtering.parse_ai_response('{"drop": [{"page": 1, "line": 1}]}', index)

    assert error == ""
    assert [item.line_no for item in removals] == [1]


def test_parse_ai_response_tolerates_fence() -> None:
    """```json 包裹也能解析。"""
    index = {(1, 1): "目录"}
    removals, error = filtering.parse_ai_response(
        '```json\n{"drop": [{"page": 1, "line": 1}]}\n```', index
    )

    assert error == ""
    assert len(removals) == 1


def test_parse_ai_response_drops_invented_line_numbers() -> None:
    """模型编出来的行号一律不认 —— 这是防止误删正文的关键一道闸。"""
    index = {(1, 1): "目录"}
    removals, _ = filtering.parse_ai_response(
        '{"drop": [{"page": 1, "line": 1}, {"page": 5, "line": 7}]}', index
    )

    assert len(removals) == 1
    assert (removals[0].page_no, removals[0].line_no) == (1, 1)


@pytest.mark.parametrize(
    "raw",
    ["没有 JSON", '{"drop": "不是数组"}', '{"其它": []}', "{坏掉的 json"],
)
def test_parse_ai_response_reports_errors(raw: str) -> None:
    """各种不合规输出都要给出错误，让上层保留原文。"""
    removals, error = filtering.parse_ai_response(raw, {(1, 1): "x"})

    assert removals == []
    assert error


def test_parse_ai_response_empty_drop() -> None:
    """模型认为都该保留时，返回空清单且不报错。"""
    removals, error = filtering.parse_ai_response('{"drop": []}', {})

    assert removals == []
    assert error == ""


# --------------------------------------------------------------------------- #
# 提示词兜底
# --------------------------------------------------------------------------- #
def test_fallback_prompt_has_placeholder() -> None:
    """兜底模板必须含 {text}，否则 format 会炸。"""
    template = filtering.ai_prompt_fallback()

    assert "{text}" in template
    assert template.format(text="示例").count("示例") == 1


def test_fallback_prompt_forbids_rewriting() -> None:
    """提示词里必须明确"拿不准就保留"，这是安全的最后一道防线。"""
    template = filtering.ai_prompt_fallback()

    assert "保留" in template
