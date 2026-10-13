"""Accounting regression tests for integer reconciliation, cutoff behavior, and pending classifications."""

import hashlib
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
from moneytrail.tools import inspect_order, reconcile


@pytest.mark.asyncio
async def test_demo_totals(test_settings: Settings):
    """
    Verify exact paise tuple:
    (gross, refunds, fees, pending, received, difference) equals (24000000, 1800000, 600000, 3000000, 18600000, 0).
    Payout headers are not added again.
    """
    demo_zip = Path("fixtures/demo.zip")
    assert demo_zip.exists()

    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    reader_conn = await get_reader_connection(test_settings)
    try:
        rec = await reconcile(case_id, reader_conn)

        # Exact paise values in tuple
        totals_tuple = (
            rec.gross_paise,
            rec.completed_refunds_paise,
            rec.assessed_fees_paise,
            rec.documented_pending_paise,
            rec.received_allocations_paise,
            rec.unexplained_difference_paise,
        )
        assert totals_tuple == (24000000, 1800000, 600000, 3000000, 18600000, 0), (
            f"Expected (24000000, 1800000, 600000, 3000000, 18600000, 0) but got {totals_tuple}"
        )

        assert rec.reconciled is True
        assert rec.order_counts == {"paid": 4, "pending": 1, "unknown": 0}

        # Verify Fact objects registered
        fact_ids = {f.fact_id: f for f in rec.facts}
        assert "fact_gross" in fact_ids
        assert fact_ids["fact_gross"].value_paise == 24000000
        assert fact_ids["fact_refunds"].value_paise == 1800000
        assert fact_ids["fact_fees"].value_paise == 600000
        assert fact_ids["fact_pending"].value_paise == 3000000
        assert fact_ids["fact_received"].value_paise == 18600000
        assert fact_ids["fact_difference"].value_paise == 0

        # Inspect specific order O-001
        o1 = await inspect_order(case_id, "O-001", reader_conn)
        assert o1.order_id == "O-001"
        assert o1.gross_paise == 8000000
        assert o1.expected_net_paise == 6800000
        assert len(o1.refunds) == 1
        assert o1.refunds[0]["amount_paise"] == 1000000
        assert len(o1.fees) == 1
        assert o1.fees[0]["amount_paise"] == 200000
        assert len(o1.links) == 1
        assert o1.links[0]["amount_paise"] == 6800000

        # Inspect pending order O-005
        o5 = await inspect_order(case_id, "O-005", reader_conn)
        assert o5.order_id == "O-005"
        assert o5.settlement_state == "pending"
        assert o5.gross_paise == 3200000
        assert o5.expected_net_paise == 3000000
        assert len(o5.links) == 0

    finally:
        await reader_conn.close()


@pytest.mark.asyncio
async def test_cutoff_and_refund_status(test_settings: Settings, tmp_path: Path):
    """
    A completed refund at exactly as_of and a pending refund are excluded;
    one immediately before cutoff is included; timestamps use India/UTC consistently.
    """
    demo_zip = Path("fixtures/demo.zip")
    unpacked = tmp_path / "case_cutoff"
    with zipfile.ZipFile(demo_zip) as zf:
        zf.extractall(unpacked)

    # Cutoff in demo manifest is 2026-10-01T00:00:00+05:30
    # Let's adjust refunds.csv:
    # R-001: processed 2026-09-30T23:59:59+05:30, completed -> included
    # R-002: processed 2026-10-01T00:00:00+05:30, completed -> EXCLUDED (at exactly cutoff)
    # R-003: processed empty, pending -> EXCLUDED (pending status)
    new_refunds = """refund_id,order_id,processed_at,amount_paise,status
R-001,O-001,2026-09-30T23:59:59+05:30,500000,completed
R-002,O-002,2026-10-01T00:00:00+05:30,680000,completed
R-003,O-005,,120000,pending
"""
    (unpacked / "refunds.csv").write_text(new_refunds, encoding="utf-8")

    m_path = unpacked / "manifest.json"
    m_dict = json.loads(m_path.read_text())
    m_dict["case_key"] = "case-cutoff-test"
    ref_sha = hashlib.sha256((unpacked / "refunds.csv").read_bytes()).hexdigest().lower()
    for f in m_dict["files"]:
        if f["path"] == "refunds.csv":
            f["sha256"] = ref_sha
    m_path.write_text(json.dumps(m_dict))

    case_zip = tmp_path / "cutoff_case.zip"
    with zipfile.ZipFile(case_zip, "w") as zf:
        for f in unpacked.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(unpacked).as_posix())

    res = await import_bundle(case_zip, test_settings)
    reader_conn = await get_reader_connection(test_settings)
    try:
        rec = await reconcile(res.case_id, reader_conn)
        # Only R-001 (500000 paise) should be included!
        # R-002 (at exact as_of cutoff) and R-003 (pending) must be excluded!
        assert rec.completed_refunds_paise == 500000

        # Check derivation records reasons
        ref_fact = next(f for f in rec.facts if f.fact_id == "fact_refunds")
        assert ref_fact.derivation["included_refund_ids"] == ["R-001"]
        excluded = {x["refund_id"]: x["reason"] for x in ref_fact.derivation["excluded"]}
        assert "R-002" in excluded
        assert "at or after as_of cutoff" in excluded["R-002"]
        assert "R-003" in excluded
        assert "pending status" in excluded["R-003"]
    finally:
        await reader_conn.close()


@pytest.mark.asyncio
async def test_missing_link_is_not_pending(test_settings: Settings, tmp_path: Path):
    """
    A paid order lacking its link yields a gap and an unexplained difference,
    never a synthetic pending allocation; missing source completeness prevents a verified total.
    """
    demo_zip = Path("fixtures/demo.zip")
    unpacked = tmp_path / "case_missing_link"
    with zipfile.ZipFile(demo_zip) as zf:
        zf.extractall(unpacked)

    # In payout_links.csv, delete link L-001 for paid order O-001
    links_lines = [
        "link_id,payout_id,order_id,amount_paise,recorded_at",
        "L-002,P-001,O-002,5170000,2026-09-28T16:00:00+05:30",
        "L-003,P-002,O-003,3900000,2026-09-29T17:00:00+05:30",
        "L-004,P-002,O-004,2730000,2026-09-29T17:00:00+05:30",
    ]
    (unpacked / "payout_links.csv").write_text("\n".join(links_lines) + "\n", encoding="utf-8")

    # In payouts.csv, adjust P-001 amount to 5170000 so header matches remaining links
    payouts_lines = [
        "payout_id,received_at,amount_paise,status",
        "P-001,2026-09-28T16:00:00+05:30,5170000,received",
        "P-002,2026-09-29T17:00:00+05:30,6630000,received",
    ]
    (unpacked / "payouts.csv").write_text("\n".join(payouts_lines) + "\n", encoding="utf-8")

    m_path = unpacked / "manifest.json"
    m_dict = json.loads(m_path.read_text())
    m_dict["case_key"] = "case-missing-link-test"
    m_dict["completeness"]["orders"] = "unknown"  # Incomplete family test

    links_sha = hashlib.sha256((unpacked / "payout_links.csv").read_bytes()).hexdigest().lower()
    payouts_sha = hashlib.sha256((unpacked / "payouts.csv").read_bytes()).hexdigest().lower()
    for f in m_dict["files"]:
        if f["path"] == "payout_links.csv":
            f["sha256"] = links_sha
        elif f["path"] == "payouts.csv":
            f["sha256"] = payouts_sha
    m_path.write_text(json.dumps(m_dict))

    case_zip = tmp_path / "missing_link.zip"
    with zipfile.ZipFile(case_zip, "w") as zf:
        for f in unpacked.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(unpacked).as_posix())

    res = await import_bundle(case_zip, test_settings)
    reader_conn = await get_reader_connection(test_settings)
    try:
        rec = await reconcile(res.case_id, reader_conn)

        # Net for O-001 is 6,800,000 paise (80k gross - 10k refund - 2k fee)
        # Because O-001 is marked 'paid' and has NO link:
        # Documented pending MUST NOT include O-001 (pending stays 3,000,000 from O-005)!
        assert rec.documented_pending_paise == 3000000

        # Received allocations are 5,170,000 + 6,630,000 = 11,800,000 paise
        assert rec.received_allocations_paise == 11800000

        # Unexplained difference:
        # Net total = 21,600,000
        # Received = 11,800,000
        # Documented pending = 3,000,000
        # Difference = 21,600,000 - 11,800,000 - 3,000,000 = 6,800,000 paise (exact net of O-001)!
        assert rec.unexplained_difference_paise == 6800000

        # Reconciled MUST be False
        assert rec.reconciled is False

        # Incomplete family sets fact.complete to False
        gross_fact = next(f for f in rec.facts if f.fact_id == "fact_gross")
        assert gross_fact.complete is False

    finally:
        await reader_conn.close()
