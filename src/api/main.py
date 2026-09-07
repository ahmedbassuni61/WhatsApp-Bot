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

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.api.models import (
    DeleteEventRequest,
    DeleteEventResponse,
    HealthResponse,
    QueryRequest,
    QueryResponse,
    ScheduleResponse,
)
from src.whatsapp.bot import WhatsAppBot
from src.whatsapp.calendar_sync import CalendarSync
from src.whatsapp.evolution_client import EvolutionClient
from src.whatsapp.group_listener import GroupListener

load_dotenv()

# ------------------------------------------------------------------ #
# Logging
# ------------------------------------------------------------------ #
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# Shared state (initialized at startup)
# ------------------------------------------------------------------ #
evolution_client: EvolutionClient | None = None
whatsapp_bot: WhatsAppBot | None = None
group_listener: GroupListener | None = None
calendar_sync: CalendarSync | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    global evolution_client, whatsapp_bot, group_listener, calendar_sync

    logger.info("🚀 Starting College Assistant AI...")

    # Initialize Evolution API client
    evolution_client = EvolutionClient()

    # Initialize WhatsApp bot
    announcement_jid = os.getenv("ANNOUNCEMENT_GROUP_JID", "")
    whatsapp_bot = WhatsAppBot(
        evolution_client=evolution_client,
        announcement_group_jid=announcement_jid,
    )

    # Initialize group listener
    group_listener = GroupListener(announcement_group_jid=announcement_jid)

    # Initialize calendar sync
    calendar_sync = CalendarSync()

    # ---- Wire up message handlers ---- #

    @whatsapp_bot.on_direct_message
    async def handle_direct(message: dict) -> str:
        """Handle direct student messages — Q&A and on-demand calendar management."""
        text = message.get("text", "")
        sender = message.get("sender_name", "Student")

        media = message.get("media")
        has_image = bool(media and media.get("type") == "image" and media.get("base64"))

        if not text and not has_image:
            return "Please send a question or an image to analyze! 📚"

        # If direct message contains an image, check if it's a schedule/timetable or study question
        if has_image:
            import io
            import PIL.Image
            import base64 as b64mod
            from src.agents.llm_router import llm_router

            clean_b64 = media["base64"]
            if "," in clean_b64:
                clean_b64 = clean_b64.split(",")[1]
            try:
                img_bytes = b64mod.b64decode(clean_b64)
                pil_img = PIL.Image.open(io.BytesIO(img_bytes))

                # 1. Attempt to parse as timetable/schedule
                events = await group_listener._parse_schedule(text, media)
                if events:
                    confirmations = []
                    for event in events:
                        action = event.get("action", "create")
                        title = event.get("title", "")
                        date_str = event.get("date")
                        time_start = event.get("time_start")

                        if action == "cancel":
                            deleted = await calendar_sync.delete_events(query=title, date_str=date_str)
                            if deleted:
                                confirmations.append(f"🗑️ *Cancelled:* {', '.join(deleted)}")
                        else:
                            created = await calendar_sync.create_event(event)
                            if created:
                                time_text = f" on {date_str}" if date_str else ""
                                if time_start:
                                    time_text += f" at {time_start}"
                                confirmations.append(f"• *{created.get('summary')}*{time_text}")
                            else:
                                confirmations.append(f"• ℹ️ *{title}* (already on calendar)")

                    if confirmations:
                        return (
                            f"📅 *Timetable Processed & Synced to Google Calendar!*\n\n"
                            + "\n".join(confirmations)
                            + "\n\n🔔 Automatic reminders have been scheduled."
                        )

                # 2. Otherwise treat as academic study question
                prompt_text = text or "Please solve or describe what is in this image, explain any concepts, or extract any key information."
                res = await llm_router.generate(prompt_text, image=pil_img)
                return f"🤖 {res}"
            except Exception as img_err:
                logger.error("Failed to process image with LLM: %s", img_err)
                return "⚠️ Could not process the attached image. Please try again."

        clean = text.strip()
        lower = clean.lower()

        # 1. Quick command shortcuts for schedule
        if lower in ("/schedule", "/مواعيد", "schedule", "مواعيد", "جدول", "what is my schedule", "show schedule"):
            try:
                events = await calendar_sync.get_upcoming_events(days=30)
                return calendar_sync.format_schedule(events)
            except Exception as e:
                logger.error("Calendar error: %s", e)
                return "⚠️ Calendar error. Please verify Google Calendar credentials."

        # 2. Quick command shortcuts for event deletion
        if lower.startswith(("/delete", "/remove", "/cancel")) or any(lower.startswith(w) for w in ["delete all", "امسح كل", "احذف كل", "مسح الكل", "حذف الكل"]):
            if lower.startswith(("/delete", "/remove", "/cancel")):
                parts = clean.split(maxsplit=1)
                target = parts[1].strip() if len(parts) > 1 else ""
            else:
                target = clean
            if not target:
                return "Please specify what to delete. Example: `/delete math exam` or `/delete all`"
            try:
                deleted = await calendar_sync.delete_events(query=target)
                if deleted:
                    return f"🗑️ *Deleted from Google Calendar:*\n• " + "\n• ".join(deleted)
                return f"🔍 No upcoming calendar events matched: *{target}*"
            except Exception as e:
                logger.error("Calendar deletion error: %s", e)
                return f"⚠️ Failed to delete event: {e}"

        # 3. Check for natural language calendar intents (Arabic or English)
        is_delete_intent = any(w in lower for w in [
            "delete", "remove", "cancel", "احذف", "امسح", "الغي", "إلغاء", "شيل"
        ]) and any(w in lower for w in [
            "exam", "lab", "lecture", "calendar", "event", "session", "schedule", "امتحان", "سكشن", "محاضرة", "كالندر", "ميعاد", "جدول"
        ])

        if is_delete_intent:
            try:
                import json
                from src.agents.llm_router import llm_router
                extract_prompt = f"""Extract the event title or subject keyword the user wants to delete/remove from their calendar.
User message: "{text}"

Return ONLY a JSON object with:
- "query": string (e.g. "Math Exam", "CS201", "all")
- "date": string (ISO YYYY-MM-DD if mentioned, or null)"""
                resp = await llm_router.generate(extract_prompt)
                raw = resp.strip()
                if raw.startswith("```"):
                    raw = "\n".join([l for l in raw.splitlines() if not l.strip().startswith("```")])
                info = json.loads(raw)
                target = info.get("query") or text
                deleted = await calendar_sync.delete_events(query=target, date_str=info.get("date"))
                if deleted:
                    return f"🗑️ *Deleted from Google Calendar:*\n• " + "\n• ".join(deleted)
                return f"🔍 No upcoming calendar events found matching: *{target}*"
            except Exception as e:
                logger.warning("Intent deletion error: %s", e)

        # 4. Standard Academic Study Assistant
        try:
            from src.agents.llm_router import llm_router
            prompt = (
                f"You are a helpful college study assistant. "
                f"Answer the following student question concisely:\n\n{text}"
            )
            result = await llm_router.generate(prompt)
            return f"🤖 {result}"
        except Exception as e:
            logger.error("LLM generation error: %s", e)
            return "Sorry, I'm having trouble right now. Please try again in a moment. 🔧"

    @whatsapp_bot.on_group_message
    async def handle_group(message: dict) -> str | None:
        """Handle group messages — parse for schedule additions or cancellations and confirm in chat."""
        text = message.get("text", "").strip()
        lower = text.lower()

        # Check for group schedule inquiry
        if lower in ("/schedule", "/مواعيد", "schedule", "مواعيد", "جدول", "what is my schedule", "show schedule"):
            try:
                events = await calendar_sync.get_upcoming_events(days=30)
                return calendar_sync.format_schedule(events)
            except Exception as e:
                logger.error("Group schedule error: %s", e)
                return "⚠️ Calendar error retrieving schedule."

        # Check for group deletion command
        if lower.startswith(("/delete", "/remove", "/cancel")) or any(lower.startswith(w) for w in ["delete all", "امسح كل", "احذف كل", "مسح الكل", "حذف الكل"]):
            if lower.startswith(("/delete", "/remove", "/cancel")):
                parts = text.split(maxsplit=1)
                target = parts[1].strip() if len(parts) > 1 else ""
            else:
                target = text
            try:
                deleted = await calendar_sync.delete_events(query=target)
                if deleted:
                    return f"🗑️ *Deleted from Google Calendar:*\n• " + "\n• ".join(deleted)
                return f"🔍 No calendar events matched: *{target}*"
            except Exception as e:
                logger.error("Group calendar deletion error: %s", e)
                return f"⚠️ Failed to delete event: {e}"

        events = await group_listener.handle_group_message(message)
        if not events:
            return None

        confirmations = []
        for event in events:
            action = event.get("action", "create")
            title = event.get("title", "")
            date_str = event.get("date")
            time_start = event.get("time_start")
            location = event.get("location")

            try:
                if action == "cancel":
                    deleted = await calendar_sync.delete_events(query=title, date_str=date_str)
                    if deleted:
                        confirmations.append(
                            f"🗑️ *Cancelled & Removed from Google Calendar:*\n• " + "\n• ".join(deleted)
                        )
                    else:
                        confirmations.append(
                            f"⚠️ Cancellation noted for *{title}*, but no matching event was found on your calendar."
                        )
                else:
                    created = await calendar_sync.create_event(event)
                    if created:
                        time_text = f" on {date_str}" if date_str else ""
                        if time_start:
                            time_text += f" at {time_start}"
                        loc_text = f"\n📍 Location: {location}" if location else ""
                        confirmations.append(
                            f"📅 *Added to Google Calendar!*\n"
                            f"📌 *{created.get('summary')}*\n"
                            f"🕐 {time_text.strip()}{loc_text}\n"
                            f"🔔 Reminders set: 1 hr & 15 mins before."
                        )
                    else:
                        confirmations.append(f"ℹ️ *{title}* is already on your calendar for this date.")
            except Exception as e:
                logger.error("Failed to process announcement event '%s': %s", title, e)

        if confirmations:
            return "\n\n".join(confirmations)
        return None

    # ---- Configure webhook on Evolution API with retry loop ---- #
    import asyncio
    api_port = os.getenv("API_PORT", "8000")
    webhook_url = os.getenv("WEBHOOK_URL", f"http://python-backend:{api_port}/webhook")

    max_retries = 15
    for attempt in range(1, max_retries + 1):
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
            if attempt == max_retries:
                logger.error("❌ Failed to configure Evolution API webhook after %d attempts: %s", max_retries, e)
            else:
                logger.warning("Waiting for Evolution API (attempt %d/%d): %s", attempt, max_retries, e)
                await asyncio.sleep(2)

    logger.info("✅ College Assistant AI ready!")

    yield  # App is running

    # Shutdown
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
    """
    Receive incoming messages from Evolution API.

    Evolution API forwards WhatsApp events (messages, connection updates)
    to this endpoint as JSON payloads.
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    if whatsapp_bot:
        # Process in background (don't block the webhook response)
        import asyncio
        asyncio.create_task(whatsapp_bot.handle_webhook(payload))

    # Must return 200 quickly to avoid Evolution API retry
    return {"status": "received"}


@app.post("/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    """
    Direct query endpoint for testing without WhatsApp.

    Send a question and get an AI response with sources.
    """
    try:
        from src.agents.llm_router import llm_router
        ans = await llm_router.generate(
            f"You are a helpful college study assistant. "
            f"Answer the following question concisely:\n\n{request.question}"
        )
        return QueryResponse(
            answer=ans,
            confidence=0.8,
            sources=[],
        )
    except Exception as e:
        return QueryResponse(
            answer=f"Error: {str(e)}",
            confidence=0.0,
            sources=[],
        )


@app.get("/schedule", response_model=ScheduleResponse)
async def get_schedule(days: int = 7):
    """Get upcoming calendar events for the next N days."""
    if not calendar_sync:
        return ScheduleResponse(formatted="Calendar not initialized")

    try:
        events = await calendar_sync.get_upcoming_events(days=days)
        formatted = calendar_sync.format_schedule(events)
        return ScheduleResponse(events=events, formatted=formatted)
    except Exception as e:
        return ScheduleResponse(formatted=f"Error: {str(e)}")


@app.post("/schedule/delete", response_model=DeleteEventResponse)
async def delete_schedule_event(request: DeleteEventRequest):
    """Delete calendar events matching query keyword and optional date."""
    if not calendar_sync:
        return DeleteEventResponse(message="Calendar not initialized")

    try:
        deleted = await calendar_sync.delete_events(query=request.query, date_str=request.date)
        if deleted:
            return DeleteEventResponse(
                deleted_events=deleted,
                message=f"Successfully deleted {len(deleted)} event(s): {', '.join(deleted)}",
            )
        return DeleteEventResponse(
            deleted_events=[],
            message=f"No upcoming events found matching '{request.query}'.",
        )
    except Exception as e:
        return DeleteEventResponse(deleted_events=[], message=f"Error deleting event: {str(e)}")


@app.get("/groups")
async def list_groups():
    """
    List all WhatsApp groups the bot is part of.
    Use this to find the JID of the announcement group.
    """
    if not evolution_client:
        return {"error": "Evolution API not connected"}

    try:
        groups = await evolution_client.fetch_all_groups()
        return {
            "groups": [
                {
                    "jid": g.get("id", ""),
                    "name": g.get("subject", "Unknown"),
                    "size": g.get("size", 0),
                }
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
    except Exception as e:
        b64 = ""
        pairing_code = ""

    pairing_html = f"<div style='margin-top:16px; background:#f1f5f9; padding:10px 16px; border-radius:8px;'><b>Pairing Code:</b> <code style='font-size:18px; color:#0f172a;'>{pairing_code}</code></div>" if pairing_code else ""

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

            // Countdown timer
            setInterval(() => {{
                timeLeft--;
                if (countdownEl) countdownEl.innerText = timeLeft;
                if (timeLeft <= 0) {{
                    fetchQR();
                    timeLeft = 35;
                }}
            }}, 1000);

            // Fast connection checker (every 2.5s)
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
                    if (data.base64) {{
                        qrImg.src = data.base64;
                    }}
                }} catch (e) {{
                    fetchQR();
                }}
            }}
        </script>
    </body>
    </html>
    """


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

    return HealthResponse(
        status="ok",
        whatsapp_connected=wa_connected,
    )
