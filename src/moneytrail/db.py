"""Database connection management, role setup, migrations, and scoped queries."""

import argparse
import hashlib
import os
from pathlib import Path
from urllib.parse import urlparse
from typing import AsyncGenerator

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from moneytrail.config import Settings, SetupSettings


def _parse_role_credentials(dsn: str) -> tuple[str, str]:
    """Extract username and password safely from a PostgreSQL DSN."""
    parsed = urlparse(dsn)
    if not parsed.username or not parsed.password:
        raise ValueError("DSN must include username and password")
    return parsed.username, parsed.password


def _compute_checksum(content: str) -> str:
    """Compute 64-character lowercase SHA256 checksum of SQL content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest().lower()


def bootstrap_database(admin_dsn: str, writer_dsn: str, reader_dsn: str) -> None:
    """Idempotently create roles, install vector extension, and grant schema permissions."""
    writer_user, writer_pw = _parse_role_credentials(writer_dsn)
    reader_user, reader_pw = _parse_role_credentials(reader_dsn)

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Install vector extension
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

            # Create or update moneytrail_writer
            cur.execute(
                sql.SQL("SELECT 1 FROM pg_roles WHERE rolname = %s"),
                [writer_user]
            )
            if cur.fetchone():
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE LOGIN;").format(
                        sql.Identifier(writer_user),
                        sql.Literal(writer_pw)
                    )
                )
            else:
                cur.execute(
                    sql.SQL("CREATE ROLE {} WITH PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE LOGIN;").format(
                        sql.Identifier(writer_user),
                        sql.Literal(writer_pw)
                    )
                )

            # Create or update moneytrail_reader
            cur.execute(
                sql.SQL("SELECT 1 FROM pg_roles WHERE rolname = %s"),
                [reader_user]
            )
            if cur.fetchone():
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE LOGIN;").format(
                        sql.Identifier(reader_user),
                        sql.Literal(reader_pw)
                    )
                )
            else:
                cur.execute(
                    sql.SQL("CREATE ROLE {} WITH PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE LOGIN;").format(
                        sql.Identifier(reader_user),
                        sql.Literal(reader_pw)
                    )
                )

            # Grant schema usage
            cur.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {};").format(
                    sql.Identifier(writer_user),
                    sql.Identifier(reader_user)
                )
            )

    print("Database bootstrap complete: roles and vector extension configured.")


def migrate_database(admin_dsn: str, writer_dsn: str, reader_dsn: str, sql_dir: Path = Path("sql")) -> int:
    """Apply unapplied numbered SQL migrations and update role table privileges."""
    writer_user, _ = _parse_role_credentials(writer_dsn)
    reader_user, _ = _parse_role_credentials(reader_dsn)

    applied_count = 0
    with psycopg.connect(admin_dsn) as conn:
        with conn.cursor() as cur:
            # Ensure schema_versions table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_versions (
                    version INTEGER PRIMARY KEY,
                    checksum CHAR(64) NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            # Query existing versions
            cur.execute("SELECT version, checksum FROM schema_versions ORDER BY version ASC;")
            existing_records = {row[0]: row[1].strip() for row in cur.fetchall()}

            # Scan SQL directory
            sql_files = sorted(sql_dir.glob("*.sql"))
            for sql_file in sql_files:
                name = sql_file.name
                version_str = name.split("_")[0]
                try:
                    ver = int(version_str)
                except ValueError:
                    continue

                content = sql_file.read_text(encoding="utf-8")
                checksum = _compute_checksum(content)

                if ver in existing_records:
                    if existing_records[ver] != checksum:
                        raise ValueError(
                            f"Checksum mismatch for migration {name}: "
                            f"applied {existing_records[ver]} vs disk {checksum}"
                        )
                else:
                    # Apply migration
                    print(f"Applying migration {name} (version {ver})...")
                    cur.execute(content)
                    cur.execute(
                        "INSERT INTO schema_versions (version, checksum) VALUES (%s, %s);",
                        [ver, checksum]
                    )
                    applied_count += 1

            # Update grants for reader and writer
            # Writer gets ALL on all tables and sequences
            cur.execute(
                sql.SQL("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO {};").format(
                    sql.Identifier(writer_user)
                )
            )
            cur.execute(
                sql.SQL("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO {};").format(
                    sql.Identifier(writer_user)
                )
            )

            # Reader gets SELECT ONLY on business/source/policy/chunk/finding tables
            read_tables = [
                "cases", "sources", "orders", "refunds", "fees",
                "payouts", "payout_links", "policies", "chunks", "case_findings"
            ]
            for tbl in read_tables:
                cur.execute(
                    sql.SQL("GRANT SELECT ON TABLE {} TO {};").format(
                        sql.Identifier(tbl),
                        sql.Identifier(reader_user)
                    )
                )

            # Revoke all privileges on runs, run_events, schema_versions from reader
            for private_tbl in ["runs", "run_events", "schema_versions"]:
                cur.execute(
                    sql.SQL("REVOKE ALL ON TABLE {} FROM {};").format(
                        sql.Identifier(private_tbl),
                        sql.Identifier(reader_user)
                    )
                )

        conn.commit()

    print(f"Migration complete: {applied_count} new migrations applied.")
    return applied_count


async def get_writer_connection(settings: Settings, schema: str | None = None) -> psycopg.AsyncConnection:
    """Return an async connection with moneytrail_writer privileges."""
    conn = await psycopg.AsyncConnection.connect(settings.database_url, autocommit=False)
    if schema:
        await conn.execute(sql.SQL("SET search_path TO {}, public;").format(sql.Identifier(schema)))
    return conn


async def get_reader_connection(settings: Settings, schema: str | None = None) -> psycopg.AsyncConnection:
    """Return an async connection authenticated as moneytrail_reader in read-only mode."""
    conn = await psycopg.AsyncConnection.connect(settings.read_database_url, autocommit=False)
    await conn.set_read_only(True)
    if schema:
        await conn.execute(sql.SQL("SET search_path TO {}, public;").format(sql.Identifier(schema)))
    return conn


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MoneyTrail Database Management")
    subparsers = parser.add_subparsers(dest="subcommand")

    boot_parser = subparsers.add_parser("bootstrap", help="Bootstrap roles and database extensions")
    boot_parser.add_argument("--admin-dsn-env", type=str, default="MONEYTRAIL_ADMIN_DATABASE_URL")

    mig_parser = subparsers.add_parser("migrate", help="Apply numbered SQL migrations")
    mig_parser.add_argument("--admin-dsn-env", type=str, default="MONEYTRAIL_ADMIN_DATABASE_URL")
    mig_parser.add_argument("--sql-dir", type=Path, default=Path("sql"))

    args = parser.parse_args()

    admin_dsn = os.getenv(args.admin_dsn_env, "")
    writer_dsn = os.getenv("MONEYTRAIL_DATABASE_URL", "")
    reader_dsn = os.getenv("MONEYTRAIL_READ_DATABASE_URL", "")

    if not admin_dsn:
        raise ValueError(f"Admin DSN environment variable {args.admin_dsn_env} is not set")
    if not writer_dsn or not reader_dsn:
        raise ValueError("MONEYTRAIL_DATABASE_URL and MONEYTRAIL_READ_DATABASE_URL must be set")

    if args.subcommand == "bootstrap":
        bootstrap_database(admin_dsn, writer_dsn, reader_dsn)
    elif args.subcommand == "migrate":
        migrate_database(admin_dsn, writer_dsn, reader_dsn, args.sql_dir)
    else:
        parser.print_help()
