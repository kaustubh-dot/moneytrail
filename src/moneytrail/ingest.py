"""Bundle validation, logical fingerprinting, and atomic case ingestion."""

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pypdf

from moneytrail.config import (
    CSV_MAX_TOTAL_RECORDS,
    DOC_MAX_BYTES,
    PDF_MAX_PAGES,
    ZIP_MAX_COMPRESSED_BYTES,
    ZIP_MAX_FILES,
    ZIP_MAX_INFLATED_BYTES,
    Settings,
)
from moneytrail.db import get_writer_connection
from moneytrail.schemas import (
    CaseFinding,
    CompletenessDeclaration,
    ImportResult,
    Manifest,
    ManifestFileEntry,
)


def compute_sha256(data: bytes | Path) -> str:
    """Compute 64-character lowercase hexadecimal SHA256."""
    h = hashlib.sha256()
    if isinstance(data, Path):
        h.update(data.read_bytes())
    else:
        h.update(data)
    return h.hexdigest().lower()


def compute_bundle_fingerprint(manifest: Manifest) -> str:
    """
    Compute logical fingerprint independent of ZIP container metadata or manifest whitespace:
    SHA256(canonical manifest logical metadata + sorted [normalized relative path, actual file SHA256]).
    """
    canonical_data = {
        "schema_version": manifest.schema_version,
        "case_key": manifest.case_key,
        "merchant_id": manifest.merchant_id,
        "currency": manifest.currency,
        "timezone": manifest.timezone,
        "cohort_start": manifest.cohort_start.isoformat(),
        "cohort_end": manifest.cohort_end.isoformat(),
        "as_of": manifest.as_of.isoformat(),
        "completeness": {
            "orders": manifest.completeness.orders,
            "refunds": manifest.completeness.refunds,
            "fees": manifest.completeness.fees,
            "payouts": manifest.completeness.payouts,
            "payout_links": manifest.completeness.payout_links,
            "policies": manifest.completeness.policies,
        },
        "files": sorted(
            [
                {
                    "kind": f.kind,
                    "path": f.path.strip("/"),
                    "sha256": f.sha256.lower(),
                }
                for f in manifest.files
            ],
            key=lambda x: x["path"],
        ),
    }
    serialized = json.dumps(canonical_data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest().lower()


def _validate_safe_zip_path(arc_name: str) -> str:
    """Ensure arcname has no traversal attempts or absolute paths."""
    cleaned = arc_name.replace("\\", "/")
    if cleaned.startswith("/") or cleaned.startswith("../") or "/../" in cleaned:
        raise ValueError(f"Unsafe ZIP member path rejected: {arc_name}")
    parts = cleaned.split("/")
    if any(p == ".." or p.startswith(":") or (len(p) > 1 and p[1] == ":") for p in parts):
        raise ValueError(f"Unsafe ZIP member path rejected: {arc_name}")
    return cleaned


def _parse_iso_datetime(value: str, field_name: str, row_loc: str) -> datetime:
    """Parse strict ISO-8601 datetime with timezone offset."""
    if not value or not value.strip():
        raise ValueError(f"Empty datetime for {field_name} at {row_loc}")
    try:
        dt = datetime.fromisoformat(value.strip())
        if dt.tzinfo is None:
            raise ValueError(f"Missing timezone offset for {field_name} at {row_loc}: {value}")
        return dt
    except Exception as e:
        raise ValueError(f"Invalid ISO datetime for {field_name} at {row_loc}: {value} ({e})")


def _parse_paise(value: str, field_name: str, row_loc: str) -> int:
    """Parse strict base-10 non-negative integer paise."""
    s = value.strip()
    if not s or not re.match(r"^\d+$", s):
        raise ValueError(f"Invalid non-negative integer paise for {field_name} at {row_loc}: '{value}'")
    val = int(s)
    if val < 0 or val > 10**12:
        raise ValueError(f"Paise value out of valid range (0..10^12) for {field_name} at {row_loc}: {val}")
    return val


def _parse_policy_text(pdf_bytes: bytes, source_id: uuid.UUID) -> tuple[dict[str, Any], list[str]]:
    """
    Parse labelled policy metadata lines from PDF bytes.
    Returns (policy_data_dict, metadata_locators).
    """
    import io
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        raise ValueError(f"Failed to read policy PDF: {e}")

    if reader.is_encrypted:
        raise ValueError("UNSUPPORTED_PDF: Encrypted PDF rejected; supply unencrypted selectable text.")

    if len(reader.pages) > PDF_MAX_PAGES:
        raise ValueError(f"PDF page count ({len(reader.pages)}) exceeds limit ({PDF_MAX_PAGES})")

    all_lines: list[tuple[int, int, str]] = []  # (page_1based, line_1based, text)
    for p_idx, page in enumerate(reader.pages):
        page_text = page.extract_text() or ""
        lines = [line.strip() for line in page_text.splitlines() if line.strip()]
        for l_idx, line in enumerate(lines):
            all_lines.append((p_idx + 1, l_idx + 1, line))

    if not all_lines:
        raise ValueError("UNSUPPORTED_PDF: Scanned PDF without selectable text rejected; supply selectable text.")

    metadata: dict[str, str] = {}
    metadata_locators: list[str] = []
    field_locators: dict[str, str] = {}

    required_keys = {
        "Policy-ID": "policy_id",
        "Published-At": "published_at",
        "Effective-From": "effective_from",
        "Effective-To": "effective_to",
        "Settlement-Calendar-Days": "calendar_days",
        "Processing-Fee-Bps": "fee_bps",
    }

    for p_num, l_num, line in all_lines:
        for prefix, key in required_keys.items():
            if line.startswith(f"{prefix}:"):
                val = line[len(prefix) + 1 :].strip()
                if key in metadata and metadata[key] != val:
                    raise ValueError(f"Contradictory policy metadata for {prefix}: {metadata[key]} vs {val}")
                metadata[key] = val
                loc = f"pdf:p{p_num}:l{l_num}"
                metadata_locators.append(loc)
                field_locators[key] = loc

    missing = [k for k, prop in required_keys.items() if prop not in metadata]
    if missing:
        raise ValueError(f"Policy PDF missing required metadata lines: {missing}")

    # Parse fields
    policy_id = metadata["policy_id"]
    pub_dt = _parse_iso_datetime(metadata["published_at"], "Published-At", field_locators["published_at"])
    eff_from = date.fromisoformat(metadata["effective_from"])
    eff_to_str = metadata["effective_to"].strip()
    eff_to = None if eff_to_str.lower() == "open" else date.fromisoformat(eff_to_str)
    if eff_to and eff_to <= eff_from:
        raise ValueError(f"Effective-To ({eff_to}) must be after Effective-From ({eff_from})")

    cal_days = int(metadata["calendar_days"])
    if cal_days < 0:
        raise ValueError(f"Settlement-Calendar-Days must be non-negative, got {cal_days}")
    fee_bps = int(metadata["fee_bps"])
    if fee_bps < 0:
        raise ValueError(f"Processing-Fee-Bps must be non-negative, got {fee_bps}")

    return {
        "policy_id": policy_id,
        "source_id": source_id,
        "published_at": pub_dt,
        "effective_from": eff_from,
        "effective_to": eff_to,
        "calendar_days": cal_days,
        "fee_bps": fee_bps,
        "metadata_locators": metadata_locators,
    }, metadata_locators


def validate_bundle(bundle_path: Path) -> dict[str, Any]:
    """
    Validate ZIP bundle structure, manifest, checksums, CSV constraints, and data integrity.
    Returns parsed dictionary of case data ready for staging/insertion.
    """
    if not bundle_path.exists():
        raise FileNotFoundError(f"Bundle not found: {bundle_path}")

    compressed_size = bundle_path.stat().st_size
    if compressed_size > ZIP_MAX_COMPRESSED_BYTES:
        raise ValueError(
            f"Bundle size ({compressed_size} bytes) exceeds limit ({ZIP_MAX_COMPRESSED_BYTES} bytes)"
        )

    if not zipfile.is_zipfile(bundle_path):
        raise ValueError(f"File is not a valid ZIP archive: {bundle_path}")

    with zipfile.ZipFile(bundle_path, "r") as zf:
        infolist = zf.infolist()
        if len(infolist) > ZIP_MAX_FILES:
            raise ValueError(f"ZIP file count ({len(infolist)}) exceeds limit ({ZIP_MAX_FILES})")

        total_inflated = sum(info.file_size for info in infolist)
        if total_inflated > ZIP_MAX_INFLATED_BYTES:
            raise ValueError(
                f"Inflated ZIP size ({total_inflated} bytes) exceeds limit ({ZIP_MAX_INFLATED_BYTES} bytes)"
            )

        names = set()
        for info in infolist:
            # Reject symlinks: Unix file mode 0o120000
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError(f"Symlink archive member rejected: {info.filename}")
            safe_name = _validate_safe_zip_path(info.filename)
            if safe_name in names:
                raise ValueError(f"Duplicate archive member path rejected: {safe_name}")
            names.add(safe_name)

        if "manifest.json" not in names:
            raise ValueError("Bundle missing required 'manifest.json' at root")

        # Read and parse manifest
        manifest_raw = zf.read("manifest.json").decode("utf-8")
        try:
            manifest_dict = json.loads(manifest_raw)
            manifest = Manifest.model_validate(manifest_dict)
        except Exception as e:
            raise ValueError(f"Invalid manifest.json schema: {e}")

        # Check cohort and as_of
        if manifest.cohort_start.tzinfo is None or manifest.cohort_end.tzinfo is None or manifest.as_of.tzinfo is None:
            raise ValueError("cohort_start, cohort_end, and as_of must include timezone offsets")
        if manifest.as_of < manifest.cohort_end:
            raise ValueError(f"Cutoff as_of ({manifest.as_of}) must be >= cohort_end ({manifest.cohort_end})")
        if manifest.cohort_end <= manifest.cohort_start:
            raise ValueError(f"cohort_end ({manifest.cohort_end}) must be after cohort_start ({manifest.cohort_start})")

        # Verify all files in manifest
        manifest_paths = {entry.path.strip("/") for entry in manifest.files}
        # Check ZIP contains only manifest.json and manifest files (plus optional dir entries)
        non_dir_names = {n.strip("/") for n in names if not n.endswith("/") and n != "manifest.json"}
        extra_files = non_dir_names - manifest_paths
        if extra_files:
            raise ValueError(f"ZIP contains unlisted files not in manifest: {extra_files}")

        missing_files = manifest_paths - non_dir_names
        if missing_files:
            raise ValueError(f"Manifest declares files missing from ZIP: {missing_files}")

        # Require at least one policy PDF and at least one correspondence file
        has_policy = any(
            (f.kind == "policy" or (f.path.endswith(".pdf") and ("policy" in f.path.lower() or "pol" in f.path.lower())))
            and f.path.endswith(".pdf")
            for f in manifest.files
        )
        if not has_policy:
            raise ValueError("Bundle must contain at least one policy PDF under docs/")
        has_correspondence = any(
            f.kind == "correspondence" or f.path.endswith((".txt", ".md"))
            for f in manifest.files
        )
        if not has_correspondence:
            raise ValueError("Bundle must contain at least one correspondence file")

        # Verify checksums and size limits of every file
        file_bytes_map: dict[str, bytes] = {
            "manifest.json": manifest_raw.encode("utf-8")
        }
        for entry in manifest.files:
            p = entry.path.strip("/")
            data = zf.read(p)
            if len(data) > DOC_MAX_BYTES:
                raise ValueError(
                    f"File '{entry.path}' size ({len(data)} bytes) exceeds limit ({DOC_MAX_BYTES} bytes)"
                )
            actual_sha = hashlib.sha256(data).hexdigest().lower()
            expected_sha = entry.sha256.lower()
            if actual_sha != expected_sha:
                raise ValueError(
                    f"Checksum mismatch for '{entry.path}': expected {expected_sha}, computed {actual_sha}"
                )
            file_bytes_map[p] = data

        # Deep validate policy PDFs and correspondence encoding
        for entry in manifest.files:
            p = entry.path.strip("/")
            is_policy_pdf = (entry.kind == "policy" or (p.endswith(".pdf") and ("policy" in p.lower() or "pol" in p.lower()))) and p.endswith(".pdf")
            if is_policy_pdf:
                _parse_policy_text(file_bytes_map[p], uuid.uuid4())
            elif entry.kind == "correspondence" and not p.endswith(".pdf"):
                try:
                    file_bytes_map[p].decode("utf-8")
                except UnicodeDecodeError as ue:
                    raise ValueError(f"Correspondence file '{entry.path}' is not valid UTF-8: {ue}")

        fingerprint = compute_bundle_fingerprint(manifest)

        # Parse CSVs
        # 1. orders.csv
        if "orders.csv" not in file_bytes_map:
            raise ValueError("Required file 'orders.csv' is missing")
        orders_raw = file_bytes_map["orders.csv"].decode("utf-8-sig")
        orders_reader = csv.DictReader(orders_raw.splitlines())
        expected_orders_cols = ["order_id", "captured_at", "gross_paise", "settlement_state"]
        if orders_reader.fieldnames != expected_orders_cols:
            raise ValueError(
                f"orders.csv columns mismatch: expected {expected_orders_cols}, got {orders_reader.fieldnames}"
            )

        orders_by_id: dict[str, dict[str, Any]] = {}
        orders_first_row: dict[str, int] = {}
        duplicate_findings: list[CaseFinding] = []

        for row_idx, row in enumerate(orders_reader, start=1):
            row_loc = f"csv:r{row_idx}"
            oid = row["order_id"].strip()
            if not oid or len(oid) > 64:
                raise ValueError(f"Invalid order_id '{oid}' at {row_loc}")
            captured_at = _parse_iso_datetime(row["captured_at"], "captured_at", row_loc)
            if not (manifest.cohort_start <= captured_at < manifest.cohort_end):
                raise ValueError(
                    f"Order {oid} captured_at ({captured_at}) outside cohort interval "
                    f"[{manifest.cohort_start}, {manifest.cohort_end})"
                )
            gross = _parse_paise(row["gross_paise"], "gross_paise", row_loc)
            state = row["settlement_state"].strip()
            if state not in ("paid", "pending", "unknown"):
                raise ValueError(f"Invalid settlement_state '{state}' for order {oid} at {row_loc}")

            if oid in orders_by_id:
                prev = orders_by_id[oid]
                prev_row = orders_first_row[oid]
                if (
                    prev["captured_at"] == captured_at
                    and prev["gross_paise"] == gross
                    and prev["settlement_state"] == state
                ):
                    prev["row_locators"].append(row_loc)
                    duplicate_findings.append(
                        CaseFinding(
                            code="DUPLICATE_ROW",
                            severity="info",
                            affected_record_ids=[oid],
                            details={"table": "orders", "locators": [f"csv:r{prev_row}", row_loc]},
                        )
                    )
                    duplicate_findings.append(
                        CaseFinding(
                            code="DUPLICATE_IDENTICAL_ROW",
                            severity="info",
                            affected_record_ids=[oid],
                            details={"table": "orders", "locators": [f"csv:r{prev_row}", row_loc]},
                        )
                    )
                else:
                    raise ValueError(
                        f"Conflicting duplicate record ID '{oid}' in orders.csv: row {prev_row} vs row {row_idx}"
                    )
            else:
                orders_by_id[oid] = {
                    "order_id": oid,
                    "captured_at": captured_at,
                    "gross_paise": gross,
                    "settlement_state": state,
                    "row_locators": [row_loc],
                }
                orders_first_row[oid] = row_idx

        # 2. refunds.csv
        if "refunds.csv" not in file_bytes_map:
            raise ValueError("Required file 'refunds.csv' is missing")
        refunds_raw = file_bytes_map["refunds.csv"].decode("utf-8-sig")
        refunds_reader = csv.DictReader(refunds_raw.splitlines())
        expected_refunds_cols = ["refund_id", "order_id", "processed_at", "amount_paise", "status"]
        if refunds_reader.fieldnames != expected_refunds_cols:
            raise ValueError(
                f"refunds.csv columns mismatch: expected {expected_refunds_cols}, got {refunds_reader.fieldnames}"
            )

        refunds_by_id: dict[str, dict[str, Any]] = {}
        refunds_first_row: dict[str, int] = {}
        for row_idx, row in enumerate(refunds_reader, start=1):
            row_loc = f"csv:r{row_idx}"
            rid = row["refund_id"].strip()
            oid = row["order_id"].strip()
            if not rid or len(rid) > 64:
                raise ValueError(f"Invalid refund_id '{rid}' at {row_loc}")
            if oid not in orders_by_id:
                raise ValueError(f"Refund {rid} references unknown order '{oid}' at {row_loc}")
            status = row["status"].strip()
            if status not in ("completed", "pending"):
                raise ValueError(f"Invalid refund status '{status}' at {row_loc}")
            proc_raw = row["processed_at"].strip()
            if status == "completed":
                if not proc_raw:
                    raise ValueError(f"Completed refund {rid} missing processed_at at {row_loc}")
                proc_at = _parse_iso_datetime(proc_raw, "processed_at", row_loc)
            else:
                proc_at = None
            amount = _parse_paise(row["amount_paise"], "amount_paise", row_loc)

            if rid in refunds_by_id:
                prev = refunds_by_id[rid]
                prev_row = refunds_first_row[rid]
                if (
                    prev["order_id"] == oid
                    and prev["processed_at"] == proc_at
                    and prev["amount_paise"] == amount
                    and prev["status"] == status
                ):
                    prev["row_locators"].append(row_loc)
                    duplicate_findings.append(
                        CaseFinding(
                            code="DUPLICATE_ROW",
                            severity="info",
                            affected_record_ids=[rid],
                            details={"table": "refunds", "locators": [f"csv:r{prev_row}", row_loc]},
                        )
                    )
                    duplicate_findings.append(
                        CaseFinding(
                            code="DUPLICATE_IDENTICAL_ROW",
                            severity="info",
                            affected_record_ids=[rid],
                            details={"table": "refunds", "locators": [f"csv:r{prev_row}", row_loc]},
                        )
                    )
                else:
                    raise ValueError(
                        f"Conflicting duplicate record ID '{rid}' in refunds.csv: row {prev_row} vs row {row_idx}"
                    )
            else:
                refunds_by_id[rid] = {
                    "refund_id": rid,
                    "order_id": oid,
                    "processed_at": proc_at,
                    "amount_paise": amount,
                    "status": status,
                    "row_locators": [row_loc],
                }
                refunds_first_row[rid] = row_idx

        # 3. fees.csv
        if "fees.csv" not in file_bytes_map:
            raise ValueError("Required file 'fees.csv' is missing")
        fees_raw = file_bytes_map["fees.csv"].decode("utf-8-sig")
        fees_reader = csv.DictReader(fees_raw.splitlines())
        expected_fees_cols = ["fee_id", "order_id", "assessed_at", "amount_paise", "fee_kind"]
        if fees_reader.fieldnames != expected_fees_cols:
            raise ValueError(
                f"fees.csv columns mismatch: expected {expected_fees_cols}, got {fees_reader.fieldnames}"
            )

        fees_by_id: dict[str, dict[str, Any]] = {}
        fees_first_row: dict[str, int] = {}
        for row_idx, row in enumerate(fees_reader, start=1):
            row_loc = f"csv:r{row_idx}"
            fid = row["fee_id"].strip()
            oid = row["order_id"].strip()
            if not fid or len(fid) > 64:
                raise ValueError(f"Invalid fee_id '{fid}' at {row_loc}")
            if oid not in orders_by_id:
                raise ValueError(f"Fee {fid} references unknown order '{oid}' at {row_loc}")
            assessed_at = _parse_iso_datetime(row["assessed_at"], "assessed_at", row_loc)
            amount = _parse_paise(row["amount_paise"], "amount_paise", row_loc)
            kind = row["fee_kind"].strip()
            if kind not in ("processing",):
                raise ValueError(f"Invalid fee_kind '{kind}' at {row_loc}")

            if fid in fees_by_id:
                prev = fees_by_id[fid]
                prev_row = fees_first_row[fid]
                if (
                    prev["order_id"] == oid
                    and prev["assessed_at"] == assessed_at
                    and prev["amount_paise"] == amount
                    and prev["fee_kind"] == kind
                ):
                    prev["row_locators"].append(row_loc)
                    duplicate_findings.append(
                        CaseFinding(
                            code="DUPLICATE_ROW",
                            severity="info",
                            affected_record_ids=[fid],
                            details={"table": "fees", "locators": [f"csv:r{prev_row}", row_loc]},
                        )
                    )
                    duplicate_findings.append(
                        CaseFinding(
                            code="DUPLICATE_IDENTICAL_ROW",
                            severity="info",
                            affected_record_ids=[fid],
                            details={"table": "fees", "locators": [f"csv:r{prev_row}", row_loc]},
                        )
                    )
                else:
                    raise ValueError(
                        f"Conflicting duplicate record ID '{fid}' in fees.csv: row {prev_row} vs row {row_idx}"
                    )
            else:
                fees_by_id[fid] = {
                    "fee_id": fid,
                    "order_id": oid,
                    "assessed_at": assessed_at,
                    "amount_paise": amount,
                    "fee_kind": kind,
                    "row_locators": [row_loc],
                }
                fees_first_row[fid] = row_idx

        # 4. payouts.csv
        if "payouts.csv" not in file_bytes_map:
            raise ValueError("Required file 'payouts.csv' is missing")
        payouts_raw = file_bytes_map["payouts.csv"].decode("utf-8-sig")
        payouts_reader = csv.DictReader(payouts_raw.splitlines())
        expected_payouts_cols = ["payout_id", "received_at", "amount_paise", "status"]
        if payouts_reader.fieldnames != expected_payouts_cols:
            raise ValueError(
                f"payouts.csv columns mismatch: expected {expected_payouts_cols}, got {payouts_reader.fieldnames}"
            )

        payouts_by_id: dict[str, dict[str, Any]] = {}
        payouts_first_row: dict[str, int] = {}
        for row_idx, row in enumerate(payouts_reader, start=1):
            row_loc = f"csv:r{row_idx}"
            pid = row["payout_id"].strip()
            if not pid or len(pid) > 64:
                raise ValueError(f"Invalid payout_id '{pid}' at {row_loc}")
            status = row["status"].strip()
            if status not in ("received", "scheduled"):
                raise ValueError(f"Invalid payout status '{status}' at {row_loc}")
            rec_raw = row["received_at"].strip()
            if status == "received":
                if not rec_raw:
                    raise ValueError(f"Received payout {pid} missing received_at at {row_loc}")
                rec_at = _parse_iso_datetime(rec_raw, "received_at", row_loc)
            else:
                rec_at = None
            amount = _parse_paise(row["amount_paise"], "amount_paise", row_loc)

            if pid in payouts_by_id:
                prev = payouts_by_id[pid]
                prev_row = payouts_first_row[pid]
                if (
                    prev["received_at"] == rec_at
                    and prev["amount_paise"] == amount
                    and prev["status"] == status
                ):
                    prev["row_locators"].append(row_loc)
                    duplicate_findings.append(
                        CaseFinding(
                            code="DUPLICATE_ROW",
                            severity="info",
                            affected_record_ids=[pid],
                            details={"table": "payouts", "locators": [f"csv:r{prev_row}", row_loc]},
                        )
                    )
                    duplicate_findings.append(
                        CaseFinding(
                            code="DUPLICATE_IDENTICAL_ROW",
                            severity="info",
                            affected_record_ids=[pid],
                            details={"table": "payouts", "locators": [f"csv:r{prev_row}", row_loc]},
                        )
                    )
                else:
                    raise ValueError(
                        f"Conflicting duplicate record ID '{pid}' in payouts.csv: row {prev_row} vs row {row_idx}"
                    )
            else:
                payouts_by_id[pid] = {
                    "payout_id": pid,
                    "received_at": rec_at,
                    "amount_paise": amount,
                    "status": status,
                    "row_locators": [row_loc],
                }
                payouts_first_row[pid] = row_idx

        # 5. payout_links.csv
        if "payout_links.csv" not in file_bytes_map:
            raise ValueError("Required file 'payout_links.csv' is missing")
        links_raw = file_bytes_map["payout_links.csv"].decode("utf-8-sig")
        links_reader = csv.DictReader(links_raw.splitlines())
        expected_links_cols = ["link_id", "payout_id", "order_id", "amount_paise", "recorded_at"]
        if links_reader.fieldnames != expected_links_cols:
            raise ValueError(
                f"payout_links.csv columns mismatch: expected {expected_links_cols}, got {links_reader.fieldnames}"
            )

        links_by_id: dict[str, dict[str, Any]] = {}
        links_first_row: dict[str, int] = {}
        order_to_link: dict[str, str] = {}
        for row_idx, row in enumerate(links_reader, start=1):
            row_loc = f"csv:r{row_idx}"
            lid = row["link_id"].strip()
            pid = row["payout_id"].strip()
            oid = row["order_id"].strip()
            if not lid or len(lid) > 64:
                raise ValueError(f"Invalid link_id '{lid}' at {row_loc}")
            if pid not in payouts_by_id:
                raise ValueError(f"Payout link {lid} references unknown payout '{pid}' at {row_loc}")
            if oid not in orders_by_id:
                raise ValueError(f"Payout link {lid} references unknown order '{oid}' at {row_loc}")
            amount = _parse_paise(row["amount_paise"], "amount_paise", row_loc)
            recorded_at = _parse_iso_datetime(row["recorded_at"], "recorded_at", row_loc)

            # Whole-order settlement validation: at most one effective link per order
            if oid in order_to_link and order_to_link[oid] != lid:
                raise ValueError(
                    f"Order '{oid}' has multiple conflicting links: {order_to_link[oid]} vs {lid}"
                )
            order_to_link[oid] = lid

            if lid in links_by_id:
                prev = links_by_id[lid]
                prev_row = links_first_row[lid]
                if (
                    prev["payout_id"] == pid
                    and prev["order_id"] == oid
                    and prev["amount_paise"] == amount
                    and prev["recorded_at"] == recorded_at
                ):
                    prev["row_locators"].append(row_loc)
                    duplicate_findings.append(
                        CaseFinding(
                            code="DUPLICATE_ROW",
                            severity="info",
                            affected_record_ids=[lid],
                            details={"table": "payout_links", "locators": [f"csv:r{prev_row}", row_loc]},
                        )
                    )
                    duplicate_findings.append(
                        CaseFinding(
                            code="DUPLICATE_IDENTICAL_ROW",
                            severity="info",
                            affected_record_ids=[lid],
                            details={"table": "payout_links", "locators": [f"csv:r{prev_row}", row_loc]},
                        )
                    )
                else:
                    raise ValueError(
                        f"Conflicting duplicate record ID '{lid}' in payout_links.csv: row {prev_row} vs row {row_idx}"
                    )
            else:
                links_by_id[lid] = {
                    "link_id": lid,
                    "payout_id": pid,
                    "order_id": oid,
                    "amount_paise": amount,
                    "recorded_at": recorded_at,
                    "row_locators": [row_loc],
                }
                links_first_row[lid] = row_idx

        # Compute business gap / discrepancy findings
        evidence_findings: list[CaseFinding] = []

        # 1. Missing payout link for paid orders
        for oid, o in orders_by_id.items():
            if o["settlement_state"] == "paid" and oid not in order_to_link:
                evidence_findings.append(
                    CaseFinding(
                        code="MISSING_PAYOUT_LINK",
                        severity="gap",
                        affected_record_ids=[oid],
                        details={"order_id": oid, "message": f"Paid order {oid} has no recorded payout link"},
                    )
                )

        # 2. Incomplete source families declared in manifest
        for fam, val in manifest.completeness.model_dump().items():
            if val == "unknown":
                fam_code = f"INCOMPLETE_{fam.upper()}"
                evidence_findings.append(
                    CaseFinding(
                        code=fam_code,
                        severity="gap",
                        affected_record_ids=[],
                        details={"family": fam, "status": "unknown"},
                    )
                )
                evidence_findings.append(
                    CaseFinding(
                        code="INCOMPLETE_SOURCE_FAMILY",
                        severity="gap",
                        affected_record_ids=[],
                        details={"family": fam, "status": "unknown"},
                    )
                )

        # 3. Payout header sum mismatch vs links
        payout_allocations: dict[str, int] = {}
        for link in links_by_id.values():
            pid = link["payout_id"]
            payout_allocations[pid] = payout_allocations.get(pid, 0) + link["amount_paise"]

        for pid, p in payouts_by_id.items():
            alloc_sum = payout_allocations.get(pid, 0)
            if p["status"] == "received" and p["amount_paise"] != alloc_sum:
                evidence_findings.append(
                    CaseFinding(
                        code="PAYOUT_ALLOCATION_MISMATCH",
                        severity="gap",
                        affected_record_ids=[pid],
                        details={
                            "payout_id": pid,
                            "header_paise": p["amount_paise"],
                            "allocated_paise": alloc_sum,
                        },
                    )
                )

        total_csv_records = len(orders_by_id) + len(refunds_by_id) + len(fees_by_id) + len(payouts_by_id) + len(links_by_id)
        if total_csv_records > CSV_MAX_TOTAL_RECORDS:
            raise ValueError(f"Total CSV records ({total_csv_records}) exceeds limit ({CSV_MAX_TOTAL_RECORDS})")

        return {
            "manifest": manifest,
            "fingerprint": fingerprint,
            "file_bytes_map": file_bytes_map,
            "orders": orders_by_id,
            "refunds": refunds_by_id,
            "fees": fees_by_id,
            "payouts": payouts_by_id,
            "payout_links": links_by_id,
            "duplicate_findings": duplicate_findings,
            "evidence_findings": evidence_findings,
        }


def _media_type_for_path(path: str) -> str:
    lower = path.lower()
    if lower.endswith(".csv"):
        return "text/csv"
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".md"):
        return "text/markdown"
    if lower.endswith(".txt"):
        return "text/plain"
    return "application/octet-stream"


async def import_bundle(
    bundle_path: Path,
    settings: Settings,
    indexer_fn: Callable[..., Any] | None = None,
    schema: str | None = None,
) -> ImportResult:
    """
    Atomically import bundle into database.
    Idempotent: returns existing case if fingerprint matches.
    If indexer_fn is None, raises INDEX_NOT_READY until MT1.3 indexer is wired.
    """
    validated = validate_bundle(bundle_path)
    manifest: Manifest = validated["manifest"]
    fingerprint: str = validated["fingerprint"]
    file_bytes_map: dict[str, bytes] = validated["file_bytes_map"]
    orders: dict[str, dict[str, Any]] = validated["orders"]
    refunds: dict[str, dict[str, Any]] = validated["refunds"]
    fees: dict[str, dict[str, Any]] = validated["fees"]
    payouts: dict[str, dict[str, Any]] = validated["payouts"]
    payout_links: dict[str, dict[str, Any]] = validated["payout_links"]
    duplicate_findings: list[CaseFinding] = validated["duplicate_findings"]
    evidence_findings: list[CaseFinding] = validated["evidence_findings"]

    # 1. Check idempotency: does case with this fingerprint already exist?
    writer_conn = await get_writer_connection(settings, schema=schema)
    try:
        async with writer_conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT case_id, status, fingerprint FROM cases WHERE fingerprint = %s",
                [fingerprint],
            )
            existing = await cur.fetchone()
            if existing:
                case_id = existing["case_id"]
                # Fetch record counts
                counts: dict[str, int] = {}
                for tbl in ["orders", "refunds", "fees", "payouts", "payout_links", "chunks", "policies"]:
                    await cur.execute(
                        sql.SQL("SELECT COUNT(*) AS c FROM {} WHERE case_id = %s").format(
                            sql.Identifier(tbl)
                        ),
                        [case_id],
                    )
                    row = await cur.fetchone()
                    counts[tbl] = row["c"] if row else 0

                # Ensure manifest.json is present in sources table for cited metadata
                await cur.execute(
                    "SELECT source_id FROM sources WHERE case_id = %s AND relative_name = 'manifest.json'",
                    [case_id],
                )
                m_row = await cur.fetchone()
                if not m_row and "manifest.json" in file_bytes_map:
                    m_content = file_bytes_map["manifest.json"]
                    m_sha = hashlib.sha256(m_content).hexdigest()
                    m_source_id = uuid.uuid4()
                    published_sources_dir = settings.data_dir / "sources" / str(case_id)
                    published_sources_dir.mkdir(parents=True, exist_ok=True)
                    (published_sources_dir / "manifest.json").write_bytes(m_content)
                    await cur.execute(
                        """
                        INSERT INTO sources (
                            source_id, case_id, relative_name, sha256, media_type,
                            kind, published_at, internal_path, aliases
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb);
                        """,
                        [
                            m_source_id,
                            case_id,
                            "manifest.json",
                            m_sha,
                            "application/json",
                            "manifest",
                            manifest.as_of,
                            (published_sources_dir / "manifest.json").as_posix(),
                            json.dumps([]),
                        ],
                    )
                    await writer_conn.commit()

                return ImportResult(
                    case_id=case_id,
                    status=existing["status"],
                    fingerprint=fingerprint,
                    reused=True,
                    record_counts=counts,
                    duplicate_findings=[df.model_dump() for df in duplicate_findings],
                    evidence_findings=[ef.model_dump() for ef in evidence_findings],
                )

        # 2. Wire real indexer
        if indexer_fn is None:
            from moneytrail.retrieval import index_sources
            indexer_fn = index_sources

        # 3. Create staging directory
        import_id = uuid.uuid4()
        staging_dir = settings.data_dir / "staging" / str(import_id)
        staging_dir.mkdir(parents=True, exist_ok=True)

        case_id = uuid.uuid4()
        published_sources_dir = settings.data_dir / "sources" / str(case_id)

        try:
            # Stage files
            source_records: list[dict[str, Any]] = []
            sources_by_rel_name: dict[str, uuid.UUID] = {}
            # Group filenames by content SHA256 to record aliases
            sha_to_rel_names: dict[str, list[str]] = {}
            for entry in manifest.files:
                p = entry.path.strip("/")
                sha_to_rel_names.setdefault(entry.sha256.lower(), []).append(p)

            for entry in manifest.files:
                p = entry.path.strip("/")
                content = file_bytes_map[p]
                staged_file = staging_dir / p
                staged_file.parent.mkdir(parents=True, exist_ok=True)
                staged_file.write_bytes(content)

                source_id = uuid.uuid4()
                sources_by_rel_name[p] = source_id
                media_type = _media_type_for_path(p)

                # Extract published_at for documents if available
                doc_pub_at = None
                is_policy_pdf = (entry.kind == "policy" or (p.endswith(".pdf") and ("policy" in p.lower() or "pol" in p.lower()))) and p.endswith(".pdf")
                effective_kind = "policy" if is_policy_pdf else entry.kind
                if is_policy_pdf:
                    try:
                        p_meta, _ = _parse_policy_text(content, source_id)
                        doc_pub_at = p_meta["published_at"]
                    except Exception:
                        pass
                elif entry.kind == "correspondence":
                    # Check first lines for Published-At:
                    lines = content.decode("utf-8", errors="ignore").splitlines()
                    for line in lines[:5]:
                        if line.startswith("Published-At:"):
                            try:
                                doc_pub_at = datetime.fromisoformat(line.split(":", 1)[1].strip())
                            except Exception:
                                pass

                internal_path = (published_sources_dir / p).as_posix()
                aliases = [other for other in sha_to_rel_names[entry.sha256.lower()] if other != p]
                source_records.append({
                    "source_id": source_id,
                    "case_id": case_id,
                    "relative_name": p,
                    "sha256": entry.sha256.lower(),
                    "media_type": media_type,
                    "kind": effective_kind,
                    "published_at": doc_pub_at,
                    "internal_path": internal_path,
                    "aliases": json.dumps(aliases),
                })

            if "manifest.json" in file_bytes_map and "manifest.json" not in sources_by_rel_name:
                m_content = file_bytes_map["manifest.json"]
                m_source_id = uuid.uuid4()
                sources_by_rel_name["manifest.json"] = m_source_id
                (staging_dir / "manifest.json").write_bytes(m_content)
                m_sha = hashlib.sha256(m_content).hexdigest()
                source_records.append({
                    "source_id": m_source_id,
                    "case_id": case_id,
                    "relative_name": "manifest.json",
                    "sha256": m_sha,
                    "media_type": "application/json",
                    "kind": "manifest",
                    "published_at": manifest.as_of,
                    "internal_path": (published_sources_dir / "manifest.json").as_posix(),
                    "aliases": json.dumps([]),
                })

            # Parse policies
            policy_records: list[dict[str, Any]] = []
            for entry in manifest.files:
                p = entry.path.strip("/")
                is_policy_pdf = (entry.kind == "policy" or (p.endswith(".pdf") and ("policy" in p.lower() or "pol" in p.lower()))) and p.endswith(".pdf")
                if is_policy_pdf:
                    content = file_bytes_map[p]
                    source_id = sources_by_rel_name[p]
                    pol_dict, locators = _parse_policy_text(content, source_id)
                    policy_records.append({
                        "case_id": case_id,
                        "policy_id": pol_dict["policy_id"],
                        "source_id": source_id,
                        "published_at": pol_dict["published_at"],
                        "effective_from": pol_dict["effective_from"],
                        "effective_to": pol_dict["effective_to"],
                        "calendar_days": pol_dict["calendar_days"],
                        "fee_bps": pol_dict["fee_bps"],
                        "metadata_locators": json.dumps(locators),
                    })

            # Run indexer function on staged files
            chunk_records = await indexer_fn(case_id, sources_by_rel_name, staging_dir, settings)

            # Determine case status
            has_gaps = any(ef.severity == "gap" for ef in evidence_findings)
            status = "ready_with_gaps" if has_gaps else "ready"

            # 4. Atomic database insertion within single transaction
            async with writer_conn.transaction():
                async with writer_conn.cursor() as cur:
                    # Insert case
                    await cur.execute(
                        """
                        INSERT INTO cases (
                            case_id, fingerprint, case_key, merchant_id, currency,
                            cohort_start, cohort_end, as_of, completeness, status, imported_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW());
                        """,
                        [
                            case_id,
                            fingerprint,
                            manifest.case_key,
                            manifest.merchant_id,
                            manifest.currency,
                            manifest.cohort_start,
                            manifest.cohort_end,
                            manifest.as_of,
                            json.dumps(manifest.completeness.model_dump()),
                            status,
                        ],
                    )

                    # Insert sources
                    for s_rec in source_records:
                        await cur.execute(
                            """
                            INSERT INTO sources (
                                source_id, case_id, relative_name, sha256, media_type,
                                kind, published_at, internal_path, aliases
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb);
                            """,
                            [
                                s_rec["source_id"],
                                s_rec["case_id"],
                                s_rec["relative_name"],
                                s_rec["sha256"],
                                s_rec["media_type"],
                                s_rec["kind"],
                                s_rec["published_at"],
                                s_rec["internal_path"],
                                s_rec["aliases"],
                            ],
                        )

                    # Insert orders
                    orders_source_id = sources_by_rel_name["orders.csv"]
                    for o in orders.values():
                        await cur.execute(
                            """
                            INSERT INTO orders (
                                case_id, order_id, captured_at, gross_paise, settlement_state,
                                source_id, row_locators
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb);
                            """,
                            [
                                case_id,
                                o["order_id"],
                                o["captured_at"],
                                o["gross_paise"],
                                o["settlement_state"],
                                orders_source_id,
                                json.dumps(o["row_locators"]),
                            ],
                        )

                    # Insert refunds
                    refunds_source_id = sources_by_rel_name["refunds.csv"]
                    for r in refunds.values():
                        await cur.execute(
                            """
                            INSERT INTO refunds (
                                case_id, refund_id, order_id, processed_at, amount_paise,
                                status, source_id, row_locators
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb);
                            """,
                            [
                                case_id,
                                r["refund_id"],
                                r["order_id"],
                                r["processed_at"],
                                r["amount_paise"],
                                r["status"],
                                refunds_source_id,
                                json.dumps(r["row_locators"]),
                            ],
                        )

                    # Insert fees
                    fees_source_id = sources_by_rel_name["fees.csv"]
                    for f in fees.values():
                        await cur.execute(
                            """
                            INSERT INTO fees (
                                case_id, fee_id, order_id, assessed_at, amount_paise,
                                fee_kind, source_id, row_locators
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb);
                            """,
                            [
                                case_id,
                                f["fee_id"],
                                f["order_id"],
                                f["assessed_at"],
                                f["amount_paise"],
                                f["fee_kind"],
                                fees_source_id,
                                json.dumps(f["row_locators"]),
                            ],
                        )

                    # Insert payouts
                    payouts_source_id = sources_by_rel_name["payouts.csv"]
                    for p in payouts.values():
                        await cur.execute(
                            """
                            INSERT INTO payouts (
                                case_id, payout_id, received_at, amount_paise, status,
                                source_id, row_locators
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb);
                            """,
                            [
                                case_id,
                                p["payout_id"],
                                p["received_at"],
                                p["amount_paise"],
                                p["status"],
                                payouts_source_id,
                                json.dumps(p["row_locators"]),
                            ],
                        )

                    # Insert payout_links
                    links_source_id = sources_by_rel_name["payout_links.csv"]
                    for lk in payout_links.values():
                        await cur.execute(
                            """
                            INSERT INTO payout_links (
                                case_id, link_id, payout_id, order_id, amount_paise,
                                recorded_at, source_id, row_locators
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb);
                            """,
                            [
                                case_id,
                                lk["link_id"],
                                lk["payout_id"],
                                lk["order_id"],
                                lk["amount_paise"],
                                lk["recorded_at"],
                                links_source_id,
                                json.dumps(lk["row_locators"]),
                            ],
                        )

                    # Insert policies
                    for pol in policy_records:
                        await cur.execute(
                            """
                            INSERT INTO policies (
                                case_id, policy_id, source_id, published_at, effective_from,
                                effective_to, calendar_days, fee_bps, metadata_locators
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb);
                            """,
                            [
                                pol["case_id"],
                                pol["policy_id"],
                                pol["source_id"],
                                pol["published_at"],
                                pol["effective_from"],
                                pol["effective_to"],
                                pol["calendar_days"],
                                pol["fee_bps"],
                                pol["metadata_locators"],
                            ],
                        )

                    # Insert case_findings
                    all_findings = duplicate_findings + evidence_findings
                    for finding in all_findings:
                        await cur.execute(
                            """
                            INSERT INTO case_findings (
                                finding_id, case_id, code, severity, affected_record_ids,
                                citations, details
                            ) VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb);
                            """,
                            [
                                uuid.uuid4(),
                                case_id,
                                finding.code,
                                finding.severity,
                                json.dumps(finding.affected_record_ids),
                                json.dumps([c.model_dump() for c in finding.citations]),
                                json.dumps(finding.details),
                            ],
                        )

                    # Insert chunks if indexer provided them
                    if chunk_records:
                        for chunk in chunk_records:
                            emb_str = f"[{','.join(str(x) for x in chunk['embedding'])}]"
                            await cur.execute(
                                """
                                INSERT INTO chunks (
                                    chunk_id, case_id, source_id, locator_id, text,
                                    token_count, embedding, tsv, embedding_revision
                                ) VALUES (
                                    %s, %s, %s, %s, %s, %s, %s::vector, to_tsvector('english', %s), %s
                                );
                                """,
                                [
                                    chunk["chunk_id"],
                                    case_id,
                                    chunk["source_id"],
                                    chunk["locator_id"],
                                    chunk["text"],
                                    chunk["token_count"],
                                    emb_str,
                                    chunk["text"],
                                    chunk["embedding_revision"],
                                ],
                            )

                    # Atomically publish source directory
                    published_sources_dir.parent.mkdir(parents=True, exist_ok=True)
                    if published_sources_dir.exists():
                        shutil.rmtree(published_sources_dir)
                    shutil.copytree(staging_dir, published_sources_dir)

            await writer_conn.commit()

            # Cleanup staging
            shutil.rmtree(staging_dir, ignore_errors=True)

            record_counts = {
                "orders": len(orders),
                "refunds": len(refunds),
                "fees": len(fees),
                "payouts": len(payouts),
                "payout_links": len(payout_links),
                "policies": len(policy_records),
                "chunks": len(chunk_records) if chunk_records else 0,
            }

            return ImportResult(
                case_id=case_id,
                status=status,
                fingerprint=fingerprint,
                reused=False,
                record_counts=record_counts,
                duplicate_findings=[df.model_dump() for df in duplicate_findings],
                evidence_findings=[ef.model_dump() for ef in evidence_findings],
            )

        except Exception as ex:
            await writer_conn.rollback()
            # Clean up staging and any published sources on failure
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            if published_sources_dir.exists():
                shutil.rmtree(published_sources_dir, ignore_errors=True)
            raise ex

    finally:
        await writer_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MoneyTrail Bundle Ingestion and Validation")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    val_parser = subparsers.add_parser("validate", help="Validate a case bundle without mutating state")
    val_parser.add_argument("path", type=Path, help="Path to bundle ZIP file")

    imp_parser = subparsers.add_parser("import", help="Import a case bundle into MoneyTrail")
    imp_parser.add_argument("path", type=Path, help="Path to bundle ZIP file")

    args = parser.parse_args()

    if args.subcommand == "validate":
        res = validate_bundle(args.path)
        manifest: Manifest = res["manifest"]
        summary = {
            "valid": True,
            "case_key": manifest.case_key,
            "fingerprint": res["fingerprint"],
            "orders_count": len(res["orders"]),
            "refunds_count": len(res["refunds"]),
            "fees_count": len(res["fees"]),
            "payouts_count": len(res["payouts"]),
            "payout_links_count": len(res["payout_links"]),
            "duplicate_findings": len(res["duplicate_findings"]),
            "evidence_findings": len(res["evidence_findings"]),
        }
        print(json.dumps(summary, indent=2))

    elif args.subcommand == "import":
        settings = Settings.from_env()
        # In MT1.3 real indexer will be supplied; until then import_bundle raises INDEX_NOT_READY
        from moneytrail.retrieval import index_sources  # Will be available in MT1.3
        result = asyncio.run(import_bundle(args.path, settings, indexer_fn=index_sources))
        print(json.dumps(result.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    main()
