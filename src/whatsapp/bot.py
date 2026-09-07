"""
WhatsApp Bot Handler — processes incoming messages and routes to the AI agent.

This module receives parsed webhook payloads from the FastAPI endpoint,
applies humanized typing delays to reduce ban risk, and sends responses
back through the Evolution API.

The bot distinguishes between:
- Direct messages (1:1 chat) → routed to the AI agent for Q&A
- Group messages → forwarded to the group listener for schedule parsing
"""

import asyncio
import logging
import random

from src.whatsapp.evolution_client import EvolutionClient

logger = logging.getLogger(__name__)


class WhatsAppBot:
    """Handles incoming WhatsApp messages and dispatches responses."""

    def __init__(
        self,
        evolution_client: EvolutionClient | None = None,
        announcement_group_jid: str | None = None,
    ):
        self.client = evolution_client or EvolutionClient()
        self.announcement_group_jid = announcement_group_jid
        # Callbacks for processing messages (will be set by the app)
        self._on_direct_message = None
        self._on_group_message = None

    def on_direct_message(self, handler):
        """Register a handler for direct (1:1) messages."""
        self._on_direct_message = handler
        return handler

    def on_group_message(self, handler):
        """Register a handler for group messages."""
        self._on_group_message = handler
        return handler

    async def handle_webhook(self, payload: dict) -> None:
        """
        Process an incoming Evolution API webhook payload.

        Expected payload structure (MESSAGES_UPSERT event):
        {
            "event": "messages.upsert",
            "instance": "college-bot",
            "data": {
                "key": {
                    "remoteJid": "201234567890@s.whatsapp.net",
                    "fromMe": false,
                    "id": "..."
                },
                "message": {
                    "conversation": "What is photosynthesis?",
                    // or "imageMessage": { ... }
                    // or "extendedTextMessage": { "text": "..." }
                },
                "messageType": "conversation",
                "pushName": "Ahmed"
            }
        }
        """
        event = payload.get("event", "")

        # Only process incoming messages
        if event != "messages.upsert":
            logger.debug("Ignoring event: %s", event)
            return

        data = payload.get("data", {})
        key = data.get("key", {})
        remote_jid = key.get("remoteJid", "")
        from_me = key.get("fromMe", True)

        # Skip messages sent by the bot itself
        if from_me:
            return

        # Skip status broadcasts
        if remote_jid == "status@broadcast":
            return

        # Extract message text
        message_obj = data.get("message", {})
        text = self._extract_text(message_obj)
        message_type = data.get("messageType", "unknown")
        sender_name = data.get("pushName", "Unknown")

        # Extract media if present
        media_info = self._extract_media_info(data, message_obj, message_type)
        if media_info and not media_info.get("base64"):
            try:
                b64 = await self.client.get_base64_from_media(data)
                if b64:
                    media_info["base64"] = b64
            except Exception as e:
                logger.error("Failed to fetch media base64: %s", e)

        parsed = {
            "jid": remote_jid,
            "sender_name": sender_name,
            "text": text,
            "message_type": message_type,
            "media": media_info,
            "raw": data,
        }

        logger.info(
            "Message from %s (%s): %s [type=%s]",
            sender_name,
            remote_jid[:20],
            text[:80] if text else "(media)",
            message_type,
        )

        # Route to appropriate handler
        is_group = remote_jid.endswith("@g.us")

        if is_group:
            if self._on_group_message:
                response = await self._on_group_message(parsed)
                if response:
                    await self.send_reply(remote_jid, response)
        else:
            if self._on_direct_message:
                response = await self._on_direct_message(parsed)
                if response:
                    await self.send_reply(remote_jid, response)

    async def send_reply(self, to_jid: str, response: str | dict) -> None:
        """
        Send a reply with humanized typing delay to reduce ban risk.

        Args:
            to_jid: Recipient JID
            response: Either a string (text reply) or a dict with keys:
                      {"text": "...", "image_base64": "...", "image_caption": "..."}
        """
        # Simulate typing (1.5 - 3.5 seconds, proportional to response length)
        try:
            await self.client.send_presence(to_jid, "composing")
        except Exception:
            pass  # Non-critical if presence fails

        try:
            if isinstance(response, str):
                # Scale delay with message length (min 1.5s, max 4s)
                delay = min(1.5 + len(response) * 0.005, 4.0)
                delay += random.uniform(-0.3, 0.5)  # Add jitter
                await asyncio.sleep(max(delay, 1.0))

                await self.client.send_text(to_jid, response)

            elif isinstance(response, dict):
                text = response.get("text", "")
                image_b64 = response.get("image_base64")
                image_caption = response.get("image_caption", "")
                doc_path = response.get("document_path")

                if text:
                    delay = min(1.5 + len(text) * 0.005, 4.0) + random.uniform(-0.3, 0.5)
                    await asyncio.sleep(max(delay, 1.0))
                    await self.client.send_text(to_jid, text)

                if image_b64:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    await self.client.send_image(to_jid, image_b64, caption=image_caption)

                if doc_path:
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                    await self.client.send_document(to_jid, doc_path)
        except Exception as send_err:
            logger.error("Failed to send reply to %s: %s", to_jid, send_err)

        # Pause typing indicator
        try:
            await self.client.send_presence(to_jid, "paused")
        except Exception:
            pass

    def _extract_text(self, message_obj: dict) -> str:
        """Extract text from various WhatsApp message formats."""
        # Simple text message
        if "conversation" in message_obj:
            return message_obj["conversation"]

        # Extended text (quoted replies, links, etc.)
        if "extendedTextMessage" in message_obj:
            return message_obj["extendedTextMessage"].get("text", "")

        # Image with caption
        if "imageMessage" in message_obj:
            return message_obj["imageMessage"].get("caption", "")

        # Document with caption
        if "documentMessage" in message_obj:
            return message_obj["documentMessage"].get("caption", "")

        # Audio/video don't have text
        return ""

    def _extract_media_info(self, data: dict, message_obj: dict, message_type: str) -> dict | None:
        """Extract media metadata if the message contains media."""
        media_types = {
            "imageMessage": "image",
            "audioMessage": "audio",
            "videoMessage": "video",
            "documentMessage": "document",
        }

        for key, media_type in media_types.items():
            if key in message_obj:
                media = message_obj[key] if isinstance(message_obj[key], dict) else {}
                b64 = (
                    media.get("base64")
                    or data.get("base64")
                    or (data.get("media", {}) if isinstance(data.get("media"), dict) else {}).get("base64")
                    or ""
                )
                return {
                    "type": media_type,
                    "mimetype": media.get("mimetype", "image/jpeg"),
                    "caption": media.get("caption", ""),
                    "filename": media.get("fileName", ""),
                    "base64": b64,
                }

        # Check if type is media but not in expected keys
        direct_b64 = data.get("base64") or (data.get("media", {}) if isinstance(data.get("media"), dict) else {}).get("base64")
        if direct_b64 and ("image" in message_type.lower() or "media" in message_type.lower()):
            return {
                "type": "image",
                "mimetype": "image/jpeg",
                "caption": "",
                "filename": "",
                "base64": direct_b64,
            }

        return None
