"""Focused unit tests for LLM client options, metrics extraction, and pilot probe accounting."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from moneytrail.agent import count_input_tokens, get_tokenizer
from moneytrail.config import (
    MODEL_OUTPUT_ALLOWANCE_TOKENS,
    OLLAMA_CONTEXT_TOKENS,
    PilotSettings,
    Settings,
)
from moneytrail.llm import (
    LLMError,
    _extract_ollama_metrics,
    complete_structured,
    run_pilot,
)
from moneytrail.schemas import Decision


class SimpleResponse(BaseModel):
    message: str
    count: int


@pytest.mark.asyncio
async def test_complete_structured_enforces_ollama_serving_limits():
    """Verify complete_structured sets num_ctx=8192 and num_predict=1024 in request options."""
    settings = PilotSettings()
    fake_response = {
        "message": {"content": '{"message": "ok", "count": 1}'},
        "total_duration": 1_000_000_000,
        "eval_count": 10,
        "eval_duration": 500_000_000,
    }

    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=fake_response)
        mock_client_cls.return_value = mock_client

        resp = await complete_structured(
            messages=[{"role": "user", "content": "hello"}],
            response_type=SimpleResponse,
            settings=settings,
        )

        assert resp.message == "ok"
        assert resp.count == 1

        mock_client.chat.assert_called_once()
        call_kwargs = mock_client.chat.call_args.kwargs
        options = call_kwargs["options"]
        assert options["num_ctx"] == OLLAMA_CONTEXT_TOKENS
        assert options["num_ctx"] == 8192
        assert options["num_predict"] == MODEL_OUTPUT_ALLOWANCE_TOKENS
        assert options["num_predict"] == 1024
        assert options["temperature"] == 0.0
        assert options["repeat_penalty"] == 1.1


@pytest.mark.asyncio
async def test_complete_structured_extracts_ollama_metrics():
    """Verify _extract_ollama_metrics and include_metadata return correct metrics and token rates."""
    fake_response = {
        "message": {"content": '{"message": "ok", "count": 2}'},
        "total_duration": 2_000_000_000,
        "load_duration": 100_000_000,
        "prompt_eval_count": 200,
        "prompt_eval_duration": 500_000_000,
        "eval_count": 50,
        "eval_duration": 1_000_000_000,
    }

    metrics = _extract_ollama_metrics(fake_response)
    assert metrics["prompt_eval_count"] == 200
    assert metrics["eval_count"] == 50
    assert metrics["eval_tokens_per_second"] == 50.0
    assert metrics["prompt_tokens_per_second"] == 400.0

    settings = PilotSettings()
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(return_value=fake_response)
        mock_client_cls.return_value = mock_client

        parsed, meta = await complete_structured(
            messages=[{"role": "user", "content": "test"}],
            response_type=SimpleResponse,
            settings=settings,
            include_metadata=True,
        )
        assert parsed.count == 2
        assert meta["prompt_eval_count"] == 200
        assert meta["eval_tokens_per_second"] == 50.0


@pytest.mark.asyncio
async def test_complete_structured_classifies_distinct_errors():
    """Verify distinct error classification for timeout, context limits, OOM, and connection errors."""
    settings = PilotSettings()

    # 1. Timeout
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=asyncio.TimeoutError())
        mock_client_cls.return_value = mock_client

        with pytest.raises(LLMError) as exc_info:
            await complete_structured(
                messages=[{"role": "user", "content": "timeout"}],
                response_type=SimpleResponse,
                settings=settings,
                timeout_seconds=0.01,
            )
        assert exc_info.value.code == "READ_TIMEOUT"

    # 2. Context limit exceeded
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=Exception("Prompt context length exceeded maximum window"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(LLMError) as exc_info:
            await complete_structured(
                messages=[{"role": "user", "content": "overflow"}],
                response_type=SimpleResponse,
                settings=settings,
            )
        assert exc_info.value.code == "CONTEXT_LIMIT"

    # 3. Out of memory
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=Exception("CUDA out of memory: failed to allocate 2048MiB"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(LLMError) as exc_info:
            await complete_structured(
                messages=[{"role": "user", "content": "oom"}],
                response_type=SimpleResponse,
                settings=settings,
            )
        assert exc_info.value.code == "OUT_OF_MEMORY"

    # 4. Connection failure
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.chat = AsyncMock(side_effect=Exception("Cannot connect to host: connection refused"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(LLMError) as exc_info:
            await complete_structured(
                messages=[{"role": "user", "content": "offline"}],
                response_type=SimpleResponse,
                settings=settings,
            )
        assert exc_info.value.code == "MODEL_UNAVAILABLE"


def test_count_input_tokens_consistent_with_chat_template():
    """Verify count_input_tokens includes chat template and schema overhead."""
    settings = PilotSettings.from_env()
    tokenizer = get_tokenizer(settings)

    messages = [
        {"role": "system", "content": "You are MoneyTrail."},
        {"role": "user", "content": "Reconcile order O-001."},
    ]

    tokens_with_schema = count_input_tokens(messages, tokenizer, Decision)
    tokens_without_schema = count_input_tokens(messages, tokenizer, None)

    assert tokens_with_schema > tokens_without_schema
    # Schema overhead should be non-trivial
    assert tokens_with_schema - tokens_without_schema > 50


@pytest.mark.asyncio
async def test_run_pilot_separates_timeout_from_context_fit(tmp_path: Path):
    """Verify run_pilot preserves fits_in_8192_context=True and records TIMEOUT on READ_TIMEOUT."""
    settings = PilotSettings.from_env()
    output_path = tmp_path / "phase0_test.json"

    # Mock complete_structured: contract probes pass, warm requests pass, near-budget probe times out
    async def mock_complete(*args, **kwargs):
        messages = kwargs.get("messages") or args[0]
        # Detect near-budget probe by content size
        content = str(messages)
        if "Order transaction record O-00100" in content:
            raise LLMError("READ_TIMEOUT", "Model request timed out after 60.0 seconds")
        decision = Decision(action="tools", calls=[{"action": "inspect_order", "order_id": "O-005"}])
        metrics = {"total_duration": 1_000_000_000, "eval_count": 10, "prompt_eval_count": 50}
        if kwargs.get("include_metadata"):
            return decision, metrics
        return decision

    with patch("moneytrail.llm.complete_structured", side_effect=mock_complete), \
         patch("ollama.AsyncClient") as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.show = AsyncMock(return_value={"details": {"quantization_level": "Q4_K_M", "family": "qwen3", "parameter_size": "8.2B"}})
        mock_client.list = AsyncMock(return_value={"models": [{"name": settings.model, "digest": "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"}]})
        mock_client.ps = AsyncMock(return_value={"models": []})
        mock_client_cls.return_value = mock_client

        report = await run_pilot(output_path, settings)

        # Assert report recorded near-budget target and tokens
        assert report["performance_probes"]["near_budget_context_tokens"] == settings.input_token_budget
        # Crucial: READ_TIMEOUT must NOT mark context as not fitting
        assert report["performance_probes"]["fits_in_8192_context"] is True
        assert report["performance_probes"]["near_budget_status"] == "TIMEOUT"
        assert report["performance_probes"]["near_budget_latency_s"] is None
        # Strict gate fails because near_budget_status != "COMPLETED"
        assert report["feasibility_gate"] == "FAIL"
