"""Policy regression tests for effective date selection, conflict citations, cutoff exclusions, and cohort grouping."""

import json
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
import psycopg

from moneytrail.config import Settings
from moneytrail.db import get_reader_connection, get_writer_connection
from moneytrail.ingest import import_bundle
from moneytrail.schemas import PolicyEvidence
from moneytrail.tools import select_policy


@pytest.mark.asyncio
async def test_demo_policy_resolution(test_settings: Settings):
    """
    In the canonical demo bundle:
    - POL-2026-09-CURRENT is effective from 2026-09-01 (open, T+2, 250 bps)
    - POL-2026-08-PREVIOUS was effective 2026-08-01 to 2026-08-31 (superseded)
    Cohort lookup (order_ids=None) resolves POL-2026-09-CURRENT for all 5 orders without conflict.
    Explicit inspection of O-005 resolves expected settlement date (Oct 2) and fee (80,000 paise).
    """
    demo_zip = Path("fixtures/demo.zip")
    assert demo_zip.exists()

    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    reader_conn = await get_reader_connection(test_settings)
    try:
        # Whole cohort lookup
        evidence = await select_policy(case_id, None, reader_conn)
        assert isinstance(evidence, PolicyEvidence)
        assert len(evidence.applicable_policies) == 1
        assert len(evidence.conflicting_policies) == 0

        pol = evidence.applicable_policies[0]
        assert pol["policy_id"] == "POL-2026-09-CURRENT"
        assert pol["calendar_days"] == 2
        assert pol["fee_bps"] == 250
        assert pol["order_count"] == 5

        # Check citations and facts registered
        assert len(evidence.citations) > 0
        facts_by_id = {f.fact_id: f for f in evidence.facts}
        assert "fact_POL-2026-09-CURRENT_fee_rate" in facts_by_id
        assert facts_by_id["fact_POL-2026-09-CURRENT_fee_rate"].value == "250 bps"
        assert "fact_POL-2026-09-CURRENT_settlement_days" in facts_by_id
        assert facts_by_id["fact_POL-2026-09-CURRENT_settlement_days"].value == 2

        # Explicit order O-005 (captured 2026-09-30, gross 32,000 INR = 3,200,000 paise)
        o5_evidence = await select_policy(case_id, ["O-005"], reader_conn)
        o5_facts = {f.fact_id: f for f in o5_evidence.facts}

        # Expected date: Sep 30 + 2 calendar days = Oct 2 (2026-10-02)
        assert "fact_O-005_expected_settlement_date" in o5_facts
        assert o5_facts["fact_O-005_expected_settlement_date"].value == "2026-10-02"

        # Expected fee: 3,200,000 paise * 250 bps / 10000 = 80,000 paise
        assert "fact_O-005_expected_fee" in o5_facts
        assert o5_facts["fact_O-005_expected_fee"].value_paise == 80000

    finally:
        await reader_conn.close()


@pytest.mark.asyncio
async def test_policy_boundary_dates(test_settings: Settings):
    """
    dev-a4 has an order captured on 2026-09-01.
    POL-OLD ended 2026-08-31 (3 days, 300 bps).
    POL-NEW started 2026-09-01 (2 days, 250 bps).
    Only POL-NEW must be selected.
    """
    dev_a4_zip = Path("evals/dev/bundles/dev-a4.zip")
    assert dev_a4_zip.exists()

    res = await import_bundle(dev_a4_zip, test_settings)
    case_id = res.case_id

    reader_conn = await get_reader_connection(test_settings)
    try:
        evidence = await select_policy(case_id, ["O-A4-1"], reader_conn)
        assert len(evidence.applicable_policies) == 1
        assert evidence.applicable_policies[0]["policy_id"] == "POL-NEW"
        assert evidence.applicable_policies[0]["fee_bps"] == 250
        assert len(evidence.conflicting_policies) == 0

        facts_by_id = {f.fact_id: f for f in evidence.facts}
        # Gross is 6,000,000 paise -> 250 bps is 150,000 paise
        assert facts_by_id["fact_O-A4-1_expected_fee"].value_paise == 150000
    finally:
        await reader_conn.close()


@pytest.mark.asyncio
async def test_policy_conflict_detection(test_settings: Settings):
    """
    dev-c3 has two active overlapping policies with conflicting terms:
    POL-P1 (T+2, 250 bps) vs POL-P2 (T+4, 300 bps).
    select_policy must report POLICY_CONFLICT and attach metadata citations for both.
    """
    dev_c3_zip = Path("evals/dev/bundles/dev-c3.zip")
    assert dev_c3_zip.exists()

    res = await import_bundle(dev_c3_zip, test_settings)
    case_id = res.case_id

    reader_conn = await get_reader_connection(test_settings)
    try:
        evidence = await select_policy(case_id, None, reader_conn)
        assert len(evidence.conflicting_policies) > 0

        conflict = evidence.conflicting_policies[0]
        assert conflict["code"] == "POLICY_CONFLICT"
        conflicting_pids = {p["policy_id"] for p in conflict["policies"]}
        assert conflicting_pids == {"POL-P1", "POL-P2"}
        assert set(conflict["differing_terms"]) == {"calendar_days", "fee_bps"}

        # Both policies must be cited in citations
        labels = [c.label for c in evidence.citations]
        assert any("POL-P1" in l for l in labels)
        assert any("POL-P2" in l for l in labels)
    finally:
        await reader_conn.close()


@pytest.mark.asyncio
async def test_unpublished_policy_excluded_by_cutoff(test_settings: Settings, tmp_path: Path):
    """
    A policy with published_at at or after as_of is excluded even if date interval matches.
    """
    demo_zip = Path("fixtures/demo.zip")
    unpacked = tmp_path / "case_cutoff_pol"
    with zipfile.ZipFile(demo_zip) as zf:
        zf.extractall(unpacked)

    # In demo manifest, as_of is 2026-10-01T00:00:00+05:30.
    # We will import demo and then test database cutoff query.
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    # Insert a future policy published on 2026-10-05 (after as_of)
    writer_conn = await get_writer_connection(test_settings)
    reader_conn = await get_reader_connection(test_settings)
    try:
        source_id = uuid.uuid4()
        async with writer_conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO sources (source_id, case_id, relative_name, sha256, media_type, kind, published_at, internal_path)
                VALUES (%s, %s, 'docs/future.pdf', '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef', 'application/pdf', 'policy', '2026-10-05T00:00:00+05:30', 'dummy')
                """,
                [source_id, case_id],
            )
            await cur.execute(
                """
                INSERT INTO policies (case_id, policy_id, source_id, published_at, effective_from, effective_to, calendar_days, fee_bps, metadata_locators)
                VALUES (%s, 'POL-FUTURE', %s, '2026-10-05T00:00:00+05:30', '2026-09-01', NULL, 1, 100, '["pdf:p1:l1"]'::jsonb)
                """,
                [case_id, source_id],
            )
        await writer_conn.commit()

        # Check that POL-FUTURE is NOT selected because published_at >= as_of
        evidence = await select_policy(case_id, None, reader_conn)
        pids = [p["policy_id"] for p in evidence.applicable_policies]
        assert "POL-FUTURE" not in pids
    finally:
        await writer_conn.close()
        await reader_conn.close()


@pytest.mark.asyncio
async def test_order_limit_and_missing_order_errors(test_settings: Settings):
    """
    An explicit order_ids list cannot exceed 20 orders, and unknown orders raise KeyError.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    reader_conn = await get_reader_connection(test_settings)
    try:
        # Exceeds 20 limit
        with pytest.raises(ValueError, match="exceeds maximum limit of 20"):
            await select_policy(case_id, [f"O-{i:03d}" for i in range(21)], reader_conn)

        # Missing order
        with pytest.raises(KeyError, match="Orders not found"):
            await select_policy(case_id, ["O-NONEXISTENT"], reader_conn)
    finally:
        await reader_conn.close()
