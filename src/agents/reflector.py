import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

logger = logging.getLogger(__name__)

REFLECTOR_PROMPT = """You are an adversarial validation engine. Your ONLY job is to find problems in the assistant's response before it reaches the user.
Assume there ARE errors and actively hunt for them.
You succeed when you catch a mistake. You fail when you let a bad answer through.

Here is the context:
User Message: {user_message}
Assistant Response: {assistant_response}
Tool History: {tool_history}
{image_section}
{events_section}

Check these things:
1. Did it actually answer the question? Did it use a tool when it should have, or answer from memory when it shouldn't?
2. TIMETABLE IMAGE & TIME COLUMN ACCURACY:
   - Carefully read the column headers from the image (times: e.g. 8:30-9:30, 9:30-10:30, ..., 19:30-20:30).
   - Read the row headers (days of the week: السبت, الأحد, الإثنين, الثلاثاء, الأربعاء, الخميس).
   - Verify each subject's start and end times correspond to the EXACT column header above that cell (NO cross-column contamination).
   - Back-to-back classes (e.g. 11:30-12:30 followed by 12:30-13:30) are SEPARATE slots, NOT conflicts!
   - College timetables are designed without overlapping classes for the same student cohort. If you or the assistant think there is a conflict, inspect the image columns closely to see if the classes are actually in separate columns or days.
   - Verify that all rows/cells containing classes were extracted without missing any.
3. If calendar events were created: verify dates and times match user specification, verify event type is correct.
4. General: language match, no fabricated info.

Your response format MUST be JSON:
{{"verdict": "fail", "problems": ["..."], "correction_instructions": "..."}}
OR
{{"verdict": "pass"}}

Remember: you are TRYING to find problems. Look harder. Check twice.
"""

def clean_and_deduplicate_events(events: list[dict]) -> tuple[list[dict], list[str]]:
    """Clean, auto-fix, and deduplicate events.
    
    Returns:
        tuple of (cleaned_events, remaining_problems)
    """
    cleaned = []
    seen = set()
    problems = []
    now = datetime.now(tz=ZoneInfo("Africa/Cairo"))
    max_date = now + timedelta(days=365)
    
    for event in events:
        if not isinstance(event, dict):
            continue
        ev = dict(event)
        title = (ev.get("title") or "Untitled").strip()
        date_str = (ev.get("date") or "").strip()
        time_start = (ev.get("time_start") or "").strip() or None
        time_end = (ev.get("time_end") or "").strip() or None
        
        if not date_str:
            problems.append(f"Event '{title}' is missing a date.")
            continue
            
        try:
            event_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Africa/Cairo"))
        except ValueError:
            problems.append(f"Event '{title}' has invalid date format: {date_str}. Must be YYYY-MM-DD.")
            continue
            
        if event_date > max_date:
            problems.append(f"Event '{title}' is scheduled more than 365 days in the future.")
            continue
            
        # Check time format and auto-fix
        t_start = None
        t_end = None
        
        if time_start:
            try:
                t_start = datetime.strptime(time_start, "%H:%M")
                ev["time_start"] = t_start.strftime("%H:%M")
            except ValueError:
                problems.append(f"Event '{title}' has invalid time_start format: {time_start}. Must be HH:MM.")
                continue
                
        if time_end:
            try:
                t_end = datetime.strptime(time_end, "%H:%M")
                ev["time_end"] = t_end.strftime("%H:%M")
            except ValueError:
                # Default end time to 1 hour after start
                if t_start:
                    t_end = t_start + timedelta(hours=1)
                    ev["time_end"] = t_end.strftime("%H:%M")
                else:
                    ev["time_end"] = None
                    
        # If start >= end, auto-fix by adding 1 hour to start
        if t_start and t_end and t_start >= t_end:
            t_end = t_start + timedelta(hours=1)
            ev["time_end"] = t_end.strftime("%H:%M")
            
        # Deduplication
        sig = (title.lower(), date_str, ev.get("time_start"))
        if sig in seen:
            logger.info("Auto-deduplicated identical event: '%s' on %s at %s", title, date_str, ev.get("time_start"))
            continue
        seen.add(sig)
        cleaned.append(ev)
        
    return cleaned, problems


def deterministic_checks(events: list[dict]) -> list[str]:
    """Perform fast deterministic validation on a list of events."""
    _, problems = clean_and_deduplicate_events(events)
    return problems


async def run_reflection(state: dict, llm_router) -> dict:
    """
    Run adversarial reflection on the assistant's response.
    
    Args:
        state: Agent state dictionary containing 'messages', 'image', 'pending_events', 'reflection_count'.
        llm_router: An instance capable of calling the LLM via generate(prompt, image=...).
        
    Returns:
        A dictionary containing the reflection results ('verdict', 'problems', 'correction_instructions').
    """
    try:
        messages = state.get("messages", [])
        image = state.get("image")
        pending_events = state.get("pending_events", [])
        reflection_count = state.get("reflection_count", 0)
        
        if pending_events:
            cleaned_events, problems = clean_and_deduplicate_events(pending_events)
            state["pending_events"] = cleaned_events
            pending_events = cleaned_events
            if problems:
                return {
                    "verdict": "fail",
                    "problems": problems,
                    "correction_instructions": "Fix the following deterministic errors:\n- " + "\n- ".join(problems)
                }
                
        user_message_text = ""
        # Find the last HumanMessage that isn't a reflection correction.
        # Assuming correction instructions might be injected as specific human messages,
        # we try to get the actual user request.
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                content_str = msg.content if isinstance(msg.content, str) else str(msg.content)
                # Avoid matching automated reflection correction messages if they contain specific keywords
                if "correction instructions" not in content_str.lower() and "reflection" not in content_str.lower():
                    user_message_text = content_str
                    break
        
        if not user_message_text:
            # Fallback to the last human message regardless
            for msg in reversed(messages):
                if isinstance(msg, HumanMessage):
                    user_message_text = msg.content if isinstance(msg.content, str) else str(msg.content)
                    break
                    
        assistant_response = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                assistant_response = msg.content if isinstance(msg.content, str) else str(msg.content)
                break
                
        tool_history = []
        for msg in messages:
            if isinstance(msg, ToolMessage):
                tool_history.append(msg.content if isinstance(msg.content, str) else str(msg.content))
                
        tool_history_text = "\n".join(tool_history) if tool_history else "No tools used."
        
        image_section = ""
        if image and pending_events:
            image_section = "An image was provided. Re-examine it to verify the extracted events."
            
        events_section = ""
        if pending_events:
            events_section = "Pending Events:\n" + json.dumps(pending_events, indent=2)
            
        prompt = REFLECTOR_PROMPT.format(
            user_message=user_message_text,
            assistant_response=assistant_response,
            tool_history=tool_history_text,
            image_section=image_section,
            events_section=events_section
        )
        
        response_text = await llm_router.generate(prompt, image=image)
        
        # Parse JSON from response
        match = re.search(r"\{[\s\S]*\}", response_text)
        if match:
            clean_text = match.group(0)
        else:
            clean_text = re.sub(r"^```(?:json)?\s*", "", response_text.strip(), flags=re.IGNORECASE)
            clean_text = re.sub(r"\s*```$", "", clean_text).strip()
        
        try:
            result = json.loads(clean_text)
            if isinstance(result, dict) and "verdict" in result:
                return result
            logger.warning("Reflector returned JSON without 'verdict' key: %s", result)
            return {"verdict": "pass"}
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse reflector JSON response: {e}. Raw response: {response_text[:300]}")
            return {"verdict": "pass"}
            
    except Exception as e:
        logger.warning(f"Reflection engine exception: {e}. Failing open.")
        return {"verdict": "pass"}
