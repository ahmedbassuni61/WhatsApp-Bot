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
