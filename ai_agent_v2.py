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
    """Simple AI voice flow for Rupeek Personal Loan outbound bot."""
    if not user_message:
        return say(
            "Hi! I’m calling from Rupeek. We’re offering instant personal loans with low interest rates and quick approval. Would you like to know your pre-approved loan limit?"
        )

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
#  Exotel Voice Flow — Bot Response
# ----------------------------
@app.route("/voice_flow", methods=["POST", "GET"])
def voice_flow():
    """Exotel calls this URL when outbound call connects."""
    first_line = run_sales_flow()
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="female">{first_line}</Say>
</Response>"""
    return Response(xml, mimetype="text/xml")


# ----------------------------
#  Webhook for Speech Recognition (Optional STT Integration)
# ----------------------------
@app.route("/user_speech", methods=["POST"])
def user_speech():
    """Receives user's transcribed response and returns next bot message."""
    user_msg = request.form.get("SpeechResult") or request.json.get("message", "")
    bot_reply = run_sales_flow(user_msg)

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="female">{bot_reply}</Say>
</Response>"""
    return Response(xml, mimetype="text/xml")


# ----------------------------
#  Outbound Call Trigger — Exotel Connect API
# ----------------------------
@app.route("/trigger_call", methods=["POST"])
def trigger_call():
    """
    Triggers an outbound call via Exotel Connect API.
    Input JSON: {"mobile": "+919599388645"}
    """
    data = request.get_json(force=True)
    customer_number = data.get("mobile")

    if not customer_number:
        return jsonify({"error": "mobile number required"}), 400

    # Exotel credentials from environment
    EXOTEL_SID = os.getenv("EXOTEL_SID", "rupeekfintech13")
    EXOTEL_TOKEN = os.getenv("EXOTEL_TOKEN")
    EXOPHONE = os.getenv("EXOPHONE", "08069489493")
    EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")

    BOT_URL = "https://ai-calling-bot-rqw5.onrender.com/voice_flow"

    # ✅ As per Exotel docs: From = customer, To = your exophone
    payload = {
        "From": customer_number,
        "To": EXOPHONE,
        "CallerId": EXOPHONE,
        "Url": BOT_URL,
        "CallType": "trans",
    }

    url = f"https://{EXOTEL_SUBDOMAIN}/v1/Accounts/{EXOTEL_SID}/Calls/connect"
    print(f"📞 Triggering Exotel call to {customer_number}...")

    try:
        response = requests.post(
            url,
            data=payload,
            auth=HTTPBasicAuth(EXOTEL_SID, EXOTEL_TOKEN),
            timeout=15
        )

        if response.status_code in [200, 201]:
            print("✅ Outbound call triggered successfully!")
            return jsonify({
                "status": "success",
                "response": response.text
            }), 200
        elif response.status_code == 401:
            print("❌ Unauthorized – check Exotel SID/TOKEN in environment variables.")
            return jsonify({
                "status": "failed",
                "error": "Unauthorized (401)",
                "details": response.text
            }), 401
        elif response.status_code == 400:
            print("⚠️ Invalid Call Parameters – check 'From' and 'CallerId'.")
            return jsonify({
                "status": "failed",
                "error": "Bad Request (400)",
                "details": response.text
            }), 400
        else:
            print(f"❌ Exotel call trigger failed with {response.status_code}")
            return jsonify({
                "status": "failed",
                "response": response.text
            }), response.status_code

    except requests.exceptions.RequestException as e:
        print("❌ Exception while calling Exotel:", e)
        return jsonify({"error": "Exception while connecting to Exotel", "details": str(e)}), 500


# ----------------------------
#  Entry Point
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"📞 Rupeek outbound voice agent running on port {port}")
    app.run(host="0.0.0.0", port=port)
