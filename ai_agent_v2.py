# ai_agent_v2.py
# Rupeek outbound voicebot, Exotel <-> Sarvam STT/TTS WebSocket bridge
# FastAPI + Uvicorn (single file). Robust logs included.

import os
import json
import base64
import asyncio
import logging
import wave
import io
try:
    import audioop  # Python <3.13
except ModuleNotFoundError:
    import audioop_lts as audioop  # Python ≥3.13

from typing import Optional, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import aiohttp
from aiohttp import WSMsgType, ClientWebSocketResponse
from dotenv import load_dotenv

# ------------- Setup -------------
load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# ---- Required ENV ----
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
if not SARVAM_API_KEY:
    log.warning("⚠️ SARVAM_API_KEY missing in environment.")

EXOTEL_SID = os.getenv("EXOTEL_SID", "rupeekfintech13")
EXOTEL_API_KEY = os.getenv("EXOTEL_API_KEY", "")
EXOTEL_API_TOKEN = os.getenv("EXOTEL_API_TOKEN", "")
EXOPHONE = os.getenv("EXOPHONE", "08069489493")
EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")

BASE_URL = os.getenv("BASE_URL", "https://ai-calling-bot-rqw5.onrender.com")

# ---- Streaming Config ----
# Exotel typically uses 8kHz μ-law for media streams in "Twilio-like" format.
EXOTEL_FORMAT = os.getenv("EXOTEL_FORMAT", "twilio").lower()  # "twilio" or "simple"
IN_SAMPLE_RATE = int(os.getenv("IN_SAMPLE_RATE", "8000"))
OUT_SAMPLE_RATE = int(os.getenv("OUT_SAMPLE_RATE", "8000"))

# STT (Sarvam)
SARVAM_STT_URL = (
    "wss://api.sarvam.ai/speech-to-text/ws"
    f"?language-code=en-IN&model=saarika:v2.5&input_audio_codec=pcm_s16le"
    f"&sample_rate={IN_SAMPLE_RATE}&high_vad_sensitivity=false&vad_signals=true"
)

# TTS (Sarvam)
SARVAM_TTS_URL = (
    "wss://api.sarvam.ai/text-to-speech/ws"
    "?model=bulbul:v2&send_completion_event=true"
)

# ---------------- Loan content (kept same as your Twilio version) ----------------
loan_steps = [
    "Open the Rupeek app.",
    "On the home screen, click the Cash banner.",
    "Check your pre-approved limit.",
    "Slide the slider to select the amount and tenure required.",
    "Tick the consent box to proceed.",
    "Add your bank account if not visible.",
    "Update your email ID and address, then select proceed to mandate setup.",
    "Setup autopay for EMI deduction on 5th of each month.",
    "Once mandate setup is done, you will see the loan summary page.",
    "Review loan details and click 'Get Money Now'.",
    "Enter OTP sent to your mobile. Loan disbursal will be initiated within 30 seconds."
]

FAQ_BANK = [
    (["rate of interest", "interest rate", "roi"],
     "The interest rate is personalized for each user. Once you reach the loan summary page after selecting the amount and tenure, you'll see the exact rate."),
    (["should i open the app", "open the rupeek app"],
     "Please open the Rupeek app and I will guide you step by step to check your offer."),
    (["consent box", "tick box"],
     "There is a consent tick box on the screen. Please select it to proceed to the next step."),
    (["add bank account", "bank not visible"],
     "Make sure you're adding a bank account that belongs to you. If it doesn't match your name, it will not be accepted.")
]

def run_sales_pitch() -> str:
    return ("Hi! I’m calling from Rupeek. You have a pre-approved personal loan offer. "
            "Would you like to check your loan eligibility now?")

def get_bot_reply(user_message: Optional[str]) -> str:
    if not user_message:
        return run_sales_pitch()
    m = user_message.lower()
    if "yes" in m:
        return "Great! Please open the Rupeek app. I will guide you step by step."
    if "no" in m:
        return "No worries. Have a nice day!"
    for variants, ans in FAQ_BANK:
        if any(v in m for v in variants):
            return ans
    return "Sorry, could you please repeat that?"

# ---------------- Utilities ----------------

def mulaw_b64_to_linear16(b64_payload: str) -> bytes:
    """Decode base64 μ-law -> linear16 PCM (8k)."""
    mulaw_bytes = base64.b64decode(b64_payload)
    # μ-law 8-bit to 16-bit linear PCM
    lin16 = audioop.ulaw2lin(mulaw_bytes, 2)  # width=2 bytes -> 16-bit
    return lin16

def wav_bytes_from_linear16(pcm_lin16: bytes, sample_rate: int) -> bytes:
    """Wrap raw linear16 PCM into a minimal WAV (mono) buffer."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        w.writeframes(pcm_lin16)
    return buf.getvalue()

def make_twilio_media_msg_from_mulaw(b64_payload: str) -> dict:
    # If you need to send media back in Twilio-like format (Exotel often accepts this):
    return {"event": "media", "media": {"payload": b64_payload}}

def decode_exotel_inbound(msg: str, is_binary: bool) -> Optional[bytes]:
    """
    Accept Exotel Voicebot inbound message in multiple shapes and
    return linear16 PCM bytes @ IN_SAMPLE_RATE or None if not media.
    """
    if is_binary:
        # Some clients send raw μ-law bytes. If so, convert to lin16.
        try:
            lin16 = audioop.ulaw2lin(msg, 2)
            return lin16
        except Exception:
            return None

    try:
        data = json.loads(msg)
    except Exception:
        return None

    # Twilio-style: {"event":"media","media":{"payload":"<b64>"}}
    if data.get("event") == "media" and "media" in data and "payload" in data["media"]:
        try:
            return mulaw_b64_to_linear16(data["media"]["payload"])
        except Exception as e:
            log.warning(f"Decode media payload failed: {e}")
            return None

    # Simple: {"audio":"<b64 pcm_s16le>","encoding":"linear16","rate":8000}
    if "audio" in data:
        try:
            raw = base64.b64decode(data["audio"])
            # if explicitly linear16 already
            if data.get("encoding", "").lower() in ("linear16", "pcm_s16le"):
                return raw
            # if μ-law flagged
            if data.get("encoding", "").lower() in ("mulaw", "mu-law", "ulaw", "u-law"):
                return audioop.ulaw2lin(raw, 2)
            # fallback assume linear16
            return raw
        except Exception as e:
            log.warning(f"Simple audio decode failed: {e}")
            return None

    # Ignore non-audio events
    return None

async def sarvam_stt_connect(session: aiohttp.ClientSession) -> ClientWebSocketResponse:
    return await session.ws_connect(
        SARVAM_STT_URL,
        headers={"Api-Subscription-Key": SARVAM_API_KEY},
        autoping=True,
        heartbeat=25,
        max_msg_size=10 * 1024 * 1024
    )

async def sarvam_tts_connect(session: aiohttp.ClientSession) -> ClientWebSocketResponse:
    return await session.ws_connect(
        SARVAM_TTS_URL,
        headers={"Api-Subscription-Key": SARVAM_API_KEY},
        autoping=True,
        heartbeat=25,
        max_msg_size=10 * 1024 * 1024
    )

async def send_audio_chunk_to_stt(ws: ClientWebSocketResponse, pcm_lin16: bytes):
    # Wrap as small WAV (Sarvam expects encoding/audio metadata)
    wav_chunk = wav_bytes_from_linear16(pcm_lin16, IN_SAMPLE_RATE)
    payload = {
        "audio": {
            "data": base64.b64encode(wav_chunk).decode("ascii"),
            "sample_rate": str(IN_SAMPLE_RATE),
            "encoding": "audio/wav",
            "input_audio_codec": "wav"
        }
    }
    await ws.send_json(payload)

async def flush_stt(ws: ClientWebSocketResponse):
    await ws.send_json({"type": "flush"})

async def tts_send_config(ws: ClientWebSocketResponse):
    # Configure TTS voice:
    cfg = {
        "type": "config",
        "data": {
            "target_language_code": "en-IN",
            "speaker": "anushka",
            "speech_sample_rate": str(OUT_SAMPLE_RATE),
            "output_audio_codec": "mulaw",   # generate μ-law so we can feed Exotel easily
            "output_audio_bitrate": "64k",
            "min_buffer_size": 0,
            "max_chunk_length": 250
        }
    }
    await ws.send_json(cfg)

async def tts_send_text(ws: ClientWebSocketResponse, text: str):
    await ws.send_json({"type": "text", "data": {"text": text}})

async def tts_flush(ws: ClientWebSocketResponse):
    await ws.send_json({"type": "flush"})

# ---------------- WS Bridge Handler ----------------

@app.websocket("/ws")
async def ws_bridge(websocket: WebSocket):
    await websocket.accept()
    log.info("🔌 WebSocket connection accepted from Exotel")

    if not SARVAM_API_KEY:
        await websocket.close(code=4000)
        log.error("❌ Missing SARVAM_API_KEY — closing WS.")
        return

    async with aiohttp.ClientSession() as session:
        # Create Sarvam STT & TTS sockets
        try:
            stt_ws = await sarvam_stt_connect(session)
            tts_ws = await sarvam_tts_connect(session)
            await tts_send_config(tts_ws)
        except Exception as e:
            log.error(f"❌ Could not connect to Sarvam WS: {e}")
            await websocket.close(code=4001)
            return

        # state
        inbound_queue: asyncio.Queue[bytes] = asyncio.Queue()
        tts_out_queue: asyncio.Queue[bytes] = asyncio.Queue()
        stt_transcript_queue: asyncio.Queue[str] = asyncio.Queue()

        async def exotel_reader():
            try:
                while True:
                    msg = await websocket.receive()
                    if "text" in msg and msg["text"] is not None:
                        pcm = decode_exotel_inbound(msg["text"], is_binary=False)
                        if pcm:
                            await inbound_queue.put(pcm)
                        continue
                    if "bytes" in msg and msg["bytes"] is not None:
                        pcm = decode_exotel_inbound(msg["bytes"], is_binary=True)
                        if pcm:
                            await inbound_queue.put(pcm)
                        continue
                    if msg["type"] == "websocket.disconnect":
                        break
            except Exception as e:
                log.error(f"Exotel reader error: {e}")
            finally:
                await stt_transcript_queue.put("__DISCONNECT__")

        async def stt_sender():
            """Send audio to Sarvam STT as chunks, and flush periodically."""
            try:
                silence_ticks = 0
                while True:
                    try:
                        pcm = await asyncio.wait_for(inbound_queue.get(), timeout=1.5)
                        await send_audio_chunk_to_stt(stt_ws, pcm)
                        silence_ticks = 0
                    except asyncio.TimeoutError:
                        silence_ticks += 1
                        if silence_ticks >= 2:
                            await flush_stt(stt_ws)
                            silence_ticks = 0
            except Exception as e:
                log.error(f"STT sender error: {e}")

        async def stt_receiver():
            """Read STT messages and push final transcript when available."""
            try:
                partial = ""
                while True:
                    msg = await stt_ws.receive()
                    if msg.type == WSMsgType.CLOSE:
                        break
                    if msg.type != WSMsgType.TEXT:
                        continue
                    data = msg.json()
                    # Sarvam STT response schema
                    if data.get("type") == "data" and isinstance(data.get("data"), dict):
                        trans = data["data"].get("transcript", "")
                        if trans:
                            partial = trans
                    elif data.get("type") == "events":
                        # look for END_SPEECH
                        ev = data.get("data", {})
                        if ev.get("signal_type") == "END_SPEECH":
                            if partial:
                                await stt_transcript_queue.put(partial)
                                partial = ""
            except Exception as e:
                log.error(f"STT receiver error: {e}")

        async def tts_worker():
            """Convert transcript -> reply text -> TTS -> send μ-law to Exotel."""
            try:
                while True:
                    transcript = await stt_transcript_queue.get()
                    if transcript == "__DISCONNECT__":
                        break
                    reply = get_bot_reply(transcript)
                    log.info(f"👂 User: {transcript}")
                    log.info(f"🗣️ Bot: {reply}")

                    await tts_send_text(tts_ws, reply)
                    await tts_flush(tts_ws)

                    # read audio chunks
                    while True:
                        m = await tts_ws.receive()
                        if m.type == WSMsgType.TEXT:
                            jd = m.json()
                            if jd.get("type") == "event" and jd.get("data", {}).get("event_type") == "final":
                                break
                            # if server ever sends error
                            if jd.get("type") == "error":
                                log.error(f"TTS error: {jd}")
                                break
                        elif m.type == WSMsgType.BINARY:
                            # Expecting base64? Sarvam TTS WS sends JSON (type=audio) usually, not raw bin.
                            continue
                        elif m.type == WSMsgType.TEXT:
                            # handled above
                            pass
                        else:
                            # Could be 'audio' frame in JSON: {"type":"audio","data":{"content_type":"audio/mulaw", "audio":"<b64>"}}
                            # Some servers deliver this as TEXT json; handle that form:
                            pass

                        # Try again: Sarvam TTS WS "audio" comes as JSON 'type':'audio'
                        if m.type == WSMsgType.TEXT:
                            jd = m.json()
                            if jd.get("type") == "audio":
                                au = jd.get("data", {})
                                b64 = au.get("audio")
                                if b64:
                                    raw_mulaw = base64.b64decode(b64)
                                    if EXOTEL_FORMAT == "twilio":
                                        # send Twilio-like "media" packet back (μ-law)
                                        out = make_twilio_media_msg_from_mulaw(base64.b64encode(raw_mulaw).decode("ascii"))
                                        await websocket.send_text(json.dumps(out))
                                    else:
                                        # simple {"audio":"<b64>","encoding":"mulaw","rate":8000}
                                        simple = {
                                            "audio": base64.b64encode(raw_mulaw).decode("ascii"),
                                            "encoding": "mulaw",
                                            "rate": OUT_SAMPLE_RATE
                                        }
                                        await websocket.send_text(json.dumps(simple))
            except Exception as e:
                log.error(f"TTS worker error: {e}")

        # Kick off tasks
        tasks = [
            asyncio.create_task(exotel_reader()),
            asyncio.create_task(stt_sender()),
            asyncio.create_task(stt_receiver()),
            asyncio.create_task(tts_worker()),
        ]

        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            log.error(f"Bridge gather error: {e}")
        finally:
            try:
                await stt_ws.close()
            except Exception:
                pass
            try:
                await tts_ws.close()
            except Exception:
                pass
            try:
                await websocket.close()
            except Exception:
                pass
            log.info("🔌 WebSocket bridge closed.")


# ---------- Simple health + trigger endpoints (HTTP) ----------

@app.get("/")
async def root():
    return {"status": "ok", "service": "Rupeek WebSocket Voicebot", "mode": EXOTEL_FORMAT}

@app.get("/ping")
async def ping():
    return {"pong": True}

# Exotel outbound trigger (unchanged)
import requests
from requests.auth import HTTPBasicAuth
from fastapi import Request

@app.post("/trigger_call")
async def trigger_call(req: Request):
    """POST JSON: {"mobile":"+91XXXXXXXXXX"} -> triggers outbound Exotel call."""
    body = await req.json()
    customer_number = body.get("mobile")
    if not customer_number:
        return JSONResponse({"error": "mobile number required"}, status_code=400)

    bot_ws_url = f"wss://{req.url.hostname}/ws"  # ensure wss public URL on Render
    if req.url.hostname is None:
        bot_ws_url = "wss://ai-calling-bot-rqw5.onrender.com/ws"

    payload = {
        "From": customer_number,
        "To": EXOPHONE,
        "CallerId": EXOPHONE,
        "Url": bot_ws_url,  # Voicebot applet expects this WS endpoint
        "CallType": "trans",
    }

    url = f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect"
    try:
        r = requests.post(url, data=payload, auth=HTTPBasicAuth(EXOTEL_API_KEY, EXOTEL_API_TOKEN), timeout=20)
        ok = r.status_code in (200, 201)
        return JSONResponse({"status": "success" if ok else "failed", "response": r.text}, status_code=(200 if ok else r.status_code))
    except Exception as e:
        return JSONResponse({"status": "failed", "error": str(e)}, status_code=500)


# ---------- Run (Render/Procfile runs this directly) ----------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    log.info(f"📞 Rupeek WebSocket Voicebot running on port {port}")
    uvicorn.run("ai_agent_v2:app", host="0.0.0.0", port=port, workers=1)
