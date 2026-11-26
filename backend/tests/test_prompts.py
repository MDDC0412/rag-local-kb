"""提示词模板库与设置的测试。

核心是两条**业务规则**，不是"代码跑通就行"：
1. 内置模板只读 —— 要改必须先另存副本，否则用户会把各功能的兜底逻辑改坏。
2. 每种 kind 只能有一个默认 —— 否则 order_by(is_default) 取到哪条就变成不确定的。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.errors import AppError
from app.db.models import Base, PromptTemplate
from app.schemas.prompt import PromptTemplateCreate, PromptTemplateUpdate
from app.services import prompts as prompt_service


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """每个测试一个独立内存库，只装内置模板。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as s:
        for kind, items in {
            "system": [("默认助手", "你是助手")],
            "tag": [("标签", "输出 JSON")],
        }.items():
            for index, (name, content) in enumerate(items):
                s.add(
                    PromptTemplate(
                        kind=kind,
                        name=name,
                        content=content,
                        is_default=index == 0,
                        is_builtin=True,
                    )
                )
        await s.commit()
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_returns_all(session: AsyncSession) -> None:
    """列出全部模板。"""
    rows = await prompt_service.list_templates(session)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_list_filters_by_kind(session: AsyncSession) -> None:
    """按 kind 过滤。"""
    rows = await prompt_service.list_templates(session, kind="tag")
    assert len(rows) == 1
    assert rows[0].kind == "tag"


@pytest.mark.asyncio
async def test_grouped_templates(session: AsyncSession) -> None:
    """分组返回，带中文标签。"""
    groups = await prompt_service.grouped_templates(session)
    kinds = [group["kind"] for group in groups]
    assert "system" in kinds
    assert all(group["label"] for group in groups)


@pytest.mark.asyncio
async def test_builtin_cannot_be_updated(session: AsyncSession) -> None:
    """内置模板拒绝直接修改 —— 必须先另存副本。"""
    builtin = (await prompt_service.list_templates(session, kind="system"))[0]

    with pytest.raises(AppError) as exc:
        await prompt_service.update_template(
            session, builtin.id, PromptTemplateUpdate(content="被我改坏了")
        )
    assert exc.value.code == "TEMPLATE_READONLY"
    assert exc.value.status_code == 409

    # 内容没被改动
    await session.refresh(builtin)
    assert builtin.content == "你是助手"


@pytest.mark.asyncio
async def test_duplicate_creates_editable_copy(session: AsyncSession) -> None:
    """另存为副本后，副本可以改。"""
    builtin = (await prompt_service.list_templates(session, kind="system"))[0]
    copy = await prompt_service.duplicate_template(session, builtin.id)

    assert copy.is_builtin is False
    assert copy.content == builtin.content
    assert "副本" in copy.name

    updated = await prompt_service.update_template(
        session, copy.id, PromptTemplateUpdate(content="新的内容")
    )
    assert updated.content == "新的内容"


@pytest.mark.asyncio
async def test_duplicate_does_not_inherit_default(session: AsyncSession) -> None:
    """副本不该自动变成默认，否则会偷偷顶掉原默认。"""
    builtin = (await prompt_service.list_templates(session, kind="system"))[0]
    copy = await prompt_service.duplicate_template(session, builtin.id)

    assert copy.is_default is False
    await session.refresh(builtin)
    assert builtin.is_default is True


@pytest.mark.asyncio
async def test_create_template(session: AsyncSession) -> None:
    """新建自定义模板。"""
    row = await prompt_service.create_template(
        session,
        PromptTemplateCreate(kind="filter", name="我的过滤", content="删掉目录"),
    )
    assert row.id
    assert row.is_builtin is False
    assert row.kind == "filter"


@pytest.mark.asyncio
async def test_only_one_default_per_kind(session: AsyncSession) -> None:
    """设新默认时旧的必须降级，否则默认变成不确定的。"""
    first = await prompt_service.create_template(
        session,
        PromptTemplateCreate(kind="tag", name="A", content="a", is_default=True),
    )
    second = await prompt_service.create_template(
        session,
        PromptTemplateCreate(kind="tag", name="B", content="b", is_default=True),
    )

    rows = await prompt_service.list_templates(session, kind="tag")
    defaults = [row for row in rows if row.is_default]
    assert len(defaults) == 1
    assert defaults[0].id == second.id
    assert first.id not in {row.id for row in defaults}


@pytest.mark.asyncio
async def test_builtin_cannot_be_deleted(session: AsyncSession) -> None:
    """内置模板不能删。"""
    builtin = (await prompt_service.list_templates(session, kind="system"))[0]
    with pytest.raises(AppError) as exc:
        await prompt_service.delete_template(session, builtin.id)
    assert exc.value.code == "TEMPLATE_READONLY"


@pytest.mark.asyncio
async def test_custom_can_be_deleted(session: AsyncSession) -> None:
    """自定义模板可以删。"""
    row = await prompt_service.create_template(
        session, PromptTemplateCreate(kind="filter", name="临时", content="x")
    )
    await prompt_service.delete_template(session, row.id)
    rows = await prompt_service.list_templates(session, kind="filter")
    assert rows == []


@pytest.mark.asyncio
async def test_reset_restores_builtin_text(session: AsyncSession) -> None:
    """重置能把副本改回内置同名模板的原文。"""
    copy = await prompt_service.create_template(
        session,
        PromptTemplateCreate(kind="system", name="默认助手", content="我改过的"),
    )
    restored = await prompt_service.reset_template(session, copy.id)
    assert restored.content == "你是助手"


@pytest.mark.asyncio
async def test_reset_duplicate_by_name_suffix(session: AsyncSession) -> None:
    """副本名字带「（副本）」后缀，重置也要能找回内置原文。

    这是真实踩到的 bug：另存副本后点「重置」报 400 TEMPLATE_NO_ORIGINAL，
    因为按名字精确匹配不到内置模板。
    """
    builtin = (await prompt_service.list_templates(session, kind="system"))[0]
    copy = await prompt_service.duplicate_template(session, builtin.id)
    assert copy.name != builtin.name, "副本名字应该带后缀，否则这个测试没意义"

    await prompt_service.update_template(
        session, copy.id, PromptTemplateUpdate(content="我改过的内容")
    )

    restored = await prompt_service.reset_template(session, copy.id)
    assert restored.content == builtin.content, "重置应恢复成该 kind 的内置原文"


@pytest.mark.asyncio
async def test_reset_without_original_fails(session: AsyncSession) -> None:
    """该 kind 下完全没有内置模板时才报错。"""
    row = await prompt_service.create_template(
        session, PromptTemplateCreate(kind="filter", name="没有原文的", content="x")
    )
    with pytest.raises(AppError) as exc:
        await prompt_service.reset_template(session, row.id)
    assert exc.value.code == "TEMPLATE_NO_ORIGINAL"


@pytest.mark.asyncio
async def test_get_missing_template(session: AsyncSession) -> None:
    """查不存在的模板报 404。"""
    with pytest.raises(AppError) as exc:
        await prompt_service.get_template(session, 999)
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_settings_have_defaults_when_empty(session: AsyncSession) -> None:
    """表为空时返回默认值，前端不会拿到 None。"""
    values = await prompt_service.get_settings_map(session)
    assert values["retrieval.rrf_k"] == "60"
    assert "chat.context_token_budget" in values


@pytest.mark.asyncio
async def test_update_settings_roundtrip(session: AsyncSession) -> None:
    """写入后能读回来，且只影响写的那个键。"""
    before = await prompt_service.get_settings_map(session)
    updated = await prompt_service.update_settings(session, {"retrieval.rerank_top_n": "8"})

    assert updated["retrieval.rerank_top_n"] == "8"
    assert updated["retrieval.rrf_k"] == before["retrieval.rrf_k"]


@pytest.mark.asyncio
async def test_update_settings_rejects_unknown_key(session: AsyncSession) -> None:
    """白名单外的键一律拒绝，别把数据库当草稿纸。"""
    with pytest.raises(AppError) as exc:
        await prompt_service.update_settings(session, {"drop.table": "1"})
    assert exc.value.code == "SETTING_UNKNOWN_KEY"


@pytest.mark.asyncio
async def test_update_settings_is_idempotent(session: AsyncSession) -> None:
    """重复写同一个键不会产生重复行。"""
    await prompt_service.update_settings(session, {"retrieval.rrf_k": "80"})
    await prompt_service.update_settings(session, {"retrieval.rrf_k": "90"})

    from sqlalchemy import select

    from app.db.models import Setting

    rows = (
        (await session.execute(select(Setting).where(Setting.key == "retrieval.rrf_k")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].value == "90"


@pytest.mark.asyncio
async def test_get_single_setting_fallback(session: AsyncSession) -> None:
    """单键读取：没有存过就返回默认值。"""
    value = await prompt_service.get_setting(session, "retrieval.rerank_top_n")
    assert value == "5"

    await prompt_service.update_settings(session, {"retrieval.rerank_top_n": "7"})
    assert await prompt_service.get_setting(session, "retrieval.rerank_top_n") == "7"


@pytest.mark.asyncio
async def test_setting_whitelist_covers_all_defaults() -> None:
    """每个默认值都必须能写回去，否则前端改了存不进。"""
    for key in prompt_service.default_settings():
        assert key in prompt_service.ALLOWED_SETTING_KEYS, f"{key} 没在白名单里，改了存不进"
