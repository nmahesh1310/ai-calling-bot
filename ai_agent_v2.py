import os
import json
import requests
from flask import Flask, request, jsonify, Response
from requests.auth import HTTPBasicAuth
from dotenv import load_dotenv

# ----------------------------
#  Setup
# ----------------------------
load_dotenv()
app = Flask(__name__)

# ----------------------------
#  Core Conversation Logic
# ----------------------------
def say(text):
    print(f"🗣️ Bot says: {text}")
    return text

def run_sales_flow(user_message=None):
    """Simple AI voice flow for personal loan outbound bot."""
    if not user_message:
        return say("Hi! I’m calling from Rupeek. We’re offering instant personal loans with low interest rates and quick approval. Would you like to know your pre-approved loan limit?")

    user_message = user_message.lower()

    if "yes" in user_message:
        return say("Great! Please confirm your registered mobile number.")
    elif "no" in user_message:
        return say("No problem! Thank you for your time. Have a good day.")
    elif user_message.isdigit() and len(user_message) == 10:
        return say("Thanks! Checking your eligibility now...")
    elif "limit" in user_message or "eligibility" in user_message:
        return say("You are eligible for up to ₹2 lakh. Would you like to proceed?")
    elif "proceed" in user_message or "ok" in user_message:
        return say("Great! Whenever you’re ready, just open the Rupeek app and check your pre-approved loan limit.")
    else:
        return say("Sorry, could you please repeat that?")

# ----------------------------
#  Health Check
# ----------------------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Rupeek outbound voice agent active"})

# ----------------------------
#  Exotel Voice Flow (TTS/STT)
# ----------------------------
@app.route("/voice_flow", methods=["POST", "GET"])
def voice_flow():
    """
    Exotel will call this URL when the outbound call starts.
    For TTS–STT flow, it just speaks the bot message.
    """
    first_line = run_sales_flow()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="female">{first_line}</Say>
</Response>"""
    return Response(xml, mimetype='text/xml')

# ----------------------------
#  Webhook for user speech (optional)
# ----------------------------
@app.route("/user_speech", methods=["POST"])
def user_speech():
    """
    This endpoint receives user's transcribed speech (via Exotel STT or your ASR module)
    and returns the next bot reply as XML.
    """
    user_msg = request.form.get("SpeechResult") or request.json.get("message", "")
    bot_reply = run_sales_flow(user_msg)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="female">{bot_reply}</Say>
</Response>"""
    return Response(xml, mimetype='text/xml')

# ----------------------------
#  Outbound Call Trigger
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

    EXOTEL_SID = os.getenv("EXOTEL_SID")
    EXOTEL_TOKEN = os.getenv("EXOTEL_TOKEN")
    EXOPHONE = os.getenv("EXOPHONE")
    EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")

    BOT_URL = "https://your-render-app-url.onrender.com/voice_flow"

    url = f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect"

    payload = {
        "From": EXOPHONE,
        "To": customer_number,
        "CallerId": EXOPHONE,
        "Url": BOT_URL,
        "CallType": "trans",  # transactional
    }

    response = requests.post(
        url,
        data=payload,
        auth=HTTPBasicAuth(EXOTEL_SID, EXOTEL_TOKEN),
    )

    if response.status_code == 200:
        print("✅ Outbound call triggered successfully!")
        return jsonify({"status": "success", "response": response.json()})
    else:
        print(f"❌ Call trigger failed: {response.status_code}")
        return jsonify({"status": "failed", "response": response.text}), response.status_code

# ----------------------------
#  Entry Point
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"📞 Rupeek outbound voice agent running on port {port}")
    app.run(host="0.0.0.0", port=port)
