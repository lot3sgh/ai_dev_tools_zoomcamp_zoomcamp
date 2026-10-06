"""Ticket 05 — Silver build: typed keyed tables, rejected rows, upserts, exit codes."""

from __future__ import annotations

from tests.conftest import DEVICES_CSV, make_zip
import pytest

pytestmark = pytest.mark.integration  # needs the throwaway Postgres DB



def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


def _q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_sleep_typed_and_bad_row_rejected(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT sleep_log_entry_id, overall_score, composition_score, resting_heart_rate, "
                        "restlessness, timestamp FROM silver.sleep_score ORDER BY sleep_log_entry_id")
        assert len(rows) == 3  # BADROW not typed
        good = rows[0]
        assert good[0] == 53603187472
        assert good[1] == 81
        assert good[2] is None  # empty -> NULL
        assert good[3] == 75
        assert rows[2][1] == 82
        # BADROW lands in rejected_rows with a reason
        rej = _q(conn, "SELECT family, reason, source_file FROM silver.rejected_rows "
                        "WHERE family = 'sleep_score'")
        assert len(rej) == 1
        assert rej[0][0] == "sleep_score"
        assert "not a number" in rej[0][1]
        assert rej[0][2].endswith("sleep_score.csv")
        # timestamps stored as timestamptz (UTC)
        ts = _q(conn, "SELECT timestamp FROM silver.sleep_score WHERE sleep_log_entry_id=53603187472")[0][0]
        assert ts.tzinfo is not None


def test_azm_typed(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT count(*), count(DISTINCT heart_zone_id) FROM silver.active_zone_minutes")
        assert rows[0][0] == 5
        assert rows[0][1] == 3
        minutes = _q(conn, "SELECT total_minutes FROM silver.active_zone_minutes "
                           "WHERE heart_zone_id='CARDIO'")[0][0]
        assert minutes == 2


def test_device_typed(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        devices = _q(conn, "SELECT wire_id, device_type, enabled FROM silver.device ORDER BY wire_id")
        assert devices[0] == ("0B2680AA5243", "Fitbit Air", True)
        assert devices[1] == ("223ee3c51f33", "MobileTrack", False)


def test_profile_typed(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        p = _q(conn, "SELECT id, date_of_birth, child, gender, height, weight FROM silver.profile")[0]
        assert p[0] == "C698DD"
        assert str(p[1]) == "1990-05-20"
        assert p[2] is False
        assert p[3] == "MALE"
        assert float(p[4]) == 180.0
        assert float(p[5]) == 75.0


def test_rerun_with_changed_value_upserts_not_duplicates(reset_db, source_dir, tmp_path, capsys):
    """Same natural key, changed value -> row updated; exit 0 despite rejected rows."""
    from pipeline import db

    assert _sync(source_dir) == 0
    make_zip(
        source_dir / "takeout-test.zip",
        {
            "Takeout/Google Health/Paired Devices/Devices.csv": DEVICES_CSV.replace(
                "APP67.20001.253.2", "APP99.0"
            ),
            "Takeout/Google Health/Sleep Score/sleep_score.csv": _sleep_ok_only(),
        },
    )
    assert _sync(source_dir) == 0
    with db.connect() as conn:
        devices = _q(conn, "SELECT count(*), max(fw_version) FROM silver.device")
        assert devices[0][0] == 2
        assert devices[0][1] == "APP99.0"
        # sleep now fully valid -> a second run has no rejects for it
        assert _q(conn, "SELECT count(*) FROM silver.sleep_score")[0][0] == 3
        assert _q(conn, "SELECT count(*) FROM silver.rejected_rows WHERE family = 'sleep_score'")[0][0] == 1  # original bad row, deduped
    captured = capsys.readouterr()
    assert "Takeouts processed: 1" in captured.out


def _sleep_ok_only() -> str:
    return (
        "sleep_log_entry_id,timestamp,overall_score,composition_score,revitalization_score,"
        "duration_score,deep_sleep_in_minutes,resting_heart_rate,restlessness\n"
        "53621952045,2026-09-20T08:14:30Z,82,,83,,93,73,0.14445574771108852\n"
        "53612104642,2026-09-19T10:01:00Z,86,,87,,97,74,0.17135761589403972\n"
        "53603187472,2026-09-18T07:43:30Z,81,,82,,83,75,0.14442013129102846\n"
    )

def test_silver_source_file_index_exists(reset_db, source_dir):
    """build_family deletes by _source_file once per takeout file, and real families
    carry ~1.4k files per family; without the index the DELETE full-scans every time."""
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        idx = _q(conn, "SELECT indexdef FROM pg_indexes WHERE schemaname='silver' "
                       "AND tablename='activity' AND indexdef LIKE '%(_source_file)%'")
        assert idx, "silver.activity needs an index on _source_file"


def test_gold_aggregate_indexes_exist(reset_db, source_dir):
    """The gold views aggregate silver.activity by day and ISO week; expression
    indexes keep those reads bounded on multi-million-row real takeouts."""
    from pipeline import db

    assert _sync(source_dir) == 0
    with db.connect() as conn:
        names = {r[0] for r in _q(
            conn, "SELECT indexname FROM pg_indexes "
                  "WHERE schemaname='silver' AND tablename='activity'")}
        assert {"activity_day_steps_idx", "activity_week_steps_idx"} <= names
