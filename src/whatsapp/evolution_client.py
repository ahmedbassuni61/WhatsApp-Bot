"""
Evolution API Client — Python wrapper for the Evolution API REST endpoints.

Evolution API is a self-hosted Docker container that wraps Baileys (WhatsApp Web
via WebSocket, no Chromium) into a clean REST API with webhook support.

Usage:
    client = EvolutionClient()
    await client.send_text("5511999999999@s.whatsapp.net", "Hello!")
    await client.send_image("5511999999999@s.whatsapp.net", image_base64, "Check this out")
"""

import base64
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
import os

load_dotenv()

logger = logging.getLogger(__name__)


class EvolutionClient:
    """Async HTTP client for Evolution API REST endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        instance_name: str | None = None,
    ):
        self.base_url = (base_url or os.getenv("EVOLUTION_API_URL", "http://localhost:8080")).rstrip("/")
        self.api_key = api_key or os.getenv("EVOLUTION_API_KEY", "")
        self.instance_name = instance_name or os.getenv("EVOLUTION_INSTANCE_NAME", "college-bot")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Content-Type": "application/json",
                "apikey": self.api_key,
            },
            timeout=30.0,
        )

    async def close(self):
        """Close the HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Instance Management
    # ------------------------------------------------------------------ #

    async def create_instance(self) -> dict:
        """Create a new WhatsApp instance (first-time setup)."""
        payload = {
            "instanceName": self.instance_name,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
        }
        resp = await self._client.post("/instance/create", json=payload)
        resp.raise_for_status()
        data = resp.json()
        logger.info("Instance '%s' created. Scan the QR code to connect.", self.instance_name)
        return data
    async def reset_instance(self) -> dict:
        """Reset and recreate the WhatsApp instance for a fresh session."""
        try:
            await self._client.delete(f"/instance/delete/{self.instance_name}")
        except Exception:
            pass
        return await self.create_instance()
    async def get_connection_state(self) -> dict:
        """Check if the WhatsApp instance is connected."""
        resp = await self._client.get(f"/instance/connectionState/{self.instance_name}")
        resp.raise_for_status()
        return resp.json()

    async def get_qr_code(self) -> dict:
        """Get the QR code for pairing (base64 image)."""
        resp = await self._client.get(f"/instance/connect/{self.instance_name}")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    # Messaging
    # ------------------------------------------------------------------ #

    async def send_text(self, to_jid: str, text: str) -> dict:
        """
        Send a text message to a user or group.

        Args:
            to_jid: WhatsApp JID (e.g. "201234567890@s.whatsapp.net" for users,
                    "120363012345678@g.us" for groups)
            text: Message text content
        """
        payload = {
            "number": to_jid,
            "text": text,
        }
        resp = await self._client.post(
            f"/message/sendText/{self.instance_name}",
            json=payload,
        )
        resp.raise_for_status()
        logger.info("Text message sent to %s", to_jid)
        return resp.json()

    async def send_image(
        self,
        to_jid: str,
        image_base64: str,
        caption: str = "",
        mimetype: str = "image/png",
    ) -> dict:
        """
        Send an image message with optional caption.

        Args:
            to_jid: WhatsApp JID
            image_base64: Base64-encoded image data
            caption: Optional image caption
            mimetype: Image MIME type (default: image/png)
        """
        payload = {
            "number": to_jid,
            "mediatype": "image",
            "mimetype": mimetype,
            "caption": caption,
            "media": image_base64,
        }
        resp = await self._client.post(
            f"/message/sendMedia/{self.instance_name}",
            json=payload,
        )
        resp.raise_for_status()
        logger.info("Image sent to %s", to_jid)
        return resp.json()

    async def send_document(
        self,
        to_jid: str,
        file_path: str,
        filename: str = "",
        caption: str = "",
    ) -> dict:
        """
        Send a document (PDF, etc.) as an attachment.

        Args:
            to_jid: WhatsApp JID
            file_path: Local path to the file
            filename: Display name for the file
            caption: Optional caption
        """
        path = Path(file_path)
        if not filename:
            filename = path.name

        with open(path, "rb") as f:
            file_b64 = base64.b64encode(f.read()).decode("utf-8")

        # Guess mimetype
        suffix = path.suffix.lower()
        mimetype_map = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }
        mimetype = mimetype_map.get(suffix, "application/octet-stream")

        payload = {
            "number": to_jid,
            "mediatype": "document",
            "mimetype": mimetype,
            "caption": caption,
            "media": file_b64,
            "fileName": filename,
        }
        resp = await self._client.post(
            f"/message/sendMedia/{self.instance_name}",
            json=payload,
        )
        resp.raise_for_status()
        logger.info("Document '%s' sent to %s", filename, to_jid)
        return resp.json()

    # ------------------------------------------------------------------ #
    # Presence (typing indicators — reduces ban risk)
    # ------------------------------------------------------------------ #

    async def send_presence(self, to_jid: str, presence: str = "composing", delay_ms: int = 1200) -> dict:
        """
        Send a presence update (typing indicator) to simulate human behavior.

        Args:
            to_jid: WhatsApp JID
            presence: "composing" (typing), "recording" (voice), or "paused"
            delay_ms: Duration to show presence in milliseconds
        """
        payload = {
            "number": to_jid,
            "presence": presence,
            "delay": delay_ms,
        }
        resp = await self._client.post(
            f"/chat/sendPresence/{self.instance_name}",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    # Groups
    # ------------------------------------------------------------------ #

    async def fetch_all_groups(self, get_participants: bool = False) -> list[dict]:
        """Fetch all WhatsApp groups the bot is part of."""
        resp = await self._client.get(
            f"/group/fetchAllGroups/{self.instance_name}",
            params={"getParticipants": str(get_participants).lower()},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_group_info(self, group_jid: str) -> dict:
        """Get metadata for a specific group."""
        resp = await self._client.get(
            f"/group/findGroupInfos/{self.instance_name}",
            params={"groupJid": group_jid},
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------ #
    # Webhook Configuration
    # ------------------------------------------------------------------ #

    async def set_webhook(self, webhook_url: str, events: list[str] | None = None) -> dict:
        """
        Configure Evolution API to forward events to a webhook URL.

        Args:
            webhook_url: The URL to forward events to (e.g. "http://localhost:8000/webhook")
            events: List of events to listen for. Defaults to messages + connection updates.
        """
        if events is None:
            events = [
                "MESSAGES_UPSERT",
                "CONNECTION_UPDATE",
            ]

        payload = {
            "webhook": {
                "enabled": True,
                "url": webhook_url,
                "byEvents": False,
                "base64": True,
                "events": events,
            }
        }
        resp = await self._client.post(
            f"/webhook/set/{self.instance_name}",
            json=payload,
        )
        resp.raise_for_status()
        logger.info("Webhook configured: %s → %s", events, webhook_url)
        return resp.json()

    async def get_base64_from_media(self, message_dict: dict) -> str | None:
        """Fetch decrypted base64 media for a message from Evolution API."""
        try:
            resp = await self._client.post(
                f"/chat/getBase64FromMediaMessage/{self.instance_name}",
                json={"message": message_dict, "convertToMp4": False},
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                b64 = data.get("base64")
                logger.info("Successfully fetched media base64 (%d chars)", len(b64) if b64 else 0)
                return b64
            else:
                logger.warning("getBase64FromMediaMessage returned status %d: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("Failed to fetch media base64: %s", e)
        return None
