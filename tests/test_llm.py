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
    _compute_processor_placement,
    _extract_ollama_metrics,
    _observe_ollama_processor,
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


def test_compute_processor_placement():
    """Verify processor placement calculation across GPU, CPU, and mixed splits."""
    assert _compute_processor_placement(None, None) is None
    assert _compute_processor_placement(0, 0) is None
    assert _compute_processor_placement(0, 1000) == "100% GPU"
    assert _compute_processor_placement(6_000_000_000, 0) == "100% CPU"
    assert _compute_processor_placement(6_000_000_000, None) == "100% CPU"
    assert _compute_processor_placement(6_000_000_000, 6_000_000_000) == "100% GPU"
    assert _compute_processor_placement(6_000_000_000, 7_000_000_000) == "100% GPU"
    assert _compute_processor_placement(10_000, 5_000) == "50% GPU / 50% CPU"
    assert _compute_processor_placement(10_000, 7_500) == "75% GPU / 25% CPU"
    assert _compute_processor_placement(10_000, 3_333) == "33% GPU / 67% CPU"


@pytest.mark.asyncio
async def test_observe_ollama_processor():
    """Verify _observe_ollama_processor extracts processor placement via API and CLI fallback."""
    settings = PilotSettings.from_env()

    # 1. API returns 100% GPU
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.ps = AsyncMock(return_value={
            "models": [{
                "model": settings.model,
                "size": 6_000_000_000,
                "size_vram": 6_000_000_000,
            }]
        })
        mock_client_cls.return_value = mock_client

        info = await _observe_ollama_processor(settings)
        assert info["processor"] == "100% GPU"
        assert info["size"] == 6_000_000_000
        assert info["size_vram"] == 6_000_000_000

    # 2. API returns 100% CPU
    with patch("ollama.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.ps = AsyncMock(return_value={
            "models": [{
                "model": settings.model,
                "size": 6_000_000_000,
                "size_vram": 0,
            }]
        })
        mock_client_cls.return_value = mock_client

        info = await _observe_ollama_processor(settings)
        assert info["processor"] == "100% CPU"
        assert info["size"] == 6_000_000_000
        assert info["size_vram"] == 0

    # 3. API fails, CLI fallback succeeds with 100% GPU
    cli_output = "NAME        ID              SIZE      PROCESSOR    CONTEXT    RUNNER\nqwen3:8b    500a1f067a9f    6.7 GB    100% GPU     8192       llamacpp\n"
    with patch("ollama.AsyncClient") as mock_client_cls, \
         patch("subprocess.run") as mock_subproc:
        mock_client = AsyncMock()
        mock_client.ps = AsyncMock(side_effect=Exception("API connection refused"))
        mock_client_cls.return_value = mock_client

        mock_proc = mock_subproc.return_value
        mock_proc.returncode = 0
        mock_proc.stdout = cli_output

        info = await _observe_ollama_processor(settings)
        assert info["processor"] == "100% GPU"
        assert info["raw_ps"] == cli_output.strip()

    # 4. API fails, CLI fallback parses mixed processor format
    cli_output_mixed = "NAME        ID              SIZE      PROCESSOR    CONTEXT    RUNNER\nqwen3:8b    500a1f067a9f    6.7 GB    40%/60% CPU/GPU     8192       llamacpp\n"
    with patch("ollama.AsyncClient") as mock_client_cls, \
         patch("subprocess.run") as mock_subproc:
        mock_client = AsyncMock()
        mock_client.ps = AsyncMock(side_effect=Exception("API connection refused"))
        mock_client_cls.return_value = mock_client

        mock_proc = mock_subproc.return_value
        mock_proc.returncode = 0
        mock_proc.stdout = cli_output_mixed

        info = await _observe_ollama_processor(settings)
        assert info["processor"] == "40%/60% CPU/GPU"


@pytest.mark.asyncio
async def test_run_pilot_warm_request_failure_fails_feasibility_gate(tmp_path: Path):
    """Verify run_pilot fails feasibility_gate if any warm request times out or fails (R8-001)."""
    settings = PilotSettings.from_env()
    output_path = tmp_path / "phase0_warm_fail.json"

    warm_call_count = 0

    async def mock_complete(*args, **kwargs):
        nonlocal warm_call_count
        messages = kwargs.get("messages") or args[0]
        content = str(messages)

        # Warm requests have the specific probe question
        if "What is the status of order O-001 with gross INR 80,000?" in content:
            warm_call_count += 1
            if warm_call_count == 3:
                # 3rd warm probe times out
                raise LLMError("READ_TIMEOUT", "Model request timed out after 60.0 seconds")
            decision = Decision(action="tools", calls=[{"action": "inspect_order", "order_id": "O-001"}])
            metrics = {"total_duration": 1_000_000_000, "eval_count": 10, "prompt_eval_count": 50}
            if kwargs.get("include_metadata"):
                return decision, metrics
            return decision

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

        # 5 warm attempts recorded
        warm_reqs = report["performance_probes"]["warm_requests"]
        assert len(warm_reqs) == 5
        assert warm_reqs[0]["status"] == "COMPLETED"
        assert warm_reqs[1]["status"] == "COMPLETED"
        assert warm_reqs[2]["status"] == "TIMEOUT"
        assert warm_reqs[2]["error_code"] == "READ_TIMEOUT"
        assert warm_reqs[3]["status"] == "COMPLETED"
        assert warm_reqs[4]["status"] == "COMPLETED"

        # Gate must FAIL because not all warm probes succeeded
        assert report["feasibility_gate"] == "FAIL"
