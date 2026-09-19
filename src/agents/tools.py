"""
LangChain tools for the College Assistant WhatsApp bot.

Each tool has a typed Pydantic args_schema so the LLM knows exactly
what parameters to provide when calling it.  Dependencies are wired
at startup via ``init_tools()``.
"""

import json
import logging
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from src.drive.drive_client import format_file_size
from src.tools.time_tool import time_tool

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Module-level dependencies (set once at startup via init_tools)
# ------------------------------------------------------------------ #
_calendar_sync = None
_llm_router = None
_drive_client = None
_current_image = None  # PIL Image for the current message (set per request)
_last_deleted_events: list[dict] = []


def get_last_deleted_events() -> list[dict]:
    """Return the most recently deleted calendar events."""
    return list(_last_deleted_events)


def init_tools(
    *,
    calendar_sync=None,
    llm_router=None,
    drive_client=None,
    **_kwargs,
):
    """Wire up runtime dependencies. Called once during app startup."""
    global _calendar_sync, _llm_router, _drive_client
    _calendar_sync = calendar_sync
    _llm_router = llm_router
    _drive_client = drive_client
    logger.info("Agent tools initialized (Calendar, LLM Router, Drive)")


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
    description: Optional[str] = Field(default="", description="The student's original message or announcement text verbatim, plus any additional notes.")


class DeleteEventInput(BaseModel):
    """Input for deleting calendar events."""

    query: str = Field(description="Keyword to match events, e.g. 'math exam', 'CS201', or 'all' to remove everything")
    date: Optional[str] = Field(default=None, description="Optional date filter in YYYY-MM-DD format")


class ParseTimetableInput(BaseModel):
    """Input for parsing a timetable image."""

    caption: str = Field(default="", description="Caption or text that accompanied the timetable image")


class ListDriveFolderInput(BaseModel):
    """Input for listing or entering a Google Drive folder."""

    folder_id: Optional[str] = Field(
        default=None,
        description="The Google Drive folder ID to enter and list. Leave empty or None to view the root directory.",
    )
    folder_name: Optional[str] = Field(
        default=None,
        description="Optional name of the folder to enter (e.g. 'Transmission Media', 'Lectures') if folder_id is not known.",
    )


class SearchDriveInput(BaseModel):
    """Input for searching files or folders across Google Drive."""

    query: str = Field(
        description="Search keyword, course name, lecture title, or exam (e.g. 'Transmission Media final', 'Lecture 1', 'Sheet 2')"
    )


# ------------------------------------------------------------------ #
# Schedule-parsing prompt for multimodal timetable analysis
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
    Use when the student wants to add, schedule, or set a reminder for an exam, lecture, lab, deadline, or any event.
    Always pass the student's original message verbatim in the description field."""
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
        time_text = f" ({time_start})" if time_start else ""
        return (
            f"⚠️ *تنبيه: لم تتم إضافة الموعد*\n"
            f"• الموعد *{title}*{time_text} في يوم {date} موجود بالفعل على تقويمك بنفس الوقت والمواصفات (Already Scheduled)."
        )
    except Exception as e:
        logger.error("  ✗ add_calendar_event failed: %s", e)
        return f"⚠️ Failed to add event: {e}"


@tool(args_schema=DeleteEventInput)
async def delete_calendar_event(query: str, date: Optional[str] = None) -> str:
    """Delete or cancel calendar events matching a keyword.
    Use when the student wants to remove, delete, or cancel events.  Use query='all' to delete everything."""
    logger.info("🔧 delete_calendar_event(query='%s', date='%s')", query, date)
    global _last_deleted_events
    try:
        deleted = await _calendar_sync.delete_events(query=query, date_str=date)
        _last_deleted_events = deleted or []
        if deleted:
            logger.info("  → deleted %d events: %s", len(deleted), [d.get("summary", d) for d in deleted])
            return _calendar_sync.format_deleted_schedule(deleted)
        logger.info("  → no events matched '%s'", query)
        return f"🔍 لم يتم العثور على مواعيد مطابقة لـ: *{query}* في Google Calendar."
    except Exception as e:
        _last_deleted_events = []
        logger.error("  ✗ delete_calendar_event failed: %s", e)
        return f"⚠️ Failed to delete event: {e}"


@tool(args_schema=ParseTimetableInput)
async def parse_timetable_image(caption: str = "") -> str:
    """Parse a timetable or schedule image and extract events for calendar sync.
    Use when the student sends an image of a timetable, exam schedule, or class schedule.
    Returns extracted events as JSON for review — events are committed after reflection."""
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

        # Validate and clean events — but do NOT commit to calendar
        # The graph's commit_node handles creation after the reflector passes
        patched_events: list[dict] = []
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

            patched_events.append(cleaned)

        if patched_events:
            logger.info("  → extracted %d events from timetable (queued for auto-sync)", len(patched_events))
            return json.dumps({
                "status": "success",
                "message": (
                    f"Successfully extracted {len(patched_events)} events from the timetable. "
                    "All events have been queued and are being automatically validated and synced to Google Calendar. "
                    "DO NOT call add_calendar_event for these events — they are already handled."
                ),
                "events": patched_events,
                "count": len(patched_events),
            })

        logger.info("  → no schedule events found in image")
        return "📋 No schedule events found in this image."
    except json.JSONDecodeError:
        logger.warning("  LLM returned invalid JSON for timetable")
        return "⚠️ Could not parse the timetable — the image may be unclear."
    except Exception as e:
        logger.error("  ✗ parse_timetable_image failed: %s", e)
        return f"⚠️ Could not process the timetable image: {e}"


@tool(args_schema=ListDriveFolderInput)
async def list_drive_folder(
    folder_id: Optional[str] = None, folder_name: Optional[str] = None
) -> str:
    """View the contents of a Google Drive directory (subfolders and files) or enter a folder needed.
    - If folder_id and folder_name are omitted: shows the root directory (subjects, courses, or semester folders).
    - To enter a folder: pass its folder_id (from a previous listing) or folder_name.
    Use this tool to explore the drive, check courses, count lectures/files in any subject, or browse study materials."""
    logger.info(
        "🔧 list_drive_folder(folder_id='%s', folder_name='%s')",
        folder_id,
        folder_name,
    )
    if folder_id and str(folder_id).strip().lower() in ("none", "null", "undefined", '""', "''"):
        folder_id = None
    if folder_name and str(folder_name).strip().lower() in ("none", "null", "undefined", '""', "''"):
        folder_name = None

    try:
        data = await _drive_client.explore_folder(
            folder_id=folder_id, folder_name=folder_name
        )
        folder_name_val = data["folder_name"]
        folder_link = data["folder_link"]
        cur_id = data["folder_id"]
        subfolders = data["subfolders"]
        files = data["files"]

        lines = [
            f"📁 *Current Folder:* **{folder_name_val}** (ID: `{cur_id}`)",
            f"🔗 [Open Folder in Google Drive]({folder_link})\n",
        ]

        if subfolders:
            lines.append(f"📂 *Subfolders ({len(subfolders)}):*")
            for idx, sf in enumerate(subfolders, 1):
                lines.append(
                    f"{idx}. 📁 **{sf['name']}** — ID: `{sf['id']}` (🔗 [Link]({sf['link']}))"
                )
            lines.append("")

        if files:
            lines.append(f"📄 *Files ({len(files)}):*")
            for idx, f in enumerate(files[:40], 1):
                size = f" ({f['size_str']})" if f.get("size_str") else ""
                lines.append(f"{idx}. 📄 [{f['name']}]({f['link']}){size}")
            if len(files) > 40:
                lines.append(f"... and {len(files) - 40} more files.")
            lines.append("")

        if not subfolders and not files:
            lines.append(
                "*(This folder is currently empty - 0 files and 0 subfolders)*\n"
            )

        lines.append(
            f"📊 *Summary:* {len(subfolders)} subfolders, {len(files)} files."
        )
        lines.append(
            "💡 *Tip:* Call `list_drive_folder(folder_id='<ID>')` or `list_drive_folder(folder_name='<Name>')` to enter any subfolder listed above."
        )
        return "\n".join(lines)
    except Exception as e:
        logger.error("  ✗ list_drive_folder failed: %s", e)
        return f"⚠️ Drive folder error: {e}"


@tool(args_schema=SearchDriveInput)
async def search_drive(query: str) -> str:
    """Search Google Drive directly by keywords, course names, or file titles (e.g. 'Transmission Media Final', 'Lecture 1', 'Sheet 2').
    Use when looking for a specific exam, lecture file, sheet, or topic across the entire Drive."""
    logger.info("🔧 search_drive(query='%s')", query)
    try:
        items = await _drive_client.search_drive_api(query, max_results=12)
        if not items:
            return f"🔍 No files or folders found matching: *{query}* on Google Drive."

        lines = [f"🔍 *Drive Search Results for:* _{query}_\n"]
        for idx, item in enumerate(items, 1):
            name = item.get("name", "Untitled")
            item_id = item.get("id", "")
            is_folder = item.get("mimeType") == "application/vnd.google-apps.folder"
            link = (
                item.get("webViewLink")
                or f"https://drive.google.com/file/d/{item_id}/view"
            )
            size_int = int(item.get("size", 0)) if item.get("size") else 0
            size_str = f" ({format_file_size(size_int)})" if size_int > 0 else ""

            if is_folder:
                lines.append(
                    f"{idx}. 📁 **{name}** — ID: `{item_id}` (🔗 [Open Folder]({link}))"
                )
            else:
                lines.append(f"{idx}. 📄 [{name}]({link}){size_str}")

        lines.append(
            "\n💡 *To open a folder found above, call `list_drive_folder(folder_id='<ID>')`.*"
        )
        return "\n".join(lines)
    except Exception as e:
        logger.error("  ✗ search_drive failed: %s", e)
        return f"⚠️ Drive search error: {e}"


# ------------------------------------------------------------------ #
# Export list for the agent
# ------------------------------------------------------------------ #

ALL_TOOLS = [
    view_schedule,
    add_calendar_event,
    delete_calendar_event,
    parse_timetable_image,
    list_drive_folder,
    search_drive,
]

