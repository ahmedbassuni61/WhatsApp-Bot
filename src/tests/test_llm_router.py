"""
Unit tests for LLMRouter.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from src.agents.llm_router import LLMRouter, extract_text_from_content


def test_extract_text_from_content():
    """Verify string and block extraction."""
    # Plain string
    assert extract_text_from_content("hello world") == "hello world"

    # List of blocks
    blocks = [
        {"type": "text", "text": "Part 1 "},
        {"type": "text", "text": "Part 2"},
    ]
    assert extract_text_from_content(blocks) == "Part 1 Part 2"

    # None / empty
    assert extract_text_from_content(None) == ""
    assert extract_text_from_content("") == ""


def test_router_model_setup():
    """Verify get_models respects environment keys and image presence."""
    with patch.dict("os.environ", {"GEMINI_API_KEY": "fake_gemini", "GROQ_API_KEY": "fake_groq"}):
        router = LLMRouter()
        # With image: only Gemini models
        models_with_img = router.get_models(has_image=True)
        assert len(models_with_img) == 4
        assert all("Gemini" in label for label, _ in models_with_img)

        # Without image: Gemini + Groq models
        models_no_img = router.get_models(has_image=False)
        assert len(models_no_img) == 6


def test_router_fallback_on_429():
    """Verify that router fails over to the next model when a 429 quota error occurs and blacklists it."""
    router = LLMRouter(quota_cooldown_seconds=300.0)

    # Mock model 1 (fails with 429)
    mock_model1 = MagicMock()
    mock_model1.ainvoke = AsyncMock(side_effect=Exception("429 ResourceExhausted quota exceeded"))

    # Mock model 2 (succeeds)
    mock_model2 = MagicMock()
    mock_model2.ainvoke = AsyncMock(return_value=AIMessage(content="Success from fallback!"))

    router._gemini_models = [
        ("Model 1", mock_model1),
        ("Model 2", mock_model2),
    ]

    messages = [HumanMessage(content="test")]
    response, elapsed_ms = asyncio.run(router.invoke_agent(messages))

    assert response.content == "Success from fallback!"
    assert mock_model1.ainvoke.called
    assert mock_model2.ainvoke.called

    # On next call, Model 1 should be skipped immediately without calling ainvoke again
    mock_model1.ainvoke.reset_mock()
    mock_model2.ainvoke.reset_mock()

    response2, _ = asyncio.run(router.invoke_agent(messages))
    assert response2.content == "Success from fallback!"
    assert not mock_model1.ainvoke.called  # Skipped instantly due to cooldown!
    assert mock_model2.ainvoke.called


def test_router_generate():
    """Verify generate produces cleaned text."""
    router = LLMRouter()
    mock_resp = AIMessage(content="Generated response text")
    router.invoke_agent = AsyncMock(return_value=(mock_resp, 150.0))

    result = asyncio.run(router.generate("hello"))
    assert result == "Generated response text"
    assert router.invoke_agent.called


if __name__ == "__main__":
    test_extract_text_from_content()
    test_router_model_setup()
    test_router_fallback_on_429()
    test_router_generate()
    print("All LLMRouter tests passed successfully!")
