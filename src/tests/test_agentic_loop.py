"""Tests for agentic execution loop in agent.py."""

import asyncio
from unittest.mock import AsyncMock, patch
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from src.agents.agent import _run_agent


@pytest.mark.asyncio
async def test_agentic_loop_multi_step():
    """Verify agent calls tool, receives ToolMessage, and makes follow-up response."""
    # Iteration 1: Model requests view_schedule tool call
    mock_resp_1 = AIMessage(
        content="",
        tool_calls=[{"name": "view_schedule", "args": {"days": 7}, "id": "call_1"}]
    )

    # Iteration 2: Model receives schedule tool result and gives final text answer
    mock_resp_2 = AIMessage(
        content="You have Math Exam on Thursday at 10 AM. I checked your schedule! 📅"
    )

    with patch("src.agents.agent.llm_router.invoke_agent", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.side_effect = [(mock_resp_1, 100.0), (mock_resp_2, 80.0)]
        with patch("src.agents.agent.TOOL_MAP") as mock_tool_map:
            mock_tool = AsyncMock()
            mock_tool.ainvoke.return_value = "📅 Upcoming Schedule:\n• Math Exam on Thu"
            mock_tool_map.get.return_value = mock_tool

            result = await _run_agent("What is my schedule?", image=None, user_id="test_user")

            assert result == "You have Math Exam on Thursday at 10 AM. I checked your schedule! 📅"
            assert mock_invoke.call_count == 2
            mock_tool.ainvoke.assert_called_once_with({"days": 7})

            # Check messages passed to 2nd invoke call
            args_iter_2 = mock_invoke.call_args_list[1][1]["messages"]
            # System, Human, AI(tool_call), ToolMessage
            assert any(isinstance(m, ToolMessage) for m in args_iter_2)


@pytest.mark.asyncio
async def test_agentic_loop_direct_response():
    """Verify single iteration when no tools needed."""
    mock_resp = AIMessage(content="Photosynthesis is the process by which plants make food.")

    with patch("src.agents.agent.llm_router.invoke_agent", new_callable=AsyncMock) as mock_invoke:
        mock_invoke.return_value = (mock_resp, 90.0)

        result = await _run_agent("What is photosynthesis?", image=None, user_id="test_user")

        assert result == "Photosynthesis is the process by which plants make food."
        assert mock_invoke.call_count == 1
