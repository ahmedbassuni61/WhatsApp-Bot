"""
Per-user conversation memory with JSON file persistence.

Stores chat history keyed by JID (WhatsApp user/group ID) so the LLM
receives recent context for follow-up messages.  History is auto-trimmed
to ``max_turns`` pairs and saved to disk on every write.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default storage path — lives inside a `data/` folder at the project root
_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "memory.json",
)


class ConversationMemory:
    """Thread-safe, file-backed per-user conversation memory."""

    def __init__(self, path: str | None = None, max_turns: int = 20):
        """
        Args:
            path:      File path for JSON persistence.  ``None`` = in-memory only.
            max_turns: Maximum number of message *pairs* (user+assistant) to keep
                       per user.  Older messages are dropped automatically.
        """
        self._path = path
        self._max_messages = max_turns * 2  # each turn = 1 user + 1 assistant
        self._store: dict[str, list[dict[str, str]]] = {}
        self._lock = asyncio.Lock()
        self._load()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def get_history(self, user_id: str) -> list[dict[str, str]]:
        """Return the conversation history for *user_id* (may be empty).

        Each entry is ``{"role": "user"|"assistant", "content": "..."}``.
        """
        return list(self._store.get(user_id, []))

    async def add_message(self, user_id: str, role: str, content: str) -> None:
        """Append a message and persist.

        Args:
            user_id: JID or other unique identifier.
            role:    ``"user"`` or ``"assistant"``.
            content: The message text.
        """
        async with self._lock:
            if user_id not in self._store:
                self._store[user_id] = []

            self._store[user_id].append({"role": role, "content": content})

            # Trim to max_messages (keep the most recent)
            if len(self._store[user_id]) > self._max_messages:
                self._store[user_id] = self._store[user_id][-self._max_messages:]

            self._save()

    async def clear(self, user_id: str) -> None:
        """Erase all history for a single user."""
        async with self._lock:
            self._store.pop(user_id, None)
            self._save()

    async def clear_all(self) -> None:
        """Erase all history for all users."""
        async with self._lock:
            self._store.clear()
            self._save()

    @property
    def users(self) -> list[str]:
        """Return a list of user IDs that have stored history."""
        return list(self._store.keys())

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        """Load history from disk (if path is set and file exists)."""
        if not self._path:
            return
        try:
            p = Path(self._path)
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._store = data
                    logger.info("Loaded conversation memory: %d users", len(self._store))
        except Exception as e:
            logger.warning("Failed to load memory from %s: %s", self._path, e)

    def _save(self) -> None:
        """Persist current state to disk (if path is set)."""
        if not self._path:
            return
        try:
            p = Path(self._path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self._store, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("Failed to save memory to %s: %s", self._path, e)


# ------------------------------------------------------------------ #
# Global instance (persistent)
# ------------------------------------------------------------------ #
memory = ConversationMemory(path=_DEFAULT_PATH)
