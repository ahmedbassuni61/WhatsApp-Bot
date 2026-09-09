"""
LangChain tools for the College Assistant WhatsApp bot.

Each tool has a typed Pydantic args_schema so the LLM knows exactly
what parameters to provide when calling it.  Dependencies are wired
at startup via ``init_tools()``.
"""

import base64 as b64mod
import io
import json
import logging
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.tools.time_tool import time_tool

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Module-level dependencies (set once at startup via init_tools)
# ------------------------------------------------------------------ #
_calendar_sync = None
_llm_router = None
_current_image = None  # PIL Image for the current message (set per request)


def init_tools(*, calendar_sync, llm_router):
    """Wire up runtime dependencies. Called once during app startup."""
    global _calendar_sync, _llm_router
    _calendar_sync = calendar_sync
    _llm_router = llm_router
    logger.info("Agent tools initialized")


def set_current_image(image):
    """Store the current message's PIL image for tool access."""
    global _current_image
    _current_image = image


def clear_current_image():
    """Clear the stored image after processing."""
    global _current_image
    _current_image = None


# ------------------------------------------------------------------ #
# Pydantic schemas — the model reads these to decide arguments
# ------------------------------------------------------------------ #


class ViewScheduleInput(BaseModel):
    """Input for viewing upcoming calendar events."""

    days: int = Field(
        default=30,
        description="Number of days ahead to show (default 30)",
    )


class AddEventInput(BaseModel):
    """Input for adding a calendar event."""

    title: str = Field(description="Event title, e.g. 'Math Exam', 'CS201 Lab', 'Project Deadline'")
    date: str = Field(description="Date in YYYY-MM-DD format")
    time_start: Optional[str] = Field(default=None, description="Start time in HH:MM 24-hour format, e.g. '14:00'")
    time_end: Optional[str] = Field(default=None, description="End time in HH:MM 24-hour format (defaults to 1h after start)")
    location: Optional[str] = Field(default=None, description="Location or room number")
    event_type: str = Field(
        default="other",
        description="One of: exam, lecture, lab, section, deadline, other",
    )
    description: Optional[str] = Field(default="", description="Additional notes")


class DeleteEventInput(BaseModel):
    """Input for deleting calendar events."""

    query: str = Field(description="Keyword to match events, e.g. 'math exam', 'CS201', or 'all' to remove everything")
    date: Optional[str] = Field(default=None, description="Optional date filter in YYYY-MM-DD format")


class AnswerQuestionInput(BaseModel):
    """Input for answering an academic question."""

    question: str = Field(description="The student's academic/study question to answer")


class ParseTimetableInput(BaseModel):
    """Input for parsing a timetable image."""

    caption: str = Field(default="", description="Caption or text that accompanied the timetable image")


# ------------------------------------------------------------------ #
# Schedule-parsing prompt (reused from group_listener)
# ------------------------------------------------------------------ #

_SCHEDULE_PARSE_PROMPT = """You are an intelligent college schedule parsing assistant.
Analyze the following WhatsApp message (and attached schedule/timetable image if present).
Extract the relevant academic schedule items (exams, lectures, labs, deadlines).

GUIDELINES FOR RELATIVE DATES & TIMES:
1. Refer to the GROUND TRUTH local time and date below.
2. For relative times ("next hour", "in 2 hours", "كمان ساعة"), compute the exact target time.
3. "tomorrow" / "بكرة" → use tomorrow's date.
4. "time_start" MUST be 24-hour HH:MM format and MUST NOT be null when a time is requested.
5. "time_end" defaults to 1 hour after time_start if not specified.

GUIDELINES FOR TIMETABLE IMAGES:
1. If the image is a multi-column timetable, check if the user specified a department/year.
2. Dates MUST be ISO YYYY-MM-DD.  Times MUST be 24-hour HH:MM.
3. Return a JSON array of objects with keys:
   title, action ("create"|"cancel"), date, time_start, time_end, location, event_type, description

{time_context}
The text message/caption is:
---
{message}
---

Return ONLY the raw JSON array (no markdown, no explanations)."""


# ------------------------------------------------------------------ #
# Tools
# ------------------------------------------------------------------ #


@tool(args_schema=ViewScheduleInput)
async def view_schedule(days: int = 30) -> str:
    """View upcoming calendar events for the next N days.
    Use when the student asks about their schedule, upcoming exams, or timetable."""
    logger.info("🔧 view_schedule(days=%d)", days)
    try:
        events = await _calendar_sync.get_upcoming_events(days=days)
        result = _calendar_sync.format_schedule(events)
        logger.info("  → returned %d events", len(events))
        return result
    except Exception as e:
        logger.error("  ✗ view_schedule failed: %s", e)
        return f"⚠️ Calendar error: {e}"


@tool(args_schema=AddEventInput)
async def add_calendar_event(
    title: str,
    date: str,
    time_start: Optional[str] = None,
    time_end: Optional[str] = None,
    location: Optional[str] = None,
    event_type: str = "other",
    description: str = "",
) -> str:
    """Add an academic event to Google Calendar.
    Use when the student wants to add, schedule, or set a reminder for an exam, lecture, lab, deadline, or any event."""
    logger.info("🔧 add_calendar_event(title='%s', date='%s', time='%s', type='%s')", title, date, time_start, event_type)
    event_data = {
        "title": title,
        "action": "create",
        "date": date,
        "time_start": time_start,
        "time_end": time_end,
        "location": location,
        "event_type": event_type,
        "description": description,
        "source_message": f"Added via WhatsApp: {title}",
    }
    # Patch with deterministic relative-time math
    event_data = time_tool.patch_event_time(event_data, f"{title} {date}")

    try:
        created = await _calendar_sync.create_event(event_data)
        if created:
            time_text = f" on {date}" if date else ""
            if time_start:
                time_text += f" at {time_start}"
            loc_text = f"\n📍 Location: {location}" if location else ""
            logger.info("  → event created: %s", created.get("summary"))
            return (
                f"📅 *Added to Google Calendar!*\n"
                f"📌 *{created.get('summary')}*\n"
                f"🕐{time_text}{loc_text}\n"
                f"🔔 Reminders set: 1 hr & 15 mins before."
            )
        logger.info("  → duplicate skipped: %s", title)
        return f"ℹ️ *{title}* is already on your calendar for this date."
    except Exception as e:
        logger.error("  ✗ add_calendar_event failed: %s", e)
        return f"⚠️ Failed to add event: {e}"


@tool(args_schema=DeleteEventInput)
async def delete_calendar_event(query: str, date: Optional[str] = None) -> str:
    """Delete or cancel calendar events matching a keyword.
    Use when the student wants to remove, delete, or cancel events.  Use query='all' to delete everything."""
    logger.info("🔧 delete_calendar_event(query='%s', date='%s')", query, date)
    try:
        deleted = await _calendar_sync.delete_events(query=query, date_str=date)
        if deleted:
            logger.info("  → deleted %d events: %s", len(deleted), deleted)
            return "🗑️ *Cancelled & Removed from Google Calendar:*\n* " + "\n* ".join(deleted)
        logger.info("  → no events matched '%s'", query)
        return f"🔍 No upcoming calendar events matched: *{query}*"
    except Exception as e:
        logger.error("  ✗ delete_calendar_event failed: %s", e)
        return f"⚠️ Failed to delete event: {e}"


@tool(args_schema=AnswerQuestionInput)
async def answer_question(question: str) -> str:
    """Answer an academic or college study question.
    Use for any educational question, homework help, concept explanation, or general knowledge.
    If there is an attached image, it will be included automatically."""
    logger.info("🔧 answer_question(question='%s')", question[:80])
    try:
        prompt = (
            "You are a helpful college study assistant. "
            "Answer the following student question concisely:\n\n" + question
        )
        result = await _llm_router.generate(prompt, image=_current_image)
        logger.info("  → response length: %d chars", len(result))
        return result
    except Exception as e:
        logger.error("  ✗ answer_question failed: %s", e)
        return f"⚠️ Could not generate answer: {e}"


@tool(args_schema=ParseTimetableInput)
async def parse_timetable_image(caption: str = "") -> str:
    """Parse a timetable or schedule image and add the extracted events to Google Calendar.
    Use when the student sends an image of a timetable, exam schedule, or class schedule."""
    logger.info("🔧 parse_timetable_image(caption='%s')", caption[:80] if caption else "(none)")
    if not _current_image:
        return "⚠️ No image attached to parse."

    try:
        # Build the specialised parsing prompt
        time_context = time_tool.get_time_context_prompt()
        display_text = caption or "(Image of schedule/announcement attached)"
        prompt = _SCHEDULE_PARSE_PROMPT.format(time_context=time_context, message=display_text)

        # Send to LLM with the image
        raw_text = await _llm_router.generate(prompt, image=_current_image)
        logger.debug("  LLM raw response: %s", raw_text[:300])

        # Strip markdown fences
        if "```json" in raw_text:
            raw_text = raw_text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in raw_text:
            raw_text = raw_text.split("```", 1)[1].split("```", 1)[0].strip()
        else:
            raw_text = raw_text.strip()

        events = json.loads(raw_text) if raw_text else []
        if isinstance(events, dict):
            events = events.get("events") or events.get("schedule") or [events]
        if not isinstance(events, list):
            events = []

        # Validate, clean, and create calendar events
        confirmations: list[str] = []
        for ev in events:
            if not isinstance(ev, dict) or not ev.get("title"):
                continue

            cleaned = {
                "title": ev.get("title", ""),
                "action": ev.get("action", "create"),
                "date": ev.get("date"),
                "time_start": ev.get("time_start"),
                "time_end": ev.get("time_end"),
                "location": ev.get("location"),
                "event_type": ev.get("event_type", "other"),
                "description": ev.get("description", ""),
                "source_message": (caption or "timetable image")[:500],
            }
            if caption:
                cleaned = time_tool.patch_event_time(cleaned, caption)

            if cleaned.get("action") == "cancel":
                deleted = await _calendar_sync.delete_events(
                    query=cleaned["title"], date_str=cleaned.get("date")
                )
                if deleted:
                    confirmations.append(f"🗑️ *Cancelled:* {', '.join(deleted)}")
            else:
                created = await _calendar_sync.create_event(cleaned)
                if created:
                    t = f" on {cleaned['date']}" if cleaned.get("date") else ""
                    if cleaned.get("time_start"):
                        t += f" at {cleaned['time_start']}"
                    confirmations.append(f"• *{created.get('summary')}*{t}")
                else:
                    confirmations.append(f"• ℹ️ *{cleaned['title']}* (already on calendar)")

        if confirmations:
            logger.info("  → parsed %d events from timetable image", len(confirmations))
            return (
                "📅 *Timetable Processed & Synced to Google Calendar!*\n\n"
                + "\n".join(confirmations)
                + "\n\n🔔 Automatic reminders have been scheduled."
            )

        logger.info("  → no schedule events found in image")
        return "📋 No schedule events found in this image."
    except json.JSONDecodeError:
        logger.warning("  LLM returned invalid JSON for timetable")
        return "⚠️ Could not parse the timetable — the image may be unclear."
    except Exception as e:
        logger.error("  ✗ parse_timetable_image failed: %s", e)
        return f"⚠️ Could not process the timetable image: {e}"


# ------------------------------------------------------------------ #
# Export list for the agent
# ------------------------------------------------------------------ #

ALL_TOOLS = [
    view_schedule,
    add_calendar_event,
    delete_calendar_event,
    answer_question,
    parse_timetable_image,
]
