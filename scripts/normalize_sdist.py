"""Normalize sdist gzip headers for reproducible release artifacts."""

from __future__ import annotations

import gzip
import io
import os
import sys
import tarfile
from pathlib import Path


def normalize(path: Path) -> None:
    """Rewrite one gzip member with the fixed release timestamp."""
    timestamp = int(os.environ["SOURCE_DATE_EPOCH"])
    source = tarfile.open(fileobj=io.BytesIO(gzip.decompress(path.read_bytes())))
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for original in sorted(source.getmembers(), key=lambda member: member.name):
            member = tarfile.TarInfo(original.name)
            member.type = original.type
            member.mode = original.mode
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            member.mtime = timestamp
            member.linkname = original.linkname
            member.pax_headers = {}
            data = source.extractfile(original)
            contents = data.read() if data is not None else b""
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
    source.close()
    with path.open("wb") as stream:
        with gzip.GzipFile(
            filename="", fileobj=stream, mode="wb", mtime=timestamp
        ) as archive:
            archive.write(payload.getvalue())


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        normalize(Path(argument))
