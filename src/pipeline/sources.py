"""Source adapters: the file catalog contract shared by local and Drive modes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


class Source(Protocol):
    """What any source adapter must provide: a file catalog and an open()-able artifact."""

    def catalog(self) -> list[FileInfo]:
        ...

    def open(self, info: FileInfo) -> Path:
        ...


@dataclass(frozen=True)
class FileInfo:
    """One takeout archive as seen by a source adapter (ADR-0001 contract)."""

    id: str
    name: str
    modified_time: datetime  # tz-aware
    md5: str
    size: int
    local_path: Path | None = None

    # local adapters: id = filename


def _md5(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


class LocalSource:
    """Takeout zips from a local directory; file id = filename, md5 = file hash."""

    def __init__(self, directory: Path, limit: int | None = None):
        self.directory = Path(directory)
        self.limit = limit

    def catalog(self) -> list[FileInfo]:
        if not self.directory.is_dir():
            raise FileNotFoundError(f"source directory does not exist: {self.directory}")
        files = sorted(self.directory.glob("*.zip"))
        infos = [
            FileInfo(
                id=p.name,
                name=p.name,
                modified_time=datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc),
                md5=_md5(p),
                size=p.stat().st_size,
                local_path=p,
            )
            for p in files
        ]
        if self.limit is not None:
            infos = infos[: self.limit]
        return infos

    def open(self, info: FileInfo) -> Path:
        assert info.local_path is not None
        return info.local_path