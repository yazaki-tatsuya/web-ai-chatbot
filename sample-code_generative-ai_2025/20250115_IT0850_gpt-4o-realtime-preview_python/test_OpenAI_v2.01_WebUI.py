# eventlet.monkey_patch() は最初に呼び出す必要があります
import eventlet
eventlet.monkey_patch()

import env_production
import json
import threading
import time
import base64
import pyaudio
import numpy as np
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import websocket
import queue

# Flaskアプリケーションの設定
app = Flask(__name__)
app.config['SECRET_KEY'] = 'secret!'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# OpenAI用の環境変数取得
key = env_production.get_env_variable("OPENAI_API_KEY")
url = "wss://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview-2024-12-17"

@app.route('/')
def index():
    return render_template('index.html')

# 音声データを保持するキュー
audio_receive_queue = queue.Queue()

# 音声再生ワーカーの起動フラグ
audio_worker_started = False
audio_worker_lock = threading.Lock()

# WebSocket接続の管理
ws_connection = None
ws_lock = threading.Lock()

# ユーザーの発言をバッファするための変数
user_transcription_buffer = ""

# AIの最後の応答を記録する変数
last_ai_message = ""

def on_message(ws, message):
    global user_transcription_buffer, last_ai_message
    try:
        message_data = json.loads(message)
        msg_type = message_data.get("type")
        
        if msg_type == "response.done":
            print("メッセージ受信：response.done")
            socketio.emit('status_message', {'message': 'AIの応答が完了しました。'})
        
        elif msg_type == "response.text.final":
            final_text = message_data.get("text")
            print(f"AIの応答（text.final）: {final_text}")
            socketio.emit('ai_message', {'message': final_text})
            last_ai_message = final_text  # AIの最後のメッセージを記録
        
        elif msg_type == "response.content_part.done":
            content = message_data.get("content")
            if isinstance(content, dict):
                transcript = content.get("transcript", "")
            else:
                transcript = str(content)
            print(f"AIの応答（content_part.done）: {transcript}")
            socketio.emit('ai_message', {'message': transcript})
            last_ai_message = transcript  # AIの最後のメッセージを記録
        
        elif msg_type == "audio":
            transcript = message_data.get("transcript")
            if transcript:
                print(f"AIの応答（audio）: {transcript}")
                socketio.emit('ai_message', {'message': transcript})
                last_ai_message = transcript  # AIの最後のメッセージを記録
        
        elif msg_type == "response.audio_transcript.delta":
            delta = message_data.get("delta")
            user_transcription_buffer += delta
            print(f"ユーザーの発言（delta）: {delta}")
            # 部分的な発言はまだ送信しない

        # ユーザーの発言（仮定）
        elif msg_type == "user.transcription":
            transcription = message_data.get("transcription")
            print(f"ユーザーの発言: {transcription}")
            socketio.emit('user_message', {'message': message_data})

        elif msg_type == "input_audio_buffer.committed":
            transcription = message_data.get("transcription")
            print(f"ユーザーの発言（完了）: {transcription}")
            # AIの応答と同じ内容の場合はユーザーのメッセージとして送信しない
            socketio.emit('user_message', {'message': message_data})

        elif msg_type == "conversation.item.input_audio_transcription.completed":
            print("#################################")
            print(message_data)
            socketio.emit('user_message', {'message': message_data})

        elif msg_type == "conversation.item.created":
            print("#################################")
            print(message_data)
            socketio.emit('user_message', {'message': message_data})

        # elif msg_type == "response.audio_transcript.done":
        #     transcription = user_transcription_buffer
        #     user_transcription_buffer = ""
        #     print(f"ユーザーの発言（完了）: {transcription}")
        #     # AIの応答と同じ内容の場合はユーザーのメッセージとして送信しない
        #     socketio.emit('user_message', {'message': "aaa"})
        
        elif msg_type == "response.audio.delta":
            # 音声データの処理（必要に応じて）
            delta = message_data.get("delta")
            audio_data = base64.b64decode(delta)
            audio_receive_queue.put(audio_data)
        
        elif msg_type == "response.audio_transcript_done":
            # 追加の終了メッセージがあれば処理
            print("メッセージ受信：response.audio_transcript_done")
            socketio.emit('status_message', {'message': 'ユーザーの音声認識が完了しました。'})
        
        else:
            print(f"メッセージ受信：{msg_type}")
            socketio.emit('status_message', {'message': f"メッセージ受信：{msg_type}"})
    except Exception as e:
        print(f"メッセージ処理エラー: {e}")
        socketio.emit('status_message', {'message': f"メッセージ処理エラー: {e}"})

def on_error(ws, error):
    print(f"WebSocket エラー: {error}")
    socketio.emit('status_message', {'message': f"WebSocket エラー: {error}"})

def on_close(ws, close_status_code, close_msg):
    global ws_connection
    print("WebSocket 接続が閉じられました。")
    socketio.emit('status_message', {'message': "Azure OpenAIサーバーとの接続が閉じられました。"})
    with ws_lock:
        ws_connection = None
    # 再接続のロジックを追加することも可能です

def on_open(ws):
    print("Azure OpenAIサーバーに接続しました。")
    socketio.emit('status_message', {'message': "Azure OpenAIサーバーに接続しました。"})
    # セッションアップデートメッセージを送信し、音声検出のしきい値を設定
    session_update = {
        "type": "session.update",
        "session": {
            "modalities": ["text","audio"],
            "input_audio_format": "pcm16",
            "instructions": "Make transcription from my speech",
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 200
            },
            "input_audio_transcription": {
                "model": "whisper-1"
            },
            # --- AIが発話中でも割り込みを許可 ---
            "output_audio": {
                "interruptible": True
            }
            # --- ここまで ---
        }
    }
    ws.send(json.dumps(session_update))
    print("セッションアップデートメッセージを送信しました。")
    socketio.emit('status_message', {'message': "セッションアップデートメッセージを送信しました。"})
    
    # response.create メッセージを送信
    response_create = {
        "type": "response.create",
        "response": {
            "modalities": ["text","audio"],
            "instructions": "ユーザーを支援します"
        }
    }
    ws.send(json.dumps(response_create))
    print("response.create メッセージを送信しました。")
    socketio.emit('status_message', {'message': "response.create メッセージを送信しました。"})
    
    # 録音開始スレッドを起動
    threading.Thread(target=record_audio, args=(ws,), daemon=True).start()

def play_audio_worker():
    """音声データをキューから取り出し、再生するワーカースレッド"""
    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=24000, output=True)
    print("音声再生を開始しました。")
    socketio.emit('status_message', {'message': "音声再生を開始しました。"})
    
    while True:
        try:
            audio_data = audio_receive_queue.get()
            if audio_data is None:
                break  # 終了信号
            stream.write(audio_data)
        except Exception as e:
            print(f"音声再生エラー: {e}")
            socketio.emit('status_message', {'message': f"音声再生エラー: {e}"})
            break
    
    stream.stop_stream()
    stream.close()
    p.terminate()
    print("音声再生を終了しました。")
    socketio.emit('status_message', {'message': "音声再生を終了しました。"})

def play_audio(audio_data):
    """キューに音声データを追加"""
    audio_receive_queue.put(audio_data)

'''
    用途
        マイクからの音声を録音し、サーバーに送信する関数
    引数
        ws（WebSocketオブジェクト）
    戻り値
        なし
    処理概要
        この関数は録音が終了するまで、無限ループで動作し続けます。
        マイクからの音声を録音し、録音中音声データをチャンクごとにサーバーに送信します。
        録音が終了した場合は、サーバーに録音終了を通知します。
'''
def record_audio(ws):
    try:
        p = pyaudio.PyAudio()
        # マイクからの音声を取得するための設定
        sample_rate = 24000  # 24kHz
        chunk_size = 2400     # マイクからの入力データのチャンクサイズ
        format = pyaudio.paInt16  # PCM16形式
        channels = 1          # モノラル

        stream = p.open(format=format,
                        channels=channels,
                        rate=sample_rate,
                        input=True,
                        frames_per_buffer=chunk_size)
        print("マイクからの録音を開始しました。")
        socketio.emit('status_message', {'message': "マイクからの録音を開始しました。"})
        # チャンクカウンター
        chunk_counter = 0        
        while True:
            try:
                # マイクからの音声データを取得
                data = stream.read(chunk_size, exception_on_overflow=False)
                # user_message += f"chunk-{_}"  # 仮テキスト
                
                # numpy配列に変換
                audio_data = np.frombuffer(data, dtype=np.int16)
                
                # base64形式にエンコード
                base64_audio = base64.b64encode(audio_data.tobytes()).decode('utf-8')
                chunk_counter += 1
                # サーバーに音声データを送信（チャンクごとに送信）
                input_audio = {
                    "type": "input_audio_buffer.append",
                    "audio": base64_audio
                }
                ws.send(json.dumps(input_audio))
                print(f"音声チャンクを送信：{chunk_counter}")
                # 音声チャンクを送信したことを通知するイベント
                socketio.emit('status_message', {'message': "音声チャンクを送信しました。"})
                
                # サーバーがチャンクを処理するために少し待機
                time.sleep(0.1)
            except Exception as e:
                print(f"録音エラー: {e}")
                socketio.emit('status_message', {'message': f"録音エラー: {e}"})
                break

        # 録音終了
        stream.stop_stream()  # マイクストリームを停止
        stream.close()        # マイクストリームをクローズ
        p.terminate()         # PyAudioを終了

        print("録音を終了しました。")
        socketio.emit('status_message', {'message': "録音を終了しました。"})
        
        # 以下はサーバー側がストリーム終了を自動検知する場合は不要
        # commit_message = {"type": "input_audio_buffer.commit"}
        # ws.send(json.dumps(commit_message))
    except Exception as e:
        print(f"録音スレッドエラー: {e}")
        socketio.emit('status_message', {'message': f"録音スレッドエラー: {e}"})

def start_websocket():
    global ws_connection
    ws_url = url
    headers = [
        "Content-Type: application/json",
        # f"api-key: {key}"
        # OpenAI用
        f"Authorization: Bearer {key}" ,
        "OpenAI-Beta: realtime=v1",
    ]
    websocket.enableTrace(True)  # デバッグログを有効にする
    
    with ws_lock:
        if ws_connection is not None:
            print("既にWebSocket接続が存在します。新しい接続を開始しません。")
            return
        ws_connection = websocket.WebSocketApp(ws_url,
                                               header=headers,
                                               on_message=on_message,
                                               on_error=on_error,
                                               on_close=on_close,
                                               on_open=on_open)
    
    ws_connection.run_forever()

@socketio.on('connect')
def handle_connect():
    global audio_worker_started
    print('クライアントが接続しました。')
    socketio.emit('status_message', {'message': "クライアントが接続しました。"})
    
    with audio_worker_lock:
        if not audio_worker_started:
            # 音声再生ワーカーを起動
            threading.Thread(target=play_audio_worker, daemon=True).start()
            audio_worker_started = True
    
    # WebSocketクライアントをバックグラウンドスレッドで開始
    threading.Thread(target=start_websocket, daemon=True).start()

@socketio.on('disconnect')
def handle_disconnect():
    print('クライアントが切断しました。')
    socketio.emit('status_message', {'message': "クライアントが切断しました。"})
    # 必要に応じて WebSocket 接続を閉じるロジックを追加

if __name__ == "__main__":
    # Flask-SocketIOでサーバーを起動
    socketio.run(app, host='0.0.0.0', port=5000)
