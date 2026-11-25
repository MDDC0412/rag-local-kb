"""提示词优化：把用户的口语化提问改写成可检索的完整问题。

刻意做成"只提交建议、不覆盖输入"：
前端并排显示原稿与优化稿，由用户点「采用」才写入输入框。
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PromptTemplate
from app.providers.llm import build_chat_model
from app.providers.registry import resolve_spec

logger = logging.getLogger(__name__)

FALLBACK_TEMPLATE = """你是提问优化助手。用户会给你一句口语化、含糊或缺少上下文的问题。
把它改写成一句清晰、完整、可直接用于检索的正式提问。

要求：
1. 保留用户原意，不要添加用户没提过的约束（金额、时间、部门等一律不要自己编）。
2. 补全代词（「那个」「它」要落到具体事物上），依据是对话历史。
3. 只输出 JSON：{{"optimized": "改写后的问题", "rationale": "一句话说明你改了什么"}}
4. 不要输出 JSON 以外的任何字符。

对话历史：
{history}

用户原问题：{question}"""


def _history_text(history: list[dict], limit: int = 6) -> str:
    """把最近几轮对话压成文本。"""
    if not history:
        return "（无）"
    lines = []
    for item in history[-limit:]:
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("content", "")).strip()
        if content:
            lines.append(f"{role}：{content[:200]}")
    return "\n".join(lines) if lines else "（无）"


def _parse(raw: str) -> tuple[str, str]:
    """从模型输出里取 optimized / rationale，容忍代码块包裹。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("模型输出里没有 JSON 对象")

    data = json.loads(text[start : end + 1])
    optimized = str(data.get("optimized", "")).strip()
    rationale = str(data.get("rationale", "")).strip()
    if not optimized:
        raise ValueError("optimized 为空")
    return optimized, rationale


async def _template(session: AsyncSession) -> str:
    """取提示词库里的优化模板，取不到用内置兜底。"""
    row = (
        await session.execute(
            select(PromptTemplate)
            .where(PromptTemplate.kind == "optimize")
            .order_by(PromptTemplate.is_default.desc(), PromptTemplate.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row.content if row and row.content.strip() else FALLBACK_TEMPLATE


async def optimize_question(
    session: AsyncSession, *, question: str, history: list[dict]
) -> tuple[str, str]:
    """返回 (优化后的问题, 改动说明)。失败时退回原问题并给出原因。"""
    spec = await resolve_spec(session, "llm")
    model = build_chat_model(spec, temperature=0.2, streaming=False)
    template = await _template(session)

    prompt = template.format(history=_history_text(history), question=question)
    try:
        response = await model.ainvoke(prompt)
        content = response.content if isinstance(response.content, str) else str(response.content)
        return _parse(content)
    except Exception as exc:
        logger.warning("提问优化失败：%s", exc)
        return question, "优化服务暂时不可用，已保留原问题"
