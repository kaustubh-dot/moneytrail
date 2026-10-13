"""Document extraction, embedding, vector retrieval, and source locator inspection."""

import asyncio
import io
import json
import re
import uuid
from pathlib import Path
from typing import Any, Literal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pypdf
from sentence_transformers import CrossEncoder, SentenceTransformer

from moneytrail.config import Settings
from moneytrail.schemas import SearchResult, SearchResultItem, SourceSegment


_EMBEDDING_MODEL: SentenceTransformer | None = None
_RERANKER_MODEL: CrossEncoder | None = None


def get_embedding_model(settings: Settings) -> SentenceTransformer:
    """Return cached SentenceTransformer model instance with pinned revision."""
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        _EMBEDDING_MODEL = SentenceTransformer(
            settings.embedding_model, revision=settings.embedding_revision
        )
    return _EMBEDDING_MODEL


def get_reranker_model(settings: Settings) -> CrossEncoder:
    """Return cached CrossEncoder model instance with pinned revision."""
    global _RERANKER_MODEL
    if _RERANKER_MODEL is None:
        _RERANKER_MODEL = CrossEncoder(
            settings.reranker_model, revision=settings.reranker_revision
        )
    return _RERANKER_MODEL


def parse_locator(locator_id: str) -> dict[str, Any]:
    """Parse structured citation locator into component fields."""
    m_csv = re.match(r"^csv:r(\d+)$", locator_id)
    if m_csv:
        row = int(m_csv.group(1))
        if row < 1:
            raise ValueError(f"CSV row must be >= 1, got {row}")
        return {"type": "csv", "row": row}

    m_pdf = re.match(r"^pdf:p(\d+):l(\d+)(?:-l?(\d+))?$", locator_id)
    if m_pdf:
        page = int(m_pdf.group(1))
        start = int(m_pdf.group(2))
        end = int(m_pdf.group(3)) if m_pdf.group(3) else start
        if page < 1:
            raise ValueError(f"PDF page must be >= 1, got {page}")
        if start < 1:
            raise ValueError(f"PDF start_line must be >= 1, got {start}")
        if end < start:
            raise ValueError(f"PDF end_line ({end}) must be >= start_line ({start})")
        return {
            "type": "pdf",
            "page": page,
            "start_line": start,
            "end_line": end,
        }

    m_txt = re.match(r"^text:l(\d+)(?:-l?(\d+))?$", locator_id)
    if m_txt:
        start = int(m_txt.group(1))
        end = int(m_txt.group(2)) if m_txt.group(2) else start
        if start < 1:
            raise ValueError(f"Text start_line must be >= 1, got {start}")
        if end < start:
            raise ValueError(f"Text end_line ({end}) must be >= start_line ({start})")
        return {"type": "text", "start_line": start, "end_line": end}

    raise ValueError(f"Unknown locator format: {locator_id}")


def _chunk_lines(
    lines_with_numbers: list[tuple[int, str]],  # (1-based line number, line text)
    tokenizer: Any,
    prefix_locator: str,  # e.g. "pdf:p1:" or "text:"
    target_tokens: int = 180,
    overlap_tokens: int = 30,
) -> list[tuple[str, str, int]]:  # (locator_id, text, token_count)
    """Chunk lines into ~180 tokens with ~30 token overlap."""
    chunks: list[tuple[str, str, int]] = []
    if not lines_with_numbers:
        return chunks

    idx = 0
    total_lines = len(lines_with_numbers)
    while idx < total_lines:
        chunk_lines: list[tuple[int, str]] = []
        token_count = 0
        curr_idx = idx

        while curr_idx < total_lines:
            line_num, line_str = lines_with_numbers[curr_idx]
            line_tokens = len(tokenizer.encode(line_str, add_special_tokens=False))
            if token_count + line_tokens > target_tokens and chunk_lines:
                break
            chunk_lines.append((line_num, line_str))
            token_count += line_tokens
            curr_idx += 1

        if not chunk_lines:
            # Single line exceeds target_tokens, take it anyway
            line_num, line_str = lines_with_numbers[curr_idx]
            chunk_lines.append((line_num, line_str))
            token_count = len(tokenizer.encode(line_str, add_special_tokens=False))
            curr_idx += 1

        start_line = chunk_lines[0][0]
        end_line = chunk_lines[-1][0]
        loc_id = (
            f"{prefix_locator}l{start_line}-{end_line}"
            if start_line != end_line
            else f"{prefix_locator}l{start_line}"
        )
        text_content = "\n".join(l[1] for l in chunk_lines)
        chunks.append((loc_id, text_content, token_count))

        if curr_idx >= total_lines:
            break

        # Calculate overlap: backtrack lines until ~overlap_tokens reached
        overlap_count = 0
        backtrack_idx = curr_idx - 1
        while backtrack_idx > idx and overlap_count < overlap_tokens:
            l_str = lines_with_numbers[backtrack_idx][1]
            overlap_count += len(tokenizer.encode(l_str, add_special_tokens=False))
            backtrack_idx -= 1

        idx = max(backtrack_idx + 1, idx + 1)

    return chunks


async def index_sources(
    case_id: uuid.UUID,
    sources: dict[str, uuid.UUID],  # relative_name -> source_id
    staging_dir: Path,
    settings: Settings,
) -> list[dict[str, Any]]:
    """
    Extract document text, chunk preserving metadata boundaries, embed, and return chunk records.
    Handles PDF pages, TXT/MD lines, and identical document aliases.
    """
    model = get_embedding_model(settings)
    tokenizer = model.tokenizer

    # Detect identical document bytes and deduplicate chunk extraction
    sha_map: dict[str, list[tuple[str, uuid.UUID]]] = {}
    for rel_name, s_id in sources.items():
        if rel_name.endswith((".pdf", ".md", ".txt")):
            file_path = staging_dir / rel_name
            if file_path.exists():
                import hashlib
                f_sha = hashlib.sha256(file_path.read_bytes()).hexdigest().lower()
                sha_map.setdefault(f_sha, []).append((rel_name, s_id))

    all_chunks: list[dict[str, Any]] = []

    for f_sha, file_entries in sha_map.items():
        primary_rel_name, primary_s_id = file_entries[0]
        file_path = staging_dir / primary_rel_name

        extracted_chunks: list[tuple[str, str, int]] = []

        if primary_rel_name.endswith(".pdf"):
            try:
                reader = pypdf.PdfReader(file_path)
                if reader.is_encrypted:
                    raise ValueError(f"UNSUPPORTED_PDF: PDF '{primary_rel_name}' is encrypted; supply unencrypted selectable text.")
            except Exception as e:
                raise ValueError(f"Failed to read PDF source '{primary_rel_name}': {e}")

            for p_idx, page in enumerate(reader.pages, start=1):
                page_text = page.extract_text() or ""
                raw_lines = page_text.splitlines()
                nonempty_lines: list[tuple[int, str]] = []
                line_counter = 1
                for raw_l in raw_lines:
                    s_l = raw_l.strip()
                    if s_l:
                        nonempty_lines.append((line_counter, s_l))
                        line_counter += 1

                # Check for policy metadata lines on page 1
                if p_idx == 1 and any(
                    l[1].startswith("Policy-ID:") for l in nonempty_lines
                ):
                    metadata_lines = [
                        l for l in nonempty_lines if re.match(
                            r"^(Policy-ID|Published-At|Effective-From|Effective-To|Settlement-Calendar-Days|Processing-Fee-Bps):",
                            l[1],
                        )
                    ]
                    body_lines = [l for l in nonempty_lines if l not in metadata_lines]

                    # Metadata chunk
                    if metadata_lines:
                        m_start = metadata_lines[0][0]
                        m_end = metadata_lines[-1][0]
                        m_loc = (
                            f"pdf:p1:l{m_start}-{m_end}"
                            if m_start != m_end
                            else f"pdf:p1:l{m_start}"
                        )
                        m_text = "\n".join(l[1] for l in metadata_lines)
                        m_tokens = len(tokenizer.encode(m_text, add_special_tokens=False))
                        extracted_chunks.append((m_loc, m_text, m_tokens))

                    # Body chunks
                    body_chunks = _chunk_lines(
                        body_lines, tokenizer, prefix_locator=f"pdf:p{p_idx}:"
                    )
                    extracted_chunks.extend(body_chunks)
                else:
                    page_chunks = _chunk_lines(
                        nonempty_lines, tokenizer, prefix_locator=f"pdf:p{p_idx}:"
                    )
                    extracted_chunks.extend(page_chunks)

        elif primary_rel_name.endswith((".md", ".txt")):
            text_content = file_path.read_text(encoding="utf-8")
            raw_lines = text_content.splitlines()
            physical_lines = [
                (idx, line.strip())
                for idx, line in enumerate(raw_lines, start=1)
                if line.strip()
            ]

            txt_chunks = _chunk_lines(
                physical_lines, tokenizer, prefix_locator="text:"
            )
            extracted_chunks.extend(txt_chunks)

        # Batch compute embeddings for extracted chunks
        if extracted_chunks:
            texts_to_embed = [c[1] for c in extracted_chunks]
            embeddings = model.encode(texts_to_embed, show_progress_bar=False)

            # Assign chunks to all sources sharing identical content bytes
            for rel_name, s_id in file_entries:
                for (loc_id, text, tokens), emb in zip(extracted_chunks, embeddings):
                    all_chunks.append({
                        "chunk_id": uuid.uuid4(),
                        "source_id": s_id,
                        "locator_id": loc_id,
                        "text": text,
                        "token_count": tokens,
                        "embedding": emb.tolist(),
                        "embedding_revision": settings.embedding_revision,
                    })

    return all_chunks


async def read_source(
    case_id: uuid.UUID,
    source_id: uuid.UUID,
    locator_id: str,
    settings: Settings,
    db: psycopg.AsyncConnection,
) -> SourceSegment:
    """
    Inspect exact source segment for given locator in case.
    Resolves CSV rows, PDF page line ranges, and text line ranges.
    """
    async with db.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            """
            SELECT source_id, case_id, relative_name, media_type, kind, internal_path
            FROM sources
            WHERE case_id = %s AND source_id = %s;
            """,
            [case_id, source_id],
        )
        src = await cur.fetchone()
        if not src:
            raise KeyError(f"Source {source_id} not found in case {case_id}")

    loc_info = parse_locator(locator_id)
    internal_path = Path(src["internal_path"])
    if not internal_path.exists():
        raise FileNotFoundError(f"Source file missing on disk: {internal_path}")

    loc_type = loc_info["type"]
    segment_text = ""
    metadata: dict[str, Any] = {"relative_name": src["relative_name"], "kind": src["kind"]}

    # Verify locator compatibility with source kind
    rel_name = src["relative_name"].lower()
    media_type = src["media_type"].lower()

    if loc_type == "csv":
        if not (rel_name.endswith(".csv") or media_type == "text/csv"):
            raise ValueError(
                f"Cannot apply CSV locator '{locator_id}' to non-CSV source '{src['relative_name']}'"
            )
        row_target = loc_info["row"]
        import csv
        found = False
        with open(internal_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            metadata["header"] = header
            curr_row = 1
            for row in reader:
                if curr_row == row_target:
                    segment_text = ",".join(row)
                    metadata["row_index"] = curr_row
                    metadata["values"] = dict(zip(header, row)) if header else row
                    found = True
                    break
                curr_row += 1
        if not found:
            raise ValueError(f"CSV row {row_target} not found in {src['relative_name']}")

    elif loc_type == "pdf":
        if not (rel_name.endswith(".pdf") or media_type == "application/pdf"):
            raise ValueError(
                f"Cannot apply PDF locator '{locator_id}' to non-PDF source '{src['relative_name']}'"
            )
        page_num = loc_info["page"]
        start_line = loc_info["start_line"]
        end_line = loc_info["end_line"]

        try:
            reader = pypdf.PdfReader(internal_path)
        except Exception as e:
            raise ValueError(f"Failed to read PDF file '{src['relative_name']}': {e}")

        if page_num > len(reader.pages):
            raise ValueError(f"Page {page_num} exceeds PDF page count ({len(reader.pages)})")

        page_text = reader.pages[page_num - 1].extract_text() or ""
        lines = [l.strip() for l in page_text.splitlines() if l.strip()]

        if start_line > len(lines):
            raise ValueError(f"Start line {start_line} exceeds PDF page {page_num} line count ({len(lines)})")
        if end_line > len(lines):
            raise ValueError(f"End line {end_line} exceeds PDF page {page_num} line count ({len(lines)})")

        selected_lines = lines[start_line - 1 : end_line]
        segment_text = "\n".join(selected_lines)
        metadata["page"] = page_num
        metadata["start_line"] = start_line
        metadata["end_line"] = end_line

    elif loc_type == "text":
        if not (rel_name.endswith((".md", ".txt")) or media_type.startswith("text/")):
            raise ValueError(
                f"Cannot apply text locator '{locator_id}' to non-text source '{src['relative_name']}'"
            )
        start_line = loc_info["start_line"]
        end_line = loc_info["end_line"]

        raw_lines = internal_path.read_text(encoding="utf-8").splitlines()
        if start_line > len(raw_lines):
            raise ValueError(f"Start line {start_line} exceeds file line count ({len(raw_lines)})")
        if end_line > len(raw_lines):
            raise ValueError(f"End line {end_line} exceeds file line count ({len(raw_lines)})")

        selected_lines = raw_lines[start_line - 1 : end_line]
        segment_text = "\n".join(selected_lines)
        metadata["start_line"] = start_line
        metadata["end_line"] = end_line

    return SourceSegment(
        source_id=source_id,
        locator_id=locator_id,
        text=segment_text,
        media_type=src["media_type"],
        metadata=metadata,
    )


def fuse_rrf(
    dense_candidates: list[dict[str, Any]],
    lexical_candidates: list[dict[str, Any]],
    k: int = 60,
    max_candidates: int = 20,
) -> list[dict[str, Any]]:
    """
    Reciprocal-rank fusion using sum(1 / (k + rank)) with 1-based ranks.
    Deduplicates candidates and breaks ties stably by (-rrf_score, source_id, locator_id).
    Returns top max_candidates unique items.
    """
    scores: dict[str, float] = {}
    items: dict[str, dict[str, Any]] = {}
    dense_ranks: dict[str, int] = {}
    lexical_ranks: dict[str, int] = {}

    for rank, cand in enumerate(dense_candidates, start=1):
        key = str(cand["chunk_id"])
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
        dense_ranks[key] = rank
        if key not in items:
            items[key] = cand

    for rank, cand in enumerate(lexical_candidates, start=1):
        key = str(cand["chunk_id"])
        scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
        lexical_ranks[key] = rank
        if key not in items:
            items[key] = cand

    fused: list[dict[str, Any]] = []
    for key, cand in items.items():
        item_copy = dict(cand)
        item_copy["rrf_score"] = scores[key]
        item_copy["dense_rank"] = dense_ranks.get(key)
        item_copy["lexical_rank"] = lexical_ranks.get(key)
        fused.append(item_copy)

    # Sort stably: highest rrf_score first, then source_id, then locator_id
    fused.sort(
        key=lambda x: (
            -x["rrf_score"],
            str(x["source_id"]),
            str(x["locator_id"]),
        )
    )
    return fused[:max_candidates]


async def rerank_candidates(
    query: str,
    candidates: list[dict[str, Any]],
    limit: int = 6,
    settings: Settings | None = None,
) -> list[dict[str, Any]]:
    """
    Rerank fused candidates using pinned CrossEncoder model.
    Breaks ties stably by (-rerank_score, source_id, locator_id).
    Returns top limit items.
    """
    if not candidates:
        return []

    if settings is None:
        settings = Settings.from_env()

    reranker = get_reranker_model(settings)
    pairs = [(query, cand["text"]) for cand in candidates]
    scores = await asyncio.to_thread(reranker.predict, pairs)

    reranked: list[dict[str, Any]] = []
    for cand, score in zip(candidates, scores):
        item_copy = dict(cand)
        item_copy["rerank_score"] = float(score)
        reranked.append(item_copy)

    reranked.sort(
        key=lambda x: (
            -x["rerank_score"],
            str(x["source_id"]),
            str(x["locator_id"]),
        )
    )
    return reranked[:limit]


async def search_evidence(
    case_id: uuid.UUID,
    query: str,
    kind: Literal["all", "policy", "correspondence"] = "all",
    limit: int = 6,
    settings: Settings | None = None,
    db: psycopg.AsyncConnection | None = None,
) -> SearchResult:
    """
    Hybrid evidence search combining exact dense cosine search and full-text lexical search
    via Reciprocal Rank Fusion (RRF constant 60) followed by CrossEncoder reranking.
    Filters by immutable case ID, publication cutoff (published_at < as_of), and requested kind.
    """
    if settings is None:
        settings = Settings.from_env()

    if db is None:
        from moneytrail.db import get_reader_connection
        async with await get_reader_connection(settings) as reader_conn:
            return await search_evidence(
                case_id=case_id,
                query=query,
                kind=kind,
                limit=limit,
                settings=settings,
                db=reader_conn,
            )

    query_filter = "WHERE c.case_id = %s"
    params: list[Any] = [case_id]

    if kind != "all":
        query_filter += " AND s.kind = %s"
        params.append(kind)

    # 1. Exact dense cosine search (top 20)
    emb_model = get_embedding_model(settings)
    query_emb = await asyncio.to_thread(emb_model.encode, query)
    emb_vector = query_emb.tolist() if hasattr(query_emb, "tolist") else list(query_emb)

    dense_sql = f"""
        SELECT
            c.chunk_id,
            c.source_id,
            c.locator_id,
            c.text,
            s.kind,
            s.relative_name,
            p.policy_id,
            p.effective_to,
            cs.cohort_start,
            1 - (c.embedding <=> %s::vector) AS dense_score
        FROM chunks c
        JOIN cases cs ON cs.case_id = c.case_id
        JOIN sources s ON s.source_id = c.source_id
        LEFT JOIN policies p ON p.source_id = s.source_id AND p.case_id = c.case_id
        {query_filter}
          AND (s.published_at IS NULL OR s.published_at < cs.as_of)
        ORDER BY (c.embedding <=> %s::vector) ASC, s.source_id ASC, c.locator_id ASC
        LIMIT 20;
    """
    dense_params = [emb_vector, *params, emb_vector]

    async with db.cursor(row_factory=dict_row) as cur:
        await cur.execute(dense_sql, dense_params)
        dense_rows = await cur.fetchall()
        dense_candidates = [dict(r) for r in dense_rows]

        # 2. PostgreSQL websearch_to_tsquery full-text search (top 20)
        lexical_sql = f"""
            SELECT
                c.chunk_id,
                c.source_id,
                c.locator_id,
                c.text,
                s.kind,
                s.relative_name,
                p.policy_id,
                p.effective_to,
                cs.cohort_start,
                ts_rank_cd(c.tsv, websearch_to_tsquery('english', %s)) AS lexical_score
            FROM chunks c
            JOIN cases cs ON cs.case_id = c.case_id
            JOIN sources s ON s.source_id = c.source_id
            LEFT JOIN policies p ON p.source_id = s.source_id AND p.case_id = c.case_id
            {query_filter}
              AND (s.published_at IS NULL OR s.published_at < cs.as_of)
              AND c.tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY ts_rank_cd(c.tsv, websearch_to_tsquery('english', %s)) DESC, s.source_id ASC, c.locator_id ASC
            LIMIT 20;
        """
        lexical_params = [query, *params, query, query]
        await cur.execute(lexical_sql, lexical_params)
        lexical_rows = await cur.fetchall()
        lexical_candidates = [dict(r) for r in lexical_rows]

        # If strict websearch_to_tsquery returned fewer than 20 results, supplement with OR query
        if len(lexical_candidates) < 20:
            words = [w for w in re.sub(r"[^A-Za-z0-9]", " ", query).split() if len(w) > 2]
            if words:
                or_query = " | ".join(words)
                existing_ids = {r["chunk_id"] for r in lexical_candidates}
                fallback_sql = f"""
                    SELECT
                        c.chunk_id,
                        c.source_id,
                        c.locator_id,
                        c.text,
                        s.kind,
                        s.relative_name,
                        p.policy_id,
                        p.effective_to,
                        cs.cohort_start,
                        ts_rank_cd(c.tsv, to_tsquery('english', %s)) AS lexical_score
                    FROM chunks c
                    JOIN cases cs ON cs.case_id = c.case_id
                    JOIN sources s ON s.source_id = c.source_id
                    LEFT JOIN policies p ON p.source_id = s.source_id AND p.case_id = c.case_id
                    {query_filter}
                      AND (s.published_at IS NULL OR s.published_at < cs.as_of)
                      AND c.tsv @@ to_tsquery('english', %s)
                    ORDER BY ts_rank_cd(c.tsv, to_tsquery('english', %s)) DESC, s.source_id ASC, c.locator_id ASC
                    LIMIT %s;
                """
                remaining = 20 - len(lexical_candidates)
                fb_params = [or_query, *params, or_query, or_query, remaining]
                try:
                    await cur.execute(fallback_sql, fb_params)
                    fb_rows = await cur.fetchall()
                    for r in fb_rows:
                        if r["chunk_id"] not in existing_ids:
                            lexical_candidates.append(dict(r))
                            existing_ids.add(r["chunk_id"])
                except Exception:
                    pass

    # 3. Reciprocal Rank Fusion (top 20)
    fused_candidates = fuse_rrf(
        dense_candidates, lexical_candidates, k=60, max_candidates=20
    )

    # 4. Cross-Encoder reranking (top limit)
    reranked_candidates = await rerank_candidates(
        query=query,
        candidates=fused_candidates,
        limit=limit,
        settings=settings,
    )

    # 5. Build SearchResultItem list with ranking metadata
    items: list[SearchResultItem] = []
    for rank, cand in enumerate(reranked_candidates, start=1):
        is_superseded = False
        if cand.get("effective_to") and cand.get("cohort_start"):
            eff_to = cand["effective_to"]
            c_start = cand["cohort_start"]
            if hasattr(c_start, "date"):
                c_start = c_start.date()
            if hasattr(eff_to, "date"):
                eff_to = eff_to.date()
            if eff_to <= c_start:
                is_superseded = True

        item_meta: dict[str, Any] = {
            "dense_score": cand.get("dense_score"),
            "dense_rank": cand.get("dense_rank"),
            "lexical_score": cand.get("lexical_score"),
            "lexical_rank": cand.get("lexical_rank"),
            "rrf_score": cand.get("rrf_score"),
            "rerank_score": cand.get("rerank_score"),
            "policy_id": cand.get("policy_id"),
            "relative_name": cand.get("relative_name"),
        }
        if is_superseded:
            item_meta["excluded_policy"] = True

        items.append(
            SearchResultItem(
                chunk_id=cand["chunk_id"],
                source_id=cand["source_id"],
                locator_id=cand["locator_id"],
                text=cand["text"],
                score=float(cand.get("rerank_score", cand.get("rrf_score", 0.0))),
                rank=rank,
                kind=cand["kind"],
                metadata=item_meta,
            )
        )

    search_metadata: dict[str, Any] = {
        "query": query,
        "kind": kind,
        "dense_candidate_count": len(dense_candidates),
        "lexical_candidate_count": len(lexical_candidates),
        "fused_candidate_count": len(fused_candidates),
        "selected_count": len(items),
        "dense_top20_sources": [str(c["source_id"]) for c in dense_candidates[:20]],
        "lexical_top20_sources": [str(c["source_id"]) for c in lexical_candidates[:20]],
        "fused_top20_sources": [str(c["source_id"]) for c in fused_candidates[:20]],
        "dense_top6_sources": [str(c["source_id"]) for c in dense_candidates[:6]],
        "lexical_top6_sources": [str(c["source_id"]) for c in lexical_candidates[:6]],
        "fused_top6_sources": [str(c["source_id"]) for c in fused_candidates[:6]],
        "reranked_top6_sources": [str(it.source_id) for it in items[:6]],
    }

    return SearchResult(items=items, total=len(items), metadata=search_metadata)

