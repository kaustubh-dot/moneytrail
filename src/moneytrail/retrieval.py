"""Document extraction, embedding, vector retrieval, and source locator inspection."""

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
from sentence_transformers import SentenceTransformer

from moneytrail.config import Settings
from moneytrail.schemas import SearchResult, SearchResultItem, SourceSegment


_EMBEDDING_MODEL: SentenceTransformer | None = None


def get_embedding_model(settings: Settings) -> SentenceTransformer:
    """Return cached SentenceTransformer model instance with pinned revision."""
    global _EMBEDDING_MODEL
    if _EMBEDDING_MODEL is None:
        _EMBEDDING_MODEL = SentenceTransformer(
            settings.embedding_model, revision=settings.embedding_revision
        )
    return _EMBEDDING_MODEL


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


async def search_evidence(
    case_id: uuid.UUID,
    query: str,
    kind: Literal["all", "policy", "correspondence"] = "all",
    limit: int = 6,
    settings: Settings | None = None,
    db: psycopg.AsyncConnection | None = None,
) -> SearchResult:
    """
    Search chunks for case using lexical full-text search with tsvector.
    Retains final SearchResult shape and rank order.
    """
    if db is None:
        raise ValueError("Database connection is required for search_evidence")

    query_filter = "WHERE c.case_id = %s"
    params: list[Any] = [case_id]

    if kind != "all":
        query_filter += " AND s.kind = %s"
        params.append(kind)

    # Use websearch_to_tsquery for forgiving multi-word natural queries
    sql_query = f"""
        SELECT
            c.chunk_id,
            c.source_id,
            c.locator_id,
            c.text,
            s.kind,
            ts_rank_cd(c.tsv, websearch_to_tsquery('english', %s)) AS score
        FROM chunks c
        JOIN sources s ON c.source_id = s.source_id
        {query_filter}
        AND c.tsv @@ websearch_to_tsquery('english', %s)
        ORDER BY score DESC, c.locator_id ASC
        LIMIT %s;
    """
    exec_params = [query, *params, query, limit]

    items: list[SearchResultItem] = []
    async with db.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql_query, exec_params)
        rows = await cur.fetchall()

        # If strict websearch_to_tsquery matched no rows, fall back to plain or ilike for broad query
        if not rows:
            fallback_sql = f"""
                SELECT
                    c.chunk_id,
                    c.source_id,
                    c.locator_id,
                    c.text,
                    s.kind,
                    0.5 AS score
                FROM chunks c
                JOIN sources s ON c.source_id = s.source_id
                {query_filter}
                AND c.text ILIKE %s
                ORDER BY c.locator_id ASC
                LIMIT %s;
            """
            fallback_params = [*params, f"%{query}%", limit]
            await cur.execute(fallback_sql, fallback_params)
            rows = await cur.fetchall()

        for rank, row in enumerate(rows, start=1):
            items.append(
                SearchResultItem(
                    chunk_id=row["chunk_id"],
                    source_id=row["source_id"],
                    locator_id=row["locator_id"],
                    text=row["text"],
                    score=float(row["score"]),
                    rank=rank,
                    kind=row["kind"],
                )
            )

    return SearchResult(items=items, total=len(items))
