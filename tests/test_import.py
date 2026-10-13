"""Regression tests for bundle validation, atomic import, deduplication, and error boundaries."""

import hashlib
import json
import os
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest
import psycopg
from psycopg import sql

from moneytrail.config import Settings
from moneytrail.db import get_reader_connection, get_writer_connection
from moneytrail.ingest import compute_bundle_fingerprint, import_bundle, validate_bundle
from moneytrail.schemas import Manifest


async def dummy_indexer(case_id: uuid.UUID, sources: dict[str, uuid.UUID], staging_dir: Path, settings: Settings) -> list[dict[str, Any]]:
    """Test indexer that extracts minimal chunks for document sources."""
    chunks = []
    for rel_name, s_id in sources.items():
        if rel_name.endswith((".pdf", ".md", ".txt")):
            chunks.append({
                "chunk_id": uuid.uuid4(),
                "source_id": s_id,
                "locator_id": "text:l1-10",
                "text": f"Sample chunk for {rel_name}",
                "token_count": 10,
                "embedding": [0.0] * 384,
                "embedding_revision": settings.embedding_revision,
            })
    return chunks


async def failing_indexer(case_id: uuid.UUID, sources: dict[str, uuid.UUID], staging_dir: Path, settings: Settings):
    """Test indexer that simulates mid-indexing failure."""
    raise RuntimeError("Simulated failure during document indexing")


@pytest.mark.asyncio
async def test_import_is_atomic(test_settings: Settings):
    """
    Force extraction/index insertion failure after valid CSV parsing;
    no case is selectable and no business/chunk rows remain for it.
    A complete valid import is ready.
    """
    demo_zip = Path("fixtures/demo.zip")
    assert demo_zip.exists(), "fixtures/demo.zip must exist"

    # 1. Verify index_not_ready guard
    with pytest.raises(RuntimeError, match="INDEX_NOT_READY"):
        await import_bundle(demo_zip, test_settings, indexer_fn=None)

    # 2. Injected mid-import failure
    with pytest.raises(RuntimeError, match="Simulated failure during document indexing"):
        await import_bundle(demo_zip, test_settings, indexer_fn=failing_indexer)

    # Verify atomicity: 0 rows survived in database
    writer_conn = await get_writer_connection(test_settings)
    try:
        tables = ["cases", "sources", "orders", "refunds", "fees", "payouts", "payout_links", "policies", "chunks", "case_findings"]
        for tbl in tables:
            cur = await writer_conn.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(tbl)))
            row = await cur.fetchone()
            count = row[0] if row else 0
            assert count == 0, f"Table '{tbl}' should have 0 rows after rolled-back import, found {count}"
    finally:
        await writer_conn.close()

    # 3. Successful import with test indexer
    result = await import_bundle(demo_zip, test_settings, indexer_fn=dummy_indexer)
    assert result.reused is False
    assert result.status == "ready"
    assert result.record_counts["orders"] == 5
    assert result.record_counts["refunds"] == 3
    assert result.record_counts["fees"] == 5
    assert result.record_counts["payouts"] == 2
    assert result.record_counts["payout_links"] == 4
    assert result.record_counts["policies"] == 2
    assert result.record_counts["chunks"] > 0

    # Verify rows in database
    reader_conn = await get_reader_connection(test_settings)
    try:
        cur = await reader_conn.execute("SELECT COUNT(*) FROM cases WHERE case_id = %s", [result.case_id])
        assert (await cur.fetchone())[0] == 1
        cur = await reader_conn.execute("SELECT COUNT(*) FROM orders WHERE case_id = %s", [result.case_id])
        assert (await cur.fetchone())[0] == 5
    finally:
        await reader_conn.close()

    # 4. Idempotent re-import returns existing case
    reused_result = await import_bundle(demo_zip, test_settings, indexer_fn=dummy_indexer)
    assert reused_result.reused is True
    assert reused_result.case_id == result.case_id
    assert reused_result.record_counts["orders"] == 5


@pytest.mark.asyncio
async def test_duplicates_and_conflicting_ids(tmp_path: Path):
    """
    Reordered ZIP entries/JSON whitespace preserve the fingerprint;
    repeated identical rows retain two locators and one amount;
    changed fields under the same ID reject the import with both row references.
    """
    demo_zip = Path("fixtures/demo.zip")
    assert demo_zip.exists()

    orig_val = validate_bundle(demo_zip)
    orig_fingerprint = orig_val["fingerprint"]

    # 1. Unpack demo, mutate manifest whitespace and reorder files in ZIP
    unpacked_dir = tmp_path / "unpacked"
    with zipfile.ZipFile(demo_zip, "r") as zf:
        zf.extractall(unpacked_dir)

    manifest_file = unpacked_dir / "manifest.json"
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))

    # Add lots of arbitrary whitespace and reformatted indentation
    reformatted_json = "   \n\n  " + json.dumps(manifest_data, indent=8) + "\n\n   "
    manifest_file.write_text(reformatted_json, encoding="utf-8")

    # Repack with reverse sorting of archive names
    mutated_zip = tmp_path / "mutated_demo.zip"
    with zipfile.ZipFile(mutated_zip, "w") as zf:
        all_files = []
        for root, _, files in os.walk(unpacked_dir):
            for f in files:
                all_files.append(Path(root) / f)
        # Reverse sort to test order independence
        for f in sorted(all_files, reverse=True):
            rel_name = f.relative_to(unpacked_dir).as_posix()
            zf.write(f, arcname=rel_name)

    mutated_val = validate_bundle(mutated_zip)
    assert mutated_val["fingerprint"] == orig_fingerprint, (
        f"Fingerprint changed despite identical logical content: {mutated_val['fingerprint']} vs {orig_fingerprint}"
    )

    # 2. Test identical duplicate row: retains two locators and creates duplicate finding
    dup_dir = tmp_path / "dup_case"
    shutil.copytree(unpacked_dir, dup_dir)
    orders_csv = dup_dir / "orders.csv"
    orig_orders_lines = orders_csv.read_text(encoding="utf-8").splitlines()
    # Duplicate O-001 identically as row 6
    dup_orders_lines = orig_orders_lines + [orig_orders_lines[1]]  # orig_orders_lines[1] is O-001
    orders_csv.write_text("\n".join(dup_orders_lines) + "\n", encoding="utf-8")

    # Update manifest sha256 for orders.csv
    dup_manifest_data = json.loads((dup_dir / "manifest.json").read_text(encoding="utf-8"))
    orders_sha = hashlib.sha256(orders_csv.read_bytes()).hexdigest().lower()
    for entry in dup_manifest_data["files"]:
        if entry["path"] == "orders.csv":
            entry["sha256"] = orders_sha
    (dup_dir / "manifest.json").write_text(json.dumps(dup_manifest_data, indent=2), encoding="utf-8")

    dup_zip = tmp_path / "dup.zip"
    with zipfile.ZipFile(dup_zip, "w") as zf:
        for root, _, files in os.walk(dup_dir):
            for f in sorted(files):
                p = Path(root) / f
                zf.write(p, arcname=p.relative_to(dup_dir).as_posix())

    dup_val = validate_bundle(dup_zip)
    assert len(dup_val["orders"]) == 5  # Deduplicated into 5 records
    assert dup_val["orders"]["O-001"]["row_locators"] == ["csv:r1", "csv:r6"]
    assert any(df.code == "DUPLICATE_ROW" and "O-001" in df.affected_record_ids for df in dup_val["duplicate_findings"])

    # 3. Test conflicting duplicate record ID: rejects import with both row references
    conflict_dir = tmp_path / "conflict_case"
    shutil.copytree(unpacked_dir, conflict_dir)
    conflict_orders_csv = conflict_dir / "orders.csv"
    # Create conflicting O-001 with different gross amount (99999999 instead of 8000000)
    conflict_line = "O-001,2026-09-25T10:00:00+05:30,99999999,paid"
    conflict_orders_lines = orig_orders_lines + [conflict_line]
    conflict_orders_csv.write_text("\n".join(conflict_orders_lines) + "\n", encoding="utf-8")

    conflict_manifest_data = json.loads((conflict_dir / "manifest.json").read_text(encoding="utf-8"))
    c_orders_sha = hashlib.sha256(conflict_orders_csv.read_bytes()).hexdigest().lower()
    for entry in conflict_manifest_data["files"]:
        if entry["path"] == "orders.csv":
            entry["sha256"] = c_orders_sha
    (conflict_dir / "manifest.json").write_text(json.dumps(conflict_manifest_data, indent=2), encoding="utf-8")

    conflict_zip = tmp_path / "conflict.zip"
    with zipfile.ZipFile(conflict_zip, "w") as zf:
        for root, _, files in os.walk(conflict_dir):
            for f in sorted(files):
                p = Path(root) / f
                zf.write(p, arcname=p.relative_to(conflict_dir).as_posix())

    with pytest.raises(ValueError) as excinfo:
        validate_bundle(conflict_zip)

    err_msg = str(excinfo.value)
    assert "Conflicting duplicate record ID 'O-001'" in err_msg
    assert "row 1" in err_msg
    assert "row 6" in err_msg
