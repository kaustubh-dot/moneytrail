"""Deterministic financial reconciliation and order record inspection tools for MoneyTrail."""

import uuid
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from moneytrail.schemas import Citation, Fact, OrderEvidence, Reconciliation


async def reconcile(case_id: uuid.UUID, db: psycopg.AsyncConnection) -> Reconciliation:
    """
    Perform deterministic financial cohort reconciliation using integer paise arithmetic outside LLM.
    Enforces cutoff timestamps, status exclusions, header validations, and independent pending classification.
    """
    async with db.cursor(row_factory=dict_row) as cur:
        # 1. Fetch case metadata
        await cur.execute(
            """
            SELECT case_id, case_key, merchant_id, currency,
                   cohort_start, cohort_end, as_of, completeness
            FROM cases
            WHERE case_id = %s;
            """,
            [case_id],
        )
        case_row = await cur.fetchone()
        if not case_row:
            raise KeyError(f"Case {case_id} not found")

        as_of: datetime = case_row["as_of"]
        completeness: dict[str, str] = case_row["completeness"]

        # 2. Fetch all orders
        await cur.execute(
            """
            SELECT order_id, captured_at, gross_paise, settlement_state, source_id, row_locators
            FROM orders
            WHERE case_id = %s
            ORDER BY order_id ASC;
            """,
            [case_id],
        )
        orders = await cur.fetchall()

        # 3. Fetch all refunds
        await cur.execute(
            """
            SELECT refund_id, order_id, processed_at, amount_paise, status, source_id, row_locators
            FROM refunds
            WHERE case_id = %s
            ORDER BY refund_id ASC;
            """,
            [case_id],
        )
        refunds = await cur.fetchall()

        # 4. Fetch all fees
        await cur.execute(
            """
            SELECT fee_id, order_id, assessed_at, amount_paise, fee_kind, source_id, row_locators
            FROM fees
            WHERE case_id = %s
            ORDER BY fee_id ASC;
            """,
            [case_id],
        )
        fees = await cur.fetchall()

        # 5. Fetch all payouts
        await cur.execute(
            """
            SELECT payout_id, received_at, amount_paise, status, source_id, row_locators
            FROM payouts
            WHERE case_id = %s
            ORDER BY payout_id ASC;
            """,
            [case_id],
        )
        payouts = await cur.fetchall()
        payouts_by_id = {p["payout_id"]: p for p in payouts}

        # 6. Fetch all payout links
        await cur.execute(
            """
            SELECT link_id, payout_id, order_id, amount_paise, recorded_at, source_id, row_locators
            FROM payout_links
            WHERE case_id = %s
            ORDER BY link_id ASC;
            """,
            [case_id],
        )
        payout_links = await cur.fetchall()

        # 7. Fetch findings
        await cur.execute(
            """
            SELECT code, severity, affected_record_ids, details
            FROM case_findings
            WHERE case_id = %s;
            """,
            [case_id],
        )
        findings = [dict(f) for f in await cur.fetchall()]

    # Maps for joining records per order
    refunds_by_order: dict[str, list[dict[str, Any]]] = {}
    for r in refunds:
        refunds_by_order.setdefault(r["order_id"], []).append(r)

    fees_by_order: dict[str, list[dict[str, Any]]] = {}
    for f in fees:
        fees_by_order.setdefault(f["order_id"], []).append(f)

    links_by_order: dict[str, list[dict[str, Any]]] = {}
    for lk in payout_links:
        links_by_order.setdefault(lk["order_id"], []).append(lk)

    links_by_payout: dict[str, list[dict[str, Any]]] = {}
    for lk in payout_links:
        links_by_payout.setdefault(lk["payout_id"], []).append(lk)

    # 1. Gross calculation
    gross_paise = sum(o["gross_paise"] for o in orders)
    gross_citations = [
        Citation(
            source_id=o["source_id"],
            locator_id=o["row_locators"][0] if o["row_locators"] else "csv:r1",
            label=f"Order {o['order_id']} Gross",
        )
        for o in orders
    ]
    gross_included_ids = [o["order_id"] for o in orders]

    # 2. Refunds deduction calculation
    completed_refunds_paise = 0
    refunds_citations: list[Citation] = []
    refunds_included_ids: list[str] = []
    refunds_excluded: list[dict[str, Any]] = []

    for r in refunds:
        # Completed refunds with processed_at strictly before cutoff as_of
        if r["status"] == "completed" and r["processed_at"] and r["processed_at"] < as_of:
            completed_refunds_paise += r["amount_paise"]
            refunds_included_ids.append(r["refund_id"])
            refunds_citations.append(
                Citation(
                    source_id=r["source_id"],
                    locator_id=r["row_locators"][0] if r["row_locators"] else "csv:r1",
                    label=f"Refund {r['refund_id']} Completed",
                )
            )
        else:
            reason = "pending status" if r["status"] != "completed" else "processed_at at or after as_of cutoff"
            refunds_excluded.append({
                "refund_id": r["refund_id"],
                "reason": reason,
                "amount_paise": r["amount_paise"],
            })

    # 3. Fees calculation
    assessed_fees_paise = 0
    fees_citations: list[Citation] = []
    fees_included_ids: list[str] = []
    fees_excluded: list[dict[str, Any]] = []

    for f in fees:
        if f["assessed_at"] < as_of:
            assessed_fees_paise += f["amount_paise"]
            fees_included_ids.append(f["fee_id"])
            fees_citations.append(
                Citation(
                    source_id=f["source_id"],
                    locator_id=f["row_locators"][0] if f["row_locators"] else "csv:r1",
                    label=f"Fee {f['fee_id']} Assessed",
                )
            )
        else:
            fees_excluded.append({
                "fee_id": f["fee_id"],
                "reason": "assessed_at at or after as_of cutoff",
                "amount_paise": f["amount_paise"],
            })

    # Expected net across cohort
    expected_net_paise = gross_paise - completed_refunds_paise - assessed_fees_paise

    # 4. Received allocations calculation
    received_allocations_paise = 0
    received_citations: list[Citation] = []
    received_included_link_ids: list[str] = []
    received_excluded_links: list[dict[str, Any]] = []

    for lk in payout_links:
        payout = payouts_by_id.get(lk["payout_id"])
        # Effective link: recorded before cutoff, payout received before cutoff
        if (
            lk["recorded_at"] < as_of
            and payout
            and payout["status"] == "received"
            and payout["received_at"]
            and payout["received_at"] < as_of
        ):
            received_allocations_paise += lk["amount_paise"]
            received_included_link_ids.append(lk["link_id"])
            received_citations.append(
                Citation(
                    source_id=lk["source_id"],
                    locator_id=lk["row_locators"][0] if lk["row_locators"] else "csv:r1",
                    label=f"Payout Link {lk['link_id']} Received",
                )
            )
        else:
            received_excluded_links.append({
                "link_id": lk["link_id"],
                "reason": "payout not received before cutoff or link recorded after cutoff",
                "amount_paise": lk["amount_paise"],
            })

    # 5. Documented pending calculation & per-order accounting checks
    documented_pending_paise = 0
    pending_citations: list[Citation] = []
    pending_included_order_ids: list[str] = []
    critical_accounting_issues: list[str] = []

    order_counts: dict[str, int] = {"paid": 0, "pending": 0, "unknown": 0}

    for o in orders:
        oid = o["order_id"]
        state = o["settlement_state"]
        order_counts[state] = order_counts.get(state, 0) + 1

        # Calculate per-order expected net
        o_refunds = sum(
            r["amount_paise"]
            for r in refunds_by_order.get(oid, [])
            if r["status"] == "completed" and r["processed_at"] and r["processed_at"] < as_of
        )
        o_fees = sum(
            f["amount_paise"]
            for f in fees_by_order.get(oid, [])
            if f["assessed_at"] < as_of
        )
        order_net = o["gross_paise"] - o_refunds - o_fees

        if order_net < 0:
            critical_accounting_issues.append(f"Negative expected net for order {oid} ({order_net} paise)")

        order_links = [
            lk
            for lk in links_by_order.get(oid, [])
            if lk["recorded_at"] < as_of
            and payouts_by_id.get(lk["payout_id"])
            and payouts_by_id[lk["payout_id"]]["status"] == "received"
            and payouts_by_id[lk["payout_id"]]["received_at"]
            and payouts_by_id[lk["payout_id"]]["received_at"] < as_of
        ]

        if state == "paid":
            if not order_links:
                critical_accounting_issues.append(f"Paid order {oid} has no effective received link")
            else:
                total_order_link_amount = sum(lk["amount_paise"] for lk in order_links)
                if total_order_link_amount != order_net:
                    critical_accounting_issues.append(
                        f"Paid order {oid} received link sum ({total_order_link_amount}) does not equal expected net ({order_net})"
                    )

        elif state == "pending":
            if order_links:
                critical_accounting_issues.append(
                    f"Pending order {oid} has conflicting received link ({order_links[0]['link_id']})"
                )
            else:
                # Documented pending: explicit pending orders without received allocation
                documented_pending_paise += order_net
                pending_included_order_ids.append(oid)
                pending_citations.append(
                    Citation(
                        source_id=o["source_id"],
                        locator_id=o["row_locators"][0] if o["row_locators"] else "csv:r1",
                        label=f"Order {oid} Documented Pending",
                    )
                )

        elif state == "unknown":
            critical_accounting_issues.append(f"Order {oid} has unknown settlement state")

    # Check payout headers match sum of their effective links
    for pid, p in payouts_by_id.items():
        if p["status"] == "received" and p["received_at"] and p["received_at"] < as_of:
            p_effective_links = [
                lk
                for lk in links_by_payout.get(pid, [])
                if lk["recorded_at"] < as_of
            ]
            link_sum = sum(lk["amount_paise"] for lk in p_effective_links)
            if p["amount_paise"] != link_sum:
                critical_accounting_issues.append(
                    f"Payout header {pid} amount ({p['amount_paise']}) does not match link sum ({link_sum})"
                )

    # 6. Unexplained difference calculation
    unexplained_difference_paise = (
        gross_paise
        - completed_refunds_paise
        - assessed_fees_paise
        - received_allocations_paise
        - documented_pending_paise
    )

    # 7. Reconciled determination
    all_families_complete = all(v == "complete" for v in completeness.values())
    no_critical_issues = len(critical_accounting_issues) == 0
    zero_diff = (unexplained_difference_paise == 0)

    reconciled = all_families_complete and no_critical_issues and zero_diff

    # 8. Register immutable Fact records
    facts = [
        Fact(
            fact_id="fact_gross",
            kind="money",
            value=gross_paise,
            value_paise=gross_paise,
            currency="INR",
            complete=completeness.get("orders") == "complete",
            citations=gross_citations,
            derivation={"included_order_ids": gross_included_ids, "order_count": len(orders)},
        ),
        Fact(
            fact_id="fact_refunds",
            kind="money",
            value=completed_refunds_paise,
            value_paise=completed_refunds_paise,
            currency="INR",
            complete=completeness.get("refunds") == "complete",
            citations=refunds_citations,
            derivation={"included_refund_ids": refunds_included_ids, "excluded": refunds_excluded},
        ),
        Fact(
            fact_id="fact_fees",
            kind="money",
            value=assessed_fees_paise,
            value_paise=assessed_fees_paise,
            currency="INR",
            complete=completeness.get("fees") == "complete",
            citations=fees_citations,
            derivation={"included_fee_ids": fees_included_ids, "excluded": fees_excluded},
        ),
        Fact(
            fact_id="fact_net",
            kind="money",
            value=expected_net_paise,
            value_paise=expected_net_paise,
            currency="INR",
            complete=all_families_complete,
            citations=gross_citations + refunds_citations + fees_citations,
            derivation={"formula": "gross - refunds - fees"},
        ),
        Fact(
            fact_id="fact_received",
            kind="money",
            value=received_allocations_paise,
            value_paise=received_allocations_paise,
            currency="INR",
            complete=completeness.get("payouts") == "complete" and completeness.get("payout_links") == "complete",
            citations=received_citations,
            derivation={"included_link_ids": received_included_link_ids, "excluded": received_excluded_links},
        ),
        Fact(
            fact_id="fact_pending",
            kind="money",
            value=documented_pending_paise,
            value_paise=documented_pending_paise,
            currency="INR",
            complete=all_families_complete,
            citations=pending_citations,
            derivation={"included_order_ids": pending_included_order_ids},
        ),
        Fact(
            fact_id="fact_difference",
            kind="money",
            value=unexplained_difference_paise,
            value_paise=unexplained_difference_paise,
            currency="INR",
            complete=all_families_complete,
            citations=[],
            derivation={
                "formula": "gross - refunds - fees - received - pending",
                "critical_issues": critical_accounting_issues,
            },
        ),
        Fact(
            fact_id="fact_reconciled",
            kind="string",
            value="true" if reconciled else "false",
            complete=True,
            citations=[],
            derivation={"reconciled": reconciled, "all_families_complete": all_families_complete},
        ),
    ]

    return Reconciliation(
        gross_paise=gross_paise,
        completed_refunds_paise=completed_refunds_paise,
        assessed_fees_paise=assessed_fees_paise,
        received_allocations_paise=received_allocations_paise,
        documented_pending_paise=documented_pending_paise,
        unexplained_difference_paise=unexplained_difference_paise,
        reconciled=reconciled,
        facts=facts,
        findings=findings,
        details_omitted=False,
        order_counts=order_counts,
    )


async def inspect_order(
    case_id: uuid.UUID, order_id: str, db: psycopg.AsyncConnection
) -> OrderEvidence:
    """
    Inspect exact database records and locators joined for an explicit order.
    Returns OrderEvidence with integer paise and resolving citations.
    """
    async with db.cursor(row_factory=dict_row) as cur:
        # Fetch order
        await cur.execute(
            """
            SELECT order_id, captured_at, gross_paise, settlement_state, source_id, row_locators
            FROM orders
            WHERE case_id = %s AND order_id = %s;
            """,
            [case_id, order_id],
        )
        order_row = await cur.fetchone()
        if not order_row:
            raise KeyError(f"Order '{order_id}' not found in case '{case_id}'")

        # Fetch refunds for this order
        await cur.execute(
            """
            SELECT refund_id, processed_at, amount_paise, status, source_id, row_locators
            FROM refunds
            WHERE case_id = %s AND order_id = %s
            ORDER BY refund_id ASC;
            """,
            [case_id, order_id],
        )
        order_refunds = await cur.fetchall()

        # Fetch fees for this order
        await cur.execute(
            """
            SELECT fee_id, assessed_at, amount_paise, fee_kind, source_id, row_locators
            FROM fees
            WHERE case_id = %s AND order_id = %s
            ORDER BY fee_id ASC;
            """,
            [case_id, order_id],
        )
        order_fees = await cur.fetchall()

        # Fetch payout links for this order
        await cur.execute(
            """
            SELECT l.link_id, l.payout_id, l.amount_paise, l.recorded_at,
                   p.received_at, p.status AS payout_status, l.source_id, l.row_locators
            FROM payout_links l
            JOIN payouts p ON l.case_id = p.case_id AND l.payout_id = p.payout_id
            WHERE l.case_id = %s AND l.order_id = %s
            ORDER BY l.link_id ASC;
            """,
            [case_id, order_id],
        )
        order_links = await cur.fetchall()

    gross_paise = order_row["gross_paise"]
    completed_refunds = sum(
        r["amount_paise"] for r in order_refunds if r["status"] == "completed"
    )
    assessed_fees = sum(f["amount_paise"] for f in order_fees)
    expected_net_paise = gross_paise - completed_refunds - assessed_fees

    citations: list[Citation] = []
    # Order citation
    if order_row["row_locators"]:
        citations.append(
            Citation(
                source_id=order_row["source_id"],
                locator_id=order_row["row_locators"][0],
                label=f"Order {order_id} Row",
            )
        )
    for r in order_refunds:
        if r["row_locators"]:
            citations.append(
                Citation(
                    source_id=r["source_id"],
                    locator_id=r["row_locators"][0],
                    label=f"Refund {r['refund_id']}",
                )
            )
    for f in order_fees:
        if f["row_locators"]:
            citations.append(
                Citation(
                    source_id=f["source_id"],
                    locator_id=f["row_locators"][0],
                    label=f"Fee {f['fee_id']}",
                )
            )
    for lk in order_links:
        if lk["row_locators"]:
            citations.append(
                Citation(
                    source_id=lk["source_id"],
                    locator_id=lk["row_locators"][0],
                    label=f"Payout Link {lk['link_id']}",
                )
            )

    facts = [
        Fact(
            fact_id=f"fact_{order_id}_gross",
            kind="money",
            value=gross_paise,
            value_paise=gross_paise,
            currency="INR",
            complete=True,
            citations=citations[:1],
            derivation={"order_id": order_id},
        ),
        Fact(
            fact_id=f"fact_{order_id}_net",
            kind="money",
            value=expected_net_paise,
            value_paise=expected_net_paise,
            currency="INR",
            complete=True,
            citations=citations,
            derivation={
                "order_id": order_id,
                "gross": gross_paise,
                "refunds": completed_refunds,
                "fees": assessed_fees,
            },
        ),
    ]

    return OrderEvidence(
        order_id=order_id,
        captured_at=order_row["captured_at"],
        gross_paise=gross_paise,
        settlement_state=order_row["settlement_state"],
        refunds=[dict(r) for r in order_refunds],
        fees=[dict(f) for f in order_fees],
        links=[dict(lk) for lk in order_links],
        expected_net_paise=expected_net_paise,
        citations=citations,
        facts=facts,
    )
