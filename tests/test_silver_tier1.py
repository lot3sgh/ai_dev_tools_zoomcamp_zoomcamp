"""Tickets 01–06 — Silver expansion (Tier 1): append-mode families + stress/spo2/hrv/temperature/activity.

Same seam as test_silver.py: run the CLI against a takeout fixture, assert on the
resulting database. All rerun assertions double as idempotency checks (the
operational contract from ADR-0003).
"""

from __future__ import annotations

from tests.conftest import make_zip


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