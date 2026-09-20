"""Ticket 02 — Bronze ingestion: family tables, provenance, monthly merge, drift, replacement."""

from __future__ import annotations

from tests.conftest import AZM_AUG, AZM_SEPT, make_zip


def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


def _q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_bronze_families_and_provenance(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM bronze.sleep_score")[0][0] == 4
        assert _q(conn, "SELECT count(*) FROM bronze.active_zone_minutes_azm")[0][0] == 5
        assert _q(conn, "SELECT count(*) FROM bronze.paired_devices")[0][0] == 2
        assert _q(conn, "SELECT count(*) FROM bronze.your_profile")[0][0] == 1
        assert _q(conn, "SELECT count(*) FROM bronze.biometrics")[0][0] == 1
        # provenance is populated
        row = _q(conn, "SELECT _takeout, _source_file, _loaded_at FROM bronze.sleep_score LIMIT 1")[0]
        assert row[0] == "takeout-test.zip"
        assert row[1] == "Takeout/Google Health/Sleep Score/sleep_score.csv"
        assert row[2] is not None
        # monthly files merged into one table, each traceable by source file
        per_file = _q(
            conn,
            "SELECT _source_file, count(*) FROM bronze.active_zone_minutes_azm GROUP BY 1 ORDER BY 1",
        )
        assert [r[1] for r in per_file] == [2, 3]


def test_family_merges_monthly_files(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(
            conn, "SELECT date_time, heart_zone_id FROM bronze.active_zone_minutes_azm ORDER BY date_time"
        )
        assert len(rows) == 5  # 3 from September + 2 from August
        zones = {r[1] for r in rows}
        assert zones == {"FAT_BURN", "CARDIO", "PEAK"}


def test_schema_drift_widens_family_table(reset_db, tmp_path):
    """Two files in one family with different columns -> union of columns, rows intact."""
    from pipeline import db

    target = tmp_path / "drift"
    target.mkdir()
    make_zip(
        target / "drift.zip",
        {
            "Takeout/App Metrics/a.csv": "a,b\n1,2\n",
            "Takeout/App Metrics/b.csv": "a,c\n3,4\n",
        },
    )
    assert _sync(target) == 0
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM bronze.app_metrics")[0][0] == 2
        cols = {r[0] for r in _q(conn, "SELECT column_name FROM information_schema.columns "
                                       "WHERE table_schema='bronze' AND table_name='app_metrics'")}
        assert {"a", "b", "c"} <= cols


def test_reingesting_changed_takeout_replaces_rows(reset_db, source_dir):
    """Content change (md5) triggers re-ingest; delete-then-insert leaves no duplicates."""
    from pipeline import db
    from tests.conftest import build_fixture_zip

    assert _sync(source_dir) == 0
    # one AZM row removed -> re-sync should reflect 5 rows, not 9
    shorter = {"Takeout/Google Health/Active Zone Minutes (AZM)/Active Zone Minutes - 2026-09-01.csv": _drop_last(AZM_SEPT)}
    make_zip(source_dir / "takeout-test.zip", shorter)
    assert _sync(source_dir) == 0
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM bronze.active_zone_minutes_azm")[0][0] == 4


def _drop_last(csv_text: str) -> str:
    lines = csv_text.rstrip().splitlines()
    return "\n".join(lines[:-1]) + "\n"