"""Shared fixtures: synthetic takeout zip + throwaway Postgres wiring.

Tests run the pipeline through its single seam (the CLI) and assert on the
resulting database. Each test starts with the bronze/silver/pipeline schemas
dropped, so runs are isolated.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

import psycopg
import pytest

from pipeline import config

# Same env resolution as the CLI itself (cli.main calls config.load_env):
# a .env at the repo root supplies POSTGRES_DB/USER/PASSWORD, PGHOST, PGPORT.
config.load_env()

# The read-only analytics login (Grafana/chatbot); ensure_schemas provisions it.
os.environ.setdefault("DASHBOARD_DB_PASSWORD", "test_dashboard_pw")

# The golden synthetic takeout lives in one place (also used by the eval corpus gate).
from assistant.fixtures import (  # noqa: E402  (re-exported for existing test imports)
    ACTIVITY_CSV,
    AZM_AUG,
    AZM_SEPT,
    DEVICES_CSV,
    GLUCOSE_CSV,
    HRV_CSV,
    PROFILE_CSV,
    SLEEP_CSV,
    SPO2_CSV,
    STRESS_CSV,
    TEMP_CSV,
    build_fixture_zip,
    make_zip,
)

TEST_DATABASE = "health_pipeline_test"


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    """Point the suite at a dedicated database so real data is never touched.

    Creates health_pipeline_test once per session (superuser pipeline can
    create databases), then every test DROPs/recreates schemas inside it.
    """
    os.environ["PGDATABASE"] = TEST_DATABASE
    maintenance = psycopg.conninfo.make_conninfo(**{
        **{k: config.db_env()[k] for k in ("host", "port", "user", "password")},
        "dbname": "postgres",
    })
    with psycopg.connect(maintenance, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE,))
            exists = cur.fetchone() is not None
        if not exists:
            conn.execute(f"CREATE DATABASE {TEST_DATABASE}")
    yield


@pytest.fixture()
def source_dir(tmp_path: Path) -> Path:
    """A directory containing a synthetic takeout zip."""
    build_fixture_zip(tmp_path)
    return tmp_path


@pytest.fixture()
def reset_db() -> None:
    """Drop the pipeline schemas so each test observes a fresh run."""
    from pipeline import db

    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS bronze CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS silver CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS pipeline CASCADE")
            cur.execute("DROP SCHEMA IF EXISTS gold CASCADE")
    yield