"""Unit tests for Phase 5 evaluation scoring, validation, and freeze functions."""

import uuid
from datetime import datetime
from pathlib import Path
import pytest

from moneytrail.evaluation import (
    validate_split,
    score_case,
    check_working_tree_clean,
    create_freeze_manifest,
    validate_freeze,
)
from moneytrail.schemas import (
    InvestigationResult,
    Claim,
    Fact,
    Citation,
    EvidenceGap,
    Reconciliation,
)


def _make_res(**kwargs) -> InvestigationResult:
    base = {
        "run_id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "cohort_start": datetime.now(),
        "cohort_end": datetime.now(),
        "as_of": datetime.now(),
        "outcome": "explained",
        "claims": [],
        "selected_facts": [],
        "citations": [],
        "unresolved": [],
    }
    base.update(kwargs)
    return InvestigationResult(**base)


def test_validate_split_dev_and_test():
    """Verify validate_split passes on both dev and test datasets."""
    eval_root = Path("evals")
    assert validate_split(eval_root, "dev") is True
    assert validate_split(eval_root, "test") is True


def test_score_case_passing():
    """Verify score_case returns overall pass on a compliant investigation result."""
    cid = uuid.uuid4()
    sid = uuid.uuid4()
    source_map = {sid: {"original_path": "orders.csv", "media_type": "text/csv"}}

    gold = {
        "eval_id": "test-pass-1",
        "expected_outcome": "explained",
        "required_fact_values": {"gross_paise": 5000000, "refunds_paise": 1000000},
        "required_findings": [],
        "prohibited_claims": ["unexplained gap"],
        "required_citations": [{"source_path": "orders.csv", "locator_type": "csv"}],
        "rubric_notes": "Hand calc: 5000000 gross - 1000000 refunds.",
    }

    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[
            Claim(
                text_template="Gross sales were {{fact:fact_gross}} with refunds of {{fact:fact_refunds}}.",
                text="Gross sales were INR 50,000 with refunds of INR 10,000.",
                fact_ids=["fact_gross", "fact_refunds"],
                citations=[Citation(source_id=sid, locator_id="csv:r1", label="Order 1")],
            )
        ],
        selected_facts=[
            Fact(fact_id="fact_gross", kind="money", value=5000000, value_paise=5000000, currency="INR"),
            Fact(fact_id="fact_refunds", kind="money", value=1000000, value_paise=1000000, currency="INR"),
        ],
        citations=[Citation(source_id=sid, locator_id="csv:r1", label="Order 1")],
    )

    scores = score_case(
        gold=gold,
        inv_res=inv_res,
        elapsed_s=5.2,
        tool_calls_used=2,
        model_turns_used=1,
        source_map=source_map,
    )

    assert scores["overall"] is True
    assert scores["execution"]["pass"] is True
    assert scores["money"]["pass"] is True
    assert scores["evidence"]["pass"] is True
    assert scores["outcome"]["pass"] is True
    assert scores["boundary"]["pass"] is True
    assert scores["review"]["support_judgment"] == "pass"
    assert scores["review"]["outcome_judgment"] == "pass"


def test_score_case_money_mismatch():
    """Verify score_case catches mismatched monetary facts."""
    cid = uuid.uuid4()
    sid = uuid.uuid4()
    source_map = {sid: {"original_path": "orders.csv", "media_type": "text/csv"}}

    gold = {
        "eval_id": "test-money-fail",
        "expected_outcome": "explained",
        "required_fact_values": {"gross_paise": 5000000},
        "required_findings": [],
        "prohibited_claims": [],
        "required_citations": [],
    }

    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[Claim(text_template="Sales were {{fact:fact_gross}}.", text="Sales were INR 40,000.", fact_ids=["fact_gross"])],
        selected_facts=[Fact(fact_id="fact_gross", kind="money", value=4000000, value_paise=4000000, currency="INR")],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=3.0, tool_calls_used=1, model_turns_used=1, source_map=source_map)
    assert scores["money"]["pass"] is False
    assert scores["overall"] is False
    assert any("gross_paise" in r for r in scores["money"]["failures"])


def test_score_case_ungrounded_currency_literal():
    """Verify score_case catches raw currency amounts in claim templates."""
    cid = uuid.uuid4()
    gold = {"eval_id": "test-lit-fail", "expected_outcome": "explained", "required_fact_values": {}}

    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[Claim(text_template="The refund was INR 5000 directly.", text="The refund was INR 5000 directly.")],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=1.0, tool_calls_used=1, model_turns_used=1)
    assert scores["money"]["pass"] is False
    assert any("Ungrounded currency" in r for r in scores["money"]["failures"])


def test_score_case_prohibited_claim():
    """Verify score_case catches prohibited claims."""
    cid = uuid.uuid4()
    gold = {
        "eval_id": "test-proh-fail",
        "expected_outcome": "explained",
        "required_fact_values": {},
        "prohibited_claims": ["unaccounted money"],
    }

    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[Claim(text_template="There is unaccounted money in the account.", text="There is unaccounted money in the account.")],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=1.0, tool_calls_used=1, model_turns_used=1)
    assert scores["money"]["pass"] is False
    assert any("Prohibited claim" in r for r in scores["money"]["failures"])


def test_score_case_outcome_and_finding_mismatch():
    """Verify score_case catches outcome mismatch and missing required findings."""
    cid = uuid.uuid4()
    gold = {
        "eval_id": "test-out-fail",
        "expected_outcome": "partial",
        "required_fact_values": {},
        "required_findings": ["POLICY_CONFLICT"],
    }

    inv_res = _make_res(
        case_id=cid,
        outcome="explained",  # Expected partial
        claims=[Claim(text_template="Everything was reconciled.", text="Everything was reconciled.")],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=2.0, tool_calls_used=1, model_turns_used=1)
    assert scores["outcome"]["pass"] is False
    assert any("Outcome mismatch" in r for r in scores["outcome"]["failures"])
    assert any("POLICY_CONFLICT" in r for r in scores["outcome"]["failures"])


def test_score_case_execution_deadline_and_tools():
    """Verify score_case catches deadline or tool limit exceedance."""
    cid = uuid.uuid4()
    gold = {"eval_id": "test-exec-fail", "expected_outcome": "explained"}

    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[Claim(text_template="Valid.", text="Valid.")],
    )

    # Exceeded deadline
    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=245.0, tool_calls_used=3, model_turns_used=1)
    assert scores["execution"]["pass"] is False
    assert any("240s deadline" in r for r in scores["execution"]["failures"])

    # Exceeded tool calls limit
    scores_tools = score_case(gold=gold, inv_res=inv_res, elapsed_s=10.0, tool_calls_used=7, model_turns_used=1)
    assert scores_tools["execution"]["pass"] is False
    assert any("limit of 6" in r for r in scores_tools["execution"]["failures"])


def test_score_case_boundary_leak():
    """Verify score_case catches cross-case citation."""
    cid = uuid.uuid4()
    sid_foreign = uuid.uuid4()
    source_map = {uuid.uuid4(): {"original_path": "orders.csv", "media_type": "text/csv"}}

    gold = {"eval_id": "test-bound-fail", "expected_outcome": "explained"}

    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[Claim(text_template="Valid.", text="Valid.")],
        citations=[Citation(source_id=sid_foreign, locator_id="csv:r1", label="Foreign")],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=1.0, tool_calls_used=1, model_turns_used=1, source_map=source_map)
    assert scores["boundary"]["pass"] is False
    assert any("Cross-case" in r for r in scores["boundary"]["failures"])


def test_check_working_tree_clean_behavior(monkeypatch, tmp_path: Path):
    """Verify check_working_tree_clean raises when dirty files are present."""
    import subprocess
    import moneytrail.evaluation as ev

    class FakeRes:
        returncode = 0
        stdout = " M src/moneytrail/agent.py\n"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeRes())
    with pytest.raises(RuntimeError) as exc_info:
        ev.check_working_tree_clean(tmp_path)
    assert "Cannot freeze" in str(exc_info.value)
    assert "src/moneytrail/agent.py" in str(exc_info.value)


def test_freeze_manifest_and_validation(monkeypatch, tmp_path: Path):
    """Verify freeze manifest generation and validation."""
    import moneytrail.evaluation as ev
    # Allow freeze manifest generation in unit test regardless of active git state
    monkeypatch.setattr(ev, "check_working_tree_clean", lambda root: None)

    freeze_file = tmp_path / "freeze.json"
    manifest = create_freeze_manifest(output_path=freeze_file, eval_root=Path("evals"))

    assert manifest["freeze_version"] == 1
    assert "commit_hash" in manifest
    assert manifest["working_tree_clean"] is True
    assert "qwen3:8b" in manifest["pinned_revisions"]["model"]

    # Validate freeze against test questions
    validated = validate_freeze(freeze_file, eval_root=Path("evals"))
    assert validated["commit_hash"] == manifest["commit_hash"]
