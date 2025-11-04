import os
import json
import base64
import requests
import tempfile
from flask import Flask, request, jsonify, Response
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# ----------------------------
#  Setup
# ----------------------------
load_dotenv()
app = Flask(__name__)

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
EXOTEL_SID = os.getenv("EXOTEL_SID", "rupeekfintech13")
EXOTEL_API_KEY = os.getenv("EXOTEL_API_KEY")
EXOTEL_API_TOKEN = os.getenv("EXOTEL_API_TOKEN")
EXOPHONE = os.getenv("EXOPHONE", "08069489493")
EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")

# ----------------------------
#  Loan data and FAQs (from Twilio version)
# ----------------------------
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
     "Make sure you're adding a bank account that belongs to you. If it doesn't match your name, it will not be accepted."),
]

# ----------------------------
#  Sarvam AI TTS + STT
# ----------------------------
def text_to_speech_file(text: str):
    """Generate a TTS file from Sarvam."""
    url = "https://api.sarvam.ai/text-to-speech"
    payload = {
        "text": text,
        "target_language_code": "en-IN",
        "speaker": "anushka",
        "model": "bulbul:v2",
        "output_audio_codec": "wav"
    }
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        r.raise_for_status()
        audio_b64 = r.json()["audios"][0]
        audio_bytes = base64.b64decode(audio_b64)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        tmp.write(audio_bytes)
        tmp.close()
        return tmp.name
    except Exception as e:
        print("TTS failed:", e)
        return None

def run_sales_pitch():
    """Starting line for outbound bot."""
    return "Hi! I’m calling from Rupeek. You have a pre-approved personal loan offer. Would you like to check your loan eligibility now?"

# ----------------------------
#  Core conversational flow (simplified for Exotel voice playback)
# ----------------------------
def get_bot_reply(user_message=None):
    """Return bot response text only — audio handled by Exotel."""
    if not user_message:
        return run_sales_pitch()
    user_message = user_message.lower()

    if "yes" in user_message:
        return "Great! Please open the Rupeek app. I will guide you step by step."
    if "no" in user_message:
        return "No worries. Have a nice day!"
    for variants, answer in FAQ_BANK:
        if any(v in user_message for v in variants):
            return answer
    return "Sorry, could you please repeat that?"

# ----------------------------
#  Voice flow for Exotel
# ----------------------------
@app.route("/voice_flow", methods=["POST", "GET"])
def voice_flow():
    """First message that plays when call connects."""
    first_line = get_bot_reply()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="female">{first_line}</Say>
</Response>"""
    return Response(xml, mimetype="text/xml")

# ----------------------------
#  Trigger outbound call (Exotel)
# ----------------------------
@app.route("/trigger_call", methods=["POST"])
def trigger_call():
    """
    Trigger an outbound call through Exotel.
    Input JSON: {"mobile": "+919599388645"}
    """
    data = request.get_json(force=True)
    customer_number = data.get("mobile")

    if not customer_number:
        return jsonify({"error": "mobile number required"}), 400

    # Read from environment
    EXOTEL_SID = os.getenv("EXOTEL_SID", "rupeekfintech13")
    EXOTEL_TOKEN = os.getenv("EXOTEL_TOKEN")
    EXOPHONE = os.getenv("EXOPHONE", "08069489493")
    EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")

    BOT_URL = "https://ai-calling-bot-rqw5.onrender.com/voice_flow"

    # --- Debug logs (safe masking) ---
    print(f"[DEBUG] Using EXOTEL_SID: {EXOTEL_SID}")
    print(f"[DEBUG] Using EXOTEL_TOKEN (first 4 chars): {EXOTEL_TOKEN[:4] if EXOTEL_TOKEN else 'None'}****")
    print(f"[DEBUG] Using EXOPHONE: {EXOPHONE}")
    print(f"[DEBUG] Exotel API endpoint: https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect")

    # Prepare payload
    payload = {
        "From": customer_number,   # customer number to be called
        "To": EXOPHONE,            # your Exophone
        "CallerId": EXOPHONE,
        "Url": BOT_URL,
        "CallType": "trans",
    }

    # Make the request
    response = requests.post(
        f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect",
        data=payload,
        auth=HTTPBasicAuth(EXOTEL_SID, EXOTEL_TOKEN),
    )

    print(f"[DEBUG] Exotel response status: {response.status_code}")
    print(f"[DEBUG] Exotel response text: {response.text[:300]}")  # truncate to avoid flooding logs

    # Return structured output
    if response.status_code in [200, 201]:
        print("✅ Outbound call triggered successfully!")
        return jsonify({
            "status": "success",
            "response": response.text
        }), 200
    else:
        print(f"❌ Call trigger failed: {response.status_code}")
        return jsonify({
            "status": "failed",
            "error": f"Unauthorized ({response.status_code})",
            "details": response.text
        }), response.status_code

# ----------------------------
#  Entry point
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"📞 Rupeek outbound voice agent running on port {port}")
    app.run(host="0.0.0.0", port=port)
