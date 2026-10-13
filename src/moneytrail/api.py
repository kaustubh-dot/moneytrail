"""FastAPI application for MoneyTrail: authenticated case and source management APIs."""

import asyncio
import hmac
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from moneytrail.config import Settings
from moneytrail.db import get_reader_connection, get_writer_connection
from moneytrail.ingest import import_bundle
from moneytrail.retrieval import read_source
from moneytrail.schemas import ImportResult, SourceSegment


# Global workload lock to enforce single active local workload (import or investigation run)
_WORKLOAD_LOCK = asyncio.Lock()


def get_settings() -> Settings:
    """Dependency to retrieve runtime application settings."""
    return Settings.from_env()


def verify_owner_token(
    authorization: str | None = Header(None, alias="Authorization"),
    settings: Settings = Depends(get_settings),
) -> str:
    """Verify owner token using constant-time comparison."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Authorization header missing or invalid scheme",
                "details": {},
            },
        )

    token = authorization[7:].strip()
    if not token or not hmac.compare_digest(token.encode("utf-8"), settings.owner_token.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "code": "UNAUTHORIZED",
                "message": "Invalid owner bearer token",
                "details": {},
            },
        )
    return token


def create_app(custom_settings: Settings | None = None) -> FastAPI:
    """Create and configure FastAPI application."""
    app = FastAPI(title="MoneyTrail API", version="1.0.0")

    app.state.settings = custom_settings

    def resolve_settings() -> Settings:
        if app.state.settings is not None:
            return app.state.settings
        return Settings.from_env()

    # Safe error envelope exception handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        request_id = str(uuid.uuid4())
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            payload = {
                "error": {
                    "code": exc.detail.get("code", "HTTP_ERROR"),
                    "message": exc.detail.get("message", "An error occurred"),
                    "details": exc.detail.get("details", {}),
                    "request_id": request_id,
                }
            }
        else:
            payload = {
                "error": {
                    "code": "HTTP_ERROR",
                    "message": str(exc.detail),
                    "details": {},
                    "request_id": request_id,
                }
            }
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        request_id = str(uuid.uuid4())
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "An unexpected server error occurred",
                    "details": {},
                    "request_id": request_id,
                }
            },
        )

    # 1. Health endpoint (unauthenticated, reveals no configuration)
    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    # 2. Case Import endpoint
    @app.post("/api/v1/cases/import")
    async def import_case(
        response: Response,
        bundle: UploadFile = File(...),
        _token: str = Depends(verify_owner_token),
    ):
        settings = resolve_settings()

        # Enforce single active local workload
        if _WORKLOAD_LOCK.locked():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "RUN_BUSY",
                    "message": "Another import or investigation workload is currently in progress",
                    "details": {},
                },
            )

        async with _WORKLOAD_LOCK:
            # Save uploaded bundle to temporary file
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                temp_path = Path(tmp.name)
                try:
                    content = await bundle.read()
                    temp_path.write_bytes(content)

                    # Import bundle using real indexer pipeline
                    result: ImportResult = await import_bundle(temp_path, settings)

                    # 200 if reused, 201 if newly created
                    response.status_code = (
                        status.HTTP_200_OK if result.reused else status.HTTP_201_CREATED
                    )
                    return result.model_dump(mode="json")

                except ValueError as ve:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail={
                            "code": "INVALID_BUNDLE",
                            "message": str(ve),
                            "details": {},
                        },
                    )
                finally:
                    if temp_path.exists():
                        temp_path.unlink()

    # 3. List Cases endpoint
    @app.get("/api/v1/cases")
    async def list_cases(_token: str = Depends(verify_owner_token)):
        settings = resolve_settings()
        reader_conn = await get_reader_connection(settings)
        try:
            async with reader_conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT case_id, fingerprint, case_key, merchant_id, currency,
                           cohort_start, cohort_end, as_of, status, imported_at
                    FROM cases
                    ORDER BY imported_at DESC;
                    """
                )
                cases = await cur.fetchall()
                return cases
        finally:
            await reader_conn.close()

    # 4. Get Case Detail endpoint
    @app.get("/api/v1/cases/{case_id}")
    async def get_case(case_id: uuid.UUID, _token: str = Depends(verify_owner_token)):
        settings = resolve_settings()
        reader_conn = await get_reader_connection(settings)
        try:
            async with reader_conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT case_id, fingerprint, case_key, merchant_id, currency,
                           cohort_start, cohort_end, as_of, completeness, status, imported_at
                    FROM cases
                    WHERE case_id = %s;
                    """,
                    [case_id],
                )
                case_row = await cur.fetchone()
                if not case_row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "NOT_FOUND", "message": f"Case '{case_id}' not found", "details": {}},
                    )

                # Fetch record counts
                counts = {}
                for tbl in ["orders", "refunds", "fees", "payouts", "payout_links", "policies", "chunks"]:
                    await cur.execute(
                        sql.SQL("SELECT COUNT(*) AS c FROM {} WHERE case_id = %s").format(sql.Identifier(tbl)),
                        [case_id],
                    )
                    r = await cur.fetchone()
                    counts[tbl] = r["c"] if r else 0

                # Fetch sources
                await cur.execute(
                    """
                    SELECT source_id, relative_name, sha256, media_type, kind, published_at, aliases
                    FROM sources
                    WHERE case_id = %s
                    ORDER BY relative_name ASC;
                    """,
                    [case_id],
                )
                sources = await cur.fetchall()

                # Fetch findings
                await cur.execute(
                    """
                    SELECT finding_id, code, severity, affected_record_ids, citations, details
                    FROM case_findings
                    WHERE case_id = %s;
                    """,
                    [case_id],
                )
                findings = await cur.fetchall()

                return {
                    **case_row,
                    "record_counts": counts,
                    "sources": sources,
                    "findings": findings,
                }
        finally:
            await reader_conn.close()

    # 5. Get Source Detail endpoint
    @app.get("/api/v1/cases/{case_id}/sources/{source_id}")
    async def get_source(
        case_id: uuid.UUID, source_id: uuid.UUID, _token: str = Depends(verify_owner_token)
    ):
        settings = resolve_settings()
        reader_conn = await get_reader_connection(settings)
        try:
            async with reader_conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT source_id, case_id, relative_name, sha256, media_type, kind, published_at, aliases
                    FROM sources
                    WHERE case_id = %s AND source_id = %s;
                    """,
                    [case_id, source_id],
                )
                src = await cur.fetchone()
                if not src:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={
                            "code": "NOT_FOUND",
                            "message": f"Source '{source_id}' not found in case '{case_id}'",
                            "details": {},
                        },
                    )

                # Fetch available chunk locators
                await cur.execute(
                    """
                    SELECT locator_id
                    FROM chunks
                    WHERE case_id = %s AND source_id = %s
                    ORDER BY locator_id ASC;
                    """,
                    [case_id, source_id],
                )
                locs = [r["locator_id"] for r in await cur.fetchall()]
                return {**src, "locators": locs}
        finally:
            await reader_conn.close()

    # 6. Get Source Segment endpoint
    @app.get("/api/v1/cases/{case_id}/sources/{source_id}/segments")
    async def get_source_segment(
        case_id: uuid.UUID,
        source_id: uuid.UUID,
        locator_id: str = Query(...),
        _token: str = Depends(verify_owner_token),
    ):
        settings = resolve_settings()
        reader_conn = await get_reader_connection(settings)
        try:
            try:
                segment: SourceSegment = await read_source(
                    case_id, source_id, locator_id, settings, reader_conn
                )
                return segment.model_dump(mode="json")
            except KeyError:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "code": "NOT_FOUND",
                        "message": f"Source '{source_id}' not found in case '{case_id}'",
                        "details": {},
                    },
                )
            except (ValueError, FileNotFoundError) as ve:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": "INVALID_LOCATOR", "message": str(ve), "details": {}},
                )
        finally:
            await reader_conn.close()

    # 7. Get Source Content endpoint
    @app.get("/api/v1/cases/{case_id}/sources/{source_id}/content")
    async def get_source_content(
        case_id: uuid.UUID, source_id: uuid.UUID, _token: str = Depends(verify_owner_token)
    ):
        settings = resolve_settings()
        reader_conn = await get_reader_connection(settings)
        try:
            async with reader_conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT source_id, case_id, relative_name, media_type, internal_path
                    FROM sources
                    WHERE case_id = %s AND source_id = %s;
                    """,
                    [case_id, source_id],
                )
                src = await cur.fetchone()
                if not src:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={
                            "code": "NOT_FOUND",
                            "message": f"Source '{source_id}' not found in case '{case_id}'",
                            "details": {},
                        },
                    )

            file_path = Path(src["internal_path"])
            if not file_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "NOT_FOUND", "message": "Source file not found on disk", "details": {}},
                )

            # Controlled media types - prohibit executable HTML
            media_type = src["media_type"]
            if media_type == "text/html" or file_path.suffix.lower() in (".html", ".htm"):
                media_type = "text/plain"

            safe_filename = Path(src["relative_name"]).name
            return FileResponse(
                file_path,
                media_type=media_type,
                headers={"Content-Disposition": f'inline; filename="{safe_filename}"'},
            )
        finally:
            await reader_conn.close()

    return app


# Default ASGI application instance for uvicorn
app = create_app()
