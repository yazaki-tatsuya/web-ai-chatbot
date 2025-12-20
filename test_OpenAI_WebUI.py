# eventlet.monkey_patch() は最初に呼び出す必要があります
import eventlet
eventlet.monkey_patch()

import os
from dotenv import load_dotenv
load_dotenv()  # .env を読み込む（ローカル用）

import json
import threading
import base64
from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask_socketio import SocketIO, emit
import websocket
import queue

# 追加
from session_store import InMemorySessionStore
store = InMemorySessionStore()

# Flaskアプリケーションの設定
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# OpenAI用の環境変数取得
key = os.environ.get("OPEN_AI_KEY")
url = "wss://api.openai.com/v1/realtime?model=gpt-realtime"

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
    session.id / session.scenario_title 形式に合わせるための薄い変換。
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
    return render_template("modes.html", modes=store.list_modes())

@app.route("/scenarios")
def scenarios():
    mode = request.args.get("mode")
    return render_template("scenarios.html", scenarios=store.list_scenarios(mode), mode=mode)

@app.post("/session/start")
def session_start():
    scenario_id = request.form.get("scenario_id", "free_talk")
    instructions = (request.form.get("instructions") or "").strip() or None
    meta = store.create_session(scenario_id, instructions)
    return redirect(url_for("index", session_id=meta.session_id))

@app.route("/history")
def history():
    return render_template("history.html", sessions=store.list_sessions())

@app.route("/feedback/<session_id>")
def feedback(session_id):
    meta = store.get_session(session_id)
    log = store.get_transcript(session_id)

    # ★追加：feedback.html が期待する変数名に合わせて渡す（既存は残す）
    session_view = _make_session_view(meta, session_id=session_id)
    transcript = log
    feedback_data = None

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
def api_save_transcript(session_id):
    payload = request.get_json(force=True)
    ok = store.save_transcript(session_id, payload)
    return jsonify({"ok": ok}), (200 if ok else 404)

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

        elif msg_type == "response.audio_transcript.delta":
            delta = message_data.get("delta") or ""
            state["ai_transcription_buffer"] += delta
            print(f"AIの応答（audio_transcript.delta）: {delta}")
            # --- ストリーミング応答: delta受信ごとに段階的に送信 ---
            if delta.strip():
                socketio.emit('ai_message', {'message': state["ai_transcription_buffer"], 'turn': state["current_turn"], 'stream': True}, room=sid)
                socketio.emit('status_message', {'message': 'AI応答(部分)ストリーミング送信'}, room=sid)

        elif msg_type == "response.audio_transcript.done":
            # emitは下の162行目側でのみ行う（ここではバッファクリアのみ）
            transcript = state["ai_transcription_buffer"]
            state["ai_transcription_buffer"] = ""
            print(f"AIの応答（audio_transcript.done）: {transcript}")
            # emitしない

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
                        "modalities": ["text","audio"],
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

        elif msg_type == "response.audio.delta":
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

        elif msg_type == "response.audio_transcript.done":
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

        elif msg_type == "response.audio.done":
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
            "modalities": ["text","audio"],
            "input_audio_format": "pcm16",
            "instructions": "ユーザーを支援します。一回の応答は短く簡潔に。",
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms":2000  # 長めに設定して1発話を統合
            },
            "input_audio_transcription": {
                "model": "whisper-1"
            },
        }
    }
    ws.send(json.dumps(session_update))
    print("セッションアップデートメッセージを送信しました。")
    socketio.emit('status_message', {'message': "セッションアップデートメッセージを送信しました。"}, room=sid)
    # response.create は「start_interview」イベント受信時のみ送信するように変更
    # response_create = {
    #     "type": "response.create",
    #     "response": {
    #         "modalities": ["text","audio"],
    #         "instructions": "ユーザーを支援します"
    #     }
    # }
    # ws.send(json.dumps(response_create))
    # print("response.create メッセージを送信しました。")
    # socketio.emit('status_message', {'message': "response.create メッセージを送信しました。"}, room=sid)
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
        "OpenAI-Beta: realtime=v1",
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
    threading.Thread(target=start_websocket, args=(sid,), daemon=True).start()

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    print(f'クライアントが切断しました: {sid}')
    socketio.emit('status_message', {'message': "クライアントが切断しました。"}, room=sid)
    cleanup_client_state(sid)

# ============================================================
# ✅ JWTトークン発行エンドポイントの追加
# ============================================================
import time
import jwt
from flask import jsonify

JWT_SECRET = os.environ.get("JWT_SECRET_KEY", "local-dev-secret")
JWT_EXP_SECONDS = 300  # トークン有効期限5分

@app.route("/jwt", methods=["GET"])
def issue_jwt_token():
    """Realtime API に直接接続するための一時JWTを発行"""
    payload = {
        "aud": "openai-realtime",
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXP_SECONDS,
        "iss": "flask-server",
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return jsonify({"jwt": token})


# ============================================================
# ✅ SDP Proxyエンドポイント（CORS回避用）
# ============================================================
@app.route("/realtime/sdp-proxy", methods=["POST"])
def realtime_sdp_proxy():
    """ブラウザのSDP Offerを安全に中継してCORSを回避"""
    try:
        import requests
        sdp_offer = request.data.decode("utf-8")
        headers = {
            "Authorization": f"Bearer {os.environ.get('OPEN_AI_KEY')}",
            "Content-Type": "application/sdp",
            "OpenAI-Beta": "realtime=v1"
        }
        url = "https://api.openai.com/v1/realtime?model=gpt-realtime"
        res = requests.post(url, headers=headers, data=sdp_offer)
        return res.text, res.status_code, {"Content-Type": "application/sdp"}
    except Exception as e:
        print("SDP Proxy error:", e)
        return str(e), 500
# クライアントから音声データを受信し、OpenAI WebSocketに転送
@socketio.on('audio_data')
def handle_audio_data(data):
    """音声チャンクをサーバーに送信（commitは分離イベントで実施）"""
    sid = request.sid
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
                "modalities": ["text", "audio"],
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