# session_store.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any, List, Tuple
import time
import threading
from uuid import uuid4

# ---- シナリオ定義（ここに集約） ----
DEFAULT_SCENARIOS = [
    {
        "id": "free_talk",
        "mode": "basic",
        "title": "フリートーク（練習）",
        "default_instructions": "あなたは親切で有能なアシスタントです。応答は簡潔に。"
    },
    {
        "id": "report_to_boss",
        "mode": "roleplay",
        "title": "上司への進捗報告（結論先）",
        "default_instructions": (
            "あなたは穏やかな上司役です。"
            "ユーザーの報告を聞き、要点が曖昧なら具体化を促してください。"
            "最後に1つだけ改善点を短く述べてください。"
        )
    },
    {
        "id": "client_hearing",
        "mode": "roleplay",
        "title": "顧客ヒアリング（深掘り質問）",
        "default_instructions": (
            "あなたは顧客役です。最初は要件が曖昧です。"
            "ユーザーの質問に対して情報を少しずつ出します。"
            "ユーザーがオープン質問を使えるように促してください。"
        )
    },
]

@dataclass
class SessionMeta:
    session_id: str
    scenario_id: str
    mode: str
    title: str
    instructions: str
    created_at: int

class InMemorySessionStore:
    """
    セッションID・シナリオ・履歴・ログ保存を担う最小ストア。
    後でSQLite版に差し替えても、同じI/Fで移行できるようにしています。
    """
    def __init__(self, scenarios: Optional[List[Dict[str, Any]]] = None):
        self._scenarios = scenarios or DEFAULT_SCENARIOS
        self._sessions: Dict[str, SessionMeta] = {}
        self._logs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ---- scenario ----
    def list_modes(self) -> List[str]:
        return sorted({s["mode"] for s in self._scenarios})

    def list_scenarios(self, mode: Optional[str] = None) -> List[Dict[str, Any]]:
        if not mode:
            return list(self._scenarios)
        return [s for s in self._scenarios if s["mode"] == mode]

    def find_scenario(self, scenario_id: str) -> Optional[Dict[str, Any]]:
        for s in self._scenarios:
            if s["id"] == scenario_id:
                return s
        return None

    # ---- session ----
    def create_session(self, scenario_id: str = "free_talk", instructions_override: Optional[str] = None) -> SessionMeta:
        s = self.find_scenario(scenario_id) or self.find_scenario("free_talk")
        assert s is not None

        sid = f"sess_{uuid4().hex}"
        instr = (instructions_override or s["default_instructions"]).strip()
        meta = SessionMeta(
            session_id=sid,
            scenario_id=s["id"],
            mode=s["mode"],
            title=s["title"],
            instructions=instr,
            created_at=int(time.time())
        )
        with self._lock:
            self._sessions[sid] = meta
        return meta

    def get_session(self, session_id: str) -> Optional[SessionMeta]:
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions(self, limit: int = 50) -> List[SessionMeta]:
        with self._lock:
            arr = list(self._sessions.values())
        arr.sort(key=lambda x: x.created_at, reverse=True)
        return arr[:limit]

    # ---- logs ----
    def save_transcript(self, session_id: str, payload: Dict[str, Any]) -> bool:
        """
        payload例:
          { "ended_at": 123, "transcript":[{"role":"user","text":"...","ts":...}, ...] }
        """
        with self._lock:
            if session_id not in self._sessions:
                return False
            self._logs[session_id] = payload
        return True

    def get_transcript(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._logs.get(session_id)
