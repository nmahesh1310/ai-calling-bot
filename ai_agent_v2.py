import os
import json
import base64
import tempfile
import requests
from flask import Flask, request, jsonify, Response
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# ================================================================
#  SETUP
# ================================================================
load_dotenv()
app = Flask(__name__, static_folder="static")

# Environment Variables
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
EXOTEL_SID = os.getenv("EXOTEL_SID", "rupeekfintech13")
EXOTEL_API_KEY = os.getenv("EXOTEL_API_KEY")
EXOTEL_API_TOKEN = os.getenv("EXOTEL_API_TOKEN")
EXOPHONE = os.getenv("EXOPHONE", "08069489493")
EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")

# Base URL (Render service)
BASE_URL = "https://ai-calling-bot-rqw5.onrender.com"

# ================================================================
#  LOAN STEPS & FAQS
# ================================================================
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

# ================================================================
#  SARVAM TTS — Generate WAV File
# ================================================================
def text_to_speech_file(text: str):
    """Convert text to speech (wav) via Sarvam API and store in /static folder."""
    if not SARVAM_API_KEY:
        print("⚠️ SARVAM_API_KEY missing — skipping TTS.")
        return None

    url = "https://api.sarvam.ai/text-to-speech"
    payload = {
        "text": text,
        "target_language_code": "en-IN",
        "speaker": "anushka",
        "model": "bulbul:v2",
        "output_audio_codec": "wav"
    }
    headers = {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        audio_b64 = r.json().get("audios", [None])[0]
        if not audio_b64:
            print("⚠️ No audio returned from Sarvam API")
            return None

        # Save to static folder
        audio_bytes = base64.b64decode(audio_b64)
        filename = "tts_intro.wav"
        static_path = os.path.join(app.static_folder, filename)
        with open(static_path, "wb") as f:
            f.write(audio_bytes)

        print(f"✅ TTS file saved: {static_path}")
        return f"{BASE_URL}/static/{filename}"
    except Exception as e:
        print("❌ TTS generation failed:", e)
        return None


# ================================================================
#  BOT LOGIC
# ================================================================
def run_sales_pitch():
    """First greeting line for the call."""
    return "Hi! I’m calling from Rupeek. You have a pre-approved personal loan offer. Would you like to check your loan eligibility now?"


def get_bot_reply(user_message=None):
    """Return conversational text based on user input."""
    if not user_message:
        return run_sales_pitch()

    user_message = user_message.lower()
    if "yes" in user_message:
        return "Great! Please open the Rupeek app. I will guide you step by step."
    elif "no" in user_message:
        return "No worries. Have a nice day!"
    for variants, answer in FAQ_BANK:
        if any(v in user_message for v in variants):
            return answer
    return "Sorry, could you please repeat that?"


# ================================================================
#  ROUTES
# ================================================================
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Rupeek outbound voice agent active"})


# --- 1️⃣ VOICE FLOW (played during call) ---
@app.route("/voice_flow", methods=["POST", "GET"])
def voice_flow():
    """Called by Exotel when the call connects — returns XML with <Play> audio file."""
    line = get_bot_reply()
    print(f"🗣️ Sending to caller: {line}")

    tts_url = text_to_speech_file(line) or f"{BASE_URL}/static/tts_intro.wav"

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play>{tts_url}</Play>
</Response>"""
    return Response(xml, mimetype="text/xml")


# --- 2️⃣ TEST TTS ROUTE ---
@app.route("/test_tts", methods=["GET"])
def test_tts():
    """Generate test TTS and return its public URL."""
    text = "This is a test message from Rupeek bot. If you hear this, your TTS is working correctly."
    tts_url = text_to_speech_file(text)
    if not tts_url:
        return jsonify({"error": "TTS generation failed"}), 500
    return jsonify({"tts_url": tts_url})


# --- 3️⃣ EXOTEL CALL TRIGGER ---
@app.route("/trigger_call", methods=["POST"])
def trigger_call():
    """Trigger an outbound call through Exotel."""
    data = request.get_json(force=True)
    customer_number = data.get("mobile")

    if not customer_number:
        return jsonify({"error": "mobile number required"}), 400

    BOT_URL = f"{BASE_URL}/voice_flow"

    print(f"[DEBUG] EXOTEL_SID: {EXOTEL_SID}")
    print(f"[DEBUG] EXOTEL_API_KEY: {EXOTEL_API_KEY[:4]}****")
    print(f"[DEBUG] EXOTEL_API_TOKEN: {EXOTEL_API_TOKEN[:4]}****")
    print(f"[DEBUG] Calling: {customer_number}")

    payload = {
        "From": customer_number,
        "To": EXOPHONE,
        "CallerId": EXOPHONE,
        "Url": BOT_URL,
        "CallType": "trans"
    }

    response = requests.post(
        f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect",
        data=payload,
        auth=HTTPBasicAuth(EXOTEL_API_KEY, EXOTEL_API_TOKEN),
    )

    print(f"[DEBUG] Exotel response: {response.status_code}")
    print(response.text[:300])

    if response.status_code in [200, 201]:
        return jsonify({"status": "success", "response": response.text}), 200
    else:
        return jsonify({"status": "failed", "response": response.text}), response.status_code


# ================================================================
#  ENTRY POINT
# ================================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"📞 Rupeek outbound voice agent running on port {port}")
    app.run(host="0.0.0.0", port=port)
