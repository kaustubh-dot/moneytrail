"""Deterministic tests for candidate fusion, deduplication, locator parsing, and ranking."""

import uuid
import pytest

from moneytrail.retrieval import fuse_rrf, parse_locator


def test_candidates_and_locators():
    """Verify deterministic candidate fusion, deduplication, stable tie-breaking, and locator parsing."""
    # 1. Locator parsing - valid formats
    csv_loc = parse_locator("csv:r12")
    assert csv_loc == {"type": "csv", "row": 12}

    pdf_range = parse_locator("pdf:p2:l10-25")
    assert pdf_range == {
        "type": "pdf",
        "page": 2,
        "start_line": 10,
        "end_line": 25,
    }

    pdf_single = parse_locator("pdf:p1:l5")
    assert pdf_single == {
        "type": "pdf",
        "page": 1,
        "start_line": 5,
        "end_line": 5,
    }

    text_range = parse_locator("text:l1-15")
    assert text_range == {
        "type": "text",
        "start_line": 1,
        "end_line": 15,
    }

    text_single = parse_locator("text:l8")
    assert text_single == {
        "type": "text",
        "start_line": 8,
        "end_line": 8,
    }

    # Locator parsing - invalid boundaries and formats
    with pytest.raises(ValueError, match="CSV row must be >= 1"):
        parse_locator("csv:r0")

    with pytest.raises(ValueError, match="PDF page must be >= 1"):
        parse_locator("pdf:p0:l1-5")

    with pytest.raises(ValueError, match="PDF start_line must be >= 1"):
        parse_locator("pdf:p1:l0-5")

    with pytest.raises(ValueError, match="end_line .* must be >= start_line"):
        parse_locator("pdf:p1:l10-5")

    with pytest.raises(ValueError, match="Text start_line must be >= 1"):
        parse_locator("text:l0-5")

    with pytest.raises(ValueError, match="end_line .* must be >= start_line"):
        parse_locator("text:l20-10")

    with pytest.raises(ValueError, match="Unknown locator format"):
        parse_locator("invalid:locator:123")

    # 2. Deterministic candidate fusion (RRF) fixture
    s1 = uuid.UUID("11111111-1111-1111-1111-111111111111")
    s2 = uuid.UUID("22222222-2222-2222-2222-222222222222")

    c1 = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    c2 = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    c3 = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    c4 = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")

    # Dense candidates: c1 (rank 1), c2 (rank 2), c3 (rank 3)
    dense = [
        {"chunk_id": c1, "source_id": s1, "locator_id": "pdf:p1:l1-5", "text": "Chunk 1", "kind": "policy"},
        {"chunk_id": c2, "source_id": s1, "locator_id": "pdf:p1:l6-10", "text": "Chunk 2", "kind": "policy"},
        {"chunk_id": c3, "source_id": s2, "locator_id": "text:l1-10", "text": "Chunk 3", "kind": "correspondence"},
    ]

    # Lexical candidates: c2 (rank 1), c1 (rank 2), c4 (rank 3)
    lexical = [
        {"chunk_id": c2, "source_id": s1, "locator_id": "pdf:p1:l6-10", "text": "Chunk 2", "kind": "policy"},
        {"chunk_id": c1, "source_id": s1, "locator_id": "pdf:p1:l1-5", "text": "Chunk 1", "kind": "policy"},
        {"chunk_id": c4, "source_id": s2, "locator_id": "text:l11-20", "text": "Chunk 4", "kind": "correspondence"},
    ]

    # Expected RRF scores with k=60:
    # c1: dense rank 1 -> 1/61, lexical rank 2 -> 1/62 => 1/61 + 1/62
    # c2: dense rank 2 -> 1/62, lexical rank 1 -> 1/61 => 1/62 + 1/61
    # Both c1 and c2 have equal score: 1/61 + 1/62 = 0.01639344262 + 0.01612903225 = 0.03252247488
    # Tie breaking: source_id is identical (s1), locator_id: "pdf:p1:l1-5" < "pdf:p1:l6-10", so c1 before c2!
    # c3: dense rank 3 -> 1/63 => ~0.015873
    # c4: lexical rank 3 -> 1/63 => ~0.015873
    # Tie breaking between c3 and c4: source_id identical (s2), locator_id "text:l1-10" < "text:l11-20", so c3 before c4!

    fused = fuse_rrf(dense, lexical, k=60, max_candidates=20)
    assert len(fused) == 4
    assert fused[0]["chunk_id"] == c1
    assert fused[1]["chunk_id"] == c2
    assert fused[2]["chunk_id"] == c3
    assert fused[3]["chunk_id"] == c4

    expected_c1_score = (1.0 / 61.0) + (1.0 / 62.0)
    assert abs(fused[0]["rrf_score"] - expected_c1_score) < 1e-9
    assert fused[0]["dense_rank"] == 1
    assert fused[0]["lexical_rank"] == 2

    # Max candidates capping
    capped = fuse_rrf(dense, lexical, k=60, max_candidates=2)
    assert len(capped) == 2
    assert capped[0]["chunk_id"] == c1
    assert capped[1]["chunk_id"] == c2
