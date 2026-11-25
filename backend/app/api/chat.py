"""对话接口：会话 CRUD、SSE 流式问答、检索详情、提问优化。"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NOT_FOUND, AppError
from app.db.models import Message
from app.db.session import get_session
from app.schemas.chat import (
    ChatStreamIn,
    ConversationDetail,
    ConversationOut,
    CreateConversationIn,
    MessageOut,
    OptimizePromptIn,
    OptimizePromptOut,
    RenameConversationIn,
)
from app.services import chat as chat_service
from app.services.promptopt import optimize_question

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    kb_id: int = 1, session: AsyncSession = Depends(get_session)
) -> list[ConversationOut]:
    """列出会话。"""
    rows = await chat_service.list_conversations(session, kb_id)
    return [ConversationOut.model_validate(row) for row in rows]


@router.post("/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(
    payload: CreateConversationIn, session: AsyncSession = Depends(get_session)
) -> ConversationOut:
    """新建会话。"""
    row = await chat_service.create_conversation(session, kb_id=payload.kb_id, title=payload.title)
    return ConversationOut.model_validate(row)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation_detail(
    conversation_id: int, session: AsyncSession = Depends(get_session)
) -> ConversationDetail:
    """会话详情 + 全部消息。"""
    conversation = await chat_service.get_conversation(session, conversation_id)
    messages = await chat_service.get_messages(session, conversation_id)
    return ConversationDetail(
        conversation=ConversationOut.model_validate(conversation),
        messages=[MessageOut.model_validate(msg) for msg in messages],
    )


@router.put("/conversations/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: int,
    payload: RenameConversationIn,
    session: AsyncSession = Depends(get_session),
) -> ConversationOut:
    """重命名会话。"""
    row = await chat_service.rename_conversation(session, conversation_id, payload.title)
    return ConversationOut.model_validate(row)


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    """删除会话。"""
    await chat_service.delete_conversation(session, conversation_id)


@router.post("/chat/stream")
async def chat_stream(payload: ChatStreamIn) -> StreamingResponse:
    """SSE 流式问答。

    注意：这里**不注入请求级 session**，因为流式生成器活得比请求依赖更长，
    复用一个已关闭的 session 会抛 "session is closed"。服务内部自管会话。
    """
    return StreamingResponse(
        chat_service.stream_chat(
            conversation_id=payload.conversation_id,
            question=payload.question,
            options=payload.options,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # 关闭 nginx 缓冲，否则流式会被攒着一次性吐出来
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/messages/{message_id}/retrieval")
async def message_retrieval(message_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """取某条回答当时的检索详情（向量/BM25/融合/精排四段）。"""
    row = await session.get(Message, message_id)
    if row is None:
        raise AppError(NOT_FOUND, f"消息 #{message_id} 不存在", status_code=404)
    try:
        data = json.loads(row.retrieval_json or "{}")
    except json.JSONDecodeError:
        data = {}
    return {"message_id": message_id, "retrieval": data}


@router.post("/chat/optimize-prompt", response_model=OptimizePromptOut)
async def optimize_prompt(
    payload: OptimizePromptIn, session: AsyncSession = Depends(get_session)
) -> OptimizePromptOut:
    """把口语化提问改写清楚（只给建议，不覆盖用户输入）。"""
    optimized, rationale = await optimize_question(
        session, question=payload.question, history=payload.history
    )
    return OptimizePromptOut(optimized=optimized, rationale=rationale)


@router.get("/conversations/{conversation_id}/export")
async def export_conversation(
    conversation_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    """导出会话为 Markdown（含引用出处）。"""
    conversation = await chat_service.get_conversation(session, conversation_id)
    messages = await chat_service.get_messages(session, conversation_id)

    lines = [f"# {conversation.title}", ""]
    for msg in messages:
        if msg.role == "user":
            lines.append(f"**我：** {msg.content}")
        elif msg.role == "assistant":
            lines.append(msg.content)
            try:
                citations = json.loads(msg.citations_json or "[]")
            except json.JSONDecodeError:
                citations = []
            if citations:
                lines.append("")
                lines.append("> 引用来源：")
                for item in citations:
                    lines.append(
                        f"> [{item.get('n')}] 《{item.get('doc_name')}》"
                        f"第 {item.get('page_no')} 段 · 相关度 {item.get('score')}"
                    )
        lines.append("")

    return {
        "title": conversation.title,
        "filename": f"{conversation.title[:20]}.md",
        "markdown": "\n".join(lines),
    }
