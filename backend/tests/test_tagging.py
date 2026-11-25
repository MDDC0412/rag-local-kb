"""标签分类的纯逻辑测试：归一化、受控词表、模型输出解析。

不打真实模型 —— 需要外部 API 的部分属于 integration，默认跳过。
这里覆盖的都是「模型给了什么就该得到什么」的确定性规则。
"""

from __future__ import annotations

import pytest

from app.db.models import Category
from app.services.tagging import (
    MAX_TAGS,
    TagResult,
    clean_tag,
    extract_json,
    normalize_tags,
    parse_payload,
    pick_category,
    tag_key,
)


# --------------------------------------------------------------------------- #
# 标签归一化
# --------------------------------------------------------------------------- #
def test_clean_tag_strips_space_and_punct() -> None:
    """标签里不该留空白和标点。"""
    assert clean_tag("  差旅 报销 ") == "差旅报销"
    assert clean_tag("报销，流程。") == "报销流程"
    assert clean_tag("【制度】") == "制度"


def test_clean_tag_keeps_inner_meaning() -> None:
    """英文、数字、中文都要保留，别误删。"""
    assert clean_tag("XR-2000") == "XR2000"
    assert clean_tag("OA 系统") == "OA系统"
    assert clean_tag("2024年度") == "2024年度"


def test_clean_tag_truncates_long_names() -> None:
    """超长标签截断，不能让它撑爆数据库字段。"""
    assert len(clean_tag("很" * 100)) == 32


def test_clean_tag_handles_empty() -> None:
    """空值返回空串，不抛异常。"""
    assert clean_tag("") == ""
    assert clean_tag("   ") == ""
    assert clean_tag("，，。") == ""


def test_tag_key_is_case_insensitive() -> None:
    """比较键要折叠大小写，避免 OA / oa 变成两个标签。"""
    assert tag_key("OA") == tag_key("oa")
    assert tag_key("OA 系统") == tag_key("oa系统")


def test_normalize_tags_dedupes_by_key() -> None:
    """同一标签的不同写法只留第一个。"""
    result = normalize_tags(["报销", "报销 ", "報銷", "报销"])
    assert result == ["报销", "報銷"]


def test_normalize_tags_keeps_order() -> None:
    """顺序保持模型给的样子，别自作主张排序。"""
    assert normalize_tags(["差旅", "报销", "制度"]) == ["差旅", "报销", "制度"]


def test_normalize_tags_limits_count() -> None:
    """最多 MAX_TAGS 个，防止模型一口气给 20 个。"""
    result = normalize_tags([f"标签{i}" for i in range(20)])
    assert len(result) == MAX_TAGS


def test_normalize_tags_drops_empty() -> None:
    """清洗后为空的直接丢掉。"""
    assert normalize_tags(["报销", "", "   ", "，，"]) == ["报销"]


# --------------------------------------------------------------------------- #
# 受控分类词表
# --------------------------------------------------------------------------- #
def _cats(*names: str) -> list[Category]:
    """造几个分类对象（不需要数据库）。"""
    return [Category(id=index + 1, name=name, description="") for index, name in enumerate(names)]


def test_pick_category_exact_match() -> None:
    """精确命中已有分类就复用，不新建。"""
    existing = _cats("制度规范", "财务相关")
    matched, new_name = pick_category("财务相关", existing)
    assert matched is not None
    assert matched.name == "财务相关"
    assert new_name is None


def test_pick_category_matches_after_normalization() -> None:
    """模型多带空格标点时仍能对上已有分类。"""
    existing = _cats("制度规范")
    matched, new_name = pick_category(" 制度规范。", existing)
    assert matched is not None
    assert matched.name == "制度规范"
    assert new_name is None


def test_pick_category_allows_one_new() -> None:
    """确实没有、且名字够短，才允许新建。"""
    matched, new_name = pick_category("合同模板", _cats("制度规范"))
    assert matched is None
    assert new_name == "合同模板"


def test_pick_category_rejects_overlong_new_name() -> None:
    """太长的名字不能当分类，否则分类会变成一句话。"""
    matched, new_name = pick_category("这是一份关于差旅报销的综合性管理文件", _cats("制度规范"))
    assert matched is None
    assert new_name is None


def test_pick_category_respects_allow_new_false() -> None:
    """不允许新建时，对不上就返回空，交给调用方保留原分类。"""
    matched, new_name = pick_category("全新分类", _cats("制度规范"), allow_new=False)
    assert matched is None
    assert new_name is None


def test_pick_category_empty_suggestion() -> None:
    """模型没给分类时不报错。"""
    assert pick_category("", _cats("制度规范")) == (None, None)


# --------------------------------------------------------------------------- #
# 模型输出解析
# --------------------------------------------------------------------------- #
def test_extract_json_plain() -> None:
    """标准 JSON 直接解析。"""
    data = extract_json('{"summary": "讲了报销", "category": "财务相关", "tags": ["报销"]}')
    assert data["summary"] == "讲了报销"


def test_extract_json_with_code_fence() -> None:
    """容忍 ```json 代码块包裹。"""
    raw = '```json\n{"summary": "x", "category": "y", "tags": []}\n```'
    assert extract_json(raw)["summary"] == "x"


def test_extract_json_with_surrounding_text() -> None:
    """容忍模型在 JSON 前后加废话。"""
    raw = '好的，结果如下：\n{"summary": "x", "category": "y", "tags": []}\n希望有帮助。'
    assert extract_json(raw)["summary"] == "x"


def test_extract_json_rejects_no_object() -> None:
    """没有 JSON 对象必须报错，不能静默返回空。"""
    with pytest.raises(ValueError):
        extract_json("这段文本里没有 JSON")


def test_extract_json_rejects_non_object() -> None:
    """数组不是我们要的结构。"""
    with pytest.raises(ValueError):
        extract_json("[1, 2, 3]")


def test_parse_payload_full() -> None:
    """三个字段都齐时正常解析。"""
    payload = parse_payload('{"summary": "摘要", "category": "财务相关", "tags": ["报销", "差旅"]}')
    assert payload.summary == "摘要"
    assert payload.category == "财务相关"
    assert payload.tags == ["报销", "差旅"]


def test_parse_payload_coerces_string_tags() -> None:
    """模型偶尔把 tags 写成逗号分隔的字符串，要能救回来。"""
    payload = parse_payload('{"summary": "s", "category": "c", "tags": "报销，差旅、制度"}')
    assert payload.tags == ["报销", "差旅", "制度"]


def test_parse_payload_tolerates_null_fields() -> None:
    """字段给 null 不算错，转成空值即可。"""
    payload = parse_payload('{"summary": null, "category": null, "tags": null}')
    assert payload.summary == ""
    assert payload.category == ""
    assert payload.tags == []


def test_parse_payload_missing_fields() -> None:
    """缺字段用默认值，不炸。"""
    payload = parse_payload('{"summary": "只有摘要"}')
    assert payload.tags == []
    assert payload.category == ""


def test_parse_payload_truncates_summary() -> None:
    """摘要过长截断，避免撑爆字段。"""
    payload = parse_payload('{"summary": "' + "长" * 1000 + '"}')
    assert len(payload.summary) == 500


def test_tag_result_describe() -> None:
    """任务结果摘要要能说清「做了什么」。"""
    result = TagResult(summary="s", category="财务相关", tags=["报销", "差旅"])
    text = result.describe()
    assert "财务相关" in text
    assert "2 个标签" in text

    empty = TagResult()
    assert "只生成了摘要" in empty.describe()
