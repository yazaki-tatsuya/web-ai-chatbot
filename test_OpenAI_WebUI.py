# eventlet.monkey_patch() は最初に呼び出す必要があります
import eventlet
eventlet.monkey_patch()

import os
from dotenv import load_dotenv
load_dotenv()  # .env を読み込む（ローカル用）

import json
import threading
import base64
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import websocket
import queue

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
    }

def cleanup_client_state(sid):
    if sid in client_states:
        del client_states[sid]

@app.route('/')
def index():
    return render_template('index.html')

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

        elif msg_type == "audio":
            transcript = message_data.get("transcript")
            if transcript:
                print(f"AIの応答（audio）: {transcript}")
            # audio ではAI応答をemitしない

        elif msg_type == "response.audio_transcript.delta":
            delta = message_data.get("delta") or ""
            state["ai_transcription_buffer"] += delta
            print(f"AIの応答（audio_transcript.delta）: {delta}")

        elif msg_type == "response.audio_transcript.done":
            transcript = state["ai_transcription_buffer"]
            state["ai_transcription_buffer"] = ""
            print(f"AIの応答（audio_transcript.done）: {transcript}")
            if transcript:
                socketio.emit('ai_message', {'message': transcript}, room=sid)

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
                    def pcm_to_wav(pcm_bytes, sample_rate=16000, bits=16, channels=1):
                        import struct
                        byte_rate = sample_rate * channels * bits // 8
                        block_align = channels * bits // 8
                        wav_header = b'RIFF' + struct.pack('<I', 36 + len(pcm_bytes)) + b'WAVEfmt '
                        wav_header += struct.pack('<IHHIIHH', 16, 1, channels, sample_rate, byte_rate, block_align, bits)
                        wav_header += b'data' + struct.pack('<I', len(pcm_bytes))
                        return wav_header + pcm_bytes
                    wav_bytes = pcm_to_wav(audio_data)
                    wav_b64 = base64.b64encode(wav_bytes).decode('ascii')
                    socketio.emit('ai_audio', {'audio': wav_b64}, room=sid)
                except Exception as e:
                    print("audio delta decode error:", e)

        elif msg_type == "response.audio_transcript.done":
            final_ai_text = state["ai_transcription_buffer"]
            state["ai_transcription_buffer"] = ""
            print("メッセージ受信：response.audio_transcript.done")
            if final_ai_text and final_ai_text.strip():
                socketio.emit('ai_message', {'message': final_ai_text, 'turn': state["current_turn"]}, room=sid)
                state["last_ai_message"] = final_ai_text
                socketio.emit('status_message', {'message': 'AIの音声文字起こしが完了しました。'}, room=sid)
            else:
                print("final_ai_textが空のためai_messageはemitしません")

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
                "silence_duration_ms": 1000
            },
            "input_audio_transcription": {
                "model": "whisper-1"
            },
        }
    }
    ws.send(json.dumps(session_update))
    print("セッションアップデートメッセージを送信しました。")
    socketio.emit('status_message', {'message': "セッションアップデートメッセージを送信しました。"}, room=sid)
    response_create = {
        "type": "response.create",
        "response": {
            "modalities": ["text","audio"],
            "instructions": "ユーザーを支援します"
        }
    }
    ws.send(json.dumps(response_create))
    print("response.create メッセージを送信しました。")
    socketio.emit('status_message', {'message': "response.create メッセージを送信しました。"}, room=sid)

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

if __name__ == "__main__":
    socketio.run(app, host='0.0.0.0', port=5000)
