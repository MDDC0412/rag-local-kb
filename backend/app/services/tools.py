"""内置工具：计算器与当前时间。

为什么需要工具（而不是让模型自己算）
------------------------------------
模型算数会错，尤其是小数。这不是"模型不够聪明"，是它的**输出机制**决定的：
它按 token 概率生成，`600 × 1.3` 可能给 780，也可能给 779.9999。
答案里出现这种数字，用户是无法信任的。

为什么**不用** `eval()`
-----------------------
`eval("__import__('os').system('...')")` 是真实的远程执行漏洞。
这里用 `ast` 走白名单：只认数字、四则运算、括号、幂，其余一律拒绝。

为什么用 `Decimal` 而不是 `float`
---------------------------------
`float` 是二进制浮点，`0.1 + 0.2 != 0.3`。报销金额这类场景必须精确到分。
`Decimal` 是十进制，`600 * 0.3` 就是精确的 `180.0`。
"""

from __future__ import annotations

import ast
import logging
import operator
from datetime import UTC, datetime
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from app.core.errors import AppError

logger = logging.getLogger(__name__)

# 计算器最多接受这么多字符，防止有人拿一亿位数把 CPU 卡死
MAX_EXPRESSION_LEN = 200
# 幂运算的指数上限，防止 9**9**9 这种天文数字
MAX_EXPONENT = 100

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# 全角数字/字母 → 半角（U+FF01..U+FF5E 整段平移 0xFEE0）
_FULLWIDTH_TABLE = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
_FULLWIDTH_TABLE[0x3000] = ord(" ")  # 全角空格


class _CalcError(ValueError):
    """计算器内部错误，外部统一转成 AppError。"""


def _to_decimal(node: ast.AST) -> Decimal:
    """把字面量转成 Decimal；只接受整数和浮点字面量。"""
    if isinstance(node, ast.Constant):
        value = node.value
        # bool 是 int 的子类，要显式排除，否则 True 会被当成 1
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise _CalcError(f"不支持的常量：{value!r}")
        # 用 str 中转，避免 float 已经带上二进制误差
        return Decimal(str(value))
    raise _CalcError("表达式里只能出现数字")


def _eval_node(node: ast.AST) -> Decimal:
    """递归求值，只走白名单节点。"""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        func = _BIN_OPS.get(op_type)
        if func is None:
            raise _CalcError(f"不支持的运算符：{op_type.__name__}")

        left = _eval_node(node.left)
        right = _eval_node(node.right)

        if op_type is ast.Pow and abs(right) > MAX_EXPONENT:
            raise _CalcError(f"指数过大（上限 {MAX_EXPONENT}），请拆成多步算")

        return func(left, right)

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        func = _UNARY_OPS.get(op_type)
        if func is None:
            raise _CalcError(f"不支持的一元运算符：{op_type.__name__}")
        return func(_eval_node(node.operand))

    # 括号在 AST 里不单独成节点，这里只剩字面量
    return _to_decimal(node)


def _format(value: Decimal) -> str:
    """把 Decimal 输出成好读的字符串：去掉多余的 0，但不吞掉有效小数。"""
    if value == value.to_integral_value():
        return str(value.quantize(Decimal(1)))
    normalized = value.normalize()
    # normalize 可能产出 1E+2 这种科学计数法，转回普通写法
    return format(normalized, "f")


def calculate(expression: str) -> str:
    """安全地计算一个数学表达式，返回结果的字符串形式。

    只支持数字与 ``+ - * / // % **`` 和括号；任何名字、函数调用、属性访问一律拒绝。
    """
    text = (expression or "").strip()
    if not text:
        raise AppError("TOOL_BAD_ARGS", "计算器需要一个表达式，例如 600*1.3", status_code=400)
    if len(text) > MAX_EXPRESSION_LEN:
        raise AppError(
            "TOOL_BAD_ARGS",
            f"表达式过长（上限 {MAX_EXPRESSION_LEN} 字符），请拆成几步算",
            status_code=400,
        )

    # 全角字符在中文字境里很常见，先归一化，否则 ast 解析直接报语法错。
    # 包括全角数字（６００）、全角运算符与括号、全角空格。
    normalized = (
        text.replace("×", "*")
        .replace("÷", "/")
        .replace("（", "(")
        .replace("）", ")")
        .replace("，", ",")
        .replace("．", ".")
        .translate(_FULLWIDTH_TABLE)
    )
    normalized = "".join(normalized.split())

    try:
        tree = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        raise AppError("TOOL_BAD_ARGS", f"表达式语法有误：{expression}", status_code=400) from exc

    try:
        result = _eval_node(tree)
    except (DivisionByZero, ZeroDivisionError) as exc:
        raise AppError("TOOL_BAD_ARGS", "除数不能为 0", status_code=400) from exc
    except InvalidOperation as exc:
        raise AppError(
            "TOOL_BAD_ARGS", "数值超出可计算范围，请拆成几步算", status_code=400
        ) from exc
    except _CalcError as exc:
        raise AppError("TOOL_BAD_ARGS", str(exc), status_code=400) from exc

    return _format(result)


def current_time() -> str:
    """当前时间（本地时区与 UTC 都给），不经过模型生成。"""
    local = datetime.now().astimezone()
    utc = datetime.now(UTC)
    offset = local.strftime("%z")
    offset_text = f"UTC{offset[:3]}:{offset[3:]}" if offset else "UTC"
    weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return (
        f"{local.strftime('%Y-%m-%d %H:%M:%S')} {weekdays[local.weekday()]}"
        f"（{offset_text}，时区 {local.tzname() or '本地'}）"
        f"；UTC 为 {utc.strftime('%Y-%m-%d %H:%M:%S')}"
    )


# --------------------------------------------------------------------------- #
# 工具注册表
# --------------------------------------------------------------------------- #
# 给模型看的 schema（OpenAI function calling 格式）
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": (
                "计算数学表达式，支持 + - * / // % ** 和括号。"
                "凡是需要做算术的时候都必须调用它，不要自己心算 —— "
                "模型心算小数和多位乘除经常出错。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "要计算的表达式，例如 600*0.3 或 (2000+500)*1.13",
                    }
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": (
                "获取当前真实时间（本地时区 + UTC）。"
                "用户问「今天几号」「现在几点」「今年是哪年」或需要按当前日期推算时调用。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

# 工具名 → (可调用函数, 中文标签)
_REGISTRY = {
    "calculator": (calculate, "计算器"),
    "get_current_time": (current_time, "当前时间"),
}


def tool_label(name: str) -> str:
    """工具的中文名（给轨迹面板显示）。"""
    entry = _REGISTRY.get(name)
    return entry[1] if entry else name


def run_tool(name: str, args: dict[str, Any]) -> str:
    """执行一个工具，返回结果文本；参数不合法或不认识的工具都抛 AppError。"""
    entry = _REGISTRY.get(name)
    if entry is None:
        raise AppError("TOOL_NOT_FOUND", f"没有名为「{name}」的工具", status_code=400)

    func = entry[0]
    try:
        if name == "calculator":
            return func(str(args.get("expression", "")))
        return func()
    except AppError:
        raise
    except TypeError as exc:
        raise AppError(
            "TOOL_BAD_ARGS", f"工具「{name}」的参数不对：{exc}", status_code=400
        ) from exc


def describe_call(name: str, args: dict[str, Any]) -> str:
    """把「调了什么、带了什么参数」压成一行，给轨迹面板用。"""
    label = tool_label(name)
    if name == "calculator":
        return f"{label}（{args.get('expression', '')}）"
    return label
