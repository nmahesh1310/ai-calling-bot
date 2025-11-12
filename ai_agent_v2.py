# ai_agent_v2.py
# Rupeek AI Voicebot integrated with Exotel Voicebot applet (HTTP POST bidirectional)
# Uses Sarvam TTS + STT to converse naturally.

import os
import json
import base64
import logging
import io
import wave
import audioop
import requests
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

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

IN_SAMPLE_RATE = 8000
OUT_SAMPLE_RATE = 8000

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"

# ---------------- Loan content ----------------
def run_sales_pitch() -> str:
    return ("Hi! I’m calling from Rupeek. You have a pre-approved personal loan offer. "
            "Would you like to check your loan eligibility now?")

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

def get_bot_reply(user_message: str) -> str:
    m = user_message.lower()
    if "yes" in m:
        return "Great! Please open the Rupeek app. I will guide you step by step."
    if "no" in m:
        return "No worries. Have a nice day!"
    for variants, ans in FAQ_BANK:
        if any(v in m for v in variants):
            return ans
    return "Sorry, could you please repeat that?"

# ---------------- Helpers ----------------
def decode_mulaw(b64_data: str) -> bytes:
    mulaw_bytes = base64.b64decode(b64_data)
    return audioop.ulaw2lin(mulaw_bytes, 2)

def encode_mulaw(pcm_data: bytes) -> str:
    mulaw_bytes = audioop.lin2ulaw(pcm_data, 2)
    return base64.b64encode(mulaw_bytes).decode()

def wav_bytes(pcm_data: bytes, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm_data)
    return buf.getvalue()

# ---------------- Sarvam APIs ----------------
def text_to_speech(text: str) -> bytes:
    """Convert text -> PCM16 audio using Sarvam."""
    payload = {
        "model": "bulbul:v2",
        "input": {"text": text},
        "voice": {"speaker": "anushka", "sample_rate": OUT_SAMPLE_RATE}
    }
    headers = {"api-subscription-key": SARVAM_API_KEY}
    log.info(f"🎤 TTS request: {text}")
    r = requests.post(SARVAM_TTS_URL, json=payload, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    audio_b64 = data.get("audio")
    if not audio_b64:
        raise ValueError("No audio returned from Sarvam TTS")
    return base64.b64decode(audio_b64)

def speech_to_text(pcm_data: bytes) -> str:
    """Convert PCM audio -> text via Sarvam STT."""
    wav_data = wav_bytes(pcm_data)
    files = {'file': ('audio.wav', wav_data, 'audio/wav')}
    data = {"model": "saarika:v2.5", "language_code": "en-IN"}
    headers = {"api-subscription-key": SARVAM_API_KEY}
    r = requests.post(SARVAM_STT_URL, files=files, data=data, headers=headers, timeout=20)
    try:
        j = r.json()
        return j.get("text") or j.get("transcript") or ""
    except Exception:
        return ""

# ---------------- Voicebot Endpoint ----------------
@app.post("/exotel/voicebot")
async def exotel_voicebot(req: Request):
    """
    Handles Exotel Voicebot POST events:
    { event: start | media | stop, media: {payload: <base64>} }
    """
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    event = body.get("event")
    log.info(f"📩 Received Exotel event: {event}")

    if event == "start":
        # Send greeting immediately
        greeting = run_sales_pitch()
        pcm_data = text_to_speech(greeting)
        mulaw_b64 = encode_mulaw(pcm_data)
        log.info("🗣️ Sending greeting audio back to Exotel.")
        return JSONResponse({
            "event": "media",
            "media": {"payload": mulaw_b64}
        })

    elif event == "media":
        payload = body.get("media", {}).get("payload")
        if not payload:
            return {"status": "no_audio"}

        pcm_data = decode_mulaw(payload)
        text = speech_to_text(pcm_data)
        log.info(f"👂 Heard from user: {text}")

        reply = get_bot_reply(text)
        pcm_reply = text_to_speech(reply)
        mulaw_reply = encode_mulaw(pcm_reply)

        return JSONResponse({
            "event": "media",
            "media": {"payload": mulaw_reply}
        })

    elif event == "stop":
        log.info("🛑 Exotel ended the call.")
        return {"status": "stopped"}

    else:
        log.warning("⚠️ Unknown event type.")
        return {"status": "ignored"}

# ---------------- Trigger Call ----------------
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
        "Url": f"{BASE_URL}/exotel/voicebot",
        "CallType": "trans",
    }

    url = f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect"
    r = requests.post(url, data=payload, auth=(EXOTEL_API_KEY, EXOTEL_API_TOKEN), timeout=20)
    return {"status": "ok", "response": r.text}

# ---------------- Health ----------------
@app.get("/")
async def root():
    return {"status": "ok", "service": "Rupeek AI Voicebot (HTTP Mode)"}

# ---------------- Run ----------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    log.info(f"📞 Rupeek AI Voicebot running on port {port}")
    uvicorn.run("ai_agent_v2:app", host="0.0.0.0", port=port)
