"""Tests for calendar conflict detection and warning formatting."""

import pytest
from src.agents.conflict_resolver import (
    _parse_time,
    _times_overlap,
    detect_conflicts,
    format_conflict_warnings,
)


def test_parse_time():
    assert _parse_time("14:30") is not None
    assert _parse_time("invalid") is None
    assert _parse_time("") is None


def test_times_overlap_different_dates():
    ev_a = {"title": "A", "date": "2026-09-20", "time_start": "10:00", "time_end": "11:00"}
    ev_b = {"title": "B", "date": "2026-09-21", "time_start": "10:00", "time_end": "11:00"}
    assert not _times_overlap(ev_a, ev_b)


def test_times_overlap_same_date_overlapping():
    ev_a = {"title": "A", "date": "2026-09-20", "time_start": "10:00", "time_end": "12:00"}
    ev_b = {"title": "B", "date": "2026-09-20", "time_start": "11:00", "time_end": "13:00"}
    assert _times_overlap(ev_a, ev_b)


def test_times_overlap_adjacent_non_overlapping():
    ev_a = {"title": "A", "date": "2026-09-20", "time_start": "10:00", "time_end": "11:00"}
    ev_b = {"title": "B", "date": "2026-09-20", "time_start": "11:00", "time_end": "12:00"}
    assert not _times_overlap(ev_a, ev_b)


def test_times_overlap_all_day_event():
    # Two all-day events on same date overlap
    ev_a = {"title": "A", "date": "2026-09-20"}
    ev_b = {"title": "B", "date": "2026-09-20"}
    assert _times_overlap(ev_a, ev_b)

    # An all-day note and a timed event do not clash
    ev_c = {"title": "C", "date": "2026-09-20", "time_start": "11:00", "time_end": "12:00"}
    assert not _times_overlap(ev_a, ev_c)


def test_detect_conflicts_batch_and_existing():
    new_events = [
        {"title": "Math Exam", "date": "2026-09-22", "time_start": "09:00", "time_end": "11:00"},
        {"title": "Physics Quiz", "date": "2026-09-22", "time_start": "10:00", "time_end": "12:00"},
    ]
    existing_events = [
        {
            "title": "Existing Lecture",
            "start": "2026-09-22T08:30:00",
            "end": "2026-09-22T09:30:00",
        }
    ]

    conflicts = detect_conflicts(new_events, existing_events)
    assert len(conflicts) >= 2

    warning_text = format_conflict_warnings(conflicts)
    assert "تعارض" in warning_text or "Conflicts Detected" in warning_text
    assert "Math Exam" in warning_text


def test_same_name_different_times_not_overlapping():
    """Verify events with similar names at different times on the same day do not overlap."""
    ev_1 = {"title": "HUM x32 - Law", "date": "2026-09-20", "time_start": "14:30", "time_end": "15:30"}
    ev_2 = {"title": "HUM x32 - Law", "date": "2026-09-20", "time_start": "15:30", "time_end": "16:30"}
    assert not _times_overlap(ev_1, ev_2)
    conflicts = detect_conflicts([ev_1, ev_2], [])
    assert len(conflicts) == 0


def test_are_same_specifications():
    from src.agents.conflict_resolver import are_same_specifications

    # Identical specs
    ev_1 = {"title": "[LECTURE] Math 101", "location": "Hall 1", "event_type": "lecture"}
    ev_2 = {"title": "محاضرة Math 101", "location": "Hall 1", "event_type": "lecture"}
    assert are_same_specifications(ev_1, ev_2)

    # Different event types (lecture vs lab)
    ev_3 = {"title": "[LAB] Math 101", "location": "Hall 1", "event_type": "lab"}
    assert not are_same_specifications(ev_1, ev_3)

    # Different locations
    ev_4 = {"title": "[LECTURE] Math 101", "location": "Hall 2", "event_type": "lecture"}
    assert not are_same_specifications(ev_1, ev_4)

    # Different subjects
    ev_5 = {"title": "[LECTURE] Physics 101", "location": "Hall 1", "event_type": "lecture"}
    assert not are_same_specifications(ev_1, ev_5)


def test_conflict_same_time_different_specifications():
    """If conflict at same time but different specs -> added, user warned."""
    new_ev = [{"title": "[LAB] EEC 461", "date": "2026-09-21", "time_start": "10:00", "time_end": "11:00", "event_type": "lab"}]
    existing_ev = [{"title": "[LECTURE] EEC 471", "start": "2026-09-21T10:00:00", "end": "2026-09-21T11:00:00", "event_type": "lecture"}]

    conflicts = detect_conflicts(new_ev, existing_ev)
    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "different_specs"
    assert conflicts[0]["action"] == "add"

    warning = format_conflict_warnings(conflicts)
    assert "تعارض في المواعيد" in warning
    assert "EEC 461" in warning
    assert "EEC 471" in warning


def test_conflict_same_time_same_specifications():
    """If conflict at same time with same specs -> skipped, user warned."""
    new_ev = [{"title": "[LECTURE] EEC 471", "date": "2026-09-21", "time_start": "10:00", "time_end": "11:00", "location": "Hall 3", "event_type": "lecture"}]
    existing_ev = [{"title": "[LECTURE] EEC 471", "start": "2026-09-21T10:00:00", "end": "2026-09-21T11:00:00", "location": "Hall 3", "event_type": "lecture"}]

    conflicts = detect_conflicts(new_ev, existing_ev)
    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "same_specs"
    assert conflicts[0]["action"] == "skip"

    warning = format_conflict_warnings(conflicts)
    assert "تم تخطي" in warning
    assert "EEC 471" in warning
    assert "نفس المواصفات" in warning


def test_format_deleted_schedule():
    from src.whatsapp.calendar_sync import CalendarSync
    cal = CalendarSync(credentials_path="./dummy", token_path="./dummy")

    deleted = [
        {
            "title": "معمل الدوائر المتكاملة الرقمية EEC 431",
            "summary": "[LAB] معمل الدوائر المتكاملة الرقمية EEC 431",
            "date": "2026-09-19",
            "time_start": "11:30",
            "time_end": "12:30",
            "location": "معمل 4",
            "event_type": "lab",
        },
        {
            "title": "محاضرة نظم التحكم الآلي EEC 471",
            "summary": "[LECTURE] محاضرة نظم التحكم الآلي EEC 471",
            "date": "2026-09-19",
            "time_start": "12:30",
            "time_end": "13:30",
            "location": "مدرج 3",
            "event_type": "lecture",
        },
    ]

    msg = cal.format_deleted_schedule(deleted)
    assert "🗑️ *تم حذف 2 مواعيد من Google Calendar بنجاح*:" in msg
    assert "السبت (Saturday), Sep 19" in msg
    assert "🔬 [LAB] *معمل الدوائر المتكاملة الرقمية EEC 431* — 🕐 11:30 → 12:30 | 📍 معمل 4" in msg
    assert "📚 [LECTURE] *محاضرة نظم التحكم الآلي EEC 471* — 🕐 12:30 → 13:30 | 📍 مدرج 3" in msg
