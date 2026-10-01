"""Phase 0b — gold views over silver: the read contract (daily, nightly, weekly, freshness).

Same seam as the rest of the suite: run the CLI against the fixture Takeout, then
assert the gold views' output rows (types, grain, aggregates) from the fixture DB.
Expected values are derived from the fixture CSVs by hand (independent source of truth).
"""

from __future__ import annotations

from decimal import Decimal


def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


def _q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_daily_health_has_one_row_per_health_day(reset_db, source_dir):
    """A row for every UTC day that has any silver data, with typed aggregates."""
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT date, sleep_score, stress_score, steps, azm_minutes, "
                        "avg_hrv_rmssd, avg_spo2, nightly_temperature FROM gold.daily_health")
        by_day = {str(r[0]): r for r in rows}
        # every fixture health day is present (sleep/stress/spo2/hrv/activity/azm/temperature)
        assert set(by_day) == {"2026-08-02", "2026-09-15", "2026-09-18", "2026-09-19",
                               "2026-09-20", "2026-01-03", "2026-01-04"}
        # 08-02: only AZM that day (azm_minutes is index 4)
        assert by_day["2026-08-02"][4] == 5
        # 09-18: only sleep that day (bad stress/spo2/hrv rows were rejected)
        assert by_day["2026-09-18"][1] == 81
        # 09-19: stress + spo2 fallback + activity
        assert by_day["2026-09-19"][2] == 32
        assert float(by_day["2026-09-19"][6]) == 94.1
        # 09-20: the duplicate-pair activity sums to 28 steps; hrv pair averages to 51.9
        assert by_day["2026-09-20"][3] == 28
        assert by_day["2026-09-20"][5] == Decimal("51.9")
        assert float(by_day["2026-09-20"][6]) == 95.2
        # temperature nights land on their own days
        assert float(by_day["2026-01-03"][7]) == 28.44326710816777
        assert float(by_day["2026-01-04"][7]) == 28.3981718464351


def test_sleep_summary_per_night_with_rolling_avg(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT night, overall_score, rmssd, hrv_coverage, "
                        "nightly_temperature, rolling_7d_score FROM gold.sleep_summary "
                        "ORDER BY night")
        assert [str(r[0]) for r in rows] == ["2026-09-18", "2026-09-19", "2026-09-20"]
        # per-night latest score (09-18 has two entries; both score 81)
        assert [r[1] for r in rows] == [81, 86, 82]
        # rolling average at the first night is its own score; grows by one row each night
        assert float(rows[0][5]) == 81.0
        assert float(rows[2][5]) == 83.0  # (81 + 86 + 82) / 3
        # hrv joins the night it belongs to (only 09-20 has typed hrv rows)
        assert rows[1][2] is None
        assert rows[2][2] == Decimal("51.9")
        assert float(rows[2][3]) == 0.9015
        # temperature nights do not appear in sleep_summary (no sleep score those nights)


def test_activity_trends_per_iso_week(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT week_start, steps, azm_minutes, active_days "
                        "FROM gold.activity_trends ORDER BY week_start")
        assert [(str(r[0]), r[1], r[2], r[3]) for r in rows] == [
            ("2026-07-27", 0, 5, 1),   # 2026-08-02 AzM rows (Sunday -> Mon-start week)
            ("2026-09-14", 28, 3, 1),  # 09-15 AzM (3 min) + 09-19/09-20 activity rows
        ]


def test_freshness_mirrors_ledger_and_audit(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT takeout, status, row_count, rejected_rows FROM gold.freshness")
        assert len(rows) == 1
        assert rows[0][0] == "takeout-test.zip"
        assert rows[0][1] == "processed"
        assert rows[0][2] == 30          # all fixture rows landed in bronze
        assert rows[0][3] == 6           # one rejected row per curated family
    # rerun is a no-op for the view's inputs
    assert _sync(source_dir) == 0
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM gold.freshness")[0][0] == 1
        assert _q(conn, "SELECT rejected_rows FROM gold.freshness")[0][0] == 6