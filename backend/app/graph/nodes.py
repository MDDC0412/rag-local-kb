"""图的节点实现。每个节点单一职责，不写数据库。

节点划分见 docs/DESIGN.md §3.5：
    rewrite → recall → fuse → rerank → grade →(fallback | answer)
    → generate（流式，在 API 层消费）

每个节点都往 ``steps`` 里追加一条轨迹，前端右侧面板据此显示"进行到哪一步"。
"""

from __future__ import annotations

import json
import logging
import time

from app.graph.state import RagState
from app.providers.base import ProviderSpec, embed_texts
from app.providers.llm import invoke_prompt
from app.services.retrieval import (
    RetrievalOptions,
    apply_rerank,
    fuse,
    recall,
)

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


def step(name: str, label: str, status: str, detail: str = "", elapsed_ms: int = 0) -> dict:
    """构造一条轨迹记录。"""
    return {
        "name": name,
        "label": label,
        "status": status,  # running / done / skipped / failed
        "detail": detail,
        "elapsed_ms": elapsed_ms,
    }


def _append_step(state: RagState, record: dict) -> list[dict]:
    """把新轨迹追加到已有轨迹后面。

    注意：state["steps"] 是**累积**的（reducer 把各节点的追加合并在一起），
    所以返回时必须给出完整列表，LangGraph 的 reducer 只做"old + new"，
    如果这里只返回一条，会把之前的轨迹丢掉。

    真正用于 SSE 的"本节点新增了哪条"，见 ``step_of``。
    """
    steps = list(state.get("steps") or [])
    steps.append(record)
    return steps


def step_of(record: dict) -> dict:
    """提取"这一条"轨迹，供 SSE 推送时精确去重。

    SSE 推送需要知道"本节点新增了什么"，而 payload 里的 steps 是累积列表，
    两者混用会导致重复推送。所以节点在 state 里另外存一份
    ``last_step``，流式层只认它。
    """
    return record


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
    started = time.perf_counter()
    question = state["question"]
    recipe = state.get("recipe", "off")
    history = state.get("history") or []

    if recipe == "off" or chat_model is None:
        steps = _append_step(
            state,
            step("rewrite", "查询改写", "skipped", "已关闭，直接用原问题检索"),
        )
        return {"rewritten": question, "steps": steps, "last_step": steps[-1] if steps else None}

    if recipe == "hyde":
        try:
            content = await invoke_prompt(chat_model, HYDE_TEMPLATE.format(question=question))
            hypothetical = (content or "").strip()
            if not hypothetical:
                raise ValueError("HyDE 返回空")
            elapsed = int((time.perf_counter() - started) * 1000)
            steps = _append_step(
                state,
                step(
                    "rewrite",
                    "HyDE 假设答案",
                    "done",
                    f"已生成 {len(hypothetical)} 字假设答案，用它检索",
                    elapsed,
                ),
            )
            return {
                "rewritten": hypothetical,
                "steps": steps,
                "last_step": steps[-1] if steps else None,
            }
        except Exception as exc:
            logger.warning("HyDE 失败，回退原问题：%s", exc)
            steps = _append_step(
                state,
                step("rewrite", "HyDE 假设答案", "failed", f"失败，已回退原问题：{exc}"),
            )
            return {
                "rewritten": question,
                "steps": steps,
                "last_step": steps[-1] if steps else None,
            }

    if not history:
        steps = _append_step(
            state,
            step("rewrite", "查询改写", "skipped", "首轮对话无需改写"),
        )
        return {"rewritten": question, "steps": steps, "last_step": steps[-1] if steps else None}

    try:
        prompt = REWRITE_TEMPLATE.format(history=_history_text(history), question=question)
        content = await invoke_prompt(chat_model, prompt)
        rewritten = (content or "").strip().strip('"').strip()
        if not rewritten or len(rewritten) > max(200, len(question) * 4):
            raise ValueError("改写结果异常")
        elapsed = int((time.perf_counter() - started) * 1000)
        steps = _append_step(
            state,
            step("rewrite", "追问改写", "done", f"已改写为「{rewritten[:40]}」", elapsed),
        )
        logger.info("查询改写：%s → %s", question[:40], rewritten[:40])
        return {"rewritten": rewritten, "steps": steps, "last_step": steps[-1] if steps else None}
    except Exception as exc:
        logger.warning("查询改写失败，回退原问题：%s", exc)
        steps = _append_step(
            state,
            step("rewrite", "追问改写", "failed", f"失败，已回退原问题：{exc}"),
        )
        return {"rewritten": question, "steps": steps, "last_step": steps[-1] if steps else None}


async def embed_node(state: RagState, *, embed_spec: ProviderSpec) -> RagState:
    """把查询文本向量化。

    单独成一个节点而不是塞进召回里：向量化本身要出网，
    是最容易慢/失败的一步，轨迹里必须能单独看到它的耗时。
    """
    started = time.perf_counter()
    query = state.get("rewritten") or state["question"]
    try:
        vector = (await embed_texts(embed_spec, [query]))[0]
        elapsed = int((time.perf_counter() - started) * 1000)
        steps = _append_step(
            state,
            step(
                "embed",
                "查询向量化",
                "done",
                f"{len(vector)} 维 · {embed_spec.model}",
                elapsed,
            ),
        )
        return {"query_vector": vector, "steps": steps, "last_step": steps[-1] if steps else None}
    except Exception as exc:
        steps = _append_step(state, step("embed", "查询向量化", "failed", f"失败：{exc}"))
        raise


async def recall_node(
    state: RagState,
    *,
    session,
) -> RagState:
    """召回节点：向量检索 + BM25 检索。"""
    started = time.perf_counter()
    query = state.get("rewritten") or state["question"]
    options: RetrievalOptions = state.get("options") or RetrievalOptions()
    factor = 2 if state.get("expanded") else 1

    vector_hits, bm25_hits = await recall(
        session,
        kb_id=state["kb_id"],
        query=query,
        query_vector=state["query_vector"],
        options=options,
        factor=factor,
    )
    elapsed = int((time.perf_counter() - started) * 1000)
    steps = _append_step(
        state,
        step(
            "recall",
            "向量 + BM25 召回",
            "done",
            f"向量 {len(vector_hits)} 条 · BM25 {len(bm25_hits)} 条"
            + ("（已扩召回×2）" if state.get("expanded") else ""),
            elapsed,
        ),
    )
    return {
        "vector_hits": vector_hits,
        "bm25_hits": bm25_hits,
        "steps": steps,
        "last_step": steps[-1] if steps else None,
    }


async def fuse_node(state: RagState, *, session) -> RagState:
    """融合节点：RRF 合并两路排名。"""
    started = time.perf_counter()
    options: RetrievalOptions = state.get("options") or RetrievalOptions()
    factor = 2 if state.get("expanded") else 1
    query = state.get("rewritten") or state["question"]

    hits, debug = await fuse(
        session,
        vector_hits=state.get("vector_hits") or [],
        bm25_hits=state.get("bm25_hits") or [],
        options=options,
        factor=factor,
        query=query,
    )
    elapsed = int((time.perf_counter() - started) * 1000)
    steps = _append_step(
        state,
        step(
            "fuse",
            "RRF 融合",
            "done",
            f"融合后 {len(hits)} 条 · k={options.rrf_k}",
            elapsed,
        ),
    )
    return {"hits": hits, "debug": debug, "steps": steps, "last_step": steps[-1] if steps else None}


async def rerank_node(
    state: RagState,
    *,
    rerank_spec=None,
) -> RagState:
    """精排节点：rerank 重排，失败降级。"""
    started = time.perf_counter()
    options: RetrievalOptions = state.get("options") or RetrievalOptions()
    query = state.get("rewritten") or state["question"]
    debug = state.get("debug")
    factor = 2 if state.get("expanded") else 1

    if rerank_spec is None or not options.use_rerank:
        hits = sorted(state.get("hits") or [], key=lambda hit: hit.score_fused, reverse=True)[
            : options.rerank_top_n
        ]
        # 没走精排时 top_score 用融合分，别让「检索详情」显示一个没意义的 0
        if debug is not None:
            debug.top_score = round(hits[0].score_fused, 6) if hits else 0.0
        steps = _append_step(
            state,
            step("rerank", "精排", "skipped", "未配置重排模型或已关闭，按融合分排序"),
        )
        return {
            "hits": hits,
            "debug": debug,
            "steps": steps,
            "last_step": steps[-1] if steps else None,
        }

    hits, debug = await apply_rerank(
        state.get("hits") or [],
        query=query,
        rerank_spec=rerank_spec,
        top_n=options.rerank_top_n,
        debug=debug,  # type: ignore[arg-type]
        factor=factor,
    )
    # top_score 只在 retrieve() 里被赋值过，而图不经过它 —— 不在这里补就是 0.0
    if debug is not None:
        debug.top_score = round(hits[0].score, 4) if hits else 0.0
    elapsed = int((time.perf_counter() - started) * 1000)
    status = "done" if debug and debug.used_rerank else "failed"
    detail = (
        f"精排后取前 {len(hits)} 条，最高分 {hits[0].score:.4f}"
        if hits and debug and debug.used_rerank
        else f"重排失败，已降级为融合分排序：{getattr(debug, 'rerank_error', '')[:60]}"
    )
    steps = _append_step(state, step("rerank", "精排", status, detail, elapsed))
    return {"hits": hits, "debug": debug, "steps": steps, "last_step": steps[-1] if steps else None}


def grade_node(state: RagState) -> RagState:
    """打分节点：判断检索质量是否足够。

    判定依据用 **向量相似度**（0~1 有绝对意义），
    而不是 rerank 分或 RRF 分（两者都是相对值，无法定阈值）。
    """
    options: RetrievalOptions = state.get("options") or RetrievalOptions()
    hits = state.get("hits") or []
    threshold = options.score_threshold

    if not hits:
        steps = _append_step(state, step("grade", "质量判定", "failed", "没有检索到任何内容"))
        return {"low_score": True, "steps": steps, "last_step": steps[-1] if steps else None}

    best_vector = max((hit.score_vector for hit in hits), default=0.0)
    # rerank 分如果可用，也用来看一眼（仅展示，判定仍以向量相似度为准）
    best_rerank = max((hit.score for hit in hits), default=0.0)
    low = best_vector < threshold

    detail = f"最佳向量相似度 {best_vector:.3f}，阈值 {threshold:.2f} → " + (
        "分数不足" if low else "分数足够"
    )
    if state.get("debug") and state["debug"].used_rerank:  # type: ignore[union-attr]
        detail += f"；精排最高 {best_rerank:.3f}"

    steps = _append_step(state, step("grade", "质量判定", "done", detail))
    logger.info("grade：%s", detail)
    return {"low_score": low, "steps": steps, "last_step": steps[-1] if steps else None}


def route_after_grade(state: RagState) -> str:
    """条件边：分数不足且还没重试过 → 扩召回；否则直接生成。

    额外保护：如果这次召回**一条都没拿到**，扩召回也没用
    （通常意味着向量库是空的，比如文档没入库或换了 embedding 模型），
    直接去生成"没找到"的回答，避免无意义地重跑一遍链路。
    """
    options: RetrievalOptions = state.get("options") or RetrievalOptions()
    if not state.get("hits"):
        return "answer"
    if state.get("low_score") and options.expand_retry and not state.get("expanded"):
        return "fallback"
    return "answer"


def fallback_node(state: RagState) -> RagState:
    """扩召回重试（最多一次，由 route 保证）。"""
    steps = _append_step(
        state,
        step("fallback", "扩召回重试", "done", "分数不足，召回数量翻倍再检索一次"),
    )
    return {"expanded": True, "steps": steps, "last_step": steps[-1] if steps else None}


def build_context(hits: list) -> str:
    """把命中片段拼成带编号的参考资料。

    parent_child 策略下优先给父块（上下文更完整），否则给命中块本身。
    """
    blocks = []
    for index, hit in enumerate(hits, start=1):
        heading = f"[{index}] 《{hit.doc_name or '未知文档'}》第 {hit.page_no} 段"
        body = hit.parent_content.strip() if hit.parent_content.strip() else hit.content
        blocks.append(f"{heading}\n{body}")
    return "\n\n".join(blocks)


def answer_node(state: RagState) -> RagState:
    """答案组织节点：拼好上下文，真正的流式生成在 API 层完成。"""
    hits = state.get("hits") or []
    context = build_context(hits)
    steps = _append_step(
        state,
        step(
            "answer",
            "组织答案",
            "done",
            f"把 {len(hits)} 段资料（{len(context)} 字）交给模型",
        ),
    )
    return {"steps": steps, "last_step": steps[-1] if steps else None, "context": context}


SYSTEM_FALLBACK = """你是用户的私人知识库助手。请严格依据【参考资料】回答问题。

规则：
1. 事实必须来自【参考资料】，不要编造，也不要用参考资料以外的常识硬凑答案。
2. 每个结论后面用 [编号] 标注来源，例如：报销需提交发票 [1][3]。
3. 如果参考资料不足以回答，就直接说「资料中没有找到相关内容」，并说明缺什么。
4. 用中文回答，先给结论再给依据，不要大段复述原文。
5. 不要描述你的检索过程，直接给答案。
6. 需要做算术时必须调用 calculator 工具，不要自己心算 —— 小数和多位乘除容易算错。
   用工具算出的是「对资料里数字的计算」，不是新的事实：引用仍标在原来的资料编号上，
   并说明这一步是算出来的（例如：600 × 1.3 = 780 元 [1]）。
7. 用户问当前日期 / 时间 / 「今天」「今年」时，用系统给出的当前时间回答，
   不要说不知道，也不要凭训练数据猜。
8. 纯计算或纯时间问题（与资料无关）可以正常回答，但要说明「这不在参考资料范围内」。"""


def build_messages(state: RagState, *, system_prompt: str | None = None) -> list[dict]:
    """组装送给模型的消息列表。"""
    hits = state.get("hits") or []
    context = state.get("context") or ""
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
