"""
Multi-LLM Router with automatic quota fallback across free providers.

Providers supported:
1. Google Gemini (gemini-2.5-flash-lite, gemini-flash-latest, gemini-2.5-flash)
2. Groq (qwen/qwen3.8-27b, allam-2-7b)
"""

import io
import logging
import os
from typing import Any

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
]

GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "qwen/qwen3.8-27b",
    "allam-2-7b",
]


class LLMRouter:
    """Routes requests to the best available free LLM with automatic failover."""

    def __init__(self):
        self.gemini_key = os.getenv("GEMINI_API_KEY", "")
        self.groq_key = os.getenv("GROQ_API_KEY", "")

    async def generate(self, prompt: str, image: Any | None = None) -> str:
        """
        Generate a text response for a prompt (and optional image).

        Automatically falls back through available models if quota is exhausted.
        """
        # 1. Try Gemini models (supports multimodal vision & text)
        if self.gemini_key:
            import google.generativeai as genai
            genai.configure(api_key=self.gemini_key)

            for model_name in GEMINI_MODELS:
                try:
                    model = genai.GenerativeModel(model_name)
                    content_parts = [prompt]
                    if image is not None:
                        content_parts.append(image)

                    resp = model.generate_content(content_parts)
                    if resp and resp.text:
                        return resp.text.strip()
                except Exception as e:
                    err_str = str(e)
                    if "429" in err_str or "quota" in err_str.lower() or "ResourceExhausted" in type(e).__name__:
                        logger.warning("Gemini model '%s' quota exceeded, failing over...", model_name)
                    else:
                        logger.warning("Gemini model '%s' failed: %s, failing over...", model_name, err_str[:120])
                    continue

        # 2. If text-only (no image), fallback to Groq
        if image is None and self.groq_key:
            try:
                from groq import Groq
                client = Groq(api_key=self.groq_key)
                for groq_model in GROQ_MODELS:
                    try:
                        chat = client.chat.completions.create(
                            model=groq_model,
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0.7,
                        )
                        if chat.choices and chat.choices[0].message.content:
                            logger.info("Answer generated via Groq fallback (%s)", groq_model)
                            return chat.choices[0].message.content.strip()
                    except Exception as g_err:
                        logger.warning("Groq model '%s' failed: %s", groq_model, g_err)
                        continue
            except Exception as import_err:
                logger.error("Failed to initialize Groq client: %s", import_err)

        raise RuntimeError("All LLM providers and fallbacks exhausted. Please check your API keys.")


# Global instance
llm_router = LLMRouter()
