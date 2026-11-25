"""LLM 增强：智能解析（清洗结构）与智能分块。

原则
----
- 都只是「可选加速器」：失败一律回退本地规则，并把原因带回前端，绝不阻断主流程；
- 长文档分段送模型，每段独立容错，一段失败不影响整篇；
- 输出必须过 JSON/结构校验，不信任模型原样返回。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PROVIDER_NOT_FOUND, AppError
from app.db.models import ProviderConfig
from app.providers.base import ProviderSpec
from app.providers.llm import build_chat_model
from app.providers.registry import resolve_spec
from app.services.chunking import ChunkResult, split_llm, split_local
from app.services.parsing import PageText, ParseResult, parse_document

logger = logging.getLogger(__name__)

# 与 prompts/defaults.py 里 kind=parse / kind=chunk 的默认模板对应
PARSE_TEMPLATE_FALLBACK = """把下面的原始文本整理成干净的 Markdown：去掉页眉页脚与页码，
恢复被硬换行切断的段落，标题用 # 表达，表格转成 Markdown 表格，不要增删事实信息。
只输出整理后的正文。

原始文本：
{text}"""

CHUNK_TEMPLATE_FALLBACK = """把下面的文本切成语义完整的块，每块 150-400 字，不要在一句话中间切断，
保留原文措辞。只输出 JSON 数组，元素是字符串：["第一块", "第二块"]。

文本：
{text}"""


async def _get_llm_spec(session: AsyncSession, provider_id: int | None = None) -> ProviderSpec:
    """取对话模型：优先指定，否则默认。"""
    if provider_id is not None:
        row = await session.get(ProviderConfig, provider_id)
        if row is None or row.kind != "llm":
            raise AppError(PROVIDER_NOT_FOUND, f"对话模型 #{provider_id} 不存在", status_code=404)
        from app.providers.registry import to_spec

        return to_spec(row)
    return await resolve_spec(session, "llm")


async def _load_template(session: AsyncSession, kind: str, fallback: str) -> str:
    """从提示词模板库取默认模板，取不到用内置兜底。"""
    from app.db.models import PromptTemplate

    row = (
        await session.execute(
            select(PromptTemplate)
            .where(PromptTemplate.kind == kind)
            .order_by(PromptTemplate.is_default.desc(), PromptTemplate.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row.content if row and row.content.strip() else fallback


async def parse_with_llm(
    session: AsyncSession, document, *, provider_id: int | None = None
) -> ParseResult:
    """LLM 智能解析：本地先抽出文字，再让模型清洗结构。

    注意：LLM 解析不负责"把图片变成文字"（那是 OCR），它只整理已经抽出来的文本。
    """
    path = Path(document.stored_path)
    if not path.exists():
        raise AppError("FILE_MISSING", "原始文件已丢失，请重新上传", status_code=400)

    # 1) 本地解析拿到原始文本（失败则无法继续）
    local = parse_document(path, document.filename)
    if local.char_count == 0:
        raise AppError(
            "NO_TEXT_EXTRACTED",
            "没能从文件里抽出文字（可能是扫描件），智能解析也帮不上忙",
            status_code=400,
        )

    spec = await _get_llm_spec(session, provider_id)
    model = build_chat_model(spec, temperature=0.0, streaming=False)
    template = await _load_template(session, "parse", PARSE_TEMPLATE_FALLBACK)

    result = ParseResult(mode="llm")
    failed = 0

    for page in local.pages:
        if not page.text.strip():
            # 空白页直接保留，避免页码错位
            result.pages.append(page)
            continue
        try:
            prompt = template.format(text=page.text[:6000])
            response = await model.ainvoke(prompt)
            content = (
                response.content if isinstance(response.content, str) else str(response.content)
            )
            cleaned = (content or "").strip()
            if not cleaned:
                raise ValueError("模型返回空内容")
            result.pages.append(PageText(page_no=page.page_no, text=cleaned))
        except Exception as exc:
            failed += 1
            logger.warning("LLM 解析第 %s 页失败：%s", page.page_no, exc)
            result.pages.append(page)

    if failed:
        result.warnings.append(f"有 {failed} 页智能解析失败，已保留原始文本。")
    if result.char_count == 0:
        raise AppError("LLM_PARSE_EMPTY", "智能解析没有产出内容，请改用本地解析", status_code=400)
    return result


async def chunk_with_llm(
    session: AsyncSession, document_id: int, *, chunk_size: int = 512, overlap: int = 0
) -> ChunkResult:
    """LLM 智能分块；整体失败时回退到递归切分而不是报错。"""
    from app.services.documents import load_pages

    pages = await load_pages(session, document_id)
    spec = await _get_llm_spec(session, None)
    model = build_chat_model(spec, temperature=0.0, streaming=False)
    template = await _load_template(session, "chunk", CHUNK_TEMPLATE_FALLBACK)

    try:
        return await split_llm(
            pages,
            chat_model=model,
            prompt_template=template,
            chunk_size=chunk_size,
            overlap=overlap,
        )
    except Exception as exc:
        logger.warning("LLM 分块整体失败，回退递归切分：%s", exc)
        fallback = split_local(
            pages, strategy="recursive", chunk_size=chunk_size, overlap=max(overlap, 64)
        )
        fallback.strategy = "recursive"
        fallback.warnings.append(f"智能切分不可用（{type(exc).__name__}），已自动改用递归切分。")
        return fallback
