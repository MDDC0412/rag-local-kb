"""首次启动的种子数据：默认知识库、内置提示词、分类、以及 .env 里的供应商。"""

from __future__ import annotations

import json
import logging

from sqlalchemy import func, select

from app.config import get_settings
from app.core.logging import mask_secret
from app.db.models import Category, KnowledgeBase, PromptTemplate, ProviderConfig
from app.db.session import SessionLocal
from app.prompts.defaults import DEFAULT_CATEGORIES, DEFAULT_PROMPTS
from app.providers.registry import secret_box

logger = logging.getLogger(__name__)

DEFAULT_KB_NAME = "我的知识库"


async def _seed_kb(session) -> KnowledgeBase:
    """确保存在一个默认知识库。"""
    row = (await session.execute(select(KnowledgeBase).limit(1))).scalar_one_or_none()
    if row is not None:
        return row
    kb = KnowledgeBase(
        name=DEFAULT_KB_NAME, description="默认知识库", chunk_strategy_default="recursive"
    )
    session.add(kb)
    await session.commit()
    await session.refresh(kb)
    logger.info("创建默认知识库 #%s", kb.id)
    return kb


async def _seed_prompts(session) -> None:
    """写入内置提示词模板（已存在则跳过）。"""
    count = (await session.execute(select(func.count()).select_from(PromptTemplate))).scalar_one()
    if count:
        return
    for kind, items in DEFAULT_PROMPTS.items():
        for index, (name, content) in enumerate(items):
            session.add(
                PromptTemplate(
                    kind=kind,
                    name=name,
                    content=content,
                    is_default=index == 0,
                    is_builtin=True,
                )
            )
    await session.commit()
    logger.info("写入内置提示词模板 %s 条", sum(len(v) for v in DEFAULT_PROMPTS.values()))


async def _seed_categories(session) -> None:
    """写入内置分类。"""
    count = (await session.execute(select(func.count()).select_from(Category))).scalar_one()
    if count:
        return
    for name, description in DEFAULT_CATEGORIES:
        session.add(Category(name=name, description=description))
    await session.commit()


async def _seed_providers(session) -> None:
    """把 .env 里的 DEV_* 配置写入数据库（仅当该 kind 下还没有任何供应商）。"""
    settings = get_settings()
    seeds = [
        (
            "llm",
            "openai",
            settings.dev_llm_base_url,
            settings.dev_llm_api_key,
            settings.dev_llm_model,
            {"temperature": 0.3},
        ),
        (
            "embedding",
            "openai",
            settings.dev_embed_base_url,
            settings.dev_embed_api_key,
            settings.dev_embed_model,
            # batch_size 只是**上限**，实际还会按 token 预算动态切批。
            # 别调大：服务端对单次请求的条数上限是硬限制，实测 26 条就 400。
            {"batch_size": 20},
        ),
        (
            "rerank",
            "dashscope_rerank",
            settings.dev_rerank_base_url,
            settings.dev_rerank_api_key,
            settings.dev_rerank_model,
            {},
        ),
    ]
    display = {"llm": "默认对话模型", "embedding": "默认向量模型", "rerank": "默认重排模型"}

    created: dict[str, int] = {}
    for kind, protocol, base_url, api_key, model, params in seeds:
        if not (base_url and api_key and model):
            continue
        exists = (
            await session.execute(
                select(func.count()).select_from(ProviderConfig).where(ProviderConfig.kind == kind)
            )
        ).scalar_one()
        if exists:
            continue
        row = ProviderConfig(
            kind=kind,
            name=display[kind],
            base_url=base_url.rstrip("/"),
            api_key_enc=secret_box().encrypt(api_key),
            model=model,
            protocol=protocol,
            params_json=json.dumps(params, ensure_ascii=False),
            is_default=True,
            enabled=True,
        )
        session.add(row)
        await session.flush()
        created[kind] = row.id
        logger.info(
            "种子供应商 %s(%s) model=%s key=%s", row.name, kind, model, mask_secret(api_key)
        )

    if created:
        await session.commit()


async def _bind_embedding(session, kb: KnowledgeBase) -> None:
    """把默认向量供应商绑到知识库上。"""
    if kb.embedding_provider_id:
        return
    row = (
        await session.execute(
            select(ProviderConfig)
            .where(ProviderConfig.kind == "embedding", ProviderConfig.enabled.is_(True))
            .order_by(ProviderConfig.is_default.desc(), ProviderConfig.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return
    kb.embedding_provider_id = row.id
    await session.commit()
    logger.info("知识库 #%s 绑定向量供应商 #%s", kb.id, row.id)


async def seed_defaults() -> None:
    """启动时调用，幂等。"""
    async with SessionLocal() as session:
        kb = await _seed_kb(session)
        await _seed_prompts(session)
        await _seed_categories(session)
        await _seed_providers(session)
        await _bind_embedding(session, kb)
