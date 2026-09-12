"""
FastAPI Application — main entry point for the College Assistant AI backend.

Endpoints:
    POST /webhook       — Receive incoming WhatsApp messages from Evolution API
    POST /query         — Direct query endpoint (for testing without WhatsApp)
    GET  /schedule      — Get upcoming calendar events
    GET  /health        — System health check
    GET  /groups        — List WhatsApp groups (for finding announcement group JID)

Run with:
    uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
"""

# ------------------------------------------------------------------ #
# Imports (consolidated at the top)
# ------------------------------------------------------------------ #
import asyncio
import base64 as b64mod
import io
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import PIL.Image
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.agents.agent import process_message
from src.agents.llm_router import llm_router
from src.agents.tools import init_tools
from src.api.models import (
    DeleteEventRequest,
    DeleteEventResponse,
    HealthResponse,
    QueryRequest,
    QueryResponse,
    ScheduleResponse,
)
from src.tools.time_tool import time_tool
from src.whatsapp.bot import WhatsAppBot
from src.whatsapp.calendar_sync import CalendarSync
from src.whatsapp.evolution_client import EvolutionClient
from src.whatsapp.group_listener import GroupListener
from src.drive import GoogleDriveClient
from src.drive.drive_indexer import drive_indexer

load_dotenv()

# ------------------------------------------------------------------ #
# Logging
# ------------------------------------------------------------------ #
# Resolve absolute path to the project root (3 levels up from main.py)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = DATA_DIR / "bot.log"
if not LOG_FILE.exists():
    LOG_FILE.touch()

# Console gets full diagnostic details
console = logging.StreamHandler()
console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S"))

# File gets ultra-clean, simple format (just Time + Message)
file_log = logging.FileHandler(str(LOG_FILE), encoding="utf-8")
file_log.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    handlers=[console, file_log]
)
logger = logging.getLogger(__name__)

# Silence third-party spam (FastAPI/HTTPX background noise)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

# ------------------------------------------------------------------ #
# Shared state (initialized at startup)
# ------------------------------------------------------------------ #
evolution_client: EvolutionClient | None = None
whatsapp_bot: WhatsAppBot | None = None
group_listener: GroupListener | None = None
calendar_sync: CalendarSync | None = None
drive_client: GoogleDriveClient | None = None


# ------------------------------------------------------------------ #
# Helpers shared by direct & group handlers
# ------------------------------------------------------------------ #

def _decode_image(media: dict | None) -> PIL.Image.Image | None:
    """Decode a base64 media dict into a PIL Image, or return None."""
    if not media or media.get("type") != "image" or not media.get("base64"):
        return None
    raw = media["base64"]
    clean = raw.split(",", 1)[1] if "," in raw else raw
    try:
        return PIL.Image.open(io.BytesIO(b64mod.b64decode(clean)))
    except Exception as e:
        logger.error("Failed to decode image: %s", e)
        return None


# ------------------------------------------------------------------ #
# Lifespan
# ------------------------------------------------------------------ #


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global evolution_client, whatsapp_bot, group_listener, calendar_sync, drive_client

    logger.info("🚀 Starting College Assistant AI...")

    # Initialize components
    evolution_client = EvolutionClient()
    announcement_jid = os.getenv("ANNOUNCEMENT_GROUP_JID", "")
    whatsapp_bot = WhatsAppBot(
        evolution_client=evolution_client,
        announcement_group_jid=announcement_jid,
    )
    group_listener = GroupListener(announcement_group_jid=announcement_jid)
    calendar_sync = CalendarSync()
    drive_client = GoogleDriveClient()

    # Wire tool dependencies so the agent can call calendar / LLM / Drive
    init_tools(
        calendar_sync=calendar_sync,
        llm_router=llm_router,
        drive_client=drive_client,
        drive_indexer=drive_indexer,
    )

    # Auto-index Level 4 on startup if index is empty and Drive is authorized
    if drive_indexer.count_items() == 0 and drive_client.is_authorized():
        logger.info("Drive index is empty on startup. Starting background crawl of Level 4...")
        asyncio.create_task(drive_indexer.sync_from_drive(drive_client))

    # ---- Message handlers ---------------------------------------- #

    @whatsapp_bot.on_direct_message
    @whatsapp_bot.on_group_message
    async def handle_message(message: dict) -> str | None:
        """Handle ALL messages (DM and Group) via the LLM agent."""
        text = message.get("text", "")
        sender = message.get("sender_name", "Student")
        jid = message.get("jid", "")
        image = _decode_image(message.get("media"))

        if not text and not image:
            logger.info("Message from %s: empty, ignoring", sender)
            return None

        # Route ALL messages through the tool-calling agent
        return await process_message(text, image, user_id=jid)


    # ---- Configure webhook on Evolution API with retry loop ------ #
    api_port = os.getenv("API_PORT", "8000")
    webhook_url = os.getenv("WEBHOOK_URL", f"http://python-backend:{api_port}/webhook")

    for attempt in range(1, 16):
        try:
            state = await evolution_client.get_connection_state()
            logger.info("WhatsApp connection state: %s", state)
            await evolution_client.set_webhook(
                webhook_url=webhook_url,
                events=["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
            )
            logger.info("✅ Evolution API webhook configured: %s", webhook_url)
            break
        except Exception as e:
            if attempt == 15:
                logger.error("❌ Failed to configure webhook after 15 attempts: %s", e)
            else:
                logger.warning("Waiting for Evolution API (attempt %d/15): %s", attempt, e)
                await asyncio.sleep(2)

    logger.info("✅ College Assistant AI ready!")
    yield
    logger.info("Shutting down...")
    if evolution_client:
        await evolution_client.close()


# ------------------------------------------------------------------ #
# FastAPI App
# ------------------------------------------------------------------ #
app = FastAPI(
    title="College Assistant AI",
    description="RAG-powered college assistant with multi-LLM verification and WhatsApp integration",
    version="0.1.0",
    lifespan=lifespan,
)


# ------------------------------------------------------------------ #
# Endpoints
# ------------------------------------------------------------------ #


@app.post("/webhook")
async def webhook(request: Request):
    """Receive incoming messages from Evolution API."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    logger.debug("Webhook payload: event=%s", payload.get("event"))

    if whatsapp_bot:
        asyncio.create_task(whatsapp_bot.handle_webhook(payload))

    return {"status": "received"}


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    """Direct query endpoint for testing without WhatsApp."""
    try:
        answer = await process_message(req.question)
        return QueryResponse(answer=answer, confidence=0.8, sources=[])
    except Exception as e:
        return QueryResponse(answer=f"Error: {e}", confidence=0.0, sources=[])


@app.get("/schedule", response_model=ScheduleResponse)
async def get_schedule(days: int = 7):
    """Get upcoming calendar events for the next N days."""
    if not calendar_sync:
        return ScheduleResponse(formatted="Calendar not initialized")
    try:
        events = await calendar_sync.get_upcoming_events(days=days)
        return ScheduleResponse(events=events, formatted=calendar_sync.format_schedule(events))
    except Exception as e:
        return ScheduleResponse(formatted=f"Error: {e}")


@app.post("/schedule/delete", response_model=DeleteEventResponse)
async def delete_schedule_event(req: DeleteEventRequest):
    """Delete calendar events matching query keyword and optional date."""
    if not calendar_sync:
        return DeleteEventResponse(message="Calendar not initialized")
    try:
        deleted = await calendar_sync.delete_events(query=req.query, date_str=req.date)
        if deleted:
            return DeleteEventResponse(
                deleted_events=deleted,
                message=f"Successfully deleted {len(deleted)} event(s): {', '.join(deleted)}",
            )
        return DeleteEventResponse(deleted_events=[], message=f"No upcoming events found matching '{req.query}'.")
    except Exception as e:
        return DeleteEventResponse(deleted_events=[], message=f"Error deleting event: {e}")


@app.get("/groups")
async def list_groups():
    """List all WhatsApp groups the bot is part of."""
    if not evolution_client:
        return {"error": "Evolution API not connected"}
    try:
        groups = await evolution_client.fetch_all_groups()
        return {
            "groups": [
                {"jid": g.get("id", ""), "name": g.get("subject", "Unknown"), "size": g.get("size", 0)}
                for g in groups
            ]
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/qr/json")
async def qr_json():
    """Return raw QR code base64 and connection state as JSON."""
    if not evolution_client:
        return JSONResponse({"error": "Evolution API not initialized"}, status_code=500)
    try:
        conn = await evolution_client.get_connection_state()
        state = conn.get("instance", {}).get("state", "")
        if state == "open":
            return {"state": "open", "connected": True}
        qr_data = await evolution_client.get_qr_code()
        return {
            "state": state,
            "connected": False,
            "base64": qr_data.get("base64", ""),
            "pairingCode": qr_data.get("pairingCode", ""),
            "count": qr_data.get("count", 1),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/qr/reset")
async def qr_reset():
    """Reset the WhatsApp instance and generate a clean session."""
    if not evolution_client:
        return JSONResponse({"error": "Evolution API not initialized"}, status_code=500)
    try:
        await evolution_client.reset_instance()
        api_port = os.getenv("API_PORT", "8000")
        webhook_url = os.getenv("WEBHOOK_URL", f"http://python-backend:{api_port}/webhook")
        await evolution_client.set_webhook(webhook_url=webhook_url)
        qr_data = await evolution_client.get_qr_code()
        return {
            "status": "ok",
            "base64": qr_data.get("base64", ""),
            "pairingCode": qr_data.get("pairingCode", ""),
        }
    except Exception as e:
        logger.error("Error resetting WhatsApp instance: %s", e)
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/qr", response_class=HTMLResponse)
async def qr_page():
    """Display interactive WhatsApp QR pairing interface."""
    if not evolution_client:
        return "<h3>Evolution API not initialized</h3>"

    try:
        conn = await evolution_client.get_connection_state()
        state = conn.get("instance", {}).get("state", "")
        if state == "open":
            return """
            <!DOCTYPE html>
            <html>
            <head><title>WhatsApp Connected</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
            <body style="font-family:system-ui,sans-serif; text-align:center; padding:50px; background:#f0fdf4;">
                <div style="max-width:480px; margin:0 auto; background:white; padding:40px; border-radius:16px; box-shadow:0 4px 20px rgba(0,0,0,0.08);">
                    <div style="font-size:64px; margin-bottom:16px;">✅</div>
                    <h1 style="color:#16a34a; margin:0 0 12px 0;">WhatsApp is Connected!</h1>
                    <p style="color:#4b5563; line-height:1.5;">The bot is active and listening for group announcements and direct messages.</p>
                </div>
            </body>
            </html>
            """
    except Exception:
        pass

    try:
        qr_data = await evolution_client.get_qr_code()
        b64 = qr_data.get("base64", "")
        pairing_code = qr_data.get("pairingCode", "")
    except Exception:
        b64, pairing_code = "", ""

    pairing_html = (
        f"<div style='margin-top:16px; background:#f1f5f9; padding:10px 16px; border-radius:8px;'>"
        f"<b>Pairing Code:</b> <code style='font-size:18px; color:#0f172a;'>{pairing_code}</code></div>"
        if pairing_code
        else ""
    )

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Link WhatsApp — College Assistant</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family:system-ui,-apple-system,sans-serif; background:#f8fafc; color:#1e293b; display:flex; justify-content:center; align-items:center; min-height:100vh; margin:0; padding:20px; }}
            .card {{ background:white; max-width:440px; width:100%; border-radius:20px; box-shadow:0 10px 30px rgba(0,0,0,0.08); padding:32px; text-align:center; box-sizing:border-box; }}
            h2 {{ margin:0 0 8px 0; font-size:22px; color:#0f172a; }}
            p.sub {{ color:#64748b; font-size:14px; margin:0 0 24px 0; }}
            .steps {{ text-align:left; background:#f8fafc; border:1px solid #e2e8f0; border-radius:12px; padding:14px 18px; font-size:13px; line-height:1.6; color:#334155; margin-bottom:20px; }}
            .qr-wrapper {{ position:relative; display:inline-block; margin:8px 0; }}
            .qr-img {{ border:3px solid #0f172a; border-radius:16px; width:260px; height:260px; display:block; }}
            .timer {{ font-size:13px; color:#64748b; margin-top:12px; }}
            .btn {{ margin-top:16px; background:#2563eb; color:white; border:none; padding:10px 20px; font-size:14px; font-weight:600; border-radius:10px; cursor:pointer; transition:0.2s; display:inline-flex; align-items:center; gap:8px; }}
            .btn:hover {{ background:#1d4ed8; }}
            .connected-box {{ display:none; background:#dcfce7; border:2px solid #22c55e; border-radius:16px; padding:24px; color:#15803d; }}
        </style>
    </head>
    <body>
        <div class="card">
            <div id="connecting-view">
                <h2>📱 Link WhatsApp Bot</h2>
                <p class="sub">Scan the QR code from WhatsApp on your phone</p>
                <div class="steps">
                    <b>1.</b> Open WhatsApp on your phone<br>
                    <b>2.</b> Tap <b>Settings</b> (or ⋮ Menu) &rarr; <b>Linked Devices</b><br>
                    <b>3.</b> Tap <b>Link a Device</b> and point at this code:
                </div>
                <div class="qr-wrapper">
                    <img id="qr-image" class="qr-img" src="{b64}" alt="Scan QR Code" />
                </div>
                {pairing_html}
                <div class="timer" id="timer-text">Refreshing in <span id="countdown">35</span>s...</div>
                <div>
                    <button class="btn" onclick="resetQR()">🔄 Get Fresh QR Code</button>
                </div>
            </div>
            <div id="connected-view" class="connected-box">
                <div style="font-size:48px; margin-bottom:12px;">🎉</div>
                <h2 style="color:#16a34a; margin:0 0 8px 0;">WhatsApp Connected!</h2>
                <p style="font-size:14px; margin:0; color:#166534;">The bot is now fully active, synchronized with Google Calendar, and ready to respond.</p>
            </div>
        </div>

        <script>
            let timeLeft = 35;
            const countdownEl = document.getElementById('countdown');
            const qrImg = document.getElementById('qr-image');
            const connectingView = document.getElementById('connecting-view');
            const connectedView = document.getElementById('connected-view');

            setInterval(() => {{
                timeLeft--;
                if (countdownEl) countdownEl.innerText = timeLeft;
                if (timeLeft <= 0) {{ fetchQR(); timeLeft = 35; }}
            }}, 1000);

            setInterval(async () => {{
                try {{
                    const res = await fetch('/health');
                    const data = await res.json();
                    if (data.whatsapp_connected) {{
                        connectingView.style.display = 'none';
                        connectedView.style.display = 'block';
                    }}
                }} catch (e) {{}}
            }}, 2500);

            async function fetchQR() {{
                try {{
                    const res = await fetch('/qr/json');
                    const data = await res.json();
                    if (data.connected) {{
                        connectingView.style.display = 'none';
                        connectedView.style.display = 'block';
                    }} else if (data.base64) {{
                        qrImg.src = data.base64;
                    }}
                }} catch (e) {{}}
            }}

            async function resetQR() {{
                timeLeft = 35;
                if (countdownEl) countdownEl.innerText = '...';
                try {{
                    const res = await fetch('/qr/reset', {{ method: 'POST' }});
                    const data = await res.json();
                    if (data.base64) {{ qrImg.src = data.base64; }}
                }} catch (e) {{ fetchQR(); }}
            }}
        </script>
    </body>
    </html>
    """


@app.get("/time")
async def get_time():
    """Return the bot's current timezone-aware date and time information."""
    return time_tool.get_current_time()


@app.get("/health", response_model=HealthResponse)
async def health():
    """System health check."""
    wa_connected = False
    client = evolution_client or EvolutionClient()
    try:
        state = await client.get_connection_state()
        inst_state = state.get("instance", {}).get("state") or state.get("state")
        wa_connected = inst_state == "open"
    except Exception as e:
        logger.error("Health check error: %s", e)

    return HealthResponse(status="ok", whatsapp_connected=wa_connected)


# ------------------------------------------------------------------ #
# Google Drive Endpoints
# ------------------------------------------------------------------ #

@app.get("/drive/search")
async def drive_search(q: str = "", file_type: str = "all", limit: int = 10):
    """Search indexed college drive materials."""
    if not q:
        return JSONResponse(status_code=400, content={"error": "Query parameter 'q' is required"})
    results = drive_indexer.search(query=q, file_type=file_type, limit=limit)
    return {
        "query": q,
        "file_type": file_type,
        "count": len(results),
        "results": results,
    }


@app.post("/drive/sync")
async def drive_sync():
    """Trigger a re-indexing crawl of the college Google Drive folder."""
    if not drive_client:
        return JSONResponse(status_code=500, content={"error": "Drive client not initialized"})
    if not drive_client.is_authorized():
        return JSONResponse(
            status_code=401,
            content={
                "error": "Google Drive is not authorized. Please run 'python setup_google.py' first."
            },
        )
    try:
        stats = await drive_indexer.sync_from_drive(drive_client)
        return stats
    except Exception as e:
        logger.error("Drive sync failed: %s", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/drive/stats")
async def drive_stats():
    """Return statistics on the indexed college drive."""
    stats = drive_indexer.get_stats()
    stats["authorized"] = drive_client.is_authorized() if drive_client else False
    return stats
