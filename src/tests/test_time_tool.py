"""
Unit tests for TimeTool and relative time resolution.
"""

from datetime import datetime, timedelta
from src.tools.time_tool import time_tool, TimeTool


def test_get_current_time_structure():
    """Verify structured time output contains all required fields."""
    info = time_tool.get_current_time()
    assert "date" in info
    assert "time_24h" in info
    assert "weekday" in info
    assert "timezone" in info
    assert "utc_offset" in info
    assert "tomorrow_date" in info

    # Date should match YYYY-MM-DD
    parts = info["date"].split("-")
    assert len(parts) == 3
    assert len(parts[0]) == 4  # Year

    # 24h time should match HH:MM
    time_parts = info["time_24h"].split(":")
    assert len(time_parts) == 2
    assert 0 <= int(time_parts[0]) <= 23
    assert 0 <= int(time_parts[1]) <= 59


def test_resolve_next_hour():
    """Verify 'next hour' and 'in an hour' are exactly 1 hour from current time."""
    now = time_tool.get_current_datetime()
    expected_dt = now + timedelta(hours=1)
    expected_date = expected_dt.strftime("%Y-%m-%d")
    expected_time = expected_dt.strftime("%H:%M")

    res = time_tool.resolve_relative_time_phrase("add a deadline next hour")
    assert res is not None
    assert res["date"] == expected_date
    assert res["time_start"] == expected_time
    assert res["matched_phrase"] == "next hour"

    res_ar = time_tool.resolve_relative_time_phrase("ضيف تسليم كمان ساعة")
    assert res_ar is not None
    assert res_ar["date"] == expected_date
    assert res_ar["time_start"] == expected_time


def test_resolve_in_2_hours():
    """Verify 'in 2 hours' and Arabic 'كمان ساعتين'."""
    now = time_tool.get_current_datetime()
    expected_dt = now + timedelta(hours=2)
    expected_date = expected_dt.strftime("%Y-%m-%d")
    expected_time = expected_dt.strftime("%H:%M")

    res = time_tool.resolve_relative_time_phrase("assignment due in 2 hours")
    assert res is not None
    assert res["date"] == expected_date
    assert res["time_start"] == expected_time

    res_ar = time_tool.resolve_relative_time_phrase("تسليم البروجكت بعد ساعتين")
    assert res_ar is not None
    assert res_ar["date"] == expected_date
    assert res_ar["time_start"] == expected_time


def test_resolve_tomorrow():
    """Verify 'tomorrow' sets tomorrow's date."""
    now = time_tool.get_current_datetime()
    expected_tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    res = time_tool.resolve_relative_time_phrase("exam tomorrow")
    assert res is not None
    assert res["date"] == expected_tomorrow


def test_patch_event_time():
    """Verify patch_event_time fills in missing time_start from relative phrase."""
    event = {
        "title": "Deadline",
        "action": "create",
        "date": "2026-09-07",
        "time_start": None,
        "time_end": None,
    }
    patched = time_tool.patch_event_time(event, "add a deadline next hour")
    assert patched["time_start"] is not None
    assert patched["time_end"] is not None
    assert patched["time_start"] != patched["time_end"]
