import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def _parse_time(time_str: str) -> datetime | None:
    """Parse HH:MM string into a datetime using a fixed date for comparison."""
    if not time_str:
        return None
    try:
        return datetime.strptime(f"2000-01-01 {time_str}", "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None

def _clean_title(title: str) -> str:
    """Strip tags, common prefixes, and normalize title for comparison."""
    import re
    t = re.sub(r"^\[.*?\]\s*", "", title).strip()
    t = re.sub(r"^(محاضرة|معمل|سكشن)\s*", "", t).strip().lower()
    return " ".join(t.split())


def _are_same_event(title_a: str, title_b: str) -> bool:
    """Check if two event titles refer to the same class/event."""
    clean_a = _clean_title(title_a)
    clean_b = _clean_title(title_b)
    if not clean_a or not clean_b:
        return False
    if clean_a == clean_b or clean_a in clean_b or clean_b in clean_a:
        return True
    # Check course code match (e.g. EEC 471, HUM x32, etc.)
    import re
    code_a = re.search(r"[a-zA-Z]{2,4}\s*[0-9xX]{2,4}", clean_a)
    code_b = re.search(r"[a-zA-Z]{2,4}\s*[0-9xX]{2,4}", clean_b)
    if code_a and code_b and code_a.group(0).replace(" ", "") == code_b.group(0).replace(" ", ""):
        return True
    return False


def _times_overlap(event_a: dict, event_b: dict) -> bool:
    """Check if two distinct events overlap in time."""
    if not event_a.get("date") or event_a.get("date") != event_b.get("date"):
        return False

    # If both events are all-day events on the same date, they overlap
    if not event_a.get("time_start") and not event_b.get("time_start"):
        return True

    # If only one is an all-day note/event, do not flag as a time clash with a scheduled class
    if not event_a.get("time_start") or not event_b.get("time_start"):
        return False

    start_a = _parse_time(event_a.get("time_start", ""))
    end_a = _parse_time(event_a.get("time_end", ""))
    start_b = _parse_time(event_b.get("time_start", ""))
    end_b = _parse_time(event_b.get("time_end", ""))

    if not all([start_a, end_a, start_b, end_b]):
        return False

    return start_a < end_b and start_b < end_a


def _normalize_existing_event(event: dict) -> dict:
    """Normalize an existing event from ISO format to new format."""
    normalized = {"title": event.get("title", "")}
    start = event.get("start")
    end = event.get("end")
    if start:
        try:
            dt = datetime.fromisoformat(start)
            normalized["date"] = dt.strftime("%Y-%m-%d")
            normalized["time_start"] = dt.strftime("%H:%M")
        except ValueError:
            pass
    if end:
        try:
            dt = datetime.fromisoformat(end)
            normalized["time_end"] = dt.strftime("%H:%M")
        except ValueError:
            pass
    return normalized


def detect_conflicts(new_events: list[dict], existing_events: list[dict]) -> list[dict]:
    """Detect scheduling conflicts between distinct events."""
    conflicts = []
    seen = set()

    normalized_existing = [_normalize_existing_event(e) for e in existing_events]
    all_events = new_events + normalized_existing

    for i, event_a in enumerate(new_events):
        title_a = event_a.get("title", "")
        for j, event_b in enumerate(all_events):
            # Same event object
            if event_a is event_b:
                continue

            title_b = event_b.get("title", "")

            # If both events represent the same class (e.g. existing Google Calendar copy), skip
            if _are_same_event(title_a, title_b):
                continue

            # Create a unique key for the pair to avoid duplicates
            pair = frozenset([title_a, title_b])

            if _times_overlap(event_a, event_b) and pair not in seen:
                seen.add(pair)

                time_a = f"{event_a.get('time_start', 'All-day')}"
                if event_a.get("time_end"):
                    time_a += f" → {event_a['time_end']}"

                time_b = f"{event_b.get('time_start', 'All-day')}"
                if event_b.get("time_end"):
                    time_b += f" → {event_b['time_end']}"

                conflicts.append({
                    "event_a": title_a or "Unknown",
                    "event_b": title_b or "Unknown",
                    "date": event_a.get("date", "Unknown Date"),
                    "time_a": time_a,
                    "time_b": time_b,
                })

    return conflicts

def format_conflict_warnings(conflicts: list[dict]) -> str:
    """Format conflicts into a user-facing message at the end of addition."""
    if not conflicts:
        return ""

    lines = ["\n⚠️ *تنبيه: تم رصد تعارض في المواعيد (Conflicts Detected):*"]
    for c in conflicts:
        time_a_formatted = c["time_a"]
        time_b_formatted = c["time_b"]
        lines.append(
            f"  • تعارض بين *{c['event_a']}* ({time_a_formatted}) و *{c['event_b']}* ({time_b_formatted}) في يوم {c['date']}."
        )
    lines.append("تمت إضافة كلا الموعدين إلى تقويمك — يمكنك تعديل أحدهما إذا رغبت.")
    return "\n".join(lines)
