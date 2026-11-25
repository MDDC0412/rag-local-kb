"""对话业务：会话持久化 + 流式问答 + 引用溯源 + 过程轨迹。

关键取舍
--------
- **会话存 SQLite，不用 LangGraph checkpointer**：checkpointer 会把消息存成它自己的
  格式，和我们的 messages 表变成两个真相源，最终出现"前端显示的和模型看到的不一致"
  这种极难查的 bug。历史以 SQLite 为准，每次通过 history 字段传进图。
- 图里不写库；落库统一在这里做，方便测试与重放。
- 用 ``graph.astream(stream_mode="updates")`` 逐节点消费，每个节点完成就推一条
  ``step`` 事件 —— 前端右侧的轨迹面板据此显示"进行到哪一步"。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NOT_FOUND, AppError
from app.db.models import Conversation, Message, PromptTemplate
from app.db.session import SessionLocal
from app.graph.build import build_graph
from app.graph.nodes import build_messages, debug_to_dict, dumps, extract_citations
from app.graph.state import RagState
from app.providers.llm import build_chat_model
from app.schemas.chat import ChatOptions

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "新会话"


# --------------------------------------------------------------------------- #
# 会话 CRUD
# --------------------------------------------------------------------------- #
async def create_conversation(
    session: AsyncSession, *, kb_id: int = 1, title: str = ""
) -> Conversation:
    """新建会话。"""
    row = Conversation(kb_id=kb_id, title=title.strip() or DEFAULT_TITLE)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def list_conversations(session: AsyncSession, kb_id: int = 1) -> list[Conversation]:
    """列出会话，最近更新的在前。"""
    rows = (
        (
            await session.execute(
                select(Conversation)
                .where(Conversation.kb_id == kb_id)
                .order_by(Conversation.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def get_conversation(session: AsyncSession, conversation_id: int) -> Conversation:
    """取会话，不存在报错。"""
    row = await session.get(Conversation, conversation_id)
    if row is None:
        raise AppError(NOT_FOUND, f"会话 #{conversation_id} 不存在", status_code=404)
    return row


async def get_messages(session: AsyncSession, conversation_id: int) -> list[Message]:
    """按时间正序列出消息。"""
    rows = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.id)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def rename_conversation(
    session: AsyncSession, conversation_id: int, title: str
) -> Conversation:
    """重命名会话。"""
    row = await get_conversation(session, conversation_id)
    row.title = title.strip() or DEFAULT_TITLE
    await session.commit()
    await session.refresh(row)
    return row


async def delete_conversation(session: AsyncSession, conversation_id: int) -> None:
    """删除会话（消息级联删除）。"""
    row = await get_conversation(session, conversation_id)
    await session.delete(row)
    await session.commit()


async def _touch(session: AsyncSession, conversation: Conversation, question: str) -> None:
    """更新会话时间；若是首条消息，用问题当标题。"""
    from datetime import datetime

    if conversation.title == DEFAULT_TITLE:
        conversation.title = question[:24] + ("…" if len(question) > 24 else "")
    conversation.updated_at = datetime.now(UTC)
    await session.commit()


async def _system_prompt(session: AsyncSession) -> str | None:
    """取默认系统提示词模板（用户可在提示词库改）。"""
    row = (
        await session.execute(
            select(PromptTemplate)
            .where(PromptTemplate.kind == "system")
            .order_by(PromptTemplate.is_default.desc(), PromptTemplate.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row.content if row and row.content.strip() else None


# --------------------------------------------------------------------------- #
# 流式问答
# --------------------------------------------------------------------------- #
def _sse(event: str, data: dict) -> str:
    """拼一条 SSE 消息。"""
    return f"event: {event}\ndata: {dumps(data)}\n\n"


async def stream_chat(
    *,
    conversation_id: int,
    question: str,
    options: ChatOptions,
) -> AsyncIterator[str]:
    """流式问答。产出已格式化好的 SSE 文本块。

    事件顺序：
        started → step* → retrieval → step(生成中) → delta* → citations → done

    ``step`` 是过程轨迹：rewrite / embed / recall / fuse / rerank / grade /
    fallback / answer / generate，前端右侧面板逐条点亮。
    """
    started = time.perf_counter()
    # 独立 session：SSE 生成器活得比请求依赖更长，不能复用请求级 session
    async with SessionLocal() as session:
        try:
            async for chunk in _stream_impl(
                session=session,
                conversation_id=conversation_id,
                question=question,
                options=options,
                started=started,
            ):
                yield chunk
        except AppError as exc:
            yield _sse("error", {"code": exc.code, "message": exc.message})
        except Exception as exc:
            logger.exception("流式问答异常")
            yield _sse(
                "error",
                {"code": "INTERNAL_ERROR", "message": f"服务内部错误：{type(exc).__name__}"},
            )


async def _stream_impl(
    *,
    session: AsyncSession,
    conversation_id: int,
    question: str,
    options: ChatOptions,
    started: float,
) -> AsyncIterator[str]:
    """真正的实现，异常向外抛由外层转成 error 事件。"""
    conversation = await get_conversation(session, conversation_id)

    # 1) 取历史（落库前先取出，避免把本轮问题算进历史）
    history = [
        {"role": msg.role, "content": msg.content}
        for msg in await get_messages(session, conversation_id)
        if msg.role in ("user", "assistant") and msg.content.strip()
    ]

    # 2) 落库用户消息
    session.add(Message(conversation_id=conversation_id, role="user", content=question))
    await session.commit()

    # 3) 解析模型：优先用会话里选定的，没选才跟随默认
    from app.services.model_choice import ModelChoice, resolve_models

    choice = ModelChoice(
        llm_provider_id=conversation.llm_provider_id,
        embedding_provider_id=conversation.embedding_provider_id,
        rerank_provider_id=conversation.rerank_provider_id,
        use_rerank=conversation.use_rerank,
    )
    llm_spec, embed_spec, rerank_spec = await resolve_models(session, choice)

    rerank_note = ""
    if options.use_rerank and rerank_spec is None:
        rerank_note = "没有可用的重排模型，本次跳过精排"
    elif not conversation.use_rerank:
        rerank_note = "会话已关闭重排"

    # 4) 跑图，逐节点推送轨迹
    from app.services.retrieval import RetrievalOptions

    retrieval_options = RetrievalOptions(
        vector_top_n=options.vector_top_n,
        bm25_top_n=options.bm25_top_n,
        rerank_top_n=options.rerank_top_n,
        rrf_k=options.rrf_k,
        use_rerank=options.use_rerank,
        score_threshold=options.score_threshold,
        expand_retry=options.expand_retry,
    )
    state: RagState = {
        "question": question,
        "history": history,
        "kb_id": conversation.kb_id,
        "options": retrieval_options,
        "recipe": options.rewrite,
        "expanded": False,
        "steps": [],
    }

    graph = build_graph(
        session=session,
        chat_model=build_chat_model(llm_spec, streaming=False)
        if options.rewrite != "off"
        else None,
        embed_spec=embed_spec,
        rerank_spec=rerank_spec if options.use_rerank else None,
    )

    yield _sse(
        "started",
        {
            "question": question,
            "rewrite": options.rewrite,
            "use_rerank": rerank_spec is not None,
            "note": rerank_note,
            "models": {
                "llm": {"name": llm_spec.name, "model": llm_spec.model},
                "embedding": {"name": embed_spec.name, "model": embed_spec.model},
                "rerank": (
                    {"name": rerank_spec.name, "model": rerank_spec.model}
                    if rerank_spec is not None
                    else None
                ),
            },
        },
    )

    final_state: dict = dict(state)
    all_steps: list[dict] = []
    async for update in graph.astream(state, stream_mode="updates"):
        # update 形如 {"recall": {"vector_hits": ..., "steps": [累积列表], "last_step": {...}}}
        # steps 是累积的（reducer 合并），last_step 才是"本节点新增的那一条"。
        # 用 last_step 推送可以精确去重，避免重复发同一条轨迹。
        for node_name, payload in update.items():
            if not isinstance(payload, dict):
                continue
            record = payload.get("last_step")
            if record:
                all_steps.append(record)
                yield _sse("step", record)
            for key, value in payload.items():
                if key not in ("steps", "last_step"):
                    final_state[key] = value
            logger.debug("节点 %s 完成", node_name)

    hits = final_state.get("hits") or []
    debug = final_state.get("debug")
    rewritten = final_state.get("rewritten") or question

    # 5) 发检索结果，前端可以先把"参考了哪几段"显示出来
    yield _sse(
        "retrieval",
        {
            "chunks": [
                {
                    "n": index,
                    "chunk_id": hit.chunk_id,
                    "doc_name": hit.doc_name,
                    "page_no": hit.page_no,
                    "snippet": hit.content[:200],
                    "score": round(hit.score, 4),
                }
                for index, hit in enumerate(hits, start=1)
            ],
            "expanded": bool(final_state.get("expanded")),
            "low_score": bool(final_state.get("low_score")),
            "rewritten": rewritten if rewritten != question else "",
        },
    )

    # 6) 流式生成
    system_prompt = await _system_prompt(session)
    messages = build_messages(final_state, system_prompt=system_prompt)
    generate_step = {
        "name": "generate",
        "label": "模型生成",
        "status": "running",
        "detail": f"已提交 {len(messages)} 条消息",
        "elapsed_ms": 0,
    }
    yield _sse("step", generate_step)

    stream_model = build_chat_model(llm_spec, streaming=True)
    answer_parts: list[str] = []
    usage: dict = {}
    gen_started = time.perf_counter()
    first_token_ms = 0

    try:
        async for event in stream_model.astream(messages):
            text = event.content if isinstance(event.content, str) else str(event.content or "")
            if text:
                if not answer_parts:
                    first_token_ms = int((time.perf_counter() - gen_started) * 1000)
                answer_parts.append(text)
                yield _sse("delta", {"text": text})
            meta = getattr(event, "usage_metadata", None)
            if meta:
                usage = dict(meta)
    except Exception as exc:
        logger.exception("生成失败")
        raise AppError(
            "GENERATION_FAILED",
            f"模型生成失败：{type(exc).__name__}，请检查对话模型是否可用",
        ) from exc

    answer = "".join(answer_parts)
    gen_elapsed = int((time.perf_counter() - gen_started) * 1000)
    generate_step = {
        "name": "generate",
        "label": "模型生成",
        "status": "done",
        "detail": f"首字 {first_token_ms}ms · 共 {len(answer)} 字",
        "elapsed_ms": gen_elapsed,
    }
    # 这一步在图之外（要推 SSE），也必须并入 all_steps，
    # 否则重新打开历史会话时「模型生成」永远是个灰点 —— 它压根没存进库
    all_steps.append(generate_step)
    yield _sse("step", generate_step)

    citations = extract_citations(answer, hits)
    latency = int((time.perf_counter() - started) * 1000)

    # 7) 落库助手消息（含引用、检索详情、过程轨迹）
    steps = all_steps
    assistant = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=answer,
        rewritten_query=rewritten if rewritten != question else "",
        citations_json=dumps(citations),
        retrieval_json=dumps(
            {
                **(debug_to_dict(debug) if debug else {}),
                "steps": steps,
                "latency_ms": latency,
            }
        ),
        token_usage=int(usage.get("total_tokens", 0) or 0),
        latency_ms=latency,
    )
    session.add(assistant)
    await _touch(session, conversation, question)
    await session.commit()
    await session.refresh(assistant)

    yield _sse("citations", {"items": citations})
    yield _sse(
        "done",
        {
            "message_id": assistant.id,
            "usage": usage,
            "latency_ms": latency,
            "answer": answer,
            "steps": steps,
        },
    )
