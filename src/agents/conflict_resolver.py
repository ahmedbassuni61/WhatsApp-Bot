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


def _extract_event_type(event: dict) -> str:
    """Extract normalized event type from event dict or title."""
    import re
    etype = (event.get("event_type") or "").strip().lower()
    if etype and etype not in ("other", "event"):
        return etype

    title = event.get("title") or event.get("summary") or ""
    tag_match = re.match(r"^\[([A-Za-z_]+)\]", title.strip())
    if tag_match:
        return tag_match.group(1).lower()

    if any(k in title for k in ["محاضرة", "lecture"]):
        return "lecture"
    if any(k in title for k in ["معمل", "lab"]):
        return "lab"
    if any(k in title for k in ["سكشن", "section"]):
        return "section"
    if any(k in title for k in ["امتحان", "exam", "quiz"]):
        return "exam"
    if any(k in title for k in ["تسليم", "deadline"]):
        return "deadline"
    return "other"


def are_same_specifications(event_a: dict, event_b: dict) -> bool:
    """Check if two events have the same specifications (title, type, location).

    Returns True if:
    - They represent the same subject/course
    - Their event types are compatible (e.g. not lab vs lecture)
    - Their locations are compatible (e.g. not Hall 1 vs Lab 4)
    """
    title_a = event_a.get("title") or event_a.get("summary") or ""
    title_b = event_b.get("title") or event_b.get("summary") or ""

    # 1. Check title/subject match
    if not _are_same_event(title_a, title_b):
        return False

    # 2. Check event type
    type_a = _extract_event_type(event_a)
    type_b = _extract_event_type(event_b)
    academic_types = {"lecture", "lab", "exam", "section", "deadline"}
    if type_a in academic_types and type_b in academic_types and type_a != type_b:
        return False

    # 3. Check location
    loc_a = (event_a.get("location") or "").strip().lower()
    loc_b = (event_b.get("location") or "").strip().lower()
    if loc_a and loc_b and loc_a != loc_b:
        return False

    return True


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
    normalized = {
        "title": event.get("title", ""),
        "location": event.get("location", ""),
        "event_type": event.get("event_type", "other"),
    }
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
    """Detect scheduling conflicts between events.

    Categorizes conflicts into:
    - 'different_specs': Same date & overlapping time, but different specifications
      (different subject, type, or location). Action: ADD IT and warn user.
    - 'same_specs': Same date & overlapping time, with identical specifications
      (same subject, type, and location). Action: DON'T ADD IT and warn user.
    """
    conflicts = []
    seen_pairs = set()

    normalized_existing = [_normalize_existing_event(e) for e in existing_events]
    all_events = new_events + normalized_existing

    for i, event_a in enumerate(new_events):
        title_a = event_a.get("title") or event_a.get("summary") or ""
        for j, event_b in enumerate(all_events):
            # Same event object
            if event_a is event_b:
                continue

            title_b = event_b.get("title") or event_b.get("summary") or ""

            # Check if times overlap
            if not _times_overlap(event_a, event_b):
                continue

            same_specs = are_same_specifications(event_a, event_b)

            time_a = f"{event_a.get('time_start', 'All-day')}"
            if event_a.get("time_end"):
                time_a += f" → {event_a['time_end']}"

            time_b = f"{event_b.get('time_start', 'All-day')}"
            if event_b.get("time_end"):
                time_b += f" → {event_b['time_end']}"

            if same_specs:
                pair_key = ("same_specs", title_a, event_a.get("date"), event_a.get("time_start"))
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    conflicts.append({
                        "conflict_type": "same_specs",
                        "action": "skip",
                        "event_a": title_a or "Untitled",
                        "event_b": title_b or "Untitled",
                        "date": event_a.get("date", "Unknown Date"),
                        "time_a": time_a,
                        "time_b": time_b,
                        "location_a": event_a.get("location", ""),
                        "location_b": event_b.get("location", ""),
                    })
            else:
                pair_key = ("different_specs", frozenset([title_a, title_b]), event_a.get("date"))
                if pair_key not in seen_pairs:
                    seen_pairs.add(pair_key)
                    conflicts.append({
                        "conflict_type": "different_specs",
                        "action": "add",
                        "event_a": title_a or "Untitled",
                        "event_b": title_b or "Untitled",
                        "date": event_a.get("date", "Unknown Date"),
                        "time_a": time_a,
                        "time_b": time_b,
                        "location_a": event_a.get("location", ""),
                        "location_b": event_b.get("location", ""),
                    })

    return conflicts


def format_conflict_warnings(conflicts: list[dict]) -> str:
    """Format conflicts into a user-facing message at the end of addition.

    Separates:
    - Same time & specifications: NOT added, user warned.
    - Same time & different specifications: ADDED, user warned of clash.
    """
    if not conflicts:
        return ""

    same_specs = [c for c in conflicts if c.get("conflict_type") == "same_specs"]
    different_specs = [c for c in conflicts if c.get("conflict_type") != "same_specs"]

    sections = []

    if same_specs:
        lines = ["\n⚠️ *تنبيه: تم تخطي مواعيد مسجلة مسبقاً (Same Time & Specs - Skipped):*"]
        for c in same_specs:
            lines.append(
                f"  • تم تخطي *{c['event_a']}* ({c['time_a']}) في يوم {c['date']} لوجود نفس الموعد مسبقاً بنفس المواصفات."
            )
        sections.append("\n".join(lines))

    if different_specs:
        lines = ["\n⚠️ *تنبيه: تم رصد تعارض في المواعيد (Time Conflicts - Both Added):*"]
        for c in different_specs:
            lines.append(
                f"  • تعارض بين *{c['event_a']}* ({c['time_a']}) و *{c['event_b']}* ({c['time_b']}) في يوم {c['date']}."
            )
        lines.append("تمت إضافة كلا الموعدين إلى تقويمك — يمكنك تعديل أو حذف أحدهما إذا رغبت.")
        sections.append("\n".join(lines))

    return "\n".join(sections)

