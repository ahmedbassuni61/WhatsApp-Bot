"""
Tests for the interactive WhatsApp menu system (poll-based).

Covers:
- extract_interactive_response() parsing all 5 webhook formats (including polls)
- InteractiveHandler callback routing
- Poll vote resolution (text → action mapping)
- Menu trigger detection
- Drive navigation state management
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.whatsapp.interactive import (
    MENU_TRIGGERS,
    InteractiveHandler,
    extract_interactive_response,
)


# ------------------------------------------------------------------ #
# extract_interactive_response tests (Poll extraction)
# ------------------------------------------------------------------ #


class TestExtractInteractiveResponse:
    """Test poll response formats."""

    def test_poll_vote_response(self):
        msg = {
            "pollUpdateMessage": {
                "vote": {
                    "selectedOptions": [{"name": "📁 Browse Drive"}],
                },
            }
        }
        result = extract_interactive_response(msg)
        assert result is not None
        assert result["type"] == "poll"
        assert result["id"] == ""  # Polls have no ID
        assert result["text"] == "📁 Browse Drive"

    def test_poll_vote_string_options(self):
        """Some Evolution API versions send options as plain strings."""
        msg = {
            "pollUpdateMessage": {
                "vote": {
                    "selectedOptions": ["📅 My Schedule"],
                },
            }
        }
        result = extract_interactive_response(msg)
        assert result is not None
        assert result["type"] == "poll"
        assert result["text"] == "📅 My Schedule"

    def test_poll_vote_message_alternative_key(self):
        """Handle pollVoteMessage key variant."""
        msg = {
            "pollVoteMessage": {
                "vote": {
                    "selectedOptions": [{"optionName": "💬 Ask a Question"}],
                },
            }
        }
        result = extract_interactive_response(msg)
        assert result is not None
        assert result["type"] == "poll"
        assert result["text"] == "💬 Ask a Question"

    def test_regular_text_returns_none(self):
        msg = {"conversation": "Hello, what's my schedule?"}
        result = extract_interactive_response(msg)
        assert result is None

    def test_empty_message_returns_none(self):
        result = extract_interactive_response({})
        assert result is None


# ------------------------------------------------------------------ #
# Menu trigger detection
# ------------------------------------------------------------------ #


class TestMenuTriggers:
    def test_english_triggers(self):
        handler = InteractiveHandler(MagicMock(), MagicMock(), MagicMock())
        assert handler.is_menu_trigger("menu")
        assert handler.is_menu_trigger("MENU")
        assert handler.is_menu_trigger("  Menu  ")
        assert handler.is_menu_trigger("/start")
        assert handler.is_menu_trigger("/menu")

    def test_arabic_triggers(self):
        handler = InteractiveHandler(MagicMock(), MagicMock(), MagicMock())
        assert handler.is_menu_trigger("قائمة")
        assert handler.is_menu_trigger("الأوامر")
        assert handler.is_menu_trigger("القائمة")

    def test_non_triggers(self):
        handler = InteractiveHandler(MagicMock(), MagicMock(), MagicMock())
        assert not handler.is_menu_trigger("hello")
        assert not handler.is_menu_trigger("what is my schedule")
        assert not handler.is_menu_trigger("show menu please")
        assert not handler.is_menu_trigger("")


# ------------------------------------------------------------------ #
# InteractiveHandler callback routing & poll resolution
# ------------------------------------------------------------------ #


class TestInteractiveHandler:
    """Test callback routing, poll vote resolution, and Drive navigation."""

    def _make_handler(self):
        client = AsyncMock()
        drive = AsyncMock()
        calendar = AsyncMock()
        handler = InteractiveHandler(client, drive, calendar)
        return handler, client, drive, calendar

    @pytest.mark.asyncio
    async def test_menu_sends_poll(self):
        handler, client, _, _ = self._make_handler()
        await handler.handle_callback("user@s.whatsapp.net", "menu")
        client.send_poll.assert_called_once()
        call_kwargs = client.send_poll.call_args
        assert "📋" in call_kwargs[1]["question"]
        assert len(call_kwargs[1]["options"]) == 3

    @pytest.mark.asyncio
    async def test_menu_registers_pending_polls(self):
        handler, client, _, _ = self._make_handler()
        jid = "user@s.whatsapp.net"
        await handler.send_main_menu(jid)

        # Should have 3 pending poll options
        pending = handler._pending_polls[jid]
        assert "📁 Browse Drive" in pending
        assert "📅 My Schedule" in pending
        assert "💬 Ask a Question" in pending

    @pytest.mark.asyncio
    async def test_resolve_poll_vote(self):
        handler, client, _, _ = self._make_handler()
        jid = "user@s.whatsapp.net"
        await handler.send_main_menu(jid)

        # Resolve a poll vote
        result = handler.resolve_poll_vote(jid, "📁 Browse Drive")
        assert result is not None
        assert result["action"] == "drive_root"

    def test_resolve_poll_vote_unknown(self):
        handler, _, _, _ = self._make_handler()
        result = handler.resolve_poll_vote("user@s.whatsapp.net", "unknown option")
        assert result is None

    @pytest.mark.asyncio
    async def test_resolve_poll_vote_shortcuts(self):
        handler, client, _, _ = self._make_handler()
        jid = "user@s.whatsapp.net"
        await handler.send_main_menu(jid)

        # Number shortcuts
        assert handler.resolve_poll_vote(jid, "1")["action"] == "drive_root"
        assert handler.resolve_poll_vote(jid, "2")["action"] == "schedule"
        assert handler.resolve_poll_vote(jid, "3")["action"] == "ask"

        # Text / keyword shortcuts
        assert handler.resolve_poll_vote(jid, "browse drive")["action"] == "drive_root"
        assert handler.resolve_poll_vote(jid, "schedule")["action"] == "schedule"
        assert handler.resolve_poll_vote(jid, "جدول")["action"] == "schedule"

    @pytest.mark.asyncio
    async def test_resolve_poll_vote_nav_keywords(self):
        handler, client, drive, _ = self._make_handler()
        jid = "user@s.whatsapp.net"
        handler._folder_stack[jid] = ["root123"]
        drive.explore_folder = AsyncMock(return_value={
            "folder_id": "sub1",
            "folder_name": "Term 1",
            "folder_link": "link",
            "subfolders": [],
            "files": [],
            "total_subfolders": 0,
            "total_files": 0,
        })
        await handler.send_drive_folder(jid, folder_id="sub1")

        # "back" or "رجوع"
        assert handler.resolve_poll_vote(jid, "back")["action"] == "drive_back"
        assert handler.resolve_poll_vote(jid, "رجوع")["action"] == "drive_back"
        assert handler.resolve_poll_vote(jid, "menu")["action"] == "menu"

    def test_extract_poll_from_data_payload(self):
        data = {
            "pollUpdates": [
                {
                    "pollUpdateMessageKey": {"id": "123"},
                    "vote": {"selectedOptions": [{"name": "📁 Browse Drive"}]},
                }
            ]
        }
        res = extract_interactive_response({}, data=data)
        assert res is not None
        assert res["type"] == "poll"
        assert res["text"] == "📁 Browse Drive"

    @pytest.mark.asyncio
    async def test_schedule_callback(self):
        handler, client, _, calendar = self._make_handler()
        calendar.get_upcoming_events = AsyncMock(return_value=[])
        calendar.format_schedule = MagicMock(return_value="📅 No upcoming events.")

        await handler.handle_callback("user@s.whatsapp.net", "schedule")
        client.send_text.assert_called_once_with("user@s.whatsapp.net", "📅 No upcoming events.")
        # Also sends nav poll after schedule
        client.send_poll.assert_called_once()

    @pytest.mark.asyncio
    async def test_drive_root_uses_poll(self):
        handler, client, drive, _ = self._make_handler()
        drive.explore_folder = AsyncMock(return_value={
            "folder_id": "root123",
            "folder_name": "College Drive",
            "folder_link": "https://drive.google.com/drive/folders/root123",
            "subfolders": [
                {"id": "sub1", "name": "Term 1", "link": "link1"},
                {"id": "sub2", "name": "Term 2", "link": "link2"},
            ],
            "files": [],
            "total_subfolders": 2,
            "total_files": 0,
        })

        await handler.handle_callback("user@s.whatsapp.net", "drive_root")
        drive.explore_folder.assert_called_once_with(folder_id=None)
        # Should send text (folder info) + poll (folder options)
        client.send_text.assert_called_once()
        client.send_poll.assert_called_once()
        # Poll should have folder options + "Main Menu"
        poll_options = client.send_poll.call_args[1]["options"]
        assert "📁 Term 1" in poll_options
        assert "📁 Term 2" in poll_options
        assert "📋 Main Menu" in poll_options

    @pytest.mark.asyncio
    async def test_drive_folder_registers_poll_mapping(self):
        handler, client, drive, _ = self._make_handler()
        jid = "user@s.whatsapp.net"
        drive.explore_folder = AsyncMock(return_value={
            "folder_id": "root123",
            "folder_name": "College Drive",
            "folder_link": "link",
            "subfolders": [
                {"id": "sub1", "name": "Term 1", "link": "link1"},
            ],
            "files": [],
            "total_subfolders": 1,
            "total_files": 0,
        })
        await handler.send_drive_folder(jid, folder_id=None)

        # Resolve the folder option
        result = handler.resolve_poll_vote(jid, "📁 Term 1")
        assert result is not None
        assert result["action"] == "drive_folder:sub1"

    @pytest.mark.asyncio
    async def test_drive_navigation_builds_stack(self):
        handler, client, drive, _ = self._make_handler()
        jid = "user@s.whatsapp.net"

        # Root
        drive.explore_folder = AsyncMock(return_value={
            "folder_id": "root123",
            "folder_name": "College Drive",
            "folder_link": "link",
            "subfolders": [{"id": "sub1", "name": "Term 1", "link": "link1"}],
            "files": [],
            "total_subfolders": 1,
            "total_files": 0,
        })
        await handler.send_drive_folder(jid, folder_id=None)
        assert handler._folder_stack[jid] == ["root123"]

        # Enter subfolder
        drive.explore_folder = AsyncMock(return_value={
            "folder_id": "sub1",
            "folder_name": "Term 1",
            "folder_link": "link",
            "subfolders": [],
            "files": [{"id": "f1", "name": "Lecture.pdf", "link": "link_f1", "size_str": "2 MB", "size_bytes": 2000000}],
            "total_subfolders": 0,
            "total_files": 1,
        })
        await handler.send_drive_folder(jid, folder_id="sub1")
        assert handler._folder_stack[jid] == ["root123", "sub1"]

    @pytest.mark.asyncio
    async def test_drive_back_pops_stack(self):
        handler, client, drive, _ = self._make_handler()
        jid = "user@s.whatsapp.net"
        handler._folder_stack[jid] = ["root123", "sub1"]

        drive.explore_folder = AsyncMock(return_value={
            "folder_id": "root123",
            "folder_name": "College Drive",
            "folder_link": "link",
            "subfolders": [],
            "files": [],
            "total_subfolders": 0,
            "total_files": 0,
        })
        await handler.handle_callback(jid, "drive_back")
        drive.explore_folder.assert_called_once_with(folder_id="root123")

    @pytest.mark.asyncio
    async def test_drive_subfolder_has_back_option(self):
        handler, client, drive, _ = self._make_handler()
        jid = "user@s.whatsapp.net"
        handler._folder_stack[jid] = ["root123"]  # Already in root

        drive.explore_folder = AsyncMock(return_value={
            "folder_id": "sub1",
            "folder_name": "Term 1",
            "folder_link": "link",
            "subfolders": [{"id": "sub2", "name": "Lectures", "link": "link2"}],
            "files": [],
            "total_subfolders": 1,
            "total_files": 0,
        })
        await handler.send_drive_folder(jid, folder_id="sub1")
        poll_options = client.send_poll.call_args[1]["options"]
        assert "⬅️ Back" in poll_options

    @pytest.mark.asyncio
    async def test_ask_callback(self):
        handler, client, _, _ = self._make_handler()
        await handler.handle_callback("user@s.whatsapp.net", "ask")
        client.send_text.assert_called_once()
        assert "type your question" in client.send_text.call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        handler, client, _, _ = self._make_handler()
        await handler.handle_callback("user@s.whatsapp.net", "unknown_action_xyz")
        client.send_text.assert_called_once()
        assert "unknown" in client.send_text.call_args[0][1].lower()

    @pytest.mark.asyncio
    async def test_empty_folder(self):
        handler, client, drive, _ = self._make_handler()
        drive.explore_folder = AsyncMock(return_value={
            "folder_id": "empty1",
            "folder_name": "Empty Folder",
            "folder_link": "link",
            "subfolders": [],
            "files": [],
            "total_subfolders": 0,
            "total_files": 0,
        })
        await handler.send_drive_folder("user@s.whatsapp.net", folder_id="empty1")
        # Should send text with folder info
        client.send_text.assert_called_once()
        # Should still send nav poll
        client.send_poll.assert_called_once()

    @pytest.mark.asyncio
    async def test_files_shown_as_text_links(self):
        handler, client, drive, _ = self._make_handler()
        drive.explore_folder = AsyncMock(return_value={
            "folder_id": "folder1",
            "folder_name": "Lectures",
            "folder_link": "link",
            "subfolders": [],
            "files": [
                {"id": "f1", "name": "Lecture 1.pdf", "link": "https://drive.google.com/file/f1", "size_str": "1.5 MB", "size_bytes": 1500000},
                {"id": "f2", "name": "Lecture 2.pdf", "link": "https://drive.google.com/file/f2", "size_str": "2 MB", "size_bytes": 2000000},
            ],
            "total_subfolders": 0,
            "total_files": 2,
        })
        await handler.send_drive_folder("user@s.whatsapp.net", folder_id="folder1")
        # Text message should contain file links
        text_msg = client.send_text.call_args[0][1]
        assert "Lecture 1.pdf" in text_msg
        assert "https://drive.google.com/file/f1" in text_msg
        assert "1.5 MB" in text_msg
