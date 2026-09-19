"""
LangGraph Agent Graph — replaces the manual agentic loop with a StateGraph.

The graph structure is:

    router ↔ executor  (tool-calling loop, same as before)
         ↓
    reflector  (adversarial end-of-loop quality gate)
         ↓
    committer  (creates pending events in Google Calendar + conflict detection)
         ↓
    responder  (formats structured final output)

The reflector runs at the END of the agent's work — after the LLM has finished
all tool calls and produced a final text response.  It re-examines everything
(the original question, tool outputs, extracted events, and the image if present)
and actively hunts for problems.  If it finds issues, it loops back to the
router with correction instructions.  This continues until the reflector passes
or the safety cap (5 reflections) is hit.
"""

import json
import logging
import time
from datetime import datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from src.agents.conflict_resolver import detect_conflicts, format_conflict_warnings
from src.agents.reflector import run_reflection

logger = logging.getLogger(__name__)

# Standard universal emoji mapping for academic schedule events
EVENT_TYPE_EMOJI = {
    "exam": "📝",
    "lab": "🔬",
    "lecture": "📚",
    "section": "👥",
    "deadline": "⏰",
    "office_hours": "💬",
    "event": "📌",
    "other": "📌",
}

# Tools that produce events requiring reflection
_REFLECTION_TOOLS = {"parse_timetable_image", "add_calendar_event"}


# ------------------------------------------------------------------ #
# State
# ------------------------------------------------------------------ #


class AgentState(TypedDict):
    """Typed state flowing through the LangGraph agent graph."""

    messages: Annotated[list[BaseMessage], add_messages]
    user_id: str
    image: Any | None
    iteration: int                   # agent loop counter (max 8)
    reflection_count: int            # reflection retries (safety cap 5)
    pending_events: list[dict]       # extracted events NOT yet committed
    committed_events: list[dict]     # events after commit (with calendar IDs)
    conflicts: list[dict]            # detected overlaps
    reflection_verdict: str          # "pass" | "fail"
    needs_reflection: bool           # set by executor when calendar/timetable tools ran
    final_response: str              # the answer to send back


# ------------------------------------------------------------------ #
# Module-level dependencies (set once at startup via init_graph)
# ------------------------------------------------------------------ #
_llm_router = None
_calendar_sync = None
_tool_map: dict = {}
_all_tools: list = []


def _get_llm_router():
    """Dynamically get llm_router with fallback to agent/tools module."""
    try:
        import src.agents.agent as agent_mod
        router = getattr(agent_mod, "llm_router", None)
        if router is not None:
            return router
    except Exception:
        pass
    if _llm_router is not None:
        return _llm_router
    from src.agents.llm_router import llm_router
    return llm_router


def _get_tool_map():
    """Dynamically get tool map with fallback to agent/tools module."""
    try:
        import src.agents.agent as agent_mod
        tmap = getattr(agent_mod, "TOOL_MAP", None)
        if tmap:
            return tmap
    except Exception:
        pass
    if _tool_map:
        return _tool_map
    from src.agents.tools import ALL_TOOLS
    return {t.name: t for t in ALL_TOOLS}


def _get_all_tools():
    """Dynamically get all tools with fallback to agent/tools module."""
    try:
        import src.agents.agent as agent_mod
        tools_list = getattr(agent_mod, "ALL_TOOLS", None)
        if tools_list:
            return tools_list
    except Exception:
        pass
    if _all_tools:
        return _all_tools
    from src.agents.tools import ALL_TOOLS
    return ALL_TOOLS


def init_graph(*, llm_router, calendar_sync, tool_map: dict, all_tools: list):
    """Wire runtime dependencies.  Called once during app startup."""
    global _llm_router, _calendar_sync, _tool_map, _all_tools
    _llm_router = llm_router
    _calendar_sync = calendar_sync
    _tool_map = tool_map
    _all_tools = all_tools
    logger.info("LangGraph agent graph dependencies initialized")


# ------------------------------------------------------------------ #
# Nodes
# ------------------------------------------------------------------ #


async def router_node(state: AgentState) -> dict:
    """Call the LLM with the current message history and tool definitions.

    If the LLM decides to call tools → routes to executor.
    If the LLM produces a final text answer → routes to reflector.
    """
    from src.agents.llm_router import extract_text_from_content

    iteration = state.get("iteration", 0) + 1
    logger.info("│ 🔄 GRAPH router_node — iteration %d/8", iteration)

    if iteration > 8:
        logger.warning("│ Max iterations (8) reached, forcing end")
        return {
            "iteration": iteration,
            "final_response": state.get("final_response", "I completed the requested actions. 📚"),
        }

    router = _get_llm_router()
    tools = _get_all_tools()
    response, llm_ms = await router.invoke_agent(
        messages=state["messages"],
        tools=tools,
        has_image=(state.get("image") is not None),
    )

    tool_calls = getattr(response, "tool_calls", None) or []

    if tool_calls:
        tool_names = [tc["name"] for tc in tool_calls]
        logger.info("│ LLM decided (%.0fms): TOOL CALL → %s", llm_ms, tool_names)
    elif response.content:
        logger.info("│ LLM decided (%.0fms): FINAL RESPONSE (%d chars)", llm_ms, len(str(response.content)))
    else:
        logger.info("│ LLM decided (%.0fms): EMPTY RESPONSE", llm_ms)

    update: dict = {
        "messages": [response],
        "iteration": iteration,
    }

    # If no tool calls, capture the final text for the reflector
    if not tool_calls:
        final_text = extract_text_from_content(response.content)
        update["final_response"] = final_text or state.get("final_response", "")

    return update


async def executor_node(state: AgentState) -> dict:
    """Execute tool calls from the last AIMessage and feed results back.

    For `parse_timetable_image`: captures extracted events into pending_events
    instead of letting them be committed directly.
    """
    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []

    if not tool_calls:
        return {}

    new_messages: list[BaseMessage] = []
    executed_results: list[str] = []
    pending_events: list[dict] = list(state.get("pending_events") or [])
    needs_reflection = state.get("needs_reflection", False)

    for i, tc in enumerate(tool_calls, 1):
        name = tc["name"]
        args = tc.get("args", {})
        call_id = tc.get("id") or f"call_{state.get('iteration', 0)}_{i}"

        logger.info("│ 🛠️  TOOL [%d/%d]: %s — args: %s", i, len(tool_calls), name.upper(), args)

        tool_fn = _get_tool_map().get(name)
        if not tool_fn:
            logger.error("│ ❌ UNKNOWN TOOL: %s", name)
            res_str = f"⚠️ Unknown action: {name}"
        else:
            try:
                t0 = time.monotonic()
                res_raw = await tool_fn.ainvoke(args)
                tool_ms = (time.monotonic() - t0) * 1000
                res_str = str(res_raw)
                preview = (res_str[:120] + "…") if len(res_str) > 120 else res_str
                logger.info("│ ✅ RESULT (%.0fms): %s", tool_ms, preview)
            except Exception as tool_err:
                logger.error("│ ❌ FAILED: %s", tool_err)
                res_str = f"⚠️ {name} error: {tool_err}"

        # If this is a timetable parse, capture the pending events
        if name == "parse_timetable_image":
            needs_reflection = True
            try:
                parsed = json.loads(res_str)
                if isinstance(parsed, dict) and (parsed.get("status") in ("success", "pending_review") or "events" in parsed):
                    extracted = parsed.get("events", [])
                    if extracted:
                        pending_events = extracted
                    logger.info("│ 📋 Captured %d pending events for reflection", len(pending_events))
            except (json.JSONDecodeError, TypeError):
                pass  # Not JSON — tool returned a plain error message

        # Single event additions also need reflection and capture into pending_events
        if name == "add_calendar_event":
            needs_reflection = True
            if isinstance(args, dict) and args.get("title") and args.get("date"):
                existing_sigs = {(e.get("title"), e.get("date"), e.get("time_start")) for e in pending_events}
                sig = (args.get("title"), args.get("date"), args.get("time_start") or args.get("time"))
                if sig not in existing_sigs:
                    pending_events.append({
                        "title": args["title"],
                        "date": args["date"],
                        "time_start": args.get("time_start") or args.get("time"),
                        "time_end": args.get("time_end"),
                        "location": args.get("location"),
                        "event_type": args.get("event_type", args.get("type", "other")),
                        "_action_result": "created" if ("Added to Google Calendar" in res_str or "already on your calendar" in res_str) else "duplicate",
                    })
                    logger.info("│ 📋 Captured add_calendar_event '%s' into pending_events", args["title"])

        executed_results.append(res_str)
        new_messages.append(ToolMessage(content=res_str, tool_call_id=call_id))

    return {
        "messages": new_messages,
        "pending_events": pending_events,
        "needs_reflection": needs_reflection,
    }


async def reflector_node(state: AgentState) -> dict:
    """Adversarial quality gate — runs at the end of the agent loop.

    Re-examines the full conversation (original question, tool outputs,
    extracted events, image) and actively hunts for problems.

    For simple Q&A (no tools / no events), the reflector does a quick pass.
    For timetable extractions, it re-examines the image and cross-checks each event.
    """
    reflection_count = state.get("reflection_count", 0) + 1
    logger.info("│ 🔍 REFLECTOR — pass %d (max 5)", reflection_count)

    # Skip reflection for basic non-calendar tasks without images or pending events
    if not state.get("needs_reflection") and not state.get("image") and not state.get("pending_events"):
        logger.info("│ ⏩ REFLECTOR — skipped for basic task / schedule view")
        return {
            "reflection_count": reflection_count,
            "reflection_verdict": "pass",
        }

    # Safety cap
    if reflection_count > 5:
        logger.warning("│ Reflection safety cap reached (5), proceeding with best effort")
        return {
            "reflection_count": reflection_count,
            "reflection_verdict": "pass",
        }

    try:
        router = _get_llm_router()
        result = await run_reflection(state, router)
    except Exception as e:
        logger.error("│ Reflector crashed: %s — failing open", e, exc_info=True)
        return {
            "reflection_count": reflection_count,
            "reflection_verdict": "pass",
        }

    verdict = result.get("verdict", "pass")
    logger.info("│ 🔍 Reflection verdict: %s", verdict)

    update: dict = {
        "reflection_count": reflection_count,
        "reflection_verdict": verdict,
    }

    if verdict == "fail":
        problems = result.get("problems", [])
        corrections = result.get("correction_instructions", "Please fix the issues found.")
        logger.info("│ ❌ Problems found: %s", problems)

        # Determine if issues are fixable in-place (duplicates, bad times)
        # or require full re-extraction (missing rows, cross-column errors)
        pending = list(state.get("pending_events") or [])
        needs_reextraction = any(
            "missing" in p.lower() or "cross-column" in p.lower() or "row" in p.lower()
            for p in problems
        )

        if pending and not needs_reextraction:
            # Fix in-place: deduplicate and remove events with bad dates/times
            seen = set()
            fixed_events = []
            for ev in pending:
                sig = (ev.get("title"), ev.get("date"), ev.get("time_start"))
                if sig in seen:
                    logger.info("│ 🔧 Auto-removed duplicate: %s", ev.get("title"))
                    continue
                seen.add(sig)

                # Skip events with time_start >= time_end
                ts, te = ev.get("time_start"), ev.get("time_end")
                if ts and te and ts >= te:
                    # Swap them as auto-fix
                    ev["time_start"], ev["time_end"] = te, ts
                    logger.info("│ 🔧 Auto-swapped times for: %s", ev.get("title"))

                fixed_events.append(ev)

            update["pending_events"] = fixed_events
            update["reflection_verdict"] = "pass"  # Fixed in-place, proceed to commit
            logger.info("│ ✅ Auto-fixed %d → %d events, proceeding to commit", len(pending), len(fixed_events))
        else:
            # Needs full re-extraction — inject correction with explicit tool call instruction
            # KEEP pending events in state so they are not lost if re-extraction fails to run
            problem_text = "\n".join(f"- {p}" for p in problems)
            correction_msg = HumanMessage(content=(
                f"REFLECTION FEEDBACK — Problems were found in your previous extraction:\n\n"
                f"{problem_text}\n\n"
                f"CORRECTION INSTRUCTIONS:\n{corrections}\n\n"
                f"You MUST call `parse_timetable_image` again to re-extract the events from the image. "
                f"Do NOT answer from memory — use the tool."
            ))
            update["messages"] = [correction_msg]
            logger.info("│ 🔄 Needs re-extraction, looping back to router (keeping existing %d events as fallback)", len(pending))

    return update


async def commit_node(state: AgentState) -> dict:
    """Commit pending events to Google Calendar and detect conflicts.

    Only runs when reflection has passed.  Creates ALL events even if
    conflicts are detected — conflicts produce warnings, not blocks.
    """
    pending = state.get("pending_events") or []
    if not pending:
        logger.info("│ 📝 commit_node — no pending events to commit")
        return {"committed_events": [], "conflicts": []}

    # Resolve calendar_sync with fallback to tools module
    cal_sync = _calendar_sync
    if cal_sync is None:
        import src.agents.tools as tools_mod
        cal_sync = tools_mod._calendar_sync

    if cal_sync is None:
        logger.error("│ ❌ commit_node — calendar_sync service is not initialized!")
        return {"committed_events": [], "conflicts": []}

    logger.info("│ 📝 commit_node — committing %d events", len(pending))

    # Fetch existing events for conflict detection
    existing_events = []
    try:
        # Get events for the date range covered by pending events
        dates = [ev.get("date") for ev in pending if ev.get("date")]
        if dates:
            min_date = min(dates)
            max_date = max(dates)
            min_dt = datetime.fromisoformat(min_date)
            max_dt = datetime.fromisoformat(max_date)
            days_span = max((max_dt - min_dt).days + 7, 14)
            existing_events = await cal_sync.get_upcoming_events(days=days_span)
    except Exception as e:
        logger.warning("│ Could not fetch existing events for conflict check: %s", e)

    # Detect conflicts (new vs existing + new vs new)
    conflicts = detect_conflicts(pending, existing_events)
    if conflicts:
        logger.info("│ ⚠️ %d conflict(s) detected", len(conflicts))

    # Create all events regardless of conflicts
    committed = []
    for ev in pending:
        # If the event was already created during an add_calendar_event call, preserve it
        if ev.get("_action_result"):
            committed.append(ev)
            continue

        if ev.get("action") == "cancel":
            try:
                deleted = await cal_sync.delete_events(
                    query=ev.get("title", ""), date_str=ev.get("date")
                )
                committed.append({**ev, "_action_result": "deleted", "_deleted": deleted})
            except Exception as e:
                logger.error("│ Failed to delete event '%s': %s", ev.get("title"), e)
                committed.append({**ev, "_action_result": "error", "_error": str(e)})
        else:
            try:
                created = await cal_sync.create_event(ev)
                if created:
                    committed.append({**ev, "_action_result": "created", "_calendar_id": created.get("id")})
                else:
                    committed.append({**ev, "_action_result": "duplicate"})
            except Exception as e:
                logger.error("│ Failed to create event '%s': %s", ev.get("title"), e)
                committed.append({**ev, "_action_result": "error", "_error": str(e)})

    logger.info("│ ✅ Committed %d events (%d conflicts)", len(committed), len(conflicts))

    return {
        "committed_events": committed,
        "conflicts": conflicts,
    }


async def respond_node(state: AgentState) -> dict:
    """Format the final structured response for the user.

    If events were processed (created or existing duplicates): ENFORCES a clean,
    grouped-by-date summary with emoji type indicators, times, and conflict warnings.

    If no events: passes through the LLM's text response unchanged.
    """
    committed = state.get("committed_events") or state.get("pending_events") or []
    conflicts = state.get("conflicts") or []
    base_response = state.get("final_response", "")

    # No events processed at all — return the LLM's text response as-is
    if not committed:
        if base_response:
            return {"final_response": base_response}
        return {"final_response": "I completed the requested actions. 📚"}

    # Separate created, deleted, duplicates, errors
    created = [ev for ev in committed if ev.get("_action_result") == "created"]
    deleted = [ev for ev in committed if ev.get("_action_result") == "deleted"]
    duplicates = [ev for ev in committed if ev.get("_action_result") == "duplicate"]
    errors = [ev for ev in committed if ev.get("_action_result") == "error"]

    active_events = created + duplicates
    lines: list[str] = []

    # ENFORCE structured date-by-date output whenever active events exist
    if active_events:
        if created and duplicates:
            lines.append(f"📅 *تم مزامنة الجدول مع Google Calendar* (تمت إضافة {len(created)} موعد جديد، و{len(duplicates)} مضاف مسبقاً):\n")
        elif created:
            lines.append(f"📅 *تمت إضافة {len(created)} موعد إلى Google Calendar بنجاح*:\n")
        else:
            lines.append(f"📅 *جدولك الحالي المسجل على Google Calendar* ({len(duplicates)} موعد):\n")

        # Group by date
        by_date: dict[str, list[dict]] = {}
        for ev in active_events:
            d = ev.get("date", "Unknown")
            by_date.setdefault(d, []).append(ev)

        day_names_map = {
            "Saturday": "السبت (Saturday)",
            "Sunday": "الأحد (Sunday)",
            "Monday": "الإثنين (Monday)",
            "Tuesday": "الثلاثاء (Tuesday)",
            "Wednesday": "الأربعاء (Wednesday)",
            "Thursday": "الخميس (Thursday)",
            "Friday": "الجمعة (Friday)",
        }

        for date_str in sorted(by_date.keys()):
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                eng_day = dt.strftime("%A")
                day_display = day_names_map.get(eng_day, eng_day)
                formatted_date = f"{day_display}, {dt.strftime('%b %d')}"
            except (ValueError, TypeError):
                formatted_date = date_str

            lines.append(f"📅 *{formatted_date}*")
            # Sort chronologically by start time
            day_events = sorted(by_date[date_str], key=lambda x: x.get("time_start") or "00:00")
            for ev in day_events:
                etype = ev.get("event_type", "other")
                emoji = EVENT_TYPE_EMOJI.get(etype, "⚪")
                tag = f"[{etype.upper()}]"
                title = ev.get("title", "Untitled")

                ts = ev.get("time_start")
                te = ev.get("time_end")
                if ts and te:
                    time_str = f"{ts} → {te}"
                elif ts:
                    time_str = f"{ts}"
                else:
                    time_str = "All Day"

                loc = ev.get("location")
                loc_str = f" | 📍 {loc}" if loc else ""

                lines.append(f"  • {emoji} {tag} *{title}* — 🕐 {time_str}{loc_str}")
            lines.append("")  # blank line between date groups

    if deleted:
        del_names = [ev.get("title", "Untitled") for ev in deleted]
        lines.append(f"🗑️ تم الإلغاء: {', '.join(del_names)}\n")

    if errors:
        err_names = [f"{ev.get('title', 'Untitled')} ({ev.get('_error', '?')})" for ev in errors]
        lines.append(f"⚠️ تعذر الحفظ: {', '.join(err_names)}\n")

    if active_events:
        lines.append("🔔 تم ضبط التنبيهات تلقائياً: قبل الموعد بساعة و15 دقيقة.")

    # Place conflict warnings at the very end of addition if found
    conflict_text = format_conflict_warnings(conflicts)
    if conflict_text:
        lines.append(conflict_text)

    final = "\n".join(lines).strip()
    return {"final_response": final}


# ------------------------------------------------------------------ #
# Edge routing
# ------------------------------------------------------------------ #


def _after_router(state: AgentState) -> str:
    """Route after router_node: tools → executor, no tools → reflector."""
    # Check if max iterations exceeded
    if state.get("iteration", 0) > 8:
        return "reflector"

    last_msg = state["messages"][-1]
    tool_calls = getattr(last_msg, "tool_calls", None) or []

    if tool_calls:
        return "executor"
    return "reflector"


def _after_reflector(state: AgentState) -> str:
    """Route after reflector_node: pass → committer, fail → router (retry)."""
    verdict = state.get("reflection_verdict", "pass")
    reflection_count = state.get("reflection_count", 0)

    if verdict == "pass" or reflection_count >= 5:
        return "committer"
    return "router"


# ------------------------------------------------------------------ #
# Graph builder
# ------------------------------------------------------------------ #


def build_agent_graph() -> Any:
    """Build and compile the LangGraph agent graph.

    Returns a compiled graph ready for `await graph.ainvoke(state)`.
    """
    graph = StateGraph(AgentState)

    # Add nodes
    graph.add_node("router", router_node)
    graph.add_node("executor", executor_node)
    graph.add_node("reflector", reflector_node)
    graph.add_node("committer", commit_node)
    graph.add_node("responder", respond_node)

    # Set entry point
    graph.set_entry_point("router")

    # Add edges
    graph.add_conditional_edges("router", _after_router, {
        "executor": "executor",
        "reflector": "reflector",
    })
    graph.add_edge("executor", "router")  # always loop back for next LLM turn
    graph.add_conditional_edges("reflector", _after_reflector, {
        "committer": "committer",
        "router": "router",  # reflection failed → retry
    })
    graph.add_edge("committer", "responder")
    graph.add_edge("responder", END)

    compiled = graph.compile()
    logger.info("LangGraph agent graph compiled successfully")
    return compiled
