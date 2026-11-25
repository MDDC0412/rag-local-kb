"""pytest 全局配置：把测试隔离到临时数据目录。

**为什么必须这么做**：Chroma 的嵌入式客户端底层是 SQLite，同一个数据目录
被两个进程打开就会导致写入丢失（见 HANDOVER §7.1）。而
``test_save_drafts_replaces_confirmed_chunks`` 会调用
``indexing.delete_document_vectors()``，它会真的去开 ``data/chroma`` ——
于是「跑一次测试」就能把开发中后端进程里的向量清空。

这里在导入任何 app 模块之前，把 ``APP_DATA_DIR`` 指到临时目录，
测试就再也碰不到真实的 ``data/``（app.db / chroma / uploads / logs 全部隔离）。
"""

from __future__ import annotations

import os
import tempfile

_TEST_DATA_DIR = tempfile.mkdtemp(prefix="rag-tests-")
os.environ["APP_DATA_DIR"] = _TEST_DATA_DIR
