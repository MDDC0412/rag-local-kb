# 后端镜像：FastAPI + uvicorn，单进程（Chroma 嵌入式库不能多进程打开）
FROM python:3.11-slim

# 时区设成东八区，否则日志时间比本地早 8 小时
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 允许构建期覆盖 PyPI 镜像源，例如内网源或国内加速源
ARG PIP_INDEX_URL=https://pypi.org/simple

# 编译依赖：chromadb / tiktoken 有些包需要 gcc；装完就删掉，镜像别背这个包袱
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# 先只拷依赖清单：改代码不会让这一层失效，重建快很多
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements.txt \
    && apt-get purge -y gcc g++ \
    && apt-get autoremove -y

# 预热 tiktoken 的编码文件：不预下载的话，首次分词要联网拉 ~1.7MB
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

# jieba 的词典也预热一下，避免第一次检索卡 1 秒
RUN python -c "import jieba; jieba.initialize()"

COPY backend/app ./app

# 数据目录挂卷（app.db / chroma / uploads / logs / .secret 都在这里）
ENV APP_DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8000

# 必须单进程：Chroma 的 PersistentClient 底层是 SQLite，多进程会丢写入
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]