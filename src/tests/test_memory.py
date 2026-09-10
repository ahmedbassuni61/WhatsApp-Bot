"""Tests for ConversationMemory."""

import asyncio
import json
import os
import tempfile

import pytest

from src.agents.memory import ConversationMemory


@pytest.fixture
def tmp_path():
    """Create a temporary file path for memory persistence tests."""
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)  # start fresh
    yield path
    if os.path.exists(path):
        os.unlink(path)


# ------------------------------------------------------------------ #
# Basic functionality
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_add_and_get():
    """Messages added are retrievable via get_history."""
    mem = ConversationMemory(path=None, max_turns=10)
    await mem.add_message("user1", "user", "hello")
    await mem.add_message("user1", "assistant", "hi there!")

    history = mem.get_history("user1")
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "hello"}
    assert history[1] == {"role": "assistant", "content": "hi there!"}


@pytest.mark.asyncio
async def test_separate_users():
    """Each user has independent history."""
    mem = ConversationMemory(path=None, max_turns=10)
    await mem.add_message("user1", "user", "hello from user1")
    await mem.add_message("user2", "user", "hello from user2")

    assert len(mem.get_history("user1")) == 1
    assert len(mem.get_history("user2")) == 1
    assert mem.get_history("user1")[0]["content"] == "hello from user1"
    assert mem.get_history("user2")[0]["content"] == "hello from user2"


@pytest.mark.asyncio
async def test_empty_history():
    """get_history returns empty list for unknown users."""
    mem = ConversationMemory(path=None)
    assert mem.get_history("unknown") == []


# ------------------------------------------------------------------ #
# Trimming
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_trim_to_max_turns():
    """History is trimmed to max_turns pairs (2 messages per turn)."""
    mem = ConversationMemory(path=None, max_turns=2)  # keep 4 messages max

    # Add 3 full turns (6 messages)
    for i in range(3):
        await mem.add_message("user1", "user", f"q{i}")
        await mem.add_message("user1", "assistant", f"a{i}")

    history = mem.get_history("user1")
    assert len(history) == 4  # 2 turns * 2 messages
    # Should keep the most recent 2 turns
    assert history[0]["content"] == "q1"
    assert history[1]["content"] == "a1"
    assert history[2]["content"] == "q2"
    assert history[3]["content"] == "a2"


# ------------------------------------------------------------------ #
# Persistence
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_persistence(tmp_path):
    """Memory persists to disk and reloads on new instance."""
    mem1 = ConversationMemory(path=tmp_path, max_turns=10)
    await mem1.add_message("user1", "user", "remember me")
    await mem1.add_message("user1", "assistant", "I will!")

    # Create a new instance pointing at the same file
    mem2 = ConversationMemory(path=tmp_path, max_turns=10)
    history = mem2.get_history("user1")
    assert len(history) == 2
    assert history[0]["content"] == "remember me"


# ------------------------------------------------------------------ #
# Clear
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_clear_user():
    """clear() removes history for a single user only."""
    mem = ConversationMemory(path=None)
    await mem.add_message("user1", "user", "hello")
    await mem.add_message("user2", "user", "hello")
    await mem.clear("user1")

    assert mem.get_history("user1") == []
    assert len(mem.get_history("user2")) == 1


@pytest.mark.asyncio
async def test_clear_all():
    """clear_all() removes all history."""
    mem = ConversationMemory(path=None)
    await mem.add_message("user1", "user", "hello")
    await mem.add_message("user2", "user", "hello")
    await mem.clear_all()

    assert mem.get_history("user1") == []
    assert mem.get_history("user2") == []
    assert mem.users == []
