"""提示词模板库与全局设置。

两条硬规则
----------
1. **内置模板只读**。用户要改，必须先「另存为副本」，改动落在副本上。
   理由：内置模板是各功能（解析、分块、标签、改写）的兜底，
   被改坏之后整个链路都会退化，而且用户很难意识到是自己改坏的。
2. **每种 kind 只能有一个默认**。设新默认时把旧的降级，否则
   ``order_by(is_default.desc())`` 取到哪条就变成不确定的了。
"""

from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NOT_FOUND, AppError
from app.db.models import PromptTemplate, Setting
from app.schemas.prompt import PROMPT_KIND_LABEL, PromptTemplateCreate, PromptTemplateUpdate

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 提示词模板
# --------------------------------------------------------------------------- #
async def list_templates(session: AsyncSession, *, kind: str | None = None) -> list[PromptTemplate]:
    """列模板，按 kind 与 id 排序（内置的排前面，便于对照）。"""
    stmt = select(PromptTemplate)
    if kind:
        stmt = stmt.where(PromptTemplate.kind == kind)
    stmt = stmt.order_by(PromptTemplate.kind, PromptTemplate.is_builtin.desc(), PromptTemplate.id)
    return list((await session.execute(stmt)).scalars().all())


async def get_template(session: AsyncSession, template_id: int) -> PromptTemplate:
    """取模板，不存在报 404。"""
    row = await session.get(PromptTemplate, template_id)
    if row is None:
        raise AppError(NOT_FOUND, f"提示词模板 #{template_id} 不存在", status_code=404)
    return row


async def _clear_other_defaults(session: AsyncSession, kind: str, keep_id: int | None) -> None:
    """把同 kind 下其它模板的 is_default 清掉。"""
    stmt = update(PromptTemplate).where(PromptTemplate.kind == kind)
    if keep_id is not None:
        stmt = stmt.where(PromptTemplate.id != keep_id)
    await session.execute(stmt.values(is_default=False))


async def create_template(session: AsyncSession, payload: PromptTemplateCreate) -> PromptTemplate:
    """新建模板（「另存为副本」也走这里）。"""
    row = PromptTemplate(
        kind=payload.kind,
        name=payload.name.strip(),
        content=payload.content,
        is_builtin=False,
        is_default=False,
    )
    session.add(row)
    await session.flush()

    if payload.is_default:
        await _clear_other_defaults(session, payload.kind, row.id)
        row.is_default = True

    await session.commit()
    await session.refresh(row)
    logger.info("新建提示词模板 #%s（%s / %s）", row.id, row.kind, row.name)
    return row


async def duplicate_template(session: AsyncSession, template_id: int) -> PromptTemplate:
    """把任意模板复制成可编辑的副本（内置模板改动的唯一入口）。"""
    source = await get_template(session, template_id)
    payload = PromptTemplateCreate(
        kind=source.kind,  # type: ignore[arg-type]
        name=f"{source.name}（副本）"[:64],
        content=source.content,
        is_default=False,
    )
    return await create_template(session, payload)


async def update_template(
    session: AsyncSession, template_id: int, payload: PromptTemplateUpdate
) -> PromptTemplate:
    """改模板。内置模板直接拒绝 —— 必须先另存副本。"""
    row = await get_template(session, template_id)
    if row.is_builtin:
        raise AppError(
            "TEMPLATE_READONLY",
            "内置提示词不能直接修改，请先「另存为副本」再改，以免改坏各功能的兜底逻辑",
            status_code=409,
        )

    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.content is not None:
        row.content = payload.content
    if payload.is_default is not None:
        if payload.is_default:
            await _clear_other_defaults(session, row.kind, row.id)
        row.is_default = payload.is_default

    await session.commit()
    await session.refresh(row)
    logger.info("更新提示词模板 #%s", row.id)
    return row


async def reset_template(session: AsyncSession, template_id: int) -> PromptTemplate:
    """把模板内容恢复成内置原文。

    原文的真相源是**数据库里的内置模板**（同 kind、``is_builtin=1``），
    不是代码里的常量字典 —— 否则用户在提示词库里看到的那条和重置时用的那条
    可能不一致，改了重置也回不去。

    名字不一定完全一致（「另存副本」会自动加「（副本）」后缀），
    所以先按同名找，找不到就退回该 kind 的内置默认模板。
    再找不到才报错，不静默清空。
    """
    row = await get_template(session, template_id)
    original = await _find_builtin_row(session, row.kind, row.name)
    if original is None:
        original = await _find_builtin_default(session, row.kind)
    if original is None:
        raise AppError(
            "TEMPLATE_NO_ORIGINAL",
            f"「{row.name}」没有对应的内置原文，无法重置",
            status_code=400,
        )

    row.content = original.content
    await session.commit()
    await session.refresh(row)
    logger.info("重置提示词模板 #%s 为内置原文（来源 #%s）", row.id, original.id)
    return row


async def _find_builtin_row(session: AsyncSession, kind: str, name: str) -> PromptTemplate | None:
    """在数据库里找同 kind 同名的内置模板。"""
    return (
        await session.execute(
            select(PromptTemplate)
            .where(
                PromptTemplate.kind == kind,
                PromptTemplate.name == name,
                PromptTemplate.is_builtin.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def _find_builtin_default(session: AsyncSession, kind: str) -> PromptTemplate | None:
    """取该 kind 的内置默认模板（副本名字带后缀、对不上原名时的兜底来源）。"""
    return (
        await session.execute(
            select(PromptTemplate)
            .where(PromptTemplate.kind == kind, PromptTemplate.is_builtin.is_(True))
            .order_by(PromptTemplate.is_default.desc(), PromptTemplate.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def delete_template(session: AsyncSession, template_id: int) -> None:
    """删除自定义模板。内置模板不允许删。"""
    row = await get_template(session, template_id)
    if row.is_builtin:
        raise AppError("TEMPLATE_READONLY", "内置提示词不能删除", status_code=409)
    await session.delete(row)
    await session.commit()
    logger.info("删除提示词模板 #%s", template_id)


async def grouped_templates(session: AsyncSession) -> list[dict]:
    """按 kind 分组返回，前端一屏看完所有模板。"""
    rows = await list_templates(session)
    grouped: dict[str, list[PromptTemplate]] = {}
    for row in rows:
        grouped.setdefault(row.kind, []).append(row)

    order = ["system", "optimize", "query_rewrite", "parse", "filter", "tag"]
    result: list[dict] = []
    for kind in order:
        items = grouped.get(kind)
        if not items:
            continue
        result.append({"kind": kind, "label": PROMPT_KIND_LABEL.get(kind, kind), "items": items})
    return result


# --------------------------------------------------------------------------- #
# 全局设置
# --------------------------------------------------------------------------- #
# 允许前端写入的键。白名单之外的一律拒绝，避免把数据库当草稿纸。
ALLOWED_SETTING_KEYS = {
    "retrieval.vector_top_n",
    "retrieval.bm25_top_n",
    "retrieval.fuse_top_n",
    "retrieval.rerank_top_n",
    "retrieval.rrf_k",
    "retrieval.use_rerank",
    "retrieval.score_threshold",
    "retrieval.expand_retry",
    "retrieval.rewrite",
    "chat.context_token_budget",
    "chat.temperature",
}


def default_settings() -> dict[str, str]:
    """从 config 里取一组默认值（不读数据库）。"""
    from app.config import get_settings

    settings = get_settings()
    return {
        "retrieval.vector_top_n": str(settings.retrieval_vector_top_n),
        "retrieval.bm25_top_n": str(settings.retrieval_bm25_top_n),
        "retrieval.fuse_top_n": str(settings.retrieval_fuse_top_n),
        "retrieval.rerank_top_n": str(settings.retrieval_rerank_top_n),
        "retrieval.rrf_k": str(settings.retrieval_rrf_k),
        "retrieval.use_rerank": "true",
        "retrieval.score_threshold": "0.35",
        "retrieval.expand_retry": "true",
        "retrieval.rewrite": "off",
        "chat.context_token_budget": str(settings.chat_context_token_budget),
        "chat.temperature": "0.3",
    }


async def get_settings_map(session: AsyncSession) -> dict[str, str]:
    """读全部设置（只含白名单里的键），缺的用默认值补齐。"""
    rows = (await session.execute(select(Setting))).scalars().all()
    stored = {row.key: row.value for row in rows if row.key in ALLOWED_SETTING_KEYS}
    merged = default_settings()
    merged.update(stored)
    return merged


async def update_settings(session: AsyncSession, items: dict[str, str]) -> dict[str, str]:
    """批量写设置，返回写入后的完整值。"""
    unknown = set(items) - ALLOWED_SETTING_KEYS
    if unknown:
        raise AppError(
            "SETTING_UNKNOWN_KEY",
            f"不认识这些设置项：{', '.join(sorted(unknown))}",
            status_code=400,
        )

    for key, value in items.items():
        row = await session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=str(value)))
        else:
            row.value = str(value)
    await session.commit()
    logger.info("更新设置 %s 项：%s", len(items), ", ".join(sorted(items)))
    return await get_settings_map(session)


async def get_setting(session: AsyncSession, key: str, fallback: str = "") -> str:
    """读单个设置值，没有就返回 fallback。"""
    row = await session.get(Setting, key)
    if row is not None:
        return row.value
    return default_settings().get(key, fallback)
