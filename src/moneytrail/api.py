"""FastAPI application for MoneyTrail: authenticated case and source management APIs."""

import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

import time
from moneytrail.agent import run_agent, run_fixed
from moneytrail.config import ZIP_MAX_COMPRESSED_BYTES, Settings
from moneytrail.db import get_reader_connection, get_writer_connection
from moneytrail.ingest import import_bundle
from moneytrail.llm import LLMError
from moneytrail.retrieval import read_source
from moneytrail.schemas import ImportResult, InvestigationRequest, SourceSegment


# Global workload lock to enforce single active local workload (import or investigation run)
_WORKLOAD_LOCK = asyncio.Lock()
_ACTIVE_RUNS: dict[uuid.UUID, asyncio.Event] = {}


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

    def resolve_settings() -> Settings:
        if app.state.settings is not None:
            return app.state.settings
        return Settings.from_env()

    @asynccontextmanager
    async def lifespan(app_instance: FastAPI):
        """Clean up orphan staging directories and mark interrupted runs as failed on startup."""
        try:
            settings = resolve_settings()
            staging_root = settings.data_dir / "staging"
            if staging_root.exists():
                for orphan in staging_root.iterdir():
                    if orphan.is_dir():
                        shutil.rmtree(orphan, ignore_errors=True)

            try:
                w_conn = await get_writer_connection(settings)
                try:
                    async with w_conn.cursor() as cur:
                        await cur.execute(
                            """
                            UPDATE runs
                            SET state = 'failed', finished_at = NOW(),
                                error = '{"code": "PROCESS_INTERRUPTED", "message": "Run interrupted by process restart"}'::jsonb
                            WHERE state = 'running';
                            """
                        )
                    await w_conn.commit()
                finally:
                    await w_conn.close()
            except Exception:
                pass
        except Exception:
            pass
        yield

    app = FastAPI(title="MoneyTrail API", version="1.0.0", lifespan=lifespan)
    app.state.settings = custom_settings

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
            content = await bundle.read()
            if len(content) > ZIP_MAX_COMPRESSED_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail={
                        "code": "PAYLOAD_TOO_LARGE",
                        "message": f"Bundle size ({len(content)} bytes) exceeds limit ({ZIP_MAX_COMPRESSED_BYTES} bytes)",
                        "details": {},
                    },
                )

            # Save uploaded bundle to temporary file
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                temp_path = Path(tmp.name)
                try:
                    temp_path.write_bytes(content)

                    # Import bundle using real indexer pipeline
                    result: ImportResult = await import_bundle(temp_path, settings)

                    # 200 if reused, 201 if newly created
                    response.status_code = (
                        status.HTTP_200_OK if result.reused else status.HTTP_201_CREATED
                    )
                    return result.model_dump(mode="json")

                except (ValueError, zipfile.BadZipFile) as ve:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
            if not file_path.exists() or not file_path.resolve().is_relative_to(settings.data_dir.resolve()):
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

    # --- Investigation Runs Endpoints ---

    @app.post("/api/v1/investigations", status_code=status.HTTP_202_ACCEPTED)
    async def start_investigation(
        req: InvestigationRequest,
        token: str = Depends(verify_owner_token),
    ):
        settings = resolve_settings()
        reader_conn = await get_reader_connection(settings)
        try:
            async with reader_conn.cursor() as cur:
                await cur.execute("SELECT case_id FROM cases WHERE case_id = %s;", [req.case_id])
                if not await cur.fetchone():
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "CASE_NOT_FOUND", "message": f"Case {req.case_id} not found"},
                    )
        finally:
            await reader_conn.close()

        if _WORKLOAD_LOCK.locked():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "RUN_BUSY", "message": "Another workload or investigation is currently running"},
            )

        run_id = uuid.uuid4()
        mode = req.mode or "agent"
        writer_conn = await get_writer_connection(settings)
        try:
            async with writer_conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO runs (run_id, case_id, mode, question, state, started_at, cancel_requested, hashes)
                    VALUES (%s, %s, %s, %s, 'running', NOW(), FALSE, %s);
                    """,
                    [run_id, req.case_id, mode, req.question, json.dumps({"model": settings.model})],
                )
            await writer_conn.commit()
        finally:
            await writer_conn.close()

        cancel_event = asyncio.Event()
        _ACTIVE_RUNS[run_id] = cancel_event

        async def _bg_run():
            async with _WORKLOAD_LOCK:
                start_time = time.perf_counter()
                seq = 0

                async def record_event(kind: str, payload: dict[str, Any]):
                    nonlocal seq
                    seq += 1
                    elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                    w_conn = await get_writer_connection(settings)
                    try:
                        async with w_conn.cursor() as cur:
                            await cur.execute(
                                """
                                INSERT INTO run_events (run_id, sequence, timestamp, kind, elapsed_ms, payload)
                                VALUES (%s, %s, NOW(), %s, %s, %s);
                                """ ,
                                [run_id, seq, kind, elapsed_ms, json.dumps(payload)],
                            )
                        await w_conn.commit()
                    finally:
                        await w_conn.close()

                w_conn = await get_writer_connection(settings)
                try:
                    await record_event("run_started", {"case_id": str(req.case_id), "question": req.question, "mode": mode})

                    if mode == "fixed":
                        result = await run_fixed(
                            req,
                            run_id,
                            settings,
                            cancel_event=cancel_event,
                            record_event_fn=record_event,
                        )
                    else:
                        result = await run_agent(
                            req,
                            run_id,
                            settings,
                            cancel_event=cancel_event,
                            record_event_fn=record_event,
                        )

                    if cancel_event.is_set():
                        async with w_conn.cursor() as cur:
                            await cur.execute(
                                "UPDATE runs SET state = 'cancelled', finished_at = NOW() WHERE run_id = %s;",
                                [run_id],
                            )
                        await w_conn.commit()
                        await record_event("cancel_requested", {"reason": "Cancelled by user"})
                    else:
                        async with w_conn.cursor() as cur:
                            await cur.execute(
                                """
                                UPDATE runs
                                SET state = 'completed', finished_at = NOW(), result = %s
                                WHERE run_id = %s;
                                """ ,
                                [json.dumps(result.model_dump(mode="json")), run_id],
                            )
                        await w_conn.commit()

                except asyncio.CancelledError:
                    async with w_conn.cursor() as cur:
                        await cur.execute(
                            "UPDATE runs SET state = 'cancelled', finished_at = NOW() WHERE run_id = %s;",
                            [run_id],
                        )
                    await w_conn.commit()
                    await record_event("cancel_requested", {"reason": "Task cancelled"})

                except LLMError as e:
                    err_payload = {"code": e.code, "message": e.message, "details": e.details}
                    async with w_conn.cursor() as cur:
                        await cur.execute(
                            """
                            UPDATE runs
                            SET state = 'failed', finished_at = NOW(), error = %s
                            WHERE run_id = %s;
                            """ ,
                            [json.dumps(err_payload), run_id],
                        )
                    await w_conn.commit()
                    await record_event("run_finished", {"error": err_payload})

                except Exception as e:
                    err_payload = {"code": "INTERNAL_ERROR", "message": str(e)}
                    async with w_conn.cursor() as cur:
                        await cur.execute(
                            """
                            UPDATE runs
                            SET state = 'failed', finished_at = NOW(), error = %s
                            WHERE run_id = %s;
                            """ ,
                            [json.dumps(err_payload), run_id],
                        )
                    await w_conn.commit()
                    await record_event("run_finished", {"error": err_payload})

                finally:
                    _ACTIVE_RUNS.pop(run_id, None)
                    await w_conn.close()

        asyncio.create_task(_bg_run())
        return {"run_id": run_id, "state": "running"}

    @app.get("/api/v1/runs/{run_id}")
    async def get_run(
        run_id: uuid.UUID,
        token: str = Depends(verify_owner_token),
    ):
        settings = resolve_settings()
        conn = await get_writer_connection(settings)
        try:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT run_id, case_id, state, result, error, started_at, finished_at
                    FROM runs
                    WHERE run_id = %s;
                    """,
                    [run_id],
                )
                row = await cur.fetchone()
                if not row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "RUN_NOT_FOUND", "message": f"Run {run_id} not found"},
                    )
                return {
                    "run_id": row["run_id"],
                    "case_id": row["case_id"],
                    "state": row["state"],
                    "result": row["result"],
                    "error": row["error"],
                    "started_at": row["started_at"].isoformat() if row["started_at"] else None,
                    "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
                }
        finally:
            await conn.close()

    @app.get("/api/v1/runs/{run_id}/events")
    async def get_run_events(
        run_id: uuid.UUID,
        after: int = Query(0, ge=0),
        token: str = Depends(verify_owner_token),
    ):
        settings = resolve_settings()
        conn = await get_writer_connection(settings)
        try:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT state FROM runs WHERE run_id = %s;", [run_id])
                run_row = await cur.fetchone()
                if not run_row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "RUN_NOT_FOUND", "message": f"Run {run_id} not found"},
                    )

                terminal = run_row["state"] in ("completed", "failed", "cancelled")

                await cur.execute(
                    """
                    SELECT run_id, sequence, timestamp, kind, elapsed_ms, payload
                    FROM run_events
                    WHERE run_id = %s AND sequence > %s
                    ORDER BY sequence ASC
                    LIMIT 100;
                    """,
                    [run_id, after],
                )
                rows = await cur.fetchall()

                events = [
                    {
                        "run_id": str(r["run_id"]),
                        "sequence": r["sequence"],
                        "timestamp": r["timestamp"].isoformat(),
                        "kind": r["kind"],
                        "elapsed_ms": r["elapsed_ms"],
                        "payload": r["payload"],
                    }
                    for r in rows
                ]
                next_after = rows[-1]["sequence"] if rows else after
                return {
                    "events": events,
                    "next_after": next_after,
                    "terminal": terminal,
                }
        finally:
            await conn.close()

    @app.post("/api/v1/runs/{run_id}/cancel")
    async def cancel_run(
        run_id: uuid.UUID,
        response: Response,
        token: str = Depends(verify_owner_token),
    ):
        settings = resolve_settings()
        conn = await get_writer_connection(settings)
        try:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT state, cancel_requested FROM runs WHERE run_id = %s;", [run_id])
                run_row = await cur.fetchone()
                if not run_row:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={"code": "RUN_NOT_FOUND", "message": f"Run {run_id} not found"},
                    )

                curr_state = run_row["state"]
                if curr_state in ("completed", "failed", "cancelled"):
                    response.status_code = status.HTTP_200_OK
                    return {"run_id": run_id, "state": curr_state}

                await cur.execute(
                    "UPDATE runs SET cancel_requested = TRUE WHERE run_id = %s;",
                    [run_id],
                )
            await conn.commit()

            if run_id in _ACTIVE_RUNS:
                _ACTIVE_RUNS[run_id].set()

            response.status_code = status.HTTP_202_ACCEPTED
            return {"run_id": run_id, "state": "cancelling"}
        finally:
            await conn.close()

    frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if frontend_dist.exists() and (frontend_dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

    return app


# Default ASGI application instance for uvicorn
app = create_app()
