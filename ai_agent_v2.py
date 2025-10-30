import os
import json
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

# Load FAQs or any external data
if os.path.exists("faqs.json"):
    with open("faqs.json", "r") as f:
        faqs = json.load(f)
else:
    faqs = {}

app = Flask(__name__)

# ----------------------------
#  Mock/Helper functions
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
#  Flask endpoints
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
#  Entry point
# ----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🤖 Rupeek Voice Agent running on port {port}")
    app.run(host="0.0.0.0", port=port)
