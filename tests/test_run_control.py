"""Run control and bounded execution tests for MoneyTrail agent investigation graph."""

import asyncio
from datetime import datetime
from pathlib import Path
import time
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from moneytrail.agent import AgentRunner, assemble_context, run_agent
from moneytrail.config import Settings
from moneytrail.db import get_reader_connection
from moneytrail.ingest import import_bundle
from moneytrail.llm import LLMError
from moneytrail.schemas import (
    AnswerDraft,
    Citation,
    Claim,
    Decision,
    Fact,
    InvestigationRequest,
    InvestigationState,
    Reconciliation,
    ToolCall,
)


@pytest.mark.asyncio
async def test_bootstrap_reconciliation(test_settings: Settings):
    """
    MT4.1: Verify bootstrap node performs deterministic reconciliation,
    counts as 1 of 6 tools, registers facts and citations, and injects case_id.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(case_id=case_id, question="What are the gross sales?")
    run_id = uuid.uuid4()

    events = []
    async def record_event(kind: str, payload: dict):
        events.append((kind, payload))

    reader_conn = await get_reader_connection(test_settings)
    try:
        runner = AgentRunner(
            request=req,
            run_id=run_id,
            settings=test_settings,
            cancel_event=None,
            record_event_fn=record_event,
            reader_conn=reader_conn,
        )
        init_state = InvestigationState(
            run_id=run_id,
            case_id=case_id,
            question=req.question,
        )

        updates = await runner.bootstrap_node(init_state)

        # Verification:
        assert updates["tool_calls_used"] == 1
        assert updates["reconciliation"] is not None
        assert updates["reconciliation"].gross_paise == 24000000
        assert updates["reconciliation"].reconciled is True
        assert len(updates["facts"]) >= 7  # gross, refunds, fees, net, received, pending, difference
        assert updates["phase"] == "decide"
        assert len(updates["tool_history"]) == 1
        assert updates["tool_history"][0]["tool"] == "reconcile"

        # Events emitted:
        kinds = [e[0] for e in events]
        assert "tool_started" in kinds
        assert "tool_finished" in kinds
    finally:
        await reader_conn.close()


@pytest.mark.asyncio
async def test_decide_immediate_answer(test_settings: Settings):
    """
    MT4.1: Exercise an original question needing no extra tools (turn 1 answer).
    Graph completes in 1 model turn and 1 tool call (reconciliation).
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(case_id=case_id, question="What was the total gross and pending amount?")
    run_id = uuid.uuid4()

    # Deterministic model decision: immediate answer using registered facts
    mock_decision = Decision(
        action="answer",
        answer=AnswerDraft(
            outcome="explained",
            claims=[
                Claim(
                    text_template="Gross sales totaled {{fact:fact_gross}}.",
                    fact_ids=["fact_gross"],
                    citations=[],
                ),
                Claim(
                    text_template="Documented pending settlements totaled {{fact:fact_pending}}.",
                    fact_ids=["fact_pending"],
                    citations=[],
                ),
            ],
            selected_fact_ids=["fact_gross", "fact_pending"],
            unresolved=[],
        ),
    )

    with patch("moneytrail.agent.complete_structured", new_callable=AsyncMock) as mock_complete:
        mock_complete.return_value = mock_decision

        result = await run_agent(req, run_id, test_settings)

        assert result.outcome == "explained"
        assert len(result.claims) == 2
        assert result.claims[0].text == "Gross sales totaled INR 240,000."
        assert result.claims[1].text == "Documented pending settlements totaled INR 30,000."
        assert result.budget_summary.get("tool_calls_used") == 1
        assert result.budget_summary.get("model_turns_used") == 1
        assert mock_complete.call_count == 1


@pytest.mark.asyncio
async def test_decide_with_tool_dispatch(test_settings: Settings):
    """
    MT4.1: Exercise an original question requiring extra source inspection.
    Turn 1: model requests inspect_order('O-002').
    Turn 2: model provides final answer with order evidence.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(case_id=case_id, question="What was the status and net for order O-002?")
    run_id = uuid.uuid4()

    turn1_decision = Decision(
        action="tools",
        calls=[ToolCall(action="inspect_order", order_id="O-002")],
    )
    turn2_decision = Decision(
        action="answer",
        answer=AnswerDraft(
            outcome="explained",
            claims=[
                Claim(
                    text_template="Order O-002 gross was {{fact:fact_O-002_gross}}.",
                    fact_ids=["fact_O-002_gross"],
                    citations=[],
                ),
            ],
            selected_fact_ids=["fact_O-002_gross"],
            unresolved=[],
        ),
    )

    with patch("moneytrail.agent.complete_structured", new_callable=AsyncMock) as mock_complete:
        mock_complete.side_effect = [turn1_decision, turn2_decision]

        result = await run_agent(req, run_id, test_settings)

        assert result.outcome == "explained"
        assert len(result.claims) == 1
        assert result.claims[0].text == "Order O-002 gross was INR 60,000."
        assert result.budget_summary.get("tool_calls_used") == 2  # reconcile + inspect_order
        assert result.budget_summary.get("model_turns_used") == 2
        assert mock_complete.call_count == 2


@pytest.mark.asyncio
async def test_sequential_cap_at_most_two_calls_per_turn(test_settings: Settings):
    """
    MT4.1: Enforce at most two sequential calls dispatched per model turn.
    If model specifies 3 calls, only the first 2 are executed.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(case_id=case_id, question="Check multiple orders")
    run_id = uuid.uuid4()

    turn1_decision = Decision(
        action="tools",
        calls=[
            ToolCall(action="inspect_order", order_id="O-001"),
            ToolCall(action="inspect_order", order_id="O-002"),
            ToolCall(action="inspect_order", order_id="O-003"),  # 3rd call should be ignored this turn
        ],
    )
    turn2_decision = Decision(
        action="answer",
        answer=AnswerDraft(
            outcome="explained",
            claims=[
                Claim(
                    text_template="Orders O-001 and O-002 were inspected.",
                    fact_ids=[],
                    citations=[],
                ),
            ],
            selected_fact_ids=[],
            unresolved=[],
        ),
    )

    with patch("moneytrail.agent.complete_structured", new_callable=AsyncMock) as mock_complete:
        mock_complete.side_effect = [turn1_decision, turn2_decision]

        result = await run_agent(req, run_id, test_settings)

        # Total tools: 1 (reconcile) + 2 (first two calls) = 3
        assert result.budget_summary.get("tool_calls_used") == 3


@pytest.mark.asyncio
async def test_fourth_turn_must_answer_budget_exhausted(test_settings: Settings):
    """
    MT4.1 / MT4.2: The fourth model call must answer; a continuation request
    terminates with BUDGET_EXHAUSTED.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(case_id=case_id, question="Perpetual tool loop")
    run_id = uuid.uuid4()

    # Model attempts tools on all 4 turns
    tool_decision = Decision(
        action="tools",
        calls=[ToolCall(action="inspect_order", order_id="O-001")],
    )

    with patch("moneytrail.agent.complete_structured", new_callable=AsyncMock) as mock_complete:
        mock_complete.return_value = tool_decision

        with pytest.raises(LLMError) as exc_info:
            await run_agent(req, run_id, test_settings)

        assert exc_info.value.code == "BUDGET_EXHAUSTED"


@pytest.mark.asyncio
async def test_tool_call_budget_limit_exhausted(test_settings: Settings):
    """
    MT4.2: Enforce max 6 tool calls budget. Once 6 calls are used,
    further tool dispatches are blocked and continuation raises BUDGET_EXHAUSTED.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(case_id=case_id, question="Tool exhaustion test")
    run_id = uuid.uuid4()

    d1 = Decision(action="tools", calls=[ToolCall(action="inspect_order", order_id="O-001"), ToolCall(action="inspect_order", order_id="O-002")])
    d2 = Decision(action="tools", calls=[ToolCall(action="inspect_order", order_id="O-003"), ToolCall(action="inspect_order", order_id="O-004")])
    d3 = Decision(action="tools", calls=[ToolCall(action="inspect_order", order_id="O-005"), ToolCall(action="inspect_order", order_id="O-001")])
    d4 = Decision(action="tools", calls=[ToolCall(action="inspect_order", order_id="O-002")])

    with patch("moneytrail.agent.complete_structured", new_callable=AsyncMock) as mock_complete:
        mock_complete.side_effect = [d1, d2, d3, d4]

        with pytest.raises(LLMError) as exc_info:
            await run_agent(req, run_id, test_settings)

        assert exc_info.value.code == "BUDGET_EXHAUSTED"


def test_context_budget_and_omitted_chunks(test_settings: Settings):
    """
    MT4.2: Count complete messages, template, and schema overhead.
    Ensure low-ranked optional chunks are dropped when near budget,
    and omitted_chunks is recorded.
    """
    dummy_state = InvestigationState(
        run_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        question="What is the policy?",
        as_of=datetime.now(),
        cohort_start=datetime.now(),
        cohort_end=datetime.now(),
        reconciliation=Reconciliation(
            gross_paise=24000000,
            completed_refunds_paise=1800000,
            assessed_fees_paise=600000,
            received_allocations_paise=18600000,
            documented_pending_paise=3000000,
            unexplained_difference_paise=0,
            reconciled=True,
        ),
        facts=[
            Fact(fact_id="fact_gross", kind="money", value_paise=24000000, value="24000000"),
        ],
        evidence=[
            {
                "chunk_id": str(uuid.uuid4()),
                "source_id": str(uuid.uuid4()),
                "locator_id": f"text:l{i*10}-{i*10+9}",
                "text": f"This is correspondence chunk #{i}. " * 50,
                "kind": "correspondence",
            }
            for i in range(20)
        ],
    )

    # With a constrained budget of 2500 tokens:
    low_budget_settings = test_settings.model_copy(update={"input_token_budget": 2500})
    packet = assemble_context(dummy_state, low_budget_settings, is_agent=True)

    assert packet.token_count <= 2500
    assert packet.omitted_chunks > 0
    assert len(packet.registered_facts) == 1


def test_mandatory_overflow_raises_context_limit(test_settings: Settings):
    """
    MT4.2: Essential overflow of mandatory content raises CONTEXT_LIMIT.
    """
    dummy_state = InvestigationState(
        run_id=uuid.uuid4(),
        case_id=uuid.uuid4(),
        question="Question " * 500,  # Huge question
        as_of=datetime.now(),
        cohort_start=datetime.now(),
        cohort_end=datetime.now(),
    )

    tiny_budget_settings = test_settings.model_copy(update={"input_token_budget": 500})
    with pytest.raises(LLMError) as exc_info:
        assemble_context(dummy_state, tiny_budget_settings, is_agent=True)

    assert exc_info.value.code == "CONTEXT_LIMIT"


@pytest.mark.asyncio
async def test_monotonic_deadline_timeout(test_settings: Settings):
    """
    MT4.2 / MT4.3: Monotonic deadline timeout raises READ_TIMEOUT.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(case_id=case_id, question="Timeout test")
    run_id = uuid.uuid4()

    # Set run timeout to 0 seconds so it immediately expires
    expired_settings = test_settings.model_copy(update={"run_timeout_seconds": 0})

    with pytest.raises(LLMError) as exc_info:
        await run_agent(req, run_id, expired_settings)

    assert exc_info.value.code == "READ_TIMEOUT"


@pytest.mark.asyncio
async def test_cancellation_propagation(test_settings: Settings):
    """
    MT4.3: Cancellation event aborts before next turn and raises asyncio.CancelledError.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(case_id=case_id, question="Cancel test")
    run_id = uuid.uuid4()

    cancel = asyncio.Event()
    cancel.set()  # Pre-cancelled

    with pytest.raises(asyncio.CancelledError):
        await run_agent(req, run_id, test_settings, cancel=cancel)


@pytest.mark.asyncio
async def test_transient_read_retry(test_settings: Settings):
    """
    MT4.3: Permit at most one retry total for a transient failed read before response data
    is consumed; log it and count the tool dispatch.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    reader_conn = await get_reader_connection(test_settings)
    try:
        async with reader_conn.cursor() as cur:
            await cur.execute("SELECT source_id FROM sources WHERE case_id = %s AND relative_name = 'orders.csv';", [case_id])
            row = await cur.fetchone()
            sample_source_id = row[0]
    finally:
        await reader_conn.close()

    req = InvestigationRequest(case_id=case_id, question="Read source with retry")
    run_id = uuid.uuid4()

    turn1_decision = Decision(
        action="tools",
        calls=[ToolCall(action="read_source", source_id=sample_source_id, locator_id="csv:r1")],
    )
    turn2_decision = Decision(
        action="answer",
        answer=AnswerDraft(
            outcome="explained",
            claims=[Claim(text_template="Read successfully.", fact_ids=[], citations=[])],
            selected_fact_ids=[],
            unresolved=[],
        ),
    )

    from moneytrail.tools import dispatch_tool
    call_count = 0

    async def mock_dispatch(c_id, call, settings, db=None):
        nonlocal call_count
        if call.action == "read_source":
            call_count += 1
            if call_count == 1:
                raise OSError("Transient I/O error")
        return await dispatch_tool(c_id, call, settings, db=db)

    with patch("moneytrail.agent.complete_structured", new_callable=AsyncMock) as mock_complete:
        mock_complete.side_effect = [turn1_decision, turn2_decision]
        with patch("moneytrail.agent.dispatch_tool", side_effect=mock_dispatch):
            result = await run_agent(req, run_id, test_settings)

            assert result.outcome == "explained"
            # 1 reconcile + 1 failed read + 1 retry read = 3 tool calls used
            assert result.budget_summary.get("tool_calls_used") == 3
            assert call_count == 2


def test_invalid_citation_locator_rejected_without_rewriting():
    """
    F-007 regression test:
    Verify validate_citations rejects invalid locators strictly by raising ValueError,
    and does NOT silently rewrite them to matching_locs[0].
    """
    from moneytrail.agent import validate_citations

    src_id = uuid.uuid4()
    valid_citations = {(src_id, "csv:r1"), (src_id, "csv:r2")}

    # Valid citation passes
    valid_cit = Citation(source_id=src_id, locator_id="csv:r1", label="Valid Citation")
    validate_citations([valid_cit], valid_citations)
    assert valid_cit.locator_id == "csv:r1"

    # Citation with invalid locator for known source must raise ValueError and NOT be rewritten
    invalid_cit = Citation(source_id=src_id, locator_id="csv:r999", label="Invalid Citation")
    with pytest.raises(ValueError, match="Invalid or cross-case citation locator"):
        validate_citations([invalid_cit], valid_citations)

    # Must not have been rewritten to csv:r1
    assert invalid_cit.locator_id == "csv:r999"


@pytest.mark.asyncio
async def test_fatal_tool_execution_error_propagates(test_settings: Settings):
    """
    F-005 regression test:
    Verify fatal tool execution error (e.g. database disconnect) raises
    LLMError('TOOL_EXECUTION_FAILED') instead of being silently ignored.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(case_id=case_id, question="What about order O-001?")
    run_id = uuid.uuid4()

    turn1_decision = Decision(
        action="tools",
        calls=[ToolCall(action="inspect_order", order_id="O-001")],
    )

    import psycopg
    with patch("moneytrail.agent.complete_structured", new_callable=AsyncMock) as mock_complete:
        mock_complete.return_value = turn1_decision
        with patch("moneytrail.agent.dispatch_tool", side_effect=psycopg.OperationalError("Database connection lost")):
            with pytest.raises(LLMError) as exc_info:
                await run_agent(req, run_id, test_settings)

            assert exc_info.value.code == "TOOL_EXECUTION_FAILED"


@pytest.mark.asyncio
async def test_exhausted_validation_turns_raises_validation_failed(test_settings: Settings):
    """
    F-005 regression test:
    Verify that when model turns are exhausted by repeated invalid answer drafts,
    LLMError('VALIDATION_FAILED') is raised instead of falling back to outcome='insufficient'.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    req = InvestigationRequest(case_id=case_id, question="What are the gross sales?")
    run_id = uuid.uuid4()

    # Draft with hallucinated fact ID that fails validate_result
    bad_draft = AnswerDraft(
        outcome="explained",
        claims=[
            Claim(
                text_template="Gross sales totaled {{fact:fact_nonexistent}}.",
                fact_ids=["fact_nonexistent"],
                citations=[],
            )
        ],
        selected_fact_ids=["fact_nonexistent"],
        unresolved=[],
    )
    bad_decision = Decision(action="answer", answer=bad_draft)

    with patch("moneytrail.agent.complete_structured", new_callable=AsyncMock) as mock_complete:
        mock_complete.return_value = bad_decision

        with pytest.raises(LLMError) as exc_info:
            await run_agent(req, run_id, test_settings)

        assert exc_info.value.code == "VALIDATION_FAILED"

