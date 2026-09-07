"""
Group Listener — monitors WhatsApp announcement groups for schedule events.

Listens for messages in a designated announcement group, uses an LLM (Gemini)
to parse unstructured schedule text into structured event objects, and
forwards them to the Calendar Sync module.

Examples of announcement messages it can parse:
- "Lab session for CS201 on Tuesday 14/10 at 2:00 PM in Hall B"
- "Reminder: Data Structures exam next Thursday 9 AM"
- "مواعيد السكاشن الاسبوع ده: الاحد 10 صباحا - الثلاثاء 2 ظهرا"
"""

import json
import logging
import os
from datetime import datetime

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Configure Gemini for schedule parsing
genai.configure(api_key=os.getenv("GEMINI_API_KEY", ""))

SCHEDULE_PARSE_PROMPT = """You are an intelligent college schedule parsing assistant.
Analyze the following WhatsApp message (and attached schedule/timetable image if present).
Extract the relevant academic schedule items (exams, lectures, labs, deadlines).

GUIDELINES FOR LARGE / COMPLEX TIMETABLE IMAGES:
1. If the image is a multi-column university-wide timetable containing many programs/departments:
   - Check if the user specified a department, year, or course in the message (e.g., 'اتصالات', 'حاسبات', 'ميكانيكا', 'مدني', 'البرنامج العام').
   - If a specific program is requested, extract ONLY exams for that program.
   - If no specific department is mentioned, extract the core/general program exams (البرنامج العام والمقررات المشتركة) or the most prominent exams (up to 15 items max).
2. Date & Time Format:
   - "date": MUST be ISO format YYYY-MM-DD (e.g., 2026-08-30). Infer the year from the image header or current date.
   - "time_start": 24-hour format HH:MM (e.g. 09:00, 11:30, 14:00, 16:30). Convert "9:00 ص" to "09:00", "2:00 م" to "14:00".
   - "time_end": 24-hour format HH:MM if listed, otherwise null.
3. Return a valid JSON array of objects with:
   - "title": string (e.g. "[البرنامج العام] رياضيات 1" or "CS201 Midterm")
   - "action": "create" | "cancel"
   - "date": "YYYY-MM-DD"
   - "time_start": "HH:MM"
   - "time_end": "HH:MM" or null
   - "location": string or null
   - "event_type": "exam" | "lecture" | "lab" | "section" | "deadline" | "other"
   - "description": string (e.g. department, notes, semester)

Today is {today}.
The text message/caption is:
---
{message}
---

Return ONLY the raw JSON array (no markdown code blocks, no explanations)."""


class GroupListener:
    """Parses announcement group messages into structured schedule events."""

    def __init__(self, announcement_group_jid: str | None = None):
        self.announcement_group_jid = announcement_group_jid
        # Callback for when events are parsed
        self._on_events_parsed = None

    def on_events_parsed(self, handler):
        """Register a callback for when schedule events are extracted."""
        self._on_events_parsed = handler
        return handler

    async def handle_group_message(self, message: dict) -> list[dict]:
        """
        Process an incoming group message and extract schedule events.

        Args:
            message: Parsed message dict from WhatsAppBot.handle_webhook()

        Returns:
            List of structured event dicts (may be empty if no events found)
        """
        group_jid = message.get("jid", "")
        text = message.get("text", "")
        sender = message.get("sender_name", "Unknown")

        # Only process messages from the designated announcement group
        if self.announcement_group_jid and group_jid != self.announcement_group_jid:
            logger.debug("Ignoring message from non-announcement group: %s", group_jid)
            return []

        media = message.get("media")
        has_image = bool(media and media.get("type") == "image" and media.get("base64"))

        # Skip if neither meaningful text nor image is present
        if not text and not has_image:
            return []
        if not has_image and len(text.strip()) < 4:
            return []

        logger.info(
            "Parsing announcement from %s: text='%s', has_image=%s",
            sender,
            text[:80] if text else "(none)",
            has_image,
        )

        try:
            events = await self._parse_schedule(text, media if has_image else None)
        except Exception as e:
            logger.error("Failed to parse schedule from message: %s", e)
            return []

        if events:
            logger.info("Extracted %d event(s) from announcement", len(events))
            # Fire callback if registered
            if self._on_events_parsed:
                await self._on_events_parsed(events, message)

        return events

    async def _parse_schedule(self, message_text: str, media: dict | None = None) -> list[dict]:
        """Use multi-LLM router to parse unstructured text or image into structured events."""
        import base64 as b64module
        from src.agents.llm_router import llm_router

        today = datetime.now().strftime("%Y-%m-%d (%A)")
        display_text = message_text or "(Image of schedule/announcement attached)"
        prompt = SCHEDULE_PARSE_PROMPT.format(today=today, message=display_text)

        pil_img = None
        if media and media.get("base64"):
            raw_b64 = media["base64"]
            clean_b64 = raw_b64.split(",")[1] if "," in raw_b64 else raw_b64
            try:
                import io
                import PIL.Image
                img_bytes = b64module.b64decode(clean_b64)
                pil_img = PIL.Image.open(io.BytesIO(img_bytes))
                logger.info("Attached PIL image to LLM schedule parser (%d bytes)", len(img_bytes))
            except Exception as b64_err:
                logger.error("Failed to decode image: %s", b64_err)

        raw_text = await llm_router.generate(prompt, image=pil_img)

        # Clean up: extract JSON block from markdown fences
        if "```json" in raw_text:
            raw_text = raw_text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```", 1)[1].split("```", 1)[0].strip()
        else:
            raw_text = raw_text.strip()

        try:
            events = json.loads(raw_text)
        except json.JSONDecodeError:
            logger.warning("Gemini returned invalid JSON: %s", raw_text[:200])
            return []

        if isinstance(events, dict):
            events = events.get("events") or events.get("schedule") or [events]

        if not isinstance(events, list):
            logger.warning("Gemini returned non-list: %s", type(events))
            return []

        # Validate and clean each event
        clean_events = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if not event.get("title"):
                continue
            clean_events.append({
                "title": event.get("title", ""),
                "action": event.get("action", "create"),
                "date": event.get("date"),
                "time_start": event.get("time_start"),
                "time_end": event.get("time_end"),
                "location": event.get("location"),
                "event_type": event.get("event_type", "other"),
                "description": event.get("description", ""),
                "source_message": message_text[:500],
            })

        return clean_events
