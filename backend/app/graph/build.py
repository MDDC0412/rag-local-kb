"""构建对话图。

    START → rewrite → retrieve → grade ─┬─(分数不足且未重试)→ fallback → retrieve
                                       └─(分数够 / 已重试)→ generate → END

图本身只做"决策与编排"，真正的生成（流式）由 API 层用 astream 消费，
因为 FastAPI 需要拿到 token 级事件往 SSE 里推。
"""

from __future__ import annotations

import logging
from functools import partial

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    fallback_node,
    grade_node,
    retrieve_node,
    rewrite_node,
    route_after_grade,
)
from app.graph.state import RagState

logger = logging.getLogger(__name__)

GENERATE = "generate"


def _generate_placeholder(state: RagState) -> RagState:
    """占位节点：真正的生成在 API 层流式完成，这里只标记进入生成阶段。"""
    return {}


def build_graph(
    *,
    session,
    chat_model=None,
    embed_spec,
    rerank_spec=None,
):
    """按当前会话的供应商构建图（每次请求构建一次，成本可忽略）。"""
    graph = StateGraph(RagState)

    graph.add_node("rewrite", partial(rewrite_node, chat_model=chat_model))
    graph.add_node(
        "retrieve",
        partial(retrieve_node, session=session, embed_spec=embed_spec, rerank_spec=rerank_spec),
    )
    graph.add_node("grade", grade_node)
    graph.add_node("fallback", fallback_node)
    graph.add_node(GENERATE, _generate_placeholder)

    graph.add_edge(START, "rewrite")
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("retrieve", "grade")
    graph.add_conditional_edges(
        "grade",
        route_after_grade,
        {"fallback": "fallback", "generate": GENERATE},
    )
    # 扩召回之后再检索一次，这次 expanded=True，条件边不会再回到 fallback
    graph.add_edge("fallback", "retrieve")
    graph.add_edge(GENERATE, END)

    return graph.compile()
