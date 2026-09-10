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
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from src.agents.llm_router import extract_text_from_content, llm_router, optimize_and_encode_image
from src.agents.memory import memory
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
You are an intelligent autonomous college assistant WhatsApp bot that helps students manage \
their academic schedule and answer study questions.

Available tools:
• view_schedule        — show upcoming calendar events from Google Calendar
• add_calendar_event   — add an exam / lecture / lab / deadline / announcement date to Google Calendar
• delete_calendar_event— delete or cancel calendar events from Google Calendar
• parse_timetable_image— parse a timetable or schedule image and sync to calendar

RULES:
1. ALWAYS use tools for calendar events — never answer schedule questions from memory.
2. For schedule queries (show / what's next / my schedule) → view_schedule.
3. For add / remind / set deadline / save exam date → add_calendar_event.
4. For delete / remove / cancel / امسح / احذف → ALWAYS use delete_calendar_event. NEVER call view_schedule for a delete request. If vague like "delete this", call delete_calendar_event with query='all'.
5. ANNOUNCEMENTS & IMAGES WITH DATES/DEADLINES: When a student sends an image or text containing an announcement, exam date, lecture schedule, or deadline, YOU MUST CALL a calendar tool (`add_calendar_event` or `parse_timetable_image`) to add it to Google Calendar! After tool execution, confirm to the student what you added and summarize the announcement.
6. MULTI-STEP AGENT: You can call multiple tools in sequence (e.g. view schedule first, then delete or add events) to complete complex user requests.
7. For purely academic questions, study help, explanations, general questions, and greetings → ANSWER DIRECTLY in your text response (do not invoke tools unless calendar action is needed).
8. Support both Arabic and English naturally. Match the language of the user's message.
9. When adding events, calculate correct dates using the CURRENT TIME below.
10. For relative times (e.g. "next hour", "tomorrow", "كمان ساعة", "بكرة") compute the exact date/time.

{time_context}"""


# ------------------------------------------------------------------ #
# Public API
# ------------------------------------------------------------------ #


async def process_message(text: str, image: Any | None = None, user_id: str = "") -> str:
    """Route a student's message through the LLM agent with tool-calling.

    Args:
        text:    Message text (may be empty if image-only).
        image:   Optional *PIL.Image* for multimodal messages.
        user_id: JID of the sender (used for conversation memory).

    Returns:
        A formatted response string ready to send back via WhatsApp.
    """
    t0 = time.monotonic()
    text_preview = (text[:100] + "…") if text and len(text) > 100 else text

    logger.info("┌─ AGENT START ─────────────────────────────────────")
    logger.info("│ Input text : '%s'", text_preview or "(none)")
    logger.info("│ Has image  : %s", image is not None)
    logger.info("│ User ID    : %s", user_id[:25] if user_id else "(none)")

    # Store image so tools can access it
    if image:
        set_current_image(image)

    try:
        result = await _run_agent(text, image, user_id)
        elapsed = time.monotonic() - t0
        result_preview = (result[:150] + "…") if len(result) > 150 else result
        logger.info("│ Final response (%d chars, %.1fs): '%s'", len(result), elapsed, result_preview)
        logger.info("└─ AGENT END ───────────────────────────────────────")

        # Save conversation turn to memory
        if user_id:
            user_content = text or "(image)"
            await memory.add_message(user_id, "user", user_content)
            await memory.add_message(user_id, "assistant", result)

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


async def _run_agent(text: str, image: Any | None, user_id: str = "") -> str:
    """Build the model, send the message, execute any tool calls in an agentic loop."""
    # ---- messages ------------------------------------------------ #
    time_context = time_tool.get_time_context_prompt()
    system = SystemMessage(content=SYSTEM_PROMPT.format(time_context=time_context))

    if image:
        img_url = optimize_and_encode_image(image)
        content = [
            {"type": "text", "text": text or "Please analyze this image and take appropriate calendar actions if needed."},
            {"type": "image_url", "image_url": {"url": img_url}},
        ]
        human = HumanMessage(content=content)
        logger.info("│ Sending    : text + compressed image (%d chars)", len(img_url))
    else:
        human = HumanMessage(content=text)
        logger.info("│ Sending    : text only")

    # ---- inject conversation history ----------------------------- #
    history_msgs = []
    if user_id:
        history = memory.get_history(user_id)
        for entry in history:
            if entry["role"] == "user":
                history_msgs.append(HumanMessage(content=entry["content"]))
            else:
                history_msgs.append(AIMessage(content=entry["content"]))
        if history_msgs:
            logger.info("│ Memory     : %d past messages for %s", len(history_msgs), user_id[:25])

    messages: list[BaseMessage] = [system, *history_msgs, human]
    max_iterations = 5
    executed_results: list[str] = []

    for iteration in range(1, max_iterations + 1):
        logger.info("│ 🔄 AGENT LOOP iteration %d/%d", iteration, max_iterations)

        response, llm_ms = await llm_router.invoke_agent(
            messages=messages,
            tools=ALL_TOOLS,
            has_image=(image is not None),
        )

        tool_calls = getattr(response, "tool_calls", None) or []
        tool_names = [tc["name"] for tc in tool_calls] if tool_calls else []

        if tool_names:
            logger.info("│ LLM decided (%.0fms): TOOL CALL → %s", llm_ms, tool_names)
        elif response.content:
            logger.info("│ LLM decided (%.0fms): DIRECT RESPONSE (%d chars)", llm_ms, len(str(response.content)))
        else:
            logger.info("│ LLM decided (%.0fms): EMPTY RESPONSE", llm_ms)

        # ---- case 1: no tool calls -> final response from model ---- #
        if not tool_calls:
            final_text = extract_text_from_content(response.content)
            if final_text:
                return final_text
            if executed_results:
                return "\n\n".join(executed_results)
            return "I'm not sure how to help with that. Try asking a question or managing your schedule! 📚"

        # ---- case 2: tool calls requested -> execute & feed back ---- #
        messages.append(response)

        for i, tc in enumerate(tool_calls, 1):
            name = tc["name"]
            args = tc.get("args", {})
            call_id = tc.get("id") or f"call_{iteration}_{i}"

            logger.info("│")
            logger.info("│ 🛠️  TOOL CALL [%d/%d] : %s", i, len(tool_calls), name.upper())
            logger.info("│ 📦 ARGUMENTS : %s", args)

            tool_fn = TOOL_MAP.get(name)
            if not tool_fn:
                logger.error("│ ❌ UNKNOWN TOOL: %s", name)
                res_str = f"⚠️ Unknown action: {name}"
            else:
                try:
                    t_tool = time.monotonic()
                    res_raw = await tool_fn.ainvoke(args)
                    tool_ms = (time.monotonic() - t_tool) * 1000
                    res_str = str(res_raw)
                    preview = (res_str[:120] + "…") if len(res_str) > 120 else res_str
                    logger.info("│ ✅ RESULT (%.0fms): %s", tool_ms, preview)
                    logger.info("│")
                except Exception as tool_err:
                    logger.error("│ ❌ FAILED: %s", tool_err)
                    res_str = f"⚠️ {name} error: {tool_err}"

            executed_results.append(res_str)
            messages.append(ToolMessage(content=res_str, tool_call_id=call_id))

    if executed_results:
        return "\n\n".join(executed_results)
    return "I completed the requested actions. 📚"


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

