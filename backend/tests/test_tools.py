"""内置工具的测试：计算器与当前时间。

计算器是**用户输入直接进求值器**的地方，安全性测试比功能测试更重要：
必须证明 ``eval`` 那类东西进不来、除零不崩、超长表达式不卡死。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.services.tools import (
    calculate,
    current_time,
    describe_call,
    run_tool,
    tool_label,
)


# --------------------------------------------------------------------------- #
# 计算器：正确性
# --------------------------------------------------------------------------- #
def test_basic_arithmetic() -> None:
    """四则运算结果正确。"""
    assert calculate("1+1") == "2"
    assert calculate("10-3") == "7"
    assert calculate("6*7") == "42"
    assert calculate("10/4") == "2.5"


def test_decimal_precision_matters() -> None:
    """核心用例：小数必须精确，这正是模型算不准的地方。

    float 下 0.1+0.2 = 0.30000000000000004；Decimal 下才是 0.3。
    """
    assert calculate("0.1+0.2") == "0.3"


def test_reimbursement_case() -> None:
    """真实场景：600 元上限上浮 30%。"""
    assert calculate("600*1.3") == "780"
    assert calculate("600*0.3") == "180"
    assert calculate("(2000+500)*1.13") == "2825"


def test_division_keeps_precision() -> None:
    """除法不丢精度（28 位有效数字）。"""
    result = calculate("1/3")
    assert result.startswith("0.333333333333")


def test_power_and_modulo() -> None:
    """幂与取模。"""
    assert calculate("2**10") == "1024"
    assert calculate("17%5") == "2"
    assert calculate("17//5") == "3"


def test_unary_minus() -> None:
    """负号。"""
    assert calculate("-5+3") == "-2"
    assert calculate("-(2+3)") == "-5"


def test_fullwidth_symbols_normalized() -> None:
    """中文输入法常打出全角数字和符号，都要能识别。"""
    assert calculate("６００×１.３") == "780"
    assert calculate("（2+3）*4") == "20"
    assert calculate("600 × 1.3") == "780"
    assert calculate("10÷4") == "2.5"


def test_whitespace_ignored() -> None:
    """空格不影响结果。"""
    assert calculate(" 1 + 2 * 3 ") == "7"


def test_integral_result_has_no_decimal_point() -> None:
    """整数结果不显示成 780.0，读起来更自然。"""
    assert calculate("600*1.3") == "780"
    assert "." not in calculate("600*1.3")


# --------------------------------------------------------------------------- #
# 计算器：安全性（最重要）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "payload",
    [
        "__import__('os').system('echo hi')",
        "open('/etc/passwd').read()",
        "1+__import__('os').getcwd()",
        "().__class__.__bases__",
        "print('x')",
        "eval('1+1')",
        "x+1",
        "lambda: 1",
        "[1,2,3]",
        "{'a':1}",
        "1 if 1 else 2",
    ],
)
def test_rejects_code_execution(payload: str) -> None:
    """任何函数调用 / 名字 / 容器字面量都必须被拒绝。"""
    with pytest.raises(AppError) as exc:
        calculate(payload)
    assert exc.value.code == "TOOL_BAD_ARGS"


def test_rejects_boolean_literal() -> None:
    """True/False 是 int 子类，不能混进算术。"""
    with pytest.raises(AppError):
        calculate("True+1")


def test_rejects_division_by_zero() -> None:
    """除零给人话错误，不是堆栈。"""
    with pytest.raises(AppError) as exc:
        calculate("1/0")
    assert "除数不能为 0" in exc.value.message


def test_rejects_huge_exponent() -> None:
    """9**9**9 会算到天荒地老，必须挡住。"""
    with pytest.raises(AppError) as exc:
        calculate("9**999")
    assert "指数过大" in exc.value.message


def test_rejects_overlong_expression() -> None:
    """超长表达式拒绝，防止拿它当 DoS。"""
    with pytest.raises(AppError) as exc:
        calculate("1+" * 200 + "1")
    assert "过长" in exc.value.message


def test_rejects_empty_expression() -> None:
    """空表达式给人话提示。"""
    with pytest.raises(AppError) as exc:
        calculate("   ")
    assert exc.value.code == "TOOL_BAD_ARGS"


def test_rejects_syntax_error() -> None:
    """语法错误要有可读提示。"""
    with pytest.raises(AppError) as exc:
        calculate("1++*2")
    assert "语法有误" in exc.value.message


# --------------------------------------------------------------------------- #
# 当前时间
# --------------------------------------------------------------------------- #
def test_current_time_has_date_and_time() -> None:
    """返回里必须有年月日和时分秒。"""
    text = current_time()
    assert len(text) > 10
    assert "UTC" in text
    # 形如 2026-11-26 00:40:00
    assert text[:4].isdigit()
    assert text[4] == "-"
    assert text.count(":") >= 2


def test_current_time_mentions_local_and_utc() -> None:
    """本地与 UTC 都要给，用户按哪个算都行。"""
    text = current_time()
    assert "UTC" in text
    assert "周" in text, "应包含星期"


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
def test_tool_schemas_cover_registry() -> None:
    """每个注册的工具都必须有给模型看的 schema，否则模型永远调不到它。"""
    from app.services.tools import TOOL_SCHEMAS

    schema_names = {item["function"]["name"] for item in TOOL_SCHEMAS}
    for name in ("calculator", "get_current_time"):
        assert name in schema_names

    for item in TOOL_SCHEMAS:
        assert item["type"] == "function"
        assert item["function"]["description"].strip(), "描述为空模型不会选它"


def test_run_tool_dispatches() -> None:
    """按名字分派到正确的工具。"""
    assert run_tool("calculator", {"expression": "2*3"}) == "6"
    assert "UTC" in run_tool("get_current_time", {})


def test_run_tool_unknown_name() -> None:
    """不认识的工具报明确错误。"""
    with pytest.raises(AppError) as exc:
        run_tool("rm_rf", {})
    assert exc.value.code == "TOOL_NOT_FOUND"


def test_run_tool_missing_args() -> None:
    """缺参数不能崩，要转成用户能懂的错。"""
    with pytest.raises(AppError) as exc:
        run_tool("calculator", {})
    assert exc.value.code == "TOOL_BAD_ARGS"


def test_describe_call_is_readable() -> None:
    """轨迹面板显示的那一行要说清调了什么、参数是什么。"""
    assert describe_call("calculator", {"expression": "600*1.3"}) == "计算器（600*1.3）"
    assert describe_call("get_current_time", {}) == "当前时间"


def test_tool_label_fallback() -> None:
    """未知工具名原样返回，不要显示成 None。"""
    assert tool_label("unknown_tool") == "unknown_tool"


def test_decimal_not_leaked() -> None:
    """返回值必须是 str，不能把 Decimal 泄漏给上层（JSON 不认它）。"""
    result = calculate("1+1")
    assert isinstance(result, str)
    assert not isinstance(Decimal(result), type(None))


# --------------------------------------------------------------------------- #
# 算术需求判定（决定要不要逼模型调工具）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "question",
    [
        "一线城市住3晚，一共报多少钱？",
        "住宿超标 30% 是多少",
        "总共能报多少钱",
        "合计一下金额",
        "按 130% 折算后是多少",
        "每晚 600 元，3 晚多少钱",
    ],
)
def test_needs_arithmetic_detects_calc_questions(question: str) -> None:
    """这些都是在问「算一下」，必须判为需要算术。"""
    from app.services.chat import needs_arithmetic

    assert needs_arithmetic(question, "一线城市每晚上限 600 元。") is True


@pytest.mark.parametrize(
    "question",
    [
        "差旅报销要提交哪些材料？",
        "报销流程是怎样的？",
        "谁能审批？",
        "今天几号",  # 时间题，不该逼它调计算器
    ],
)
def test_needs_arithmetic_ignores_non_calc(question: str) -> None:
    """这些不需要算术，不要白逼模型调计算器。"""
    from app.services.chat import needs_arithmetic

    assert needs_arithmetic(question, "一线城市每晚上限 600 元。") is False


def test_needs_arithmetic_requires_numbers_in_context() -> None:
    """光有「多少」但上下文没有数字时不算，没东西可算。"""
    from app.services.chat import needs_arithmetic

    assert needs_arithmetic("这个多少钱", "本文档没有任何数字。") is False


def test_forced_retry_is_implemented() -> None:
    """必须有一条「模型偷懒不调工具 → 强制补一轮」的兜底。

    实测：同一个提示词连跑 8 次，有 1 次模型就是不肯调（觉得 600×3 太简单），
    而心算正是算错的来源。光靠提示词不够，代码里必须有兜底。
    """
    import inspect

    from app.services import chat as chat_service

    source = inspect.getsource(chat_service._run_tool_rounds)
    assert "needs_calc" in source
    assert "forced" in source
    assert "必须修正" in source, "强制重试时要给出更明确的指令"


def test_first_token_ms_not_zero_when_tool_round_answers() -> None:
    """工具轮直接给出答案时，「首字」不能是 0ms（那不合理，用户会怀疑）。"""
    import inspect

    from app.services import chat as chat_service

    source = inspect.getsource(chat_service._stream_impl)
    # final_text 分支里必须真的计算耗时，而不是硬写 0
    assert "first_token_ms = 0\n" not in source.split("if final_text:")[1].split("else:")[0], (
        "final_text 分支不能把 first_token_ms 硬编码为 0"
    )
