"""Drive source adapter: list takeout zips via Drive API and stream their download (ADR-0001)."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account

from pipeline import config
from pipeline.sources import FileInfo

DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"
DOWNLOAD_CHUNK = 1024 * 256


def build_service() -> Any:
    """Build the Drive API service for the Service Account with a readonly token."""
    from googleapiclient.discovery import build

    key_path = config.sa_key_path()
    if key_path is None:
        raise FileNotFoundError(
            "Drive mode needs the Service Account key; set GOOGLE_APPLICATION_CREDENTIALS"
        )
    creds = service_account.Credentials.from_service_account_file(
        str(key_path), scopes=[DRIVE_READONLY]
    )
    creds.refresh(Request())
    return build("drive", "v3", credentials=creds, cache_discovery=False)


class DriveSource:
    """Drive files through the same catalog contract as LocalSource."""

    def __init__(self, service: Any, limit: int | None = None):
        self.service = service
        self.limit = limit

    def catalog(self) -> list[FileInfo]:
        files = (
            self.service.files()
            .list(
                q="name contains '.zip'",
                fields="files(id,name,modifiedTime,md5Checksum,size)",
                pageSize=1000,
            )
            .execute()
            .get("files", [])
        )
        infos = [
            FileInfo(
                id=f["id"],
                name=f["name"],
                modified_time=datetime.fromisoformat(
                    f["modifiedTime"].replace("Z", "+00:00")
                ).astimezone(timezone.utc),
                md5=f.get("md5Checksum") or "",
                size=int(f.get("size") or 0),
            )
            for f in files
        ]
        if self.limit is not None:
            infos = infos[: self.limit]
        return infos

    def open(self, info: FileInfo) -> Path:
        """Stream the Drive file to a temp path (takeouts are too big for memory)."""
        from googleapiclient.http import MediaIoBaseDownload

        request = self.service.files().get_media(fileId=info.id)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        path = Path(tmp.name)
        tmp.close()
        downloader = MediaIoBaseDownload(path.open("wb"), request, chunksize=DOWNLOAD_CHUNK)
        done = False
        while not done:
            _status, done = downloader.next_chunk()
        return path

    @staticmethod
    def make(limit: int | None = None) -> "DriveSource":
        return DriveSource(build_service(), limit=limit)