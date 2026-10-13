# MoneyTrail architecture and interface contract

Planning baseline v1, 2026-10-13. All modules, commands, tables, fixture files, and performance results below are planned unless later marked verified in [TASKS.md](TASKS.md). The product contract is [PRD.md](PRD.md). Keep this file authoritative for interfaces; change a contract here before changing its consumers.

## Shape and decisions

One Python 3.12/FastAPI application serves a thin React/TypeScript/Vite build, performs import/retrieval, runs one bounded LangGraph investigation at a time, and stores structured records, text chunks/vectors, and local traces in PostgreSQL 16 with pgvector. Use Pydantic 2 schemas, psycopg parameterized SQL, numbered SQL files, and the native Ollama Python `AsyncClient`. A single process with one Uvicorn worker owns active tasks. There is no background queue or resumable distributed workflow.

The database is a separate local infrastructure process; Ollama is the single local inference service. On the authorized Linux lab, measure the actual RTX 4000 Ada device and memory before selecting execution settings. Local embeddings/reranking run on CPU initially; Ollama may use an available GPU. Lack of remote access must not leave implementation waiting indefinitely: run the phase-0 probe on an available local host, report its measured suitability, and stop the feasibility gate if neither host can satisfy the agreed live-demo target. Never assume the lab is permanently public or reachable.

| Decision | Why this is enough | Primary reference checked 2026-10-13 |
| --- | --- | --- |
| PostgreSQL plus pgvector, exact vector search | One database stores transactions and retrieval material; the small corpus does not justify HNSW or a vector service. | [pgvector documentation](https://github.com/pgvector/pgvector) |
| One explicit LangGraph `StateGraph` | Makes bounded decide/tool/finish transitions inspectable without adopting a multi-agent framework. | [Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) |
| Ollama native client plus JSON-schema output | One transport and one model provider; Pydantic validates the actual response independently of model formatting. | [Structured outputs](https://docs.ollama.com/capabilities/structured-outputs), [Python client](https://github.com/ollama/ollama-python) |
| `all-MiniLM-L6-v2` and `ms-marco-MiniLM-L6-v2` | Small local embedding/reranking candidates suitable for a pilot; revisions, licenses, and domain quality must be recorded before acceptance. | [Embedding model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), [Reranker model card](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2) |
| Fixed workflow before bounded investigation | Gives an early usable vertical slice and a fair comparison for the added tool-selection loop. | Project decision; evaluation protocol in EVALUATION. |
| Polling and local database events | Supports progress/cancel/source inspection without streaming infrastructure or an external telemetry account. | Project decision. |

These references justify API choices, not an assertion that a particular package combination or model quality has been tested. Phase 0 locks actual versions and revisions and verifies compatibility.

## Planned file responsibilities

```text
moneytrail/
  README.md  PRD.md  AGENTS.md  ARCHITECTURE.md  EVALUATION.md  ROADMAP.md  TASKS.md
  .env.example  .gitignore
  pyproject.toml  uv.lock
  sql/001_initial.sql
  src/moneytrail/
    __init__.py
    api.py          # FastAPI routes, auth dependency, active task/cancel registry
    config.py       # validated runtime settings; fixed operational limits
    schemas.py      # Pydantic import, API, tool, model decision, result contracts
    db.py           # connections, role setup, numbered SQL CLI, scoped queries
    ingest.py       # bundle validation, extraction, provenance, atomic import CLI
    retrieval.py    # chunks, embeddings, full-text/dense fusion, reranker
    tools.py        # allowlisted read tools and deterministic monetary facts
    agent.py        # fixed baseline plus bounded StateGraph, context assembly
    llm.py          # native Ollama request, schema validation, tokenizer/pilot CLI
    evaluation.py   # fixture builder, dataset validation, scoring and benchmark CLI
  frontend/
    package.json  package-lock.json  tsconfig.json  vite.config.ts  index.html
    src/App.tsx  src/api.ts  src/types.ts  src/styles.css
    src/components/ImportPanel.tsx  InvestigationPanel.tsx  SourceViewer.tsx  RunTrace.tsx
  fixtures/demo/manifest.json  orders.csv  refunds.csv  fees.csv  payouts.csv  payout_links.csv
  fixtures/demo/docs/policy-current.pdf  policy-previous.pdf  settlement-note.md
  fixtures/demo.zip
  evals/dev/questions.jsonl  evals/dev/bundles/
  evals/test/questions.jsonl  evals/test/bundles/
  evals/reports/phase0.json  evals/reports/final-summary.md
  tests/conftest.py  test_import.py  test_accounting.py  test_policy.py
  tests/test_retrieval.py  test_answer.py  test_run_control.py  test_security.py  test_workflow.py
  scripts/smoke.sh  scripts/start-local.sh
  Dockerfile  compose.yaml  .dockerignore  .github/workflows/ci.yml
  data/             # ignored imports and runtime artifacts, not committed
```

This is a future layout, not a request to create application files during planning. Store supported models in the platform's local model cache, not Git. Synthetic small fixtures are committed; raw runtime traces and local credentials are ignored. Split a Python module only if a coherent responsibility warrants it, not to add abstract service/repository layers.

## Bundle and atomic import contract

`POST /api/v1/cases/import` accepts one multipart field `bundle` containing a ZIP. Its root contains exactly `manifest.json`, the five named CSV files, and the manifest-listed files under `docs/`. CSV files always exist, with headers even when empty. UTF-8 is required; reject other encodings with filename/row diagnostics. Accept text-native PDFs and `.txt`/`.md`; scanned or encrypted PDFs fail with `UNSUPPORTED_PDF` and instructions to supply selectable text. No remote URL fetching or archive-within-archive extraction.

Limits are constants in `config.py`: ZIP 20 MiB compressed, 50 MiB total inflated, 50 files, 100,000 total CSV data records, 100 pages per PDF, 10 MiB per document, question length 2,000 characters. Check archive declared lengths and actual streamed bytes. Reject traversal, absolute/drive paths, symlink entries, duplicates at the same archive path, undeclared entries, wrong extensions, and unexpected columns. Stage under a newly generated internal directory beneath `MONEYTRAIL_DATA_DIR`; never use an upload filename as a filesystem destination.

Manifest field contract:

| Field | Required value/type |
| --- | --- |
| schema_version | Integer `1`. |
| case_key | Descriptive ASCII slug, not trusted as an identifier. Main fixture: `demo-september`. |
| merchant_id / currency / timezone | Exactly `meridian_store` / `INR` / `Asia/Kolkata`. |
| cohort_start / cohort_end / as_of | Offset timestamps. Main fixture: `2026-09-01T00:00:00+05:30`, `2026-10-01T00:00:00+05:30`, `2026-10-01T00:00:00+05:30`. |
| completeness | Object with exactly `orders`, `refunds`, `fees`, `payouts`, `payout_links`, `policies`; each value is `complete` or `unknown`. Main fixture uses `complete` for all. |
| files | Array listing every required CSV and document exactly once. Each entry has `path`, `sha256` (actual 64-character lowercase hexadecimal content hash), and `kind` (`orders`, `refunds`, `fees`, `payouts`, `payout_links`, `policy`, or `correspondence`). |

Completeness describes supplied evidence, not truth externally verified by this app. At least one policy PDF and one correspondence file are required. The main demo has complete evidence. Gap cases still have structurally valid required files, but may declare a source family `unknown`, omit a necessary business link, or contain conflicting policy/correspondence evidence.

CSV headers and primitive contracts:

| File | Exact columns |
| --- | --- |
| orders.csv | `order_id,captured_at,gross_paise,settlement_state` |
| refunds.csv | `refund_id,order_id,processed_at,amount_paise,status` |
| fees.csv | `fee_id,order_id,assessed_at,amount_paise,fee_kind` |
| payouts.csv | `payout_id,received_at,amount_paise,status` |
| payout_links.csv | `link_id,payout_id,order_id,amount_paise,recorded_at` |

IDs are nonempty case-local ASCII strings of at most 64 characters. Times are ISO-8601 with explicit offsets; normalized storage is UTC `timestamptz`. Monetary inputs are base-10 integer paise between zero and 10^12; no decimals, scientific notation, floats, symbols, or implicit rupee conversion. Orders have `pending|paid|unknown` settlement state as of the manifest cutoff. Refunds have `completed|pending`; completed requires `processed_at`, pending has an empty `processed_at` and is not deducted. Fees have `processing` as the only v1 kind. Payouts have `received|scheduled`; received requires a timestamp, scheduled has an empty timestamp. Links have a real recorded timestamp. A zero monetary value is legal and not missing. Unknown monetary values are not encoded as zero: omit the unavailable record and declare completeness/gap.

Syntactic faults, unknown foreign identifiers, and conflicting duplicate IDs reject the whole import. Business discrepancies that are valid evidence (missing expected link, differing payout/net sums, unknown state, overlapping policies) are retained as `case_findings`; they do not get repaired or dropped. Identical repeated CSV IDs and fields are stored once with all row locators and a duplicate finding; a second ID with the same amount is a different record, not an assumed duplicate. Repeated document bytes with equal metadata share extracted chunks while retaining filename aliases; differing policy metadata is a conflict, not silent deduplication.

Compute `bundle_fingerprint = SHA256(canonical manifest logical metadata + sorted [normalized relative path, actual file SHA256])`; exclude manifest whitespace and ZIP container metadata. Verify file checksums before this calculation. It is unique globally in this single-owner application. Identical re-import returns HTTP 200 and the existing case with `reused:true`; a new successful import returns HTTP 201 with `reused:false`. Distinct logical bundles become immutable independent cases and are never automatically aggregated. No append/update import operation exists.

Parse, validate, extract, chunk, and embed into staging first. Open one writer transaction to insert case, sources, records, policies, findings, and chunks. Publish an internal source directory atomically before committing; if publishing or commit fails, roll back and remove only that import's staging/unreferenced directory. On startup remove/report orphan staging directories safely. Only committed `ready` or `ready_with_gaps` cases can be selected. An import error returns its error directly and never exposes a half-imported case. Concurrent identical imports use the fingerprint uniqueness constraint and return the winner after rollback.

## Accounting and time contract

The bundle is a closed captured-order cohort `[cohort_start, cohort_end)`, with `as_of >= cohort_end`. Every order must be in that cohort; every refund/fee/link refers to one of its orders; every payout header contains only links from this case. This makes payout header validation possible without unprovided outside-cohort rows. Whole-order settlement is the only allocation form: at most one effective link per order and one payout may contain many orders. Partial settlements and cross-cohort payouts fail the supported-format contract with a clear explanation.

All transaction inclusion is strictly before `as_of`, which is an exclusive cutoff. `as_of = 2026-10-01T00:00:00+05:30` means “through 30 September in India.” The run always uses its case's frozen cohort and cutoff; a question asking for another snapshot returns `insufficient` with instructions to upload that snapshot. Do not reinterpret settlement-state snapshots at an earlier/later time. Documents published at or after `as_of` are not evidence available at that snapshot.

1. Gross is the sum of unique captured order amounts in the cohort.
2. Refund deduction is the sum of unique `completed` refund amounts with `processed_at < as_of`; pending and later refunds remain visible but are excluded with a reason.
3. Fees are actual unique assessed fees with `assessed_at < as_of`. Do not estimate missing fees from policy and silently insert them into the total.
4. Per-order expected net is gross minus those refunds and fees. A negative net is an evidence conflict; do not clamp it to zero.
5. Received is the sum of effective unique payout links with `recorded_at < as_of` whose payout is `received` with `received_at < as_of`. Never add payout headers to allocation amounts. Every received payout header must equal the sum of all its effective links. Every paid order must have exactly one effective received link equal to its expected net.
6. Documented pending is the sum of net values only for explicit `pending` orders that have no received allocation and have complete refund/fee evidence. A scheduled payout may provide context, but its header is not received money. A pending order with a received link is conflicting evidence and is excluded from the definitive pending total until resolved.
7. The signed unexplained difference is net minus received minus documented pending, accompanied by exact scope and completeness flags. `unknown` settlement, missing paid-order links, header/allocation discrepancies, and incomplete source families stay visible even when the arithmetic happens to equal zero.

Totals have shape `{value_paise:int, complete:bool, source_ids:list[str]}`. If a source family is unknown, its sum is labelled “known records only”; it does not establish a complete aggregate. Received sums only supported allocations; a payout header without complete links is separately shown as an observed record and gap. The UI cannot label an incomplete subtotal as a verified final total. A `reconciled:true` result requires all required families complete, no critical accounting findings, and zero difference. Policy uncertainty can still make the explanation `partial` even when the arithmetic reconciles.

The main fixture's exact rows in rupees for human review (files store each value ×100) are:

| Order | Gross | Completed refunds | Assessed fees | Net | State / allocation |
| --- | ---: | ---: | ---: | ---: | --- |
| O-001 | 80,000 | 10,000 | 2,000 | 68,000 | paid, P-001 |
| O-002 | 60,000 | 6,800 | 1,500 | 51,700 | paid, P-001 |
| O-003 | 40,000 | 0 | 1,000 | 39,000 | paid, P-002 |
| O-004 | 28,000 | 0 | 700 | 27,300 | paid, P-002 |
| O-005 | 32,000 | 1,200 | 800 | 30,000 | pending, no received link |

P-001 is INR 119,700 and P-002 is INR 66,300. Earlier orders are captured 25–27 September and paid before the cutoff. O-005 is captured 30 September; its completed refund and fee are also before cutoff. A policy effective 1 September states T+2 **calendar days**, using the captured local date, and a 250-basis-point processing fee. The expected date is 2 October for O-005. These are fictional policy terms, not an assertion about payment-industry practice. Policy checks use integer basis points and `Decimal` half-up rounding to paise where needed; actual records remain the accounting inputs.

## Documents, policies, retrieval, and citations

Policy PDFs are text-native synthetic source documents containing exact labelled metadata lines: `Policy-ID:`, `Published-At:` (offset timestamp), `Effective-From:` (ISO local date), `Effective-To:` (ISO local date or `open`), `Settlement-Calendar-Days:` (integer), and `Processing-Fee-Bps:` (integer). Parse these lines from extracted PDF text, retaining page/line spans. Missing or contradictory labelled metadata rejects that policy file. Correspondence begins with `Document-ID:` and `Published-At:` lines, followed by ordinary untrusted text. No model generates effective dates or numerical policy terms during import.

Applicability is `effective_from <= captured_local_date < effective_to`, or no upper limit for `open`, and `published_at < as_of`. For multiple applicable policy versions: equal terms can be cited together; different terms produce `POLICY_CONFLICT` and prevent a definitive policy-based explanation. No applicable version produces `MISSING_POLICY`. Correspondence cannot override policy selection; disagreement becomes a cited finding. A superseded policy may be shown as context with its excluded status, never returned as authoritative applicable evidence.

Extract PDFs with pypdf, preserving pages and the sequence of nonempty extracted lines; preserve original text files' 1-based physical lines. Chunk within a page/document into approximately 180 embedding-token chunks with 30-token overlap, never crossing policy metadata/version boundaries. Record actual tokenizer counts and adjust the chunk ceiling to the locked embedding model's supported length during phase 0. CSV locators use 1-based **data-row numbers**, excluding the header; preserve all duplicate row locators. Retrieval does not embed raw transaction tables as a substitute for SQL.

`search_evidence` first filters by immutable case ID, publication cutoff, and requested kind. Exact dense cosine search returns 20 candidates; PostgreSQL `websearch_to_tsquery('english', query)` with a `tsvector` returns 20. Reciprocal-rank fusion uses `sum(1/(60+rank))`, 1-based rank, stable source/locator ties. Rerank the top 20 fused unique chunks with the pinned cross-encoder, return at most six final chunks. The `policy` tool applies temporal policy selection independently of relevance ranking; it must retrieve all conflicting applicable versions, even if search would rank only one highly. Search hits include excluded-policy labels when specifically requested for historical comparison.

Each source has a server-generated UUID, SHA256, original relative filename, media type, and case UUID. A `Citation` is `{source_id, locator_id, label}`. Locators are `csv:r<record>`, `pdf:p<page>:l<start>-<end>`, or `text:l<start>-<end>`; ranges are inclusive. The source viewer shows the original record/page with extracted text for exact line matching. It is acceptable for PDF text order to differ visually; the viewer explicitly labels extracted line numbers. Citations resolve only against the current case. No external URLs, uploaded executable HTML, or client filesystem paths are citation targets.

## Database and role contract

Use composite case-local keys and foreign keys wherever identities are structurally required. `db.py` owns fixed SQL statements and transaction boundaries; no ORM or dynamically composed user SQL.

| Table | Principal fields and constraints |
| --- | --- |
| cases | UUID PK; unique fingerprint; case_key; merchant_id; currency; cohort_start/end; as_of; completeness JSONB; `ready|ready_with_gaps`; imported_at |
| sources | UUID PK; case_id FK; relative_name; SHA256; media_type; kind from manifest; published_at for documents (null for CSV); internal stored path; aliases JSONB; unique case/path |
| orders | PK(case_id,order_id); captured_at; gross_paise BIGINT; settlement_state; source_id; row locators JSONB |
| refunds | PK(case_id,refund_id); order_id composite FK; processed_at nullable; amount_paise BIGINT; status; source/locators |
| fees | PK(case_id,fee_id); order_id composite FK; assessed_at; amount_paise BIGINT; fee_kind; source/locators |
| payouts | PK(case_id,payout_id); received_at nullable; amount_paise BIGINT; status; source/locators |
| payout_links | PK(case_id,link_id); composite order/payout FKs; amount_paise BIGINT; recorded_at; source/locators; effective whole-order uniqueness validated at import |
| policies | case_id; policy_id; source_id; published_at; effective dates; calendar_days; fee_bps; metadata locators; unique case/policy_id/source_id |
| chunks | UUID PK; case_id/source_id FKs; locator_id; text; token_count; `vector(384)`; tsvector; embedding revision; unique source/locator/revision |
| case_findings | UUID PK; case_id; code; severity `info|gap|conflict`; affected record IDs; citations; structured details |
| runs | UUID PK; case_id; mode `fixed|agent`; question; snapshot metadata; state; timestamps; cancel_requested; result JSONB nullable; error JSONB nullable; model/prompt/config hashes |
| run_events | PK(run_id,sequence); timestamp; kind; elapsed_ms; sanitized JSONB payload |
| schema_versions | version integer PK; SQL checksum; applied_at |

SQL checks enforce nonnegative source amounts, valid enums, timestamp/state combinations, and case-scoped references. Gross/refund/fee arithmetic and correspondence contradictions are business findings, not SQL value repairs. `moneytrail_writer` can insert imports, manage runs/events, and read data, but has no superuser/role-creation privilege. `moneytrail_reader` gets SELECT only on case/source/business/policy/chunk/finding tables, no run secrets and no mutation privileges. Tool queries use the reader connection and a read-only transaction. Import and event persistence use the writer connection. The bootstrap command uses an admin connection only to create roles/grants/extensions and apply numbered SQL; the running API never loads the admin DSN.

## Typed Python interfaces

Types live in `schemas.py`; IDs are UUIDs except case-local record IDs. Datetimes are timezone-aware. `ReadConnection` names a psycopg AsyncConnection authenticated as the reader role with read-only transactions, not a custom database abstraction. Methods are async for I/O; CPU extraction/embedding runs off the event loop and remains bounded.

| Function | Arguments | Return / execution |
| --- | --- | --- |
| import_bundle | bundle_path: Path, settings: Settings | ImportResult; async |
| reconcile | case_id: UUID, db: ReadConnection | Reconciliation; async |
| inspect_order | case_id: UUID, order_id: str, db: ReadConnection | OrderEvidence; async |
| select_policy | case_id: UUID, order_ids: list[str] or None, db: ReadConnection | PolicyEvidence; async; None selects the entire cohort through SQL grouping |
| search_evidence | case_id: UUID, query: str, kind: Literal[all, policy, correspondence], limit: int = 6 | SearchResult; async |
| read_source | case_id: UUID, source_id: UUID, locator_id: str | SourceSegment; async |
| dispatch_tool | case_id: UUID, call: ToolCall | ToolResult; async |
| run_fixed | request: InvestigationRequest, run_id: UUID | InvestigationResult; async |
| run_agent | request: InvestigationRequest, run_id: UUID, cancel: asyncio.Event | InvestigationResult; async |
| complete_structured | messages: list[ChatMessage], response_type: type[BaseModel] | Validated response_type instance; async |
| assemble_context | state: InvestigationState | ContextPacket; synchronous token assembly |
| validate_result | draft: AnswerDraft, state: InvestigationState | InvestigationResult; synchronous validation |

`InvestigationRequest = {case_id:UUID, question:str}`; `mode` is selected only by the evaluation runner, with the public UI using the completed agent after phase 4. `ToolCall` is a discriminated union for exactly `reconcile`, `inspect_order(order_id)`, `select_policy(order_ids)`, `search_evidence(query,kind,limit)`, and `read_source(source_id,locator_id)`. The case ID is injected by the dispatcher, never accepted from model arguments. A `ToolResult` contains `tool_call_id`, typed data, immutable `Fact` records, citations, findings, and an elapsed time. An explicit order_ids list is limited to 20; null means the whole cohort, not an empty or truncated selection. Cohort policy lookup joins captured dates and effective intervals in SQL and returns grouped policy IDs/date spans/order counts and every applicable conflict, rather than all orders. Order inspection never returns more than 100 related rows without an explicit `RESULT_LIMIT` error directing a narrower supported question. There is no truncation that appears complete.

`Fact = {fact_id, kind, value, complete, citations, derivation}`. Monetary facts have `kind:'money'`, integer `value_paise`, currency `INR`, and a derivation identifying included/excluded record IDs. Date/rate/count facts have their own typed fields. Facts created by tools are registered in the current run; a model cannot create or change their values. Full derivations and per-order classifications remain server-side and can be inspected through scoped record/source operations. The model-facing `Reconciliation` contains six aggregate totals, grouped counts/date spans, payout-header summary, `reconciled`, and at most 20 representative finding references plus total counts and an explicit `details_omitted` flag. It never inserts up to 100,000 record IDs/rows into context. Required conflict/policy groups remain explicit; if their essential summary cannot fit, raise CONTEXT_LIMIT. The same bounded projection is used by both workflows. `OrderEvidence` supplies exact joined records and locators for an explicitly selected order.

`AnswerDraft = {outcome:'explained'|'partial'|'insufficient', claims:list[Claim], selected_fact_ids:list[str], unresolved:list[EvidenceGap]}`. A `Claim` contains `text_template`, `fact_ids`, and `citations`; `EvidenceGap` contains a typed code, cited supporting evidence if any, and `needed_evidence`. The model selects registered fact IDs; it has no amount fields to populate. Claims reference money/date/rate facts using `{{fact:<fact_id>}}` tokens, and the server expands those after validation, formatting paise without floating-point conversion. Validation rejects unregistered facts, changed values, invalid citations, malformed/unexpanded tokens, and obvious currency-plus-number literals outside a fact token. Keep this check small; do not build a language parser or reject ordinary words such as “one” or “second.” Prompts require monetary claims to use facts, and evaluation checks all prose, including amounts expressed in words, against sources. The narrative remains generative. Neither schema validation nor the simple literal check proves semantic truth; unsupported or invented monetary prose fails the hard evaluation gate.

The final `InvestigationResult` contains run/case IDs, cutoff/cohort, outcome, validated claims, full selected facts, reconciliation when available, citations, unresolved gaps, and budget/trace summary. The server may downgrade `explained` when critical findings remain; it must never upgrade the model's uncertainty. Tool/model failures are run errors, not `insufficient` answers. If there is not enough verified evidence at a budget limit, finish `insufficient` with `BUDGET_EXHAUSTED`; no extra model call is permitted to make it sound better.

## Fixed workflow, bounded loop, and context

The fixed baseline executes reconcile → select_policy with order_ids null for whole-cohort date/policy groups → search correspondence with the question → one structured synthesis/validation. SQL performs cohort policy grouping even for more than 20 orders; only explicit detail selections have the 20-order cap. It uses the same retrieval, model, evidence limits, and result schema as the agent. The agent bootstraps the deterministic reconciliation, then uses the remaining tool budget for selected evidence. Both pipelines answer arbitrary in-scope questions; neither maps a question to a saved fixture answer.

The agent graph is `bootstrap → decide → tools → decide`, with `decide → validate → finish` and explicit error/cancel exits. Bootstrap reconciliation counts as one of six tool calls. Each model `decide` call returns `Decision = {action:'tools'|'answer', calls:list[ToolCall], answer:AnswerDraft|null}` with mutually exclusive fields. At most two calls are permitted in one decision; execute sequentially, count every dispatched call including a permitted retry, and stop before an over-budget dispatch. Four model calls include final synthesis. The fourth decision must be `answer`; an attempted further tool turn yields `BUDGET_EXHAUSTED`. The graph recursion cap is an additional guard (16), not a replacement for explicit counters.

State is `{run_id,case_id,question,cohort,as_of,phase,tool_calls_used,model_turns_used,deadline,cancelled,facts,evidence,findings,unresolved,selected_sources,last_decision}`. Working memory stores references and short tool summaries, not an ever-growing transcript or hidden reasoning. No conversational memory persists across investigations; completed run records are inspection artifacts. Documents and tool text are labelled untrusted evidence and are never inserted into the system instruction channel.

Input context budget defaults to 6,000 actual tokens with the pinned Qwen tokenizer; request output allowance is 1,024 tokens and Ollama context is 8,192. Phase 0 verifies chat-template accounting plus schema/system overhead and records any conservative safety margin. Count the entire serialized prompt/schema before every call. Reserve space for system/rules, question, reconciliation/facts, and unresolved findings first; then add reranked evidence in deterministic order until the budget is reached. Drop whole low-priority optional chunks and record omissions. Never truncate critical numbers, citation locators, or policy conflicts. If mandatory content cannot fit, raise `CONTEXT_LIMIT`. No unmeasured character-to-token shortcut is accepted as the final implementation.

Default inference is temperature 0, non-streaming, native `think=false` after the pinned Qwen/Ollama combination is qualified for it in phase 0, one active inference, 60 seconds per model call, 240 seconds per run, and a 5-second SQL statement timeout. If that setting is unsupported, the pilot reports the incompatibility for review rather than silently relying on different behavior. Permit at most one retry total for a transient failed read before response data is consumed; log it and count the tool dispatch. Do not retry model schema errors, policy conflicts, import errors, or entire runs automatically. Retry behavior is finite and explicit, not a fallback matrix.

The API reserves the single active-run slot before creating a task; a second request receives `409 RUN_BUSY`. A cancellation sets the run flag/event, cancels the active async provider request, releases the slot in `finally`, and persists `cancelled`. Check the flag between graph nodes and before recording an answer. Process startup marks previously `running` runs `failed` with `PROCESS_INTERRUPTED`; it does not resume them. The caller may explicitly start a fresh run. Provider unavailability becomes `MODEL_UNAVAILABLE` with instructions to check Ollama/model digest. A saved replay, if subsequently requested, must be prominently labelled replay and must never satisfy live acceptance.

## HTTP and UI contract

All routes below except `/healthz` require `Authorization: Bearer <owner token>`. Error envelope: `{error:{code,message,details,request_id}}`; details include only safe fields. UUIDs identify server resources. The response has no raw exception stack, secret, filesystem path, or provider prompt.

| Method / path | Request and response |
| --- | --- |
| GET `/healthz` | 200 `{status:'ok'}` for process liveness only; it does not claim model readiness. |
| POST `/api/v1/cases/import` | Multipart `bundle`; 201/200 `{case_id,status,fingerprint,reused,record_counts,duplicate_findings,evidence_findings}`. |
| GET `/api/v1/cases` | 200 case summaries including cohort/cutoff and ready/gap label. |
| GET `/api/v1/cases/{case_id}` | 200 metadata, counts, source summaries, completeness, and findings. |
| POST `/api/v1/investigations` | JSON InvestigationRequest; 202 `{run_id,state:'running'}` or 409 busy. |
| GET `/api/v1/runs/{run_id}` | 200 `{run_id,case_id,state,result,error,started_at,finished_at}`. States: running, completed, failed, cancelled. Completed includes explained/partial/insufficient result. |
| GET `/api/v1/runs/{run_id}/events?after=0` | Ordered events with sequence greater than after, max 100; `{events,next_after,terminal}`. UI polls every second, stops after terminal plus final result fetch. No SSE. |
| POST `/api/v1/runs/{run_id}/cancel` | Empty body; 202 while cancelling, 200 if already terminal; does not rewrite a completed result. |
| GET `/api/v1/cases/{case_id}/sources/{source_id}` | Source metadata and locator list; 404 if case/source pair is absent. |
| GET `/api/v1/cases/{case_id}/sources/{source_id}/segments?locator_id={locator_id}` | Exact escaped text/CSV record and citation metadata; URL-encode the observed locator value. |
| GET `/api/v1/cases/{case_id}/sources/{source_id}/content` | Authenticated original PDF or plain-text content with controlled media type and safe disposition; no executable HTML. |

Use 401 for missing/invalid token, 404 for resources outside the requested case, 413 for size, 415 for unsupported media, 422 for schema/import contract, 409 for busy, and terminal run errors for failures after 202. Imports are short awaited requests; prohibit a new import while an investigation is active and return 409 to keep one bounded local workload. A failed import is not an investigation run.

The UI keeps token and API state in memory. Use a simple owner-token entry followed by a case list/upload area, natural-language question area, progress/cancel controls, result/fact table, source pane, and expandable trace. Fetch PDFs using the token, create a temporary blob URL, and revoke it on close; do not put tokens in URLs. Escape correspondence and model text; do not render uploaded Markdown as trusted HTML. Disable repeat submission while importing/running. Explicitly show snapshot date, source completeness, live/error state, and unknown amounts.

## Security and observability

Bind the app and provider to loopback. A trusted remote-lab session may use a user-established private tunnel; do not publish provider ports or document credentials/SSH addresses. Runtime configuration must refuse a non-loopback Ollama URL unless the owner explicitly configures an approved private endpoint; no source or model output can change that URL. Same-origin serving needs no permissive CORS. Require token auth even on local APIs. No browser persistent storage, anonymous import, external retrieval, arbitrary network/SQL/file tools, or uploaded code execution.

Event kinds are `run_started`, `context_built`, `model_started`, `model_finished`, `tool_started`, `tool_finished`, `retrieval_selected`, `retry`, `cancel_requested`, and `run_finished`. Store model digest, prompt/schema/config hash, token counts, selected source IDs/scores, durations, and sanitized error codes. Do not persist chain-of-thought, token, DSNs, credentials, raw arbitrary environment, or full provider request headers. Tool result references are sufficient; structured monetary facts are already immutable run data. Each event sequence increases monotonically under one run writer. UI trace explains operations and evidence selections, not private model reasoning.

## Configuration and eventual commands

`.env.example` is a names/defaults template; generate real local credentials during implementation and never commit `.env`. Runtime `Settings` fails startup with specific missing/invalid-setting names, never their values. Pin operational defaults below; a material change requires recorded evaluation impact. Admin/Postgres setup values are read only by bootstrap/Compose, not logged or passed to the model.

Phase-0 pilot configuration is separate from runtime Settings: it accepts initially blank digest/revision fields specifically to discover and report the actual identities. It cannot start a normal investigation. Once qualification is accepted, normal application startup/runs require all pinned identities and verify them. This avoids making first-time model discovery depend on an identity not yet known.

Local setup splits the template into two ignored files: `.env` contains only runtime MONEYTRAIL settings except the admin DSN; `.env.setup` contains only `MONEYTRAIL_ADMIN_DATABASE_URL` and the three POSTGRES settings. Generate the owner token and separate admin/writer/reader passwords with Python `secrets.token_urlsafe(32)` during MT0.1, writing directly to those local files without printing secret values or overwriting an existing configuration. Record only filenames in output. Use the generated role passwords in the corresponding DSNs. After these files exist, set `export UV_ENV_FILE=.env` in Bash (PowerShell: `$env:UV_ENV_FILE='.env'`); subsequent ordinary `uv run` commands load runtime values through uv's built-in [environment-file support](https://docs.astral.sh/uv/reference/environment/#uv_env_file). Bootstrap/migrate and database tests explicitly load both files with `uv run --env-file .env --env-file .env.setup`; never load `.env.setup` into the application. Compose uses `--env-file .env.setup` for database initialization/interpolation and gives the app only `env_file: .env`; no admin DSN/password is passed to the app service. Do not export setup secrets into the parent shell.

| Name | Template value / purpose |
| --- | --- |
| `MONEYTRAIL_DATABASE_URL` | Empty; required restricted writer PostgreSQL DSN. |
| `MONEYTRAIL_READ_DATABASE_URL` | Empty; required SELECT-only PostgreSQL DSN. |
| `MONEYTRAIL_ADMIN_DATABASE_URL` | Empty; setup-only admin DSN for bootstrap/migrate. |
| `POSTGRES_USER` | `moneytrail_admin`; Compose initialization only. |
| `POSTGRES_DB` | `moneytrail`; Compose initialization only. |
| `POSTGRES_PASSWORD` | Empty; generated local secret for Compose, never a committed default. |
| `MONEYTRAIL_OWNER_TOKEN` | Empty; required unpredictable local secret, at least 32 characters. |
| `MONEYTRAIL_OLLAMA_URL` | `http://127.0.0.1:11434` |
| `MONEYTRAIL_MODEL` | `qwen3:8b`; initial pilot candidate. |
| `MONEYTRAIL_MODEL_DIGEST` | Empty until phase 0 records exact Ollama digest; thereafter required and verified before runs. |
| `MONEYTRAIL_MODEL_TOKENIZER` | `Qwen/Qwen3-8B` |
| `MONEYTRAIL_TOKENIZER_REVISION` | Empty until phase 0 resolves and locks a commit revision. |
| `MONEYTRAIL_EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` |
| `MONEYTRAIL_EMBEDDING_REVISION` | Empty until phase 0 records a commit revision. |
| `MONEYTRAIL_RERANKER_MODEL` | `cross-encoder/ms-marco-MiniLM-L6-v2` |
| `MONEYTRAIL_RERANKER_REVISION` | Empty until phase 0 records a commit revision. |
| `MONEYTRAIL_DATA_DIR` | `./data`; resolve once to an absolute approved local directory. |
| `MONEYTRAIL_INPUT_TOKEN_BUDGET` | `6000` |
| `MONEYTRAIL_MAX_TOOL_CALLS` | `6` |
| `MONEYTRAIL_MAX_MODEL_TURNS` | `4` |
| `MONEYTRAIL_MODEL_TIMEOUT_SECONDS` | `60` |
| `MONEYTRAIL_RUN_TIMEOUT_SECONDS` | `240` |

The following commands are the eventual Linux/WSL reference workflow, to be executed only when ROADMAP has created their files and the preceding gate is approved. Native Windows may use the same uv/npm commands; `scripts/*.sh` require Bash. Phase 0 documents installation prerequisites from official installers, then records installed Python 3.12, Node 24 LTS patch, uv, Docker, and Ollama versions. Never use `uv sync --frozen` before `uv.lock` exists.

```bash
# After dependency/package files and lockfiles exist:
uv sync --frozen --extra dev
npm --prefix frontend ci
npm --prefix frontend run build

# After compose.yaml and sql/001_initial.sql exist and local credentials are set:
docker compose --env-file .env.setup up -d db
uv run --env-file .env --env-file .env.setup python -m moneytrail.db bootstrap --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL
uv run --env-file .env --env-file .env.setup python -m moneytrail.db migrate --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL

# After the provider is installed and the phase-0 digest is locked:
ollama serve
# In another terminal, only when a model download is required:
ollama pull qwen3:8b
uv run python -m moneytrail.llm pilot --output evals/reports/phase0.json

# After the API/UI implementation exists:
uv run uvicorn moneytrail.api:app --host 127.0.0.1 --port 8000 --workers 1

# After fixtures/tests/evaluation CLI exist:
uv run python -m moneytrail.evaluation build-fixtures --output fixtures --eval-root evals
uv run --env-file .env --env-file .env.setup pytest -q
uv run python -m moneytrail.evaluation run --split dev --mode fixed
uv run python -m moneytrail.evaluation run --split dev --mode agent
```

`db bootstrap` creates roles with safely quoted identifiers and parameterized secret handling using the supplied DSNs, installs `vector`, and grants schema access. `migrate` verifies checksums, applies unapplied numbered SQL in order, and grants the prescribed table privileges. Re-running both is idempotent; changed applied SQL is an error. A phase-0/Postgres installation probe may use a disposable manually started database; final Compose consolidation occurs only in phase 6. The final Docker image runs the already-built frontend and FastAPI, with the same schema and pinned dependencies. Compose contains only `db` and `app`; Ollama stays a separately managed host process. The final reference deployment is native Linux: `app` uses `network_mode: host`, binds FastAPI to `127.0.0.1:8000`, and reaches Ollama at host loopback. `db` publishes PostgreSQL only on `127.0.0.1:5432`; app DSNs use that address. Host networking gives the trusted app access to host network interfaces, so keep all services bound to loopback and do not claim container network isolation. Native Windows development uses host API plus Docker database; the complete Linux Compose reference is verified on Linux rather than assumed to work identically in Docker Desktop. No proxy/forwarder is added.

The configuration, port binding, model access from containers, and fresh-clone commands are not verified until the corresponding roadmap checkpoint. A clean-clone failure blocks final delivery rather than being replaced by a replay.
