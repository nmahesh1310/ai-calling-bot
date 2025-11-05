import os
import json
import base64
import logging
import aiohttp
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
import requests

# ================================================================
#  SETUP
# ================================================================
load_dotenv()
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Rupeek WebSocket Voicebot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
EXOTEL_SID = os.getenv("EXOTEL_SID")
EXOTEL_API_KEY = os.getenv("EXOTEL_API_KEY")
EXOTEL_API_TOKEN = os.getenv("EXOTEL_API_TOKEN")
EXOPHONE = os.getenv("EXOPHONE", "")
EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")
BASE_URL = os.getenv("BASE_URL", "https://ai-calling-bot-rqw5.onrender.com")

SARVAM_STT_WS = "wss://api.sarvam.ai/speech-to-text/ws"
SARVAM_TTS_WS = "wss://api.sarvam.ai/text-to-speech/ws"

# ================================================================
#  BOT LOGIC
# ================================================================
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
     "The interest rate is personalized for each user. Once you reach the loan summary page, you'll see the exact rate."),
    (["open the app", "rupeek app"],
     "Please open the Rupeek app and I will guide you step by step to check your offer."),
    (["consent box", "tick box"],
     "There is a consent box on the screen. Please select it to proceed."),
    (["add bank", "bank not visible"],
     "Make sure the bank account belongs to you. If not, it won’t be accepted.")
]


def run_sales_pitch():
    return "Hi! I’m calling from Rupeek. You have a pre-approved personal loan offer. Would you like to check your loan eligibility now?"


def get_bot_reply(user_input=None):
    if not user_input:
        return run_sales_pitch()
    text = user_input.lower()
    if "yes" in text:
        return "Great! Please open the Rupeek app. I will guide you step by step."
    if "no" in text:
        return "No worries. Have a nice day!"
    for variants, answer in FAQ_BANK:
        if any(v in text for v in variants):
            return answer
    return "Sorry, could you please repeat that?"


# ================================================================
#  SARVAM WEBSOCKET INTEGRATION
# ================================================================
async def sarvam_stt(audio_bytes: bytes) -> str:
    """Stream caller audio to Sarvam STT and return transcript."""
    params = "?language-code=en-IN&model=saarika:v2.5&input_audio_codec=pcm_s16le&sample_rate=16000"
    headers = {"Api-Subscription-Key": SARVAM_API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(SARVAM_STT_WS + params, headers=headers) as ws:
            # Send audio
            msg = {
                "audio": {
                    "data": base64.b64encode(audio_bytes).decode(),
                    "sample_rate": "16000",
                    "encoding": "audio/wav",
                    "input_audio_codec": "pcm_s16le"
                }
            }
            await ws.send_str(json.dumps(msg))
            # Send flush signal
            await ws.send_str(json.dumps({"type": "flush"}))

            # Wait for transcription
            async for resp in ws:
                data = json.loads(resp.data)
                if data.get("type") == "data" and "transcript" in data["data"]:
                    return data["data"]["transcript"]
    return ""


async def sarvam_tts_stream(text: str):
    """Stream TTS response as audio bytes."""
    params = "?model=bulbul:v2&send_completion_event=true"
    headers = {"Api-Subscription-Key": SARVAM_API_KEY}
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(SARVAM_TTS_WS + params, headers=headers) as ws:
            # Step 1: Configure connection
            config = {
                "type": "config",
                "data": {
                    "target_language_code": "en-IN",
                    "speaker": "anushka",
                    "speech_sample_rate": "16000",
                    "output_audio_codec": "wav"
                }
            }
            await ws.send_str(json.dumps(config))
            # Step 2: Send text
            await ws.send_str(json.dumps({"type": "text", "data": {"text": text}}))
            await ws.send_str(json.dumps({"type": "flush"}))

            async for msg in ws:
                data = json.loads(msg.data) if msg.type == aiohttp.WSMsgType.TEXT else None
                if data and data.get("type") == "event":
                    if data["data"].get("event_type") == "final":
                        break
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    yield msg.data


# ================================================================
#  EXOTEL → SARVAM LIVE STREAM HANDLER
# ================================================================
@app.websocket("/voice_stream")
async def voice_stream(ws: WebSocket):
    await ws.accept()
    logging.info("🎧 Exotel connected to /voice_stream")

    try:
        # Initial greeting via TTS
        greeting = run_sales_pitch()
        async for chunk in sarvam_tts_stream(greeting):
            await ws.send_bytes(chunk)

        while True:
            msg = await ws.receive()
            if msg.type == aiohttp.WSMsgType.BINARY:
                # Caller speech
                transcript = await sarvam_stt(msg.data)
                if transcript:
                    logging.info(f"🗣️ Caller said: {transcript}")
                    bot_reply = get_bot_reply(transcript)
                    logging.info(f"🤖 Bot reply: {bot_reply}")
                    async for chunk in sarvam_tts_stream(bot_reply):
                        await ws.send_bytes(chunk)
            elif msg.type == aiohttp.WSMsgType.CLOSE:
                break
    except WebSocketDisconnect:
        logging.info("🔌 Exotel disconnected")
    except Exception as e:
        logging.error(f"❌ Error in voice_stream: {e}")
    finally:
        await ws.close()


# ================================================================
#  REST FALLBACK + TRIGGER CALL
# ================================================================
@app.get("/")
async def home():
    return JSONResponse({"status": "ok", "message": "Rupeek WebSocket voicebot active"})


@app.post("/voice_flow")
async def voice_flow():
    """Fallback: non-WS version (for testing only)."""
    line = run_sales_pitch()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>{line}</Say>
</Response>"""
    return Response(content=xml, media_type="text/xml")


@app.post("/trigger_call")
async def trigger_call(req: Request):
    data = await req.json()
    number = data.get("mobile")
    if not number:
        return JSONResponse({"error": "mobile required"}, 400)

    payload = {
        "From": number,
        "To": EXOPHONE,
        "CallerId": EXOPHONE,
        "Url": f"{BASE_URL}/voice_flow",
        "CallType": "trans"
    }
    resp = requests.post(
        f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect",
        data=payload,
        auth=HTTPBasicAuth(EXOTEL_API_KEY, EXOTEL_API_TOKEN)
    )
    return JSONResponse({"status": resp.status_code, "response": resp.text})
    

# ================================================================
#  ENTRY POINT
# ================================================================
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    logging.info(f"📞 Rupeek WebSocket Voicebot running on port {port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
