# ai_agent_v2.py
# Rupeek outbound voicebot: Exotel Voicebot <-> Sarvam STT/TTS Bridge
# Handles start/media/stop events properly to maintain call connection.

import os
import json
import base64
import logging
import requests
from requests.auth import HTTPBasicAuth
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

# ------------------- Setup -------------------
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("rupeek-ai")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# ------------------- ENV -------------------
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "").strip()
EXOTEL_SID = os.getenv("EXOTEL_SID", "rupeekfintech13")
EXOTEL_API_KEY = os.getenv("EXOTEL_API_KEY", "")
EXOTEL_API_TOKEN = os.getenv("EXOTEL_API_TOKEN", "")
EXOPHONE = os.getenv("EXOPHONE", "08069489493")
EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")
BASE_URL = os.getenv("BASE_URL", "https://ai-calling-bot-rqw5.onrender.com")

# ------------------- Loan pitch + FAQs -------------------
loan_steps = [
    "Open the Rupeek app.",
    "On the home screen, click the Cash banner.",
    "Check your pre-approved limit.",
    "Slide the slider to select the amount and tenure required.",
    "Tick the consent box to proceed.",
    "Add your bank account if not visible.",
    "Update your email ID and address, then proceed to mandate setup.",
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

def get_bot_reply(user_message: str) -> str:
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

# ------------------- Sarvam TTS Helper -------------------
def generate_tts_audio(text: str) -> str:
    """Generate μ-law base64 encoded audio for the given text via Sarvam."""
    tts_url = "https://api.sarvam.ai/text-to-speech"
    headers = {"api-subscription-key": SARVAM_API_KEY}
    data = {
        "model": "bulbul:v2",
        "input": text,
        "speaker": "anushka",
        "output_audio_format": "mulaw",
        "sample_rate": 8000
    }

    try:
        r = requests.post(tts_url, json=data, headers=headers, timeout=15)
        if r.status_code != 200:
            log.error(f"TTS failed: {r.text}")
            return ""
        return base64.b64encode(r.content).decode()
    except Exception as e:
        log.error(f"TTS generation error: {e}")
        return ""

# ------------------- Exotel Voicebot Handler -------------------
@app.post("/exotel/voicebot")
async def exotel_voicebot(req: Request):
    """Main entry for Exotel Voicebot bi-directional flow."""
    try:
        body = await req.json()
    except Exception:
        log.error("Invalid JSON from Exotel")
        return JSONResponse({"event": "stop"})

    event = body.get("event")
    log.info(f"📩 Received Exotel event: {event}")

    # ---- START EVENT ----
    if event == "start":
        greeting_text = run_sales_pitch()
        log.info(f"🗣️ Sending greeting: {greeting_text}")
        audio_data = generate_tts_audio(greeting_text)

        if not audio_data:
            log.error("Failed to generate greeting TTS")
            return JSONResponse({"event": "stop"})

        response = {
            "event": "media",
            "media": {"payload": audio_data}
        }

        log.info("🔁 Sent one TTS audio chunk to Exotel")
        log.info("✅ Greeting playback complete")
        return JSONResponse(content=response)

    # ---- MEDIA EVENT ----
    elif event == "media":
        payload = body.get("media", {}).get("payload", "")
        if not payload:
            return JSONResponse({"event": "continue"})

        log.info("🎧 Received user speech audio (ignored in this version)")
        # In next version: decode payload -> STT -> generate reply -> TTS -> send media back

        return JSONResponse({"event": "continue"})

    # ---- STOP EVENT ----
    elif event == "stop":
        log.info("🛑 Exotel session stopped normally")
        return JSONResponse({"event": "stop"})

    # ---- UNKNOWN EVENT ----
    log.warning(f"⚠️ Unknown event type received: {event}")
    return JSONResponse({"event": "continue"})

# ------------------- Trigger Call Endpoint -------------------
@app.post("/trigger_call")
async def trigger_call(req: Request):
    """Trigger outbound Exotel call."""
    body = await req.json()
    customer_number = body.get("mobile")
    if not customer_number:
        return JSONResponse({"error": "mobile number required"}, status_code=400)

    payload = {
        "From": customer_number,
        "To": EXOPHONE,
        "CallerId": EXOPHONE,
        "Url": f"https://{BASE_URL.replace('https://','')}/exotel/voicebot",
        "CallType": "trans",
    }

    url = f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect"
    try:
        r = requests.post(url, data=payload, auth=HTTPBasicAuth(EXOTEL_API_KEY, EXOTEL_API_TOKEN), timeout=20)
        log.info(f"📞 Triggered call via Exotel for {customer_number}")
        return JSONResponse({"status": "ok", "response": r.text}, status_code=r.status_code)
    except Exception as e:
        log.error(f"Call trigger failed: {e}")
        return JSONResponse({"status": "failed", "error": str(e)}, status_code=500)

# ------------------- Health Check -------------------
@app.get("/")
async def root():
    return {"status": "ok", "service": "Rupeek AI Voicebot", "version": "v1.0"}

# ------------------- Run -------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    log.info(f"📞 Rupeek AI Voicebot running on port {port}")
    uvicorn.run("ai_agent_v2:app", host="0.0.0.0", port=port)
