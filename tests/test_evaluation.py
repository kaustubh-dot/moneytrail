"""Unit tests for Phase 5 evaluation scoring, validation, and freeze functions."""

import json
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
    generate_final_report,
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
    assert scores["review"]["independent_review"] is False
    assert scores["review"]["support_judgment"] == "unreviewed"

    # With explicit independent reviewer review passed in
    scores_reviewed = score_case(
        gold=gold,
        inv_res=inv_res,
        elapsed_s=5.2,
        tool_calls_used=2,
        model_turns_used=1,
        source_map=source_map,
        reviewer_review={"support_judgment": "pass", "outcome_judgment": "pass", "independent_review": True},
    )
    assert scores_reviewed["review"]["support_judgment"] == "pass"
    assert scores_reviewed["review"]["outcome_judgment"] == "pass"


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


def test_score_case_received_paise_disallows_net():
    """Verify that score_case does NOT accept fact_net or _net when received_paise is required."""
    cid = uuid.uuid4()
    gold = {
        "eval_id": "test-received-fail",
        "expected_outcome": "partial",
        "required_fact_values": {"received_paise": 0},
    }

    # Model emitted fact_net with net amount (4875000), not received_paise (0)
    inv_res = _make_res(
        case_id=cid,
        outcome="partial",
        claims=[Claim(text_template="Net was {{fact:fact_net}}.", text="Net was INR 48,750.", fact_ids=["fact_net"])],
        selected_facts=[Fact(fact_id="fact_net", kind="money", value=4875000, value_paise=4875000, currency="INR")],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=1.0, tool_calls_used=1, model_turns_used=1)
    assert scores["money"]["pass"] is False
    assert any("received_paise" in r for r in scores["money"]["failures"])


def test_score_case_ungrounded_extra_money_fact():
    """Verify score_case catches ungrounded extra money facts not present in gold or derivations."""
    cid = uuid.uuid4()
    gold = {
        "eval_id": "test-grounding-fail",
        "expected_outcome": "explained",
        "required_fact_values": {"gross_paise": 5000000},
    }

    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[
            Claim(
                text_template="Gross was {{fact:fact_gross}} with bonus {{fact:fact_hallucinated}}.",
                text="Gross was INR 50,000 with bonus INR 99,999.99.",
                fact_ids=["fact_gross", "fact_hallucinated"],
            )
        ],
        selected_facts=[
            Fact(fact_id="fact_gross", kind="money", value=5000000, value_paise=5000000, currency="INR"),
            Fact(fact_id="fact_hallucinated", kind="money", value=9999999, value_paise=9999999, currency="INR"),
        ],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=1.0, tool_calls_used=1, model_turns_used=1)
    assert scores["money"]["pass"] is False
    assert any("Ungrounded monetary fact 'fact_hallucinated'" in r for r in scores["money"]["failures"])


def test_score_case_exact_citation_locator_mismatch():
    """Verify score_case checks exact locator and locator_type in required_citations."""
    cid = uuid.uuid4()
    sid = uuid.uuid4()
    source_map = {sid: {"original_path": "orders.csv", "media_type": "text/csv"}}

    gold = {
        "eval_id": "test-cit-locator-fail",
        "expected_outcome": "explained",
        "required_citations": [{"source_path": "orders.csv", "locator_type": "csv", "locator": "csv:row:5"}],
    }

    # Model cited row 1 instead of required row 5
    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[Claim(text_template="Valid.", text="Valid.")],
        citations=[Citation(source_id=sid, locator_id="csv:row:1", label="Row 1")],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=1.0, tool_calls_used=1, model_turns_used=1, source_map=source_map)
    assert scores["evidence"]["pass"] is False
    assert any("locator 'csv:row:5'" in r for r in scores["evidence"]["failures"])


def test_generate_final_report_fails_closed_on_missing_retrieval(tmp_path: Path):
    """Verify generate_final_report fails closed when retrieval report is missing."""
    import json
    eval_output = tmp_path / "test_paired.json"
    summary_output = tmp_path / "summary.md"
    freeze_file = tmp_path / "freeze.json"

    # Minimal runs data where all cases passed
    runs = {}
    for i in range(1, 19):
        fam = "A" if i <= 6 else ("B" if i <= 12 else "C")
        runs[f"test-{i}"] = {
            "eval_id": f"test-{i}",
            "family": fam,
            "expected_outcome": "explained",
            "fixed": {
                "outcome": "explained",
                "elapsed_s": 1.0,
                "budget_summary": {"tool_calls_used": 1, "model_turns_used": 1},
                "scores": {"overall": True},
                "review": {"support_judgment": "pass", "outcome_judgment": "pass"},
            },
            "agent": {
                "outcome": "explained",
                "elapsed_s": 2.0,
                "budget_summary": {"tool_calls_used": 2, "model_turns_used": 1},
                "scores": {
                    "overall": True,
                    "money": {"pass": True, "failures": []},
                    "boundary": {"pass": True, "failures": []},
                    "execution": {"pass": True, "failures": []},
                    "evidence": {"pass": True, "failures": []},
                    "outcome": {"pass": True, "failures": []},
                    "failure_reasons": [],
                },
                "review": {"support_judgment": "pass", "outcome_judgment": "pass"},
            },
            "comparison": {"winner": "agent"},
        }

    eval_data = {
        "split": "test",
        "freeze_commit": "abcdef123456",
        "runs": runs,
    }
    eval_output.write_text(json.dumps(eval_data), encoding="utf-8")

    freeze_data = {
        "commit_hash": "abcdef123456",
        "pinned_revisions": {"model": "qwen3:8b", "model_digest": "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"},
        "hardware": {"cpu": "AMD", "ram": "32GB"},
    }
    freeze_file.write_text(json.dumps(freeze_data), encoding="utf-8")

    report = generate_final_report(eval_output, summary_output, freeze_path=freeze_file)
    # Since test_retrieval.json is missing, retrieval recall must default to 0.0 and fail G5
    assert "FAIL (REJECTED)" in report
    assert "DOES NOT SATISFY" in report


def test_score_case_ungrounded_extra_money_fact_even_with_citation():
    """R2-003: Verify score_case rejects ungrounded extra money facts even if citing a case source."""
    cid = uuid.uuid4()
    sid = uuid.uuid4()
    source_map = {sid: {"original_path": "orders.csv", "media_type": "text/csv"}}
    gold = {
        "eval_id": "test-grounding-fail-with-citation",
        "expected_outcome": "explained",
        "required_fact_values": {"gross_paise": 5000000},
    }

    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[
            Claim(
                text_template="Gross was {{fact:fact_gross}} with extra {{fact:fact_hallucinated}}.",
                text="Gross was INR 50,000 with extra INR 99,999.99.",
                fact_ids=["fact_gross", "fact_hallucinated"],
            )
        ],
        selected_facts=[
            Fact(fact_id="fact_gross", kind="money", value=5000000, value_paise=5000000, currency="INR"),
            Fact(
                fact_id="fact_hallucinated",
                kind="money",
                value=9999999,
                value_paise=9999999,
                currency="INR",
                citations=[Citation(source_id=sid, locator_id="csv:r1", label="Row 1")],
            ),
        ],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=1.0, tool_calls_used=1, model_turns_used=1, source_map=source_map)
    assert scores["money"]["pass"] is False
    assert any("Ungrounded monetary fact 'fact_hallucinated'" in r for r in scores["money"]["failures"])


def test_score_case_does_not_synthesize_human_review_pass():
    """R2-003: Verify score_case does not synthesize a human review pass when unreviewed."""
    cid = uuid.uuid4()
    gold = {"eval_id": "test-review-unreviewed", "expected_outcome": "explained"}
    inv_res = _make_res(case_id=cid, outcome="explained", claims=[Claim(text_template="Valid.", text="Valid.")])

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=1.0, tool_calls_used=1, model_turns_used=1)
    assert scores["review"]["independent_review"] is False
    assert scores["review"]["support_judgment"] == "unreviewed"
    assert scores["review"]["outcome_judgment"] == "unreviewed"


def test_score_case_claim_unregistered_fact_fails_evidence():
    """R2-003: Verify claim referencing unregistered fact ID fails evidence."""
    cid = uuid.uuid4()
    gold = {"eval_id": "test-claim-unreg", "expected_outcome": "explained"}
    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[Claim(text_template="Text {{fact:fact_missing}}", text="Text", fact_ids=["fact_missing"])],
        selected_facts=[],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=1.0, tool_calls_used=1, model_turns_used=1, source_map={})
    assert scores["evidence"]["pass"] is False
    assert any("unregistered fact ID 'fact_missing'" in r for r in scores["evidence"]["failures"])


def test_strict_environment_freeze_validation_fails_on_commit_mismatch(tmp_path: Path):
    """R3-001: Verify validate_freeze(strict_environment=True) refuses if active git HEAD != freeze commit."""
    from moneytrail.evaluation import validate_freeze
    freeze_file = tmp_path / "freeze.json"
    fake_data = {
        "commit_hash": "deadbeef1234567890abcdef1234567890abcdef",
        "dependency_locks": {},
        "prompts_and_schemas": {},
        "datasets": {},
    }
    freeze_file.write_text(json.dumps(fake_data), encoding="utf-8")

    # strict_environment=False passes (locks and datasets empty)
    res = validate_freeze(freeze_file, strict_environment=False, repo_root=Path("."))
    assert res["commit_hash"] == fake_data["commit_hash"]

    # strict_environment=True must raise ValueError refusing run
    with pytest.raises(ValueError) as exc_info:
        validate_freeze(freeze_file, strict_environment=True, repo_root=Path("."))
    assert "Strict environment check failed" in str(exc_info.value)
    assert "does not match freeze commit" in str(exc_info.value)


def test_score_case_semantic_money_collision_rejected():
    """R3-002: Verify score_case rejects facts that borrow valid numeric values for the wrong semantic field."""
    cid = uuid.uuid4()
    gold = {
        "eval_id": "test-semantic-collision",
        "expected_outcome": "explained",
        "required_fact_values": {"gross_paise": 8000000, "refunds_paise": 1000000},
    }

    inv_res = _make_res(
        case_id=cid,
        outcome="explained",
        claims=[
            Claim(
                text_template="Gross was {{fact:fact_gross}} and refunds were {{fact:fact_refunds}}.",
                text="text",
                fact_ids=["fact_gross", "fact_refunds"],
            )
        ],
        selected_facts=[
            Fact(fact_id="fact_gross", kind="money", value=1000000, value_paise=1000000, currency="INR"),
            Fact(fact_id="fact_refunds", kind="money", value=8000000, value_paise=8000000, currency="INR"),
        ],
    )

    scores = score_case(gold=gold, inv_res=inv_res, elapsed_s=1.0, tool_calls_used=1, model_turns_used=1)
    assert scores["money"]["pass"] is False
    assert any("does not match expected gross derivation" in r for r in scores["money"]["failures"])
    assert any("does not match expected refunds derivation" in r for r in scores["money"]["failures"])


def test_generate_final_report_fails_closed_on_unreviewed_cases(tmp_path: Path):
    """R3-002: Verify generate_final_report requires independent_review=True and passing judgments for G5."""
    eval_output = tmp_path / "test_paired.json"
    summary_output = tmp_path / "summary.md"
    freeze_file = tmp_path / "freeze.json"
    retrieval_file = tmp_path / "test_retrieval.json"

    retrieval_data = {
        "total_questions": 18,
        "eligible_questions": 17,
        "candidate_recall_top20": {"reranked": {"pooled": 1.0}},
        "final_doc_recall_top6": {"reranked": {"pooled": 1.0}},
    }
    retrieval_file.write_text(json.dumps(retrieval_data), encoding="utf-8")

    runs = {}
    for i in range(1, 19):
        fam = "A" if i <= 6 else ("B" if i <= 12 else "C")
        runs[f"test-{i}"] = {
            "eval_id": f"test-{i}",
            "family": fam,
            "expected_outcome": "explained",
            "fixed": {
                "outcome": "explained",
                "elapsed_s": 1.0,
                "budget_summary": {"tool_calls_used": 1, "model_turns_used": 1},
                "scores": {"overall": True},
                "review": {"support_judgment": "pass", "outcome_judgment": "pass", "independent_review": False},
            },
            "agent": {
                "outcome": "explained",
                "elapsed_s": 2.0,
                "budget_summary": {"tool_calls_used": 2, "model_turns_used": 1},
                "scores": {
                    "overall": True,
                    "money": {"pass": True, "failures": []},
                    "boundary": {"pass": True, "failures": []},
                    "execution": {"pass": True, "failures": []},
                    "evidence": {"pass": True, "failures": []},
                    "outcome": {"pass": True, "failures": []},
                    "failure_reasons": [],
                },
                "review": {"support_judgment": "pass", "outcome_judgment": "pass", "independent_review": False},
            },
            "comparison": {"winner": "agent"},
        }

    eval_data = {
        "split": "test",
        "freeze_commit": "abcdef123456",
        "runs": runs,
    }
    eval_output.write_text(json.dumps(eval_data), encoding="utf-8")

    freeze_data = {
        "commit_hash": "abcdef123456",
        "pinned_revisions": {"model": "qwen3:8b", "model_digest": "500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41"},
        "hardware": {"cpu": "AMD", "ram": "32GB"},
    }
    freeze_file.write_text(json.dumps(freeze_data), encoding="utf-8")

    report = generate_final_report(eval_output, summary_output, freeze_path=freeze_file)
    assert "FAIL (REJECTED)" in report
    assert "Independent Evaluator Review Incomplete" in report

    for i in range(1, 19):
        runs[f"test-{i}"]["agent"]["review"]["independent_review"] = True
    eval_data["runs"] = runs
    eval_output.write_text(json.dumps(eval_data), encoding="utf-8")

    report_approved = generate_final_report(eval_output, summary_output, freeze_path=freeze_file)
    assert "PASS (ACCEPTED)" in report_approved
    assert "18/18 PASSED" in report_approved



