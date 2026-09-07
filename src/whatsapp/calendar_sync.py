"""
Google Calendar Sync — creates and manages calendar events from parsed announcements.

Uses the Google Calendar API to:
- Create events from parsed WhatsApp announcement messages
- Detect duplicates to avoid creating the same event twice
- Query upcoming events for the /schedule bot command

Setup:
1. Go to https://console.cloud.google.com/
2. Enable the Google Calendar API
3. Create OAuth2 credentials (Desktop app) → download as credentials.json
4. On first run, a browser window opens for authorization → saves token.json
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

load_dotenv()

logger = logging.getLogger(__name__)

# Google Calendar API scopes
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Color IDs for different event types (Google Calendar color palette)
EVENT_COLORS = {
    "lab": "9",        # Blueberry
    "lecture": "1",    # Lavender
    "exam": "11",      # Tomato
    "section": "2",    # Sage
    "office_hours": "5",  # Banana
    "event": "6",      # Tangerine
    "deadline": "4",   # Flamingo
    "other": "8",      # Graphite
}


class CalendarSync:
    """Manages Google Calendar events from parsed schedule announcements."""

    def __init__(
        self,
        credentials_path: str | None = None,
        token_path: str | None = None,
        calendar_id: str | None = None,
    ):
        self.credentials_path = credentials_path or os.getenv(
            "GOOGLE_CREDENTIALS_PATH", "./credentials.json"
        )
        self.token_path = token_path or "./token.json"
        self.calendar_id = calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "primary")
        self._service = None

    def _get_service(self):
        """Authenticate and return the Google Calendar API service."""
        if self._service:
            return self._service

        creds = None
        token_file = Path(self.token_path)

        # Load existing token
        if token_file.is_file():
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

        # Refresh or check credentials
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_file.write_text(creds.to_json())
        elif not creds or not creds.valid:
            if not token_file.is_file():
                raise RuntimeError(
                    "Google Calendar not authorized yet. Please run 'python setup_calendar.py' in your terminal to log in."
                )
            raise RuntimeError("Invalid Google Calendar credentials. Please re-run 'python setup_calendar.py'.")

        self._service = build("calendar", "v3", credentials=creds)
        return self._service

    async def create_event(self, event_data: dict) -> dict | None:
        """
        Create a Google Calendar event from a parsed schedule event.

        Args:
            event_data: Dict with keys: title, date, time_start, time_end,
                       location, event_type, description, source_message

        Returns:
            Created event dict from Google API, or None if duplicate detected
        """
        service = self._get_service()

        title = event_data.get("title", "College Event")
        date_str = event_data.get("date")
        time_start = event_data.get("time_start")
        time_end = event_data.get("time_end")
        location = event_data.get("location", "")
        event_type = event_data.get("event_type", "other")
        description = event_data.get("description", "")
        source = event_data.get("source_message", "")

        # Build the event body
        event_body = {
            "summary": f"[{event_type.upper()}] {title}",
            "description": f"{description}\n\n---\nSource: WhatsApp announcement\n{source}",
            "colorId": EVENT_COLORS.get(event_type, "8"),
        }

        if location:
            event_body["location"] = location

        # Set reminders
        event_body["reminders"] = {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 60},   # 1 hour before
                {"method": "popup", "minutes": 15},    # 15 min before
            ],
        }

        # Handle date/time
        if date_str and time_start:
            # Full date + time event
            start_dt = datetime.fromisoformat(f"{date_str}T{time_start}:00")
            if time_end:
                end_dt = datetime.fromisoformat(f"{date_str}T{time_end}:00")
            else:
                end_dt = start_dt + timedelta(hours=1)  # Default 1 hour duration

            event_body["start"] = {
                "dateTime": start_dt.isoformat(),
                "timeZone": "Africa/Cairo",  # Adjust to your timezone
            }
            event_body["end"] = {
                "dateTime": end_dt.isoformat(),
                "timeZone": "Africa/Cairo",
            }
        elif date_str:
            # All-day event
            event_body["start"] = {"date": date_str}
            # End date must be the next day for all-day events
            end_date = datetime.fromisoformat(date_str) + timedelta(days=1)
            event_body["end"] = {"date": end_date.strftime("%Y-%m-%d")}
        else:
            logger.warning("Event '%s' has no date, skipping", title)
            return None

        # Check for duplicates before creating
        if await self._is_duplicate(title, date_str, time_start):
            logger.info("Duplicate event detected, skipping: %s on %s", title, date_str)
            return None

        # Create the event
        try:
            created = service.events().insert(
                calendarId=self.calendar_id,
                body=event_body,
            ).execute()

            logger.info(
                "Calendar event created: '%s' on %s — %s",
                title,
                date_str,
                created.get("htmlLink", ""),
            )
            return created

        except Exception as e:
            logger.error("Failed to create calendar event: %s", e)
            return None

    async def create_events_batch(self, events: list[dict]) -> list[dict]:
        """Create multiple calendar events from a list of parsed events."""
        created = []
        for event_data in events:
            result = await self.create_event(event_data)
            if result:
                created.append(result)
        return created

    async def get_upcoming_events(self, days: int = 7, max_results: int = 20) -> list[dict]:
        """
        Get upcoming calendar events for the next N days.

        Args:
            days: Number of days to look ahead (default: 7)
            max_results: Maximum events to return

        Returns:
            List of event dicts with summary, start, end, location
        """
        service = self._get_service()

        now = datetime.utcnow()
        time_min = (now - timedelta(days=1)).isoformat() + "Z"
        time_max = (now + timedelta(days=days)).isoformat() + "Z"

        try:
            result = service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            events = result.get("items", [])

            return [
                {
                    "title": e.get("summary", "No Title"),
                    "start": e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "")),
                    "end": e.get("end", {}).get("dateTime", e.get("end", {}).get("date", "")),
                    "location": e.get("location", ""),
                    "description": e.get("description", ""),
                    "link": e.get("htmlLink", ""),
                }
                for e in events
            ]

        except Exception as e:
            logger.error("Failed to fetch upcoming events: %s", e)
            return []

    def format_schedule(self, events: list[dict]) -> str:
        """Format upcoming events into a readable WhatsApp message."""
        if not events:
            return "📅 No upcoming events in the next week."

        lines = ["📅 *Upcoming Schedule:*\n"]

        for event in events:
            start = event.get("start", "")
            title = event.get("title", "No Title")
            location = event.get("location", "")

            # Parse and format the date
            try:
                if "T" in start:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    date_str = dt.strftime("%a %d/%m at %I:%M %p")
                else:
                    dt = datetime.fromisoformat(start)
                    date_str = dt.strftime("%a %d/%m (All Day)")
            except (ValueError, TypeError):
                date_str = start

            line = f"• *{title}*\n  🕐 {date_str}"
            if location:
                line += f"\n  📍 {location}"
            lines.append(line)

        return "\n\n".join(lines)

    async def _is_duplicate(self, title: str, date_str: str | None, time_start: str | None) -> bool:
        """Check if a similar event already exists on the same date."""
        if not date_str:
            return False

        service = self._get_service()

        time_min = f"{date_str}T00:00:00Z"
        time_max = f"{date_str}T23:59:59Z"

        try:
            result = service.events().list(
                calendarId=self.calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                q=title,  # Search by title
            ).execute()

            existing = result.get("items", [])
            # If any event on the same day has a similar title, it's a duplicate
            for e in existing:
                if title.lower() in e.get("summary", "").lower():
                    return True

            return False

        except Exception:
            return False  # On error, allow creation

    async def delete_events(self, query: str = "", date_str: str | None = None) -> list[str]:
        """
        Delete Google Calendar events matching query keyword and optional date.

        Supports:
        - Bulk deletion: "all", "all exams", "كل الامتحانات", etc.
        - Cross-lingual matching (e.g. "math" matches "رياضيات")
        - LLM-assisted matching for complex or dialectal phrasing
        """
        service = self._get_service()
        deleted = []

        now = datetime.utcnow()
        clean_q = (query or "").strip().lower()

        # Check for bulk delete intent
        bulk_keywords = {
            "all", "all exams", "all events", "all exam schedule", "all schedule",
            "everything", "كل", "الكل", "كل الامتحانات", "كل المواعيد", "كل الجدول",
            "جدول الامتحانات بالكامل", "جميع الامتحانات", "مسح الكل", "حذف الكل", "*"
        }
        is_bulk = clean_q in bulk_keywords or not clean_q

        params = {
            "calendarId": self.calendar_id,
            "maxResults": 100,
            "singleEvents": True,
        }

        # Look in full exam session window: 30 days past to 180 days ahead
        if date_str:
            params["timeMin"] = f"{date_str}T00:00:00Z"
            params["timeMax"] = f"{date_str}T23:59:59Z"
        else:
            params["timeMin"] = (now - timedelta(days=30)).isoformat() + "Z"
            params["timeMax"] = (now + timedelta(days=180)).isoformat() + "Z"

        try:
            result = service.events().list(**params).execute()
            all_items = result.get("items", [])

            if not all_items:
                logger.info("No events found in calendar window.")
                return []

            matched_items = []

            if is_bulk:
                logger.info("Bulk delete requested: removing all %d events", len(all_items))
                matched_items = all_items
            else:
                # Subject translation dictionary for cross-lingual search
                subject_map = {
                    "math": "رياضيات",
                    "maths": "رياضيات",
                    "calculus": "تفاضل",
                    "physics": "فيزياء",
                    "chemistry": "كيمياء",
                    "mechanics": "ميكانيكا",
                    "drawing": "رسم",
                    "english": "انجليز",
                    "civil": "مدني",
                    "electrical": "كهرب",
                    "sanitary": "صرف صحي",
                    "roads": "طرق",
                    "railways": "سكك",
                    "construction": "تشييد",
                    "materials": "مواد",
                    "structures": "منشآت",
                    "surveying": "مساحة",
                    "management": "إدارة",
                    "hydraulics": "هيدروليك",
                }

                # Build search tokens
                tokens = clean_q.split()
                expanded_tokens = set(tokens)
                for t in tokens:
                    if t in subject_map:
                        expanded_tokens.add(subject_map[t])

                # 1. Direct and token matching
                for item in all_items:
                    summary = item.get("summary", "").lower()
                    desc = item.get("description", "").lower()
                    full_text = f"{summary} {desc}"

                    if clean_q in full_text:
                        matched_items.append(item)
                    elif any(tok in full_text for tok in expanded_tokens):
                        matched_items.append(item)

                # 2. If no direct match found, use LLM router to find matching events
                if not matched_items and all_items:
                    try:
                        import json
                        from src.agents.llm_router import llm_router

                        events_summary = [
                            {"id": it.get("id"), "summary": it.get("summary"), "date": it.get("start", {}).get("dateTime") or it.get("start", {}).get("date")}
                            for it in all_items
                        ]
                        match_prompt = f"""Given the following list of calendar events:
{json.dumps(events_summary, ensure_ascii=False)}

The user wants to delete/cancel events matching: "{query}"

Return ONLY a JSON array of the matching event "id" strings. If none match, return []."""

                        llm_res = await llm_router.generate(match_prompt)
                        clean_json = llm_res.strip()
                        if "```json" in clean_json:
                            clean_json = clean_json.split("```json", 1)[1].split("```", 1)[0].strip()
                        elif "```" in clean_json:
                            clean_json = clean_json.split("```", 1)[1].split("```", 1)[0].strip()

                        matched_ids = json.loads(clean_json)
                        if isinstance(matched_ids, list):
                            matched_items = [it for it in all_items if it.get("id") in matched_ids]
                    except Exception as match_err:
                        logger.warning("LLM event matching fallback error: %s", match_err)

            # Perform deletions
            for item in matched_items:
                summary = item.get("summary", "Untitled")
                event_id = item.get("id")
                try:
                    service.events().delete(
                        calendarId=self.calendar_id,
                        eventId=event_id,
                    ).execute()
                    deleted.append(summary)
                    logger.info("Deleted calendar event: '%s' (ID: %s)", summary, event_id)
                except Exception as del_err:
                    logger.error("Failed to delete event %s: %s", event_id, del_err)

        except Exception as e:
            logger.error("Error finding events to delete for query '%s': %s", query, e)

        return deleted
