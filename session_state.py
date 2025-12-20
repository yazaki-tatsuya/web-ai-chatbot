# session_state.py

import queue
import threading

# 元の client_states をそのまま移動
client_states = {}

def init_client_state(sid):
    client_states[sid] = {
        "audio_receive_queue": queue.Queue(),
        "audio_worker_started": False,
        "audio_worker_lock": threading.Lock(),
        "audio_worker_lock": threading.Lock(),
        "ws_connection": None,
        "ws_lock": threading.Lock(),
        "user_transcription_buffer": "",
        "last_ai_message": "",
        "current_turn": 0,
        "ai_transcription_buffer": "",
        "audio_pcm_buffer": bytearray(),
    }

def cleanup_client_state(sid):
    if sid in client_states:
        del client_states[sid]

def get_client_state(sid):
    return client_states.get(sid)
