# 本地 RAG 知识库问答系统

一套运行于个人计算机的知识库问答系统，覆盖文档接入、解析、分块、向量化、混合检索、
重排与带引用的流式问答全链路，并提供用于教学演示的 RAG 实验室页面。

系统面向单用户、本地部署场景，不包含多用户、权限与 OCR 能力。

## 功能范围

| 模块 | 能力 |
|---|---|
| 模型供应商 | LLM / Embedding / Rerank 三类；OpenAI 兼容协议，另支持硅基流动、Cohere、DashScope 三种重排协议 |
| 文档接入 | 支持 PDF、DOCX、Markdown、TXT；扩展名白名单、单文件 50 MB 上限、SHA-256 去重 |
| 解析与分块 | 本地解析或 LLM 辅助解析；分块前可选规则过滤与模型过滤；提供定长、递归、句子、段落、标题、父子块、语义七种策略 |
| 预览与编辑 | 分块结果先落库再展示，支持改文、删块、合并、调参重切，确认后方可入库 |
| 索引 | 异步任务队列执行向量化与入库，进度可查、失败可重试；支持重建索引 |
| 检索 | 向量召回与 BM25 召回并行，RRF 融合，重排模型精排，质量判定不足时扩大召回重试 |
| 问答 | SSE 流式输出，回答带 `[n]` 引用并可溯源到文档页码；可选查询改写与 HyDE |
| 标签分类 | 入库后异步生成摘要、分类与标签；分类为受控词表，标签按规范化键去重 |
| 提示词 | 内置模板可另存副本、设默认、重置；系统提示词、优化、解析、过滤、分块、标签、改写分类管理 |
| 实验室 | 文本向量化、余弦相似度计算、分块策略对照、检索链路分步演示、术语与常见问题 |
| 工程能力 | 统一错误码与可读提示、密钥加密存储与日志脱敏、SQLite 单一事实源 |

## 技术栈

- 后端：Python 3.11、FastAPI、SQLAlchemy 2（异步）、Pydantic v2
- 编排：LangChain、LangGraph
- 存储：SQLite（业务数据）、Chroma（向量库，可重建）
- 前端：Vue 3、Vite、TypeScript、Element Plus、Pinia
- 部署：Docker Compose（nginx + uvicorn）

## 目录结构

```
backend/
  app/
    api/         路由层
    services/    业务逻辑：解析、分块、索引、检索、问答、标签、提示词
    providers/   模型适配：LLM、Embedding、Rerank
    graph/       LangGraph 图定义
    workers/     异步任务队列
    db/          数据模型与会话
    core/        错误、日志、网络、安全、时间
  tests/         单元测试
frontend/
  src/views/     页面
  src/components 组件
  src/api/       接口封装
  src/stores/    状态管理
samples/         示例文档
scripts/         开发与运维脚本
```

## 快速开始

```powershell
# 1. 后端环境
conda create -n rag311 python=3.11 -y
conda activate rag311
pip install -r backend/requirements.txt

# 2. 前端依赖
cd frontend
pnpm install

# 3. 配置模型供应商：复制 .env.example 为 .env，填写三类服务的地址、密钥与模型名
#    密钥写入数据库时使用 Fernet 加密，接口与日志仅返回打码结果

# 4. 启动
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000   # 单进程运行

cd frontend
pnpm dev
```

访问 `http://127.0.0.1:5173`，后端接口文档位于 `http://127.0.0.1:8000/docs`。

一键启动脚本：`powershell -File scripts/dev.ps1`。

## 部署

```powershell
docker compose up --build
# 访问 http://127.0.0.1:8080
```

数据目录挂载至宿主机 `./data`。容器内后端以单进程运行。

## 运行约束

- Chroma 采用嵌入式模式，底层为 SQLite，**同一数据目录不得被多个进程同时打开**；
  后端须单进程运行，容器部署同理。
- 更换向量模型后须重建索引；SQLite 为唯一事实源，向量可随时重建。
- 系统不提供 OCR，扫描版 PDF 需先转换为可检索文本。

## 许可

MIT License，详见 `LICENSE`。
