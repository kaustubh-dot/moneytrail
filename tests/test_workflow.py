"""End-to-end investigation workflow tests: HTTP lifecycle, events, cancellation, and live Ollama runs."""

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from moneytrail.agent import run_fixed
from moneytrail.api import create_app
from moneytrail.config import Settings
from moneytrail.db import get_reader_connection, get_writer_connection
from moneytrail.ingest import import_bundle
from moneytrail.llm import LLMError
from moneytrail.schemas import (
    AnswerDraft,
    Citation,
    Claim,
    Decision,
    InvestigationRequest,
)


@pytest.mark.asyncio
async def test_offline_http_journey(test_settings: Settings):
    """
    Test complete offline HTTP journey using a test double for the LLM response
    and real PostgreSQL queries/tables.
    Validates: start -> 202 -> polling -> events -> terminal completed result.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    app = create_app(test_settings)
    auth_headers = {"Authorization": f"Bearer {test_settings.owner_token}"}

    reader_conn = await get_reader_connection(test_settings)
    try:
        async with reader_conn.cursor() as cur:
            await cur.execute("SELECT source_id FROM sources WHERE case_id = %s AND relative_name = 'orders.csv';", [case_id])
            row = await cur.fetchone()
            orders_source_id = row[0]
    finally:
        await reader_conn.close()

    # Test-only deterministic structured model decision
    test_decision = Decision(
        action="answer",
        answer=AnswerDraft(
            outcome="explained",
            claims=[
                Claim(
                    text_template="Gross sales totaled {{fact:fact_gross}} across the cohort.",
                    fact_ids=["fact_gross"],
                    citations=[
                        Citation(
                            source_id=orders_source_id,
                            locator_id="csv:r1",
                            label="Orders",
                        )
                    ],
                ),
                Claim(
                    text_template="Completed refunds of {{fact:fact_refunds}} and fees of {{fact:fact_fees}} were deducted.",
                    fact_ids=["fact_refunds", "fact_fees"],
                ),
                Claim(
                    text_template="Total received payout allocations reached {{fact:fact_received}} with zero unexplained difference.",
                    fact_ids=["fact_received", "fact_difference"],
                ),
            ],
            selected_fact_ids=["fact_gross", "fact_refunds", "fact_fees", "fact_received", "fact_difference"],
            unresolved=[],
        ),
    )

    with patch("moneytrail.agent.complete_structured", return_value=test_decision):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            # 1. Start investigation
            start_resp = await client.post(
                "/api/v1/investigations",
                headers=auth_headers,
                json={"case_id": str(case_id), "question": "Why did we receive less than gross?"},
            )
            assert start_resp.status_code == 202
            run_data = start_resp.json()
            run_id = uuid.UUID(run_data["run_id"])
            assert run_data["state"] == "running"

            # 2. Poll until completed (timeout 10s)
            for _ in range(20):
                await asyncio.sleep(0.1)
                poll_resp = await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
                assert poll_resp.status_code == 200
                st = poll_resp.json()["state"]
                if st in ("completed", "failed", "cancelled"):
                    break

            final_resp = await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
            assert final_resp.status_code == 200
            run_info = final_resp.json()
            assert run_info["state"] == "completed"
            assert run_info["result"] is not None
            result = run_info["result"]
            assert result["outcome"] == "explained"
            assert len(result["claims"]) == 3
            # Check integer paise formatted text without floats
            assert "INR 240,000" in result["claims"][0]["text"]
            assert "INR 18,000" in result["claims"][1]["text"]
            assert "INR 6,000" in result["claims"][1]["text"]
            assert "INR 186,000" in result["claims"][2]["text"]

            # 3. Retrieve events
            ev_resp = await client.get(f"/api/v1/runs/{run_id}/events?after=0", headers=auth_headers)
            assert ev_resp.status_code == 200
            events_data = ev_resp.json()
            assert events_data["terminal"] is True
            kinds = [e["kind"] for e in events_data["events"]]
            assert "run_started" in kinds
            assert "tool_started" in kinds
            assert "tool_finished" in kinds
            assert "context_built" in kinds
            assert "model_started" in kinds
            assert "model_finished" in kinds
            assert "run_finished" in kinds


@pytest.mark.asyncio
async def test_investigation_cancellation_and_terminal_lock(test_settings: Settings):
    """
    Test that active runs can be cancelled and that terminal runs cannot be rewritten.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    app = create_app(test_settings)
    auth_headers = {"Authorization": f"Bearer {test_settings.owner_token}"}

    async def slow_complete(*args, **kwargs):
        await asyncio.sleep(5.0)
        return Decision(action="answer", answer=AnswerDraft(outcome="insufficient"))

    with patch("moneytrail.agent.complete_structured", side_effect=slow_complete):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            start_resp = await client.post(
                "/api/v1/investigations",
                headers=auth_headers,
                json={"case_id": str(case_id), "question": "Cancel this run"},
            )
            assert start_resp.status_code == 202
            run_id = start_resp.json()["run_id"]

            # Cancel run
            cancel_resp = await client.post(f"/api/v1/runs/{run_id}/cancel", headers=auth_headers)
            assert cancel_resp.status_code in (200, 202)

            # Wait for terminal cancelled
            for _ in range(20):
                await asyncio.sleep(0.1)
                poll = await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
                if poll.json()["state"] == "cancelled":
                    break

            final = await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
            assert final.json()["state"] == "cancelled"

            # Re-cancelling returns 200 with terminal state, not rewritten
            re_cancel = await client.post(f"/api/v1/runs/{run_id}/cancel", headers=auth_headers)
            assert re_cancel.status_code == 200
            assert re_cancel.json()["state"] == "cancelled"


@pytest.mark.asyncio
async def test_offline_provider_failure(test_settings: Settings):
    """
    Test that provider unavailability becomes terminal MODEL_UNAVAILABLE error.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    # Configure invalid offline Ollama host
    offline_settings = test_settings.model_copy(update={"ollama_url": "http://127.0.0.1:54321"})
    app = create_app(offline_settings)
    auth_headers = {"Authorization": f"Bearer {offline_settings.owner_token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        start_resp = await client.post(
            "/api/v1/investigations",
            headers=auth_headers,
            json={"case_id": str(case_id), "question": "Offline test"},
        )
        assert start_resp.status_code == 202
        run_id = start_resp.json()["run_id"]

        for _ in range(30):
            await asyncio.sleep(0.1)
            poll = await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
            if poll.json()["state"] == "failed":
                break

        final = await client.get(f"/api/v1/runs/{run_id}", headers=auth_headers)
        info = final.json()
        assert info["state"] == "failed"
        assert info["error"]["code"] == "MODEL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_live_fixed_demo_investigation(test_settings: Settings):
    """
    Execute live fixed baseline run against the active Ollama daemon with real model qwen3:8b.
    Question: Why was our received payout lower than total gross for the September cohort?
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(
        case_id=case_id,
        question="Why did we receive INR 186,000 instead of our gross sales for the September cohort?",
    )
    run_id = uuid.uuid4()

    try:
        inv_result = await run_fixed(req, run_id, test_settings)
    except LLMError as e:
        if e.code in ("MODEL_UNAVAILABLE", "READ_TIMEOUT", "CONNECT_ERROR"):
            pytest.skip(f"Local Ollama provider unavailable or timed out ({e}); skipping live test in offline test suite")
        raise
    assert inv_result.run_id == run_id
    assert inv_result.case_id == case_id
    assert inv_result.outcome in ("explained", "partial")
    assert len(inv_result.claims) > 0

    # Ensure facts were referenced and validated
    fact_ids = {f.fact_id for f in inv_result.selected_facts}
    assert any("fact_" in fid for fid in fact_ids)

    # Ensure claims contain expanded text without raw currency violations
    for claim in inv_result.claims:
        assert claim.text is not None
        assert "{{fact:" not in claim.text
