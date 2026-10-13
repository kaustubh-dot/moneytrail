# MoneyTrail

An evidence-backed merchant payment investigator: explain why recorded sales and received payments differ, using source documents, transaction records, and deterministic calculations.

**Status: planning and repository scaffold only. The application, model integration, evaluations, and deployment have not been implemented or verified.**

Private repository: [kaustubh-dot/moneytrail](https://github.com/kaustubh-dot/moneytrail).

## The product we are building

A fictional merchant can import a supported case bundle, ask a natural-language question, inspect the resulting investigation, and follow each material claim back to its evidence. The LLM selects and interprets evidence; Python and SQL compute financial amounts. Missing evidence remains missing rather than becoming a plausible invented explanation.

The headline demonstration is a synthetic case with INR 240,000 in gross sales and INR 186,000 received: the system investigates refunds, fees, and genuinely evidenced pending settlements. The gap is not automatically classified as pending money. The financial identity and source-of-truth rules live in [ARCHITECTURE.md](ARCHITECTURE.md).

Planned capabilities:

- Supported bundle import with source provenance and explicit import errors.
- Retrieval over payout policies and correspondence, combined with read-only transaction tools.
- Policy-date-aware explanations, source inspection, and exact financial totals.
- Bounded investigation, cancellation, actionable errors, and inspectable run traces.
- A measured baseline comparison, focused safety checks, and a short repeatable demonstration.

This is not an accounting platform, annual-report chatbot, trading assistant, bank integration, or payment execution service. Fine-tuning is not required for the product or its completion.

## Read the planning pack

| File | Authority and purpose |
| --- | --- |
| [PRD.md](PRD.md) | Canonical product vision, scope, requirement IDs, acceptance criteria, and non-goals. |
| [AGENTS.md](AGENTS.md) | Binding execution rules, vision preservation, simplicity, verification, and handoff contract. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Canonical data, financial, tool, API, configuration, and module contracts. |
| [ROADMAP.md](ROADMAP.md) | Ordered implementation phases, task procedures, commands, dependencies, and review gates. |
| [TASKS.md](TASKS.md) | Current task statuses, gate approvals, decisions, blockers, and the exact next action. |
| [EVALUATION.md](EVALUATION.md) | Evaluation cases, baselines, acceptance measurements, and essential regression checks. |
| [.env.example](.env.example) | Configuration names only; local credentials and pinned model identities must be supplied during implementation. |

The docs are intentionally complementary. Do not create a second competing roadmap or copy the PRD into other files.

## Architecture summary

One Python/FastAPI application, a thin React/TypeScript UI, one PostgreSQL database with pgvector, and a separately operated Ollama model endpoint. One bounded LangGraph investigator uses explicit tools. Retrieval uses local embedding/reranking models. Run traces remain in the application database; no external observability platform is required.

The initial local model candidate is `qwen3:8b`. Its identity, available memory, structured-output/tool behavior, and usable latency are qualification checks, not promises. Linux lab access with Docker and sudo is authorized; actual available GPU capacity and installed software still need measurement. The lab is not assumed to be a permanent public host.

## Setup and execution status

There is no runnable application yet. Do not run application setup commands against this planning scaffold and report success. The exact commands are introduced in [ROADMAP.md](ROADMAP.md) when the required files and lockfiles are created.

The planned implementation environment is Linux, Python 3.12, Node.js 24 LTS, Git, `uv`, Docker/Compose, and access to the qualified Ollama endpoint. Dependency versions are locked during the first implementation phase. Windows can hold the repository and edit the documents; Linux is the reference runtime.

Configuration is documented in [ARCHITECTURE.md](ARCHITECTURE.md). During the configuration task, use `.env.example` as the names template and follow the documented split between ignored runtime `.env` and setup-only `.env.setup`. Do not give the running API administrator credentials. Never copy SSH passwords, private keys, real merchant information, or access tokens into a tracked file. Blank required credentials must produce clear startup errors.

Run one flagship model workload at a time unless actual capacity measurements establish that concurrent operation is practical. This repository must not depend on IncidentLab being present.

## First implementation handoff

Give the implementation agent this instruction:

> Read AGENTS.md, PRD.md, ARCHITECTURE.md, ROADMAP.md, TASKS.md, and EVALUATION.md. Report the current verified checkpoint and the first incomplete task. Execute only the phase I explicitly authorize. Preserve the PRD, use the documented architecture, and do not introduce additional features or infrastructure. At the phase gate, show evidence, update TASKS.md, commit the coherent checkpoint, and stop for review before the next phase. Do not call planned or mocked behavior implemented.

The user has authorized this project's direction and creation of this planning repository. Implementation phase approvals are recorded separately in `TASKS.md`; repository creation is not permission to execute all phases unattended.

## Intended interview demonstration

This sequence is a delivery target, not a claim that a demo already exists:

1. Start the documented application and open its local UI.
2. Import the included synthetic merchant case.
3. Ask why the sales total differs from received payments.
4. Inspect the monetary breakdown, transaction rows, policy version, and citations.
5. Ask which pending settlements are overdue as of the case's cutoff date.
6. Open a case with missing or conflicting evidence and show the explicit limitation.
7. Show one evaluation comparison and a trace explaining a real implementation failure and its fix.

Demo data may be prepared, but the investigation must execute the actual model and tools. Any saved replay must be clearly labelled and must not pretend to be live inference.

## Interview preparation at completion

Before the project is marked complete, replace this preparation checklist with concise, factual notes based on the built system:

- Explain the merchant problem and why an LLM is useful for evidence interpretation but not arithmetic.
- Trace one request through ingestion, retrieval, tools, validation, and the response UI.
- Explain the financial identity, data boundaries, and why one database is sufficient.
- Present measured quality and latency, baseline results, and failures without inflated claims.
- Explain why fine-tuning, unrestricted SQL, bank integrations, and additional agents were excluded.
- Describe the most consequential bug actually encountered and how it was verified as fixed.

## Completion standard

All mandatory PRD requirements must work and pass their mapped acceptance checks; the core demo and clean-clone setup must be verified; critical security and financial-integrity failures must be resolved. Known limitations must be documented. File presence, test quantity, or an attractive UI alone does not establish completion.
