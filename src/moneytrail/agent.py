"""Agent validation, fact expansion, and investigation execution for MoneyTrail."""

import re
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from moneytrail.schemas import (
    AnswerDraft,
    Citation,
    Claim,
    Fact,
    InvestigationResult,
    InvestigationState,
    Reconciliation,
)


def format_paise(paise: int, currency: str = "INR") -> str:
    """
    Format integer paise amount without floating-point conversion.
    e.g. 24000000 -> 'INR 240,000'
         1800050  -> 'INR 18,000.50'
         -50000   -> '-INR 500'
    """
    if not isinstance(paise, int):
        raise TypeError(f"Paise value must be an integer, got {type(paise).__name__}")
    sign = "-" if paise < 0 else ""
    abs_paise = abs(paise)
    rupees = abs_paise // 100
    rem = abs_paise % 100
    if rem == 0:
        return f"{sign}{currency} {rupees:,}"
    else:
        return f"{sign}{currency} {rupees:,}.{rem:02d}"


_CURRENCY_PREFIX_RE = re.compile(
    r"(?i)(?:\b(?:INR|Rs\.?|Rupees?)|₹)\s*\d+(?:,\d+)*(?:\.\d+)?"
)
_CURRENCY_SUFFIX_RE = re.compile(
    r"(?i)\b\d+(?:,\d+)*(?:\.\d+)?\s*(?:(?:INR|Rs\.?|Rupees?|paise)\b|₹)"
)
_FACT_TOKEN_RE = re.compile(r"\{\{([^}]+)\}\}")
_LOCATOR_SYNTAX_RE = re.compile(
    r"^(csv:r\d+|pdf:p\d+:l\d+(?:-\d+)?|text:l\d+(?:-\d+)?)$"
)


def check_currency_literals(template: str) -> None:
    """
    Ensure no obvious hardcoded currency-plus-number literals exist outside fact tokens.
    Allows ordinary words such as 'one', 'second', 'two'.
    """
    # Strip valid fact tokens first
    stripped = re.sub(r"\{\{fact:[A-Za-z0-9_]+\}\}", "", template)

    m = _CURRENCY_PREFIX_RE.search(stripped)
    if m:
        raise ValueError(
            f"Obvious currency literal found outside fact token: '{m.group(0)}' in template: '{template}'"
        )
    m = _CURRENCY_SUFFIX_RE.search(stripped)
    if m:
        raise ValueError(
            f"Obvious currency literal found outside fact token: '{m.group(0)}' in template: '{template}'"
        )


def expand_fact_tokens(template: str, facts_by_id: dict[str, Fact]) -> str:
    """
    Expand {{fact:<fact_id>}} tokens in template with formatted fact values.
    Validates token syntax and checks that referenced facts are registered.
    """
    check_currency_literals(template)

    def _replace_token(match: re.Match) -> str:
        token_body = match.group(1).strip()
        if not token_body.startswith("fact:"):
            raise ValueError(
                f"Malformed token '{{{{{token_body}}}}}': expected 'fact:<id>' format"
            )
        fact_id = token_body[len("fact:") :].strip()
        if not fact_id or not re.match(r"^[A-Za-z0-9_]+$", fact_id):
            raise ValueError(f"Malformed fact ID in token '{{{{{match.group(0)}}}}}'")
        if fact_id not in facts_by_id:
            raise ValueError(
                f"Unregistered fact ID '{fact_id}' referenced in token '{{{{{match.group(0)}}}}}'"
            )

        fact = facts_by_id[fact_id]
        if fact.kind == "money":
            val = fact.value_paise if fact.value_paise is not None else fact.value
            return format_paise(val, fact.currency or "INR")
        elif fact.kind == "rate":
            return str(fact.value)
        elif fact.kind == "date":
            return str(fact.value)
        elif fact.kind == "count":
            return str(fact.value)
        else:
            return str(fact.value)

    expanded = _FACT_TOKEN_RE.sub(_replace_token, template)
    return expanded


def validate_citations(
    citations: list[Citation],
    valid_citations: set[tuple[UUID, str]] | None = None,
) -> None:
    """
    Validate citation locators and case scope.
    Rejects malformed locators and citations not belonging to the case.
    """
    for cit in citations:
        if not _LOCATOR_SYNTAX_RE.match(cit.locator_id):
            raise ValueError(f"Malformed locator ID: '{cit.locator_id}'")
        if valid_citations is not None and len(valid_citations) > 0:
            if (cit.source_id, cit.locator_id) not in valid_citations:
                raise ValueError(
                    f"Invalid or cross-case citation: source_id={cit.source_id}, locator_id='{cit.locator_id}'"
                )


def validate_result(
    draft: AnswerDraft,
    state: InvestigationState | None = None,
    *,
    facts: list[Fact] | None = None,
    valid_citations: set[tuple[UUID, str]] | None = None,
    critical_findings: list[str] | None = None,
    run_id: UUID | None = None,
    case_id: UUID | None = None,
    cohort_start: datetime | None = None,
    cohort_end: datetime | None = None,
    as_of: datetime | None = None,
    reconciliation: Reconciliation | None = None,
) -> InvestigationResult:
    """
    Validate model AnswerDraft, expanding fact tokens without floating point conversion,
    enforcing grounding rules, rejecting unregistered facts/invalid citations, and
    downgrading uncertainty if critical findings remain.
    """
    # 1. Resolve context from state or direct arguments
    if state is not None:
        run_id = run_id or state.run_id
        case_id = case_id or state.case_id
        cohort_start = cohort_start or state.cohort_start or datetime.now()
        cohort_end = cohort_end or state.cohort_end or datetime.now()
        as_of = as_of or state.as_of or datetime.now()
        facts = facts or state.facts
        valid_citations = valid_citations or state.valid_citations
        reconciliation = reconciliation or state.reconciliation
        if critical_findings is None:
            critical_findings = [
                f["code"]
                for f in state.findings
                if f.get("severity") in ("gap", "conflict")
                or f.get("code") in ("POLICY_CONFLICT", "MISSING_POLICY")
            ]
    else:
        run_id = run_id or uuid.uuid4()
        case_id = case_id or uuid.uuid4()
        cohort_start = cohort_start or datetime.now()
        cohort_end = cohort_end or datetime.now()
        as_of = as_of or datetime.now()
        facts = facts or []
        critical_findings = critical_findings or []

    facts_by_id: dict[str, Fact] = {f.fact_id: f for f in facts}

    # 2. Check draft.selected_fact_ids are registered
    for fid in draft.selected_fact_ids:
        if fid not in facts_by_id:
            raise ValueError(
                f"Unregistered fact ID '{fid}' referenced in draft.selected_fact_ids"
            )

    # 3. Validate claims
    validated_claims: list[Claim] = []
    seen_citations: list[Citation] = []
    seen_citation_keys: set[tuple[UUID, str]] = set()

    for claim in draft.claims:
        # Check claim.fact_ids
        for fid in claim.fact_ids:
            if fid not in facts_by_id:
                raise ValueError(
                    f"Unregistered fact ID '{fid}' referenced in claim.fact_ids"
                )

        # Validate citations
        validate_citations(claim.citations, valid_citations)
        for cit in claim.citations:
            cit_key = (cit.source_id, cit.locator_id)
            if cit_key not in seen_citation_keys:
                seen_citation_keys.add(cit_key)
                seen_citations.append(cit)

        # Expand tokens and check currency literals
        expanded_text = expand_fact_tokens(claim.text_template, facts_by_id)

        validated_claims.append(
            Claim(
                text_template=claim.text_template,
                text=expanded_text,
                fact_ids=claim.fact_ids,
                citations=claim.citations,
            )
        )

    # Add citations from selected facts
    selected_facts = [facts_by_id[fid] for fid in draft.selected_fact_ids]
    for sf in selected_facts:
        for cit in sf.citations:
            cit_key = (cit.source_id, cit.locator_id)
            if cit_key not in seen_citation_keys:
                seen_citation_keys.add(cit_key)
                seen_citations.append(cit)

    # 4. Check outcome and enforce uncertainty
    outcome = draft.outcome
    has_critical_findings = len(critical_findings) > 0
    has_unresolved_gaps = len(draft.unresolved) > 0
    has_reconciliation_gap = (
        reconciliation is not None and not reconciliation.reconciled
    )

    if outcome == "explained":
        if has_critical_findings or has_unresolved_gaps or has_reconciliation_gap:
            # Downgrade to partial
            outcome = "partial"

    # Server must never upgrade model uncertainty:
    # if draft.outcome in ("partial", "insufficient"), keep draft.outcome

    return InvestigationResult(
        run_id=run_id,
        case_id=case_id,
        cohort_start=cohort_start,
        cohort_end=cohort_end,
        as_of=as_of,
        outcome=outcome,
        claims=validated_claims,
        selected_facts=selected_facts,
        reconciliation=reconciliation,
        citations=seen_citations,
        unresolved=draft.unresolved,
        budget_summary={},
        trace_summary={},
    )
