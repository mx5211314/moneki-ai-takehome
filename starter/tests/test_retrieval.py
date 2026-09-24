"""检索回归测试：公开题库 R01-R15 的 gold 文档必须进 top_k=5，且恰好返回 5 条。

这是第二关的“会红的测试”：在修 loader / tokenizer / retriever 之前，
中文问句因为分词只按空白切而无法匹配，.txt/.html 文档因为后缀白名单被漏掉，
所以这批用例必然红；修完之后应当全绿。

只依赖标准库 + 项目内 kbqa 模块，不拉 fastapi，直接构造 Retriever 跑。
gold 来自 eval/public_questions.jsonl（评审 oracle），写死在这里当回归网。
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]          # starter/
REPO = ROOT.parent                                  # 作业包根
KB_DIR = REPO / "knowledge_base"
QUESTIONS = REPO / "eval" / "public_questions.jsonl"

sys.path.insert(0, str(ROOT))

from kbqa.retriever import Retriever, build_retriever  # noqa: E402


def _load_retrieval_cases() -> list[dict]:
    cases = []
    for line in QUESTIONS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        item = json.loads(line)
        if item.get("category") == "retrieval":
            cases.append(item)
    return cases


RETRIEVAL_CASES = _load_retrieval_cases()
TODAY = date(2026, 9, 1)


@pytest.fixture(scope="module")
def retriever(tmp_path_factory) -> Retriever:
    index_path = tmp_path_factory.mktemp("idx") / "index.json"
    # rebuild=True：绕过仓库里可能过期的 .cache/index.json，按当前代码重建。
    return build_retriever(KB_DIR, index_path, TODAY, rebuild=True)


def _top_doc_ids(retriever: Retriever, query: str, top_k: int = 5) -> list[str]:
    return [hit.doc_id for hit in retriever.search(query, top_k=top_k).hits]


@pytest.mark.parametrize("case", RETRIEVAL_CASES, ids=lambda c: c["id"])
def test_retrieval_gold_in_topk(retriever: Retriever, case: dict) -> None:
    top_k = case.get("top_k", 5)
    doc_ids = _top_doc_ids(retriever, case["query"], top_k)

    # 契约 §4：片段够时必须恰好返回 top_k 条（先过滤再取 top-k 的实现会得到不足的数）。
    assert len(doc_ids) == top_k, (
        "返回 %d 条，应为 %d（top_k 不足说明过滤在取 top-k 之后才做，或索引文档不全）：%s"
        % (len(doc_ids), top_k, doc_ids)
    )

    gold_all = case.get("gold_all") or []
    missing_all = [d for d in gold_all if d not in doc_ids]
    assert not missing_all, "gold_all 缺失 %s，top-%d=%s" % (missing_all, top_k, doc_ids)

    gold_any = case.get("gold_any") or []
    if gold_any:
        hit_any = [d for d in gold_any if d in doc_ids]
        assert hit_any, "gold_any 一个都没命中 %s，top-%d=%s" % (gold_any, top_k, doc_ids)
