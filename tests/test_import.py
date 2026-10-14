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
from moneytrail.retrieval import read_source, search_evidence
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

    # Injected mid-import failure
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


@pytest.mark.asyncio
async def test_import_with_real_indexer_and_locators(test_settings: Settings):
    """
    MT1.3: Import complete case with real document extraction/indexing;
    verify record/chunk counts, idempotent re-import, locator inspection, and search.
    """
    demo_zip = Path("fixtures/demo.zip")
    assert demo_zip.exists()

    # 1. Full import with real indexer (indexer_fn=None uses index_sources)
    res = await import_bundle(demo_zip, test_settings)
    assert res.reused is False
    assert res.status == "ready"
    assert res.record_counts["orders"] == 5
    assert res.record_counts["refunds"] == 3
    assert res.record_counts["fees"] == 5
    assert res.record_counts["payouts"] == 2
    assert res.record_counts["payout_links"] == 4
    assert res.record_counts["policies"] == 2
    assert res.record_counts["chunks"] == 6

    # 2. Idempotent re-import
    re_res = await import_bundle(demo_zip, test_settings)
    assert re_res.reused is True
    assert re_res.case_id == res.case_id
    assert re_res.record_counts == res.record_counts

    # 3. Read and resolve source locators
    reader_conn = await get_reader_connection(test_settings)
    try:
        # Get sources
        cur = await reader_conn.execute("SELECT source_id, relative_name FROM sources WHERE case_id = %s", [res.case_id])
        sources = {name: sid for sid, name in (await cur.fetchall())}

        # CSV row locator
        orders_sid = sources["orders.csv"]
        seg_csv = await read_source(res.case_id, orders_sid, "csv:r1", test_settings, reader_conn)
        assert "O-001" in seg_csv.text
        assert seg_csv.metadata["values"]["gross_paise"] == "8000000"

        # PDF metadata locator
        pol_sid = sources["docs/policy-current.pdf"]
        seg_pdf = await read_source(res.case_id, pol_sid, "pdf:p1:l1-6", test_settings, reader_conn)
        assert "Policy-ID: POL-2026-09-CURRENT" in seg_pdf.text
        assert "Settlement-Calendar-Days: 2" in seg_pdf.text

        # Text line locator
        note_sid = sources["docs/settlement-note.md"]
        seg_txt = await read_source(res.case_id, note_sid, "text:l9-10", test_settings, reader_conn)
        assert "P-001" in seg_txt.text
        assert "P-002" in seg_txt.text

        # 4. Search evidence
        search_res = await search_evidence(res.case_id, "payout policy calendar days", kind="all", limit=4, settings=test_settings, db=reader_conn)
        assert search_res.total > 0
        assert search_res.items[0].locator_id.startswith("pdf:p1:")
        assert search_res.items[0].kind == "policy"
    finally:
        await reader_conn.close()


@pytest.mark.asyncio
async def test_correspondence_missing_or_malformed_headers_rejected(test_settings: Settings, tmp_path: Path):
    """
    F-009 regression test:
    Verify that correspondence files missing Document-ID: or Published-At:,
    or containing a malformed Published-At: timestamp, are rejected at import.
    """
    demo_zip = Path("fixtures/demo.zip")
    extract_dir = tmp_path / "case_malformed_corr"
    with zipfile.ZipFile(demo_zip) as zf:
        zf.extractall(extract_dir)

    # 1. Missing Document-ID
    note_path = extract_dir / "docs" / "settlement-note.md"
    note_path.write_text("Published-At: 2026-09-30T19:00:00+05:30\n\nMissing doc id\n", encoding="utf-8")

    # Re-calculate manifest sha256 for note
    manifest_path = extract_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for f in manifest["files"]:
        if f["path"] == "docs/settlement-note.md":
            f["sha256"] = hashlib.sha256(note_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    test_zip = tmp_path / "missing_doc_id.zip"
    with zipfile.ZipFile(test_zip, "w") as zf:
        for root, _, files in os.walk(extract_dir):
            for file in files:
                abs_f = Path(root) / file
                zf.write(abs_f, arcname=abs_f.relative_to(extract_dir).as_posix())

    with pytest.raises(ValueError, match="missing required 'Document-ID:' line"):
        await import_bundle(test_zip, test_settings)

    # 2. Malformed Published-At
    note_path.write_text("Document-ID: DOC-INVALID\nPublished-At: not-a-date\n\nInvalid timestamp\n", encoding="utf-8")
    for f in manifest["files"]:
        if f["path"] == "docs/settlement-note.md":
            f["sha256"] = hashlib.sha256(note_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    test_zip2 = tmp_path / "malformed_pub_at.zip"
    with zipfile.ZipFile(test_zip2, "w") as zf:
        for root, _, files in os.walk(extract_dir):
            for file in files:
                abs_f = Path(root) / file
                zf.write(abs_f, arcname=abs_f.relative_to(extract_dir).as_posix())

    with pytest.raises(ValueError, match="malformed Published-At"):
        await import_bundle(test_zip2, test_settings)

