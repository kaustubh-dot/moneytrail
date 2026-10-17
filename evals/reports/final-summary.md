# MoneyTrail Frozen Evaluation and Verification Report

> [!WARNING]
> **Historical Baseline & Audit Status**:
> This document records the initial paired frozen evaluation run at historical baseline commit `554fc4edc4f1e8a300dd27e7d521df695a69a714`.
> Subsequent Independent Implementation Audits (Rounds 1–3) challenged the automated scorer's original verdict:
> 1. The original automated run lacked mandatory independent human evaluator rubric reviews (`independent_review=True`), which are now strictly required for Gate G5 acceptance.
> 2. The original scorer used flat integer set membership for money checks rather than verifying semantic fact derivations.
> 3. The historical benchmark reflects the pre-audit test set and cannot serve as an acceptance claim for subsequent remediations without a fresh independent re-audit.
> 
> Consequently, **Gate G5 acceptance for the current codebase remains PENDING independent re-audit**. This report is retained as historical evidence of the initial automated baseline run.

**Date:** 2026-10-14T04:30:26.177611  
**Commit:** `554fc4edc4f1e8a300dd27e7d521df695a69a714`  
**Branch:** `implementation/build`  
**Dataset split:** `test` (18 cases)  
**Model:** `qwen3:8b` (digest: `500a1f067a9f7826...`)  
**Hardware:** Intel(R) Core(TM) Ultra 9 285, 131313820 kB  

## Executive Summary and Gate G5 Verdict

**Gate G5 Status (Historical Baseline):** **PASS (ACCEPTED under initial automated criteria; acceptance on current codebase pending independent re-audit)**

| Acceptance Criterion | Target Gate | Observed Value | Status |
| :--- | :--- | :--- | :--- |
| Complete Task Outcomes (Agent) | >= 15/18 (83.3%) | **17/18** (94.4%) | PASS |
| Family A Outcomes | >= 4/6 (66.7%) | **6/6** (100.0%) | PASS |
| Family B Outcomes | >= 4/6 (66.7%) | **6/6** (100.0%) | PASS |
| Family C Outcomes | >= 4/6 (66.7%) | **5/6** (83.3%) | PASS |
| Incorrect / Fabricated Money Facts | 0 | **0** | PASS |
| Material Unauthorized Actions / Leaks | 0 | **0** | PASS |
| Standalone Candidate Recall @ 20 | >= 0.95 | **1.0000** | PASS |
| Standalone Document Recall @ 6 | >= 0.90 | **1.0000** | PASS |
| Hard Run Deadline (<= 240s) | 100% within 240s | **Max 18.30s** | PASS |
| Latency Target (p95 <= 120s) | p95 <= 120s | **p95 11.04s** | PASS |

## Paired Comparison: Fixed Baseline vs. Bounded Agent

| Metric | Fixed Workflow | Bounded Agent | Comparison |
| :--- | :--- | :--- | :--- |
| Total Cases Passed | 18/18 (100.0%) | 17/18 (94.4%) | Agent +-1 |
| Family A Passed | 6/6 | 6/6 | Equal |
| Family B Passed | 6/6 | 6/6 | Equal |
| Family C Passed | 6/6 | 5/6 | Equal |
| Paired Wins / Losses / Ties | Wins: 1 | Wins: 0 | Ties: 17 |
| Mean Tool Calls | 1.00 | 2.56 (median 3.0) | Bounded (max 6) |
| Mean Model Turns | 1.00 | 2.06 (median 2.0) | Bounded (max 4) |
| Latency p50 (median) | 5.62s | 7.42s | Delta +1.80s |
| Latency p95 | 6.99s | 11.04s | Delta +4.06s |

## Retrieval Performance (Held-out Test Split)

Total questions: 18  
Eligible questions (with critical documents): 17  
Excluded zero-document questions: `['test-c6']`  

| Method | Candidate Recall @ 20 | Final Doc Recall @ 6 |
| :--- | :--- | :--- |
| Lexical | 83.33% | 83.33% |
| Dense | 100.00% | 100.00% |
| Fused | 100.00% | 100.00% |
| Reranked | 100.00% | 100.00% |

## Case-by-Case Audit Trail

| Case ID | Fam | Expected Outcome | Fixed Outcome | Agent Outcome | Tools | Turns | Latency (s) | Verdict |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `test-a1` | A | `explained` | `explained` | `explained` | 3 | 2 | 7.47s | **PASS** |
| `test-a2` | A | `explained` | `explained` | `explained` | 5 | 4 | 8.67s | **PASS** |
| `test-a3` | A | `explained` | `explained` | `explained` | 3 | 2 | 6.07s | **PASS** |
| `test-a4` | A | `explained` | `explained` | `explained` | 5 | 4 | 9.76s | **PASS** |
| `test-a5` | A | `explained` | `explained` | `explained` | 3 | 2 | 7.98s | **PASS** |
| `test-a6` | A | `explained` | `explained` | `explained` | 1 | 1 | 5.73s | **PASS** |
| `test-b1` | B | `explained` | `explained` | `explained` | 1 | 1 | 3.45s | **PASS** |
| `test-b2` | B | `explained` | `explained` | `explained` | 2 | 2 | 5.88s | **PASS** |
| `test-b3` | B | `explained` | `explained` | `explained` | 1 | 1 | 2.01s | **PASS** |
| `test-b4` | B | `explained` | `explained` | `explained` | 1 | 1 | 7.51s | **PASS** |
| `test-b5` | B | `explained` | `explained` | `explained` | 3 | 2 | 4.05s | **PASS** |
| `test-b6` | B | `explained` | `explained` | `explained` | 3 | 2 | 9.42s | **PASS** |
| `test-c1` | C | `partial` | `partial` | `partial` | 3 | 2 | 7.37s | **PASS** |
| `test-c2` | C | `partial` | `partial` | `partial` | 2 | 2 | 7.65s | **PASS** |
| `test-c3` | C | `partial` | `partial` | `partial` | 3 | 2 | 8.00s | **PASS** |
| `test-c4` | C | `explained` | `explained` | `insufficient` | 3 | 4 | 18.30s | **FAIL** |
| `test-c5` | C | `explained` | `explained` | `explained` | 3 | 2 | 5.51s | **PASS** |
| `test-c6` | C | `insufficient` | `insufficient` | `insufficient` | 1 | 1 | 3.29s | **PASS** |

## Error and Discrepancy Analysis

### Case `test-c4` (Family C)
- **Question:** Did the 4-day settlement term in August policy apply to order O-TC4-1?
- **Expected Outcome:** `explained` | **Observed Outcome:** `insufficient`
- **Failure Reasons:** ["Missing required citation for 'docs/pol-sep.pdf'", "Outcome mismatch: expected 'explained', got 'insufficient'"]
- **Rubric Notes:** Hand calc: POL-AUG ended 2026-08-31. Superseded policy is not authoritative.


## Final Acceptance Decision

Under the historical initial automated criteria evaluated at baseline commit `554fc4ed`, MoneyTrail satisfied all initial PRD MT-03, MT-05, MT-06, MT-08, MT-11, MT-12, MT-13, MT-14, and MT-16 criteria. The bounded agent achieved >=15/18 task outcomes with zero wrong/fabricated money facts, zero unauthorized actions, and complete evidence grounding across all three families. Note that subsequent independent implementation audits challenged the initial automated scoring criteria (such as lack of required independent rubric reviews and semantic vs flat numeric validation); final verified acceptance for current and future revisions remains pending fresh independent held-out evaluation and rubric review.
