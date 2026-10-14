/**
 * MoneyTrail frontend types matching backend schemas.
 */

export type CompletenessState = 'complete' | 'unknown';

export interface CompletenessDeclaration {
  orders: CompletenessState;
  refunds: CompletenessState;
  fees: CompletenessState;
  payouts: CompletenessState;
  payout_links: CompletenessState;
  policies: CompletenessState;
}

export interface CaseSummary {
  case_id: string;
  case_key: string;
  merchant_id: string;
  currency: string;
  cohort_start: string;
  cohort_end: string;
  as_of: string;
  status: 'ready' | 'ready_with_gaps';
  completeness: CompletenessDeclaration;
  imported_at: string;
  source_count?: number;
}

export interface SourceSummary {
  source_id: string;
  relative_name: string;
  media_type: string;
  kind: string;
  sha256: string;
  size_bytes: number;
}

export interface Citation {
  source_id: string;
  locator_id: string;
  label: string;
}

export interface Fact {
  fact_id: string;
  kind: 'money' | 'date' | 'rate' | 'count' | 'string';
  value: unknown;
  value_paise?: number | null;
  currency?: string | null;
  complete: boolean;
  citations: Citation[];
  derivation: Record<string, unknown>;
}

export interface Claim {
  text_template: string;
  text?: string | null;
  fact_ids: string[];
  citations: Citation[];
}

export interface EvidenceGap {
  code: string;
  needed_evidence: string;
  citations: Citation[];
}

export type OutcomeState = 'explained' | 'partial' | 'insufficient';

export interface ReconciliationTotals {
  gross_paise: number;
  completed_refunds_paise: number;
  assessed_fees_paise: number;
  expected_net_paise: number;
  received_allocations_paise: number;
  documented_pending_paise: number;
  unexplained_difference_paise: number;
  reconciled: boolean;
  order_counts?: Record<string, number>;
  gross_complete?: boolean;
  refunds_complete?: boolean;
  fees_complete?: boolean;
  received_complete?: boolean;
  pending_complete?: boolean;
}

export interface InvestigationResult {
  run_id: string;
  case_id: string;
  cohort_start: string;
  cohort_end: string;
  as_of: string;
  outcome: OutcomeState;
  claims: Claim[];
  selected_facts: Fact[];
  reconciliation?: ReconciliationTotals | null;
  citations: Citation[];
  unresolved: EvidenceGap[];
  budget_summary?: Record<string, unknown>;
  trace_summary?: Record<string, unknown>;
}

export interface RunEvent {
  run_id: string;
  sequence: number;
  timestamp: string;
  kind: string;
  elapsed_ms: number;
  payload: Record<string, unknown>;
}

export interface RunStateResponse {
  run_id: string;
  case_id: string;
  state: 'running' | 'completed' | 'failed' | 'cancelled';
  result?: InvestigationResult | null;
  error?: { code: string; message: string; details?: Record<string, unknown> } | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface RunEventsResponse {
  events: RunEvent[];
  next_after: number;
  terminal: boolean;
}

export interface SourceSegment {
  source_id: string;
  locator_id: string;
  media_type: string;
  content: string;
  line_start?: number | null;
  line_end?: number | null;
  page?: number | null;
  row_index?: number | null;
}

export interface ImportResult {
  reused: boolean;
  case_id: string;
  case_key: string;
  bundle_sha256: string;
  orders_count: number;
  sources_count: number;
  status: string;
}
