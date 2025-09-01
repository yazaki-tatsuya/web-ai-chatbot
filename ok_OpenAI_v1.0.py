'''
    仕組み
        このスクリプトは、Azure OpenAIのリアルタイムAPIを使用して、音声を送受信するためのサンプルコードです。
        サーバーに接続して音声を送受信するための関数を実装しています。
        音声の送受信は別スレッドで行うため、非同期処理を使用しています。
        
'''
import env_production
import asyncio
import websockets
import json
import os

import pyaudio
import numpy as np
import base64
import time

# OpenAI用
key = env_production.get_env_variable("OPENAI_API_KEY")
url = "wss://api.openai.com/v1/realtime?model=gpt-4o-mini-realtime-preview-2024-12-17"
# url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"

'''
    用途
        サーバーに接続して音声を送受信する関数
    引数
        なし
    戻り値
        なし
    処理概要
        サーバーに接続するためのWebSocketを作成し、音声の送受信を行うためのタスクを作成します。
        この関数は無限ループで動作し、サーバーに接続した後、音声の送受信を繰り返します。
        音声の送受信は別スレッドで行うため、非同期処理を使用しています。
        この関数は、接続が切断されるまで音声の送受信を繰り返します。

'''
async def connect():
    async with websockets.connect(url, additional_headers={
        "Content-Type": "application/json",
        # OpenAI用
        "Authorization": f"Bearer {key}" ,
        "OpenAI-Beta": "realtime=v1",
    }) as websocket:
        print("Connected to server.")
        
        # メッセージを送受信するタスクを作成
        receive_task = asyncio.create_task(receive_messages(websocket))
        send_task = asyncio.create_task(send_messages(websocket))

        # レコードオーディオを別スレッドで処理するタスクを作成
        record_task= await asyncio.to_thread(record_audio,  websocket)

        # タスクが終了するまで待機
        await asyncio.gather(receive_task, send_task, record_task)

'''
    用途
        サーバーからの音声を受信して再生する関数
    引数
        websocket（WebSocketオブジェクト）
    戻り値
        なし
    処理概要
        サーバーからの応答(JSON)を受信し、その内容に応じて音声を再生します。
        応答の内容に応じて、音声データをデコードして再生したり、テキストデータを表示したりします。
        この関数は無限ループで動作し、サーバーからの応答を受信するたびに処理を行います。
        応答の内容によって処理が異なるため、応答のタイプに応じて処理を分岐させる必要があります。
        例えば、音声データの場合はデコードして再生し、テキストデータの場合は表示するなどの処理を行います。
'''
async def receive_messages(websocket):

    p = pyaudio.PyAudio()
    stream = p.open(format=pyaudio.paInt16, channels=1, rate=24000, output=True)


    conversaition_list = []

    # 無限ループで動作
    while True:
        # サーバーからの応答を受信
        message = await websocket.recv()
        message_data = json.loads(message)
        # サーバーからの応答をリアルタイム（ストリーム）で表示
        # サーバからの応答が完了したことを取得
        if message_data.get("type") == "response.done":
            print("メッセージ受信：response.done")
        # サーバーからの応答に音声データが含まれているか確認
        elif message_data.get("type") == "response.audio.delta":
            delta = message_data.get("delta")
            # 音声データをデコードして再生
            delta = base64.b64decode(delta)
            stream.write(delta)
        else:
            if not str(message_data.get("transcript")) == "None":
                # UserかAIかの判断処理
                if str(message_data.get("type")) == "response.audio_transcript.done":
                    # AI
                    print(f"AI：{message_data.get('transcript')}")
                    conversaition_list.append(f"AI：{message_data.get('transcript')}")
                else:
                    # User
                    print(f"User：{message_data.get('transcript')}")
                    conversaition_list.append(f"User：{message_data.get('transcript')}")

    print(str(conversaition_list))

    stream.stop_stream()
    stream.close()
    p.terminate()

'''
    用途
        音声を録音してサーバーに送信する関数
    引数
        websocket（WebSocketオブジェクト）
    戻り値
        なし
    処理概要
        マイクから音声を録音し、録音した音声データをサーバーに送信します。
        録音した音声データはbase64形式にエンコードしてサーバーに送信します。
        この関数は無限ループで動作し、録音した音声データをサーバーに送信した後、再度録音を行います。
        録音した音声データをサーバーに送信する際は、input_audio_buffer.appendメッセージを送信します。
        録音を終了する際は、input_audio_buffer.commitメッセージを送信します。
'''
async def record_audio(websocket):

    p = pyaudio.PyAudio()
    # マイクからの音声を取得するための設定
    sample_rate = 24000 # 24kHz
    duration_ms = 100 # 100ms
    samples_per_chunk = sample_rate * (duration_ms / 1000) # チャンクあたりのサンプル数
    bytes_per_sample = 2 # 16bit
    bytes_per_chunk = int(samples_per_chunk * bytes_per_sample) # チャンクあたりのバイト数
    
    # マイクストリームの初期化
    chunk_size = 2400  # マイクからの入力データのチャンクサイズ
    format = pyaudio.paInt16 # PCM16形式
    channels = 1  # モノラル
    record_seconds = 500 # 5秒間録音
    # マイクストリームの初期化
    stream = p.open(format=format,
                    channels=channels,
                    rate=sample_rate,
                    input=True,
                    frames_per_buffer=chunk_size)
    # セッションアップデートメッセージを送信し、音声検出のしきい値を設定
    await websocket.send(json.dumps({
        "type": "session.update",
        "session": {
            "turn_detection": {
                "type": "server_vad", 
                "threshold": 0.5,
            "prefix_padding_ms": 300,
            "silence_duration_ms": 200
            },
            "input_audio_transcription": {
                "model": "whisper-1"
            }
        }
    }))
    print("Listening to microphone for 5 seconds...")
    # 録音開始
    start_time = time.time()
    # チャンクカウンター
    chunk_counter = 0
    # 5秒間録音
    while time.time() - start_time < record_seconds:
        # マイクからの音声データを取得
        data = stream.read(chunk_size)
        
        # numpy配列に変換
        audio_data = np.frombuffer(data, dtype=np.int16)
        
        # base64形式にエンコード
        base64_audio = base64.b64encode(audio_data.tobytes()).decode('utf-8')
        
        chunk_counter += 1
        print(f"sending audio chunk {chunk_counter}")
        # サーバーに音声データを送信（チャンクごとに送信）
        await websocket.send(json.dumps({
            "type": "input_audio_buffer.append",
            "audio": base64_audio
        }))

        # サーバーがチャンクを処理するために少し待機
        # bufferオーバーフローを防ぐために必要
        await asyncio.sleep(0.1)

    # 録音終了
    stream.stop_stream() # マイクストリームを停止
    stream.close() # マイクストリームをクローズ
    p.terminate() # PyAudioを終了

    print("Finished recording.")

    # オーディオバッファの終了メッセージを送信
    # 　→サーバーがオーディオ ストリームの終了を自動で検知するため不要
    #await websocket.send(json.dumps({
    #    "type": "input_audio_buffer.commit",
    #}))

'''
    用途
        ユーザーからのメッセージを受信してサーバーに送信する関数
    引数
        websocket（WebSocketオブジェクト）
    戻り値
        なし
    処理概要
        この関数は、サーバーにメッセージを送信するためのタスクを作成します。
        接続が切断されるまでメッセージを送信し続けます。
'''
async def send_messages(websocket):
    await websocket.send(json.dumps({
        "type": "response.create",
        "response": {
            "modalities": ["text"],
            "instructions": "ユーザー（中堅のコンサルタント）をサポートしてください。",
        }
    }))

 
if __name__ == "__main__":
    asyncio.run(connect())