"""Database access and idempotent DDL (schemas + fixed tables)."""

from __future__ import annotations

import psycopg
from psycopg import sql

from pipeline import config

CONNINFO_KWARGS = ("dbname", "host", "port", "user", "password")


def conninfo() -> str:
    return psycopg.conninfo.make_conninfo(**{k: config.db_env()[k] for k in CONNINFO_KWARGS})


def connect() -> psycopg.Connection:
    return psycopg.connect(conninfo(), autocommit=True)


DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS pipeline;

CREATE TABLE IF NOT EXISTS pipeline.processed_files (
    file_id        TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    modified_time  TIMESTAMPTZ,
    md5            TEXT,
    status         TEXT NOT NULL,          -- 'processed' | 'error'
    row_count      BIGINT,
    error          TEXT,
    processed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.sleep_score (
    sleep_log_entry_id     BIGINT PRIMARY KEY,
    timestamp              TIMESTAMPTZ,
    overall_score          INTEGER,
    composition_score      INTEGER,
    revitalization_score   INTEGER,
    duration_score         INTEGER,
    deep_sleep_in_minutes  INTEGER,
    resting_heart_rate     INTEGER,
    restlessness           NUMERIC,
    _takeout               TEXT,
    _source_file           TEXT
);

CREATE TABLE IF NOT EXISTS silver.active_zone_minutes (
    date_time      TIMESTAMPTZ NOT NULL,
    heart_zone_id  TEXT NOT NULL,
    total_minutes  INTEGER,
    _takeout       TEXT,
    _source_file   TEXT,
    PRIMARY KEY (date_time, heart_zone_id)
);

CREATE TABLE IF NOT EXISTS silver.device (
    wire_id       TEXT PRIMARY KEY,
    device_type   TEXT,
    serial_number TEXT,
    enabled       BOOLEAN,
    fw_version    TEXT,
    _takeout      TEXT,
    _source_file  TEXT
);

CREATE TABLE IF NOT EXISTS silver.profile (
    id                    TEXT PRIMARY KEY,
    full_name             TEXT,
    first_name            TEXT,
    last_name             TEXT,
    username              TEXT,
    email_address         TEXT,
    date_of_birth         DATE,
    child                 BOOLEAN,
    country               TEXT,
    state                 TEXT,
    city                  TEXT,
    timezone              TEXT,
    locale                TEXT,
    member_since          DATE,
    start_of_week         TEXT,
    sleep_tracking        TEXT,
    time_display_format   TEXT,
    gender                TEXT,
    height                NUMERIC,
    weight                NUMERIC,
    weight_unit           TEXT,
    distance_unit         TEXT,
    height_unit           TEXT,
    _takeout              TEXT,
    _source_file          TEXT
);

CREATE TABLE IF NOT EXISTS silver.rejected_rows (
    id           BIGSERIAL PRIMARY KEY,
    family       TEXT NOT NULL,
    source_file  TEXT NOT NULL,
    takeout      TEXT NOT NULL,
    row_text     TEXT NOT NULL,
    reason       TEXT NOT NULL,
    loaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS silver.hrv (
    timestamp                      TIMESTAMPTZ,
    rmssd                          NUMERIC,
    coverage                       NUMERIC,
    low_frequency                  NUMERIC,
    high_frequency                 NUMERIC,
    full_sleep_breathing_rate      NUMERIC,
    full_sleep_standard_deviation  NUMERIC,
    full_sleep_signal_to_noise     NUMERIC,
    _takeout                       TEXT,
    _source_file                   TEXT
);

CREATE TABLE IF NOT EXISTS silver.stress (
    date                    DATE PRIMARY KEY,
    stress_score            INTEGER,
    sleep_points            INTEGER,
    responsiveness_points   INTEGER,
    exertion_points         INTEGER,
    status                  TEXT,
    _takeout                TEXT,
    _source_file            TEXT
);

CREATE TABLE IF NOT EXISTS silver.spo2 (
    timestamp      TIMESTAMPTZ PRIMARY KEY,
    value          NUMERIC,
    average_value  NUMERIC,
    lower_bound    NUMERIC,
    upper_bound    NUMERIC,
    _takeout       TEXT,
    _source_file   TEXT
);
"""


def ensure_schemas() -> None:
    """Create schemas and fixed tables; safe to run on every invocation."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(DDL)
            # keep delete-then-insert cheap on pre-existing bronze tables too
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'bronze'"
            )
            for (family,) in cur.fetchall():
                cur.execute(
                    sql.SQL(
                        "CREATE INDEX IF NOT EXISTS {index} ON bronze.{} (_source_file)"
                    ).format(
                        sql.Identifier(family),
                        index=sql.Identifier(f"{family}_source_file_idx"),
                    )
                )
        conn.commit()