"""Answer draft validation, fact token expansion, and uncertainty enforcement regressions."""

import uuid
from datetime import datetime
import pytest

from moneytrail.agent import (
    check_currency_literals,
    expand_fact_tokens,
    format_paise,
    validate_result,
)
from moneytrail.schemas import (
    AnswerDraft,
    Citation,
    Claim,
    EvidenceGap,
    Fact,
    Reconciliation,
)


def test_format_paise_exact_integer():
    """Verify exact integer paise math with no floating-point conversion."""
    assert format_paise(24000000) == "INR 240,000"
    assert format_paise(1800000) == "INR 18,000"
    assert format_paise(600000) == "INR 6,000"
    assert format_paise(18600000) == "INR 186,000"
    assert format_paise(3000000) == "INR 30,000"
    assert format_paise(0) == "INR 0"
    assert format_paise(125050) == "INR 1,250.50"
    assert format_paise(-50000) == "-INR 500"
    assert format_paise(-125050) == "-INR 1,250.50"

    with pytest.raises(TypeError):
        format_paise(24000.0)  # type: ignore


def test_expand_fact_tokens_valid():
    """Verify fact tokens are expanded into formatted amounts, rates, and dates."""
    facts = {
        "fact_gross": Fact(
            fact_id="fact_gross",
            kind="money",
            value=24000000,
            value_paise=24000000,
            currency="INR",
        ),
        "fact_fee_rate": Fact(
            fact_id="fact_fee_rate",
            kind="rate",
            value="250 bps",
        ),
        "fact_settle_date": Fact(
            fact_id="fact_settle_date",
            kind="date",
            value="2026-10-02",
        ),
    }

    template = (
        "Total gross was {{fact:fact_gross}} with fee {{fact:fact_fee_rate}}, "
        "settling on {{fact:fact_settle_date}}."
    )
    expanded = expand_fact_tokens(template, facts)
    assert (
        expanded
        == "Total gross was INR 240,000 with fee 250 bps, settling on 2026-10-02."
    )


def test_expand_fact_tokens_unregistered_and_malformed():
    """Verify unregistered fact IDs and malformed tokens are rejected."""
    facts = {
        "fact_gross": Fact(
            fact_id="fact_gross",
            kind="money",
            value=100000,
            value_paise=100000,
        )
    }

    with pytest.raises(ValueError, match="Unregistered fact ID 'fact_invented'"):
        expand_fact_tokens("Amount is {{fact:fact_invented}}.", facts)

    with pytest.raises(ValueError, match="expected 'fact:<id>' format"):
        expand_fact_tokens("Amount is {{invalid_token}}.", facts)

    with pytest.raises(ValueError, match="Malformed fact ID"):
        expand_fact_tokens("Amount is {{fact:}}.", facts)


def test_currency_literal_rejection():
    """
    Validation rejects obvious currency literals outside fact tokens,
    but permits ordinary words like 'one', 'second', 'two'.
    """
    with pytest.raises(ValueError, match="Obvious currency literal found"):
        check_currency_literals("We paid INR 50,000 for the order.")

    with pytest.raises(ValueError, match="Obvious currency literal found"):
        check_currency_literals("The fee was Rs. 250.")

    with pytest.raises(ValueError, match="Obvious currency literal found"):
        check_currency_literals("Received ₹1,000 directly.")

    with pytest.raises(ValueError, match="Obvious currency literal found"):
        check_currency_literals("The refund of 50000 rupees was processed.")

    with pytest.raises(ValueError, match="Obvious currency literal found"):
        check_currency_literals("Total fee is 12500 paise.")

    # Plain words and numbers without currency symbols must NOT raise
    check_currency_literals("There were two orders and one refund in the second week.")
    check_currency_literals("All 5 orders settled within 2 days.")


def test_validate_result_unregistered_facts():
    """Draft with unregistered fact IDs in selected_fact_ids or claim.fact_ids is rejected."""
    facts = [
        Fact(
            fact_id="fact_gross",
            kind="money",
            value=24000000,
            value_paise=24000000,
        )
    ]

    # Unregistered in selected_fact_ids
    draft1 = AnswerDraft(
        outcome="explained",
        claims=[],
        selected_fact_ids=["fact_invented"],
    )
    with pytest.raises(ValueError, match="Unregistered fact ID 'fact_invented'"):
        validate_result(draft1, facts=facts)

    # Unregistered in claim.fact_ids
    draft2 = AnswerDraft(
        outcome="explained",
        claims=[
            Claim(
                text_template="Gross was {{fact:fact_gross}}.",
                fact_ids=["fact_gross", "fact_invented"],
            )
        ],
        selected_fact_ids=["fact_gross"],
    )
    with pytest.raises(ValueError, match="Unregistered fact ID 'fact_invented'"):
        validate_result(draft2, facts=facts)


def test_validate_result_citations():
    """Verify citations are checked for valid syntax and case scope."""
    case_source_id = uuid.uuid4()
    other_source_id = uuid.uuid4()
    valid_citations = {(case_source_id, "csv:r2")}

    facts = [
        Fact(
            fact_id="fact_gross",
            kind="money",
            value=10000,
            value_paise=10000,
        )
    ]

    # Valid citation
    draft_valid = AnswerDraft(
        outcome="explained",
        claims=[
            Claim(
                text_template="Gross was {{fact:fact_gross}}.",
                fact_ids=["fact_gross"],
                citations=[
                    Citation(
                        source_id=case_source_id,
                        locator_id="csv:r2",
                        label="Order 1",
                    )
                ],
            )
        ],
        selected_fact_ids=["fact_gross"],
    )
    res = validate_result(draft_valid, facts=facts, valid_citations=valid_citations)
    assert len(res.citations) == 1

    # Malformed locator
    draft_bad_loc = AnswerDraft(
        outcome="explained",
        claims=[
            Claim(
                text_template="Gross was {{fact:fact_gross}}.",
                fact_ids=["fact_gross"],
                citations=[
                    Citation(
                        source_id=case_source_id,
                        locator_id="invalid_locator",
                        label="Bad",
                    )
                ],
            )
        ],
        selected_fact_ids=["fact_gross"],
    )
    with pytest.raises(ValueError, match="Malformed locator ID"):
        validate_result(draft_bad_loc, facts=facts, valid_citations=valid_citations)

    # Cross-case citation
    draft_cross_case = AnswerDraft(
        outcome="explained",
        claims=[
            Claim(
                text_template="Gross was {{fact:fact_gross}}.",
                fact_ids=["fact_gross"],
                citations=[
                    Citation(
                        source_id=other_source_id,
                        locator_id="csv:r2",
                        label="Cross case",
                    )
                ],
            )
        ],
        selected_fact_ids=["fact_gross"],
    )
    with pytest.raises(ValueError, match="Invalid or cross-case citation"):
        validate_result(draft_cross_case, facts=facts, valid_citations=valid_citations)


def test_validate_result_downgrades_outcome():
    """
    Critical findings, unresolved gaps, or unreconciled status downgrade 'explained' to 'partial'.
    Model uncertainty is never upgraded.
    """
    facts = [
        Fact(
            fact_id="fact_gross",
            kind="money",
            value=10000,
            value_paise=10000,
        )
    ]

    base_draft = AnswerDraft(
        outcome="explained",
        claims=[
            Claim(
                text_template="Gross was {{fact:fact_gross}}.",
                fact_ids=["fact_gross"],
            )
        ],
        selected_fact_ids=["fact_gross"],
    )

    # 1. Clean run -> explained
    res_clean = validate_result(base_draft, facts=facts)
    assert res_clean.outcome == "explained"
    assert res_clean.claims[0].text == "Gross was INR 100."

    # 2. Critical findings -> downgraded to partial
    res_conflict = validate_result(
        base_draft, facts=facts, critical_findings=["POLICY_CONFLICT"]
    )
    assert res_conflict.outcome == "partial"

    # 3. Unresolved gap -> downgraded to partial
    draft_with_gap = AnswerDraft(
        outcome="explained",
        claims=base_draft.claims,
        selected_fact_ids=["fact_gross"],
        unresolved=[
            EvidenceGap(
                code="GAP_MISSING_DOC",
                needed_evidence="Need gateway settlement batch note",
            )
        ],
    )
    res_gap = validate_result(draft_with_gap, facts=facts)
    assert res_gap.outcome == "partial"

    # 4. Unreconciled status -> downgraded to partial
    unreconciled = Reconciliation(
        gross_paise=10000,
        completed_refunds_paise=0,
        assessed_fees_paise=0,
        received_allocations_paise=0,
        documented_pending_paise=0,
        unexplained_difference_paise=10000,
        reconciled=False,
    )
    res_unrec = validate_result(base_draft, facts=facts, reconciliation=unreconciled)
    assert res_unrec.outcome == "partial"

    # 5. Model uncertainty is never upgraded
    draft_partial = AnswerDraft(
        outcome="partial",
        claims=base_draft.claims,
        selected_fact_ids=["fact_gross"],
    )
    res_partial = validate_result(draft_partial, facts=facts)
    assert res_partial.outcome == "partial"

    draft_insufficient = AnswerDraft(
        outcome="insufficient",
        claims=[],
        selected_fact_ids=[],
    )
    res_insufficient = validate_result(draft_insufficient, facts=facts)
    assert res_insufficient.outcome == "insufficient"


def test_incomplete_total_label_preservation():
    """Fact with complete=False preserves complete=False in selected_facts."""
    incomplete_fact = Fact(
        fact_id="fact_pending",
        kind="money",
        value=3000000,
        value_paise=3000000,
        complete=False,
    )
    draft = AnswerDraft(
        outcome="partial",
        claims=[
            Claim(
                text_template="Pending was {{fact:fact_pending}}.",
                fact_ids=["fact_pending"],
            )
        ],
        selected_fact_ids=["fact_pending"],
    )
    res = validate_result(draft, facts=[incomplete_fact])
    assert len(res.selected_facts) == 1
    assert res.selected_facts[0].complete is False
