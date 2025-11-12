# ai_agent_v2.py
# Rupeek outbound voicebot: Exotel Stream <-> Sarvam STT/TTS WebSocket bridge
# FastAPI + Uvicorn | Handles real-time calls, fallback chunks, and audio logging.

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
    import audioop  # Python <3.13
except ModuleNotFoundError:
    import audioop_lts as audioop  # Python ≥3.13

from typing import Optional
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import aiohttp
from aiohttp import WSMsgType, ClientWebSocketResponse
from dotenv import load_dotenv
import requests
from requests.auth import HTTPBasicAuth

# ------------- Setup -------------
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("rupeek-ai")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# ---- ENV ----
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
EXOTEL_SID = os.getenv("EXOTEL_SID", "rupeekfintech13")
EXOTEL_API_KEY = os.getenv("EXOTEL_API_KEY", "")
EXOTEL_API_TOKEN = os.getenv("EXOTEL_API_TOKEN", "")
EXOPHONE = os.getenv("EXOPHONE", "08069489493")
EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")
BASE_URL = os.getenv("BASE_URL", "https://ai-calling-bot-rqw5.onrender.com")

EXOTEL_FORMAT = os.getenv("EXOTEL_FORMAT", "twilio").lower()
IN_SAMPLE_RATE = int(os.getenv("IN_SAMPLE_RATE", "8000"))
OUT_SAMPLE_RATE = int(os.getenv("OUT_SAMPLE_RATE", "8000"))

# ---- Sarvam URLs ----
SARVAM_STT_URL = (
    "wss://api.sarvam.ai/speech-to-text/ws"
    f"?language-code=en-IN&model=saarika:v2.5&input_audio_codec=pcm_s16le"
    f"&sample_rate={IN_SAMPLE_RATE}&high_vad_sensitivity=false&vad_signals=true"
)
SARVAM_TTS_URL = (
    "wss://api.sarvam.ai/text-to-speech/ws"
    "?model=bulbul:v2&send_completion_event=true"
)

# ---- Directory for Audio Debugging ----
AUDIO_DEBUG_DIR = "/tmp/call_chunks"
os.makedirs(AUDIO_DEBUG_DIR, exist_ok=True)

# ---------------- Bot content ----------------
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
    mulaw_bytes = base64.b64decode(b64_payload)
    return audioop.ulaw2lin(mulaw_bytes, 2)

def wav_bytes_from_linear16(pcm_lin16: bytes, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm_lin16)
    return buf.getvalue()

def save_audio_chunk(pcm_data: bytes, prefix: str = "chunk"):
    """Saves PCM data as a small WAV file for debugging."""
    timestamp = datetime.now().strftime("%H%M%S_%f")
    filepath = os.path.join(AUDIO_DEBUG_DIR, f"{prefix}_{timestamp}.wav")
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(IN_SAMPLE_RATE)
        wf.writeframes(pcm_data)
    log.info(f"🎧 Audio chunk saved: {filepath}")

def make_twilio_media_msg_from_mulaw(b64_payload: str) -> dict:
    return {"event": "media", "media": {"payload": b64_payload}}

def decode_exotel_inbound(msg: str, is_binary: bool) -> Optional[bytes]:
    if is_binary:
        try:
            return audioop.ulaw2lin(msg, 2)
        except Exception:
            return None
    try:
        data = json.loads(msg)
    except Exception:
        return None
    if data.get("event") == "media" and "media" in data:
        try:
            return mulaw_b64_to_linear16(data["media"]["payload"])
        except Exception:
            return None
    return None

# ---------------- Sarvam helpers ----------------
async def sarvam_stt_connect(session: aiohttp.ClientSession) -> ClientWebSocketResponse:
    return await session.ws_connect(SARVAM_STT_URL, headers={"Api-Subscription-Key": SARVAM_API_KEY})

async def sarvam_tts_connect(session: aiohttp.ClientSession) -> ClientWebSocketResponse:
    return await session.ws_connect(SARVAM_TTS_URL, headers={"Api-Subscription-Key": SARVAM_API_KEY})

async def send_audio_chunk_to_stt(ws: ClientWebSocketResponse, pcm_lin16: bytes):
    wav_chunk = wav_bytes_from_linear16(pcm_lin16, IN_SAMPLE_RATE)
    payload = {"audio": {"data": base64.b64encode(wav_chunk).decode(), "sample_rate": str(IN_SAMPLE_RATE), "encoding": "audio/wav"}}
    await ws.send_json(payload)

async def flush_stt(ws: ClientWebSocketResponse):
    await ws.send_json({"type": "flush"})

async def tts_send_config(ws: ClientWebSocketResponse):
    cfg = {
        "type": "config",
        "data": {"target_language_code": "en-IN", "speaker": "anushka",
                 "speech_sample_rate": str(OUT_SAMPLE_RATE),
                 "output_audio_codec": "mulaw", "output_audio_bitrate": "64k"}
    }
    await ws.send_json(cfg)

async def tts_send_text(ws: ClientWebSocketResponse, text: str):
    await ws.send_json({"type": "text", "data": {"text": text}})
    await ws.send_json({"type": "flush"})

# ---------------- WebSocket Bridge ----------------
@app.websocket("/ws")
async def ws_echo_debug(websocket: WebSocket):
    """
    Exotel stream debug mode with initial TTS greeting:
    - Sends initial sales pitch via Sarvam TTS immediately after accept
    - Streams TTS μ-law frames to Exotel
    - Then continues to echo incoming media frames for diagnostic verification
    """
    log.info("🛰️ Incoming WebSocket request from Exotel... waiting to accept...")
    try:
        await websocket.accept()
        log.info("🔌 WebSocket connection accepted from Exotel ✅")
    except Exception as e:
        log.error(f"❌ WS accept error: {e}")
        return

    session = None
    tts_ws = None
    try:
        # If SARVAM_API_KEY missing, log but continue (echo-only)
        if SARVAM_API_KEY:
            session = aiohttp.ClientSession()
            try:
                tts_ws = await sarvam_tts_connect(session)
                await tts_send_config(tts_ws)
            except Exception as e:
                log.error(f"❌ Could not connect to Sarvam TTS for greeting: {e}")
                # close session if opened
                try:
                    await session.close()
                except Exception:
                    pass
                session = None
                tts_ws = None

        # === Send initial greeting TTS (if available) ===
        if tts_ws:
            greeting = run_sales_pitch()
            log.info(f"🗣️ Sending initial greeting via Sarvam TTS: {greeting}")
            try:
                await tts_send_text(tts_ws, greeting)
                # Read TTS frames and forward to Exotel until final event
                while True:
                    m = await tts_ws.receive()
                    if m.type == WSMsgType.TEXT:
                        jd = m.json()
                        t = jd.get("type")
                        if t == "audio":
                            # Sarvam TTS audio b64 (μ-law) - forward as Twilio-style media
                            aud_b64 = (jd.get("data") or {}).get("audio")
                            if aud_b64:
                                out = make_twilio_media_msg_from_mulaw(aud_b64)
                                await websocket.send_text(json.dumps(out))
                        elif t == "event":
                            if (jd.get("data") or {}).get("event_type") == "final":
                                log.info("✅ Initial greeting playback finished.")
                                break
                        elif t == "error":
                            log.error(f"TTS error during greeting: {jd}")
                            break
                    else:
                        # ignore non-TEXT frames
                        continue
            except Exception as e:
                log.error(f"Error streaming initial greeting: {e}")
        else:
            log.info("ℹ️ Skipping initial TTS greeting (no Sarvam connection).")

        # === After greeting: enter echo/diagnostic loop ===
        while True:
            msg = await websocket.receive()

            # Handle binary audio
            if "bytes" in msg and msg["bytes"]:
                # Some clients might send raw μ-law bytes; echo them back
                log.info(f"🎧 Received {len(msg['bytes'])} raw bytes (binary)")
                try:
                    await websocket.send_bytes(msg["bytes"])
                    log.info("🔁 Echoed raw binary bytes back.")
                except Exception as e:
                    log.warning(f"Failed to echo binary bytes: {e}")
                continue

            # Handle text messages (Exotel JSON)
            if "text" in msg and msg["text"]:
                try:
                    data = json.loads(msg["text"])
                except Exception as e:
                    log.warning(f"JSON parse issue from Exotel message: {e}")
                    continue

                event = data.get("event")
                if event == "media":
                    media = data.get("media", {})
                    payload = media.get("payload")
                    if payload:
                        # Save a small debug WAV (converted to PCM16) for later inspection
                        try:
                            pcm = mulaw_b64_to_linear16(payload)
                            save_audio_chunk(pcm, prefix="exotel_in")
                        except Exception as e:
                            log.debug(f"Could not save audio chunk: {e}")

                        # Echo the same media payload back to Exotel (diagnostic)
                        out = {"event": "media", "media": {"payload": payload}}
                        try:
                            await websocket.send_text(json.dumps(out))
                            log.info(f"🔁 Echoed one media frame ({len(payload)} bytes b64)")
                        except Exception as e:
                            log.warning(f"Failed to echo media frame: {e}")
                    else:
                        log.info("⚠️ media event with empty payload")
                elif event == "start":
                    log.info("🎙️ Exotel Stream started")
                elif event == "stop":
                    log.info("🛑 Exotel Stream stopped by Exotel")
                    break
                else:
                    log.info(f"ℹ️ Received non-media event: {event}")

            if msg.get("type") == "websocket.disconnect":
                log.warning("⚡ WebSocket disconnected by Exotel")
                break

    except Exception as e:
        log.error(f"💥 WS runtime error: {e}")
    finally:
        # Cleanup Sarvam TTS session if opened
        try:
            if tts_ws:
                await tts_ws.close()
        except Exception:
            pass
        try:
            if session:
                await session.close()
        except Exception:
            pass

        log.info("🔚 Echo debug WebSocket closed.")
        try:
            await websocket.close()
        except Exception:
            pass

# ---------------- API Routes ----------------
@app.get("/")
async def root():
    return {"status": "ok", "service": "Rupeek AI Voicebot", "mode": EXOTEL_FORMAT}

@app.post("/trigger_call")
async def trigger_call(req: Request):
    body = await req.json()
    customer_number = body.get("mobile")
    if not customer_number:
        return JSONResponse({"error": "mobile number required"}, status_code=400)

    payload = {
        "From": customer_number,
        "To": EXOPHONE,
        "CallerId": EXOPHONE,
        "Url": f"wss://{req.url.hostname}/ws",
        "CallType": "trans",
    }
    url = f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect"
    r = requests.post(url, data=payload, auth=HTTPBasicAuth(EXOTEL_API_KEY, EXOTEL_API_TOKEN))
    return {"status": "ok", "response": r.text}

# ---------------- Run ----------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    log.info(f"📞 Rupeek AI Voicebot running on port {port}")
    uvicorn.run("ai_agent_v2:app", host="0.0.0.0", port=port)
