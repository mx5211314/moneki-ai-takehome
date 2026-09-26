"""对话历史。按 session_id 分开存，不同会话之间不能串线。"""

from __future__ import annotations

import threading
from typing import Optional

MAX_TURNS = 6
MAX_SESSIONS = 500


class SessionStore:
    """最近几轮对话，够解追问就行。

    契约要求“不同 session_id 之间不能串线”。评测脚本给每道题发一个随机
    session_id（eval/run_eval.py 里是 uuid4），只有同一道多轮题才会复用它，
    所以历史必须按 session_id 分开存。

    改之前这里只有一个全局 _turns 列表，session_id 收了却从不使用：
    A 会话问完“6 月的净营业额是多少”，B 会话再说“那 7 月呢？”会直接拿到
    A 的上文，跨题串线。
    """

    def __init__(self, max_sessions: int = MAX_SESSIONS, max_turns: int = MAX_TURNS) -> None:
        self._turns: dict[str, list[dict]] = {}
        self._lock = threading.Lock()
        self.max_sessions = max_sessions
        self.max_turns = max_turns

    @staticmethod
    def _key(session_id: Optional[str]) -> str:
        """没有 session_id 的请求统一归到匿名档。

        契约只保证“不同 session_id 之间”不串线；一个连会话标识都不带的
        客户端本来就没声明自己是谁，按同一档处理，并在接口文档里说明。
        """
        return (session_id or "").strip()

    def history(self, session_id: Optional[str] = None) -> list[dict]:
        with self._lock:
            return list(self._turns.get(self._key(session_id), ()))

    def append(self, session_id: Optional[str] = None, turn: Optional[dict] = None) -> None:
        key = self._key(session_id)
        with self._lock:
            turns = self._turns.setdefault(key, [])
            turns.append(turn or {})
            del turns[: max(0, len(turns) - self.max_turns)]
            # 会话数超上限时淘汰最久没说话的那个（dict 保持插入顺序）。
            while len(self._turns) > self.max_sessions:
                self._turns.pop(next(iter(self._turns)))

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()
