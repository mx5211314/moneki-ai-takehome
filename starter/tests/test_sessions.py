"""会话层回归测试：不同 session_id 必须隔离，追问必须能接住上文。

这是第三关的“会红的测试”。缺陷是分层的：

第一层 —— SessionStore 收了 session_id 却从不使用它，所有会话共用一份
    _turns。于是 history("s2") 会看到 s1 的内容，直接违反第三关硬性要求
    “不同 session_id 之间不能串线”。

第二层（修完第一层才真正暴露）—— service._answer 取到了 history，却只
    把它交给 live 模式的 engine；mock 模式下 planner.plan(question) 拿不到
    历史，planner 里“没有 history 的追问”分支就把“那 7 月呢？”判成 clarify
    拒答。这一层现在把第一层盖住了：因为压根不传 history，所以暂时看不出
    串线，得先修隔离、再把 history 接上，才看得见追问到底能不能还原。

gold 来自 eval/public_questions.jsonl 的 T01（6 月 156757、7 月 162414）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # starter/
sys.path.insert(0, str(ROOT))

from kbqa.sessions import SessionStore  # noqa: E402
from kbqa.service import Service  # noqa: E402

#: T01 第二轮“那 7 月呢？”的期望净营业额。
JULY_NET = 162414.0


def _turn(question: str) -> dict:
    return {
        "question": question,
        "standalone": question,
        "slots": {},
        "answer": "",
        "answer_type": "data",
    }


# -- 第一层：SessionStore 按 session_id 隔离 ------------------------------------


def test_history_of_unknown_session_is_empty() -> None:
    store = SessionStore()
    store.append("s1", _turn("6 月的净营业额是多少？"))
    assert store.history("s2") == [], "s2 从没说过话却看到了 s1 的历史：session_id 没起作用"


def test_sessions_keep_their_own_turns() -> None:
    store = SessionStore()
    store.append("s1", _turn("问A"))
    store.append("s2", _turn("问B"))
    assert [t["question"] for t in store.history("s1")] == ["问A"]
    assert [t["question"] for t in store.history("s2")] == ["问B"]


# -- 第二层：端到端追问 --------------------------------------------------------


@pytest.fixture(scope="module")
def service() -> Service:
    return Service()


def _has_number(payload: dict, value: float) -> bool:
    text = (payload.get("answer") or "").replace(",", "")
    if str(int(value)) in text:
        return True
    for item in payload.get("data_evidence") or []:
        result = item.get("result") or {}
        if isinstance(result, dict):
            try:
                if abs(float(result.get("net_revenue", 0.0)) - value) < 0.01:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def test_followup_resolves_with_history(service: Service) -> None:
    """T01 前两轮：先问 6 月，再问“那 7 月呢？”应当给出 7 月的数。"""
    first = service.chat("sess-followup-1", "6 月的净营业额是多少？")
    assert first["answer_type"] in ("data", "hybrid"), first
    second = service.chat("sess-followup-1", "那 7 月呢？")
    assert second["answer_type"] in ("data", "hybrid"), second
    assert _has_number(second, JULY_NET), "追问没接住上文（planner 没拿到 history）：%s" % second


def test_sessions_do_not_leak(service: Service) -> None:
    """另一个 session 里的“那 7 月呢？”没有上文，不该冒出 7 月的数。"""
    service.chat("sess-isolate-a", "6 月的净营业额是多少？")
    other = service.chat("sess-isolate-b", "那 7 月呢？")
    assert not _has_number(other, JULY_NET), "两个 session 串线了：%s" % other
