# ai_agent_v2.py
# Exotel Voicebot-compliant bridge: Exotel <-> Sarvam (STT/TTS)
# - Sends initial greeting as PCM16 (little-endian) b64 media frames (Exotel voicebot protocol)
# - Handles start/connected/media/mark/stop events
# - Streams inbound media to Sarvam STT (websocket) and plays Sarvam TTS (pcm_s16le) back to Exotel
# - Debug audio saved under /tmp/call_chunks

import os
import json
import base64
import asyncio
import logging
import wave
import io
import time
from datetime import datetime
try:
    import audioop  # available <3.13
except ModuleNotFoundError:
    import audioop_lts as audioop  # fallback for 3.13+

from typing import Optional, Dict, Any
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import aiohttp
from aiohttp import WSMsgType, ClientWebSocketResponse
from dotenv import load_dotenv
import requests
from requests.auth import HTTPBasicAuth

# ---------------- setup ----------------
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("rupeek-ai")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# env
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
EXOTEL_SID = os.getenv("EXOTEL_SID", "rupeekfintech13")
EXOTEL_API_KEY = os.getenv("EXOTEL_API_KEY", "")
EXOTEL_API_TOKEN = os.getenv("EXOTEL_API_TOKEN", "")
EXOPHONE = os.getenv("EXOPHONE", "08069489493")
EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")
BASE_URL = os.getenv("BASE_URL", "https://ai-calling-bot-rqw5.onrender.com")

# audio settings
IN_SAMPLE_RATE = int(os.getenv("IN_SAMPLE_RATE", "8000"))   # incoming from Exotel
OUT_SAMPLE_RATE = int(os.getenv("OUT_SAMPLE_RATE", "8000")) # what we send back

# sarvam endpoints (websocket)
SARVAM_STT_URL = (
    "wss://api.sarvam.ai/speech-to-text/ws"
    f"?language-code=en-IN&model=saarika:v2.5&input_audio_codec=pcm_s16le"
    f"&sample_rate={IN_SAMPLE_RATE}&high_vad_sensitivity=false&vad_signals=true"
)
SARVAM_TTS_URL = (
    "wss://api.sarvam.ai/text-to-speech/ws"
    "?model=bulbul:v2&send_completion_event=true"
)

# debug dir
AUDIO_DEBUG_DIR = "/tmp/call_chunks"
os.makedirs(AUDIO_DEBUG_DIR, exist_ok=True)

# ---------------- bot content ----------------
def run_sales_pitch() -> str:
    return ("Hi! I’m calling from Rupeek. You have a pre-approved personal loan offer. "
            "Would you like to check your loan eligibility now?")

FAQ_BANK = [
    (["interest rate", "rate of interest", "roi"], "The interest rate is personalized. Check the loan summary in the Rupeek app."),
    (["open the rupeek app", "open app"], "Please open the Rupeek app and I will guide you step by step."),
]

def get_bot_reply(user_message: Optional[str]) -> str:
    if not user_message:
        return run_sales_pitch()
    m = user_message.lower()
    if "yes" in m: return "Great! Please open the Rupeek app. I will guide you step by step."
    if "no" in m: return "No worries. Have a nice day!"
    for variants, ans in FAQ_BANK:
        if any(v in m for v in variants):
            return ans
    return "Sorry, could you please repeat that?"

# ---------------- audio helpers ----------------
def wav_bytes_from_linear16(pcm_lin16: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_lin16)
    return buf.getvalue()

def save_wav_from_pcm(pcm16: bytes, sample_rate: int, prefix: str):
    path = os.path.join(AUDIO_DEBUG_DIR, f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.wav")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    log.info(f"🎧 Saved debug wav: {path}")
    return path

def strip_wav_header_and_return_pcm(wav_bytes: bytes) -> bytes:
    # returns raw pcm16 bytes from wav bytes buffer
    bio = io.BytesIO(wav_bytes)
    with wave.open(bio, "rb") as r:
        frames = r.readframes(r.getnframes())
        return frames

def is_wav(b: bytes) -> bool:
    return b[:4] == b"RIFF"

# ---------------- Sarvam helpers ----------------
async def sarvam_stt_connect(session: aiohttp.ClientSession) -> ClientWebSocketResponse:
    return await session.ws_connect(SARVAM_STT_URL, headers={"Api-Subscription-Key": SARVAM_API_KEY}, heartbeat=25, max_msg_size=50*1024*1024)

async def sarvam_tts_connect(session: aiohttp.ClientSession) -> ClientWebSocketResponse:
    return await session.ws_connect(SARVAM_TTS_URL, headers={"Api-Subscription-Key": SARVAM_API_KEY}, heartbeat=25, max_msg_size=50*1024*1024)

async def send_audio_chunk_to_stt(ws: ClientWebSocketResponse, pcm_lin16: bytes):
    # send WAV container as Sarvam expects "audio" with base64 WAV
    wav_chunk = wav_bytes_from_linear16(pcm_lin16, IN_SAMPLE_RATE)
    payload = {"audio": {"data": base64.b64encode(wav_chunk).decode("ascii"), "sample_rate": str(IN_SAMPLE_RATE), "encoding": "audio/wav", "input_audio_codec": "wav"}}
    await ws.send_json(payload)

# ---------------- Exotel protocol helpers ----------------
def exotel_media_event_b64_pcm(sequence_number: int, stream_sid: str, chunk_idx: int, payload_b64_pcm: str, timestamp_ms: int=None) -> Dict[str, Any]:
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    return {
        "event": "media",
        "sequence_number": sequence_number,
        "stream_sid": stream_sid,
        "media": {
            "chunk": chunk_idx,
            "timestamp": timestamp_ms,
            "payload": payload_b64_pcm
        }
    }

def exotel_mark_event(sequence_number: int, stream_sid: str, name: str="tts_end") -> Dict[str, Any]:
    return {
        "event": "mark",
        "sequence_number": sequence_number,
        "stream_sid": stream_sid,
        "mark": {"name": name}
    }

# ---------------- WebSocket handler ----------------
@app.websocket("/ws")
async def ws_bridge(websocket: WebSocket):
    """
    Exotel voicebot-compliant WS handler:
    - handles 'connected' / 'start' events (capture stream_sid, sample rate)
    - when 'start' received, generates initial greeting TTS (pcm_s16le) from Sarvam and streams it as media frames
    - after greeting, listens for 'media' frames from Exotel (inbound PCM), forwards to Sarvam STT, and processes transcripts
    - sends mark after TTS playback
    """
    await websocket.accept()
    log.info("🔌 WebSocket accepted (incoming from Exotel)")

    # per-call state
    stream_sid: Optional[str] = None
    sequence_number = 1
    chunk_index = 1
    exotel_sample_rate = IN_SAMPLE_RATE  # default, can be overridden by start event
    session = aiohttp.ClientSession()
    stt_ws = None
    tts_ws = None

    # transcripts queue
    transcripts_q: asyncio.Queue = asyncio.Queue()

    async def start_sarvam_stt():
        nonlocal stt_ws
        try:
            stt_ws = await sarvam_stt_connect(session)
            log.info("🔗 Connected to Sarvam STT WS")
            # start stt receiver task
            asyncio.create_task(stt_receiver_loop())
        except Exception as e:
            log.error(f"❌ Failed connect to Sarvam STT: {e}")
            stt_ws = None

    async def stt_receiver_loop():
        nonlocal stt_ws
        if not stt_ws:
            return
        partial = ""
        try:
            while True:
                msg = await stt_ws.receive()
                if msg.type == WSMsgType.CLOSE:
                    break
                if msg.type != WSMsgType.TEXT:
                    continue
                data = msg.json()
                if data.get("type") == "data":
                    tx = (data.get("data") or {}).get("transcript", "")
                    if tx:
                        partial = tx
                elif data.get("type") == "events":
                    ev = data.get("data") or {}
                    if ev.get("signal_type") == "END_SPEECH":
                        if partial:
                            await transcripts_q.put(partial)
                            partial = ""
        except Exception as e:
            log.error(f"STT receiver loop error: {e}")

    async def ensure_tts_ws():
        nonlocal tts_ws
        if tts_ws:
            return
        try:
            tts_ws = await sarvam_tts_connect(session)
            # request linear16 output (pcm_s16le). Some servers use "pcm_s16le" or "linear16"
            cfg = {
                "type": "config",
                "data": {
                    "target_language_code": "en-IN",
                    "speaker": "anushka",
                    "speech_sample_rate": str(OUT_SAMPLE_RATE),
                    "output_audio_codec": "pcm_s16le",   # request raw pcm16 little-endian
                    "output_audio_bitrate": "64k",
                    "min_buffer_size": 0,
                }
            }
            await tts_ws.send_json(cfg)
            log.info("🔗 Connected to Sarvam TTS WS (requested pcm_s16le)")
        except Exception as e:
            log.error(f"❌ Failed connect to Sarvam TTS: {e}")
            tts_ws = None

    async def send_initial_greeting_and_wait_finish():
        nonlocal sequence_number, chunk_index, tts_ws, stream_sid
        if not tts_ws or not stream_sid:
            return
        greeting = run_sales_pitch()
        try:
            await tts_ws.send_json({"type": "text", "data": {"text": greeting}})
            await tts_ws.send_json({"type": "flush"})
        except Exception as e:
            log.error(f"❌ Failed to send text to TTS ws: {e}")
            return

        # receive audio frames (Sarvam may send WAV bytes in base64 inside 'audio' json)
        # we accept: 1) 'audio' frames containing base64 audio (pcm or wav), 2) 'event' final
        try:
            while True:
                m = await tts_ws.receive()
                if m.type == WSMsgType.TEXT:
                    jd = m.json()
                    ttype = jd.get("type")
                    if ttype == "audio":
                        aud_b64 = (jd.get("data") or {}).get("audio")
                        if not aud_b64:
                            continue
                        raw = base64.b64decode(aud_b64)
                        # if Sarvam sends WAV container, extract pcm frames
                        if is_wav(raw):
                            pcm = strip_wav_header_and_return_pcm(raw)
                        else:
                            # assume raw pcm16 little endian
                            pcm = raw
                        # optional: resample or ensure sample rate matches OUT_SAMPLE_RATE
                        # send PCM16 frames to Exotel in manageable chunks (e.g., 1600 bytes ~100ms @ 8k)
                        frame_bytes = 1600  # chunk ~100-200ms depending on sample rate
                        total = len(pcm)
                        idx = 0
                        while idx < total:
                            part = pcm[idx:idx+frame_bytes]
                            # pad if short
                            if len(part) < frame_bytes:
                                part = part + b"\x00" * (frame_bytes - len(part))
                            b64pcm = base64.b64encode(part).decode("ascii")
                            evt = exotel_media_event_b64_pcm(sequence_number, stream_sid, chunk_index, b64pcm)
                            await websocket.send_text(json.dumps(evt))
                            log.debug(f"→ Sent media seq={sequence_number} chunk={chunk_index} bytes={len(part)}")
                            sequence_number += 1
                            chunk_index += 1
                            idx += frame_bytes
                            await asyncio.sleep(0.02)  # pacing
                        # save debug wav
                        try:
                            save_wav_from_pcm(pcm, OUT_SAMPLE_RATE, "tts_out")
                        except Exception:
                            pass
                    elif ttype == "event":
                        evd = jd.get("data") or {}
                        if evd.get("event_type") == "final":
                            log.info("✅ Sarvam TTS signaled final event for greeting")
                            break
                        if evd.get("event_type") == "error":
                            log.error(f"TTS error event: {jd}")
                            break
                    elif ttype == "error":
                        log.error(f"TTS error: {jd}")
                        break
                else:
                    # ignore non-text frames
                    continue
            # after finishing greeting audio, send mark
            try:
                mark = exotel_mark_event(sequence_number, stream_sid, name="greeting_end")
                await websocket.send_text(json.dumps(mark))
                log.info(f"→ Sent mark seq={sequence_number} name=greeting_end")
                sequence_number += 1
            except Exception as e:
                log.warning(f"Failed send mark: {e}")
        except Exception as e:
            log.error(f"Error streaming greeting from TTS: {e}")

    async def forward_inbound_media_to_stt(pcm16: bytes):
        # save debug wav
        try:
            save_wav_from_pcm(pcm16, IN_SAMPLE_RATE, "inbound")
        except Exception:
            pass
        # ensure stt_ws is connected
        if not stt_ws:
            await start_sarvam_stt()
        if stt_ws:
            try:
                await send_audio_chunk_to_stt(stt_ws, pcm16)
            except Exception as e:
                log.error(f"Failed to send chunk to STT: {e}")

    # main read loop from Exotel
    try:
        while True:
            msg = await websocket.receive()
            if "text" in msg and msg["text"]:
                try:
                    data = json.loads(msg["text"])
                except Exception as e:
                    log.warning(f"Bad JSON from Exotel: {e}")
                    continue
                evt = data.get("event")
                # --------------- connected ---------------
                if evt == "connected":
                    log.info("🔔 Exotel reported 'connected'")
                    # nothing to do yet
                    continue

                # --------------- start ---------------
                if evt == "start":
                    # capture stream_sid and sample rate
                    stream_sid = data.get("stream_sid") or data.get("streamSid") or data.get("metadata", {}).get("stream_sid")
                    # some variants include sample_rate in metadata
                    sr = None
                    if data.get("metadata") and isinstance(data["metadata"], dict):
                        sr = data["metadata"].get("sample_rate") or data["metadata"].get("sampleRate")
                    if sr:
                        try:
                            exotel_sample_rate = int(sr)
                        except Exception:
                            exotel_sample_rate = IN_SAMPLE_RATE
                    log.info(f"▶️ Stream started: stream_sid={stream_sid} sample_rate={exotel_sample_rate}")
                    # prepare sarvam connections and immediately play greeting
                    await ensure_tts_ws()
                    # play greeting if possible (non-blocking)
                    if tts_ws and stream_sid:
                        # run greeting streaming in background so we can still receive inbound media
                        asyncio.create_task(send_initial_greeting_and_wait_finish())
                    else:
                        log.info("ℹ️ Skipping greeting (tts_ws or stream_sid missing).")
                    continue

                # --------------- media (incoming) ---------------
                if evt == "media":
                    # Exotel media payload expected to be base64 PCM16 (little-endian)
                    media = data.get("media", {})
                    payload_b64 = media.get("payload") or media.get("chunk") or None
                    if not payload_b64:
                        # maybe payload stored under media['payload']
                        payload_b64 = media.get("payload")
                    if payload_b64:
                        try:
                            pcm = base64.b64decode(payload_b64)
                            # If Exotel gives WAV (unlikely per doc), strip header
                            if is_wav(pcm):
                                pcm = strip_wav_header_and_return_pcm(pcm)
                            # forward to STT
                            asyncio.create_task(forward_inbound_media_to_stt(pcm))
                        except Exception as e:
                            log.warning(f"Failed decode inbound media payload: {e}")
                    else:
                        log.debug("Received media event with no payload")
                    continue

                # --------------- mark ---------------
                if evt == "mark":
                    # playback mark or other informational mark
                    mark_info = data.get("mark") or {}
                    log.info(f"ℹ️ Received mark from Exotel: {mark_info}")
                    continue

                # --------------- stop ---------------
                if evt == "stop":
                    log.info("⏹️ Exotel Stream stopped")
                    break

                # unknown events
                log.debug(f"Unknown Exotel event: {evt} payload keys: {list(data.keys())}")
                continue

            # binary frames handling (some providers might send raw binary μ-law etc.)
            if "bytes" in msg and msg["bytes"]:
                # try interpret as raw pcm16 bytes or μ-law; we'll attempt to detect.
                raw = msg["bytes"]
                # Heuristic: if first 4 bytes = 'RIFF' then it's wav
                if is_wav(raw):
                    pcm = strip_wav_header_and_return_pcm(raw)
                else:
                    # assume it's already PCM16 little-endian
                    pcm = raw
                # forward to STT
                asyncio.create_task(forward_inbound_media_to_stt(pcm))
                continue

            if msg.get("type") == "websocket.disconnect":
                log.warning("⚡ WebSocket disconnected by remote")
                break

    except Exception as e:
        log.error(f"WS bridge uncaught error: {e}")
    finally:
        # cleanup
        try:
            if stt_ws:
                await stt_ws.close()
        except Exception:
            pass
        try:
            if tts_ws:
                await tts_ws.close()
        except Exception:
            pass
        try:
            await session.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
        log.info("🔚 WebSocket bridge closed (call ended)")

# ---------------- HTTP endpoints ----------------
@app.get("/")
async def root():
    return {"status": "ok", "service": "Rupeek AI Voicebot"}

@app.post("/trigger_call")
async def trigger_call(req: Request):
    body = await req.json()
    customer_number = body.get("mobile")
    if not customer_number:
        return JSONResponse({"error": "mobile number required"}, status_code=400)
    bot_ws_url = f"wss://{req.url.hostname}/ws" if req.url.hostname else f"{BASE_URL}/ws"
    payload = {
        "From": customer_number,
        "To": EXOPHONE,
        "CallerId": EXOPHONE,
        "Url": bot_ws_url,
        "CallType": "trans",
    }
    url = f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect"
    try:
        r = requests.post(url, data=payload, auth=HTTPBasicAuth(EXOTEL_API_KEY, EXOTEL_API_TOKEN), timeout=20)
        ok = r.status_code in (200, 201)
        return JSONResponse({"status": "success" if ok else "failed", "response": r.text}, status_code=(200 if ok else r.status_code))
    except Exception as e:
        return JSONResponse({"status": "failed", "error": str(e)}, status_code=500)

# ---------------- run server ----------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    log.info(f"📞 Rupeek AI Voicebot running on port {port}")
    uvicorn.run("ai_agent_v2:app", host="0.0.0.0", port=port)
