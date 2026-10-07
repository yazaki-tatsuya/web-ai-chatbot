# eventlet.monkey_patch() は最初に呼び出す必要があります
import eventlet
eventlet.monkey_patch()

import os
from dotenv import load_dotenv
load_dotenv()  # .env を読み込む（ローカル用）

import json
import threading
import base64
from datetime import datetime, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from flask_socketio import SocketIO, emit
import websocket
import queue
from functools import wraps

# 追加
from session_store import SQLiteSessionStore
store = SQLiteSessionStore(os.environ.get("SQLITE_PATH") or "app.db")

# Flaskアプリケーションの設定
app = Flask(__name__)

try:
    app.json.ensure_ascii = False   # Flask 2.2+ 系
except Exception:
    app.config['JSON_AS_ASCII'] = False  # 旧Flask互換
    
app.config['SECRET_KEY'] = 'secret!'
app.config['SESSION_COOKIE_SAMESITE'] = 'Strict'
app.config['SESSION_COOKIE_HTTPONLY'] = True
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# ============================================================
# SEC-001: 簡易認可（PIN）
#  - APP_PIN が設定されている場合のみ有効
#  - 未設定なら従来通り（すべて許可）で挙動を変えない
# ============================================================
def _auth_enabled():
    return bool(os.environ.get("APP_PIN"))

def _is_authorized():
    if not _auth_enabled():
        return True
    return session.get("authorized") is True

def _unauthorized_response():
    return jsonify({"ok": False, "error": "unauthorized", "login_url": url_for("login", next=request.path)}), 401

def require_auth(fn):
    @wraps(fn)
    def _wrapper(*args, **kwargs):
        if _is_authorized():
            return fn(*args, **kwargs)
        return _unauthorized_response()
    return _wrapper

@app.route("/login", methods=["GET", "POST"])
def login():
    # APP_PIN 未設定なら常に許可（既存のローカル運用を崩さない）
    if not _auth_enabled():
        session["authorized"] = True
        return redirect(request.form.get("next") or request.args.get("next") or url_for("home"))

    if request.method == "POST":
        pin = request.form.get("pin") or ""
        if pin == os.environ.get("APP_PIN"):
            session["authorized"] = True
            return redirect(request.form.get("next") or request.args.get("next") or url_for("home"))
        return "<h3>Unauthorized</h3><p>PIN is incorrect.</p>", 401

    next_url = request.args.get("next") or url_for("home")
    return (
        "<h2>Login</h2>"
        "<form method='post'>"
        f"<input type='hidden' name='next' value='{next_url}'>"
        "<div><input name='pin' type='password' placeholder='PIN'></div>"
        "<div><button type='submit'>Login</button></div>"
        "</form>"
    )

@app.route("/logout")
def logout():
    try:
        session.clear()
    except Exception:
        session["authorized"] = False
    return redirect(url_for("home"))

# OpenAI用の環境変数取得
key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPEN_AI_KEY")
REALTIME_MODEL = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime-2.1")
REALTIME_VOICE = os.environ.get("OPENAI_REALTIME_VOICE", "marin")
url = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"

# ============================================================
# LEGACY: OpenAI Realtime WebSocket経路（Socket.IO連携）を使うか
#  - 現在の推奨はブラウザはWebRTC直結（practice.html）なのでデフォルトOFF
#  - 必要な場合のみ環境変数 ENABLE_LEGACY_OPENAI_WS=1 で有効化
# ============================================================
ENABLE_LEGACY_OPENAI_WS = os.environ.get("ENABLE_LEGACY_OPENAI_WS", "0") == "1"

# クライアントごとの状態を管理する辞書
client_states = {}

def init_client_state(sid):
    client_states[sid] = {
        "audio_receive_queue": queue.Queue(),
        "audio_worker_started": False,
        "audio_worker_lock": threading.Lock(),
        "ws_connection": None,
        "ws_lock": threading.Lock(),
        "user_transcription_buffer": "",
        "last_ai_message": "",
        "current_turn": 0,
        "ai_transcription_buffer": "",
        "audio_pcm_buffer": bytearray(),  # AI音声PCMバッファを初期化
    }

def cleanup_client_state(sid):
    if sid in client_states:
        del client_states[sid]

def _make_session_view(meta, session_id=None):
    """
    templates 側（practice.html / feedback.html）が期待する
    session.id / session.scenario_title 形式に合わせて
    session.id / session.scenario_title 形式に合わせて session を追加で渡す
    """
    if not meta and not session_id:
        return None

    sid = session_id
    title = ""

    if meta:
        # meta がオブジェクトでもdictでも落ちないように
        try:
            sid = sid or getattr(meta, "session_id", None) or getattr(meta, "id", None)
        except Exception:
            pass
        try:
            title = getattr(meta, "title", "") or getattr(meta, "scenario_title", "")
        except Exception:
            pass
        if isinstance(meta, dict):
            sid = sid or meta.get("session_id") or meta.get("id")
            title = title or meta.get("title") or meta.get("scenario_title") or ""

    return {
        "id": sid,
        "scenario_title": title,
    }

# ▼▼▼ 追加：templates（modes.html / scenarios.html / history.html）向けの薄い変換 ▼▼▼
JST = timezone(timedelta(hours=9))

def _make_scenario_view(s):
    """
    scenarios.html が期待する focus / duration_sec を補完する薄い変換。
    store 側が未定義でもテンプレ側で落ちないようにする。
    """
    if isinstance(s, dict):
        v = dict(s)
    else:
        v = {}
        try:
            v["id"] = getattr(s, "id", None)
        except Exception:
            admit = None
            v["id"] = None
        try:
            v["title"] = getattr(s, "title", "")
        except Exception:
            v["title"] = ""
        try:
            v["default_instructions"] = getattr(s, "default_instructions", "")
        except Exception:
            v["default_instructions"] = ""

    if "focus" not in v or v.get("focus") is None:
        v["focus"] = []
    if not isinstance(v.get("focus"), list):
        v["focus"] = [str(v.get("focus"))] if v.get("focus") else []

    if "duration_sec" not in v or v.get("duration_sec") is None:
        v["duration_sec"] = 300

    return v

def _make_mode_view(mode):
    scenarios = [_make_scenario_view(x) for x in store.list_scenarios(mode)]
    return {"mode": mode, "scenarios": scenarios}

def _format_created_at(val):
    """
    history.html の created_at 表示用。
    - epoch秒（int/float/数字文字列）なら JST に変換して "YYYY-MM-DD HH:MM"
    - それ以外の文字列ならそのまま表示
    """
    if val is None:
        return ""
    try:
        # 数字（epoch秒）として解釈できるならそれを優先
        ts = int(float(val))
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(JST)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    try:
        return str(val)
    except Exception:
        return ""
# ▲▲▲ 追加ここまで ▲▲▲

@app.route('/')
def index():
    session_id = request.args.get("session_id")
    meta = store.get_session(session_id) if session_id else None
    if not meta:
        meta = store.create_session("free_talk")  # 直アクセスでも壊さない
        session_id = meta.session_id

    # ★追加：templates が期待する session も渡す（既存変数は維持）
    session_view = _make_session_view(meta, session_id=session_id)

    return render_template(
        'practice.html',
        session_id=session_id,
        scenario_title=meta.title,
        instructions=meta.instructions,
        session=session_view
    )

# ★追加：feedback.html の「同じシナリオで再挑戦」リンク対応
@app.route('/practice/<session_id>')
def practice(session_id):
    meta = store.get_session(session_id) if session_id else None
    if not meta:
        meta = store.create_session("free_talk")  # 壊さない
        session_id = meta.session_id

    session_view = _make_session_view(meta, session_id=session_id)

    return render_template(
        'practice.html',
        session_id=session_id,
        scenario_title=meta.title,
        instructions=meta.instructions,
        session=session_view
    )

@app.route("/home")
def home():
    return render_template("home.html")

@app.route("/modes")
def modes():
    return render_template("modes.html", modes=[_make_mode_view(m) for m in store.list_modes()])

@app.route("/shelves")
def shelves():
    mode = request.args.get("mode")
    if not mode:
        mode = "basic"
    shelves = store.list_shelves(mode) if hasattr(store, "list_shelves") else []
    if len(shelves) <= 1:
        if shelves and shelves[0].get("shelf_id") and shelves[0].get("shelf_id") != "UNSPECIFIED":
            return redirect(f"/scenarios?mode={mode}&shelf_id={shelves[0].get('shelf_id')}")
        return redirect(f"/scenarios?mode={mode}")
    return render_template("shelves.html", shelves=shelves, mode=mode)

@app.route("/scenarios")
def scenarios():
    mode = request.args.get("mode")
    if not mode:
        mode = "basic"
    shelf_id = request.args.get("shelf_id")
    shelf_title = None
    if shelf_id and hasattr(store, "list_shelves"):
        try:
            for sh in store.list_shelves(mode):
                if sh.get("shelf_id") == shelf_id:
                    shelf_title = sh.get("shelf_title")
                    break
        except Exception:
            shelf_title = None
    return render_template("scenarios.html", scenarios=[_make_scenario_view(s) for s in store.list_scenarios(mode, shelf_id=shelf_id)], mode=mode, shelf_id=shelf_id, shelf_title=shelf_title)

@app.post("/session/start")
def session_start():
    scenario_id = request.form.get("scenario_id", "free_talk")
    instructions = (request.form.get("instructions") or "").strip() or None
    meta = store.create_session(scenario_id, instructions)
    return redirect(url_for("index", session_id=meta.session_id))

@app.route("/history")
def history():
    # UI改善（溜まり過ぎ対策）：/history をページング表示
    # - 既存の store.list_sessions() はそのまま利用し、ここでスライスする
    # - クエリ: ?page=1&page_size=10
    try:
        page = int(request.args.get("page", "1") or "1")
    except Exception:
        page = 1
    try:
        page_size = int(request.args.get("page_size", "10") or "10")
    except Exception:
        page_size = 10

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 10
    if page_size > 200:
        page_size = 200

    all_sessions = list(store.list_sessions())
    total = len(all_sessions)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    start = (page - 1) * page_size
    end = start + page_size
    page_sessions = all_sessions[start:end]

    sessions_view = []
    for s in page_sessions:
        # s が dict / object どちらでも落ちないように
        sid = ""
        title = ""
        created_at = None
        mode = ""

        if isinstance(s, dict):
            sid = s.get("id") or s.get("session_id") or ""
            title = s.get("scenario_title") or s.get("title") or ""
            created_at = s.get("created_at")
            mode = s.get("mode") or ""
        else:
            try:
                sid = getattr(s, "id", "") or getattr(s, "session_id", "")
            except Exception:
                sid = ""
            try:
                title = getattr(s, "scenario_title", "") or getattr(s, "title", "")
            except Exception:
                title = ""
            try:
                created_at = getattr(s, "created_at", None)
            except Exception:
                created_at = None
            try:
                mode = getattr(s, "mode", "") or ""
            except Exception:
                mode = ""

        sessions_view.append({
            "id": sid,
            "scenario_title": title,
            "created_at": _format_created_at(created_at),
            "mode": mode,
        })

    return render_template(
        "history.html",
        sessions=sessions_view,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        has_prev=(page > 1),
        has_next=(page < total_pages),
    )

# ▼▼▼ STG-002: 生成済フィードバック一覧 ▼▼▼
@app.route("/feedbacks")
def feedbacks():
    items = []
    for sid, title, created_at, mode in store.list_feedback_sessions():
        items.append({
            "id": sid,
            "scenario_title": title,
            "created_at": _format_created_at(created_at),
            "mode": mode,
        })
    return render_template("feedbacks.html", feedbacks=items)
# ▲▲▲ 追加ここまで ▲▲▲

@app.route("/feedback/<session_id>")
def feedback(session_id):
    meta = store.get_session(session_id)
    log = store.get_transcript(session_id)

    # ★追加：feedback.html が期待する変数名に合わせて渡す（既存は残す）
    session_view = _make_session_view(meta, session_id=session_id)
    transcript = log
    feedback_data = store.get_feedback(session_id)  # ★変更：保存済みフィードバックを取得

    return render_template(
        "feedback.html",
        meta=meta,
        log=log,
        session_id=session_id,
        session=session_view,
        transcript=transcript,
        feedback=feedback_data
    )

@app.post("/api/session/<session_id>/transcript")
@require_auth
def api_save_transcript(session_id):
    payload = request.get_json(force=True)
    ok = store.save_transcript(session_id, payload)
    return jsonify({"ok": ok}), (200 if ok else 404)

# ▼▼▼ 追加：フィードバック生成API（最小差分で追加） ▼▼▼
def _generate_feedback_with_openai(meta, transcript):
    """
    transcript（list[dict]）から簡易フィードバックを生成する。
    - 失敗時は {"error": "..."} を返す
    - 成功時は dict（JSONにできる形）を返す
    """
    try:
        import requests

        api_key = os.environ.get("OPEN_AI_KEY") or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return {"error": "OPEN_AI_KEY (or OPENAI_API_KEY) が設定されていません"}

        model = os.environ.get("FEEDBACK_MODEL") or "gpt-4o-mini"

        title = ""
        instructions = ""
        try:
            title = getattr(meta, "title", "") or ""
        except Exception:
            title = ""
        try:
            instructions = getattr(meta, "instructions", "") or ""
        except Exception:
            instructions = ""

        def _clip(s, n=500):
            s = s or ""
            return s if len(s) <= n else s[:n] + "…"

        lines = []
        for t in transcript:
            role = (t.get("role") or "").strip()
            text = (t.get("text") or "").strip()
            if not role or not text:
                continue
            if role == "user":
                lines.append(f"ユーザー: {_clip(text)}")
            elif role == "assistant":
                lines.append(f"AI: {_clip(text)}")
            else:
                lines.append(f"{role}: {_clip(text)}")

        convo_text = "\n".join(lines)

        system = (
            "あなたは会話練習のコーチです。日本語で、短く具体的にフィードバックしてください。"
            "相手を傷つけないトーンで、改善点は行動に落とせる形で提案してください。"
        )
        user = (
            f"シナリオ: {title}\n"
            f"追加指示: {instructions}\n\n"
            "以下の会話ログ（全体）を読んで、次のJSON形式で返してください。\n"
            "※ユーザーの最初の返答だけでなく、会話の流れ全体を対象にしてください。\n"
            "{\n"
            "  \"summary\": \"会話の要約（2〜4行。状況/論点/結論が分かるように）\",\n"
            "  \"score\": 0,\n"
            "  \"good_points\": [\"良かった点（最大3）\"],\n"
            "  \"improvements\": [\"改善点（最大3。次回すぐ実行できる具体行動で）\"],\n"
            "  \"better_questions\": [\"次に確認すべき質問（最大3）\"],\n"
            "  \"key_moments\": [\n"
            "    \"【局面】(いつ/何に対して)\\n【狙い】(相手が知りたいこと)\\n【改善】(こう言うと良い)\\n【模範（ユーザー）】(1〜3文)\\n【短い言い換え】(1文)\",\n"
            "    \"...（2〜5個）\"\n"
            "  ],\n"
            "  \"model_answer\": \"模範会話例（2〜4往復。ユーザーと相手の両方を書き、重要局面ではユーザーの返しを示す）\",\n"
            "  \"alt_phrasings\": [\"言い換え例（柔らかめ）\", \"言い換え例（端的）\"],\n"
            "  \"next_actions\": [\"次回の練習でやること（最大3）\"],\n"
            "  \"next_drill\": \"次の1本ノック（次回の練習テーマを1つ。短く）\"\n"
            "}\n\n"
            "会話ログ:\n"
            f"{convo_text}"
        )

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        data = None
        last_err = None
        for _ in range(2):
            try:
                res = requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=payload, timeout=60)
                res.raise_for_status()
                data = res.json()
                last_err = None
                break
            except Exception as e:
                last_err = e
                data = None
        if last_err is not None:
            raise last_err

        content = None
        try:
            content = data["choices"][0]["message"]["content"]
        except Exception:
            content = None

        if isinstance(content, str) and content.strip():
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return parsed
                return {"text": content}
            except Exception:
                return {"text": content}

        return {"error": "フィードバック生成に失敗しました（contentが空）"}

    except Exception as e:
        return {"error": f"フィードバック生成エラー: {e}"}



def _normalize_feedback_payload(payload):
    """
    OpenAIの返り値（JSON/テキスト/エラー）を templates で扱いやすい形に正規化する。
    - summary: str
    - good_points / improvements / next_actions: list[str]
    - score: int
    """
    try:
        if payload is None:
            payload = {}
        if isinstance(payload, str):
            payload = {"text": payload}
        elif not isinstance(payload, dict):
            payload = {"text": str(payload)}

        v = dict(payload)

        def _as_str(x):
            if x is None:
                return ""
            try:
                return str(x)
            except Exception:
                return ""

        def _as_list(x):
            if x is None:
                return []
            if isinstance(x, list):
                out = []
                for it in x:
                    s = _as_str(it).strip()
                    if s:
                        out.append(s)
                return out
            s = _as_str(x).strip()
            return [s] if s else []

        if not isinstance(v.get("summary"), str):
            v["summary"] = _as_str(v.get("summary"))

        if not v.get("summary"):
            if v.get("error"):
                v["summary"] = _as_str(v.get("error"))
            elif v.get("text"):
                v["summary"] = _as_str(v.get("text"))

        v["good_points"] = _as_list(v.get("good_points"))
        v["improvements"] = _as_list(v.get("improvements"))
        v["next_actions"] = _as_list(v.get("next_actions"))
        v["better_questions"] = _as_list(v.get("better_questions"))
        v["key_moments"] = _as_list(v.get("key_moments"))

        if not isinstance(v.get("model_answer"), str):
            v["model_answer"] = _as_str(v.get("model_answer"))
        v["alt_phrasings"] = _as_list(v.get("alt_phrasings"))
        if not isinstance(v.get("next_drill"), str):
            v["next_drill"] = _as_str(v.get("next_drill"))

        score = v.get("score")
        try:
            v["score"] = int(score)
        except Exception:
            v["score"] = 0

        if "better_questions" not in v:
            v["better_questions"] = []
        if "key_moments" not in v:
            v["key_moments"] = []
        if "model_answer" not in v:
            v["model_answer"] = ""
        if "alt_phrasings" not in v:
            v["alt_phrasings"] = []
        if "next_drill" not in v:
            v["next_drill"] = ""

        if "summary" not in v:
            v["summary"] = ""
        if "good_points" not in v:
            v["good_points"] = []
        if "improvements" not in v:
            v["improvements"] = []
        if "next_actions" not in v:
            v["next_actions"] = []
        if "score" not in v:
            v["score"] = 0

        return v
    except Exception:
        return {
            "summary": "（フィードバックの整形に失敗しました）",
            "good_points": [],
            "improvements": [],
            "next_actions": [],
            "score": 0
        }

@app.post("/api/session/<session_id>/feedback/generate")
@require_auth
def api_generate_feedback(session_id):
    meta = store.get_session(session_id)
    if not meta:
        return jsonify({"ok": False, "error": "session not found"}), 404

    log = store.get_transcript(session_id) or {}
    transcript = log.get("transcript") or []
    if not transcript:
        return jsonify({"ok": False, "error": "transcript is empty"}), 400

    feedback_payload = _generate_feedback_with_openai(meta, transcript)
    feedback_payload = _normalize_feedback_payload(feedback_payload)
    ok = store.save_feedback(session_id, feedback_payload)

    return jsonify({"ok": ok, "feedback": feedback_payload}), (200 if ok else 404)
# ▲▲▲ 追加ここまで ▲▲▲

# ▼▼▼ STG-002: フィードバック削除API ▼▼▼
@app.post("/api/session/<session_id>/feedback/delete")
@require_auth
def api_delete_feedback(session_id):
    meta = store.get_session(session_id)
    if not meta:
        return jsonify({"ok": False, "error": "session not found"}), 404

    ok = store.delete_feedback(session_id)
    return jsonify({"ok": ok}), (200 if ok else 404)
# ▲▲▲ 追加ここまで ▲▲▲

def on_message(ws, message, sid):
    try:
        state = client_states.get(sid)
        if not state:
            print(f"状態が見つかりません: {sid}")
            return
        message_data = json.loads(message)
        msg_type = message_data.get("type")

        if msg_type == "error":
            print("メッセージ受信：error")
            print("エラー内容:", message_data)
            socketio.emit('status_message', {'message': f"AIサーバーエラー: {message_data}"}, room=sid)

        elif msg_type == "response.done":
            print("メッセージ受信：response.done")
            socketio.emit('status_message', {'message': 'AIの応答が完了しました。'}, room=sid)

        elif msg_type == "response.text.final":
            final_text = message_data.get("text")
            print(f"AIの応答（text.final）: {final_text}")
            # text.final ではAI応答をemitしない

        elif msg_type == "response.content_part.done":
            content = message_data.get("content") or message_data.get("part")
            if isinstance(content, dict):
                text_or_transcript = content.get("text") or content.get("transcript") or ""
            else:
                text_or_transcript = str(content)
            print(f"AIの応答（content_part.done）: {text_or_transcript}")
            if text_or_transcript:
                state["ai_transcription_buffer"] += text_or_transcript
                # AI吹き出しを即時emit
                socketio.emit('ai_message', {'message': text_or_transcript}, room=sid)

        elif msg_type == "audio":
            transcript = message_data.get("transcript")
            if transcript:
                print(f"AIの応答（audio）: {transcript}")
            # audio ではAI応答をemitしない

        elif msg_type in ("response.output_audio_transcript.delta", "response.audio_transcript.delta"):
            delta = message_data.get("delta") or ""
            state["ai_transcription_buffer"] += delta
            print(f"AIの応答（audio_transcript.delta）: {delta}")
            # --- ストリーミング応答: delta受信ごとに段階的に送信 ---
            if delta.strip():
                socketio.emit('ai_message', {'message': state["ai_transcription_buffer"], 'turn': state["current_turn"], 'stream': True}, room=sid)
                socketio.emit('status_message', {'message': 'AI応答(部分)ストリーミング送信'}, room=sid)

        elif msg_type == "user.transcription":
            transcription = message_data.get("transcription")
            print(f"ユーザーの発言(途中): {transcription}")

        elif msg_type == "input_audio_buffer.committed":
            transcription = message_data.get("transcription")
            print(f"ユーザーの発言（committed中間）: {transcription}")
            if transcription and len(transcription) > 2:
                state["current_turn"] += 1
                socketio.emit('user_message', {'message': transcription, 'turn': state["current_turn"], 'interim': True}, room=sid)

        elif msg_type == "conversation.item.input_audio_transcription.completed":
            print("#################################")
            print(message_data)
            transcript = message_data.get("transcript")
            import re
            def is_valid_japanese(text):
                return bool(re.search(r'[\u3040-\u30FF\u4E00-\u9FFF]', text or ""))
            if transcript and len(transcript) > 2 and is_valid_japanese(transcript):
                state["current_turn"] += 1
                socketio.emit('user_message', {'message': transcript, 'turn': state["current_turn"]}, room=sid)
                system_prompt = "あなたは親切で有能なアシスタントです。応答は簡潔に。"
                instructions = f"{system_prompt}\n{transcript}"
                response_create = {
                    "type": "response.create",
                    "response": {
                        "output_modalities": ["audio"],
                        "instructions": instructions
                    }
                }
                ws.send(json.dumps(response_create))
            else:
                print(f"transcript無効: {transcript}")
                print("response.create をユーザー発話に応じて送信しました。")

        elif msg_type == "conversation.item.created":
            print("#################################")
            print(message_data)
            # user_message emit を削除

        elif msg_type in ("response.output_audio.delta", "response.audio.delta"):
            delta = message_data.get("delta")
            if delta:
                try:
                    import binascii
                    audio_data = base64.b64decode(delta)
                    print("audio delta head (hex):", binascii.hexlify(audio_data[:16]))
                    # PCMをバッファにappendのみ
                    state["audio_pcm_buffer"] += audio_data
                except Exception as e:
                    print("audio delta decode error:", e)

        elif msg_type in ("response.output_audio_transcript.done", "response.audio_transcript.done"):
            final_ai_text = state["ai_transcription_buffer"]
            state["ai_transcription_buffer"] = ""
            print("メッセージ受信：response.audio_transcript.done")
            # --- 各AI応答ごとにturnを進めて独立した吹き出しを確保 ---
            state["current_turn"] += 1
            if final_ai_text and final_ai_text.strip():
                socketio.emit('ai_message', {'message': final_ai_text, 'turn': state["current_turn"]}, room=sid)
                state["last_ai_message"] = final_ai_text
                socketio.emit('status_message', {'message': 'AIの音声文字起こしが完了しました。'}, room=sid)
            else:
                socketio.emit('ai_message', {'message': '（無応答）', 'turn': state["current_turn"]}, room=sid)
                print("final_ai_textが空のためダミーai_messageをemitしました")

        elif msg_type in ("response.output_audio.done", "response.audio.done"):
            # バッファにたまったPCMをWAV化してemit
            pcm_bytes = state["audio_pcm_buffer"]
            if pcm_bytes:
                try:
                    def pcm_to_wav(pcm_bytes, sample_rate=24000, channels=1):
                        import io
                        import wave
                        with io.BytesIO() as wav_buffer:
                            with wave.open(wav_buffer, "wb") as wav_file:
                                wav_file.setnchannels(channels)
                                wav_file.setsampwidth(2)  # 16bit
                                wav_file.setframerate(sample_rate)
                                wav_file.writeframes(pcm_bytes)
                            return wav_buffer.getvalue()
                    wav_bytes = pcm_to_wav(pcm_bytes)
                    wav_b64 = base64.b64encode(wav_bytes).decode('ascii')
                    socketio.emit('audio_data', {'audio': wav_b64}, room=sid)
                except Exception as e:
                    print("audio done decode error:", e)
            # バッファクリア
            state["audio_pcm_buffer"] = bytearray()
        elif msg_type == "response.created":
            print("メッセージ受信：response.created")
            # --- 🔧 新規AI応答開始時にバッファ初期化 ---
            state["ai_transcription_buffer"] = ""
            state["last_ai_message"] = ""
            print("AI応答バッファを初期化しました。")
            socketio.emit('status_message', {'message': "メッセージ受信：response.created"}, room=sid)
        else:
            print(f"メッセージ受信：{msg_type}")
            socketio.emit('status_message', {'message': f"メッセージ受信：{msg_type}"}, room=sid)
    except Exception as e:
        print(f"メッセージ処理エラー: {e}")
        socketio.emit('status_message', {'message': f"メッセージ処理エラー: {e}"}, room=sid)

def on_error(ws, error, sid):
    print(f"WebSocket エラー: {error}")
    socketio.emit('status_message', {'message': f"WebSocket エラー: {error}"}, room=sid)

def on_close(ws, close_status_code, close_msg, sid):
    state = client_states.get(sid)
    print("WebSocket 接続が閉じられました。")
    socketio.emit('status_message', {'message': "Azure OpenAIサーバーとの接続が閉じられました。"}, room=sid)
    if state:
        with state["ws_lock"]:
            state["ws_connection"] = None

def on_open(ws, sid):
    print("Azure OpenAIサーバーに接続しました。")
    socketio.emit('status_message', {'message': "Azure OpenAIサーバーに接続しました。"}, room=sid)
    session_update = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "output_modalities": ["audio"],
            "instructions": "ユーザーを支援します。一回の応答は短く簡潔に。",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 2000
                    },
                    "transcription": {"model": "whisper-1", "language": "ja"}
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": REALTIME_VOICE
                }
            },
        }
    }
    ws.send(json.dumps(session_update))
    print("セッションアップデートメッセージを送信しました。")
    socketio.emit('status_message', {'message': "セッションアップデートメッセージを送信しました。"}, room=sid)
    # response.create は「start_interview」イベント受信時のみ送信するように変更
    # --- 追加: 起動時に自動発話しない旨を明示 ---
    print("AI初手発話は on_open では行いません（ユーザー操作または発話後に開始）。")
    socketio.emit('status_message', {'message': "AI初手発話は on_open では行いません。"}, room=sid)

def start_websocket(sid):
    state = client_states.get(sid)
    if not state:
        print(f"状態が見つかりません: {sid}")
        return
    ws_url = url
    headers = [
        "Content-Type: application/json",
        f"Authorization: Bearer {key}" ,
    ]
    with state["ws_lock"]:
        if state["ws_connection"] is not None:
            print("既にWebSocket接続が存在します。新しい接続を開始しません。")
            return
        state["ws_connection"] = websocket.WebSocketApp(
            ws_url,
            header=headers,
            on_message=lambda ws, msg: on_message(ws, msg, sid),
            on_error=lambda ws, err: on_error(ws, err, sid),
            on_close=lambda ws, code, msg: on_close(ws, code, msg, sid),
            on_open=lambda ws: on_open(ws, sid)
        )
    state["ws_connection"].run_forever()

@socketio.on('connect')
def handle_connect():
    sid = request.sid
    print(f'クライアントが接続しました: {sid}')
    socketio.emit('status_message', {'message': "クライアントが接続しました。"}, room=sid)
    init_client_state(sid)
    state = client_states[sid]
    with state["audio_worker_lock"]:
        if not state["audio_worker_started"]:
            # 音声再生ワーカーは現状未使用
            state["audio_worker_started"] = True
    if ENABLE_LEGACY_OPENAI_WS:
        threading.Thread(target=start_websocket, args=(sid,), daemon=True).start()
    else:
        socketio.emit('status_message', {'message': "LEGACY OpenAI WebSocket経路は無効です（ENABLE_LEGACY_OPENAI_WS=1で有効化）"}, room=sid)

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f'クライアントが切断しました: {sid}')
    socketio.emit('status_message', {'message': "クライアントが切断しました。"}, room=sid)
    cleanup_client_state(sid)

# ============================================================
# ✅ Realtime WebRTC セッション作成（Unified interface）
# ============================================================
@app.route("/realtime/sdp-proxy", methods=["POST"])
@require_auth
def realtime_sdp_proxy():
    """SDP とセッション設定を OpenAI に中継し、API キーをブラウザへ露出させない。"""
    try:
        import requests
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPEN_AI_KEY")
        if not api_key:
            return jsonify({"error": "OPENAI_API_KEY (or OPEN_AI_KEY) is not configured"}), 500

        sdp_offer = request.get_data(as_text=True)
        if not sdp_offer.strip():
            return jsonify({"error": "SDP offer is empty"}), 400

        session_config = {
            "type": "realtime",
            "model": REALTIME_MODEL,
            "output_modalities": ["audio"],
            "audio": {
                "output": {"voice": REALTIME_VOICE}
            }
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        files = {
            "sdp": (None, sdp_offer, "application/sdp"),
            "session": (None, json.dumps(session_config), "application/json"),
        }
        res = requests.post(
            "https://api.openai.com/v1/realtime/calls",
            headers=headers,
            files=files,
            timeout=30,
        )
        content_type = "application/sdp" if res.ok else (res.headers.get("Content-Type") or "text/plain")
        return res.text, res.status_code, {"Content-Type": content_type}
    except Exception as e:
        print("SDP Proxy error:", e)
        return str(e), 500
# クライアントから音声データを受信し、OpenAI WebSocketに転送
@socketio.on('audio_data')
def handle_audio_data(data):
    """音声チャンクをサーバーに送信（commitは分離イベントで実施）"""
    sid = request.sid
    if not ENABLE_LEGACY_OPENAI_WS:
        socketio.emit('status_message', {'message': "LEGACY経路は無効です（ENABLE_LEGACY_OPENAI_WS=1で有効化）"}, room=sid)
        return
    state = client_states.get(sid)
    if not state:
        print(f"状態が見つかりません: {sid}")
        return
    ws = state.get("ws_connection")
    if not ws:
        print(f"WebSocket接続が存在しません: {sid}")
        return
    try:
        audio_b64 = data.get("audio")
        if not audio_b64:
            print("audioデータが空です")
            return
        import base64 as b64
        audio_bytes = b64.b64decode(audio_b64)
        if len(audio_bytes) < 1000:
            print(f"audioデータが短すぎるため送信スキップ（{len(audio_bytes)} bytes）")
            socketio.emit('status_message', {'message': f"短小チャンクスキップ: {len(audio_bytes)} bytes"}, room=sid)
            return
        input_audio = {
            "type": "input_audio_buffer.append",
            "audio": audio_b64
        }
        ws.send(json.dumps(input_audio))
        socketio.emit('status_message', {'message': f"音声チャンク送信: {len(audio_bytes)} bytes"}, room=sid)
    except Exception as e:
        print(f"音声データ送信エラー: {e}")
        socketio.emit('status_message', {'message': f"音声データ送信エラー: {e}"}, room=sid)

# commitイベントを分離
@socketio.on('audio_commit')
def handle_audio_commit():
    """前回送信済みの音声データを明示的にcommit"""
    sid = request.sid
    if not ENABLE_LEGACY_OPENAI_WS:
        socketio.emit('status_message', {'message': "LEGACY経路は無効です（ENABLE_LEGACY_OPENAI_WS=1で有効化）"}, room=sid)
        return
    state = client_states.get(sid)
    if not state:
        print(f"状態が見つかりません: {sid}")
        return
    ws = state.get("ws_connection")
    if not ws:
        print(f"WebSocket接続が存在しません: {sid}")
        return
    try:
        commit_msg = {"type": "input_audio_buffer.commit"}
        ws.send(json.dumps(commit_msg))
        print("[audio_commit] input_audio_buffer.commitを送信しました")
        socketio.emit('status_message', {'message': "commit送信完了"}, room=sid)
    except Exception as e:
        print(f"[audio_commit] commit送信エラー: {e}")
        socketio.emit('status_message', {'message': f"commit送信エラー: {e}"}, room=sid)


@socketio.on('start_process')
def handle_start_process():
    sid = request.sid
    if not ENABLE_LEGACY_OPENAI_WS:
        socketio.emit('status_message', {'message': "LEGACY経路は無効です（ENABLE_LEGACY_OPENAI_WS=1で有効化）"}, room=sid)
        return
    print(f"[start_process] クライアント {sid} から受信")
    # クライアント状態初期化（なければ）
    if sid not in client_states:
        init_client_state(sid)
    state = client_states[sid]
    # WebSocket接続がなければ開始
    if state["ws_connection"] is None:
        print("[start_process] WebSocket未接続のため接続開始")
        start_websocket(sid)
        # WebSocket接続は非同期なので、on_openでresponse.createを送る
        # ここでは何もしない
    else:
        # 既に接続済みならAI初手発話（response.create）を送信
        ws = state["ws_connection"]
        response_create = {
            "type": "response.create",
            "response": {
                "output_modalities": ["audio"],
                "instructions": (
                    "あなたは丁寧で穏やかなインタビュアーです。"
                    "初回の発話では「よろしくお願いします。」の後に一言だけ自然な導入（例：「今日はよろしくお願いします。」や「では始めていきましょうか。」）を添えてください。"
                    "部屋や物体など視覚的な描写は行わないでください。"
                )
            }
        }
        try:
            ws.send(json.dumps(response_create))
            print("[start_process] response.createを送信しました")
            socketio.emit('status_message', {'message': "AI初手発話を送信しました。"}, room=sid)
        except Exception as e:
            print("[start_process] response.create送信エラー:", e)
            socketio.emit('status_message', {'message': f"AI初手発話送信エラー: {e}"}, room=sid)

if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=5000)