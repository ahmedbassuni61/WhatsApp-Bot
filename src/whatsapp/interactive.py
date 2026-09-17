"""
Interactive WhatsApp Menu Handler — native poll-based Drive browsing & schedule access.

Uses WhatsApp **polls** (single-select) as the official interactive selection UI,
guaranteeing 100% native rendering across all WhatsApp mobile clients (Android, iOS),
WhatsApp Web, and WhatsApp Desktop.

Flow:
    1. User types "menu" → bot sends a single-select poll
    2. User votes on an option (or replies with number/text) → executes the action
    3. For Drive browsing: folders shown as poll options, files as text links
"""

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# Trigger words that show the main menu instead of going to the LLM agent
MENU_TRIGGERS = frozenset({
    "menu", "start", "/start", "/menu",
    "قائمة", "الأوامر", "القائمة",
})

# Max poll options (Evolution API practical limit; WhatsApp official max is 12
# but some API versions reject >8)
_MAX_POLL_OPTIONS = 12


def _clean_text(s: str) -> str:
    """Normalize string by removing common emoji icons and extra whitespace."""
    for ch in ("📁", "📅", "💬", "📂", "📄", "📍", "⬅️", "📋", "🏠", "•", "-", "*", "_", "ℹ️", "⚠️"):
        s = s.replace(ch, "")
    return " ".join(s.lower().split())


class InteractiveHandler:
    """Manages poll-based interactive menus and per-user Drive navigation state."""

    def __init__(self, evolution_client, drive_client, calendar_sync):
        self.client = evolution_client
        self.drive = drive_client
        self.calendar = calendar_sync
        # Per-user folder stack for "Back" navigation
        self._folder_stack: dict[str, list[str]] = defaultdict(list)
        # Per-user mapping of last poll options → action data
        # Key: JID, Value: dict mapping option_text → {"action": str, "data": dict}
        self._pending_polls: dict[str, dict[str, dict]] = defaultdict(dict)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def handle_callback(self, jid: str, action_id: str, display_text: str = "") -> None:
        """Route a button/list click or poll vote to the appropriate handler.

        Args:
            jid: Sender's WhatsApp JID
            action_id: The action string (from button ID or matched poll text)
            display_text: The display text shown to the user
        """
        logger.info("🔘 Interactive callback from %s: action='%s' text='%s'",
                     jid[:25], action_id[:60], display_text[:40])

        try:
            if action_id == "menu":
                await self.send_main_menu(jid)
            elif action_id == "drive_root":
                self._folder_stack[jid].clear()
                await self.send_drive_folder(jid, folder_id=None)
            elif action_id.startswith("drive_folder:"):
                folder_id = action_id.split(":", 1)[1]
                await self.send_drive_folder(jid, folder_id=folder_id)
            elif action_id == "drive_back":
                await self._go_back(jid)
            elif action_id.startswith("drive_file:"):
                file_link = action_id.split(":", 1)[1]
                await self._send_file_link(jid, display_text, file_link)
            elif action_id == "schedule":
                await self._send_schedule(jid)
            elif action_id == "ask":
                await self.client.send_text(
                    jid, "💬 Just type your question and I'll answer! You can ask about any topic."
                )
            else:
                logger.warning("Unknown interactive action: %s", action_id)
                await self.client.send_text(jid, "⚠️ Unknown action. Type *menu* to see options.")
        except Exception as e:
            logger.error("Interactive handler error for action '%s': %s", action_id, e, exc_info=True)
            await self.client.send_text(jid, "⚠️ Something went wrong. Please try again or type *menu*.")

    def is_menu_trigger(self, text: str) -> bool:
        """Check if text is a menu trigger word."""
        return text.strip().lower() in MENU_TRIGGERS

    def resolve_poll_vote(self, jid: str, vote_text: str) -> dict | None:
        """Look up a poll vote or text shortcut in the user's pending poll options.

        Supports:
        - Exact match (e.g. "📁 Browse Drive")
        - Cleaned text match (e.g. "browse drive")
        - Numeric shortcuts (e.g. "1", "2", "3")
        - Standard navigation keywords ("back", "رجوع", "menu", "قائمة")
        - Unique substring match (e.g. "schedule", "drive")

        Returns the action dict if found, or None.
        """
        if not vote_text:
            return None

        pending = self._pending_polls.get(jid, {})
        if not pending:
            return None

        clean_vote = _clean_text(vote_text)
        if not clean_vote:
            return None

        # 1. Exact match
        if vote_text in pending:
            return pending[vote_text]

        # 2. Number index match (e.g. "1", "2", "3")
        if clean_vote.isdigit():
            idx = int(clean_vote) - 1
            keys = list(pending.keys())
            if 0 <= idx < len(keys):
                return pending[keys[idx]]

        # 3. Cleaned text exact match
        for opt, act in pending.items():
            if clean_vote == _clean_text(opt):
                return act

        # 4. Standard navigation keywords
        if clean_vote in ("back", "go back", "رجوع", "السابق", "ارجع", "خروج"):
            for opt, act in pending.items():
                if act.get("action") == "drive_back":
                    return act

        if clean_vote in ("menu", "main menu", "قائمة", "الرئيسية", "القائمة"):
            for opt, act in pending.items():
                if act.get("action") == "menu":
                    return act

        if clean_vote in ("drive", "browse drive", "browse", "درايف"):
            for opt, act in pending.items():
                if act.get("action") in ("drive_root",) or act.get("action", "").startswith("drive_folder"):
                    return act

        if clean_vote in ("schedule", "my schedule", "جدول", "الجدول"):
            for opt, act in pending.items():
                if act.get("action") == "schedule":
                    return act

        # 5. Unique substring match (if unambiguous)
        matches = [act for opt, act in pending.items() if clean_vote in _clean_text(opt)]
        if len(matches) == 1:
            return matches[0]

        return None

    # ------------------------------------------------------------------ #
    # Menu & Navigation
    # ------------------------------------------------------------------ #

    async def send_main_menu(self, jid: str) -> None:
        """Send the main interactive menu as a single-select poll."""
        options = ["📁 Browse Drive", "📅 My Schedule", "💬 Ask a Question"]

        # Register poll options → actions
        self._pending_polls[jid] = {
            "📁 Browse Drive": {"action": "drive_root"},
            "📅 My Schedule": {"action": "schedule"},
            "💬 Ask a Question": {"action": "ask"},
        }

        await self.client.send_poll(
            to_jid=jid,
            question="📋 What would you like to do? (Choose or reply 1-3)",
            options=options,
            selectable_count=1,
        )

    async def send_drive_folder(self, jid: str, folder_id: str | None = None) -> None:
        """Browse a Google Drive folder using polls for navigation."""
        data = await self.drive.explore_folder(folder_id=folder_id)
        current_id = data["folder_id"]
        folder_name = data["folder_name"]
        subfolders = data["subfolders"]
        files = data["files"]

        # Update folder stack
        stack = self._folder_stack[jid]
        if not stack or stack[-1] != current_id:
            stack.append(current_id)

        # Build text message with files (as links)
        text_parts = [f"📁 *{folder_name}*\n"]

        if files:
            text_parts.append(f"📄 *Files ({len(files)}):*")
            for i, f in enumerate(files[:20], 1):
                size = f" ({f['size_str']})" if f.get("size_str") else ""
                text_parts.append(f"{i}. 📄 {f['name']}{size}\n🔗 {f['link']}")
            if len(files) > 20:
                text_parts.append(f"... and {len(files) - 20} more files.")
            text_parts.append("")

        summary = f"📊 {len(subfolders)} folder(s), {len(files)} file(s)"
        text_parts.append(summary)

        # Send the text message with file links
        await self.client.send_text(jid, "\n".join(text_parts))

        # Build poll options for subfolders + navigation
        poll_options = []
        poll_mapping = {}

        # Add subfolders as options (truncate names for WhatsApp poll limit)
        for sf in subfolders[:(_MAX_POLL_OPTIONS - 2)]:  # Reserve 2 slots for nav
            name = sf['name'][:93]  # Truncate — leaves room for "📁 " prefix
            option_text = f"📁 {name}"
            poll_options.append(option_text)
            poll_mapping[option_text] = {
                "action": f"drive_folder:{sf['id']}",
            }

        # Always add both navigation options to guarantee ≥ 2 poll options
        if len(stack) > 1:
            poll_options.append("⬅️ Back")
            poll_mapping["⬅️ Back"] = {"action": "drive_back"}

        poll_options.append("📋 Main Menu")
        poll_mapping["📋 Main Menu"] = {"action": "menu"}

        # Safety net: if only 1 option (e.g. lost stack after restart + no subfolders),
        # prepend a "⬅️ Back to Root" so the poll is valid (min 2 options)
        if len(poll_options) < 2:
            poll_options.insert(0, "⬅️ Back to Root")
            poll_mapping["⬅️ Back to Root"] = {"action": "drive_root"}

        # Store mapping for this user
        self._pending_polls[jid] = poll_mapping

        poll_question = (
            f"📍 {folder_name} — Navigate:"
            if not subfolders
            else f"📂 {folder_name} — Select a folder:"
        )

        try:
            await self.client.send_poll(
                to_jid=jid,
                question=poll_question,
                options=poll_options,
                selectable_count=1,
            )
        except Exception as e:
            logger.warning("Poll send failed for folder '%s', falling back to text menu: %s", folder_name, e)
            # Fallback: send options as a numbered text list
            lines = ["📂 *Navigate:*\n"]
            for i, opt in enumerate(poll_options, 1):
                lines.append(f"{i}. {opt}")
            lines.append("\n_Reply with a number to choose._")
            await self.client.send_text(jid, "\n".join(lines))

    # ------------------------------------------------------------------ #
    # Navigation helpers
    # ------------------------------------------------------------------ #

    async def _go_back(self, jid: str) -> None:
        """Navigate back to the parent folder."""
        stack = self._folder_stack[jid]
        if len(stack) > 1:
            stack.pop()  # Remove current
            parent_id = stack.pop()  # Get parent (re-pushed by send_drive_folder)
            await self.send_drive_folder(jid, folder_id=parent_id)
        else:
            stack.clear()
            await self.send_drive_folder(jid, folder_id=None)

    async def _send_file_link(self, jid: str, file_name: str, file_link: str) -> None:
        """Send a file's Google Drive link."""
        clean_name = file_name.lstrip("📄 ").strip() if file_name else "File"
        await self.client.send_text(
            jid,
            f"📄 *{clean_name}*\n\n"
            f"🔗 {file_link}\n\n"
            f"Tap the link to view/download from Google Drive.",
        )

    async def _send_schedule(self, jid: str) -> None:
        """Fetch and send the upcoming schedule, then show nav poll."""
        try:
            events = await self.calendar.get_upcoming_events(days=30)
            result = self.calendar.format_schedule(events)
            await self.client.send_text(jid, result)
        except Exception as e:
            logger.error("Schedule fetch failed: %s", e)
            await self.client.send_text(jid, f"⚠️ Could not fetch schedule: {e}")

        # Nav poll after schedule
        nav_options = ["📁 Browse Drive", "📋 Main Menu"]
        self._pending_polls[jid] = {
            "📁 Browse Drive": {"action": "drive_root"},
            "📋 Main Menu": {"action": "menu"},
        }
        await self.client.send_poll(
            to_jid=jid,
            question="What's next?",
            options=nav_options,
            selectable_count=1,
        )


# ------------------------------------------------------------------ #
# Webhook response extraction helper
# ------------------------------------------------------------------ #


def extract_interactive_response(message_obj: dict, data: dict | None = None) -> dict | None:
    """Extract poll vote from an incoming Evolution API webhook message.

    Handles poll update messages from Evolution API v2:
    - message.pollUpdateMessage
    - message.pollVoteMessage
    - data.pollUpdates

    Returns:
        {"type": "poll", "id": "", "text": str} or None
    """
    if not isinstance(message_obj, dict):
        message_obj = {}

    poll_update = message_obj.get("pollUpdateMessage") or message_obj.get("pollVoteMessage")
    if not poll_update and data and isinstance(data, dict):
        updates = data.get("pollUpdates")
        if updates and isinstance(updates, list) and len(updates) > 0:
            poll_update = updates[0]

    if poll_update and isinstance(poll_update, dict):
        vote = poll_update.get("vote") or {}
        selected = vote.get("selectedOptions") or poll_update.get("selectedOptions") or []
        if selected:
            first = selected[0]
            if isinstance(first, str):
                name = first
            elif isinstance(first, dict):
                name = first.get("name") or first.get("optionName") or first.get("text") or ""
            else:
                name = ""
            if name:
                return {
                    "type": "poll",
                    "id": "",
                    "text": name,
                }

    return None

