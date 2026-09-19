"""Tests for reflector deterministic validation and cleaning."""

import pytest
from src.agents.reflector import (
    clean_and_deduplicate_events,
    deterministic_checks,
    REFLECTOR_PROMPT,
)


def test_reflector_prompt_format_no_key_error():
    formatted = REFLECTOR_PROMPT.format(
        user_message="Test request",
        assistant_response="Test response",
        tool_history="Tool result",
        image_section="Image info",
        events_section="Events list",
    )
    assert "Test request" in formatted
    assert '{"verdict":' in formatted


def test_clean_and_deduplicate_events_removes_duplicates():
    events = [
        {"title": "Math Exam", "date": "2026-09-25", "time_start": "09:00", "time_end": "11:00"},
        {"title": "math exam", "date": "2026-09-25", "time_start": "09:00", "time_end": "11:00"},
    ]
    cleaned, problems = clean_and_deduplicate_events(events)
    assert len(cleaned) == 1
    assert problems == []


def test_clean_and_deduplicate_auto_fixes_inverted_times():
    events = [
        {"title": "Physics Lab", "date": "2026-09-25", "time_start": "14:00", "time_end": "12:00"},
    ]
    cleaned, problems = clean_and_deduplicate_events(events)
    assert len(cleaned) == 1
    assert cleaned[0]["time_end"] == "15:00"


def test_deterministic_checks_catches_invalid_dates():
    events = [
        {"title": "Bad Event", "date": "invalid-date", "time_start": "10:00"},
    ]
    problems = deterministic_checks(events)
    assert len(problems) == 1
    assert "invalid date format" in problems[0].lower()


def test_deterministic_checks_catches_missing_dates():
    events = [
        {"title": "Missing Date Event", "time_start": "10:00"},
    ]
    problems = deterministic_checks(events)
    assert len(problems) == 1
    assert "missing a date" in problems[0].lower()
