"""构建对话图。

    START → rewrite → embed → recall → fuse → rerank → grade ─┬→ fallback → recall
                                                              └→ answer → END

图的职责只到"把上下文准备好"（answer 节点）。
真正的流式生成在 API 层用 astream 消费，因为 FastAPI 需要拿到 token 级事件推 SSE。
"""

from __future__ import annotations

import logging
from functools import partial

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import (
    answer_node,
    embed_node,
    fallback_node,
    fuse_node,
    grade_node,
    recall_node,
    rerank_node,
    rewrite_node,
    route_after_grade,
)
from app.graph.state import RagState

logger = logging.getLogger(__name__)

ANSWER = "answer"


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
    graph.add_node("embed", partial(embed_node, embed_spec=embed_spec))
    graph.add_node("recall", partial(recall_node, session=session))
    graph.add_node("fuse", partial(fuse_node, session=session))
    graph.add_node("rerank", partial(rerank_node, rerank_spec=rerank_spec))
    graph.add_node("grade", grade_node)
    graph.add_node("fallback", fallback_node)
    graph.add_node(ANSWER, answer_node)

    graph.add_edge(START, "rewrite")
    graph.add_edge("rewrite", "embed")
    graph.add_edge("embed", "recall")
    graph.add_edge("recall", "fuse")
    graph.add_edge("fuse", "rerank")
    graph.add_edge("rerank", "grade")
    graph.add_conditional_edges(
        "grade",
        route_after_grade,
        {"fallback": "fallback", "answer": ANSWER},
    )
    # 扩召回只重跑召回之后的链路，不重复向量化和改写（省钱）
    graph.add_edge("fallback", "recall")
    graph.add_edge(ANSWER, END)

    return graph.compile()
