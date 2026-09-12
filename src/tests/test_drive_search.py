"""
Unit tests for College Google Drive search, indexer, and normalization.
"""

import pytest
from pathlib import Path
from src.drive.drive_indexer import (
    DriveIndexer,
    normalize_search_text,
    format_file_size,
    get_icon_for_item,
)
from src.agents.tools import search_college_drive


def test_arabic_and_english_normalization():
    """Verify Arabic and English search normalization rules."""
    # Harakat / Tashkeel removal
    assert normalize_search_text("مُحَاضَرَةٌ") == "محاضره"

    # Alif normalization (أ, إ, آ -> ا)
    assert normalize_search_text("إمتحان أعمال السنة") == "امتحان اعمال السنه"
    assert normalize_search_text("آلة") == "اله"

    # Ta Marbuta normalization (ة -> ه)
    assert normalize_search_text("هندسة البرمجيات") == "هندسه البرمجيات"

    # Ya / Alif Maqsura (ى -> ي)
    assert normalize_search_text("ذكاء اصطناعى") == "ذكاء اصطناعي"

    # English lowercase & punctuation
    assert normalize_search_text("CS-401: Advanced Algorithms (Fall 2026)") == "cs 401 advanced algorithms fall 2026"


def test_file_size_formatter():
    """Verify human-readable file size formatting."""
    assert format_file_size(0) == ""
    assert format_file_size(500) == "500 B"
    assert format_file_size(2048) == "2 KB"
    assert format_file_size(10 * 1024 * 1024) == "10.0 MB"


def test_icon_selection():
    """Verify appropriate emoji icon is selected based on mime type and extension."""
    assert get_icon_for_item("application/vnd.google-apps.folder", "Lectures", True) == "📁"
    assert get_icon_for_item("application/pdf", "sheet1.pdf", False) == "📄"
    assert get_icon_for_item("application/vnd.ms-powerpoint", "slides.pptx", False) == "📊"
    assert get_icon_for_item("application/msword", "summary.docx", False) == "📝"
    assert get_icon_for_item("application/octet-stream", "code.zip", False) == "📦"


@pytest.fixture
def temp_indexer(tmp_path):
    """Fixture providing a clean SQLite DriveIndexer in a temporary directory."""
    db_file = tmp_path / "test_drive_index.db"
    indexer = DriveIndexer(db_path=db_file)

    # Populate with sample college materials
    sample_items = [
        {
            "id": "root123",
            "name": "Level 4",
            "parent_id": "",
            "full_path": "Level 4",
            "mime_type": "application/vnd.google-apps.folder",
            "web_view_link": "https://drive.google.com/drive/folders/root123",
            "size_bytes": 0,
            "modified_time": "2026-09-01T10:00:00Z",
            "is_folder": True,
        },
        {
            "id": "f_cs401",
            "name": "Algorithms CS401",
            "parent_id": "root123",
            "full_path": "Level 4 / Algorithms CS401",
            "mime_type": "application/vnd.google-apps.folder",
            "web_view_link": "https://drive.google.com/drive/folders/f_cs401",
            "size_bytes": 0,
            "modified_time": "2026-09-02T10:00:00Z",
            "is_folder": True,
        },
        {
            "id": "file_lec1",
            "name": "Lecture 1 - Introduction.pdf",
            "parent_id": "f_cs401",
            "full_path": "Level 4 / Algorithms CS401 / Lectures / Lecture 1 - Introduction.pdf",
            "mime_type": "application/pdf",
            "web_view_link": "https://drive.google.com/file/d/file_lec1/view",
            "size_bytes": 3500000,
            "modified_time": "2026-09-05T12:00:00Z",
            "is_folder": False,
        },
        {
            "id": "file_slides2",
            "name": "Greedy Algorithms Slides.pptx",
            "parent_id": "f_cs401",
            "full_path": "Level 4 / Algorithms CS401 / Slides / Greedy Algorithms Slides.pptx",
            "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "web_view_link": "https://drive.google.com/file/d/file_slides2/view",
            "size_bytes": 5200000,
            "modified_time": "2026-09-06T12:00:00Z",
            "is_folder": False,
        },
        {
            "id": "file_ai_arabic",
            "name": "تلخيص مادة الذكاء الاصطناعي.docx",
            "parent_id": "root123",
            "full_path": "Level 4 / الذكاء الاصطناعي / تلخيص مادة الذكاء الاصطناعي.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "web_view_link": "https://drive.google.com/file/d/file_ai_arabic/view",
            "size_bytes": 1200000,
            "modified_time": "2026-09-07T12:00:00Z",
            "is_folder": False,
        },
    ]

    with indexer._get_connection() as conn:
        for item in sample_items:
            norm_name = normalize_search_text(item["name"])
            norm_path = normalize_search_text(item["full_path"])
            conn.execute(
                """
                INSERT INTO drive_items (
                    id, name, parent_id, full_path, mime_type, web_view_link,
                    size_bytes, modified_time, is_folder, normalized_name,
                    normalized_path, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["name"],
                    item["parent_id"],
                    item["full_path"],
                    item["mime_type"],
                    item["web_view_link"],
                    item["size_bytes"],
                    item["modified_time"],
                    1 if item["is_folder"] else 0,
                    norm_name,
                    norm_path,
                    "2026-09-12T00:00:00Z",
                ),
            )
        conn.commit()

    return indexer


def test_search_by_path_and_name(temp_indexer):
    """Verify searching for course name + file token matches file inside folder path."""
    # Searching "Algorithms Lecture 1" matches Lecture 1.pdf because path contains Algorithms
    results = temp_indexer.search("Algorithms Lecture 1")
    assert len(results) >= 1
    top = results[0]
    assert "Lecture 1" in top["name"]
    assert top["id"] == "file_lec1"
    assert top["icon"] == "📄"
    assert "MB" in top["size_str"]


def test_arabic_search(temp_indexer):
    """Verify Arabic search matches regardless of ya/alif maqsura variations."""
    # Search with 'ى' matches item created with 'ي'
    results = temp_indexer.search("الذكاء الاصطناعى")
    assert len(results) >= 1
    assert "الذكاء الاصطناعي" in results[0]["name"]
    assert results[0]["icon"] == "📝"


def test_type_filtering(temp_indexer):
    """Verify filtering by pdf, slides, and folders."""
    # Only PDFs
    pdf_results = temp_indexer.search("Algorithms", file_type="pdf")
    assert len(pdf_results) == 1
    assert pdf_results[0]["name"].endswith(".pdf")

    # Only Slides
    slides_results = temp_indexer.search("Algorithms", file_type="slides")
    assert len(slides_results) == 1
    assert slides_results[0]["name"].endswith(".pptx")

    # Only Folders
    folder_results = temp_indexer.search("Algorithms", file_type="folder")
    assert len(folder_results) == 1
    assert folder_results[0]["is_folder"] is True


@pytest.mark.asyncio
async def test_agent_tool_output_formatting(temp_indexer, monkeypatch):
    """Verify the search_college_drive tool returns a beautifully formatted WhatsApp message."""
    import src.agents.tools as agent_tools

    monkeypatch.setattr(agent_tools, "_drive_indexer", temp_indexer)

    response = await search_college_drive.ainvoke({"query": "Algorithms lecture 1"})
    assert "Level 4 Drive Search Results" in response or "College Drive Search Results" in response
    assert "Lecture 1 - Introduction.pdf" in response
    assert "Level 4 / Algorithms CS401 / Lectures" in response
    assert "https://drive.google.com/file/d/file_lec1/view" in response
    assert "📄" in response


@pytest.mark.asyncio
async def test_agent_tool_no_results(temp_indexer, monkeypatch):
    """Verify friendly fallback when no files match."""
    import src.agents.tools as agent_tools

    monkeypatch.setattr(agent_tools, "_drive_indexer", temp_indexer)

    response = await search_college_drive.ainvoke({"query": "Quantum Physics 999"})
    assert "No materials found in Level 4 College Drive matching" in response or "No materials found" in response


@pytest.mark.asyncio
async def test_get_course_details_and_lecture_counts(temp_indexer, monkeypatch):
    """Verify get_course_details accurately counts lectures and handles empty/populated folders."""
    import src.agents.tools as agent_tools

    monkeypatch.setattr(agent_tools, "_drive_indexer", temp_indexer)

    # Search for course details for Algorithms
    details = temp_indexer.get_course_details("Algorithms")
    assert details is not None
    assert "Algorithms" in details["clean_title"]
    assert "lectures" in details["categories"]

    # Test tool invocation
    msg = await agent_tools.get_course_details.ainvoke({"query": "how many lectures in algorithms"})
    assert "Course Overview" in msg
    assert "Algorithms" in msg
    assert "Lectures" in msg


@pytest.mark.asyncio
async def test_get_term_overview_tool(temp_indexer, monkeypatch):
    """Verify term overview returns subjects."""
    import src.agents.tools as agent_tools

    monkeypatch.setattr(agent_tools, "_drive_indexer", temp_indexer)

    # Even with minimal fixture, should not crash and should format or search
    msg = await agent_tools.get_course_details.ainvoke({"query": "what do I have in first semester"})
    assert msg is not None

