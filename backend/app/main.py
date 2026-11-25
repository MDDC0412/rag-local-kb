"""FastAPI 入口：lifespan 初始化、全局异常处理、CORS、路由注册。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import providers as providers_api
from app.config import get_settings
from app.core.errors import INTERNAL_ERROR, VALIDATION_FAILED, AppError
from app.core.logging import setup_logging
from app.db.seed import seed_defaults
from app.db.session import init_db

settings = get_settings()
setup_logging(settings.app_log_level, settings.logs_dir)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：建表 + 种子数据；关闭：无需清理。"""
    logger.info("启动中… data_dir=%s", settings.data_dir)
    await init_db()
    await seed_defaults()
    logger.info("就绪：http://%s:%s/docs", settings.app_host, settings.app_port)
    yield
    logger.info("已停止")


app = FastAPI(
    title="本地 RAG 知识库",
    version="0.1.0",
    description="个人本地 RAG 对话系统 · 学习项目",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    """业务异常 → 统一结构。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "detail": exc.detail}},
    )


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """参数校验失败 → 人话提示。"""
    details = exc.errors()
    first = details[0] if details else {}
    field = ".".join(str(part) for part in first.get("loc", [])[1:]) or "请求参数"
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": VALIDATION_FAILED,
                "message": f"参数「{field}」不合法：{first.get('msg', '请检查输入')}",
                "detail": details,
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底：记录完整堆栈，但不把内部细节暴露给前端。"""
    logger.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": INTERNAL_ERROR,
                "message": "服务内部错误，请查看后端日志",
                "detail": type(exc).__name__,
            }
        },
    )


@app.get("/api/health", tags=["meta"])
async def health() -> dict[str, object]:
    """健康检查与基础信息。"""
    return {
        "ok": True,
        "data_dir": str(settings.data_dir),
        "version": app.version,
    }


app.include_router(providers_api.router)
