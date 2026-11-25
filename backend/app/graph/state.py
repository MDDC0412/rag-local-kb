"""LangGraph 状态定义。

为什么用图而不是一条链
----------------------
grade 这个条件边 + 回环才是 LangGraph 的正当理由：
检索质量不够时自动放大召回重试一次。纯线性链路用 LCEL 就够，
套图属于为框架而框架。
"""

from __future__ import annotations

from typing import TypedDict

from app.services.retrieval import Hit, RetrievalDebug, RetrievalOptions


def _keep_last(_old, new):
    """后者覆盖前者的 reducer。"""
    return new


class RagState(TypedDict, total=False):
    """对话图的状态。

    刻意只放"一次问答需要的东西"，会话历史由 SQLite 负责，
    不把 LangGraph 的 message 对象当真相源（否则会和数据库两份数据打架）。
    """

    # 输入
    question: str  # 用户原始问题
    history: list[dict]  # [{"role": "user"/"assistant", "content": "..."}]
    kb_id: int
    options: RetrievalOptions
    recipe: str  # 改写策略：off / rewrite / hyde

    # 中间产物
    rewritten: str  # 改写后的查询（未改写时等于 question）
    query_vector: list[float]
    hits: list[Hit]
    debug: RetrievalDebug
    expanded: bool  # 是否已经扩召回重试过
    low_score: bool  # 检索质量是否不足

    # 输出
    answer: str
    usage: dict
    error: str
