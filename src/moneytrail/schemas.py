"""Pydantic schemas and interface contracts for MoneyTrail."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import BaseModel, Field

# --- Manifest and Import Schemas ---

class CompletenessDeclaration(BaseModel):
    orders: Literal["complete", "unknown"]
    refunds: Literal["complete", "unknown"]
    fees: Literal["complete", "unknown"]
    payouts: Literal["complete", "unknown"]
    payout_links: Literal["complete", "unknown"]
    policies: Literal["complete", "unknown"]


class ManifestFileEntry(BaseModel):
    path: str
    sha256: str
    kind: Literal[
        "orders",
        "refunds",
        "fees",
        "payouts",
        "payout_links",
        "policy",
        "correspondence",
    ]


class Manifest(BaseModel):
    schema_version: Literal[1]
    case_key: str
    merchant_id: Literal["meridian_store"]
    currency: Literal["INR"]
    timezone: Literal["Asia/Kolkata"]
    cohort_start: datetime
    cohort_end: datetime
    as_of: datetime
    completeness: CompletenessDeclaration
    files: list[ManifestFileEntry]


class CaseFinding(BaseModel):
    code: str
    severity: Literal["info", "gap", "conflict"]
    affected_record_ids: list[str] = Field(default_factory=list)
    citations: list["Citation"] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ImportResult(BaseModel):
    case_id: UUID
    status: Literal["ready", "ready_with_gaps"]
    fingerprint: str
    reused: bool
    record_counts: dict[str, int]
    duplicate_findings: list[dict[str, Any]] = Field(default_factory=list)
    evidence_findings: list[dict[str, Any]] = Field(default_factory=list)


# --- Citation and Provenance Schemas ---

class Citation(BaseModel):
    source_id: UUID
    locator_id: str
    label: str


class SourceSegment(BaseModel):
    source_id: UUID
    locator_id: str
    text: str
    media_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResultItem(BaseModel):
    chunk_id: UUID
    source_id: UUID
    locator_id: str
    text: str
    score: float
    rank: int
    kind: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    items: list[SearchResultItem]
    total: int
    metadata: dict[str, Any] = Field(default_factory=dict)


# --- Deterministic Facts and Investigation Tools ---

class Fact(BaseModel):
    fact_id: str
    kind: Literal["money", "date", "rate", "count", "string"]
    value: Any
    value_paise: int | None = None
    currency: str | None = "INR"
    complete: bool = True
    citations: list[Citation] = Field(default_factory=list)
    derivation: dict[str, Any] = Field(default_factory=dict)


class TotalAmount(BaseModel):
    value_paise: int
    complete: bool
    source_ids: list[str] = Field(default_factory=list)


class Reconciliation(BaseModel):
    gross_paise: int
    completed_refunds_paise: int
    assessed_fees_paise: int
    received_allocations_paise: int
    documented_pending_paise: int
    unexplained_difference_paise: int
    reconciled: bool
    facts: list[Fact] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    details_omitted: bool = False
    order_counts: dict[str, int] = Field(default_factory=dict)


class OrderEvidence(BaseModel):
    order_id: str
    captured_at: datetime
    gross_paise: int
    settlement_state: str
    refunds: list[dict[str, Any]] = Field(default_factory=list)
    fees: list[dict[str, Any]] = Field(default_factory=list)
    links: list[dict[str, Any]] = Field(default_factory=list)
    expected_net_paise: int
    citations: list[Citation] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)


class PolicyEvidence(BaseModel):
    applicable_policies: list[dict[str, Any]] = Field(default_factory=list)
    conflicting_policies: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)


# --- Tool Call Definitions ---

class ReconcileCall(BaseModel):
    action: Literal["reconcile"] = "reconcile"


class InspectOrderCall(BaseModel):
    action: Literal["inspect_order"] = "inspect_order"
    order_id: str


class SelectPolicyCall(BaseModel):
    action: Literal["select_policy"] = "select_policy"
    order_ids: list[str] | None = None


class SearchEvidenceCall(BaseModel):
    action: Literal["search_evidence"] = "search_evidence"
    query: str
    kind: Literal["all", "policy", "correspondence"] = "all"
    limit: int = 6


class ReadSourceCall(BaseModel):
    action: Literal["read_source"] = "read_source"
    source_id: UUID
    locator_id: str


# Discriminated union for ToolCall
class ToolCall(BaseModel):
    action: Literal["reconcile", "inspect_order", "select_policy", "search_evidence", "read_source"]
    order_id: str | None = None
    order_ids: list[str] | None = None
    query: str | None = None
    kind: Literal["all", "policy", "correspondence"] | None = None
    limit: int | None = None
    source_id: UUID | None = None
    locator_id: str | None = None


class ToolResult(BaseModel):
    tool_call_id: str
    action: str
    data: dict[str, Any]
    facts: list[Fact] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    elapsed_ms: int = 0


# --- Model Decision and Structured Answer Draft ---

class Claim(BaseModel):
    text_template: str
    text: str | None = None
    fact_ids: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


class EvidenceGap(BaseModel):
    code: str
    needed_evidence: str
    citations: list[Citation] = Field(default_factory=list)


class AnswerDraft(BaseModel):
    outcome: Literal["explained", "partial", "insufficient"]
    claims: list[Claim] = Field(default_factory=list)
    selected_fact_ids: list[str] = Field(default_factory=list)
    unresolved: list[EvidenceGap] = Field(default_factory=list)


class Decision(BaseModel):
    action: Literal["tools", "answer"]
    calls: list[ToolCall] | None = None
    answer: AnswerDraft | None = None


# --- Investigation Runs and Public APIs ---

class InvestigationRequest(BaseModel):
    case_id: UUID
    question: str


class InvestigationResult(BaseModel):
    run_id: UUID
    case_id: UUID
    cohort_start: datetime
    cohort_end: datetime
    as_of: datetime
    outcome: Literal["explained", "partial", "insufficient"]
    claims: list[Claim]
    selected_facts: list[Fact]
    reconciliation: Reconciliation | None = None
    citations: list[Citation]
    unresolved: list[EvidenceGap]
    budget_summary: dict[str, Any] = Field(default_factory=dict)
    trace_summary: dict[str, Any] = Field(default_factory=dict)


class RunEvent(BaseModel):
    run_id: UUID
    sequence: int
    timestamp: datetime
    kind: str
    elapsed_ms: int
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class InvestigationState(BaseModel):
    run_id: UUID
    case_id: UUID
    question: str
    cohort_start: datetime | None = None
    cohort_end: datetime | None = None
    as_of: datetime | None = None
    phase: str = "init"
    tool_calls_used: int = 0
    model_turns_used: int = 0
    deadline: datetime | None = None
    cancelled: bool = False
    facts: list[Fact] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    findings: list[dict[str, Any]] = Field(default_factory=list)
    unresolved: list[EvidenceGap] = Field(default_factory=list)
    selected_sources: list[UUID] = Field(default_factory=list)
    last_decision: Decision | None = None
    valid_citations: set[tuple[UUID, str]] = Field(default_factory=set)
    reconciliation: Reconciliation | None = None


class ContextPacket(BaseModel):
    messages: list[ChatMessage]
    token_count: int
    omitted_chunks: int = 0
