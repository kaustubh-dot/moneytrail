"""Test configuration, isolated database schemas, and shared fixtures."""

import os
import re
import uuid
import pytest
import psycopg
from psycopg import sql

from moneytrail.config import Settings, SetupSettings


@pytest.fixture(scope="session")
def setup_settings() -> SetupSettings:
    """Load setup-only admin settings for test schema creation."""
    return SetupSettings.from_env()


@pytest.fixture(scope="session")
def base_settings() -> Settings:
    """Load base runtime settings."""
    return Settings.from_env()


@pytest.fixture
def test_schema_name() -> str:
    """Generate a validated isolated schema name for testing."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    # Validate strictly
    if not re.match(r"^test_[a-f0-9]{12}$", name):
        raise ValueError(f"Invalid test schema name: {name}")
    return name


@pytest.fixture
def test_db_schema(setup_settings: SetupSettings, test_schema_name: str):
    """Create an isolated test schema with tables and permissions, clean up after test."""
    admin_dsn = setup_settings.admin_database_url
    # Validate schema name again before executing any DDL
    if not re.match(r"^test_[a-f0-9]{12}$", test_schema_name):
        raise ValueError(f"Unvalidated schema identifier rejected: {test_schema_name}")

    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {};").format(sql.Identifier(test_schema_name)))
            # Apply initial SQL within this test schema
            cur.execute(sql.SQL("SET search_path TO {}, public;").format(sql.Identifier(test_schema_name)))
            sql_path = "sql/001_initial.sql"
            if os.path.exists(sql_path):
                with open(sql_path, "r", encoding="utf-8") as f:
                    cur.execute(f.read())
            # Grant scoped permissions
            cur.execute(
                sql.SQL("GRANT ALL ON ALL TABLES IN SCHEMA {} TO moneytrail_writer;").format(
                    sql.Identifier(test_schema_name)
                )
            )
            cur.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO moneytrail_reader;").format(
                    sql.Identifier(test_schema_name)
                )
            )

    yield test_schema_name

    # Cleanup: remove ONLY this exact test schema
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE;").format(sql.Identifier(test_schema_name)))
