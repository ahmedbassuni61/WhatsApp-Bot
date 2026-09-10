"""
LangChain Agent — routes student messages to the right tool via LLM tool-calling.

Replaces the 100+ lines of manual keyword matching in ``main.py`` with a single
``process_message()`` call.  Gemini sees the student's message (text and/or image),
inspects the available tools and their Pydantic schemas, and decides which tool
to invoke.  If all providers fail, a basic text fallback is attempted.
"""

import base64 as b64mod
import io
import logging
import time
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.llm_router import extract_text_from_content, llm_router, optimize_and_encode_image
from src.agents.tools import ALL_TOOLS, clear_current_image, set_current_image
from src.tools.time_tool import time_tool

load_dotenv()

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# Name → tool lookup for executing tool calls
# ------------------------------------------------------------------ #
TOOL_MAP: dict = {t.name: t for t in ALL_TOOLS}

# ------------------------------------------------------------------ #
# System prompt injected into every agent call
# ------------------------------------------------------------------ #
SYSTEM_PROMPT = """\
You are a helpful college assistant WhatsApp bot that helps students manage \
their academic schedule and answer study questions.

Available tools:
• view_schedule        — show upcoming calendar events from Google Calendar
• add_calendar_event   — add an exam / lecture / lab / deadline to Google Calendar
• delete_calendar_event— delete or cancel calendar events from Google Calendar
• parse_timetable_image— parse a timetable or schedule image and sync to calendar

RULES:
1. ALWAYS use tools for calendar events — never answer schedule questions from memory.
2. For schedule queries → view_schedule.
3. For add / remind / set deadline → add_calendar_event.
4. For delete / remove / cancel → delete_calendar_event.
5. If the user sends an image that is a timetable / exam schedule → parse_timetable_image.
6. For ALL academic questions, study help, explanations, general questions, and greetings → ANSWER DIRECTLY in your text response (do not invoke tools).
7. If the user sends an image with a question, inspect the image and ANSWER DIRECTLY in your response.
8. Support both Arabic and English naturally.
9. When adding events, calculate correct dates using the CURRENT TIME below.
10. For relative times (e.g. "next hour", "tomorrow", "كمان ساعة", "بكرة") compute the exact date/time.

{time_context}"""


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #


async def process_message(text: str, image: Any | None = None) -> str:
    """Route a student's message through the LLM agent with tool-calling.

    Args:
        text:  Message text (may be empty if image-only).
        image: Optional *PIL.Image* for multimodal messages.

    Returns:
        A formatted response string ready to send back via WhatsApp.
    """
    t0 = time.monotonic()
    text_preview = (text[:100] + "…") if text and len(text) > 100 else text

    logger.info("┌─ AGENT START ─────────────────────────────────────")
    logger.info("│ Input text : '%s'", text_preview or "(none)")
    logger.info("│ Has image  : %s", image is not None)

    # Store image so tools can access it
    if image:
        set_current_image(image)

    try:
        result = await _run_agent(text, image)
        elapsed = time.monotonic() - t0
        result_preview = (result[:150] + "…") if len(result) > 150 else result
        logger.info("│ Final response (%d chars, %.1fs): '%s'", len(result), elapsed, result_preview)
        logger.info("└─ AGENT END ───────────────────────────────────────")
        return result
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error("│ Agent FAILED after %.1fs: %s", elapsed, e, exc_info=True)
        logger.info("│ Attempting fallback…")
        result = await _fallback(text)
        logger.info("└─ AGENT END (fallback) ────────────────────────────")
        return result
    finally:
        clear_current_image()


# ------------------------------------------------------------------ #
# Internals
# ------------------------------------------------------------------ #


async def _run_agent(text: str, image: Any | None) -> str:
    """Build the model, send the message, execute any tool calls."""
    # ---- messages ------------------------------------------------ #
    time_context = time_tool.get_time_context_prompt()
    system = SystemMessage(content=SYSTEM_PROMPT.format(time_context=time_context))

    if image:
        img_url = optimize_and_encode_image(image)
        content = [
            {"type": "text", "text": text or "Please analyze this image."},
            {"type": "image_url", "image_url": {"url": img_url}},
        ]
        human = HumanMessage(content=content)
        logger.info("│ Sending    : text + compressed image (%d chars)", len(img_url))
    else:
        human = HumanMessage(content=text)
        logger.info("│ Sending    : text only")

    messages = [system, human]

    # ---- call model with fallbacks via unified router ------------- #
    response, llm_ms = await llm_router.invoke_agent(
        messages=messages,
        tools=ALL_TOOLS,
        has_image=(image is not None),
    )

    tool_names = [tc["name"] for tc in response.tool_calls] if response.tool_calls else []

    if tool_names:
        logger.info("│ LLM decided (%.0fms): TOOL CALL → %s", llm_ms, tool_names)
    elif response.content:
        logger.info("│ LLM decided (%.0fms): DIRECT RESPONSE (%d chars)", llm_ms, len(str(response.content)))
    else:
        logger.info("│ LLM decided (%.0fms): EMPTY RESPONSE", llm_ms)

    # ---- execute tool calls -------------------------------------- #
    if response.tool_calls:
        results: list[str] = []
        for i, tc in enumerate(response.tool_calls, 1):
            name, args = tc["name"], tc["args"]
            
            # --- HIGH VISIBILITY TOOL LOGGING ---
            logger.info("│")
            logger.info("│ 🛠️  TOOL CALL [%d/%d] : %s", i, len(response.tool_calls), name.upper())
            logger.info("│ 📦 ARGUMENTS : %s", args)
            
            tool_fn = TOOL_MAP.get(name)
            if not tool_fn:
                logger.error("│ ❌ UNKNOWN TOOL: %s", name)
                results.append(f"⚠️ Unknown action: {name}")
                continue

            try:
                t_tool = time.monotonic()
                result = await tool_fn.ainvoke(args)
                tool_ms = (time.monotonic() - t_tool) * 1000
                result_str = str(result)
                result_preview = (result_str[:120] + "…") if len(result_str) > 120 else result_str
                
                logger.info("│ ✅ RESULT (%.0fms): %s", tool_ms, result_preview)
                logger.info("│")
                
                results.append(result_str)
            except Exception as tool_err:
                logger.error("│ ❌ FAILED: %s", tool_err)
                results.append(f"⚠️ {name} error: {tool_err}")

        return "\n\n".join(results)

    # ---- no tool calls — direct model response ------------------- #
    if response.content:
        final_text = extract_text_from_content(response.content)
        return final_text if final_text else "I'm not sure how to help with that. 📚"

    return "I'm not sure how to help with that. Try asking a question or managing your schedule! 📚"


async def _fallback(text: str) -> str:
    """Last-resort: plain text generation without tools."""
    try:
        prompt = (
            "You are a helpful college study assistant. "
            "Answer the following question concisely:\n\n" + (text or "Hello")
        )
        result = await llm_router.generate(prompt)
        logger.info("│ Fallback succeeded (%d chars)", len(result))
        return f"{result}"
    except Exception as err:
        logger.error("│ Fallback also FAILED: %s", err)
        return "Sorry, I'm having trouble right now. Please try again in a moment. 🔧"

