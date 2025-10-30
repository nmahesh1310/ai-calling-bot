import os
import json
import base64
import tempfile
import time
import requests
print("🎧 Audio libraries disabled for Render deployment (text-only mode).")
from dotenv import load_dotenv
import difflib  # for fuzzy matching FAQ questions

# ======================================================
# STEP 0: SETUP
# ======================================================
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")

if not GOOGLE_API_KEY or not SARVAM_API_KEY:
    raise ValueError("❌ Missing GOOGLE_API_KEY or SARVAM_API_KEY in .env")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"

SARVAM_TTS_SPEAKER = "anushka"
SARVAM_TTS_MODEL = "bulbul:v2"
SARVAM_TTS_CODEC = "wav"
SARVAM_STT_MODEL = "saarika:v2.5"

os.makedirs("audio", exist_ok=True)

# ======================================================
# STEP 0.5: LOAD FAQ KNOWLEDGE BASE
# ======================================================
FAQ_PATH = "faqs.json"
faq_data = []

if os.path.exists(FAQ_PATH):
    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        faq_data = data.get("faqs", data)
        print(f"📘 Loaded {len(faq_data)} FAQs from {FAQ_PATH}")
else:
    print("⚠️ No FAQ file found — Gemini will handle queries directly.")

# ======================================================
# STEP 0.6: IMPROVED FAQ MATCHING
# ======================================================
def normalize_text(text: str) -> str:
    text = text.lower()
    replacements = {
        "preapproved": "pre-approved",
        "limit amount": "limit",
        "loan limit": "pre-approved limit",
        "my limit": "pre-approved limit",
        "interest": "rate of interest",
        "emi": "installment",
        "duration": "tenure",
        "period": "tenure",
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text.strip()

def find_best_faq_match(user_query: str, threshold: float = 0.65):
    if not faq_data:
        return None
    query = normalize_text(user_query)
    questions = [normalize_text(item["question"]) for item in faq_data]
    best_match = difflib.get_close_matches(query, questions, n=1, cutoff=threshold)
    if best_match:
        matched_q = best_match[0]
        for item in faq_data:
            if normalize_text(item["question"]) == matched_q:
                return item["answer"]
    for item in faq_data:
        if any(word in query for word in normalize_text(item["question"]).split()):
            return item["answer"]
    return None

# ======================================================
# STEP 1: SARVAM TTS (text-only fallback)
# ======================================================
def text_to_speech_file(text: str):
    payload = {
        "text": text,
        "target_language_code": "en-IN",
        "speaker": SARVAM_TTS_SPEAKER,
        "model": SARVAM_TTS_MODEL,
        "output_audio_codec": SARVAM_TTS_CODEC
    }
    headers = {"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"}

    r = requests.post(SARVAM_TTS_URL, json=payload, headers=headers, timeout=20)
    r.raise_for_status()
    audio_b64 = r.json()["audios"][0]
    audio_bytes = base64.b64decode(audio_b64)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    tmp.write(audio_bytes)
    tmp.close()
    return tmp.name

def tts_and_play(text: str):
    """Text-only simulation on Render."""
    print("🗣️ Bot says:", text)

# ======================================================
# STEP 2: STT simulation (Render doesn’t support mic)
# ======================================================
def record_audio(filename="audio/input.wav", duration=5):
    print("(Simulated recording — Render does not support microphone input)")
    return filename

def stt_from_file(audio_file: str) -> str:
    """Simulate user input on Render by reading from console."""
    return input("💬 Type your response: ")

# ======================================================
# STEP 3: GEMINI QA
# ======================================================
def ask_gemini(user_query: str):
    faq_answer = find_best_faq_match(user_query)
    if faq_answer:
        print("💡 Matched FAQ response.")
        return faq_answer

    context = f"""
You are Rupeek’s intelligent assistant.
Answer only about Rupeek Personal Loans (interest rates, tenure, documents, processing time, eligibility, app usage).
Keep responses short, clear, and conversational.
If user says they are not interested, acknowledge politely and stop.
"""
    prompt = context + f"\nUser: {user_query}\nAssistant:"
    headers = {"Content-Type": "application/json", "x-goog-api-key": GOOGLE_API_KEY}
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    resp = requests.post(GEMINI_URL, headers=headers, json=payload, timeout=20)
    if resp.status_code == 200:
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    else:
        print("Gemini Error:", resp.text)
        return "I’m facing a technical issue right now."

# ======================================================
# STEP 4: SALES FLOW
# ======================================================
def run_sales_flow():
    pitch = (
        "Hi! I’m calling from Rupeek. "
        "We’re offering instant personal loans with low interest rates and quick approval. "
        "Would you like to know your pre-approved loan limit?"
    )
    print("\n🎤 Bot:", pitch)
    tts_and_play(pitch)

    user_text = stt_from_file("audio/input.wav").lower()
    print("👂 You said:", user_text)

    if any(word in user_text for word in ["no", "not interested", "later"]):
        closing = "Sure, no problem! You can always check your eligibility anytime in the Rupeek app. Have a great day!"
        print("Bot:", closing)
        tts_and_play(closing)
        return

    if any(word in user_text for word in ["yes", "interested", "okay", "ok"]):
        ack = "Great! Let me help answer any questions you have about Rupeek Personal Loans."
        print("Bot:", ack)
        tts_and_play(ack)

        while True:
            prompt = "Please ask your question about Rupeek Personal Loans. Type 'no doubts' to finish."
            print("Bot:", prompt)
            tts_and_play(prompt)

            query = stt_from_file("audio/input.wav")
            print("👂 You asked:", query)

            if query.strip() == "" or "no doubt" in query.lower():
                final = "Great! Whenever you’re ready, just open the Rupeek app and check your pre-approved loan limit."
                print("Bot:", final)
                tts_and_play(final)
                break

            response = ask_gemini(query)
            print("Bot:", response)
            tts_and_play(response)

    else:
        unclear = "Sorry, I couldn’t understand that. Could you please say if you’re interested or not?"
        print("Bot:", unclear)
        tts_and_play(unclear)

# ======================================================
# MAIN
# ======================================================
if __name__ == "__main__":
    print("🤖 Rupeek Voice Agent (Render Mode — Text I/O)\n")
    try:
        run_sales_flow()
    except KeyboardInterrupt:
        print("\n👋 Gracefully stopped Rupeek Voice Agent.")
