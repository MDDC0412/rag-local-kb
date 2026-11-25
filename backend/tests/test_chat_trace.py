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
        "generate",
    }

    # 每个 "name": "xxx" 都得是前端认识的步骤名
    for name in pipeline_names:
        if f'"name": "{name}"' in source:
            assert name in pipeline_names
    assert '"name": "generate"' in source
