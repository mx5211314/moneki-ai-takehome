"""测试夹具。

这里只做两件事：把运行期产物（清洗库）指到临时目录，以及清掉可能存在的
大模型环境变量，保证接口测试跑在 mock 降级模式下。

注意：早期版本在这里把 Retriever.search 整个换成固定返回——一个永远只返回
一条假命中的假实现。那是**类级别**的猴子补丁，又挂在 session 作用域的
fixture 上，于是污染同一次 pytest 里所有后续测试：连真实检索的回归测试拿
到的也是那个假实现。接口测试因此从来没真正验证过检索链路，starter 才能
“自带测试全绿”却答非所问。现在接口测试走真实检索。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    os.environ["VAR_DIR"] = str(tmp_path_factory.mktemp("var"))
    for key in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        os.environ.pop(key, None)

    from fastapi.testclient import TestClient

    from kbqa import server

    return TestClient(server.app)
