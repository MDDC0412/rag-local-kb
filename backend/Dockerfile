# 后端镜像：FastAPI + uvicorn，单进程（Chroma 嵌入式库不能多进程打开）
#
# 依赖均为预编译 wheel（chromadb / tiktoken / grpcio 在 PyPI 与 PyPI 镜像上
# 都有 manylinux 包），因此不安装 gcc/g++：省去 apt 联网步骤，构建更快、
# 镜像更小。若将来出现需要源码编译的依赖，再按需补回编译工具并清理。

FROM python:3.11-slim

# 时区设成东八区，否则日志时间比本地早 8 小时
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 允许构建期覆盖 PyPI 镜像源（内网源或加速源）
ARG PIP_INDEX_URL=https://pypi.org/simple

# 先只拷依赖清单：改代码不会让这一层失效，重建更快
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements.txt

# 预热 tiktoken 编码文件，避免首次分词联网下载
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# 预热 jieba 词典，避免首次检索慢约 1 秒
RUN python -c "import jieba; jieba.initialize()"

COPY backend/app ./app

# 数据目录挂卷（app.db / chroma / uploads / logs / .secret 均在此）
ENV APP_DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8000

# 必须单进程：Chroma 的 PersistentClient 底层是 SQLite，多进程会丢写入
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
