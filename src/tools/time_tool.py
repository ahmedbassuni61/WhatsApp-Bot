"""
Time Tool — Timezone-aware date & time extraction and relative time resolution.

Provides ground truth for current date, time, weekday, and timezone,
and resolves relative time expressions (e.g. "next hour", "in 2 hours",
"tomorrow at 3pm", "كمان ساعة", "بعد ساعتين", "بكرة").
"""

import os
import re
import logging
from datetime import datetime, timedelta, timezone
try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = os.getenv("TIMEZONE", "Africa/Cairo")


class TimeTool:
    """Time extraction and relative datetime resolution tool."""

    def __init__(self, default_timezone: str = DEFAULT_TIMEZONE):
        self.default_timezone = default_timezone

    def _get_tz(self, tz_name: str | None = None) -> timezone:
        """Resolve a timezone object from name or fallback."""
        name = tz_name or os.getenv("TIMEZONE", self.default_timezone) or "Africa/Cairo"
        try:
            return zoneinfo.ZoneInfo(name)
        except Exception as e:
            logger.warning("Could not load zoneinfo '%s' (%s), using UTC+3 fallback", name, e)
            return timezone(timedelta(hours=3))

    def get_current_datetime(self, tz_name: str | None = None) -> datetime:
        """Return a timezone-aware current datetime object."""
        tz = self._get_tz(tz_name)
        return datetime.now(tz)

    def get_current_time(self, tz_name: str | None = None) -> dict:
        """
        Return structured current time details.
        
        Example:
        {
            "iso": "2026-09-08T02:40:00+03:00",
            "date": "2026-09-08",
            "time_24h": "02:40",
            "time_12h": "02:40 AM",
            "weekday": "Tuesday",
            "day": 8,
            "month": 9,
            "year": 2026,
            "tomorrow_date": "2026-09-09",
            "timezone": "Africa/Cairo",
            "utc_offset": "+03:00",
            "readable": "Tuesday, 08 Sep 2026, 02:40 (Africa/Cairo, UTC+03:00)"
        }
        """
        now = self.get_current_datetime(tz_name)
        tz_str = tz_name or os.getenv("TIMEZONE", self.default_timezone)
        offset_str = now.strftime("%z")
        if offset_str and len(offset_str) == 5:
            offset_formatted = f"{offset_str[:3]}:{offset_str[3:]}"
        else:
            offset_formatted = "+03:00"

        tomorrow = now + timedelta(days=1)

        return {
            "iso": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time_24h": now.strftime("%H:%M"),
            "time_12h": now.strftime("%I:%M %p"),
            "weekday": now.strftime("%A"),
            "day": now.day,
            "month": now.month,
            "year": now.year,
            "tomorrow_date": tomorrow.strftime("%Y-%m-%d"),
            "timezone": tz_str,
            "utc_offset": offset_formatted,
            "readable": f"{now.strftime('%A, %d %b %Y, %H:%M')} ({tz_str}, UTC{offset_formatted})",
        }

    def get_time_context_prompt(self, tz_name: str | None = None) -> str:
        """
        Return a clean, structured context block for injection into LLM prompts.
        """
        info = self.get_current_time(tz_name)
        return (
            f"CURRENT REFERENCE LOCAL TIME & DATE (GROUND TRUTH):\n"
            f"- Timezone: {info['timezone']} (UTC{info['utc_offset']})\n"
            f"- Today's Date: {info['date']} ({info['weekday']})\n"
            f"- Current Time: {info['time_24h']} (24-hour) / {info['time_12h']}\n"
            f"- Tomorrow's Date: {info['tomorrow_date']}\n"
        )

    def resolve_relative_time_phrase(self, text: str, tz_name: str | None = None) -> dict | None:
        """
        Deterministic math resolver for relative time expressions.
        
        Handles:
        - "next hour", "in an hour", "كمان ساعة", "بعد ساعة", "ساعة من دلوقتي"
        - "in 2 hours", "in X hours", "كمان ساعتين", "بعد ساعتين", "كمان X ساعة", "بعد X ساعات"
        - "in 30 mins", "كمان نص ساعة", "بعد نص ساعة", "كمان ربع ساعة", "بعد ربع ساعة"
        - "tomorrow", "بكرة", "غدا"
        
        Returns dict with keys: 'date', 'time_start', 'time_end', 'matched_phrase'
        or None if no recognized relative expression was found.
        """
        if not text:
            return None

        clean = text.lower().strip()
        now = self.get_current_datetime(tz_name)

        target_dt = None
        matched_phrase = None
        duration_hours = 1

        # 1. Next hour / in an hour / كمان ساعة / بعد ساعة
        if re.search(r"\b(next\s+hour|in\s+(?:an?|1)\s+hour|ساعة\s+من\s+دلوقتي)\b", clean) or \
           re.search(r"(?:كمان|بعد)\s+ساعة", clean):
            target_dt = now + timedelta(hours=1)
            matched_phrase = "next hour"

        # 2. In 2 hours / كمان ساعتين / بعد ساعتين
        elif re.search(r"\b(in\s+2\s+hours?|after\s+2\s+hours?)\b", clean) or \
             re.search(r"(?:كمان|بعد)\s+ساعتين", clean):
            target_dt = now + timedelta(hours=2)
            matched_phrase = "in 2 hours"

        # 3. In X hours / كمان X ساعة / بعد X ساعات
        elif (m := re.search(r"\bin\s+(\d+)\s+hours?\b", clean)) or \
             (m := re.search(r"(?:كمان|بعد)\s+(\d+)\s*(?:ساعات|ساعة)", clean)):
            hours = int(m.group(1))
            target_dt = now + timedelta(hours=hours)
            matched_phrase = f"in {hours} hours"

        # 4. In 30 mins / half an hour / نص ساعة
        elif re.search(r"\b(in\s+30\s*(?:mins?|minutes?)|half\s+an?\s+hour)\b", clean) or \
             re.search(r"(?:كمان|بعد)\s+نص\s+ساعة", clean):
            target_dt = now + timedelta(minutes=30)
            matched_phrase = "in 30 minutes"

        # 5. In 15 mins / quarter hour / ربع ساعة
        elif re.search(r"\b(in\s+15\s*(?:mins?|minutes?)|quarter\s+(?:of\s+)?an?\s+hour)\b", clean) or \
             re.search(r"(?:كمان|بعد)\s+ربع\s+ساعة", clean):
            target_dt = now + timedelta(minutes=15)
            matched_phrase = "in 15 minutes"

        # 6. In X minutes / كمان X دقيقة / بعد X دقائق
        elif (m := re.search(r"\bin\s+(\d+)\s*(?:mins?|minutes?)\b", clean)) or \
             (m := re.search(r"(?:كمان|بعد)\s+(\d+)\s*(?:دقائق|دقيقة)", clean)):
            mins = int(m.group(1))
            target_dt = now + timedelta(minutes=mins)
            matched_phrase = f"in {mins} minutes"

        # 7. Tomorrow (without specific hour) / بكرة / غداً
        elif re.search(r"\b(tomorrow)\b", clean) or re.search(r"(?:بكرة|غدا|غداً)", clean):
            # Check if specific time mentioned tomorrow e.g. "tomorrow at 3pm" or "بكرة الساعة 5"
            time_match = re.search(r"(?:at|الساعة)\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|ص|م)?", clean)
            if time_match:
                h = int(time_match.group(1))
                minute = int(time_match.group(2)) if time_match.group(2) else 0
                period = time_match.group(3)
                if period in ("pm", "م") and h < 12:
                    h += 12
                elif period in ("am", "ص") and h == 12:
                    h = 0
                tomorrow = now + timedelta(days=1)
                target_dt = tomorrow.replace(hour=h, minute=minute, second=0, microsecond=0)
                matched_phrase = f"tomorrow at {h:02d}:{minute:02d}"
            else:
                tomorrow = now + timedelta(days=1)
                return {
                    "date": tomorrow.strftime("%Y-%m-%d"),
                    "time_start": None,
                    "time_end": None,
                    "matched_phrase": "tomorrow",
                }

        if target_dt:
            end_dt = target_dt + timedelta(hours=duration_hours)
            return {
                "date": target_dt.strftime("%Y-%m-%d"),
                "time_start": target_dt.strftime("%H:%M"),
                "time_end": end_dt.strftime("%H:%M"),
                "matched_phrase": matched_phrase,
            }

        return None

    def patch_event_time(self, event: dict, original_text: str) -> dict:
        """
        Check if an extracted event lacks a time or date, and patch it
        using deterministic relative time resolution if applicable.
        """
        rel = self.resolve_relative_time_phrase(original_text)
        if not rel:
            return event

        # If LLM didn't set time_start, or set an all-day event when a relative time was requested
        if rel.get("time_start") and (not event.get("time_start") or event.get("time_start") == "null"):
            event["time_start"] = rel["time_start"]
            event["date"] = rel["date"]
            if not event.get("time_end"):
                event["time_end"] = rel.get("time_end")
            logger.info(
                "Patched event '%s' with deterministic relative time: %s at %s",
                event.get("title"),
                event["date"],
                event["time_start"],
            )
        elif rel.get("date") and not event.get("date"):
            event["date"] = rel["date"]

        return event


# Global singleton instance
time_tool = TimeTool()
