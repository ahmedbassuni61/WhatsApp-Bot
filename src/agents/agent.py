"""
LangChain Agent — routes student messages to the right tool via LLM tool-calling.

Uses a LangGraph StateGraph with an adversarial reflection loop to validate
responses before they reach the user.  The graph structure is:

    router ↔ executor  (tool-calling loop)
         ↓
    reflector  (end-of-loop quality gate that hunts for problems)
         ↓
    committer  (creates pending events in Google Calendar + conflict detection)
         ↓
    responder  (structured final output with times and conflict warnings)
"""

import logging
import time
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.agents.graph import build_agent_graph, init_graph
from src.agents.llm_router import llm_router, optimize_and_encode_image
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
• list_drive_folder    — view Google Drive folder contents (subfolders & files) or enter a folder needed
• search_drive         — search Google Drive by keywords, course names, or file titles

RULES:
1. ALWAYS use tools for calendar events — never answer schedule questions from memory.
2. For schedule queries (show / what's next / my schedule) → view_schedule.
3. For add / remind / set deadline / save exam date → add_calendar_event (always pass the student's original message text in the `description` parameter).
4. For delete / remove / cancel / امسح / احذف → ALWAYS use delete_calendar_event. NEVER call view_schedule for a delete request. If vague like "delete this", call delete_calendar_event with query='all'.
5. TIMETABLE IMAGES & SCHEDULE ANNOUNCEMENTS:
   • When a student sends a timetable or class schedule image: YOU MUST CALL `parse_timetable_image()`. The system automatically extracts, reflects, and syncs all events to Google Calendar.
   • NEVER call `add_calendar_event()` individually for events extracted by `parse_timetable_image()`! Once `parse_timetable_image()` is called, all schedule events are queued and synced automatically.
   • When a student sends a single date, deadline, or announcement in text, call `add_calendar_event()`.
6. GOOGLE DRIVE EXPLORATION & STUDY MATERIALS:
   • To see what subjects, courses, or semesters exist: ALWAYS call `list_drive_folder()` (with no args) to see the root directory.
   • To enter a subject or subfolder: call `list_drive_folder(folder_id=...)` using the folder ID or `list_drive_folder(folder_name=...)`.
   • To count lectures, sections, or study files in a subject (e.g. "how many lectures in [subject]?", "كام محاضرة في [مادة]؟"):
     1) Enter that subject's folder with `list_drive_folder(folder_name=...)` or ID.
     2) Enter the Lectures / Sections / Labs folder to inspect and count the files.
     3) If lectures are divided into parts (e.g. Part I, Part II), you can enter those subfolders to count all lecture files.
   • To find specific exams, slides, or files by name: call `search_drive(query=...)`.
   • NEVER make up Google Drive links, file names, or counts. Always quote the accurate links and information returned by the tools.
7. MULTI-STEP AGENTIC LOOP: You can call tools repeatedly in sequence (e.g. browse folder -> enter subfolder -> get files -> answer) until your task is completely finished.
8. For purely academic questions, study help, concept explanations, and greetings → ANSWER DIRECTLY in your text response (do not invoke tools unless calendar or drive action is needed).
9. Support both Arabic and English naturally. Match the language of the user's message.
10. When adding events, calculate correct dates using the CURRENT TIME below.
11. For relative times (e.g. "next hour", "tomorrow", "كمان ساعة", "بكرة") compute the exact date/time.

{time_context}"""


# ------------------------------------------------------------------ #
# Compiled LangGraph graph (lazy singleton)
# ------------------------------------------------------------------ #
_graph = None
_graph_initialized = False


def _ensure_graph_deps(calendar_sync=None):
    """Ensure graph dependencies are wired.  Called lazily on first use."""
    global _graph_initialized
    if _graph_initialized:
        return
    init_graph(
        llm_router=llm_router,
        calendar_sync=calendar_sync,
        tool_map=TOOL_MAP,
        all_tools=ALL_TOOLS,
    )
    _graph_initialized = True


def _get_graph():
    """Build or return the compiled LangGraph agent graph."""
    global _graph
    if _graph is None:
        _graph = build_agent_graph()
    return _graph


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
    """Build the initial state and invoke the LangGraph agent graph."""
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

    messages = [system, *history_msgs, human]

    # ---- invoke the LangGraph agent graph ----------------------- #
    graph = _get_graph()
    initial_state = {
        "messages": messages,
        "user_id": user_id,
        "image": image,
        "iteration": 0,
        "reflection_count": 0,
        "pending_events": [],
        "committed_events": [],
        "conflicts": [],
        "reflection_verdict": "pass",
        "needs_reflection": False,
        "final_response": "",
    }

    result = await graph.ainvoke(initial_state)
    final = result.get("final_response", "")

    if final:
        return final

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


