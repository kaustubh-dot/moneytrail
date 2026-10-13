# MoneyTrail implementation plan

> For agentic workers: REQUIRED SUB-SKILL at implementation time: use `executing-plans` to execute this plan task by task. `subagent-driven-development` is an alternative only with explicit delegation authorization. The user requested this planning pack, not application implementation. Canonical completion checkboxes live in TASKS.md; the instructions here use numbered steps. The user's focused-testing and minimal-documentation requirements override generic skill requests for exhaustive test boilerplate or additional plan files.

**Goal:** Build the source-backed merchant payment discrepancy investigator in [PRD.md](PRD.md), including actual supported upload, deterministic money, free-text investigation, sources, and measured local operation.

**Architecture:** One FastAPI application, PostgreSQL/pgvector database, local Ollama provider, thin React UI, and bounded LangGraph investigator. Establish a complete fixed-workflow vertical slice before expanding retrieval and adaptive tool selection.

**Tech stack:** Python 3.12, FastAPI, Pydantic 2, psycopg, PostgreSQL 16/pgvector, pypdf, sentence-transformers, Transformers tokenizer, native Ollama Python client, LangGraph, React/TypeScript/Vite, uv/npm locks, and final minimal Docker/Compose plus offline GitHub Actions.

**Spec:** PRD is the vision/scope source; [ARCHITECTURE.md](ARCHITECTURE.md) owns formats, schema, APIs, defaults, and function contracts; [EVALUATION.md](EVALUATION.md) owns measurements; [TASKS.md](TASKS.md) alone owns status and approvals.

## Global execution contract

Seven sequential phases contain 24 meaningful tasks. Before each session read AGENTS, PRD, this roadmap, TASKS, and affected code. Application code, downloads, database provisioning, and runtime tests are not authorized merely because a future command appears here. Once implementation is authorized, execute the current phase and stop at its review gate.

Use integer paise, explicit evidence completeness, scoped read tools, safe sources, one inference provider, one active run, and clear failures. No silent fallback, money movement, unrestricted SQL, queues, multi-agent application, paid service, or speculative fine-tuning. Use the named architecture types/functions rather than inventing parallel interfaces. Apply the twelve essential regressions where indicated; no coverage target or test per formatting edit. Follow AGENTS' simplicity rules during coding, ask briefly “Can this be simpler without losing required behavior?”, and stop when mapped acceptance is met. This is not an extra optimization/review loop; every proposed addition needs an active PRD reason.

Commands are planned Linux/Bash reference commands from the repository root. Execute them only after the task creates referenced files and the preceding gate is approved. `uv lock` must precede the first `uv sync --frozen`; `npm install` creates the initial lock before `npm ci`; tests run after their files exist. Python/frontend/model versions and host fitness are measured in phase 0, not assumed here.

MT0.1 creates ignored `.env` runtime values and `.env.setup` setup-only values using the exact loading/secret-generation convention in ARCHITECTURE. After creation, `export UV_ENV_FILE=.env` makes the ordinary uv commands below load runtime configuration. Admin setup/database-test commands explicitly load both files; the runtime API never receives setup secrets. No dotenv framework is added.

Each task ends with actual verification evidence in TASKS. Each phase ends with focused diff review, a Git checkpoint, and **STOP for user review and approval** before dependent work. Use the existing independent repository. Inspect `git status --short`, stage exact intended paths from that phase's file lists, inspect the staged diff, then use its commit message. Never stage `.env`, model weights, runtime imports, or unrelated edits. These are future implementation checkpoint instructions; no Git action is required to write this plan. A passing task is not approval to begin another phase.

## Phase 0 — Scope, data, and model feasibility

**Objective/deliverable:** Reviewed scope, exact dependency/model identities, measured feasibility report, and valid synthetic evidence/gold cases. **PRD:** MT-01, MT-03, MT-07, MT-12, MT-13, MT-15, MT-16. **Prerequisite:** Explicit implementation authorization; prerequisite availability remains unverified.

### MT0.1 — Lock runtime dependencies and configuration

**PRD:** MT-13, MT-15, MT-16. **Files:** Create `pyproject.toml`, `uv.lock`, `src/moneytrail/__init__.py`, `config.py`, `schemas.py`, `frontend/package.json`, `package-lock.json`, `tsconfig.json`; update `.env.example`/README only with verified prerequisite information. **Interfaces:** Runtime `Settings`, separate setup-only admin settings, and the architecture's Pydantic import/fact/run/decision schemas.

1. Inspect installed Python, uv, Node, npm, Docker, and Ollama versions using their version commands. Select and record the actual demo host. Target Python 3.12 and Node 24 LTS; report missing prerequisites and their official installation steps without inventing successful installation.
2. Create a minimal `src`-layout Python package with the declared runtime dependencies. Development extras are pytest, httpx, and reportlab for synthetic PDFs. Resolve compatible exact versions; verify the sentence-transformers/Torch CPU install on this host. Record the PostgreSQL-16 pgvector image/release and real registry digest for the database setup task. No ORM or generic service framework.
3. Create the React/Vite manifest with TypeScript, required React types/plugin, and `dev`, `typecheck`, and `build` scripts. `build` runs typecheck then Vite. Resolve exact direct versions and retain the npm lock.
4. Implement validation for secrets, finite positive budgets, loopback provider default, and data directory. Generate the separate local token/passwords with `secrets.token_urlsafe(32)` and write the ignored `.env`/`.env.setup` files directly as ARCHITECTURE specifies; preserve existing files and print only their names. Set `UV_ENV_FILE=.env` after creation. Runtime Settings does not load the admin DSN; failure messages name invalid settings without printing values.

```bash
uv python install 3.12
uv lock
uv sync --frozen --extra dev
npm --prefix frontend install
uv run python -c "import fastapi,pydantic,psycopg,ollama,langgraph,sentence_transformers,pypdf; print('imports-ok')"
```

**Verification/result:** Locked installs succeed and print `imports-ok`; inspect resolved versions. An empty owner token produces a specific setting error. No automated installation test is needed.

### MT0.2 — Measure the local model candidate

**PRD:** MT-08, MT-09, MT-13. **Depends:** MT0.1. **Files:** Create `src/moneytrail/llm.py`, produce `evals/reports/phase0.json`; update architecture/config values with measured identities. **Interfaces:** `complete_structured(messages,response_type)` and CLI `python -m moneytrail.llm pilot --output PATH`. Pilot identity discovery runs before strict runtime digest enforcement.

1. Install/start Ollama locally and pull the initial `qwen3:8b` candidate if absent. Inspect its actual digest, details, quantization, and tokenizer chat template. Resolve tokenizer/embedding/reranker commit revisions, licenses, and installation compatibility.
2. Implement the single native AsyncClient call path, schema passed through `format`, non-streaming response validation, timeout, and sanitized errors. Qualify native `think=false` on the pinned Qwen/Ollama combination; record observed support rather than assuming it.
3. Execute the ten labelled contract probes, one cold/five warm requests, and near-context-budget probe in EVALUATION. Record actual GPU/VRAM or CPU, RAM, token counts, memory, latency, tool decisions, and offline-provider behavior.
4. If feasibility fails, retain outcomes and stop G0 with measured alternatives for review. Never silently switch the model/provider or invent throughput.

```bash
ollama pull qwen3:8b
ollama show qwen3:8b
uv run python -m moneytrail.llm pilot --output evals/reports/phase0.json
```

**Verification/result:** Exact identities and measured probe outcomes satisfy EVALUATION's pilot gates. Neither GPU model name nor a successful download proves usable capacity.

### MT0.3 — Generate and review synthetic evidence and scope

**PRD:** MT-01, MT-03, MT-07, MT-12, MT-16. **Depends:** MT0.1–MT0.2. **Files:** Create fixture generation/validation in `src/moneytrail/evaluation.py`, `fixtures/demo/`, `fixtures/demo.zip`, `evals/dev/questions.jsonl`, `evals/dev/bundles/`, `evals/test/questions.jsonl`, `evals/test/bundles/`; update PRD/TASKS review records. **Interfaces:** `build-fixtures --output fixtures --eval-root evals`; `validate --split dev|test`.

1. Generate the five demo orders/refunds/fees/payouts/links exactly as ARCHITECTURE specifies, with real manifest hashes, text-native dated policy PDFs, and correspondence. Preserve stable extracted locators.
2. Generate 12 development and 18 held-out cases in the three families with distinct source facts. Keep gold outside imports. Record independent hand calculations in rubric notes; do not derive gold by calling the production reconciliation function.
3. Inspect representative PDF/CSV/text files from each family. Verify exact demo arithmetic and T+2 dates; validate family counts/checksums/disjoint sources. Review the v1 scope and measured feasibility with the user at G0.

```bash
uv run python -m moneytrail.evaluation build-fixtures --output fixtures --eval-root evals
uv run python -m moneytrail.evaluation validate --split dev
uv run python -m moneytrail.evaluation validate --split test
```

**Verification/result:** One complete demo and 12/18 valid independent cases exist. Dataset validation is not a model score.

**G0 exit:** Reviewed scope, locked identities, passing feasibility or explicitly approved revised decision, and valid independent fixtures/gold. **Git checkpoint:** `git commit -m "chore: lock MoneyTrail scope fixtures and model feasibility"` after exact-path staging. Record real measurements/commit. **STOP for G0 user approval before phase 1.**

## Phase 1 — Safe storage and real uploads

**Objective/deliverable:** Authenticated import of immutable fully indexed cases with inspectable provenance. **PRD:** MT-01, MT-02, MT-04, MT-07, MT-11, MT-15. **Prerequisite:** G0 approved; local database credentials configured.

### MT1.1 — Establish SQL schema and database privileges

**PRD:** MT-03, MT-04, MT-11, MT-15. **Files:** Create `sql/001_initial.sql`, `src/moneytrail/db.py`, `tests/conftest.py`; extend schemas. **Interfaces:** Writer/read connection helpers and `db bootstrap|migrate --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL`.

1. Start PostgreSQL 16 with the phase-0 resolved pgvector release. If needed, use a disposable pgvector PostgreSQL-16 container pinned to the resolved image digest and publish only loopback. Record the actual startup command/digest; final Compose replaces manual startup in phase 6.
2. Implement architecture tables/checks/composite keys, integer paise, `vector(384)`, schema checksums, restricted writer, and SELECT-only reader. The API never uses admin privileges.
3. Make setup/migration idempotent. Test setup creates a uniquely named, validated schema and grants scoped roles; cleanup removes only that schema, never the application database.

```bash
uv run --env-file .env --env-file .env.setup python -m moneytrail.db bootstrap --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL
uv run --env-file .env --env-file .env.setup python -m moneytrail.db migrate --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL
uv run --env-file .env --env-file .env.setup python -m moneytrail.db migrate --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL
```

**Verification/result:** One applied schema version; second migration is a no-op. Reader SELECT succeeds and an attempted reader write fails. MT1.4 retains this as a regression.

### MT1.2 — Validate supported bundles and atomic imports

**PRD:** MT-01, MT-02, MT-03, MT-11. **Depends:** MT1.1. **Files:** Create `src/moneytrail/ingest.py`, `tests/test_import.py`; extend db/schemas. **Interfaces:** `import_bundle(bundle_path,settings)` and nonmutating `python -m moneytrail.ingest validate PATH`.

1. Validate exact manifest/CSV types/checksums/times, archive size/path constraints, required files/references, and duplicate equality/conflicts. Stage under generated internal paths; preserve duplicate row locators.
2. Compute the logical fingerprint independent of ZIP container metadata/manifest whitespace. Retain supported business discrepancies as findings; reject malformed identities instead of silently repairing data.
3. Implement staged extraction/indexing → transaction → source publish → commit/cleanup. Until MT1.3 supplies the real indexer, production import remains unavailable with `INDEX_NOT_READY`; no partial ready case is exposed.
4. Implement the two import regression scenarios. A test-only indexer can inject a mid-import failure; verify no case/rows survive, exact duplicate re-import is idempotent, and conflicting IDs report both locators.

```bash
uv run python -m moneytrail.ingest validate fixtures/demo.zip
uv run --env-file .env --env-file .env.setup pytest tests/test_import.py -q
```

**Verification/result:** Validation and atomicity/duplicate regressions pass. This task alone does not establish a complete production import.

### MT1.3 — Extract/index complete cases with source locators

**PRD:** MT-01, MT-02, MT-05, MT-07. **Depends:** MT1.2. **Files:** Create `src/moneytrail/retrieval.py`; extend ingest/db/schemas/import tests. **Interfaces:** SourceSegment/chunks/policy metadata; initial lexical `search_evidence` retains final SearchResult shape; `ingest import PATH` uses the same importer as the API.

1. Extract PDF page lines/TXT-MD physical lines, parse metadata, reject unsupported PDFs, and retain filename aliases for identical document bytes.
2. Chunk under the locked embedding-token ceiling, embed with the locked MiniLM revision, and insert vectors/tsvector atomically. Connect the real indexer and remove `INDEX_NOT_READY` only when it works.
3. Retain missing-link/completeness/policy findings. Import the demo, inspect all record/chunk counts, and resolve representative PDF/CSV/text locators. Re-import must preserve IDs/counts.

```bash
uv run python -m moneytrail.ingest import fixtures/demo.zip
uv run --env-file .env --env-file .env.setup pytest tests/test_import.py -q
```

**Verification/result:** One ready demo exists with complete provenance; repeated import returns its case ID and `reused:true`.

### MT1.4 — Expose authenticated case/source APIs

**PRD:** MT-01, MT-02, MT-04, MT-11. **Depends:** MT1.3. **Files:** Create `src/moneytrail/api.py`, `tests/test_security.py`; extend config/db/schemas. **Interfaces:** Architecture health, case import/list/detail, and source metadata/segments/content endpoints.

1. Implement shared constant-time bearer-token validation, safe error envelope, per-case source checks, escaped text, and safe content dispositions. Health reveals no config.
2. Wire real uploads with 201 new/200 reused responses and explicit gaps/duplicates. Enforce request limits and prevent overlapping import/investigation workloads.
3. Implement the two security scenarios: auth/scope/reader-write denial and archive/inert source instructions. MT4.3 extends the latter through the agent dispatcher.

```bash
uv run --env-file .env --env-file .env.setup pytest tests/test_import.py tests/test_security.py -q
uv run uvicorn moneytrail.api:app --host 127.0.0.1 --port 8000 --workers 1
```

**Verification/result:** 401 without token, correct upload responses, 404 for wrong case/source, no ready case after failed extraction. Tokens go in headers, never URLs or committed request files.

**G1 exit:** Real atomic upload, duplicates, provenance, scoped auth, and read-only role behavior work. **Git checkpoint:** `git commit -m "feat: import immutable merchant cases with source provenance"`. **STOP for G1 user approval before phase 2.**

## Phase 2 — Complete first investigation workflow

**Objective/deliverable:** Live supported upload → free-text question → deterministic explanation → clickable evidence. **PRD:** MT-02, MT-03, MT-04, MT-06, MT-07, MT-09, MT-10, MT-14. **Prerequisite:** G1 approved.

### MT2.1 — Implement reconciliation and record inspection

**PRD:** MT-03, MT-04, MT-06. **Files:** Create `src/moneytrail/tools.py`, `tests/test_accounting.py`; extend schemas/db. **Interfaces:** `reconcile`, `inspect_order`, Fact/Reconciliation/OrderEvidence.

1. Implement fixed scoped SQL for cohort/cutoff, completed refunds, actual fees, received allocations, header checks, and independent pending classification. Calculate net/difference using integer paise.
2. Attach derivations/citations/completeness and expose missing links, unknown state, negative net, pending/received conflicts, and payout mismatch. Do not impute missing money or assign residuals to pending.
3. Implement three accounting regressions: exact demo tuple, cutoff/status exclusion, and missing-link behavior. Verify joins do not double-count header/allocation money.

```bash
uv run --env-file .env --env-file .env.setup pytest tests/test_accounting.py -q
```

**Verification/result:** `(gross,refunds,fees,pending,received,difference)` equals `(24000000,1800000,600000,3000000,18600000,0)` paise. Missing paid-order links yield a gap and `reconciled:false`.

### MT2.2 — Resolve policy and validate grounded results

**PRD:** MT-02, MT-06, MT-07. **Depends:** MT2.1. **Files:** Extend tools/schemas; create `tests/test_policy.py`, `tests/test_answer.py`; begin `src/moneytrail/agent.py`. **Interfaces:** `select_policy`, `validate_result`, policy groups, Fact/Claim/AnswerDraft/InvestigationResult.

1. Select policies by order date/publication cutoff, retaining all conflicts. Support whole-cohort grouped policy lookup independently of the 20-explicit-order selection limit. Compute expected dates/rates deterministically; source records remain cash inputs.
2. Register facts per run, validate model-selected IDs/citations, and render paise from registered facts. Keep obvious currency-literal checking small; no language parser. Critical findings prevent an unqualified explained outcome.
3. Implement policy boundary/conflict and answer-integrity regressions: invented fact ID, wrong-case citation, accurate paise rendering, and incomplete total labels. Semantic narrative support remains an evaluation responsibility.

```bash
uv run --env-file .env --env-file .env.setup pytest tests/test_policy.py tests/test_answer.py -q
```

**Verification/result:** Correct effective version/conflict citations, safe grounded facts, and explicit uncertainty.

### MT2.3 — Run fixed investigations through the API

**PRD:** MT-04, MT-06, MT-08, MT-09, MT-14. **Depends:** MT2.2. **Files:** Extend agent/llm/api/db; create `tests/test_workflow.py`; extend evaluation CLI. **Interfaces:** `run_fixed`, `assemble_context`, start/get/events/cancel endpoints, sanitized local events.

1. Implement reconcile → whole-cohort policy lookup → question-based correspondence search → one live structured synthesis → validation. Accept arbitrary in-scope questions; do not match question strings to gold or saved responses.
2. Count context tokens, retain essential facts/conflicts, reserve one active slot, and persist runs/events. Implement in-process async task, polling, cancellation, and process-interruption failure; no queue.
3. Add the offline HTTP journey using a clearly test-only provider response and real database queries. Run one live demo question and source/offline-provider checks manually. Implement basic development evaluation output capture now; full scoring comes in phase 5.

```bash
uv run --env-file .env --env-file .env.setup pytest tests/test_workflow.py tests/test_answer.py -q
uv run python -m moneytrail.evaluation run --split dev --mode fixed
```

**Verification/result:** Actual outputs for the 12 development cases and a live original-question answer; a test double is not live evidence.

### MT2.4 — Deliver the owner UI and first live demonstration

**PRD:** MT-01, MT-02, MT-10, MT-11, MT-14. **Depends:** MT2.3. **Files:** Create frontend `index.html`, `vite.config.ts`, `src/App.tsx`, `api.ts`, `types.ts`, `styles.css`, and the four architecture components; extend API static serving/README. **Interfaces:** Only canonical endpoints; bearer token stays in memory; PDF fetch uses revocable blob URLs.

1. Build owner-token entry, case list/upload, snapshot/completeness labels, question input, progress/cancel, result/facts, source viewer, and trace. Safely render text; no uploaded trusted HTML.
2. Implement one-second polling with cursor, terminal result fetch, cancellation display, and repeated-submit prevention. Keep unknown/incomplete amounts visibly distinct.
3. Build/serve from FastAPI. Through the UI upload a new case, ask an original question, inspect CSV/PDF/text evidence, cancel another run, and show the real offline-provider error.

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run build
uv run --env-file .env --env-file .env.setup pytest tests/test_workflow.py -q
```

**Verification/result:** Complete live workflow with the five exact demo totals. A manual UI smoke suffices; no new browser-test framework.

**G2 exit:** Complete live vertical slice, accounting/policy/output integrity and source journey verified; lexical-only/fixed-orchestration limitations recorded. **Git checkpoint:** `git commit -m "feat: investigate merchant payouts end to end with cited facts"`. **STOP for G2 user approval before phase 3.**

## Phase 3 — Hybrid retrieval and richer evidence access

**Objective/deliverable:** Measured hybrid retrieval/reranking and complete allowlisted evidence tools. **PRD:** MT-02, MT-04, MT-05, MT-07, MT-09, MT-12. **Prerequisite:** G2 approved.

### MT3.1 — Fuse and rerank retrieval candidates

**PRD:** MT-02, MT-05, MT-07, MT-12. **Files:** Extend retrieval/db; create `tests/test_retrieval.py`; extend evaluation.py. **Interfaces:** Final `search_evidence` contract and `evaluation retrieval --split dev|test`.

1. Implement exact dense and full-text top-20 candidates, case/publication filters, stable ties, RRF constant 60, deduplication, cross-encoder reranking of 20 fused candidates, and at most six selected chunks.
2. Return ranking metadata for trace/evaluation. Keep policy applicability/conflict selection independent of relevance ranking.
3. Add deterministic candidate/locator regression without model download in CI. Measure actual locked embedding/reranking models on development data using EVALUATION's standalone original-question, `kind="all"` search protocol. Print explicit numerator/denominator and eligible questions; never credit independently selected policies to retrieval recall.

```bash
uv run --env-file .env --env-file .env.setup pytest tests/test_retrieval.py -q
uv run python -m moneytrail.evaluation retrieval --split dev
```

**Verification/result:** Stable valid citations and honest lexical/dense/fused/reranked metrics; no zero-denominator success claim.

### MT3.2 — Complete the typed evidence dispatcher

**PRD:** MT-02, MT-04, MT-06, MT-09, MT-11. **Depends:** MT3.1. **Files:** Extend tools/schemas/agent/api and SourceViewer. **Interfaces:** `dispatch_tool`, the five tool variants, and `read_source` exactly as ARCHITECTURE.

1. Finish bounded order inspection, optional explicit-order policy selection plus cohort mode, exact-locator source reads, and search validation. Inject case scope; reject unknown tools, SQL strings, foreign source IDs, and result overflow explicitly.
2. Use one registry mapping discriminated arguments to ordinary functions. Register only returned real facts/citations; record safe call IDs and trace metadata.
3. Connect detailed source/order evidence to context/UI and inspect a development case requiring both correspondence and refund joins. No incomplete truncation may look like a complete result.

```bash
uv run --env-file .env --env-file .env.setup pytest tests/test_security.py tests/test_accounting.py tests/test_workflow.py -q
npm --prefix frontend run build
```

**Verification/result:** Existing scenarios pass through the real dispatcher/source viewer without new abstraction layers.

### MT3.3 — Record the fixed development baseline

**PRD:** MT-05, MT-06, MT-12, MT-13. **Depends:** MT3.2. **Files:** Extend evaluation reports; update EVALUATION measured-development link and TASKS. **Interfaces:** Stable captured outputs/retrieval rows for subsequent comparison.

1. Run 12 live fixed-mode cases using final hybrid retrieval. Inspect failures by retrieval, policy, accounting, structured output, and explanation support.
2. Resolve actual in-scope failures with the smallest change; rerun affected regressions/cases, then one full stable development run. Record changed parameters/prompts. Material model/architecture changes need review.
3. Retain failures and actual latency/token use. Do not tune using held-out answers or call development scores final results.

```bash
uv run python -m moneytrail.evaluation run --split dev --mode fixed
uv run python -m moneytrail.evaluation retrieval --split dev
```

**Verification/result:** Inspectable baseline with no unexamined critical money/security defect.

**G3 exit:** Hybrid retrieval/read tools work and the fixed development baseline is recorded. **Git checkpoint:** `git commit -m "feat: retrieve and rerank policy and correspondence evidence"`. **STOP for G3 user approval before phase 4.**

## Phase 4 — Bounded investigator, controls, and trace

**Objective/deliverable:** An observable investigator choosing evidence for natural-language questions while preserving deterministic money and finite execution. **PRD:** MT-04, MT-06, MT-08, MT-09, MT-10, MT-11, MT-13, MT-14. **Prerequisite:** G3 approved; fixed baseline retained.

### MT4.1 — Add the explicit investigation graph

**PRD:** MT-04, MT-06, MT-08. **Files:** Extend agent/llm/schemas; create `tests/test_run_control.py`. **Interfaces:** InvestigationState/Decision and `run_agent(request,run_id,cancel)`; fixed mode remains internal to evaluation.

1. Build bootstrap/decide/tools/validate/finish with explicit error exits. Reconciliation bootstrap counts as one of six tools; case ID is injected, not model-supplied.
2. Dispatch at most two sequential calls per decision and validate final answers through the existing result path. The fourth model call must answer; a continuation request yields the documented budget terminal outcome.
3. Exercise an original development question requiring extra source inspection and one needing no extra tool. Record actual selections; no question-to-saved-answer matching.

```bash
uv run --env-file .env --env-file .env.setup pytest tests/test_run_control.py tests/test_answer.py -q
uv run python -m moneytrail.evaluation run --split dev --mode agent
```

**Verification/result:** Bounded real decisions with grounded result or explicit error/uncertainty, not merely parseable JSON.

### MT4.2 — Enforce context and execution budgets

**PRD:** MT-08, MT-09, MT-13. **Depends:** MT4.1. **Files:** Extend agent/config/llm and run-control test. **Interfaces:** ContextPacket includes counted tokens, selected/omitted sources, and registered facts.

1. Count complete messages/schema/template overhead with the locked tokenizer. Preserve required facts/conflicts; drop only whole optional low-ranked chunks. Essential overflow raises CONTEXT_LIMIT.
2. Enforce six tool calls including retry, four model turns, 60-second call/240-second run deadlines, single active inference, and recursion backstop. Use monotonic elapsed time.
3. Extend the same run-control scenario with repeated tool decisions and near-budget context. Assert no dispatch after limit and no unsupported success on exhaustion.

```bash
uv run --env-file .env --env-file .env.setup pytest tests/test_run_control.py -q
uv run python -m moneytrail.llm pilot --output evals/reports/phase4-context.json
```

**Verification/result:** Actual counted budgets/finite execution. Preserve original phase-0 measurements rather than overwriting them.

### MT4.3 — Finish cancellation, errors, and source boundaries

**PRD:** MT-08, MT-10, MT-11, MT-14. **Depends:** MT4.2. **Files:** Extend api/agent/llm/tools and existing run-control/security tests. **Interfaces:** Canonical cancellation/error states; one transient read retry maximum.

1. Propagate cancel through provider await and node boundaries, prevent late answer persistence, and release the active slot in finally. Startup marks interrupted runs failed without resume.
2. Implement MODEL_UNAVAILABLE, MODEL_SCHEMA, TOOL_ERROR, READ_TIMEOUT, CONTEXT_LIMIT, BUDGET_EXHAUSTED, and PROCESS_INTERRUPTED behavior. Never replace errors with mocked successful answers or hosted fallback.
3. Exercise malicious source text through the real graph/dispatcher boundary with test-only provider controls. Assert no external tools, secrets in context/events, reader mutations, or cross-case access. Manually stop Ollama to inspect the error, then explicitly restore it.

```bash
uv run --env-file .env --env-file .env.setup pytest tests/test_run_control.py tests/test_security.py -q
```

**Verification/result:** Visible finite failures, working cancel, clean slot release, and zero unauthorized access.

### MT4.4 — Finish trace/UI and development review

**PRD:** MT-06, MT-10, MT-12, MT-14. **Depends:** MT4.3. **Files:** Extend db/api/agent/retrieval, RunTrace/InvestigationPanel/SourceViewer, README. **Interfaces:** Ordered sanitized run_events and polling, never a raw graph-state stream.

1. Record canonical event kinds, model/prompt/config identities, timings/tokens, safe tool inputs, retrieval scores/IDs, fact references, and terminal events. Exclude secrets/hidden reasoning.
2. Use run_agent for the public start route; keep fixed mode for evaluation. Display outcome, partial evidence, snapshot, sources, and trace clearly.
3. Run all development cases live and inspect money/claims. Perform UI cancel/source/trace smoke and record remaining quality/latency limits before final evaluation.

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run build
uv run --env-file .env --env-file .env.setup pytest -q
uv run python -m moneytrail.evaluation run --split dev --mode agent
```

**Verification/result:** Working bounded investigator, local trace, and no critical integrity/security defect. The twelve focused scenarios suffice unless a real failure warrants another regression.

**G4 exit:** Controls/trace/UI and development review verified. **Git checkpoint:** `git commit -m "feat: bound and trace the merchant investigation agent"`. **STOP for G4 user approval before phase 5.**

## Phase 5 — Freeze, compare, and judge results

**Objective/deliverable:** Frozen paired evaluation, retrieval denominators, complete errors, and an honest acceptance decision. **PRD:** MT-03, MT-05, MT-06, MT-08, MT-11, MT-12, MT-13, MT-14, MT-16. **Prerequisite:** G4 approved; stable development and source-disjointness checks.

### MT5.1 — Finalize scoring/gold and freeze inputs

**PRD:** MT-03, MT-05, MT-06, MT-11, MT-12. **Files:** Extend evaluation.py; validate existing gold files; create `evals/reports/freeze.json`. **Interfaces:** `freeze`, deterministic fact/citation/arithmetic scoring, and required manual support/outcome judgments.

1. Validate 12/18 counts, independent source content/gold calculations, uncertainty labels, and critical-document denominator. Verify locators against actual imports.
2. Implement scoring of every returned monetary fact, citation resolution, forbidden access, bounded terminal outcome, and task rubric. Narrative money/support judgments remain explicit human review; schema alone cannot establish truth.
3. Commit application/dataset inputs before freeze; record hashes/revision/model/config/hardware. Refuse dirty evaluated files and exclude the freeze report from its own content hash.

```bash
uv run python -m moneytrail.evaluation validate --split dev
uv run python -m moneytrail.evaluation validate --split test
uv run python -m moneytrail.evaluation freeze --output evals/reports/freeze.json
```

**Verification/result:** Reproducible frozen manifest and independently reviewed gold. No unreviewed input discrepancy is ignored.

### MT5.2 — Execute paired held-out and retrieval runs

**PRD:** MT-05, MT-06, MT-08, MT-12, MT-13, MT-14. **Depends:** MT5.1. **Files:** Produce `evals/reports/final.json`/retrieval report via evaluation.py; bulky raw traces stay ignored. **Interfaces:** `paired --split test --freeze PATH` alternates mode order and captures both outputs.

1. Warm the locked model as recorded and run all 18 fixed/18 agent cases with identical frozen inputs. Keep errors; measure real latency/tool counts/tokens.
2. Measure held-out retrieval using the same corpus/revisions and EVALUATION's standalone original-question, `kind="all"` protocol. Retain explicit numerator/denominator and eligible cases. Report investigation policy-conflict coverage separately from ranking recall.
3. Review every narrative against source/gold and record support/outcome decisions. Even one incorrect/fabricated returned monetary claim or material unauthorized access is a hard failure.

```bash
uv run python -m moneytrail.evaluation paired --split test --freeze evals/reports/freeze.json
uv run python -m moneytrail.evaluation retrieval --split test --freeze evals/reports/freeze.json
```

**Verification/result:** 36 captured outcomes, all 18 agent cases scored, and complete retrieval/manual-review evidence. HTTP success is not task success.

### MT5.3 — Publish error analysis and acceptance decision

**PRD:** MT-03, MT-06, MT-11, MT-12, MT-16. **Depends:** MT5.2. **Files:** Create `evals/reports/final-summary.md`; update EVALUATION result link, README limitations, TASKS. **Interfaces:** `report --input PATH --output PATH` rejects missing judgments or mismatched freeze identities.

1. Report exact paired/per-family counts, money/citation/security checks, retrieval denominators, wins/losses/ties, tool/token use, p50/p95, and hardware. Label missed performance targets.
2. Explain every error with expected/actual outcome and source/run references. Failure blocks advancement. Retain failed reports and apply EVALUATION's new-held-out-set rule if further development is approved; never quietly tune/relabel the final set.
3. Review PRD coverage and synthetic-data limits. Recommend acceptance or continued work without lowering thresholds. Fine-tuning remains parked.

```bash
uv run python -m moneytrail.evaluation report --input evals/reports/final.json --output evals/reports/final-summary.md
```

**Verification/result:** Complete truthful comparison and error report.

**G5 exit:** At least 15/18 agent outcomes, 4/6 each family, zero fabricated/wrong money or unauthorized access, retrieval/citation/conflict/bounded-runtime gates met, both modes reported. **Git checkpoint:** `git commit -m "test: record frozen MoneyTrail evaluation and error analysis"`. **STOP for G5 user approval before phase 6.**

## Phase 6 — Reproducible delivery and demonstration

**Objective/deliverable:** Clean-clone local deployment, actual offline CI result, accurate README, reliable live demo. **PRD:** MT-10, MT-11, MT-13, MT-14, MT-15, MT-16. **Prerequisite:** G5 approved and no critical acceptance failure.

### MT6.1 — Package monolith and database

**PRD:** MT-11, MT-13, MT-15. **Files:** Create Dockerfile, compose.yaml, .dockerignore, `scripts/start-local.sh`; update verified setup in README/ARCHITECTURE. **Interfaces:** Same API/static build/config; exactly app/db Compose services.

1. Build frontend/Python runtime with locks in a minimal multi-stage image. Resolve/pin real base/database image digests for the target platform; no invented digests.
2. Implement Linux reference networking: app host network and loopback bind, db loopback published port, host Ollama loopback. Document reduced container network isolation. Native Windows development uses host API plus Docker DB; no custom proxy or public provider.
3. Keep admin credentials out of app runtime. Startup checks config/digest/schema/frontend and returns actionable errors without resets/download fallback.

```bash
docker compose --env-file .env.setup config --quiet
docker compose --env-file .env.setup build app
docker compose --env-file .env.setup up -d db
uv run --env-file .env --env-file .env.setup python -m moneytrail.db bootstrap --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL
uv run --env-file .env --env-file .env.setup python -m moneytrail.db migrate --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL
docker compose --env-file .env.setup up -d app
```

**Verification/result:** Local application connects on the declared Linux host; inspect listeners to confirm no public app/db/provider and no embedded image secrets.

### MT6.2 — Add offline CI and prove clean-clone setup

**PRD:** MT-01, MT-10, MT-11, MT-15. **Depends:** MT6.1. **Files:** Create `.github/workflows/ci.yml`, `scripts/smoke.sh`; adjust test fixtures for ephemeral CI DB. **Interfaces:** `bash scripts/smoke.sh --live`; one CI workflow with PostgreSQL/pgvector service and no live LLM.

1. Configure locked installs, twelve regressions, typecheck/build, and ephemeral test secrets in one GitHub Actions job. Ordinary CI downloads no model and makes no generative API call; dependencies may be installed normally.
2. Implement smoke script to upload through the real API, ask an original question, poll, verify exact facts, and resolve sources. `--live` fails if provider unavailable and never selects the test double.
3. Clone into a new temporary directory and follow docs for configuration/schema/model/UI startup. Run checks/live smoke and inspect actual CI result. Preserve original user work/data; do not reset its database.

```bash
uv sync --frozen --extra dev
npm --prefix frontend ci
uv run --env-file .env --env-file .env.setup pytest -q
npm --prefix frontend run build
bash scripts/smoke.sh --live
```

**Verification/result:** Successful clean-clone journey and actual offline CI pass. Missing GitHub access/execution is an explicit pending blocker, not a claimed passing workflow.

### MT6.3 — Finish demonstration and interview handoff

**PRD:** MT-10, MT-12, MT-14, MT-15, MT-16. **Depends:** MT6.2. **Files:** Update README/TASKS and inaccurate existing contracts; remove only proven unused implementation components. **Interfaces:** No new product interface or scope expansion.

1. Demonstrate all three families through uploads, including duplicate import, missing/conflicting evidence, an original question, source/trace inspection, cancel, and actionable error. Record a short repeatable README demo script.
2. Add factual interview notes: problem, architecture, request/data flow, paise/cutoff integrity, retrieval/agent comparison, measured serving tradeoffs, actual challenges/fixes, and limits. Label synthetic evaluations and any replay.
3. Review each PRD ID against evidence: no secrets, silent fallback, unused dependencies, unapproved features, or docs ahead of code. Record final commit/CI/live-smoke/evaluation references, noncritical limits, and exact stopped/completed status.

**Verification/result:** Review final diff and requirement mappings; rerun only checks affected since MT6.2. Behavior changes invalidating frozen inputs require an appropriate new evaluation. Documentation edits alone do not justify another live benchmark.

**G6 exit:** Clean-clone/live demo/CI verified, mandatory acceptance met, docs accurate, secrets absent, and limits explicit. **Git checkpoint:** `git commit -m "docs: finalize MoneyTrail demo and verified delivery handoff"`. **STOP for final user review.** Mark implementation complete only when evidence exists; this planning delivery leaves every task unstarted.
