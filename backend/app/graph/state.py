"""LangGraph 状态定义。

为什么用图而不是一条链
----------------------
grade 这个条件边 + 回环才是 LangGraph 的正当理由：
检索质量不够时自动放大召回重试一次。纯线性链路用 LCEL 就够，
套图属于为框架而框架。

为什么把召回/融合/精排拆成独立节点
--------------------------------
除了条件边，还为了**过程可见**：每个节点产出一条轨迹，
前端右侧面板据此显示"正在召回 → 正在融合 → 正在精排 → 正在组织答案"。
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from app.services.retrieval import Hit, RetrievalDebug, RetrievalOptions


def _keep_last(_old, new):
    """后者覆盖前者的 reducer。"""
    return new


def _extend(old: list, new: list) -> list:
    """列表追加的 reducer：多节点各自往 steps 里写，不互相覆盖。"""
    return (old or []) + (new or [])


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
    vector_hits: list[tuple[int, float]]
    bm25_hits: list[tuple[int, float]]
    hits: list[Hit]
    context: str
    debug: RetrievalDebug
    expanded: bool  # 是否已经扩召回重试过
    low_score: bool  # 检索质量是否不足

    # 过程轨迹（每个节点追加一条，前端右侧面板展示）
    steps: Annotated[list[dict], _extend]
    # 本节点新增的那一条轨迹（不参与 reducer，用于 SSE 精确推送、避免重复）
    last_step: dict | None

    # 输出
    answer: str
    usage: dict
    error: str
