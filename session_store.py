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
    {
        "id": "comm_drill",
        "mode": "roleplay",
        "title": "コミュニケーション専門ノック（状況把握→筋道→的確回答）",
        "default_instructions": (
            "あなたは会話相手としてロールプレイしつつ、ユーザーの『状況把握→整理→回答』を鍛えるコーチでもあります。"
            "最初に、現実的なシーンを1つ提示してください（例：圧の強い顧客、上司への説明、部下の相談、他部署調整など）。"
            "シーン提示は短く、前提・制約・相手の感情を含めてください。"
            "ユーザーが回答したら、相手として自然に返し、必要なら追加情報を少しずつ出し、追加質問や反論で揺さぶってください。"
            "ユーザーの回答が曖昧なときは、結論・根拠・次アクションが揃うように1つだけ質問して具体化を促してください。"
            "会話はリアルな日本語で、1ターンは短めに。不要に長い講義や箇条書きの解説はしないでください。"
            "ユーザーが『一旦まとめて』や『整理して』と言った場合のみ、3点以内で要点を整理してから続けてください。"
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
        self._feedback: Dict[str, Any] = {}  # 追加：フィードバック保存用
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

    # ---- feedback ----
    def save_feedback(self, session_id: str, payload: Any) -> bool:
        with self._lock:
            if session_id not in self._sessions:
                return False
            self._feedback[session_id] = payload
        return True

    def get_feedback(self, session_id: str) -> Any:
        with self._lock:
            return self._feedback.get(session_id)

    # ---- STG-002 ----
    def delete_feedback(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._feedback:
                del self._feedback[session_id]
        return True

    def list_feedback_sessions(self, limit: int = 200) -> List[Tuple[str, str, int, str]]:
        """
        生成済フィードバックが存在するセッションの一覧
        return: [(session_id, title, created_at, mode), ...]
        """
        with self._lock:
            ids = list(self._feedback.keys())

            out: List[Tuple[str, str, int, str]] = []
            for sid in ids:
                meta = self._sessions.get(sid)
                if not meta:
                    continue
                out.append((sid, meta.title, meta.created_at, meta.mode))

        out.sort(key=lambda x: x[2], reverse=True)
        return out[:limit]


class SQLiteSessionStore:
    """
    SQLite に永続化するストア。
    InMemorySessionStore と同じ I/F を維持し、最小差分で差し替えできるようにする。
    """
    def __init__(self, db_path: str = "app.db", scenarios: Optional[List[Dict[str, Any]]] = None):
        import sqlite3
        self._scenarios = scenarios or DEFAULT_SCENARIOS
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    scenario_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    title TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS transcripts (
                    session_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    session_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at DESC);")
            self._conn.commit()

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
            self._conn.execute(
                "INSERT INTO sessions(session_id, scenario_id, mode, title, instructions, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (meta.session_id, meta.scenario_id, meta.mode, meta.title, meta.instructions, meta.created_at)
            )
            self._conn.commit()
        return meta

    def get_session(self, session_id: str) -> Optional[SessionMeta]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT session_id, scenario_id, mode, title, instructions, created_at FROM sessions WHERE session_id=?",
                (session_id,)
            )
            row = cur.fetchone()
        if not row:
            return None
        return SessionMeta(
            session_id=row[0],
            scenario_id=row[1],
            mode=row[2],
            title=row[3],
            instructions=row[4],
            created_at=int(row[5]),
        )

    def list_sessions(self, limit: int = 50) -> List[SessionMeta]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT session_id, scenario_id, mode, title, instructions, created_at FROM sessions ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )
            rows = cur.fetchall()
        return [
            SessionMeta(
                session_id=r[0],
                scenario_id=r[1],
                mode=r[2],
                title=r[3],
                instructions=r[4],
                created_at=int(r[5]),
            )
            for r in rows
        ]

    # ---- logs ----
    def save_transcript(self, session_id: str, payload: Dict[str, Any]) -> bool:
        import json
        with self._lock:
            cur = self._conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,))
            if not cur.fetchone():
                return False
            payload_json = json.dumps(payload, ensure_ascii=False)
            self._conn.execute(
                "INSERT INTO transcripts(session_id, payload_json) VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET payload_json=excluded.payload_json",
                (session_id, payload_json)
            )
            self._conn.commit()
        return True

    def get_transcript(self, session_id: str) -> Optional[Dict[str, Any]]:
        import json
        with self._lock:
            cur = self._conn.execute("SELECT payload_json FROM transcripts WHERE session_id=?", (session_id,))
            row = cur.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    # ---- feedback ----
    def save_feedback(self, session_id: str, payload: Any) -> bool:
        import json
        with self._lock:
            cur = self._conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,))
            if not cur.fetchone():
                return False
            payload_json = json.dumps(payload, ensure_ascii=False)
            self._conn.execute(
                "INSERT INTO feedback(session_id, payload_json) VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET payload_json=excluded.payload_json",
                (session_id, payload_json)
            )
            self._conn.commit()
        return True

    def get_feedback(self, session_id: str) -> Any:
        import json
        with self._lock:
            cur = self._conn.execute("SELECT payload_json FROM feedback WHERE session_id=?", (session_id,))
            row = cur.fetchone()
        if not row:
            return None
        try:
            return json.loads(row[0])
        except Exception:
            return None

    # ---- STG-002 ----
    def delete_feedback(self, session_id: str) -> bool:
        with self._lock:
            self._conn.execute("DELETE FROM feedback WHERE session_id=?", (session_id,))
            self._conn.commit()
        return True

    def list_feedback_sessions(self, limit: int = 200) -> List[Tuple[str, str, int, str]]:
        """
        生成済フィードバックが存在するセッションの一覧
        return: [(session_id, title, created_at, mode), ...]
        """
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT s.session_id, s.title, s.created_at, s.mode
                FROM sessions s
                INNER JOIN feedback f ON f.session_id = s.session_id
                ORDER BY s.created_at DESC
                LIMIT ?
                """,
                (limit,)
            )
            rows = cur.fetchall()
        return [(r[0], r[1], int(r[2]), r[3]) for r in rows]
