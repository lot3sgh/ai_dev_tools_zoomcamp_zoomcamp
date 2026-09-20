"""Ticket 04 — State & idempotency: no-op re-runs, md5 change detection, error retry, deletion ignore."""

from __future__ import annotations

from tests.conftest import make_zip


def _sync(source_dir):
    from pipeline.cli import main

    return main(["sync", "--source", "local", "--path", str(source_dir)])


def _q(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def test_rerun_is_noop(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    before = None
    with db.connect() as conn:
        before = _q(conn, "SELECT count(*) FROM bronze.sleep_score")[0][0]
    out = _sync(source_dir)
    assert out == 0
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM bronze.sleep_score")[0][0] == before
        assert _q(conn, "SELECT status, row_count FROM pipeline.processed_files")[0] == (
            "processed",
            13,  # 4 sleep + 5 azm + 2 devices + 1 profile + 1 glucose
        )
        assert _q(conn, "SELECT count(*) FROM pipeline.processed_files")[0][0] == 1


def test_changed_content_reingests(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    make_zip(source_dir / "takeout-test.zip", {"Takeout/Google Health/Your Profile/Profile.csv": _profile_changed()})
    assert _sync(source_dir) == 0
    with db.connect() as conn:
        rows = _q(conn, "SELECT count(*) FROM bronze.your_profile")
        assert rows[0][0] == 1  # replaced, not doubled
        assert _q(conn, "SELECT status FROM pipeline.processed_files")[0][0] == "processed"


def test_failed_file_is_marked_error_and_retried(reset_db, tmp_path):
    from pipeline import db

    src = tmp_path / "err"
    src.mkdir()
    bad = src / "broken.zip"
    bad.write_text("this is not a zip")
    assert _sync(src) == 1  # failure -> non-zero
    with db.connect() as conn:
        row = _q(conn, "SELECT status, error FROM pipeline.processed_files WHERE file_id='broken.zip'")[0]
        assert row[0] == "error"
        assert "not a zip" in row[1].lower() or "bad zip" in row[1].lower() or "zipfile" in row[1].lower()

    # replacing it with a valid takeout -> retried and processed
    entries = {"Takeout/Metrics/a.csv": "x\n1\n"}
    make_zip(src / "broken.zip", entries)
    assert _sync(src) == 0
    with db.connect() as conn:
        assert _q(conn, "SELECT status FROM pipeline.processed_files WHERE file_id='broken.zip'")[0][0] == "processed"
        assert _q(conn, "SELECT count(*) FROM bronze.metrics")[0][0] == 1


def test_deleting_source_file_keeps_data(reset_db, source_dir):
    from pipeline import db

    assert _sync(source_dir) == 0
    (source_dir / "takeout-test.zip").unlink()
    assert _sync(source_dir) == 0
    with db.connect() as conn:
        assert _q(conn, "SELECT count(*) FROM bronze.sleep_score")[0][0] == 4
        assert _q(conn, "SELECT count(*) FROM pipeline.processed_files")[0][0] == 1


def _profile_changed() -> str:
    return (
        "id,full_name,first_name,last_name,display_name_setting,username,email_address,date_of_birth,"
        "child,country,state,city,timezone,locale,member_since,start_of_week,sleep_tracking,"
        "time_display_format,gender,height,weight,weight_unit,distance_unit,height_unit\n"
        "C698DD,Tony L,Tony,L,name,renshou753@gmail.com,renshou753@gmail.com,1990-05-20,false,"
        "null,null,null,Asia/Shanghai,en_US,2024-07-23,SUNDAY,Normal,12hour,MALE,181.0,76.0,"
        "METRIC,METRIC,METRIC\n"
    )