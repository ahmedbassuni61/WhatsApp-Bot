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

import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
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
                    "Google APIs not authorized yet. Please run 'python setup_google.py' in your terminal to log in."
                )
            raise RuntimeError("Invalid Google credentials. Please re-run 'python setup_google.py'.")

        self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
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
        description = (event_data.get("description") or "").strip()
        source = (event_data.get("source_message") or "").strip()
        final_description = description or source or "Scheduled via WhatsApp"

        # Build the event body
        event_body = {
            "summary": f"[{event_type.upper()}] {title}",
            "description": final_description,
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

        # Check for duplicates before creating (requires matching date, time, and specifications)
        if await self._is_duplicate(title, date_str, time_start, event_type=event_type, location=location):
            logger.info("Duplicate event detected with matching specifications, skipping: %s on %s", title, date_str)
            return None

        # Create the event
        try:
            created = await asyncio.to_thread(
                service.events().insert(
                    calendarId=self.calendar_id,
                    body=event_body,
                ).execute
            )

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
            result = await asyncio.to_thread(
                service.events().list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    maxResults=max_results,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute
            )

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

    async def _is_duplicate(
        self,
        title: str,
        date_str: str | None,
        time_start: str | None,
        event_type: str = "other",
        location: str = "",
    ) -> bool:
        """Check if an event already exists with the same date, time, and specifications."""
        if not date_str:
            return False

        service = self._get_service()

        time_min = f"{date_str}T00:00:00Z"
        time_max = f"{date_str}T23:59:59Z"

        new_ev = {
            "title": title,
            "date": date_str,
            "time_start": time_start,
            "event_type": event_type,
            "location": location,
        }

        try:
            from src.agents.conflict_resolver import are_same_specifications

            result = await asyncio.to_thread(
                service.events().list(
                    calendarId=self.calendar_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    q=title,  # Search by title
                ).execute
            )

            existing = result.get("items", [])
            for e in existing:
                e_summary = e.get("summary", "")
                e_loc = e.get("location", "")
                e_ev = {"title": e_summary, "summary": e_summary, "location": e_loc}

                # If specifications differ (e.g. [LAB] vs [LECTURE], or different hall), it is NOT a duplicate
                if not are_same_specifications(new_ev, e_ev):
                    continue

                e_start = e.get("start", {})
                e_dt_str = e_start.get("dateTime")

                # If both have time_start, only duplicate if times match
                # If two tasks have different times on the same day, they are NOT duplicates
                if time_start and e_dt_str:
                    try:
                        dt = datetime.fromisoformat(e_dt_str)
                        if dt.strftime("%H:%M") == time_start:
                            return True
                        continue
                    except Exception:
                        pass
                elif not time_start and e_start.get("date"):
                    # Both are all-day events with the same specifications
                    return True

            return False

        except Exception:
            return False  # On error, allow creation

    async def delete_events(self, query: str = "", date_str: str | None = None) -> list[dict]:
        """Delete Google Calendar events matching query keyword, or 'all' for bulk deletion.

        Returns a list of dicts with full details of deleted events:
        [{'title': ..., 'summary': ..., 'date': ..., 'time_start': ..., 'time_end': ..., 'location': ..., 'event_type': ...}]
        """
        import re
        service = self._get_service()
        deleted = []

        now = datetime.utcnow()
        clean_q = (query or "").strip().lower()

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

        # If it's a specific query (not 'all'), use Google Calendar's native search
        if clean_q != "all" and clean_q != "":
            params["q"] = query

        try:
            result = await asyncio.to_thread(service.events().list(**params).execute)
            matched_items = result.get("items", [])

            if not matched_items:
                logger.info("No events found matching query: %s", query)
                return []

            # Perform deletions
            for item in matched_items:
                summary = item.get("summary", "Untitled")
                event_id = item.get("id")
                start = item.get("start", {})
                end = item.get("end", {})
                location = item.get("location", "")

                ev_date = None
                time_start = None
                time_end = None

                start_dt_str = start.get("dateTime")
                if start_dt_str:
                    try:
                        dt = datetime.fromisoformat(start_dt_str)
                        ev_date = dt.strftime("%Y-%m-%d")
                        time_start = dt.strftime("%H:%M")
                    except Exception:
                        pass
                elif start.get("date"):
                    ev_date = start["date"]

                end_dt_str = end.get("dateTime")
                if end_dt_str:
                    try:
                        dt = datetime.fromisoformat(end_dt_str)
                        time_end = dt.strftime("%H:%M")
                    except Exception:
                        pass

                # Extract event type from summary tag like [LECTURE], [EXAM], [LAB], etc.
                event_type = "event"
                clean_title = summary
                tag_match = re.match(r"^\[([A-Za-z_]+)\]\s*(.*)", summary)
                if tag_match:
                    event_type = tag_match.group(1).lower()
                    clean_title = tag_match.group(2).strip()
                else:
                    for kw, et in [("محاضرة", "lecture"), ("معمل", "lab"), ("سكشن", "section"), ("امتحان", "exam"), ("تسليم", "deadline")]:
                        if kw in summary:
                            event_type = et
                            break

                event_dict = {
                    "title": clean_title,
                    "summary": summary,
                    "date": ev_date,
                    "time_start": time_start,
                    "time_end": time_end,
                    "location": location,
                    "event_type": event_type,
                    "id": event_id,
                }

                try:
                    await asyncio.to_thread(
                        service.events().delete(
                            calendarId=self.calendar_id,
                            eventId=event_id,
                        ).execute
                    )
                    deleted.append(event_dict)
                    logger.info("Deleted calendar event: '%s' on %s (ID: %s)", summary, ev_date, event_id)
                except Exception as del_err:
                    logger.error("Failed to delete event %s: %s", event_id, del_err)

        except Exception as e:
            logger.error("Error finding events to delete for query '%s': %s", query, e)

        return deleted

    def format_deleted_schedule(self, deleted_events: list[dict]) -> str:
        """Format deleted events into a structured WhatsApp message similar to addition."""
        if not deleted_events:
            return "🔍 لم يتم العثور على مواعيد مطابقة للحذف في تقويم Google Calendar."

        emoji_map = {
            "exam": "📝",
            "lab": "🔬",
            "lecture": "📚",
            "section": "👥",
            "deadline": "⏰",
            "office_hours": "💬",
            "event": "📌",
            "other": "📌",
        }

        count = len(deleted_events)
        if count == 1:
            header = "🗑️ *تم حذف موعد واحد من Google Calendar بنجاح*:\n"
        else:
            header = f"🗑️ *تم حذف {count} مواعيد من Google Calendar بنجاح*:\n"

        by_date: dict[str, list[dict]] = {}
        for ev in deleted_events:
            d = ev.get("date") or "Unknown"
            by_date.setdefault(d, []).append(ev)

        day_names_map = {
            "Saturday": "السبت (Saturday)",
            "Sunday": "الأحد (Sunday)",
            "Monday": "الإثنين (Monday)",
            "Tuesday": "الثلاثاء (Tuesday)",
            "Wednesday": "الأربعاء (Wednesday)",
            "Thursday": "الخميس (Thursday)",
            "Friday": "الجمعة (Friday)",
        }

        lines = [header]
        for date_str in sorted(by_date.keys()):
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                eng_day = dt.strftime("%A")
                day_display = day_names_map.get(eng_day, eng_day)
                formatted_date = f"{day_display}, {dt.strftime('%b %d')}"
            except (ValueError, TypeError):
                formatted_date = date_str

            lines.append(f"📅 *{formatted_date}*")
            day_events = sorted(by_date[date_str], key=lambda x: x.get("time_start") or "00:00")
            for ev in day_events:
                etype = ev.get("event_type", "event")
                emoji = emoji_map.get(etype, "📌")
                tag = f"[{etype.upper()}]"
                title = ev.get("title") or ev.get("summary") or "Untitled"

                ts = ev.get("time_start")
                te = ev.get("time_end")
                if ts and te:
                    time_str = f"{ts} → {te}"
                elif ts:
                    time_str = f"{ts}"
                else:
                    time_str = "All Day"

                loc = ev.get("location")
                loc_str = f" | 📍 {loc}" if loc else ""

                lines.append(f"  • {emoji} {tag} *{title}* — 🕐 {time_str}{loc_str}")
            lines.append("")

        return "\n".join(lines).strip()
