"""Tickets 01–06 — Silver expansion (Tier 1): append-mode families + stress/spo2/hrv/temperature/activity.

Same seam as test_silver.py: run the CLI against a takeout fixture, assert on the
resulting database. All rerun assertions double as idempotency checks (the
operational contract from ADR-0003).
"""

from __future__ import annotations

from tests.conftest import HRV_CSV, SPO2_CSV, STRESS_CSV, make_zip
import pytest

pytestmark = pytest.mark.integration  # needs the throwaway Postgres DB



def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


def _q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_hrv_append_mode_rerun_is_stable_and_bad_row_rejected(reset_db, source_dir):
    """Append-mode family: duplicate timestamps survive, reruns don't duplicate,
    unparseable rows route to the audit table (ticket 01 + 04)."""
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT timestamp, rmssd, low_frequency, high_frequency "
                        "FROM silver.hrv ORDER BY rmssd")
        assert len(rows) == 2          # BADROW not typed
        # both rows of the duplicate timestamp pair are kept, not silently merged
        assert _q(conn, "SELECT count(*) FROM silver.hrv WHERE timestamp = "
                        "'2026-09-20T03:14:30+00:00'")[0][0] == 2
        assert float(rows[0][1]) == 51.7
        assert float(rows[1][2]) == 681.0
        rej = _q(conn, "SELECT family, reason FROM silver.rejected_rows "
                        "WHERE family = 'heart_rate_variability'")
        assert len(rej) == 1
        assert "not a number" in rej[0][1]
    # rerun changes nothing
    assert _sync(source_dir) == 0
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM silver.hrv")[0][0] == 2
        assert _q(conn, "SELECT count(*) FROM silver.rejected_rows "
                        "WHERE family = 'heart_rate_variability'")[0][0] == 1


def test_stress_keyed_by_date_upserts_and_bad_row_rejected(reset_db, source_dir):
    """Keyed daily stress: one typed row per date; changed value updates in place;
    unparseable rows route to the audit table (ticket 02)."""
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT date, stress_score, sleep_points, responsiveness_points, "
                        "exertion_points, status FROM silver.stress ORDER BY date")
        assert len(rows) == 2          # BADROW not typed
        assert str(rows[0][0]) == "2026-09-19"
        assert rows[0][1] == 32
        assert rows[0][4] == 55
        assert rows[1][5] == "restful"
        rej = _q(conn, "SELECT reason FROM silver.rejected_rows WHERE family = 'stress_score'")
        assert len(rej) == 1 and "not an integer" in rej[0][0]
    # changed value on the same natural key -> updated, not duplicated
    make_zip(
        source_dir / "takeout-test.zip",
        {"Takeout/Google Health/Stress Score/stress.csv": STRESS_CSV.replace(
            "2026-09-20,67", "2026-09-20,71")},
    )
    assert _sync(source_dir) == 0
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM silver.stress")[0][0] == 2
        assert _q(conn, "SELECT stress_score FROM silver.stress WHERE date = '2026-09-20'")[0][0] == 71
        assert _q(conn, "SELECT count(*) FROM silver.rejected_rows WHERE family = 'stress_score'")[0][0] == 1


def test_spo2_value_precedence_and_rerun_stable(reset_db, source_dir):
    """Typed SpO2 by timestamp; value is primary, average_value falls back;
    reruns don't duplicate (ticket 03)."""
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT timestamp, value, average_value, lower_bound, upper_bound "
                        "FROM silver.spo2 ORDER BY timestamp")
        assert len(rows) == 2          # BADROW not typed
        # earliest row has only average_value -> fallback into value
        assert float(rows[0][1]) == 94.1
        assert float(rows[0][4]) == 95.5
        # later row has only value populated
        assert float(rows[1][1]) == 95.2
        assert rows[1][2] is None
        rej = _q(conn, "SELECT reason FROM silver.rejected_rows WHERE family = 'oxygen_saturation_spo2'")
        assert len(rej) == 1 and "not a number" in rej[0][0]
    assert _sync(source_dir) == 0                     # rerun: no duplicates
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM silver.spo2")[0][0] == 2


def test_temperature_maps_night_rows_and_skips_samples(reset_db, source_dir):
    """Night-summary rows are typed (append mode); per-sample rows stay in bronze
    and are skipped silently, never rejected (ticket 05)."""
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT type, sleep_start, temperature_samples, nightly_temperature "
                        "FROM silver.temperature ORDER BY sleep_start")
        assert len(rows) == 2          # sample row + bad nightly row not mapped
        assert rows[0][0] == "IDT"
        assert float(rows[0][3]) == 28.44326710816777
        assert rows[1][2] == 547
        # the per-sample row is skipped silently; the bad nightly row is rejected
        rej = _q(conn, "SELECT reason FROM silver.rejected_rows WHERE family = 'temperature'")
        assert [r[0] for r in rej] == ["not a number: 'not-a-number'"]
    assert _sync(source_dir) == 0                     # rerun: no duplicates
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM silver.temperature")[0][0] == 2


def test_activity_append_mode_keeps_duplicate_pairs(reset_db, source_dir):
    """Intraday activity in append mode: duplicate (timestamp, data_source) pairs
    are legitimate and kept, reruns don't duplicate, bad rows rejected
    (ticket 06)."""
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT timestamp, steps, beats_per_minute, distance, data_source "
                        "FROM silver.activity ORDER BY timestamp, data_source")
        assert len(rows) == 3          # BADROW not typed
        # the deliberate duplicate pair survives append mode (a key would collapse it)
        assert _q(conn, "SELECT count(*) FROM silver.activity WHERE timestamp = "
                        "'2026-09-20T00:05:00+00:00'")[0][0] == 2
        assert float(rows[0][3]) == 0.1   # earliest row: 23:59, Charge 4
        assert rows[0][2] == 65
        assert rows[2][2] == 72           # duplicate pair rows carry bpm 72
        rej = _q(conn, "SELECT reason FROM silver.rejected_rows WHERE family = "
                        "'physical_activity_googledata'")
        assert len(rej) == 1 and "not an integer" in rej[0][0]
    assert _sync(source_dir) == 0                     # rerun: no duplicates
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM silver.activity")[0][0] == 3