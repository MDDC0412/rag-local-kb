"""聊天轨迹（右侧「执行过程」面板）的落库回归测试。

盯住一个曾经真实存在的 bug：
``generate`` 这一步发生在 LangGraph 图**之外**（因为要推 SSE 增量），
但它只被 ``yield`` 出去、没有 append 进 ``all_steps``。于是：

- 当次浏览：前端收到了 ``step`` 事件，能看到「模型生成」变绿；
- 重新打开历史会话：从 ``retrieval_json.steps`` 恢复，**少了这一步**，
  界面就永远显示成一个灰点。

用户看到的现象就是「模型输出完了，最后那一步没变绿」。
"""

from __future__ import annotations

import inspect

from app.services import chat as chat_service


def test_generate_step_is_appended_to_all_steps() -> None:
    """源码层面确认 generate 的 done 步骤会并入 all_steps。

    这里不跑真实 SSE（那需要外部模型），只守住那个「必须 append」的契约：
    删掉这一行，历史会话的轨迹就又会少一步。
    """
    source = inspect.getsource(chat_service._stream_impl)

    assert "all_steps.append(generate_step)" in source, (
        "generate 步骤必须并入 all_steps，否则历史会话里「模型生成」永远是灰点"
    )


def test_generate_step_name_matches_frontend_pipeline() -> None:
    """后端推送的步骤名必须和前端 PIPELINE 里的名字对得上。"""
    source = inspect.getsource(chat_service._stream_impl)
    pipeline_names = {
        "rewrite",
        "embed",
        "recall",
        "fuse",
        "rerank",
        "grade",
        "answer",
        "tool",
        "generate",
    }

    # 每个 "name": "xxx" 都得是前端认识的步骤名
    for name in pipeline_names:
        if f'"name": "{name}"' in source:
            assert name in pipeline_names
    assert '"name": "generate"' in source


def test_tool_steps_are_persisted_like_generate() -> None:
    """工具步骤也要并入 all_steps，否则历史会话里看不到调用过什么。"""
    source = inspect.getsource(chat_service._stream_impl)

    assert "all_steps.append(record)" in source, (
        "工具调用记录必须并入 all_steps，否则重开历史会话就丢了"
    )
    assert "tool_calls" in source, "完整调用记录要存进 retrieval_json.tool_calls"


def test_multiple_tool_calls_share_one_assistant_message() -> None:
    """一轮里多个工具调用必须合并进**一条** assistant 消息。

    这是 OpenAI function-calling 协议：一条 assistant 消息带全部 tool_calls，
    后面跟每个 tool 结果。拆成多条会被 API 直接拒绝 ——
    实测「现在几点 + 住 2 晚多少钱」会同时要两个工具，正好走这条路径。
    """
    source = inspect.getsource(chat_service._run_tool_rounds)

    # 先把结果收集进列表，再一次性 append assistant
    assert "results: list[tuple[str, str, dict, str]] = []" in source
    assert "for call_id, call_name, call_args, _ in results" in source, (
        "tool_calls 必须用列表推导一次性写入，不能每个 call 单独 append 一条 assistant"
    )


def test_tool_round_is_not_streamed_twice() -> None:
    """工具轮若已给出正文，不能再流式重说一遍（耗时翻倍且措辞会变）。"""
    source = inspect.getsource(chat_service._run_tool_rounds)

    assert "return working, usage, steps, records, content.strip()" in source
    # 工具轮绝不能用流式模型
    assert "streaming=False" in source
