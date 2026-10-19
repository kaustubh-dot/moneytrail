# MoneyTrail

An evidence-backed merchant payment investigator: explain why recorded sales and received payments differ, using source documents, transaction records, and deterministic calculations.

**Status: Remediated following Independent Implementation Audit and Re-Audits (Rounds 1–8 remediated). Clean-clone live setup, offline CI, strict freeze manifest & runtime environment binding, retrieval provenance fail-closed validation, SHA256 review sidecar binding, explicit Ollama serving limits (8192 ctx / 1024 out), warm probe gate enforcement, robust processor placement detection, and 82 passing tests (1 live test skipped) in offline regression suite. Historical baseline evaluation recorded at commit `554fc4ed`; Gate G0 feasibility unresolved pending serving/pilot remeasurement on unconstrained demo host.**

Private repository: [kaustubh-dot/moneytrail](https://github.com/kaustubh-dot/moneytrail).

---

## 1. Executive Summary & Capabilities

A merchant imports an immutable case bundle (orders, refunds, fees, payouts, payout links, policies, and correspondence), asks an original natural-language question, and receives an auditable, evidence-backed explanation. 

- **Deterministic Integer Paise Accounting**: All currency arithmetic is strictly computed in integer paise outside the LLM. Floating-point numbers are prohibited in business calculation paths.
- **Strict Grounding & Zero Hallucination**: Claim templates require fact tokens (`{{fact:id}}`) and source citations (`source_id:locator_id`). Raw ungrounded currency literals in claim text are strictly rejected.
- **Bounded LangGraph Investigation**: Explicit execution graph (`bootstrap -> decide -> tools -> validate -> finish`) with recursion limit 16, maximum 6 dispatched tool calls, maximum 4 model turns, and a hard 240-second monotonic run deadline.
- **Hybrid RAG & Reranking**: Reciprocal Rank Fusion (RRF $k=60$) combining PostgreSQL full-text (`websearch_to_tsquery`) and pgvector dense cosine search (`all-MiniLM-L6-v2`), followed by Cross-Encoder reranking (`ms-marco-MiniLM-L6-v2`).
- **Honest Uncertainty**: If evidence is missing, conflicting, or out-of-cohort, the system returns `partial` or `insufficient`—never inventing plausible amounts or unsupported resolutions.

---

## 2. Architecture & Security Boundaries

```
[Merchant Browser UI] (React 18 + TypeScript + Vite)
         |
         | Authenticated Bearer Token (In-memory only, no localStorage)
         v
[FastAPI Monolith] (:8000, 127.0.0.1 loopback)
   |-- Case Management & ZIP Import
   |-- Deterministic Reconciliation Engine (pure integer paise)
   |-- Hybrid Retrieval & Reranker (dense + lexical + RRF + Cross-Encoder)
   `-- LangGraph Agent Runner (max 6 tools, 4 turns, 6000 token context budget)
         |                             |
         v                             v
[PostgreSQL 16 + pgvector]    [Local Ollama Daemon]
 (:5432, loopback)             (:11434, loopback)
 - writer role (app mutations) - Model: qwen3:8b
 - reader role (tool reads)    - Pinned Digest: 500a1f067a9f...
 - Case-scoped composite keys
```

### Security & Isolation Controls
- **In-Memory Authentication**: Owner tokens exist solely in client memory and are never persisted in `localStorage`, `sessionStorage`, or backend logs.
- **Scoped Read-Only Database Role**: Investigation tools operate exclusively over a restricted `moneytrail_reader` role. Attempted write queries inside tools fail immediately with `ReadOnlySqlTransaction`.
- **Zip-Slip & Path Traversal Guards**: ZIP bundle extraction verifies file path containment and rejects absolute paths, directory traversal sequences (`..`), and oversized archives (>50 MB).
- **Strict Loopback Binding**: Application, database, and Ollama listeners bind exclusively to Linux host loopback (`127.0.0.1`), ensuring zero public attack surface.

---

## 3. Verified Setup & Local Execution

### Prerequisites
- Linux OS (Ubuntu 22.04+ or Debian reference)
- Python 3.12 with [Astral `uv`](https://docs.astral.sh/uv/)
- Node.js 22 LTS with `npm`
- PostgreSQL 16 with `pgvector`
- [Ollama](https://ollama.com/) with model `qwen3:8b` installed (`ollama pull qwen3:8b`)

### Configuration
Generate runtime credentials into two local, git-ignored files using `.env.example` as a template:
1. `.env`: Contains runtime application settings (DSNs for `moneytrail_writer` and `moneytrail_reader`, owner token, model pins, token budgets).
2. `.env.setup`: Contains setup-only credentials (`MONEYTRAIL_ADMIN_DATABASE_URL`, `POSTGRES_PASSWORD`). Admin credentials are never passed to the running application service.

### Quickstart (Native Linux)
To run the automated local startup script:
```bash
# 1. Install dependencies and build frontend
uv sync --frozen --extra dev
npm --prefix frontend ci
npm --prefix frontend run build

# 2. Start local stack (handles DB check, migrations, Ollama verification, and server startup)
bash scripts/start-local.sh
```
The application will be accessible at `http://127.0.0.1:8000`.

### Docker & Compose Reference Deployment
```bash
# Validate Compose configuration
docker compose --env-file .env.setup config --quiet

# Start database service
docker compose --env-file .env.setup up -d db

# Bootstrap and migrate database schema
uv run --env-file .env --env-file .env.setup python -m moneytrail.db bootstrap --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL
uv run --env-file .env --env-file .env.setup python -m moneytrail.db migrate --admin-dsn-env MONEYTRAIL_ADMIN_DATABASE_URL

# Build and start the monolith container with Linux host networking
docker compose --env-file .env.setup build app
docker compose --env-file .env.setup up -d app
```

---

## 4. Verification & Testing

### Offline Test Regression Suite
Runs the complete offline unit and regression suite (70 passing tests, 1 live test skipped) without model downloads or external network access:
```bash
uv run --env-file .env --env-file .env.setup pytest -v
```

### Live End-to-End Smoke Test
Uploads `fixtures/demo.zip` through the real authenticated API, verifies exact integer reconciliation, asks an original natural-language question, polls the agent run to completion, validates result grounding, and resolves the cited source segment:
```bash
bash scripts/smoke.sh --live
```

### Dataset Validation & Frozen Manifest Check
```bash
# Validate dataset splits
uv run python -m moneytrail.evaluation validate --split dev
uv run python -m moneytrail.evaluation validate --split test

# Validate freeze manifest integrity
uv run python -c "from pathlib import Path; from moneytrail.evaluation import validate_freeze; validate_freeze(Path('evals/reports/freeze.json'))"
```

---

## 5. Live Demonstration Script

Follow this sequence to demonstrate MoneyTrail's capabilities across the three test families:

### Step 1: Normal Settlement & Headline Accounting (Family A)
1. Navigate to `http://127.0.0.1:8000` and supply the owner bearer token.
2. In the **Import** panel, upload `fixtures/demo.zip` (synthetic Meridian Store case).
3. Verify headline cohort accounting totals:
   - **Gross Sales:** INR 240,000 (24,000,000 paise)
   - **Received Payments:** INR 186,000 (18,600,000 paise)
   - **Completed Refunds:** INR 18,000 (1,800,000 paise)
   - **Assessed Fees:** INR 6,000 (600,000 paise)
   - **Documented Pending:** INR 30,000 (3,000,000 paise)
   - **Unexplained Difference:** INR 0 (0 paise)
4. Ask an original query: *"Why does the recorded gross sales amount differ from received payments?"*
5. Observe live agent progress through the run trace. The agent inspects transactions, verifies pending order `O-005`, and outputs an `explained` outcome with grounded claim templates and CSV citations.

### Step 2: Idempotent Deduplication & Missing Evidence (Family B)
1. Re-upload `fixtures/demo.zip`. Observe that the system detects the identical bundle fingerprint and returns `reused: true` with identical record counts without creating duplicate rows.
2. Upload a case with missing payout links (e.g. `evals/dev/dev-b1.zip`).
3. Ask for the reconciliation explanation: the agent detects missing settlement linkages and honestly outputs `partial` or `insufficient` with explicit evidence gaps (`gap_missing_payout_links`), refusing to guess or invent missing payment links.

### Step 3: Policy Conflict & Superseded Terms (Family C)
1. Upload a case with overlapping effective policies or superseded terms (e.g. `evals/dev/dev-c3.zip`).
2. Ask: *"What fee rate applied to September orders?"*
3. Observe that the investigator identifies policy conflict findings (`conflict_overlapping_policies`), cites both approved policy documents together, and notes the applicable rates without silently choosing one.
4. Test in-flight cancellation: submit a complex query and click **Cancel**. The run immediately halts before the next turn and records terminal state `cancelled`.

---

## 6. Factual Interview & Technical Notes

### 1. Merchant Discrepancy Problem & Role of LLMs
Merchants frequently observe that gross sales recorded in their shopping cart do not match net deposits in their bank account. The discrepancy consists of multiple intertwined factors: completed customer refunds, processor interchange fees, settlement hold periods, and timing cutoffs. 
- **LLMs are unsuited for financial arithmetic**: Even advanced models hallucinate subtraction or round decimal cents. In MoneyTrail, arithmetic is strictly computed in Python and PostgreSQL using integer paise.
- **LLMs excel at evidence interpretation**: The model is bounded to extracting fee rules from messy PDF policy documents, interpreting merchant email correspondence, and identifying which orders fall within contractual terms.

### 2. Request & Data Lifecycle
1. **Ingest**: Bundle is unzipped in memory, verified against schema, and written atomically to Postgres. Chunks are extracted with exact line/row locators and embedded.
2. **Bootstrap**: When an investigation starts, an integer reconciliation tool is dispatched automatically to register baseline cohort totals into working memory.
3. **Decide**: The LangGraph agent inspects available evidence, decides whether more tools are needed (`search_evidence`, `inspect_order`, `select_policy`, `read_source`), and executes up to 2 tools per turn.
4. **Validate**: Generated claims are checked against registered tool facts. If an ungrounded number is present, the agent enters a reflection loop to self-correct.
5. **Serve**: The completed result is persisted with structured trace events, citations, and evidence gaps.

### 3. Serving Tradeoffs & Budgets
- **Hardware Profile**: Evaluated on Intel Core Ultra 9 285 with local Ollama (`qwen3:8b`).
- **Context Budget**: Pinned to 6,000 tokens using the exact Qwen tokenizer. System prompt and schema overhead are measured up front; low-scoring chunks are deterministically dropped if budget is reached.
- **Execution Bounds**: Maximum 4 model decision turns, maximum 6 dispatched tool calls, and a monotonic 240-second wall-clock deadline. Observed test split p50 latency was 7.42s; p95 latency was 11.04s.

### 4. Key Engineering Challenges Encountered & Fixed
1. **Source Citation Registration**: During held-out evaluation, `manifest.json` metadata citations failed validation because the manifest was not initially cataloged as a queryable source. Fixed by registering the manifest as an authentic source during bundle ingestion.
2. **Policy Conflict Extraction**: The agent initially failed on dual-policy cases because conflict details were nested at the top level of findings rather than inside `details`. Fixed by supporting both top-level and nested policy structures.
3. **Out-of-Cohort Date Guards**: Specific single-order queries referencing previous-month policies triggered an aggregate out-of-cohort warning. Fixed by scoping out-of-cohort detection to cohort-wide queries lacking specific order IDs.
4. **Strict Citation Validation**: The LLM occasionally guessed sub-ranges or malformed locators. Fixed by strictly validating citations against genuine registered case locators; invalid or cross-case citations are rejected without silent rewriting.
5. **Test Fixture Isolation**: Tests inserting temporary overflow records caused subsequent runs to observe stale fee counts. Fixed by ensuring test teardown blocks always purge injected records.

---

## 7. Explicit Limitations & Non-Goals

- **Synthetic Data**: Evaluated on synthetic merchant datasets designed to reflect realistic transaction cohorts and edge cases. Not tested on live proprietary bank feeds.
- **Single-Node Local Architecture**: Designed for local merchant ownership on a single workstation or server. Not built as a multi-tenant cloud SaaS or distributed cluster.
- **No Direct Fund Movement**: The system investigates, explains, and audits payment differences; it does not trigger bank transfers, execute refunds, or modify processor ledger balances.
