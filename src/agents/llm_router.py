"""
Multi-LLM Router with automatic quota fallback across free providers.

Acts as the single unified LLM gateway for the entire application:
- Agent execution with tool-calling and failover
- Prompt and vision generation for tools, background listeners, and fallbacks
"""

import asyncio
import base64 as b64mod
import io
import logging
import os
import time
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

try:
    from langchain_groq import ChatGroq
except ImportError:
    ChatGroq = None

load_dotenv()
logger = logging.getLogger(__name__)

# Model configurations
GEMINI_MODELS = [
    ("Gemini 3.5 Flash", "gemini-3.5-flash"),
    ("Gemini 3.5 Flash Lite", "gemini-3.5-flash-lite"),
    ("Gemini 2.5 Flash", "gemini-2.5-flash"),
    ("Gemini 2.5 Flash Lite", "gemini-2.5-flash-lite"),
]

GROQ_MODELS = [
    ("Groq (Llama 3.3 70B)", "llama-3.3-70b-versatile"),
    ("Groq (Mixtral 8x7B)", "mixtral-8x7b-32768"),
]


import PIL.Image


def optimize_and_encode_image(image: Any, max_dim: int = 1024, quality: int = 85) -> str:
    """
    Downscale and compress image to a JPEG base64 data URL to minimize network payload.
    Reduces 10MB+ camera photos to ~100-150KB for fast API transmission.
    """
    if isinstance(image, str) and image.startswith("data:"):
        return image

    pil_img = None
    if isinstance(image, PIL.Image.Image):
        pil_img = image.copy()
    elif isinstance(image, bytes):
        pil_img = PIL.Image.open(io.BytesIO(image))
    elif hasattr(image, "read"):
        pil_img = PIL.Image.open(image)

    if pil_img:
        # Convert to RGB (JPEG does not support RGBA or palette modes)
        if pil_img.mode in ("RGBA", "P", "LA"):
            pil_img = pil_img.convert("RGB")

        # Downscale if larger than max_dim (preserving aspect ratio)
        if max(pil_img.size) > max_dim:
            pil_img.thumbnail((max_dim, max_dim), PIL.Image.Resampling.LANCZOS)

        buf = io.BytesIO()
        pil_img.save(buf, format="JPEG", quality=quality, optimize=True)
        b64_str = b64mod.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64_str}"

    return str(image)


def extract_text_from_content(content: Any) -> str:
    """Extract plain text string from LangChain response content."""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts).strip()
    return str(content).strip() if content else ""


class LLMRouter:
    """Single unified gateway to route requests with automatic quota failover."""

    def __init__(self, temperature: float = 0.3, quota_cooldown_seconds: float = 300.0):
        self.temperature = temperature
        self.quota_cooldown_seconds = quota_cooldown_seconds
        self.gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.groq_key = os.getenv("GROQ_API_KEY", "")
        self._gemini_models: list[tuple[str, Any]] = []
        self._groq_models: list[tuple[str, Any]] = []
        self._quota_cooldowns: dict[str, float] = {}
        self._init_models()

    def _init_models(self) -> None:
        """Pre-initialize model instances once to avoid per-request instantiation overhead."""
        self._gemini_models = []
        if self.gemini_key:
            for label, model_id in GEMINI_MODELS:
                try:
                    self._gemini_models.append((
                        label,
                        ChatGoogleGenerativeAI(
                            model=model_id,
                            google_api_key=self.gemini_key,
                            temperature=self.temperature,
                        ),
                    ))
                except Exception as init_err:
                    logger.warning("Failed to initialize %s (%s): %s", label, model_id, init_err)

        self._groq_models = []
        if self.groq_key and ChatGroq:
            for label, model_id in GROQ_MODELS:
                try:
                    self._groq_models.append((
                        label,
                        ChatGroq(
                            model=model_id,
                            groq_api_key=self.groq_key,
                            temperature=self.temperature,
                        ),
                    ))
                except Exception as init_err:
                    logger.warning("Failed to initialize %s (%s): %s", label, model_id, init_err)

    def get_models(self, has_image: bool = False) -> list[tuple[str, Any]]:
        """Return cached models in failover priority order, filtering out models on quota cooldown."""
        current_gemini = os.getenv("GEMINI_API_KEY", "")
        current_groq = os.getenv("GROQ_API_KEY", "")
        if current_gemini != self.gemini_key or current_groq != self.groq_key or (not self._gemini_models and not self._groq_models):
            self.gemini_key = current_gemini
            self.groq_key = current_groq
            self._init_models()

        all_models = list(self._gemini_models) if has_image else (list(self._gemini_models) + list(self._groq_models))

        # Filter out models currently on quota cooldown
        now = time.monotonic()
        available_models = [m for m in all_models if self._quota_cooldowns.get(m[0], 0.0) <= now]

        if available_models:
            skipped = [m[0] for m in all_models if m[0] not in [a[0] for a in available_models]]
            if skipped:
                logger.debug("│ Skipping models on quota cooldown: %s", skipped)
            return available_models

        # If all models are on cooldown, reset cooldowns and retry all as fallback
        logger.warning("│ All models are on quota cooldown! Resetting cooldowns to retry.")
        self._quota_cooldowns.clear()
        return all_models

    async def invoke_agent(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        has_image: bool = False,
        timeout: float = 25.0,
    ) -> tuple[Any, float]:
        """
        Invoke the LLM with automatic failover across models.
        """
        models = self.get_models(has_image=has_image)
        if not models:
            raise RuntimeError("No LLM API keys configured or models available.")

        response = None
        t_llm = time.monotonic()

        for name, model in models:
            try:
                logger.info("│ Trying LLM : %s...", name)
                runner = model.bind_tools(tools) if tools else model
                response = await asyncio.wait_for(runner.ainvoke(messages), timeout=timeout)
                llm_ms = (time.monotonic() - t_llm) * 1000
                logger.info("│ LLM Success: %s (%.0fms)", name, llm_ms)
                # Clear quota cooldown on success
                self._quota_cooldowns.pop(name, None)
                return response, llm_ms
            except asyncio.TimeoutError:
                logger.warning("│ ⚠️ Timeout (%.0fs) on %s — temporary 5-min cooldown", timeout, name)
                self._quota_cooldowns[name] = time.monotonic() + self.quota_cooldown_seconds
                continue
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "quota" in err_str or "resource" in err_str or "exhausted" in err_str:
                    logger.warning("│ ⚠️ Quota hit on %s — blacklisting for %.0fs: %s", name, self.quota_cooldown_seconds, str(e).split("\n")[0])
                    self._quota_cooldowns[name] = time.monotonic() + self.quota_cooldown_seconds
                else:
                    logger.error("│ ❌ Error on %s: %s", name, str(e).split("\n")[0])
                continue

        raise RuntimeError("All configured LLMs hit quota limits, timed out, or failed.")

    async def generate(self, prompt: str, image: Any | None = None) -> str:
        """
        Generate a text response for a prompt (and optional image).

        Unified one-shot generation using the same underlying failover pipeline.
        Used by tools, group listener, and agent fallback.
        """
        has_image = image is not None

        if has_image:
            img_url = optimize_and_encode_image(image)
            content = [
                {"type": "text", "text": prompt or "Please analyze this image."},
                {"type": "image_url", "image_url": {"url": img_url}},
            ]
            human = HumanMessage(content=content)
        else:
            human = HumanMessage(content=prompt)

        response, _ = await self.invoke_agent(
            messages=[human],
            tools=None,
            has_image=has_image,
        )

        return extract_text_from_content(response.content)


# Global instance
llm_router = LLMRouter()
