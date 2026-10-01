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
CREATE SCHEMA IF NOT EXISTS gold;

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

CREATE TABLE IF NOT EXISTS silver.temperature (
    type                                        TEXT,
    sleep_start                                 TIMESTAMPTZ,
    sleep_end                                   TIMESTAMPTZ,
    temperature_samples                         INTEGER,
    nightly_temperature                         NUMERIC,
    baseline_relative_sample_sum                NUMERIC,
    baseline_relative_nightly_standard_deviation  NUMERIC,
    baseline_relative_sample_standard_deviation NUMERIC,
    _takeout                                    TEXT,
    _source_file                                TEXT
);

CREATE TABLE IF NOT EXISTS silver.activity (
    timestamp         TIMESTAMPTZ,
    steps             INTEGER,
    beats_per_minute  INTEGER,
    distance          NUMERIC,
    data_source       TEXT,
    _takeout          TEXT,
    _source_file      TEXT
);

-- Gold: live read contract over silver (Phase 0b). UTC-day grain; night metrics
-- (sleep/hrv/temperature) key to the date of the night they belong to.

CREATE OR REPLACE VIEW gold.daily_health AS
WITH days AS (
    SELECT timestamp::date AS day FROM silver.activity
    UNION SELECT date FROM silver.stress
    UNION SELECT date_time::date FROM silver.active_zone_minutes
    UNION SELECT timestamp::date FROM silver.sleep_score
    UNION SELECT timestamp::date FROM silver.spo2
    UNION SELECT timestamp::date FROM silver.hrv
    UNION SELECT sleep_start::date FROM silver.temperature
),
sleep_day AS (SELECT timestamp::date AS day, max(overall_score) AS sleep_score FROM silver.sleep_score GROUP BY 1),
stress_day AS (SELECT date AS day, max(stress_score) AS stress_score FROM silver.stress GROUP BY 1),
steps_day AS (SELECT timestamp::date AS day, sum(steps) AS steps FROM silver.activity GROUP BY 1),
azm_day AS (SELECT date_time::date AS day, sum(total_minutes) AS azm_minutes FROM silver.active_zone_minutes GROUP BY 1),
hrv_day AS (SELECT timestamp::date AS day, avg(rmssd) AS avg_hrv_rmssd FROM silver.hrv GROUP BY 1),
spo2_day AS (SELECT timestamp::date AS day, avg(value) AS avg_spo2 FROM silver.spo2 WHERE value IS NOT NULL GROUP BY 1),
temp_day AS (SELECT sleep_start::date AS day, max(nightly_temperature) AS nightly_temperature FROM silver.temperature GROUP BY 1)
SELECT d.day AS date,
       sl.sleep_score, st.stress_score, pd.steps, az.azm_minutes,
       hr.avg_hrv_rmssd, sp.avg_spo2, tp.nightly_temperature
FROM days d
LEFT JOIN sleep_day sl ON sl.day = d.day
LEFT JOIN stress_day st ON st.day = d.day
LEFT JOIN steps_day pd ON pd.day = d.day
LEFT JOIN azm_day az ON az.day = d.day
LEFT JOIN hrv_day hr ON hr.day = d.day
LEFT JOIN spo2_day sp ON sp.day = d.day
LEFT JOIN temp_day tp ON tp.day = d.day;

CREATE OR REPLACE VIEW gold.sleep_summary AS
WITH nights AS (
    SELECT DISTINCT ON (timestamp::date)
           timestamp::date AS night,
           overall_score,
           deep_sleep_in_minutes,
           resting_heart_rate,
           restlessness
    FROM silver.sleep_score
    ORDER BY timestamp::date, timestamp DESC
),
hrv_night AS (SELECT timestamp::date AS night, avg(rmssd) AS rmssd, avg(coverage) AS coverage
               FROM silver.hrv GROUP BY 1),
temp_night AS (SELECT sleep_start::date AS night,
               max(nightly_temperature) AS nightly_temperature,
               max(baseline_relative_nightly_standard_deviation) AS temperature_deviation
               FROM silver.temperature GROUP BY 1)
SELECT n.night,
       n.overall_score,
       n.deep_sleep_in_minutes,
       n.resting_heart_rate,
       n.restlessness,
       h.rmssd,
       h.coverage AS hrv_coverage,
       t.nightly_temperature,
       t.temperature_deviation,
       AVG(n.overall_score) OVER (ORDER BY n.night ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS rolling_7d_score
FROM nights n
LEFT JOIN hrv_night h ON h.night = n.night
LEFT JOIN temp_night t ON t.night = n.night;

CREATE OR REPLACE VIEW gold.activity_trends AS
WITH steps AS (
    SELECT date_trunc('week', timestamp)::date AS week_start, sum(steps) AS steps
    FROM silver.activity GROUP BY 1
),
azm AS (
    SELECT date_trunc('week', date_time)::date AS week_start,
           sum(total_minutes) AS azm_minutes,
           count(DISTINCT date_time::date) AS active_days
    FROM silver.active_zone_minutes GROUP BY 1
)
SELECT COALESCE(s.week_start, a.week_start) AS week_start,
       COALESCE(s.steps, 0) AS steps,
       COALESCE(a.azm_minutes, 0) AS azm_minutes,
       COALESCE(a.active_days, 0) AS active_days
FROM steps s
FULL OUTER JOIN azm a ON s.week_start = a.week_start;

CREATE OR REPLACE VIEW gold.freshness AS
SELECT p.name AS takeout,
       p.md5,
       p.status,
       p.row_count,
       p.processed_at,
       COALESCE(r.rejected, 0) AS rejected_rows
FROM pipeline.processed_files p
LEFT JOIN (SELECT takeout, count(*) AS rejected FROM silver.rejected_rows GROUP BY 1) r
       ON r.takeout = p.name;
"""


def provision_dashboard_role(conn: psycopg.Connection, password: str) -> None:
    """Idempotently create the read-only analytics role (SELECT on silver/gold).

    Password comes from the environment and is inlined as a SQL literal so it
    can never be interpreted as SQL.
    """
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "DO $do$ BEGIN\n"
                "  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dashboard') THEN\n"
                "    CREATE ROLE dashboard LOGIN;\n"
                "  END IF;\n"
                "END $do$;\n"
                "ALTER ROLE dashboard PASSWORD {pw};\n"
                "GRANT USAGE ON SCHEMA silver, gold TO dashboard;\n"
                "GRANT SELECT ON ALL TABLES IN SCHEMA silver, gold TO dashboard;"
            ).format(pw=sql.Literal(password))
        )


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
        password = config.dashboard_password()
        if password:
            provision_dashboard_role(conn, password)
        conn.commit()