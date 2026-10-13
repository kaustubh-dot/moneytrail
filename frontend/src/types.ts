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
  imported_at: string;
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
  fact_ids: string[];
  citations: Citation[];
}

export interface EvidenceGap {
  code: string;
  needed_evidence: string;
  citations: Citation[];
}

export type OutcomeState = 'explained' | 'partial' | 'insufficient';

export interface InvestigationResult {
  run_id: string;
  case_id: string;
  cohort_start: string;
  cohort_end: string;
  as_of: string;
  outcome: OutcomeState;
  claims: Claim[];
  selected_facts: Fact[];
  reconciliation?: {
    gross_paise: number;
    completed_refunds_paise: number;
    assessed_fees_paise: number;
    received_allocations_paise: number;
    documented_pending_paise: number;
    unexplained_difference_paise: number;
    reconciled: boolean;
  } | null;
  citations: Citation[];
  unresolved: EvidenceGap[];
}
