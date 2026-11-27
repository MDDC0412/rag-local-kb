# 本地 RAG 知识库问答系统

一套运行于个人计算机的知识库问答系统。系统接收本地文档，完成解析、分块、向量化与入库，
提供混合检索（向量召回 + 关键词召回）、重排与带引用溯源的流式问答，并附带用于教学演示的
RAG 实验室页面。

系统面向单用户、本地部署场景，不提供账号体系、权限控制与 OCR 能力。

## 功能范围

### 模型供应商

- 三类供应商：对话模型（LLM）、向量模型（Embedding）、重排模型（Rerank）。
- 协议支持：OpenAI 兼容协议；重排另支持硅基流动、Cohere、DashScope 三种非标准协议。
- 供应商支持连通性测试（返回延迟、可用模型列表）、模型列表拉取、草稿试连与默认项设置。
- 密钥采用 Fernet 对称加密存储，接口与日志仅返回打码结果。

### 文档接入与解析

- 支持格式：PDF、DOCX、Markdown、TXT；扩展名白名单校验，单文件上限 50 MB。
- 上传文件以 UUID 落盘，原始文件名仅存数据库，规避路径穿越与重名冲突。
- 按 SHA-256 去重，重复上传返回提示而不重复入库。
- 解析方式：本地解析，或由对话模型辅助清洗乱排版、页眉页脚与表格结构。
- 解析结果按页落库，重新分块时无需重新解析。

### 分块与预览

- 分块前可选过滤：规则过滤（页眉页脚、页码、碎片行、乱码，毫秒级）与模型过滤
  （整篇仅调用一次模型，判断哪些行不具备检索价值）。
- 七种分块策略：定长、递归、句子、段落、标题、父子块、语义。
  除语义策略需调用向量模型外，其余均为本地计算。
- 分块结果先落库再展示，预览态可改文、删块、合并、调整参数重切。
- 确认入库前不产生向量，入库后状态置为已索引。

### 索引

- 向量化与入库由后台异步任务队列执行，文档详情页可查看进度并按需重试。
- 任务持久化于 `task_jobs` 表，进程重启后自动重放未完成任务。
- 领任务使用带条件的更新语句保证原子性；失败按指数退避重试，参数类错误不重试。
- 支持手动重建索引，用于更换向量模型或修复向量与元数据不一致。

### 检索与问答

- 检索链路：向量召回与关键词召回并行，RRF 融合，重排模型精排，判定质量不足时扩大召回重试一次。
- 关键词检索为自实现打分，采用恒正 IDF 变体，避免小语料下常见词权重归零。
- 查询改写可选，支持关闭、追问改写与 HyDE 三种模式。
- 回答经 SSE 流式输出，每个节点完成时推送执行轨迹。
- 引用溯源：回答中的 `[n]` 可点击展开原文片段与文档页码；引用与检索详情随消息持久化。
- 内置计算器与当前时间读取工具，用于需要精确算术或时间信息的提问；工具调用记录进入执行轨迹。

### 标签与分类

- 入库完成后自动排队，异步生成摘要、分类与标签。
- 分类为受控词表：优先从已有分类中匹配，词表确实缺失且名称足够短时才允许新建。
- 标签按规范化键去重（忽略空白、标点与大小写），数量截断至六个。
- 模型输出经 Schema 校验，解析失败重试一次；分类与标签局部失败仅记警告，摘要照常保存。

### 提示词配置

- 内置模板按用途分组：系统提示词、提问优化、查询改写、解析、过滤、分块、标签。
- 内置模板只读，修改需另存副本；副本可设默认、可重置为内置原文。
- 每种用途仅保留一个默认模板，设定新默认时旧默认自动降级。

### RAG 实验室

用于理解检索增强生成各环节的可视化页面，包含五个部分：

1. 文本向量化：展示分词结果、向量维度与范数、维度分布，结果按文本与模型缓存。
2. 余弦相似度计算器：两段文本逐步展示点积、模长、余弦值与夹角，并给出可读判定。
3. 分块实验台：定长、递归、语义三种策略并排切分，标出被硬切断的位置。
4. 检索链路演示：向量召回、关键词召回、RRF 逐项计算与重排前后名次对照。
5. 术语与常见问题：名词解释与检索系统常见追问。

### 工程能力

- 统一错误码与可读提示，前端按错误码分支，不解析提示文本。
- 全局异常处理统一输出 `{"error": {"code", "message", "detail"}}`。
- 时间统一以 UTC 存储，序列化时带时区标识。
- 日志不输出明文密钥，不输出调试打印。

## 技术栈

| 层 | 选型 |
|---|---|
| 运行环境 | Python 3.11 |
| Web 框架 | FastAPI、uvicorn |
| 数据访问 | SQLAlchemy 2（异步）、aiosqlite |
| 数据校验 | Pydantic v2、pydantic-settings |
| 流程编排 | LangChain、LangGraph |
| 模型调用 | langchain-openai（OpenAI 兼容协议） |
| 向量库 | Chroma（嵌入式持久化） |
| 关键词检索 | jieba 分词 + 自实现 BM25 |
| 文档解析 | PyMuPDF、python-docx、markdown-it-py |
| 前端框架 | Vue 3、Vite、TypeScript |
| 前端组件与状态 | Element Plus、Pinia、vue-router |
| 部署 | Docker Compose（nginx + uvicorn） |

## 目录结构

```
backend/
  app/
    main.py         应用入口：生命周期、异常处理、跨域配置
    config.py       配置加载
    api/            路由层，仅做参数校验与编排
    services/       业务层：解析、分块、索引、检索、问答、标签、提示词、实验室
    providers/      模型适配：对话、向量、重排
    graph/          LangGraph 图定义：状态、节点、构建
    workers/        异步任务队列与任务处理器
    db/             数据模型、会话、种子数据
    core/           错误、日志、网络出口、密钥、时间处理
    schemas/        请求与响应模型
    prompts/        内置提示词常量
  tests/            单元测试
  Dockerfile        后端镜像定义
frontend/
  src/views/        页面：总览、供应商、文档、分块预览、对话、提示词、实验室、设置
  src/components/   通用组件与实验室可视化组件
  src/api/          接口封装，含 SSE 解析
  src/stores/       状态管理
  src/router/       路由定义
  src/styles/       主题变量
  Dockerfile        前端镜像定义（多阶段构建）
  nginx.conf        静态资源服务与反向代理配置
samples/            示例文档
scripts/            开发启动、重置与迁移脚本
docker-compose.yml  容器编排定义
```

## 快速开始

### 环境要求

| 项 | 要求 |
|---|---|
| Python | 3.11 |
| Node.js | ≥ 20 |
| pnpm | ≥ 9 |
| 内存 | 建议 ≥ 8 GB |

### 安装依赖

```powershell
conda create -n rag311 python=3.11 -y
conda activate rag311
pip install -r backend/requirements.txt

cd frontend
pnpm install
```

### 配置模型供应商

将 `.env.example` 复制为 `.env`，填写三类服务的地址、密钥与模型名：

```ini
DEV_LLM_BASE_URL=https://your-gateway/v1
DEV_LLM_API_KEY=sk-xxxx
DEV_LLM_MODEL=你的模型名

DEV_EMBED_BASE_URL=https://your-gateway/v1
DEV_EMBED_API_KEY=sk-xxxx
DEV_EMBED_MODEL=你的向量模型名

DEV_RERANK_BASE_URL=https://your-gateway/compatible-mode/v1
DEV_RERANK_API_KEY=sk-xxxx
DEV_RERANK_MODEL=你的重排模型名
```

`.env` 仅作为首次启动的种子配置：启动时写入数据库并以 Fernet 加密保存，
此后以「模型供应商」页面中的配置为准。

### 启动服务

```powershell
# 后端：必须单进程运行
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端
cd frontend
pnpm dev
```

- 前端：http://127.0.0.1:5173
- 接口文档：http://127.0.0.1:8000/docs

一键启动：`powershell -File scripts/dev.ps1`。

### 使用流程

1. 在「模型供应商」页面为三类服务各执行一次连通性测试。
2. 在「文档库」上传文件。
3. 生成分块预览，按需调整策略、块大小与过滤选项，确认后入库。
4. 入库完成后系统自动排队生成摘要、分类与标签。
5. 在「对话」页面提问，回答附引用来源。

## 部署

### 标准方式：一条命令

```bash
docker compose up -d --build
```

依赖安装在构建镜像时完成：前端在 `node:22-alpine` 阶段执行 `npm install` 与 `vite build`，
后端在 `python:3.11-slim` 阶段执行 `pip install`，产物拷入运行镜像。宿主机不需要安装
Node、Python 或任何包管理器，只需 Docker 与 Docker Compose。

访问：`http://服务器IP:8080`。数据目录为宿主机上的 `./data`，
包含数据库、向量库、上传文件与日志。

### 服务器无法访问 npm 源时：镜像分发

内网或离线服务器若不能访问 npm registry，构建会在依赖安装阶段失败。
此时改为在任意可上网的机器上构建镜像，再把镜像分发到服务器：

```bash
# 1. 可上网的机器：构建并导出镜像
docker compose build
docker save rag-backend:latest rag-frontend:latest | gzip > rag-images.tar.gz

# 2. 上传 rag-images.tar.gz 与仓库源码到服务器后
docker load < rag-images.tar.gz
docker compose up -d --no-build
```

服务器只加载镜像并启动容器，不参与构建，也不需要 Node 与 npm 源。前端依赖更新后重新执行第 1 步。

### 可选环境变量

在仓库根目录的 `.env` 中按需设置（`docker compose` 自动读取）：

```ini
# 跨域白名单，设置为实际访问地址
APP_PUBLIC_ORIGIN=http://服务器IP:8080

# 构建期依赖源与代理
NPM_REGISTRY=https://registry.npmjs.org
PIP_INDEX_URL=https://pypi.org/simple
HTTP_PROXY=
HTTPS_PROXY=
NO_PROXY=
```

`APP_*` 变量写入容器运行时环境，其余变量仅在构建阶段生效。

### 单进程约束

Chroma 采用嵌入式模式，底层为 SQLite，同一数据目录不得被多个进程同时打开。
后端容器须单进程运行，不得配置多 worker，也不得在宿主机另行启动进程指向同一数据目录。

## 运行约束与已知限制

| 项 | 说明 |
|---|---|
| 数据一致性 | SQLite 为唯一事实源，向量库可随时重建；更换向量模型后必须重建索引 |
| OCR | 不支持，扫描版 PDF 需先转换为可检索文本 |
| 多知识库 | 本期为单库设计，数据表保留扩展字段 |
| 语义分块 | 需调用向量模型，长文档成本较高，结果按句缓存 |
| 外部服务 | 依赖外部模型接口，供应商不可用时问答与标签生成会失败并返回可读提示 |

## 许可

MIT License，详见 [`LICENSE`](LICENSE)。
