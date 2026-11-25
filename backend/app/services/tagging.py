"""标签与分类：让模型读文档，产出摘要、分类和关键词。

三条设计约束（来自 DESIGN §6.3）
--------------------------------
1. **分类是受控词表**：只能从已有分类里选，确实都不合适才允许新建，
   且新建一次只允许一个、名字不超过 6 字 —— 否则一年后会攒出 200 个分类。
2. **标签优先复用**：比较时归一化（大小写、全半角空格、标点），避免
   「报销」「報銷」「报销 」同时存在。
3. **不信任模型输出**：必须过 Pydantic 校验，失败重试 1 次，
   再失败就只存摘要（摘要拿不到就整体算失败，由队列重试）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NOT_FOUND, AppError
from app.db.models import Category, Chunk, Document, DocumentPage, DocumentTag, PromptTemplate, Tag
from app.providers.llm import build_chat_model
from app.providers.registry import resolve_spec

logger = logging.getLogger(__name__)

SUMMARY_LIMIT = 500
TAG_NAME_LIMIT = 32
MAX_TAGS = 6
# 新建分类的长度上限：够表达一个概念，又不至于变成一句话
MAX_NEW_CATEGORY_LEN = 6

# 标签里不该出现的标点（中英文都算）
_TAG_PUNCT = re.compile(r"[，。、；：！？,.;:!?'\"“”‘’（）()\[\]【】<>《》·—\-_/\\|]")
_WHITESPACE = re.compile(r"\s+")

FALLBACK_TEMPLATE = """阅读文档信息，输出摘要、分类和标签。

要求：
1. summary：60-100 字中文摘要，说明这份文档讲什么。
2. category：必须从【已有分类】里选一个；只有确实都不合适时，才新建一个不超过 6 字的分类。
3. tags：3-6 个中文关键词，优先复用【已有标签】里的词，不要带标点。
4. 只输出 JSON：{{"summary": "...", "category": "...", "tags": ["..."]}}

已有分类：{categories}
已有标签：{tags}

文档标题：{filename}
文档开头：
{head}"""


# --------------------------------------------------------------------------- #
# 归一化（纯函数，单测重点）
# --------------------------------------------------------------------------- #
def clean_tag(name: str) -> str:
    """把标签清洗成可入库的展示形式：去空白、去标点、截断。"""
    text = _WHITESPACE.sub("", (name or "").strip())
    text = _TAG_PUNCT.sub("", text)
    return text[:TAG_NAME_LIMIT]


def tag_key(name: str) -> str:
    """标签的比较键：清洗 + 大小写折叠，用来判断「是不是同一个标签」。"""
    return clean_tag(name).casefold()


def normalize_tags(names: list[str], *, limit: int = MAX_TAGS) -> list[str]:
    """清洗 + 去重（按比较键）+ 截断，保持模型给的先后顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for raw in names:
        name = clean_tag(str(raw))
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
        if len(result) >= limit:
            break
    return result


def pick_category(
    suggested: str, existing: list[Category], *, allow_new: bool = True
) -> tuple[Category | None, str | None]:
    """把模型给的分类名对上受控词表，返回 ``(命中的分类, 要新建的名字)``。

    先精确匹配，再按归一化比较（模型常会多带空格或标点）；
    都对不上时，只有名字短到可以当分类才允许新建，否则宁可不分类。
    """
    name = clean_tag(suggested)
    if not name:
        return None, None

    for row in existing:
        if tag_key(row.name) == name.casefold():
            return row, None

    if not allow_new or len(name) > MAX_NEW_CATEGORY_LEN:
        return None, None
    return None, name


# --------------------------------------------------------------------------- #
# 模型输出解析
# --------------------------------------------------------------------------- #
class TagPayload(BaseModel):
    """模型必须返回的结构。字段都给了默认值，缺字段不算错，错的是类型。"""

    summary: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: object) -> list[str]:
        """模型偶尔把 tags 写成逗号分隔的字符串，容错成列表。"""
        if value is None:
            return []
        if isinstance(value, str):
            return [part for part in re.split(r"[,，、;；\s]+", value) if part]
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    @field_validator("summary", "category", mode="before")
    @classmethod
    def _coerce_text(cls, value: object) -> str:
        """None / 数字一律转成字符串，避免校验直接炸。"""
        return "" if value is None else str(value)


def extract_json(raw: str) -> dict:
    """从模型输出里抠出第一个 JSON 对象，容忍 ```json 代码块和前后废话。"""
    text = (raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("模型输出里没有 JSON 对象")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型输出的 JSON 不是对象")
    return data


def parse_payload(raw: str) -> TagPayload:
    """把模型输出解析成 TagPayload，顺便校验结构与长度。"""
    payload = TagPayload.model_validate(extract_json(raw))
    payload.summary = payload.summary.strip()[:SUMMARY_LIMIT]
    return payload


# --------------------------------------------------------------------------- #
# 文档信息收集
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class TagResult:
    """一次打标签的结果，供队列回显与人话提示。"""

    summary: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def describe(self) -> str:
        """给日志和任务结果用的一句话。"""
        parts = []
        if self.category:
            parts.append(f"分类「{self.category}」")
        if self.tags:
            parts.append(f"{len(self.tags)} 个标签")
        return "，".join(parts) if parts else "只生成了摘要"


async def _document_head(session: AsyncSession, document_id: int, limit: int = 800) -> str:
    """取文档开头：优先用解析出的原文，没有再退回分块内容。"""
    pages = (
        (
            await session.execute(
                select(DocumentPage.text)
                .where(DocumentPage.document_id == document_id)
                .order_by(DocumentPage.page_no)
            )
        )
        .scalars()
        .all()
    )
    text = "\n".join(str(page) for page in pages if page).strip()
    if not text:
        chunks = (
            (
                await session.execute(
                    select(Chunk.content)
                    .where(Chunk.document_id == document_id)
                    .order_by(Chunk.seq)
                    .limit(2)
                )
            )
            .scalars()
            .all()
        )
        text = "\n".join(str(chunk) for chunk in chunks if chunk).strip()
    return text[:limit]


async def _template(session: AsyncSession) -> str:
    """取提示词库里的 tag 模板，取不到用内置兜底。"""
    row = (
        await session.execute(
            select(PromptTemplate)
            .where(PromptTemplate.kind == "tag")
            .order_by(PromptTemplate.is_default.desc(), PromptTemplate.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return row.content if row and row.content.strip() else FALLBACK_TEMPLATE


def _build_prompt(
    template: str, *, filename: str, head: str, categories: list[str], tags: list[str]
) -> str:
    """把词表和文档信息填进模板。词表为空时给一句明确说明，别让模型瞎猜。"""
    return template.format(
        filename=filename,
        head=head,
        categories="、".join(categories) if categories else "（暂无，可新建一个）",
        tags="、".join(tags) if tags else "（暂无）",
    )


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
async def _ask_model(prompt: str, session: AsyncSession) -> TagPayload:
    """调一次模型并解析；解析失败重试 1 次（提示模型只输出 JSON）。"""
    spec = await resolve_spec(session, "llm")
    model = build_chat_model(spec, temperature=0.0, streaming=False)

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            response = await model.ainvoke(prompt)
            content = (
                response.content if isinstance(response.content, str) else str(response.content)
            )
            return parse_payload(content)
        except Exception as exc:
            last_error = exc
            logger.warning("打标签第 %s 次解析失败：%s", attempt, exc)
            if attempt == 1:
                # 第二次把要求再钉死一遍，模型多半就不带废话了
                prompt = f"{prompt}\n\n【再次强调】只输出 JSON 对象本身，不要代码块，不要解释。"
    raise ValueError(f"模型输出无法解析：{last_error}")


async def _apply_category(
    session: AsyncSession, doc: Document, payload: TagPayload, result: TagResult
) -> None:
    """把分类落到文档上：命中已有分类就挂上，允许时新建一个。"""
    existing = list((await session.execute(select(Category).order_by(Category.id))).scalars())
    matched, new_name = pick_category(payload.category, existing)

    if matched is not None:
        doc.category_id = matched.id
        result.category = matched.name
        return

    if new_name is None:
        if payload.category:
            result.warnings.append(
                f"模型建议的分类「{clean_tag(payload.category)}」不在词表里且过长，已保留原分类"
            )
        return

    row = Category(name=new_name, description="模型自动创建")
    session.add(row)
    await session.flush()
    doc.category_id = row.id
    result.category = new_name
    logger.info("新建分类「%s」（文档 #%s 触发）", new_name, doc.id)


async def _apply_tags(
    session: AsyncSession, doc: Document, names: list[str], result: TagResult
) -> None:
    """把标签写到 document_tags：已存在的复用，不存在的建。"""
    clean = normalize_tags(names)
    if not clean:
        return

    existing_rows = list((await session.execute(select(Tag))).scalars())
    by_key = {tag_key(row.name): row for row in existing_rows}

    await session.execute(delete(DocumentTag).where(DocumentTag.document_id == doc.id))

    saved: list[str] = []
    for name in clean:
        row = by_key.get(name.casefold())
        if row is None:
            row = Tag(name=name, color="")
            session.add(row)
            await session.flush()
            by_key[name.casefold()] = row
        session.add(DocumentTag(document_id=doc.id, tag_id=row.id))
        saved.append(row.name)

    result.tags = saved


async def tag_document(session: AsyncSession, document_id: int) -> TagResult:
    """给一篇文档生成摘要、分类和标签，并把结果落库。

    失败语义：模型调用/解析彻底失败时抛异常，让队列去重试；
    分类或标签单独出问题只记 warning，摘要照存 —— 部分成功好过全丢。
    """
    doc = await session.get(Document, document_id)
    if doc is None:
        raise AppError(NOT_FOUND, f"文档 #{document_id} 不存在", status_code=404)

    head = await _document_head(session, document_id)
    if not head:
        raise AppError(
            "NO_TEXT_FOR_TAGGING",
            f"「{doc.filename}」还没有可用的文本，请先解析文档",
            status_code=400,
        )

    categories = list(
        (await session.execute(select(Category.name).order_by(Category.id))).scalars()
    )
    tags = list((await session.execute(select(Tag.name).order_by(Tag.id))).scalars())
    template = await _template(session)
    prompt = _build_prompt(
        template, filename=doc.filename, head=head, categories=categories, tags=tags
    )

    payload = await _ask_model(prompt, session)

    result = TagResult()
    if payload.summary:
        doc.summary = payload.summary
        result.summary = payload.summary
    else:
        result.warnings.append("模型没有给出摘要")

    await _apply_category(session, doc, payload, result)
    await _apply_tags(session, doc, payload.tags, result)

    await session.commit()
    logger.info("文档 #%s 打标签完成：%s", document_id, result.describe())
    return result


# --------------------------------------------------------------------------- #
# 查询与手动编辑
# --------------------------------------------------------------------------- #
async def document_tag_map(session: AsyncSession, document_ids: list[int]) -> dict[int, list[str]]:
    """批量取「文档 → 标签名」，列表页一次查完，避免 N+1。"""
    if not document_ids:
        return {}
    rows = (
        await session.execute(
            select(DocumentTag.document_id, Tag.name)
            .join(Tag, Tag.id == DocumentTag.tag_id)
            .where(DocumentTag.document_id.in_(document_ids))
            .order_by(Tag.name)
        )
    ).all()
    result: dict[int, list[str]] = {doc_id: [] for doc_id in document_ids}
    for document_id, name in rows:
        result.setdefault(document_id, []).append(name)
    return result


async def document_category_name(session: AsyncSession, category_id: int | None) -> str | None:
    """取分类名；没有分类返回 None。"""
    if category_id is None:
        return None
    row = await session.get(Category, category_id)
    return row.name if row else None


async def list_tags(session: AsyncSession) -> list[tuple[Tag, int]]:
    """列出全部标签及使用次数（使用多的排前面）。"""
    rows = (
        await session.execute(
            select(Tag, func.count(DocumentTag.document_id))
            .outerjoin(DocumentTag, DocumentTag.tag_id == Tag.id)
            .group_by(Tag.id)
            .order_by(func.count(DocumentTag.document_id).desc(), Tag.name)
        )
    ).all()
    return [(row[0], int(row[1])) for row in rows]


async def list_categories(session: AsyncSession) -> list[tuple[Category, int]]:
    """列出全部分类及文档数。"""
    rows = (
        await session.execute(
            select(Category, func.count(Document.id))
            .outerjoin(Document, Document.category_id == Category.id)
            .group_by(Category.id)
            .order_by(Category.id)
        )
    ).all()
    return [(row[0], int(row[1])) for row in rows]


async def set_document_tags(session: AsyncSession, document_id: int, names: list[str]) -> list[str]:
    """手动覆盖文档标签（前端编辑用），返回落库后的标签名。"""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise AppError(NOT_FOUND, f"文档 #{document_id} 不存在", status_code=404)

    result = TagResult()
    await _apply_tags(session, doc, names, result)
    await session.commit()
    return result.tags
