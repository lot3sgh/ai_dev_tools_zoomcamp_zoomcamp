"""Silver build: type bronze rows into typed, keyed tables; rejects go to rejected_rows."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import psycopg
from psycopg import sql

# bronze family slug -> silver table name (silver keeps domain-entity names)
CURATED_FAMILIES: dict[str, str] = {
    "sleep_score": "sleep_score",
    "active_zone_minutes_azm": "active_zone_minutes",
    "paired_devices": "device",
    "your_profile": "profile",
    "heart_rate_variability": "hrv",
    "stress_score": "stress",
}


class _Rejected(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _Skip(Exception):
    """Row is valid data we deliberately do not map (stays bronze-only)."""


def _t(v: Any) -> str | None:
    return None if v is None or v == "" else str(v)


def _i(v: Any) -> int | None:
    s = _t(v)
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        raise _Rejected(f"not an integer: {s!r}")


def _f(v: Any) -> Decimal | None:
    s = _t(v)
    if s is None:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        raise _Rejected(f"not a number: {s!r}")


def _b(v: Any) -> bool | None:
    s = _t(v)
    if s is None:
        return None
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    raise _Rejected(f"not a boolean: {s!r}")


def _dt(v: Any) -> datetime | None:
    s = _t(v)
    if s is None:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise _Rejected(f"not a datetime: {s!r}")


def _d(v: Any) -> date | None:
    s = _t(v)
    if s is None:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise _Rejected(f"not a date: {s!r}")


Builder = Callable[[dict, str, str], tuple[tuple, tuple]]


def _sleep(row: dict, takeout: str, source_file: str) -> tuple[tuple, tuple]:
    values = (
        _i(row.get("sleep_log_entry_id")),
        _dt(row.get("timestamp")),
        _i(row.get("overall_score")),
        _i(row.get("composition_score")),
        _i(row.get("revitalization_score")),
        _i(row.get("duration_score")),
        _i(row.get("deep_sleep_in_minutes")),
        _i(row.get("resting_heart_rate")),
        _f(row.get("restlessness")),
        takeout,
        source_file,
    )
    if values[0] is None:
        raise _Rejected("missing sleep_log_entry_id")
    return values, (values[0],)


def _azm(row: dict, takeout: str, source_file: str) -> tuple[tuple, tuple]:
    dt = _dt(row.get("date_time"))
    zone = _t(row.get("heart_zone_id"))
    if dt is None or zone is None:
        raise _Rejected("missing date_time or heart_zone_id")
    return (dt, zone, _i(row.get("total_minutes")), takeout, source_file), (dt, zone)


def _device(row: dict, takeout: str, source_file: str) -> tuple[tuple, tuple]:
    wire_id = _t(row.get("wire_id"))
    if wire_id is None:
        raise _Rejected("missing wire_id")
    values = (
        wire_id,
        _t(row.get("device_type")),
        _t(row.get("serial_number")),
        _b(row.get("enabled")),
        _t(row.get("fw_version")),
        takeout,
        source_file,
    )
    return values, (wire_id,)


def _profile(row: dict, takeout: str, source_file: str) -> tuple[tuple, tuple]:
    pid = _t(row.get("id"))
    if pid is None:
        raise _Rejected("missing profile id")
    values = (
        pid,
        _t(row.get("full_name")),
        _t(row.get("first_name")),
        _t(row.get("last_name")),
        _t(row.get("username")),
        _t(row.get("email_address")),
        _d(row.get("date_of_birth")),
        _b(row.get("child")),
        _t(row.get("country")),
        _t(row.get("state")),
        _t(row.get("city")),
        _t(row.get("timezone")),
        _t(row.get("locale")),
        _d(row.get("member_since")),
        _t(row.get("start_of_week")),
        _t(row.get("sleep_tracking")),
        _t(row.get("time_display_format")),
        _t(row.get("gender")),
        _f(row.get("height")),
        _f(row.get("weight")),
        _t(row.get("weight_unit")),
        _t(row.get("distance_unit")),
        _t(row.get("height_unit")),
        takeout,
        source_file,
    )
    return values, (pid,)


def _hrv(row: dict, takeout: str, source_file: str) -> tuple[tuple, tuple]:
    ts = _dt(row.get("timestamp"))
    if ts is None:
        raise _Rejected("missing timestamp")
    values = (
        ts,
        _f(row.get("rmssd")),
        _f(row.get("coverage")),
        _f(row.get("low_frequency")),
        _f(row.get("high_frequency")),
        _f(row.get("full_sleep_breathing_rate")),
        _f(row.get("full_sleep_standard_deviation")),
        _f(row.get("full_sleep_signal_to_noise")),
        takeout,
        source_file,
    )
    return values, ()  # append mode: no natural key (timestamps collide across files)


def _stress(row: dict, takeout: str, source_file: str) -> tuple[tuple, tuple]:
    day = _d(row.get("date"))
    if day is None:
        raise _Rejected("missing date")
    values = (
        day,
        _i(row.get("stress_score")),
        _i(row.get("sleep_points")),
        _i(row.get("responsiveness_points")),
        _i(row.get("exertion_points")),
        _t(row.get("status")),
        takeout,
        source_file,
    )
    return values, (day,)


# silver table -> (key columns, row builder); key columns None = append mode
_BUILDERS: dict[str, tuple[list[str] | None, Callable]] = {
    "sleep_score": (["sleep_log_entry_id"], _sleep),
    "active_zone_minutes": (["date_time", "heart_zone_id"], _azm),
    "device": (["wire_id"], _device),
    "profile": (["id"], _profile),
    "hrv": (None, _hrv),
    "stress": (["date"], _stress),
}


def _bronze_columns(conn: psycopg.Connection, family: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'bronze' AND table_name = %s ORDER BY ordinal_position",
            (family,),
        )
        return [r[0] for r in cur.fetchall() if not r[0].startswith("_")]


def _silver_columns(conn: psycopg.Connection, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'silver' AND table_name = %s ORDER BY ordinal_position",
            (table,),
        )
        return [r[0] for r in cur.fetchall()]


def build_family(
    conn: psycopg.Connection, takeout: str, source_file: str, table: str, family: str
) -> dict[str, int]:
    """Type one bronze family's rows (for one source file) into a silver table."""
    key_cols, builder = _BUILDERS[table]
    stats = {"written": 0, "rejected": 0}
    cols = _bronze_columns(conn, family)

    with conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM silver.{table} WHERE _source_file = %s", (source_file,)
        )  # scoped cleanup so re-runs don't leave stale rows
        cur.execute(
            sql.SQL("SELECT {} FROM bronze.{} WHERE _source_file = %s").format(
                sql.SQL(", ").join(sql.Identifier(c) for c in cols),
                sql.Identifier(family),
            ),
            (source_file,),
        )
        bronze_rows = cur.fetchall()

    col_index = {c: i for i, c in enumerate(cols)}
    silver_cols = _silver_columns(conn, table)
    prepared: list[tuple] = []

    with conn.cursor() as cur:
        for raw in bronze_rows:
            row = {c: raw[col_index[c]] for c in cols}
            try:
                values, _keys = builder(row, takeout, source_file)
                prepared.append(values)
            except _Rejected as exc:
                cur.execute(
                    sql.SQL(
                        "INSERT INTO silver.rejected_rows"
                        " (family, source_file, takeout, row_text, reason)"
                        " SELECT %s, %s, %s, %s, %s"
                        " WHERE NOT EXISTS ("
                        "   SELECT 1 FROM silver.rejected_rows"
                        "   WHERE family = %s AND source_file = %s"
                        "     AND row_text = %s AND reason = %s)"
                    ),
                    (
                        family,
                        source_file,
                        takeout,
                        ",".join(str(v) for v in row.values()),
                        exc.reason,
                        family,
                        source_file,
                        ",".join(str(v) for v in row.values()),
                        exc.reason,
                    ),
                )
                stats["rejected"] += 1

        if prepared:
            if key_cols:
                # keyed families: upsert on the natural key so changed values update in place
                update_cols = [c for c in silver_cols if c not in key_cols]
                insert_stmt = sql.SQL(
                    "INSERT INTO silver.{table} ({cols}) VALUES ({placeholders})"
                    " ON CONFLICT ({keys}) DO UPDATE SET {updates}"
                ).format(
                    table=sql.Identifier(table),
                    cols=sql.SQL(", ").join(sql.Identifier(c) for c in silver_cols),
                    placeholders=sql.SQL(", ").join(
                        sql.Placeholder() for _ in silver_cols
                    ),
                    keys=sql.SQL(", ").join(sql.Identifier(c) for c in key_cols),
                    updates=sql.SQL(", ").join(
                        sql.SQL("{} = EXCLUDED.{}").format(
                            sql.Identifier(c), sql.Identifier(c)
                        )
                        for c in update_cols
                    ),
                )
            else:
                # append families have no defensible natural key; the delete-by-source-file
                # above keeps reruns idempotent without inventing a key that would
                # silently collapse legitimate duplicate rows
                insert_stmt = sql.SQL(
                    "INSERT INTO silver.{table} ({cols}) VALUES ({placeholders})"
                ).format(
                    table=sql.Identifier(table),
                    cols=sql.SQL(", ").join(sql.Identifier(c) for c in silver_cols),
                    placeholders=sql.SQL(", ").join(
                        sql.Placeholder() for _ in silver_cols
                    ),
                )
            cur.executemany(insert_stmt, prepared)
            stats["written"] += len(prepared)
    conn.commit()
    return stats


def build(
    conn: psycopg.Connection, takeout: str, source_file: str, family: str
) -> dict[str, int]:
    """Build silver for a curated family; no-op for bronze-only families."""
    table = CURATED_FAMILIES.get(family)
    if table is None:
        return {"written": 0, "rejected": 0}
    return build_family(conn, takeout, source_file, table, family)