# 本地 RAG 知识库

一个跑在自己电脑上的个人知识库问答系统。把 PDF / Word / Markdown / 纯文本丢进去，
它会切块、向量化、自动打标签；然后用自然语言提问，它从你的文档里找依据，
**带着出处**回答你，并把整个检索过程摊开给你看。

自用 + 学习项目，不是企业级产品：不做多用户、不做 OCR、不追求高并发。

---

## 它长什么样

```
文档上传 → 解析成文字 → 切块（可预览可编辑） → 向量化入库
                                                      ↓
                                        自动生成摘要 / 分类 / 标签
                                                      ↓
提问 → 查询改写（可选）→ 向量召回 + 关键词召回 → RRF 融合 → 精排
     → 质量判定（不够就扩召回重试）→ 调工具（算数/时间）→ 流式生成 → 引用溯源
```

右侧常驻一个「执行过程」面板，上面每一步都会点亮，能看到检索命中了什么、
用了哪几个模型、调了什么工具、花了多少毫秒。

---

## 快速开始

### 1. 环境

| 项 | 要求 |
|---|---|
| Python | **3.11**（3.14 装不上 chromadb，没有 wheel） |
| Node | ≥ 20（本项目用 Node 24 验证） |
| pnpm | ≥ 9 |
| 内存 | 建议 ≥ 8GB（向量库与 BM25 索引都在内存里） |

### 2. 装依赖

```powershell
# 后端（conda 是推荐方式，venv 也行）
conda create -n rag311 python=3.11 -y
conda activate rag311
pip install -r backend/requirements.txt

# 前端
cd frontend
pnpm install
```

### 3. 配置模型供应商

复制 `.env.example` 为 `.env`（仓库里已有 `.env` 可参考），填三类供应商：

```ini
# 对话模型（任何 OpenAI 兼容服务都行）
DEV_LLM_BASE_URL=https://your-gateway/v1
DEV_LLM_API_KEY=sk-xxxx
DEV_LLM_MODEL=你的模型名

# 向量模型（必须支持 /embeddings）
DEV_EMBED_BASE_URL=https://your-gateway/v1
DEV_EMBED_API_KEY=sk-xxxx
DEV_EMBED_MODEL=你的向量模型名

# 重排模型（可选，不填就按融合分排序）
DEV_RERANK_BASE_URL=https://your-gateway/compatible-mode/v1
DEV_RERANK_API_KEY=sk-xxxx
DEV_RERANK_MODEL=你的重排模型名
```

`.env` 只是**首次启动的种子**：启动时写入数据库（密钥用 Fernet 加密），
之后以「模型供应商」页面里的配置为准。

> ⚠️ **密钥安全**：`.env` 与 `data/.secret` 都在 `.gitignore` 里。
> 接口只返回打码形式（`sk-8***7Hsd`），日志里也打码。

### 4. 起服务

```powershell
# 后端（⚠️ 必须单进程，不要加 --workers）
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（另开一个终端）
cd frontend
pnpm dev
```

- 前端 http://127.0.0.1:5173
- 后端 http://127.0.0.1:8000/docs（Swagger，可直接试接口）

或者一条命令起两边：

```powershell
powershell -File scripts/dev.ps1
```

### 5. 用起来

1. 打开「模型供应商」，三类各点一次「测试」，确认连通
2. 打开「文档库」，拖一个文件进去
3. 点「生成分块预览」，调整策略和块大小，看着顺眼就「确认入库」
4. 入库完成后会自动排队打标签
5. 打开「对话」提问，比如「住宿标准是多少」

---

## Docker 部署

```powershell
docker compose up --build
# → http://127.0.0.1:8080
```

数据落在宿主机的 `./data`，删容器不丢数据。

> **Docker 必须单进程**：Chroma 的嵌入式客户端底层是 SQLite，
> 多进程同时打开会丢写入。这是本项目最容易踩的坑（见下）。

---

## 常见问题

### 统计里 `vectors` 是 0，但分块明明入库了

**Chroma 嵌入式库不能被多个进程同时打开。** 典型触发方式：

- 一边跑 `uvicorn`，一边用另一个脚本 `chromadb.PersistentClient(path="data/chroma")`
- 用 `sqlite3` **只读**打开 `data/chroma/chroma.sqlite3`（只读也会破坏）
- 跑 `uvicorn --workers 2`
- **跑 `pytest`** —— 以前有测试会真的去开 `data/chroma`（已修，见下）

**排查**：先停后端，再确认目录干净，然后走页面上的「重建索引」。
以后想查向量数量，只走 `GET /api/kbs/1/stats`，别碰那个 sqlite 文件。

### 接口报「连接失败」，但网络明明是通的

Windows 注册表里的系统代理可能是坏的。本项目所有 HTTP 客户端都设了
`trust_env=False`，只认 `.env` 里的 `APP_HTTP_PROXY`（默认直连）。

排查口诀：先用 `curl --noproxy '*'` 试直连，能通就说明是代理问题。

### 时间显示早了 8 小时

已修。根因是 SQLite 的 `DateTime(timezone=True)` 不保存时区，
接口返回的 ISO 串不带 `Z`，浏览器按本地时区解析。
现在所有时间序列化都带 `Z`（见 `core/timeutil.py`）。

### 模型算数算错了

模型算数不可靠是**机制问题**（按 token 概率生成），不是它「不够聪明」。
本项目内置了 `calculator` 工具（`Decimal` 精确算术），并且：
当问题里有算术信号、模型却没调工具时，**代码会强制补一轮**逼它调。
所以你会看到轨迹面板里出现「工具调用」这一步。

### PDF 上传后没有内容

可能是**扫描件**（图片版 PDF）。本项目**不做 OCR**，
需要先用其他工具转成文字版再上传。系统会明确提示你这一点。

### 改了提示词，效果变差了

提示词库里的**内置模板是只读的**。要改请先「另存为副本」，
再把副本设为默认。改坏了可以「重置」回内置原文，
或直接把默认切回内置那条。

---

## 项目结构

```
backend/app/
  api/        路由层（薄）：providers documents chunk_samples chat jobs tags prompts
  services/   业务层（厚）：parsing chunking documents indexing retrieval chat
              tagging tools prompts promptopt model_choice llm_parse tokenizer
  workers/    asyncio 任务队列：queue handlers
  providers/  模型适配：base（三协议）embed llm registry
  graph/      LangGraph 图：state nodes build
  core/       errors（含统一文案）logging net security timeutil
  db/         models session seed
frontend/src/
  views/      Dashboard Documents DocumentDetail Chat Providers Prompts Settings
  components/ TracePanel StrategySampleModal
  api/        每个接口一个文件，组件里禁止直接 fetch
  stores/     Pinia：一个 store 一个业务域
docs/         DESIGN.md MILESTONES.md HANDOVER.md
scripts/      dev.ps1 reset.ps1 migrate.py
```

依赖方向是**单向**的：`api → services → (providers | db | graph)`。
反向 import 一律禁止。

---

## 开发约定

完整规范见 `AGENTS.md`，几条硬性的：

- **不擅自升级依赖**。Lock 在 `backend/requirements.txt` 与 `frontend/package.json`。
- **不用 `print`**，一律 `logger`。
- **密钥不进日志、不进响应**。
- **不信任模型的 JSON 输出**，必须过 Schema 校验 + 失败回退。
- **不做假实现**（例如 rerank 直接返回原顺序）。

```powershell
# 提交前自查
cd backend
ruff check .
ruff format --check .
pytest -q

cd ../frontend
pnpm build        # 含 vue-tsc 类型检查
```

---

## 已经做到哪一步

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 供应商配置、UI 骨架 | ✅ |
| M1 | 文档解析与分块（8 种策略） | ✅ |
| M2 | 混合检索 + 对话 + 引用溯源 + 轨迹面板 | ✅ |
| M3 | 异步任务队列、标签分类、内置工具、提示词库、设置页、错误文案 | ✅ |
| M3 | Docker、README | 🚧 |
| M4 | RAG 实验室（`/lab`） | 未开始 |

细节与踩坑记录见 `docs/MILESTONES.md` 与 `docs/HANDOVER.md`。
**HANDOVER 的「踩过的坑」那一节最值钱**，动手前建议先读一遍。

---

## 文档

| 文件 | 内容 |
|---|---|
| [docs/DESIGN.md](docs/DESIGN.md) | 设计文档：范围、数据模型、核心流程、接口清单 |
| [docs/MILESTONES.md](docs/MILESTONES.md) | 任务清单与进度 |
| [docs/HANDOVER.md](docs/HANDOVER.md) | 交接文档，含 20 条踩坑记录 |
| [AGENTS.md](AGENTS.md) | 开发规范（强制约束） |