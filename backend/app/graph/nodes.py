"""图的节点实现。每个节点单一职责，不写数据库。

节点划分见 docs/DESIGN.md §3.5：
    rewrite → retrieve → fuse → rerank → grade →(fallback | generate)
"""

from __future__ import annotations

import json
import logging

from app.graph.state import RagState
from app.providers.base import ProviderSpec, embed_texts
from app.services.retrieval import RetrievalOptions, retrieve

logger = logging.getLogger(__name__)

REWRITE_TEMPLATE = """把这一轮对话里的追问补全成一个可以独立检索的问题。
只输出改写后的完整问题，不要解释，不要加引号。

对话历史：
{history}

用户这一句：{question}"""

HYDE_TEMPLATE = """针对下面的问题，写一段 150-250 字的、像知识库文档一样的假设性答案。
不必保证事实准确，目的是让措辞和结构接近真实文档，用于向量检索。
只输出这段答案正文。

问题：{question}"""


def _history_text(history: list[dict], limit: int = 6) -> str:
    """把最近几轮对话压成文本，供改写提示用。"""
    if not history:
        return "（无）"
    recent = history[-limit:]
    lines = []
    for item in recent:
        role = "用户" if item.get("role") == "user" else "助手"
        content = str(item.get("content", "")).strip()
        if content:
            lines.append(f"{role}：{content[:300]}")
    return "\n".join(lines) if lines else "（无）"


async def rewrite_node(state: RagState, *, chat_model=None) -> RagState:
    """按 recipe 决定是否改写查询；改写失败一律退回原问题。"""
    question = state["question"]
    recipe = state.get("recipe", "off")

    if recipe == "off" or chat_model is None or not state.get("history"):
        # 单轮对话或关闭改写：原样检索
        if recipe == "hyde" and chat_model is not None:
            return await _hyde(state, chat_model)
        return {"rewritten": question}

    try:
        prompt = REWRITE_TEMPLATE.format(
            history=_history_text(state.get("history", [])), question=question
        )
        response = await chat_model.ainvoke(prompt)
        content = response.content if isinstance(response.content, str) else str(response.content)
        rewritten = (content or "").strip().strip('"').strip()
        # 模型偶尔会啰嗦，太长就当失败
        if not rewritten or len(rewritten) > max(200, len(question) * 4):
            raise ValueError("改写结果异常")
        logger.info("查询改写：%s → %s", question[:40], rewritten[:40])
        return {"rewritten": rewritten}
    except Exception as exc:
        logger.warning("查询改写失败，回退原问题：%s", exc)
        return {"rewritten": question}


async def _hyde(state: RagState, chat_model) -> RagState:
    """HyDE：先生成假设答案，再用它去检索。"""
    question = state["question"]
    try:
        response = await chat_model.ainvoke(HYDE_TEMPLATE.format(question=question))
        content = response.content if isinstance(response.content, str) else str(response.content)
        hypothetical = (content or "").strip()
        if not hypothetical:
            raise ValueError("HyDE 返回空")
        logger.info("HyDE 已生成假设答案（%s 字）", len(hypothetical))
        return {"rewritten": hypothetical}
    except Exception as exc:
        logger.warning("HyDE 失败，回退原问题：%s", exc)
        return {"rewritten": question}


async def retrieve_node(
    state: RagState,
    *,
    session,
    embed_spec: ProviderSpec,
    rerank_spec: ProviderSpec | None = None,
) -> RagState:
    """检索节点：算查询向量 → 向量+BM25 → RRF → rerank。"""
    query = state.get("rewritten") or state["question"]
    options: RetrievalOptions = state.get("options") or RetrievalOptions()
    expanded = bool(state.get("expanded", False))

    query_vector = (await embed_texts(embed_spec, [query]))[0]

    hits, debug = await retrieve(
        session,
        kb_id=state["kb_id"],
        query=query,
        query_vector=query_vector,
        embed_spec=embed_spec,
        rerank_spec=rerank_spec,
        options=options,
        expand=expanded,
    )
    return {"query_vector": query_vector, "hits": hits, "debug": debug}


def grade_node(state: RagState) -> RagState:
    """打分节点：判断检索质量是否足够。

    判定依据用 **向量相似度**（0~1 有绝对意义），
    而不是 rerank 分或 RRF 分（两者都是相对值，无法定阈值）。
    """
    options: RetrievalOptions = state.get("options") or RetrievalOptions()
    hits = state.get("hits") or []
    if not hits:
        return {"low_score": True}

    best_vector = max((hit.score_vector for hit in hits), default=0.0)
    low = best_vector < options.score_threshold
    logger.info(
        "grade：最佳向量相似度 %.3f，阈值 %.2f → %s",
        best_vector,
        options.score_threshold,
        "不足" if low else "充分",
    )
    return {"low_score": low}


def route_after_grade(state: RagState) -> str:
    """条件边：分数不足且还没重试过 → 扩召回；否则直接生成。"""
    options: RetrievalOptions = state.get("options") or RetrievalOptions()
    if state.get("low_score") and options.expand_retry and not state.get("expanded"):
        return "fallback"
    return "generate"


def fallback_node(state: RagState) -> RagState:
    """扩召回重试（最多一次，由 route 保证）。"""
    return {"expanded": True}


SYSTEM_FALLBACK = """你是用户的私人知识库助手。请严格依据【参考资料】回答问题。

规则：
1. 只能使用参考资料中的信息，不要编造，也不要用参考资料以外的常识硬凑答案。
2. 每个结论后面用 [编号] 标注来源，例如：报销需提交发票 [1][3]。
3. 如果参考资料不足以回答，就直接说「资料中没有找到相关内容」，并说明缺什么。
4. 用中文回答，先给结论再给依据，不要大段复述原文。
5. 不要描述你的检索过程，直接给答案。"""


def build_context(hits: list) -> str:
    """把命中片段拼成带编号的参考资料。"""
    blocks = []
    for index, hit in enumerate(hits, start=1):
        heading = f"[{index}] 《{hit.doc_name or '未知文档'}》第 {hit.page_no} 段"
        # parent_child 策略下优先给父块（上下文更完整）
        body = hit.parent_content.strip() if hit.parent_content.strip() else hit.content
        blocks.append(f"{heading}\n{body}")
    return "\n\n".join(blocks)


def build_messages(state: RagState, *, system_prompt: str | None = None) -> list[dict]:
    """组装送给模型的消息列表。"""
    hits = state.get("hits") or []
    context = build_context(hits)
    system = system_prompt or SYSTEM_FALLBACK

    messages: list[dict] = [{"role": "system", "content": system}]
    for item in state.get("history", [])[-6:]:
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    if hits:
        user = f"【参考资料】\n{context}\n\n【问题】\n{state['question']}"
    else:
        user = (
            "【参考资料】\n（没有检索到任何相关内容）\n\n"
            f"【问题】\n{state['question']}\n\n"
            "请直接告诉用户资料里没有相关内容，不要编造。"
        )
    messages.append({"role": "user", "content": user})
    return messages


def extract_citations(answer: str, hits: list) -> list[dict]:
    """从回答里提取 [n] 并映射回分块，供前端点击溯源。"""
    import re

    numbers = {int(match) for match in re.findall(r"\[(\d{1,2})\]", answer)}
    citations: list[dict] = []
    for number in sorted(numbers):
        if 1 <= number <= len(hits):
            hit = hits[number - 1]
            citations.append(
                {
                    "n": number,
                    "chunk_id": hit.chunk_id,
                    "document_id": hit.doc_id,
                    "doc_name": hit.doc_name,
                    "page_no": hit.page_no,
                    "snippet": hit.content[:300],
                    "score": round(hit.score, 4),
                }
            )
    return citations


def debug_to_dict(debug) -> dict:
    """检索调试信息转 dict，落库用。"""
    return {
        "query": debug.query,
        "used_rerank": debug.used_rerank,
        "rerank_error": debug.rerank_error,
        "expanded": debug.expanded,
        "top_score": debug.top_score,
        "vector": debug.vector,
        "bm25": debug.bm25,
        "fused": debug.fused,
        "reranked": debug.reranked,
    }


def dumps(data) -> str:
    """统一 JSON 序列化（中文不转义，便于人看）。"""
    return json.dumps(data, ensure_ascii=False)
