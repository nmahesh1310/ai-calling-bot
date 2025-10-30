from dotenv import load_dotenv
import os
import requests

load_dotenv()

SARVAM_API_KEY = os.getenv("SARVAM_API_KEY")
print("Loaded Sarvam API Key:", SARVAM_API_KEY)

url = "https://api.sarvam.ai/text-to-speech"
headers = {
    "x-api-key": SARVAM_API_KEY,
    "Content-Type": "application/json"
}
payload = {
    "text": "This is a test from Rupeek voice bot",
    "voice": "meera",
    "language": "en-IN"
}

try:
    response = requests.post(url, headers=headers, json=payload)
    print("Status Code:", response.status_code)
    print("Response:", response.text[:300])
except Exception as e:
    print("Error:", e)
