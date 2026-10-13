-- 001_initial.sql: Initial schema for MoneyTrail

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_versions (
    version INTEGER PRIMARY KEY,
    checksum CHAR(64) NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cases (
    case_id UUID PRIMARY KEY,
    fingerprint VARCHAR(64) NOT NULL UNIQUE,
    case_key VARCHAR(64) NOT NULL,
    merchant_id VARCHAR(64) NOT NULL,
    currency VARCHAR(3) NOT NULL,
    cohort_start TIMESTAMPTZ NOT NULL,
    cohort_end TIMESTAMPTZ NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    completeness JSONB NOT NULL,
    status VARCHAR(32) NOT NULL CHECK (status IN ('ready', 'ready_with_gaps')),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS sources (
    source_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    relative_name TEXT NOT NULL,
    sha256 CHAR(64) NOT NULL,
    media_type VARCHAR(64) NOT NULL,
    kind VARCHAR(32) NOT NULL,
    published_at TIMESTAMPTZ,
    internal_path TEXT NOT NULL,
    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE (case_id, relative_name)
);

CREATE TABLE IF NOT EXISTS orders (
    case_id UUID NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    order_id VARCHAR(64) NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    gross_paise BIGINT NOT NULL CHECK (gross_paise >= 0),
    settlement_state VARCHAR(32) NOT NULL CHECK (settlement_state IN ('paid', 'pending', 'unknown')),
    source_id UUID NOT NULL REFERENCES sources(source_id),
    row_locators JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (case_id, order_id)
);

CREATE TABLE IF NOT EXISTS refunds (
    case_id UUID NOT NULL,
    refund_id VARCHAR(64) NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    processed_at TIMESTAMPTZ,
    amount_paise BIGINT NOT NULL CHECK (amount_paise >= 0),
    status VARCHAR(32) NOT NULL CHECK (status IN ('completed', 'pending')),
    source_id UUID NOT NULL REFERENCES sources(source_id),
    row_locators JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (case_id, refund_id),
    FOREIGN KEY (case_id, order_id) REFERENCES orders(case_id, order_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS fees (
    case_id UUID NOT NULL,
    fee_id VARCHAR(64) NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    assessed_at TIMESTAMPTZ NOT NULL,
    amount_paise BIGINT NOT NULL CHECK (amount_paise >= 0),
    fee_kind VARCHAR(32) NOT NULL CHECK (fee_kind IN ('processing')),
    source_id UUID NOT NULL REFERENCES sources(source_id),
    row_locators JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (case_id, fee_id),
    FOREIGN KEY (case_id, order_id) REFERENCES orders(case_id, order_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS payouts (
    case_id UUID NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    payout_id VARCHAR(64) NOT NULL,
    received_at TIMESTAMPTZ,
    amount_paise BIGINT NOT NULL CHECK (amount_paise >= 0),
    status VARCHAR(32) NOT NULL CHECK (status IN ('received', 'scheduled')),
    source_id UUID NOT NULL REFERENCES sources(source_id),
    row_locators JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (case_id, payout_id)
);

CREATE TABLE IF NOT EXISTS payout_links (
    case_id UUID NOT NULL,
    link_id VARCHAR(64) NOT NULL,
    payout_id VARCHAR(64) NOT NULL,
    order_id VARCHAR(64) NOT NULL,
    amount_paise BIGINT NOT NULL CHECK (amount_paise >= 0),
    recorded_at TIMESTAMPTZ NOT NULL,
    source_id UUID NOT NULL REFERENCES sources(source_id),
    row_locators JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (case_id, link_id),
    FOREIGN KEY (case_id, payout_id) REFERENCES payouts(case_id, payout_id) ON DELETE CASCADE,
    FOREIGN KEY (case_id, order_id) REFERENCES orders(case_id, order_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS policies (
    case_id UUID NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    policy_id VARCHAR(64) NOT NULL,
    source_id UUID NOT NULL REFERENCES sources(source_id),
    published_at TIMESTAMPTZ NOT NULL,
    effective_from DATE NOT NULL,
    effective_to DATE,
    calendar_days INTEGER NOT NULL CHECK (calendar_days >= 0),
    fee_bps INTEGER NOT NULL CHECK (fee_bps >= 0),
    metadata_locators JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (case_id, policy_id, source_id)
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    source_id UUID NOT NULL REFERENCES sources(source_id),
    locator_id VARCHAR(64) NOT NULL,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    embedding vector(384) NOT NULL,
    tsv tsvector NOT NULL,
    embedding_revision VARCHAR(64) NOT NULL,
    UNIQUE (source_id, locator_id, embedding_revision)
);

CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING gin(tsv);

CREATE TABLE IF NOT EXISTS case_findings (
    finding_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    code VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL CHECK (severity IN ('info', 'gap', 'conflict')),
    affected_record_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS runs (
    run_id UUID PRIMARY KEY,
    case_id UUID NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    mode VARCHAR(16) NOT NULL CHECK (mode IN ('fixed', 'agent')),
    question TEXT NOT NULL,
    snapshot_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    state VARCHAR(32) NOT NULL CHECK (state IN ('running', 'completed', 'failed', 'cancelled')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    result JSONB,
    error JSONB,
    hashes JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS run_events (
    run_id UUID NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind VARCHAR(64) NOT NULL,
    elapsed_ms INTEGER NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (run_id, sequence)
);
