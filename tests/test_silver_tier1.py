"""Tickets 01–06 — Silver expansion (Tier 1): append-mode families + stress/spo2/hrv/temperature/activity.

Same seam as test_silver.py: run the CLI against a takeout fixture, assert on the
resulting database. All rerun assertions double as idempotency checks (the
operational contract from ADR-0003).
"""

from __future__ import annotations

from tests.conftest import HRV_CSV, STRESS_CSV, make_zip


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