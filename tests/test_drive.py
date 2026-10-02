"""Ticket 06 — Drive adapter: catalog contract + files flowing through the engine."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pipeline.sources import FileInfo
from tests.conftest import DEVICES_CSV, SLEEP_CSV, make_zip
import pytest

pytestmark = pytest.mark.integration  # needs the throwaway Postgres DB



class _FakeRequest:
    def __init__(self, body):
        self.body = body

    def execute(self):
        return self.body


class _FakeFiles:
    def list(self, **kwargs):
        return _FakeRequest(
            {
                "files": [
                    {
                        "id": "drive-id-1",
                        "name": "takeout-drive.zip",
                        "modifiedTime": "2026-09-20T01:49:00.000Z",
                        "md5Checksum": "abc123",
                        "size": "1024",
                    }
                ]
            }
        )

    def get_media(self, **kwargs):
        return _FakeRequest(b"media")


class _FakeService:
    def files(self):
        return _FakeFiles()


def test_drive_catalog_shape():
    from pipeline.drive import DriveSource

    infos = DriveSource(_FakeService()).catalog()
    assert len(infos) == 1
    info = infos[0]
    assert info.id == "drive-id-1"
    assert info.name == "takeout-drive.zip"
    assert info.md5 == "abc123"
    assert isinstance(info.modified_time, datetime)
    assert info.modified_time.tzinfo is not None


def test_drive_files_flow_through_engine(reset_db, tmp_path):
    """Drive files hit the same engine as local: bronze + silver land identically."""
    from pipeline import db, state
    from pipeline.runner import process

    drv_dir = tmp_path / "drv"
    drv_dir.mkdir()
    zip_path = make_zip(
        drv_dir / "takeout-drive.zip",
        {
            "Takeout/Google Health/Sleep Score/sleep_score.csv": SLEEP_CSV,
            "Takeout/Google Health/Paired Devices/Devices.csv": DEVICES_CSV,
        },
    )

    class _DownloadDrive:
        """DriveSource whose downloader yields the fixture zip from disk."""

        def __init__(self):
            from pipeline.drive import DriveSource

            self.inner = DriveSource(_FakeService())

        def catalog(self):
            return self.inner.catalog()

        def open(self, info: FileInfo) -> Path:
            return zip_path

    db.ensure_schemas()
    summary = state.sync(_DownloadDrive(), process)
    assert len(summary.processed) == 1
    with db.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM bronze.sleep_score")
            assert cur.fetchone()[0] == 4  # all rows verbatim, incl. the bad one
            cur.execute("SELECT count(*) FROM silver.sleep_score")
            assert cur.fetchone()[0] == 3  # BADROW rejected
            cur.execute("SELECT count(*) FROM silver.device")
            assert cur.fetchone()[0] == 2
            cur.execute("SELECT md5 FROM pipeline.processed_files WHERE file_id = 'drive-id-1'")
            assert cur.fetchone()[0] == "abc123"