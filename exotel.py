import requests
from requests.auth import HTTPBasicAuth

# === Exotel credentials ===
ACCOUNT_SID = "rupeek81"
API_KEY = "e21d764adb78d2b0fb5478fe7fa9589685d8d3e0a8568ed7"   # API Key (Username)
API_TOKEN = "771ca78b0d8d5acacf3c84bd459f96089d0af6ff6a16c0df"  # API Token (Password)
EXO_SUBDOMAIN = "api.exotel.com"

# === Call details ===
EXOPHONE = "09513886363"        # Your verified Exotel number
DESTINATION = "+919167124413"   # Customer number
CALL_URL = "http://my.exotel.in/exoml/start_voice/"  # Dummy XML for testing

# === Endpoint ===
url = f"https://{EXO_SUBDOMAIN}/v1/Accounts/{ACCOUNT_SID}/Calls/connect.json"

# === Payload with correct fields ===
payload = {
    "Caller": EXOPHONE,
    "CallType": "trans",
    "Destination": DESTINATION,
    "Url": CALL_URL
}

# === Make request ===
response = requests.post(url, data=payload, auth=HTTPBasicAuth(API_KEY, API_TOKEN))

print("Status Code:", response.status_code)
print("Response:", response.text)
