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
#  Load FAQs or external data
# ----------------------------
if os.path.exists("faqs.json"):
    with open("faqs.json", "r") as f:
        faqs = json.load(f)
else:
    faqs = {}

# ----------------------------
#  Helper / Core Logic
# ----------------------------
def say(text):
    print(f"🗣️ Bot says: {text}")
    return text

def run_sales_flow(user_message=None):
    """
    Core sales flow logic.
    Each call processes the user message and returns next bot line.
    """
    if not user_message:
        return say(
            "Hi! I’m calling from Rupeek. We’re offering instant personal loans with low interest rates and quick approval. Would you like to know your pre-approved loan limit?"
        )

    user_message = user_message.lower()

    if "yes" in user_message:
        return say("Great! Please confirm your registered mobile number.")
    elif "no" in user_message:
        return say("No worries! Thanks for your time. Have a great day.")
    elif user_message.isdigit() and len(user_message) == 10:
        return say("Thanks! Checking your eligibility...")
    elif "limit" in user_message:
        return say("You’re eligible for up to ₹2 lakh. Would you like to proceed?")
    else:
        return say("Sorry, could you please clarify that?")

# ----------------------------
#  Basic API Endpoints
# ----------------------------
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "ok", "message": "Rupeek Voice Agent API active"})

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    user_msg = data.get("message", "").strip()
    response = run_sales_flow(user_msg)
    return jsonify({"response": response})

# ----------------------------
#  Exotel Voice Flow (Inbound)
# ----------------------------
@app.route("/voice_flow", methods=["POST", "GET"])
def voice_flow():
    """
    Exotel will hit this endpoint when the call begins.
    """
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="female">Hi! I’m calling from Rupeek. We’re offering instant personal loans with low interest rates and quick approval. Would you like to know your pre-approved loan limit?</Say>
    <Gather numDigits="1" timeout="5" action="/handle_input" method="POST">
        <Say>Press 1 for Yes, 2 for No.</Say>
    </Gather>
</Response>"""
    return Response(xml, mimetype='text/xml')

@app.route("/handle_input", methods=["POST"])
def handle_input():
    """
    Exotel sends the user keypad input (1/2) to this endpoint.
    """
    digits = request.form.get("Digits")
    if digits == "1":
        message = "Great! Whenever you’re ready, just open the Rupeek app and check your pre-approved loan limit."
    else:
        message = "Alright, thank you for your time. Have a nice day!"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>{message}</Say>
    <Hangup/>
</Response>"""
    return Response(xml, mimetype='text/xml')

# ----------------------------
#  Exotel Outbound Call Trigger
# ----------------------------
@app.route("/trigger_call", methods=["POST"])
def trigger_call():
    """
    Trigger an outbound call via Exotel to connect user to the Voicebot.
    Input JSON: {"mobile": "+919599388645"}
    """
    data = request.get_json(force=True)
    customer_number = data.get("mobile")

    if not customer_number:
        return jsonify({"error": "mobile number required"}), 400

    EXOTEL_SID = os.getenv("EXOTEL_SID")
    EXOTEL_TOKEN = os.getenv("EXOTEL_TOKEN")
    EXOPHONE = os.getenv("EXOPHONE", "08069489493")  # default exophone
    EXOTEL_SUBDOMAIN = os.getenv("EXOTEL_SUBDOMAIN", "api.exotel.com")

    BOT_URL = "https://ai-calling-bot-rqw5.onrender.com/voice_flow"

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
        print("✅ Call triggered successfully!")
        return jsonify({"status": "success", "response": response.json()})
    else:
        print(f"❌ Call trigger failed: {response.status_code}")
        return jsonify({"status": "failed", "response": response.text}), response.status_code

# ----------------------------
#  Entry Point
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🤖 Rupeek Voice Agent running on port {port}")
    app.run(host="0.0.0.0", port=port)
