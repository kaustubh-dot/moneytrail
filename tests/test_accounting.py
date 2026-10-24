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
from moneytrail.schemas import ToolCall
from moneytrail.tools import dispatch_tool, inspect_order, reconcile, select_policy


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


@pytest.mark.asyncio
async def test_dispatch_tool(test_settings: Settings):
    """
    Verify typed evidence dispatcher across all 5 tool variants:
    reconcile, inspect_order, select_policy, search_evidence, and read_source.
    Enforces RESULT_LIMIT, unknown tool rejection, and scoped case injection.
    """
    demo_zip = Path("fixtures/demo.zip")
    res = await import_bundle(demo_zip, test_settings)
    case_id = res.case_id

    reader_conn = await get_reader_connection(test_settings)
    try:
        # 1. Reconcile tool dispatch
        call_rec = ToolCall(action="reconcile")
        res_rec = await dispatch_tool(case_id, call_rec, test_settings, reader_conn)
        assert res_rec.action == "reconcile"
        assert res_rec.data["gross_paise"] == 24000000
        assert res_rec.data["unexplained_difference_paise"] == 0
        assert len(res_rec.facts) >= 6
        assert len(res_rec.citations) >= 1

        # 2. Inspect order tool dispatch
        call_order = ToolCall(action="inspect_order", order_id="O-001")
        res_order = await dispatch_tool(case_id, call_order, test_settings, reader_conn)
        assert res_order.action == "inspect_order"
        assert res_order.data["order_id"] == "O-001"
        assert res_order.data["gross_paise"] == 8000000
        assert res_order.data["expected_net_paise"] == 6800000
        assert len(res_order.facts) >= 2
        assert len(res_order.citations) == 4

        # 3. Select policy tool dispatch (cohort mode)
        call_pol_cohort = ToolCall(action="select_policy", order_ids=None)
        res_pol_cohort = await dispatch_tool(case_id, call_pol_cohort, test_settings, reader_conn)
        assert res_pol_cohort.action == "select_policy"
        assert len(res_pol_cohort.data["applicable_policies"]) >= 1

        # Select policy tool dispatch (explicit orders)
        call_pol_explicit = ToolCall(action="select_policy", order_ids=["O-001", "O-002"])
        res_pol_explicit = await dispatch_tool(case_id, call_pol_explicit, test_settings, reader_conn)
        assert res_pol_explicit.action == "select_policy"
        assert any(f.fact_id == "fact_O-001_expected_fee" for f in res_pol_explicit.facts)

        # 4. Search evidence tool dispatch
        call_search = ToolCall(action="search_evidence", query="settlement terms", kind="all", limit=4)
        res_search = await dispatch_tool(case_id, call_search, test_settings, reader_conn)
        assert res_search.action == "search_evidence"
        assert len(res_search.data["items"]) >= 1
        assert len(res_search.citations) >= 1
        top_src_id = uuid.UUID(str(res_search.data["items"][0]["source_id"]))
        top_locator = res_search.data["items"][0]["locator_id"]

        # 5. Read source tool dispatch
        call_read = ToolCall(action="read_source", source_id=top_src_id, locator_id=top_locator)
        res_read = await dispatch_tool(case_id, call_read, test_settings, reader_conn)
        assert res_read.action == "read_source"
        assert res_read.data["source_id"] == top_src_id
        assert res_read.data["locator_id"] == top_locator
        assert len(res_read.data["text"]) > 0

        # 6. Reject unknown tool
        with pytest.raises(ValueError, match="UNKNOWN_TOOL"):
            # Construct raw model bypassing enum
            fake_call = ToolCall.model_construct(action="unsupported_analytics")
            await dispatch_tool(case_id, fake_call, test_settings, reader_conn)

        # 7. Reject RESULT_LIMIT overflow (>20 orders in select_policy)
        with pytest.raises(ValueError, match="RESULT_LIMIT"):
            overflow_orders = [f"O-{i}" for i in range(25)]
            overflow_call = ToolCall(action="select_policy", order_ids=overflow_orders)
            await dispatch_tool(case_id, overflow_call, test_settings, reader_conn)

        # Direct select_policy also rejects >20 orders with RESULT_LIMIT
        with pytest.raises(ValueError, match="RESULT_LIMIT"):
            await select_policy(case_id, [f"O-{i}" for i in range(21)], reader_conn)

        # 8. Reject RESULT_LIMIT overflow (>100 related records in inspect_order)
        writer_conn = await get_writer_connection(test_settings)
        try:
            async with writer_conn.cursor() as cur:
                await cur.execute(
                    "SELECT source_id FROM orders WHERE case_id = %s AND order_id = 'O-002';",
                    [case_id],
                )
                src_id = (await cur.fetchone())[0]
                for i in range(101):
                    await cur.execute(
                        """
                        INSERT INTO fees (case_id, fee_id, order_id, assessed_at, amount_paise, fee_kind, source_id, row_locators)
                        VALUES (%s, %s, 'O-002', '2026-09-10T12:00:00+05:30', 100, 'processing', %s, '["csv:r1"]'::jsonb);
                        """,
                        [case_id, f"F-OVERFLOW-{i}", src_id],
                    )
            await writer_conn.commit()
        finally:
            await writer_conn.close()

        try:
            with pytest.raises(ValueError, match="RESULT_LIMIT"):
                overflow_inspect_call = ToolCall(action="inspect_order", order_id="O-002")
                await dispatch_tool(case_id, overflow_inspect_call, test_settings, reader_conn)
        finally:
            w_conn = await get_writer_connection(test_settings)
            try:
                async with w_conn.cursor() as cur:
                    await cur.execute("DELETE FROM fees WHERE case_id = %s AND fee_id LIKE %s;", [case_id, "F-OVERFLOW-%"])
                await w_conn.commit()
            finally:
                await w_conn.close()

        # 9. Missing required argument
        with pytest.raises(ValueError, match="MISSING_ARGUMENT"):
            empty_order_call = ToolCall(action="inspect_order", order_id="")
            await dispatch_tool(case_id, empty_order_call, test_settings, reader_conn)

        # 10. Verify case ID injection: foreign case cannot access O-001
        foreign_case_id = uuid.uuid4()
        with pytest.raises(KeyError, match="not found in case"):
            await dispatch_tool(foreign_case_id, call_order, test_settings, reader_conn)

    finally:
        await reader_conn.close()


@pytest.mark.asyncio
async def test_inspect_order_cutoff_and_post_cutoff_exclusion(test_settings: Settings):
    """
    F-002: Verify inspect_order respects snapshot cutoff as_of.
    In test-b6, gross=5,000,000, fee=125,000 before cutoff.
    Refund=1,000,000 is processed in October (post-cutoff).
    Payout link=3,875,000 is recorded in October (post-cutoff).
    Snapshot expected net must be 4,875,000 paise, received must be 0,
    and post-cutoff records must be preserved as excluded with explicit reasons.
    """
    test_b6_zip = Path("tests/fixtures/test-b6.zip")
    assert test_b6_zip.exists()

    res = await import_bundle(test_b6_zip, test_settings)
    reader_conn = await get_reader_connection(test_settings)
    try:
        from moneytrail.tools import inspect_order
        evidence = await inspect_order(res.case_id, "O-TB6-1", reader_conn)

        assert evidence.gross_paise == 5000000
        assert evidence.expected_net_paise == 4875000

        # Facts
        facts_by_id = {f.fact_id: f for f in evidence.facts}
        assert facts_by_id["fact_O-TB6-1_gross"].value_paise == 5000000
        assert facts_by_id["fact_O-TB6-1_refunds"].value_paise == 0
        assert facts_by_id["fact_O-TB6-1_fees"].value_paise == 125000
        assert facts_by_id["fact_O-TB6-1_net"].value_paise == 4875000
        assert facts_by_id["fact_O-TB6-1_pending"].value_paise == 4875000
        assert "fact_O-TB6-1_received" not in facts_by_id

        # Post-cutoff records retained with explicit exclusion reasons
        assert len(evidence.refunds) == 1
        assert evidence.refunds[0]["included"] is False
        assert "at or after as_of cutoff" in evidence.refunds[0]["excluded_reason"]

        assert len(evidence.links) == 1
        assert evidence.links[0]["included"] is False
        assert "at or after as_of cutoff" in evidence.links[0]["excluded_reason"]
    finally:
        await reader_conn.close()


@pytest.mark.asyncio
async def test_incomplete_manifest_payouts_unknown_and_pending_unknown(test_settings: Settings, tmp_path: Path):
    """
    F-006: Verify partial-knowledge reconciliation does not crash (UnboundLocalError)
    when payouts=unknown, and does not claim a definitive documented pending subtotal
    when refund/fee evidence is incomplete.
    """
    demo_zip = Path("fixtures/demo.zip")
    unpacked = tmp_path / "case_payouts_unknown"
    with zipfile.ZipFile(demo_zip) as zf:
        zf.extractall(unpacked)

    manifest_data = json.loads((unpacked / "manifest.json").read_text(encoding="utf-8"))
    manifest_data["case_key"] = "test-payouts-unknown"
    manifest_data["completeness"]["payouts"] = "unknown"
    manifest_data["completeness"]["refunds"] = "unknown"
    (unpacked / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    case_zip = tmp_path / "case_payouts_unknown.zip"
    with zipfile.ZipFile(case_zip, "w") as zf:
        for f in unpacked.rglob("*"):
            if f.is_file():
                zf.write(f, arcname=f.relative_to(unpacked).as_posix())

    res = await import_bundle(case_zip, test_settings)
    reader_conn = await get_reader_connection(test_settings)
    try:
        # 1. Does not crash with UnboundLocalError
        rec = await reconcile(res.case_id, reader_conn)
        assert rec.reconciled is False
        assert rec.expected_net_paise == rec.gross_paise - rec.completed_refunds_paise - rec.assessed_fees_paise
        assert rec.pending_complete is False
        assert rec.received_complete is False

        # 2. Documented pending must not accumulate verified pending when refund evidence is unknown
        assert rec.documented_pending_paise == 0
        assert any(f.get("code") == "INCOMPLETE_PENDING_EVIDENCE" for f in rec.findings)

        # 3. inspect_order facts must reflect manifest completeness
        o_ev = await inspect_order(res.case_id, "O-001", reader_conn)
        facts_by_id = {f.fact_id: f for f in o_ev.facts}
        assert facts_by_id["fact_O-001_gross"].complete is True
        assert facts_by_id["fact_O-001_refunds"].complete is False
        assert facts_by_id["fact_O-001_net"].complete is False
        assert facts_by_id["fact_O-001_received"].complete is False
    finally:
        await reader_conn.close()

