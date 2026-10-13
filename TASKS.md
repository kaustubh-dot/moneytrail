# MoneyTrail task status and handoff

Updated 2026-10-13. Current state: documentation-only planning pack. Application implementation has not started; no runtime test, fixture generation, model measurement, evaluation, CI run, deployment, or phase approval is claimed here. Repository/scaffold operations are separate from application completion.

[PRD.md](PRD.md) owns vision; [ARCHITECTURE.md](ARCHITECTURE.md) owns contracts; [EVALUATION.md](EVALUATION.md) owns verification; [ROADMAP.md](ROADMAP.md) owns exact ordered instructions. This file is the sole canonical checkbox/status/approval ledger.

## Current position and next action

Current phase: **Phase 0 — Scope, data, and model feasibility**. Exact next task: **MT0.3 — Generate/review synthetic evidence, independent gold, and v1 scope**. Batch implementation authorization recorded.

Blockers: **none**. Docker missing and sudo requires password; pgvector PostgreSQL-16 registry digest resolved for phase 1. Model feasibility passed (10/10 probes, ~3.8s warm latency).

## Canonical task list

A checkbox means the deliverable and minimal verification passed and evidence was recorded below. It does not authorize a dependent phase. Stop once mapped acceptance is met: no speculative features, extra services, coverage quotas, broad serving matrices, repeated review cycles, or additional tests without an actual failure or active PRD need.

### Phase 0 — Scope, data, and model feasibility

- [x] **MT0.1** Lock runtime dependencies/configuration. PRD: MT-13, MT-15, MT-16. Depends on implementation authorization.
- [x] **MT0.2** Measure exact local model capability/context/performance. PRD: MT-08, MT-09, MT-13. Depends on MT0.1.
- [ ] **MT0.3** Generate/review synthetic evidence, independent gold, and v1 scope. PRD: MT-01, MT-03, MT-07, MT-12, MT-16. Depends on MT0.1–MT0.2.

### Phase 1 — Safe storage and real uploads

- [ ] **MT1.1** Establish schema, migrations, and restricted database roles. PRD: MT-03, MT-04, MT-11, MT-15. Depends on G0 approval.
- [ ] **MT1.2** Validate supported bundles and atomic imports/deduplication. PRD: MT-01, MT-02, MT-03, MT-11. Depends on MT1.1.
- [ ] **MT1.3** Extract/index complete cases with source locators. PRD: MT-01, MT-02, MT-05, MT-07. Depends on MT1.2.
- [ ] **MT1.4** Expose authenticated case/source APIs and security checks. PRD: MT-01, MT-02, MT-04, MT-11. Depends on MT1.3.

### Phase 2 — Complete first investigation workflow

- [ ] **MT2.1** Implement integer reconciliation and record inspection. PRD: MT-03, MT-04, MT-06. Depends on G1 approval.
- [ ] **MT2.2** Resolve effective policies and validate grounded result facts/citations. PRD: MT-02, MT-06, MT-07. Depends on MT2.1.
- [ ] **MT2.3** Run live fixed investigations through authenticated APIs. PRD: MT-04, MT-06, MT-08, MT-09, MT-14. Depends on MT2.2.
- [ ] **MT2.4** Complete upload/question/result/source UI and first live demo. PRD: MT-01, MT-02, MT-10, MT-11, MT-14. Depends on MT2.3.

### Phase 3 — Hybrid retrieval and richer evidence access

- [ ] **MT3.1** Fuse exact vector/full-text retrieval and rerank evidence. PRD: MT-02, MT-05, MT-07, MT-12. Depends on G2 approval.
- [ ] **MT3.2** Complete the typed evidence dispatcher/source/order access. PRD: MT-02, MT-04, MT-06, MT-09, MT-11. Depends on MT3.1.
- [ ] **MT3.3** Record fixed development baseline and retrieval findings. PRD: MT-05, MT-06, MT-12, MT-13. Depends on MT3.2.

### Phase 4 — Bounded investigator, controls, and trace

- [ ] **MT4.1** Add the explicit bounded LangGraph investigator. PRD: MT-04, MT-06, MT-08. Depends on G3 approval.
- [ ] **MT4.2** Enforce actual context/tool/model/time budgets. PRD: MT-08, MT-09, MT-13. Depends on MT4.1.
- [ ] **MT4.3** Complete cancellation, errors, and source boundaries. PRD: MT-08, MT-10, MT-11, MT-14. Depends on MT4.2.
- [ ] **MT4.4** Complete local trace/UI and development quality review. PRD: MT-06, MT-10, MT-12, MT-14. Depends on MT4.3.

### Phase 5 — Freeze, compare, and judge results

- [ ] **MT5.1** Finalize scoring/gold and freeze evaluated inputs. PRD: MT-03, MT-05, MT-06, MT-11, MT-12. Depends on G4 approval.
- [ ] **MT5.2** Run paired held-out and retrieval evaluations. PRD: MT-05, MT-06, MT-08, MT-12, MT-13, MT-14. Depends on MT5.1.
- [ ] **MT5.3** Publish all errors and the acceptance decision. PRD: MT-03, MT-06, MT-11, MT-12, MT-16. Depends on MT5.2.

### Phase 6 — Reproducible delivery and demonstration

- [ ] **MT6.1** Package monolith/database and verify Linux local networking. PRD: MT-11, MT-13, MT-15. Depends on G5 approval.
- [ ] **MT6.2** Add offline CI and prove clean-clone live setup. PRD: MT-01, MT-10, MT-11, MT-15. Depends on MT6.1.
- [ ] **MT6.3** Finish demo/interview handoff and PRD review. PRD: MT-10, MT-12, MT-14, MT-15, MT-16. Depends on MT6.2.

## Gate ledger

| Gate | Required evidence | Verification | User approval |
| --- | --- | --- | --- |
| G0 | Reviewed scope, locked identities, measured feasibility, independent valid fixtures/gold | Pending | Not recorded |
| G1 | Atomic actual import, duplicates, source locators, authenticated scoped API/read-only role | Pending | Not recorded |
| G2 | Live uploaded case → original question → exact totals/explanation → CSV/PDF/text citations | Pending | Not recorded |
| G3 | Hybrid retrieval/read tools and fixed development baseline | Pending | Not recorded |
| G4 | Bounded investigator, context, cancel/error/security/trace behavior, development review | Pending | Not recorded |
| G5 | Frozen paired evaluation, monetary/security/outcome/retrieval gates, complete error report | Pending | Not recorded |
| G6 | Clean-clone live demo, actual CI result, accurate docs/interview handoff, final PRD review | Pending | Not recorded |

Implementation-start authorization: **Recorded (explicit batch authorization for Phases 0 through 6 in order with per-phase review-and-fix gate; 2026-10-13)**. Planning/repository authorization does not authorize application phases. Record actual user approval scope/date at each gate; never treat silence, a passing test, or a commit as approval. A user's explicit batch authorization of named phases may be recorded, but must not be inferred.

## Verification and checkpoint ledger

- **MT0.2 (2026-10-13)**: Implemented `src/moneytrail/llm.py` with native `complete_structured`, AsyncClient, and `pilot` CLI. Model candidate Ollama `qwen3:8b` (digest `500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`, Q4_K_M, Apache-2.0, native `think=false` verified). Pinned revisions: tokenizer `b968826d9c46dd6066d109eabc6255188de91218`, embedding `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, reranker `233902d25c440f23af6f7d6e94d2946bac0bee0a`. Executed `uv run python -m moneytrail.llm pilot --output evals/reports/phase0.json`: 10/10 contract probes parsed and passed (0 illegal dispatches); 5 warm requests measured ~3.8s latency; near-budget (~6,000 token) probe passed in 5.8s within 8,192 context window; offline provider check returned expected `MODEL_UNAVAILABLE`. Pilot gate: PASS. Next action: MT0.3 — Generate synthetic evidence and gold cases.
- **MT0.1 (2026-10-13)**: Installed Python 3.12.3, uv 0.11.27, Node v24.19.0, npm 9.2.0, Ollama 0.40.3; hardware measured: Intel Core Ultra 9 285, 125 GiB RAM; recorded pgvector Docker image `pgvector/pgvector:pg16` digest `sha256:a97d77306ff47cc1d8add4d000cec2207da2e110746318df3137ce53c337ca30`. Generated ignored `.env` and `.env.setup` with local secrets via `secrets.token_urlsafe(32)`. Verified imports via `uv run python -c "import fastapi,pydantic,psycopg,ollama,langgraph,sentence_transformers,pypdf; print('imports-ok')"` -> `imports-ok`. Verified Settings missing-token failure behavior. Verified frontend TypeScript check via `npm --prefix frontend run typecheck` (0 errors). Next action: MT0.2 — Measure local model candidate.

No implementation verification entries exist prior to MT0.1. Append one compact entry per meaningful checkpoint with task IDs, date, exact commands, actual outcomes/report paths, commit hash, remaining limits, and next action. Preserve failed checks and their causes; do not copy expected command output from ROADMAP as observed success.

The planning pack has received a document consistency review. This is not evidence that imports, model calls, tests, or deployment work.

Planning audit, 2026-10-13: verified seven phases, 24 roadmap tasks represented in this ledger, all 16 PRD IDs mapped, required files present, local document links resolving, and configuration names documented. Reviewed the financial/evidence contracts and phase dependencies. Credential-pattern and file-allowlist checks found no unexpected content. Application tests were not run because no application has been implemented. Simplicity during coding and proportionate verification are binding in AGENTS.

## Decision and deviation log

Approved-amendment audit, 2026-10-13: `git diff --check` passed. Read-only document checks confirmed only the six intended Markdown files changed, all requirement rows except the approved MT-05 measurement clarification remained identical, all 24 task IDs remain represented, and local links/configuration references resolve. The diff preserves the original financial workflow, budgets, evaluation sizes/thresholds, and essential regression coverage. No application tests were added or run; implementation remains unstarted.

Approved pre-lock amendments, 2026-10-13:

- MT-05/MT-12: make ranking evaluation one standalone original-question, `kind="all"` search per eligible question/configuration. Score only ranked results; retain investigation policy-conflict coverage separately. This removes scoring ambiguity without changing thresholds, datasets, runtime tools, or infrastructure.
- MT-04/MT-08/MT-10: explicitly bound free-text questions to cohort reconciliation, timing/fee/refund explanations, policy/correspondence evidence, and identified orders. Align the demo; unsupported analytics/snapshots return insufficiency. Narrow the promise instead of adding analytics tools. Import, deterministic money, deep RAG, evidence, and the bounded investigator remain unchanged.

PRD, ARCHITECTURE, EVALUATION, ROADMAP, and README are synchronized with these decisions. Baseline decisions/references otherwise remain unchanged. Future material changes require the reason, alternatives, requirement/API/evaluation/cost impact, and explicit approval; do not silently change the model/provider, accounting contract, formats, or original vision.

## Approval ledger

2026-10-13: the user approved the three pre-lock amendments across the two flagship projects: “Approved but ensure you dont overengineer , overdo and drift apart from the original intended vision”. For this repository the approval covers only the two amendments above and corresponding documentation alignment. No implementation-start or phase approval is recorded; G0 feasibility remains unverified. Append actual phase approvals separately.

2026-10-13: The user explicitly granted batch implementation authorization for Phase 0, Phase 1, Phase 2, Phase 3, Phase 4, Phase 5, and Phase 6 in that order, replacing routine instructions to wait for manual permission between phases. Each phase requires a fresh review-and-fix subagent gate, verification by the coordinating agent, passing mandatory criteria, and recording in TASKS.md before automated continuation. No scope changes, weaker criteria, paid services, destructive operations, or fabricated acceptance are authorized. Final product acceptance remains with the user.

## Parked work

Fine-tuning is inactive and unnecessary for completion. Only measured baseline failure plus a separately approved proposal can activate it. Other PRD non-goals remain outside the roadmap unless the user approves a scope change.
