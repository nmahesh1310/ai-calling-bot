# ai_agent_v2.py
# Rupeek outbound voicebot: Exotel Voicebot <-> Sarvam STT/TTS WebSocket bridge
# FastAPI + Uvicorn | Real-time Exotel Voicebot integration

import os
import json
import base64
import asyncio
import logging
import wave
import io
from datetime import datetime
try:
    import audioop  # Python <3.13
except ModuleNotFoundError:
    import audioop_lts as audioop  # Python ≥3.13

from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import aiohttp
from aiohttp import ClientWebSocketResponse
from dotenv import load_dotenv
import requests
from requests.auth import HTTPBasicAuth

# ---------- Setup ----------
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("rupeek-ai")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# ---------- ENV ----------
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
EXOTEL_SID = os.getenv("EXOTEL_SID", "rupeekfintech13")
EXOTEL_API_KEY = os.getenv("EXOTEL_API_KEY", "")
EXOTEL_API_TOKEN = os.getenv("EXOTEL_API_TOKEN", "")
EXOPHONE = os.getenv("EXOPHONE", "08069489493")
BASE_URL = os.getenv("BASE_URL", "https://ai-calling-bot-rqw5.onrender.com")

IN_SAMPLE_RATE = int(os.getenv("IN_SAMPLE_RATE", "8000"))
OUT_SAMPLE_RATE = int(os.getenv("OUT_SAMPLE_RATE", "8000"))

# ---------- Sarvam URLs ----------
SARVAM_STT_URL = (
    "wss://api.sarvam.ai/speech-to-text/ws"
    f"?language-code=en-IN&model=saarika:v2.5&input_audio_codec=pcm_s16le"
    f"&sample_rate={IN_SAMPLE_RATE}&vad_signals=true"
)
SARVAM_TTS_URL = (
    "wss://api.sarvam.ai/text-to-speech/ws"
    "?model=bulbul:v2&send_completion_event=true"
)

# ---------- Directory for Audio Debug ----------
AUDIO_DEBUG_DIR = "/tmp/call_chunks"
os.makedirs(AUDIO_DEBUG_DIR, exist_ok=True)

# ---------- Bot Texts ----------
def run_sales_pitch() -> str:
    return "Hi! I’m calling from Rupeek. You have a pre-approved personal loan offer. Would you like to check your eligibility now?"

def get_bot_reply(user_message: str) -> str:
    if not user_message:
        return run_sales_pitch()
    m = user_message.lower()
    if "yes" in m:
        return "Great! Please open the Rupeek app. I will guide you step by step."
    if "no" in m:
        return "No worries. Have a nice day!"
    return "Sorry, could you please repeat that?"

# ---------- Utility ----------
def mulaw_b64_to_linear16(b64_payload: str) -> bytes:
    mulaw_bytes = base64.b64decode(b64_payload)
    return audioop.ulaw2lin(mulaw_bytes, 2)

# ---------- Sarvam Connectors ----------
async def sarvam_stt_connect(session: aiohttp.ClientSession) -> ClientWebSocketResponse:
    return await session.ws_connect(SARVAM_STT_URL, headers={"Api-Subscription-Key": SARVAM_API_KEY})

async def sarvam_tts_connect(session: aiohttp.ClientSession) -> ClientWebSocketResponse:
    return await session.ws_connect(SARVAM_TTS_URL, headers={"Api-Subscription-Key": SARVAM_API_KEY})

# ---------- NEW: Exotel Voicebot Handshake ----------
@app.api_route("/exotel/voicebot", methods=["GET", "POST"])
async def exotel_voicebot_handler(request: Request):
    """
    Handles both GET (for Exotel verification) and POST (for Voicebot handshake).
    Returns the WSS stream URL for bi-directional communication.
    """
    if request.method == "GET":
        log.info("🌐 Received Exotel GET verification request")
        return JSONResponse({"status": "ok", "message": "Exotel Voicebot endpoint reachable"})

    try:
        data = await request.json()
        log.info(f"📩 Received initial Exotel Voicebot handshake: {data}")
    except Exception:
        log.info("📩 Received Exotel Voicebot handshake (non-JSON payload)")

    wss_url = f"wss://{request.url.hostname}/ws"
    response = {
        "connect": {
            "stream": {"url": wss_url}
        }
    }
    log.info(f"🔗 Returning stream URL to Exotel: {wss_url}")
    return JSONResponse(content=response)

# ---------- WebSocket Bridge ----------
@app.websocket("/ws")
async def ws_bridge(websocket: WebSocket):
    """
    Exotel Voicebot bi-directional stream handler.
    1. Accepts Exotel's WSS connection.
    2. Bridges audio to Sarvam STT/TTS.
    """
    log.info("🛰️ Incoming WebSocket request from Exotel...")
    await websocket.accept()
    log.info("🔌 WebSocket connection accepted ✅")

    try:
        async with aiohttp.ClientSession() as session:
            stt_ws = await sarvam_stt_connect(session)
            tts_ws = await sarvam_tts_connect(session)

            # Send greeting via TTS
            greeting = run_sales_pitch()
            log.info(f"🗣️ Sending greeting: {greeting}")
            await tts_ws.send_json({
                "type": "text",
                "data": {"text": greeting}
            })

            # Listen and stream back
            while True:
                msg = await websocket.receive()

                if "text" in msg and msg["text"]:
                    data = json.loads(msg["text"])
                    event = data.get("event")

                    if event == "start":
                        log.info("🎙️ Stream started from Exotel")
                    elif event == "media":
                        payload = data["media"]["payload"]
                        pcm_data = mulaw_b64_to_linear16(payload)
                        await stt_ws.send_json({
                            "audio": {"data": base64.b64encode(pcm_data).decode()}
                        })
                    elif event == "stop":
                        log.info("🛑 Exotel Stream stopped")
                        break

                elif msg["type"] == "websocket.disconnect":
                    log.warning("⚡ WebSocket disconnected")
                    break

    except Exception as e:
        log.error(f"💥 Error in WebSocket bridge: {e}")

    finally:
        log.info("🔚 WebSocket closed.")

# ---------- Root ----------
@app.get("/")
async def root():
    return {"status": "ok", "service": "Rupeek AI Voicebot"}

# ---------- Trigger Call ----------
@app.post("/trigger_call")
async def trigger_call(req: Request):
    """
    Triggers an outbound Exotel call via API.
    """
    body = await req.json()
    customer_number = body.get("mobile")
    if not customer_number:
        return JSONResponse({"error": "mobile number required"}, status_code=400)

    exotel_api_url = f"https://api.exotel.com/v1/Accounts/{EXOTEL_SID}/Calls/connect.json"
    payload = {
        "From": customer_number,
        "To": EXOPHONE,
        "CallerId": EXOPHONE,
        "Url": f"https://ai-calling-bot-rqw5.onrender.com/exotel/voicebot",
    }

    try:
        r = requests.post(
            exotel_api_url,
            data=payload,
            auth=HTTPBasicAuth(EXOTEL_API_KEY, EXOTEL_API_TOKEN),
            timeout=20,
        )
        log.info(f"📞 Triggered call via Exotel for {customer_number} -> {r.status_code}")
        return JSONResponse({"status": "ok", "response": r.json()})
    except Exception as e:
        log.exception("❌ Error triggering Exotel call")
        return JSONResponse({"error": str(e)}, status_code=500)

# ---------- Run ----------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    log.info(f"📞 Rupeek AI Voicebot running on port {port}")
    uvicorn.run("ai_agent_v2:app", host="0.0.0.0", port=port)
