"""后台任务相关的请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.core.timeutil import UTCDateTime, UTCDateTimeOptional

JobStatus = str  # pending / running / done / failed


class JobOut(BaseModel):
    """任务详情，前端据此显示进度条与重试按钮。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    target_id: int | None
    status: str
    progress: int
    attempts: int
    error_msg: str
    started_at: UTCDateTimeOptional
    finished_at: UTCDateTimeOptional
    created_at: UTCDateTime


class JobEnqueued(BaseModel):
    """把一件事排进队列后的回执；``deduplicated`` 表示复用了已存在的任务。"""

    job: JobOut
    deduplicated: bool
