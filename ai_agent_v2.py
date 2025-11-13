# ai_agent_step1.py
# Step 1 — Minimal working Exotel Voicebot handshake
# Confirms Exotel can reach this app & get WSS URL for bi-directional stream.

import os
import json
import logging
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# -------------------- Setup --------------------
load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("rupeek-step1")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------- Healthcheck --------------------
@app.get("/")
async def root():
    return {"status": "ok", "stage": "step1-handshake"}

# -------------------- Voicebot Handshake --------------------
@app.api_route("/exotel/voicebot", methods=["GET", "POST"])
async def exotel_voicebot(request: Request):
    """
    Handles both GET (verification) and POST (Voicebot handshake) from Exotel.
    Returns a WSS stream URL for Exotel to connect.
    """

    if request.method == "GET":
        log.info("🌐 Received Exotel GET verification request ✅")
        return JSONResponse({"status": "ok", "message": "Exotel Voicebot endpoint reachable"})

    # POST - Exotel sends call metadata before opening WS
    try:
        data = await request.json()
        log.info(f"📩 Received Exotel Voicebot handshake: {json.dumps(data, indent=2)}")
    except Exception:
        log.warning("📩 Received non-JSON Voicebot handshake")
        data = {}

    wss_url = f"wss://{request.url.hostname}/ws"
    log.info(f"🔗 Returning stream URL to Exotel: {wss_url}")

    response = {
        "connect": {
            "stream": {"url": wss_url}
        }
    }
    return JSONResponse(content=response)

# -------------------- WebSocket Endpoint --------------------
@app.websocket("/ws")
async def ws_debug(websocket: WebSocket):
    """
    Simple diagnostic WS endpoint that accepts connection from Exotel.
    No audio or AI processing yet.
    """
    log.info("🛰️ Incoming WebSocket connection request from Exotel...")
    await websocket.accept()
    log.info("🔌 WebSocket connected ✅ (diagnostic mode)")

    try:
        while True:
            msg = await websocket.receive_text()
            log.info(f"📥 Received WS text frame: {msg[:100]}...")
            # Just echo to keep Exotel connection open
            await websocket.send_text(msg)
    except Exception as e:
        log.warning(f"⚡ WS closed or error: {e}")
    finally:
        log.info("🔚 WebSocket closed.")

# -------------------- Run --------------------
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    log.info(f"📞 Rupeek AI Voicebot Step1 running on port {port}")
    uvicorn.run("ai_agent_step1:app", host="0.0.0.0", port=port)
