"""`invoke_prompt` 必须发 system + user 两条消息。

回归背景（2026-11-26 收尾实测）：本项目用的网关对「只有 user 一条消息」的聊天请求
直接返回 400 `upstream_error`（`{"type":"upstream_error"}`），补一条 system 就正常。
当时提问优化、打标签、LLM 解析、查询改写、HyDE 全都传的是裸字符串，
于是这些功能在真实环境里静默降级（提问优化退回原问题、标签解析失败）。
这个测试守住「调用形态」，不需要真实 API。
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from app.providers.llm import DEFAULT_SYSTEM, invoke_prompt


class _FakeModel:
    """只记录收到了什么消息，不联网。"""

    def __init__(self, reply: str = "结果") -> None:
        self.calls: list[list] = []
        self._reply = reply

    async def ainvoke(self, messages: list) -> AIMessage:
        self.calls.append(messages)
        return AIMessage(content=self._reply)


@pytest.mark.asyncio
async def test_invoke_prompt_sends_system_then_human() -> None:
    """必须先是 system，再是 human —— 顺序错了网关同样报 400。"""
    model = _FakeModel("已优化")
    text = await invoke_prompt(model, "正文")

    assert text == "已优化"
    assert len(model.calls) == 1
    messages = model.calls[0]
    assert [m.type for m in messages] == ["system", "human"]
    assert messages[0].content == DEFAULT_SYSTEM
    assert messages[1].content == "正文"


@pytest.mark.asyncio
async def test_invoke_prompt_allows_custom_system() -> None:
    """调用方可以换成自己的 system（例如分类器口吻）。"""
    model = _FakeModel()
    await invoke_prompt(model, "x", system="你是一个分类器")

    assert model.calls[0][0].content == "你是一个分类器"
    assert model.calls[0][1].content == "x"


class _FlakyModel(_FakeModel):
    """前 N 次抛「上游抖动」，之后成功。"""

    def __init__(self, fail_times: int, error: str) -> None:
        super().__init__("终于成功")
        self._remaining = fail_times
        self._error = error

    async def ainvoke(self, messages: list) -> AIMessage:
        self.calls.append(messages)
        if self._remaining > 0:
            self._remaining -= 1
            raise RuntimeError(self._error)
        return AIMessage(content=self._reply)


@pytest.mark.asyncio
async def test_invoke_prompt_retries_upstream_error() -> None:
    """网关的 400 upstream_error 会成片出现，值得重试。"""
    model = _FlakyModel(2, "Error code: 400 - upstream_error")
    text = await invoke_prompt(model, "正文")

    assert text == "终于成功"
    assert len(model.calls) == 3


@pytest.mark.asyncio
async def test_invoke_prompt_does_not_retry_hard_errors() -> None:
    """401 无效密钥这类错误重试也没用，必须立刻抛出。"""
    model = _FlakyModel(5, "Error code: 401 - invalid api key")

    with pytest.raises(RuntimeError):
        await invoke_prompt(model, "正文")

    assert len(model.calls) == 1
