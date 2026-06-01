"""Archive transport for static export bundle roots."""

from __future__ import annotations

import base64
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from moexport.export import ExportResult, SpecInput, export

EXPORT_ARCHIVE_MEDIA_TYPE = "application/vnd.marimo.static-export+zip"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def archive_bundle(result: ExportResult) -> bytes:
    """Return zip bytes for the export root that contains an `ExportResult`."""

    return _archive_export_root(_export_root(result))


async def emit_bundle_archive(
    spec: SpecInput,
    *,
    to: str | Path | None = None,
    marker: str = "",
) -> None:
    """Export a bundle archive and emit it as base64 for scratchpad clients."""

    archive = await _capture_bundle_archive(spec, to=to)
    payload = base64.b64encode(archive).decode("ascii")
    print(f"{marker}{payload}")


def _archive_export_root(root: str | Path) -> bytes:
    """Return zip bytes for a canonical static export root directory."""

    root_path = Path(root)
    if not root_path.is_dir():
        raise NotADirectoryError(f"export root does not exist: {root_path}")

    buffer = BytesIO()
    with ZipFile(
        buffer,
        mode="w",
        compression=ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        for path in _archive_files(root_path):
            info = _zip_info(path.relative_to(root_path).as_posix())
            archive.writestr(info, path.read_bytes())

    return buffer.getvalue()


def _archive_files(root: Path) -> Iterable[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _zip_info(name: str) -> ZipInfo:
    info = ZipInfo(filename=name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _export_root(result: ExportResult) -> Path:
    return Path(result.bundle_path).parent.parent


async def _capture_bundle_archive(
    spec: SpecInput,
    *,
    to: str | Path | None,
) -> bytes:
    if to is not None:
        return archive_bundle(await export(spec, to=to))

    with TemporaryDirectory(prefix="moexport-archive-") as directory:
        return archive_bundle(await export(spec, to=directory))
