import os
import json
import base64
import asyncio
import websockets
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from dotenv import load_dotenv
from requests.auth import HTTPBasicAuth
import requests

# ==========================================================
#  SETUP
# ==========================================================
load_dotenv()
app = FastAPI()
logging.basicConfig(level=logging.INFO)

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
EXOTEL_SID = os.getenv("EXOTEL_SID", "rupeekfintech13")
EXOTEL_API_KEY = os.getenv("EXOTEL_API_KEY")
EXOTEL_API_TOKEN = os.getenv("EXOTEL_API_TOKEN")
EXOPHONE = os.getenv("EXOPHONE", "08069489493")
EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")

BASE_URL = "https://ai-calling-bot-rqw5.onrender.com"

# ==========================================================
#  BOT LOGIC
# ==========================================================
def get_bot_reply(user_message=None):
    if not user_message:
        return "Hi! I’m calling from Rupeek. You have a pre-approved personal loan offer. Would you like to check your loan eligibility now?"

    user_message = user_message.lower()
    if "yes" in user_message:
        return "Great! Please open the Rupeek app. I will guide you step by step."
    elif "no" in user_message:
        return "No worries. Have a nice day!"
    elif "rate" in user_message or "interest" in user_message:
        return "The interest rate is personalized for each user. Once you reach the loan summary page, you’ll see the exact rate."
    else:
        return "Sorry, could you please repeat that?"

# ==========================================================
#  HEALTH CHECK
# ==========================================================
@app.get("/")
async def root():
    return {"status": "ok", "message": "Rupeek outbound WebSocket voice agent active"}

# ==========================================================
#  WEBSOCKET HANDLER
# ==========================================================
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logging.info("🔌 WebSocket connection accepted from Exotel")

    # Connect to Sarvam STT WebSocket
    stt_uri = (
        "wss://api.sarvam.ai/speech-to-text/ws?"
        "language-code=en-IN&model=saarika:v2.5&input_audio_codec=wav&sample_rate=8000"
    )
    tts_uri = "wss://api.sarvam.ai/text-to-speech/ws?model=bulbul:v2"

    try:
        async with websockets.connect(
            stt_uri, extra_headers={"Api-Subscription-Key": SARVAM_API_KEY}
        ) as stt_ws, websockets.connect(
            tts_uri, extra_headers={"Api-Subscription-Key": SARVAM_API_KEY}
        ) as tts_ws:

            logging.info("🧠 Connected to Sarvam STT & TTS")

            # Send TTS config
            await tts_ws.send(
                json.dumps(
                    {
                        "type": "config",
                        "data": {
                            "target_language_code": "en-IN",
                            "speaker": "anushka",
                            "output_audio_codec": "wav",
                            "speech_sample_rate": "8000",
                        },
                    }
                )
            )

            # Start initial greeting
            greeting = get_bot_reply()
            await tts_ws.send(json.dumps({"type": "text", "data": {"text": greeting}}))
            await tts_ws.send(json.dumps({"type": "flush"}))

            logging.info(f"👋 Sent greeting: {greeting}")

            # Receive and stream back the TTS audio chunks
            async for tts_msg in tts_ws:
                msg = json.loads(tts_msg)
                if msg.get("type") == "audio":
                    audio_data = base64.b64decode(msg["data"]["audio"])
                    await websocket.send_bytes(audio_data)
                elif msg.get("type") == "event" and msg["data"].get("event_type") == "final":
                    logging.info("✅ TTS playback complete")

            # Listen to caller input audio
            async for message in websocket.iter_bytes():
                await stt_ws.send(
                    json.dumps(
                        {
                            "audio": {
                                "data": base64.b64encode(message).decode(),
                                "sample_rate": "8000",
                                "encoding": "audio/wav",
                                "input_audio_codec": "wav",
                            }
                        }
                    )
                )

                stt_response = await stt_ws.recv()
                stt_msg = json.loads(stt_response)
                if stt_msg.get("type") == "data":
                    transcript = stt_msg["data"].get("transcript", "")
                    logging.info(f"🎤 User said: {transcript}")

                    reply = get_bot_reply(transcript)
                    logging.info(f"💬 Bot reply: {reply}")

                    await tts_ws.send(json.dumps({"type": "text", "data": {"text": reply}}))
                    await tts_ws.send(json.dumps({"type": "flush"}))

    except WebSocketDisconnect:
        logging.info("🔌 Exotel disconnected WebSocket")
    except Exception as e:
        logging.error(f"❌ WebSocket error: {e}")

# ==========================================================
#  EXOTEL TRIGGER (same as before)
# ==========================================================
@app.post("/trigger_call")
async def trigger_call(data: dict):
    customer_number = data.get("mobile")
    if not customer_number:
        return {"error": "mobile number required"}

    BOT_URL = f"{BASE_URL}/ws"
    payload = {
        "From": customer_number,
        "To": EXOPHONE,
        "CallerId": EXOPHONE,
        "Url": BOT_URL,
        "CallType": "trans",
    }

    url = f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect"
    response = requests.post(
        url, data=payload, auth=HTTPBasicAuth(EXOTEL_API_KEY, EXOTEL_API_TOKEN)
    )

    if response.status_code in [200, 201]:
        return {"status": "success", "response": response.text}
    else:
        return {"status": "failed", "response": response.text}

# ==========================================================
#  ENTRY POINT
# ==========================================================
if __name__ == "__main__":
    import uvicorn
    logging.info("📞 Rupeek WebSocket Voicebot running on port 8000")
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
