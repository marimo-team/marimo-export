from __future__ import annotations

import argparse
import atexit
import contextlib
import hashlib
import http.server
import json
import os
import platform
import secrets
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyarrow.ipc
import pyarrow.parquet
import yaml
from marimo_export import ExportSpec, open_publication
from marimo_export._json import JsonObject, JsonValue
from marimo_export._remote.client import HttpKernelTransport
from marimo_export._remote.managed import _SessionStream
from marimo_export.publication import ScalarDescriptor

REPOSITORY = Path(__file__).resolve().parents[3]
DEFAULT_TIMEOUT = 600.0
EXPECTED_SOURCE_SHA256 = "7b7f99ab80d34ec5bbeff465fb1cc94c095f9babe37e42ea3572d1327667c0bc"
EXPECTED_SOURCE_BYTES = 26_016
EXPECTED_STATES = (
    "baseline",
    "compact",
    "focus",
    "narrow_universe",
    "short_window",
    "weekly",
)
EXPECTED_OUTPUTS = (
    "chart_png",
    "chart_vegalite",
    "dashboard",
    "ohlc_matrix",
    "prices_arrow",
    "prices_parquet",
    "row_count",
)
EXPECTED_REPRESENTATIONS = {
    "dashboard": (
        "marimo.blob-asset.msgpack.v1",
        "application/vnd.marimo-export.anywidget.v1+json",
    ),
    "chart_vegalite": (
        "marimo.blob-asset.msgpack.v1",
        "application/vnd.vegalite.v6+json",
    ),
    "chart_png": ("marimo.blob-asset.msgpack.v1", "image/png"),
    "prices_parquet": (
        "marimo.blob-asset.msgpack.v1",
        "application/vnd.apache.parquet",
    ),
    "prices_arrow": (
        "apache.arrow.file.v1",
        "application/vnd.apache.arrow.file",
    ),
    "ohlc_matrix": ("numpy.npy.v1", "application/x-npy"),
    "row_count": (
        "marimo.scalar.v1",
        "application/vnd.marimo.scalar.v1+json",
    ),
}
SPEC_VALUE: JsonObject = {
    "schema": "marimo-export.spec.v1",
    "inputs": [
        "symbols",
        "interval",
        "start",
        "end",
        "chart_width",
        "symbols_selector",
    ],
    "states": {
        "baseline": {},
        "focus": {"symbols_selector": ["MSFT", "GOOGL", "AMZN"]},
        "compact": {"chart_width": 480},
        "narrow_universe": {
            "symbols": ["AAPL", "MSFT", "GOOGL"],
            "symbols_selector": ["AAPL", "MSFT"],
        },
        "short_window": {"start": "2025-04-14", "end": "2025-04-26"},
        "weekly": {"interval": "1wk"},
    },
    "outputs": {
        "dashboard": {"source": "dashboard"},
        "chart_vegalite": {"source": "chart_vegalite"},
        "chart_png": {"source": "chart_png"},
        "prices_parquet": {"source": "prices_parquet"},
        "prices_arrow": {"source": "df"},
        "ohlc_matrix": {"source": "ohlc_matrix"},
        "row_count": {"source": "row_count"},
    },
}
AUTHORED_CELLS = (
    """\
from marimo_export.exporters.altair import png, vegalite
from marimo_export.exporters.anywidget import bundle
from marimo_export.exporters.parquet import table
""",
    """\
dashboard = bundle(widget)
""",
    """\
chart_vegalite = vegalite(symbols_chart)
""",
    """\
chart_png = png(symbols_chart, scale=2)
""",
    """\
prices_parquet = table(
    df,
    compression="snappy",
    filename="prices.parquet",
)
""",
    """\
ohlc_matrix = df.select(
    "Open",
    "High",
    "Low",
    "Close",
).to_numpy()
""",
    """\
row_count = df.height
""",
    """\
publication_outputs = {
    "dashboard": dashboard,
    "chart_vegalite": chart_vegalite,
    "chart_png": chart_png,
    "prices_parquet": prices_parquet,
    "ohlc_matrix": ohlc_matrix,
    "row_count": row_count,
}
""",
)
AUTHORED_MARKERS = (
    "from marimo_export.exporters.altair import png, vegalite",
    "dashboard = bundle(widget)",
    "chart_vegalite = vegalite(symbols_chart)",
    "chart_png = png(symbols_chart, scale=2)",
    "prices_parquet = table(",
    "ohlc_matrix = df.select(",
    "row_count = df.height",
    "publication_outputs = {",
)
SOURCE_DEPENDENCIES = (
    "altair==6.0.0",
    "anywidget==0.11.0",
    "marimo",
    "polars==1.40.0",
    "pyarrow==23.0.1",
    "traitlets==5.14.3",
    "yfinance==1.3.0",
)
ENVIRONMENT_REQUIREMENTS = (
    "altair==6.0.0",
    "anywidget==0.11.0",
    "marimo==0.23.15",
    "msgspec>=0.20",
    "polars==1.40.0",
    "pyarrow==23.0.1",
    "pydantic>=2,<3",
    "PyYAML>=6.0.1",
    "traitlets==5.14.3",
    "uv>=0.11.33",
    "vl-convert-python>=1.8",
    "yfinance==1.3.0",
)


@dataclass(frozen=True)
class Paths:
    root: Path
    source: Path
    authored: Path
    spec: Path
    environment: Path
    wheel: Path
    cache: Path
    publications: Path
    browser: Path
    logs: Path
    acceptance: Path

    @classmethod
    def create(cls, root: Path) -> Paths:
        paths = cls(
            root=root,
            source=root / "source" / "finance.py",
            authored=root / "authored" / "finance.py",
            spec=root / "authored" / "finance.export.yaml",
            environment=root / "env",
            wheel=root / "wheel",
            cache=root / "authored" / "__marimo__" / "cache",
            publications=root / "publications",
            browser=root / "browser",
            logs=root / "logs",
            acceptance=root / "acceptance.json",
        )
        for directory in (
            paths.source.parent,
            paths.authored.parent,
            paths.wheel,
            paths.publications,
            paths.browser,
            paths.logs,
            root / "cache-evidence",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return paths


class LiveServer:
    def __init__(
        self,
        interpreter: Path,
        notebook: Path,
        log: Path,
        *,
        timeout: float,
    ) -> None:
        self.timeout = timeout
        self.session_id = f"s_{secrets.token_hex(16)}"
        self.access_token = secrets.token_urlsafe(32)
        self.port = _free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._log_stream = log.open("wb")
        self._stream: _SessionStream | None = None
        environment = _clean_environment(interpreter)
        self._process = subprocess.Popen(
            [
                str(interpreter),
                "-m",
                "marimo",
                "edit",
                str(notebook),
                "--headless",
                "--token-password",
                self.access_token,
                "--no-skew-protection",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=notebook.parent,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=self._log_stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.pid = self._process.pid
        self.process_group = os.getpgid(self.pid)
        try:
            self._wait_ready()
            self._stream = _SessionStream(self, timeout=timeout)
            self._stream.wait_for_kernel()
            completed = self._stream.completed_runs
            self._post_json(
                "/api/kernel/instantiate",
                {"objectIds": [], "values": [], "autoRun": True},
            )
            self._stream.wait_for_completed_run(completed)
        except BaseException:
            self.stop()
            raise

    @property
    def process(self) -> subprocess.Popen[bytes]:
        return self._process

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError("borrowed marimo server exited during startup")
            request = urllib.request.Request(
                f"{self.base_url}/api/sessions",
                headers=self._headers(),
            )
            try:
                with urllib.request.urlopen(request, timeout=1) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, OSError):
                time.sleep(0.05)
        raise TimeoutError("borrowed marimo server did not become ready")

    def _post_json(self, path: str, value: Mapping[str, object]) -> None:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(value, separators=(",", ":")).encode(),
            headers={
                **self._headers(),
                "Content-Type": "application/json",
                "Marimo-Session-Id": self.session_id,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            response.read()

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                stream.close()
        if self._process.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process_group, signal.SIGTERM)
            try:
                self._process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process_group, signal.SIGKILL)
                self._process.wait(timeout=15)
        self._log_stream.close()
        _wait_process_group_gone(self.process_group)


class Scratchpad:
    def __init__(self, server: LiveServer) -> None:
        self._transport = HttpKernelTransport(
            server.base_url,
            access_token=server.access_token,
            timeout=server.timeout,
        )
        self._session_id = server.session_id

    def execute(self, code: str) -> JsonValue:
        marker = f"__MARIMO_EXPORT_ACCEPTANCE_{secrets.token_hex(24)}__:"
        wrapped = "\n".join(
            (
                code,
                "import json as _acceptance_json",
                f"print({marker!r} + _acceptance_json.dumps("
                "_acceptance_result, sort_keys=True, separators=(',', ':')))",
            )
        )
        body = json.dumps({"code": wrapped}, separators=(",", ":")).encode()
        request = self._transport._request(
            "api/kernel/execute",
            method="POST",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Marimo-Session-Id": self._session_id,
            },
        )
        response = self._transport._open(request, "acceptance scratchpad")
        try:
            payload = self._transport._read_execution(response, marker)
        finally:
            response.close()
        return cast(JsonValue, json.loads(payload))


class WheelServer:
    def __init__(self, directory: Path, log: Path) -> None:
        self._requests: list[str] = []
        outer = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, directory=str(directory), **kwargs)

            def log_message(self, format: str, *args: object) -> None:
                del format
                outer._requests.append(" ".join(str(value) for value in args)[:1000])

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._log = log

    def url(self, filename: str) -> str:
        return f"http://127.0.0.1:{self.port}/{filename}"

    def close(self) -> tuple[str, ...]:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._log.write_text(
            "\n".join(self._requests) + ("\n" if self._requests else ""),
            encoding="utf-8",
        )
        return tuple(self._requests)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    source = Path(arguments.notebook).expanduser().resolve(strict=True)
    if not source.is_file() or not source.is_absolute():
        raise ValueError("finance notebook must be an absolute regular file")
    workdir = (
        Path(arguments.workdir).expanduser().absolute()
        if arguments.workdir
        else Path.cwd() / ".acceptance" / "finance"
    )
    if workdir.exists():
        if not arguments.replace:
            raise FileExistsError(f"acceptance workspace already exists: {workdir}")
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    paths = Paths.create(workdir)
    record: dict[str, Any] = {
        "schema": "marimo-export.finance-acceptance.v1",
        "pass": False,
        "stage": "initializing",
    }
    try:
        _run(source, paths, record, timeout=arguments.timeout)
    except BaseException as error:
        record["pass"] = False
        record["stage"] = "failed"
        record["failure"] = {
            "type": type(error).__name__,
            "message": str(error)[:4000],
        }
        _write_json(paths.acceptance, record)
        raise
    record["stage"] = "browser_pending"
    _write_json(paths.acceptance, record)
    print(json.dumps({"acceptance": str(paths.acceptance), "workdir": str(paths.root)}))
    return 0


def _run(source: Path, paths: Paths, record: dict[str, Any], *, timeout: float) -> None:
    started = _now()
    source_bytes = source.read_bytes()
    source_digest = _sha256(source_bytes)
    if source_digest != EXPECTED_SOURCE_SHA256 or len(source_bytes) != EXPECTED_SOURCE_BYTES:
        raise AssertionError("finance source does not match the accepted live notebook")
    dependencies = _source_dependencies(source_bytes.decode("utf-8"))
    if dependencies != SOURCE_DEPENDENCIES:
        raise AssertionError("finance source dependency block changed")
    shutil.copyfile(source, paths.source)
    shutil.copyfile(source, paths.authored)
    paths.spec.write_text(
        yaml.safe_dump(SPEC_VALUE, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    spec = ExportSpec.from_file(paths.spec)
    if spec.to_value() != SPEC_VALUE:
        raise AssertionError("serialized acceptance spec changed")

    record.update(
        {
            "started_at": started,
            "source": {
                "path": str(source),
                "sha256": source_digest,
                "bytes": len(source_bytes),
                "dependencies": list(dependencies),
            },
            "environment": {
                "host_python": platform.python_version(),
                "platform": platform.platform(),
                "repository_head": _git("rev-parse", "HEAD"),
                "repository_status": _git("status", "--short"),
            },
            "runs": {},
        }
    )
    _write_json(paths.acceptance, record)

    record["stage"] = "environment"
    interpreter = _create_environment(paths)
    absence = _run_process(
        [
            str(interpreter),
            "-c",
            "import importlib.util; assert importlib.util.find_spec('marimo_export') is None",
        ],
        cwd=paths.authored.parent,
        env=_clean_environment(interpreter),
        log=paths.logs / "package-absence.log",
    )
    if absence.returncode != 0:
        raise AssertionError("marimo-export was present before the sideload")
    environment_probe = _json_process(
        [
            str(interpreter),
            "-c",
            (
                "import json, platform, sys; "
                "print(json.dumps({'executable': sys.executable, "
                "'python': platform.python_version()}))"
            ),
        ],
        cwd=paths.authored.parent,
        env=_clean_environment(interpreter),
        log=paths.logs / "environment-probe.log",
    )
    record["environment"].update(environment_probe)
    record["environment"]["cache_path"] = str(paths.cache.resolve())
    _write_json(paths.acceptance, record)

    record["stage"] = "borrowed_server"
    server = LiveServer(
        interpreter,
        paths.authored,
        paths.logs / "borrowed-server.log",
        timeout=timeout,
    )
    atexit.register(server.stop)
    authored_after_initial = _sha256(paths.authored.read_bytes())
    scratchpad = Scratchpad(server)
    initial = cast(
        dict[str, Any],
        scratchpad.execute(
            """\
import importlib.util
from marimo._code_mode import get_context as _get_context

async with _get_context() as _context:
    _df = _context.globals.get("df")
    _chart = _context.globals.get("symbols_chart")
    _widget = _context.globals.get("widget")
    if _df is None or _chart is None or _widget is None:
        raise RuntimeError("live finance outputs are unavailable")
    _dates = _df.get_column("Date")
    _acceptance_result = {
        "marimo_export_present": importlib.util.find_spec("marimo_export") is not None,
        "rows": _df.height,
        "columns": _df.columns,
        "symbols": sorted(_df.get_column("Symbol").unique().to_list()),
        "date_min": str(_dates.min()),
        "date_max": str(_dates.max()),
        "chart_type": type(_chart).__module__ + "." + type(_chart).__qualname__,
        "widget_type": type(_widget).__module__ + "." + type(_widget).__qualname__,
        "kernel_pid": __import__("os").getpid(),
    }
"""
        ),
    )
    if initial["marimo_export_present"]:
        raise AssertionError("marimo-export entered the kernel before sideload")
    if initial["rows"] <= 0 or set(initial["symbols"]) != {
        "AAPL",
        "AMZN",
        "CRWV",
        "GOOGL",
        "MSFT",
    }:
        raise AssertionError("Yahoo Finance returned an invalid live result")
    initial["authored_sha256"] = authored_after_initial
    initial["completed_at"] = _now()
    record["provider"] = initial
    _assert_source_unchanged(source, paths.source, source_digest)
    _write_json(paths.acceptance, record)

    record["stage"] = "wheel_sideload"
    wheel = _build_wheel(paths)
    wheel_bytes = wheel.read_bytes()
    wheel_digest = _sha256(wheel_bytes)
    wheel_version = _wheel_version(wheel)
    wheel_server = WheelServer(paths.wheel, paths.logs / "wheel-http.log")
    wheel_url = wheel_server.url(wheel.name)
    try:
        sideload = cast(
            dict[str, Any],
            scratchpad.execute(
                f"""\
import hashlib as _hashlib
import importlib as _importlib
import importlib.metadata as _metadata
import importlib.util as _importlib_util
import shutil as _shutil
import subprocess as _subprocess
import sys as _sys
import urllib.request as _urllib_request

_before = _importlib_util.find_spec("marimo_export")
if _before is not None:
    raise RuntimeError("marimo-export is already importable")
_wheel_url = {wheel_url!r}
_expected_digest = {wheel_digest!r}
with _urllib_request.urlopen(_wheel_url, timeout=60) as _response:
    _wheel_bytes = _response.read()
_download_digest = _hashlib.sha256(_wheel_bytes).hexdigest()
if _download_digest != _expected_digest:
    raise RuntimeError("wheel download digest mismatch")
_uv = _shutil.which("uv")
if _uv is None:
    raise RuntimeError("uv executable is unavailable")
_install = _subprocess.run(
    [
        _uv,
        "pip",
        "install",
        "--python",
        _sys.executable,
        "--no-deps",
        "--no-cache",
        _wheel_url + "#sha256=" + _expected_digest,
    ],
    capture_output=True,
    text=True,
    timeout=180,
)
if _install.returncode != 0:
    raise RuntimeError("wheel install failed: " + _install.stderr[-2000:])
_importlib.invalidate_caches()
import marimo_export as _marimo_export

_acceptance_result = {{
    "present_before": _before is not None,
    "download_sha256": _download_digest,
    "download_bytes": len(_wheel_bytes),
    "distribution_version": _metadata.version("marimo-export"),
    "module_path": _marimo_export.__file__,
    "kernel_pid": __import__("os").getpid(),
}}
"""
            ),
        )
    finally:
        wheel_requests = wheel_server.close()
    if (
        sideload["present_before"]
        or sideload["download_sha256"] != wheel_digest
        or sideload["download_bytes"] != len(wheel_bytes)
        or sideload["distribution_version"] != wheel_version
        or sideload["kernel_pid"] != initial["kernel_pid"]
    ):
        raise AssertionError("development wheel sideload provenance is invalid")
    record["wheel"] = {
        "filename": wheel.name,
        "sha256": wheel_digest,
        "bytes": len(wheel_bytes),
        "version": wheel_version,
        "built_after_provider": True,
        "http_requests": len(wheel_requests),
        "module_path": sideload["module_path"],
    }
    _write_json(paths.acceptance, record)

    record["stage"] = "code_mode"
    created = cast(
        list[str],
        scratchpad.execute(
            """\
from marimo._code_mode import get_context as _get_context

_codes = """
            + repr(AUTHORED_CELLS)
            + """
_created = []
async with _get_context() as _context:
    _after = str(_context.cells[-1].id) if len(_context.cells) else None
    for _code in _codes:
        _cell_id = _context.create_cell(
            _code,
            after=_after,
            hide_code=True,
            disabled=False,
        )
        _context.run_cell(str(_cell_id))
        _created.append(str(_cell_id))
        _after = str(_cell_id)
_acceptance_result = _created
"""
        ),
    )
    if len(created) != len(AUTHORED_CELLS) or len(set(created)) != len(created):
        raise AssertionError("code mode did not create eight distinct cells")
    _wait_for_authored_cells(paths.authored, timeout)
    authored_digest = _sha256(paths.authored.read_bytes())
    authored_probe = cast(
        dict[str, Any],
        scratchpad.execute(
            """\
from marimo._code_mode import get_context as _get_context
from marimo_export import BlobAsset as _BlobAsset

_names = (
    "dashboard",
    "chart_vegalite",
    "chart_png",
    "prices_parquet",
    "ohlc_matrix",
    "row_count",
    "publication_outputs",
)
async with _get_context() as _context:
    _values = {name: _context.globals.get(name) for name in _names}
    _df = _context.globals["df"]
    _acceptance_result = {
        "definitions": sorted(name for name in _names if name in _context.graph.definitions),
        "types": {
            name: type(value).__module__ + "." + type(value).__qualname__
            for name, value in _values.items()
        },
        "media_types": {
            name: value.media_type
            for name, value in _values.items()
            if isinstance(value, _BlobAsset)
        },
        "matrix_shape": list(_values["ohlc_matrix"].shape),
        "matrix_dtype": str(_values["ohlc_matrix"].dtype),
        "row_count": _values["row_count"],
        "df_rows": _df.height,
        "document_cells": len(_context.cells),
    }
"""
        ),
    )
    if authored_probe["definitions"] != sorted(
        [
            "dashboard",
            "chart_vegalite",
            "chart_png",
            "prices_parquet",
            "ohlc_matrix",
            "row_count",
            "publication_outputs",
        ]
    ):
        raise AssertionError("authored output definitions are incomplete")
    if authored_probe["matrix_shape"][1] != 4:
        raise AssertionError("authored OHLC matrix does not have four columns")
    if authored_probe["row_count"] != authored_probe["df_rows"]:
        raise AssertionError("authored row count disagrees with df.height")
    if authored_probe["media_types"] != {
        "dashboard": "application/vnd.marimo-export.anywidget.v1+json",
        "chart_png": "image/png",
        "chart_vegalite": "application/vnd.vegalite.v6+json",
        "prices_parquet": "application/vnd.apache.parquet",
    }:
        raise AssertionError("authored BlobAsset media types changed")
    _validate_marimo_notebook(interpreter, paths.authored, paths.logs / "authored-parse.log")
    record["authored"] = {
        "sha256": authored_digest,
        "definitions": authored_probe["definitions"],
        "created_cell_ids": created,
        "types": authored_probe["types"],
        "media_types": authored_probe["media_types"],
        "matrix_shape": authored_probe["matrix_shape"],
        "matrix_dtype": authored_probe["matrix_dtype"],
    }
    _assert_source_unchanged(source, paths.source, source_digest)
    _write_json(paths.acceptance, record)

    cli = paths.environment / "bin" / "marimo-export"
    capture_environment = _clean_environment(interpreter)
    capture_environment["MARIMO_EXPORT_ACCESS_TOKEN"] = server.access_token
    for run_name in ("capture_cold", "capture_warm"):
        record["stage"] = run_name
        output = paths.publications / run_name.replace("_", "-")
        command = [
            str(cli),
            "capture",
            server.base_url,
            "--session",
            server.session_id,
            "--spec",
            str(paths.spec),
            "--output",
            str(output),
            "--timeout",
            str(timeout),
            "--json",
        ]
        started_at = _now()
        result = _cli_json(
            command,
            cwd=paths.authored.parent,
            env=capture_environment,
            log=paths.logs / f"{run_name}.log",
        )
        record["runs"][run_name] = _run_record(
            output,
            result,
            command=_redacted_command(command, server),
            started_at=started_at,
        )
        _write_json(paths.acceptance, record)
    authored_after_capture = _sha256(paths.authored.read_bytes())
    if authored_after_capture != authored_digest:
        raise AssertionError("borrowed capture changed the authored notebook")
    if server.process.poll() is not None:
        raise AssertionError("borrowed capture stopped the borrowed server")
    post_capture = cast(
        dict[str, Any],
        scratchpad.execute(
            """\
from marimo._code_mode import get_context as _get_context

async with _get_context() as _context:
    _projection_cells = [
        str(cell.id)
        for cell in _context.cells
        if cell.code.startswith("# marimo-export projection:")
    ]
    _selector = _context.globals["symbols_selector"]
    _acceptance_result = {
        "projection_cells": _projection_cells,
        "selector_value": list(_selector.value),
        "cells": len(_context.cells),
    }
"""
        ),
    )
    if post_capture["projection_cells"] or post_capture["selector_value"] != ["AAPL", "CRWV"]:
        raise AssertionError("borrowed capture did not restore the parent session")
    _require_warm_pair(
        paths.publications / "capture-cold",
        paths.publications / "capture-warm",
        record["runs"]["capture_cold"],
        record["runs"]["capture_warm"],
    )
    server.stop()
    atexit.unregister(server.stop)
    if server.process.poll() is None:
        raise AssertionError("borrowed marimo server survived shutdown")

    record["stage"] = "managed_build"
    if paths.cache.resolve() != (paths.authored.parent / "__marimo__" / "cache").resolve():
        raise AssertionError("resolved cache path escaped the acceptance workspace")
    cache_evidence = paths.root / "cache-evidence" / "capture-cache.txt"
    cache_evidence.write_text(_tree_digest_text(paths.cache), encoding="utf-8")
    if paths.cache.exists():
        shutil.rmtree(paths.cache)
    build_environment = _clean_environment(interpreter)
    for run_name in ("build_cold", "build_warm"):
        output = paths.publications / run_name.replace("_", "-")
        command = [
            str(cli),
            "build",
            str(paths.authored),
            "--spec",
            str(paths.spec),
            "--output",
            str(output),
            "--timeout",
            str(timeout),
            "--json",
        ]
        started_at = _now()
        result = _cli_json(
            command,
            cwd=paths.authored.parent,
            env=build_environment,
            log=paths.logs / f"{run_name}.log",
        )
        record["runs"][run_name] = _run_record(
            output,
            result,
            command=[str(cli), "build", "finance.py", "--spec", "finance.export.yaml"],
            started_at=started_at,
        )
        _write_json(paths.acceptance, record)
    if _sha256(paths.authored.read_bytes()) != authored_digest:
        raise AssertionError("managed build changed the authored notebook")
    _require_warm_pair(
        paths.publications / "build-cold",
        paths.publications / "build-warm",
        record["runs"]["build_cold"],
        record["runs"]["build_warm"],
    )

    record["stage"] = "cross_mode"
    cross_mode = _cross_mode_checks(
        paths.publications / "capture-cold",
        paths.publications / "build-cold",
    )
    record["cross_mode"] = cross_mode
    record["deduplication"] = {
        name: {
            "logical_outputs": len(EXPECTED_STATES) * len(EXPECTED_OUTPUTS),
            "unique_assets": run["assets"],
        }
        for name, run in record["runs"].items()
    }
    _integrity_negative_test(
        paths.publications / "capture-cold",
        paths.root / "integrity-negative",
    )
    record["integrity_negative_test"] = {"python_rejected_tamper": True}
    _assemble_browser_root(paths)
    _assert_source_unchanged(source, paths.source, source_digest)
    record["cleanup"] = {
        "source_unchanged": True,
        "authored_unchanged_after_capture": authored_after_capture == authored_digest,
        "remaining_python_processes": _matching_process_count(paths.root, "python"),
        "remaining_server_sockets": _listening_socket_count(paths.root),
    }
    if record["cleanup"]["remaining_python_processes"] != 0:
        raise AssertionError("acceptance Python processes survived publication")
    record["prepared_at"] = _now()


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare the live finance publications for Python-free browser acceptance."
    )
    parser.add_argument("notebook")
    parser.add_argument("--workdir")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    arguments = parser.parse_args(argv)
    if arguments.timeout <= 0:
        parser.error("--timeout must be positive")
    return arguments


def _create_environment(paths: Paths) -> Path:
    _run_checked(
        ["uv", "venv", "--python", "3.12", str(paths.environment)],
        cwd=REPOSITORY,
        log=paths.logs / "environment-create.log",
    )
    interpreter = paths.environment / "bin" / "python"
    _run_checked(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(interpreter),
            *ENVIRONMENT_REQUIREMENTS,
        ],
        cwd=paths.authored.parent,
        log=paths.logs / "environment-install.log",
    )
    return interpreter


def _build_wheel(paths: Paths) -> Path:
    _run_checked(
        [
            "uv",
            "build",
            "--package",
            "marimo-export",
            "--wheel",
            "--out-dir",
            str(paths.wheel),
            "--clear",
        ],
        cwd=REPOSITORY,
        log=paths.logs / "wheel-build.log",
    )
    wheels = tuple(paths.wheel.glob("marimo_export-*.whl"))
    if len(wheels) != 1:
        raise AssertionError("wheel build did not produce exactly one artifact")
    return wheels[0]


def _wheel_version(wheel: Path) -> str:
    result = _json_process(
        [
            "uv",
            "run",
            "--isolated",
            "--no-project",
            "--with",
            str(wheel),
            "python",
            "-c",
            (
                "import importlib.metadata, json; "
                "print(json.dumps({'version': importlib.metadata.version('marimo-export')}))"
            ),
        ],
        cwd=REPOSITORY,
        env=dict(os.environ),
        log=wheel.parent.parent / "logs" / "wheel-version.log",
    )
    return cast(str, result["version"])


def _run_record(
    path: Path,
    result: Mapping[str, Any],
    *,
    command: Sequence[str],
    started_at: str,
) -> dict[str, Any]:
    publication = open_publication(path)
    verification = publication.verify()
    if tuple(state.name for state in publication.states()) != EXPECTED_STATES:
        raise AssertionError("publication state relation changed")
    if publication.output_names != EXPECTED_OUTPUTS:
        raise AssertionError("publication output relation changed")
    if verification.outputs != len(EXPECTED_STATES) * len(EXPECTED_OUTPUTS):
        raise AssertionError("publication does not contain 42 output receipts")
    representations = {
        output.name: (output.codec, output.media_type)
        for output in publication.state("baseline").outputs()
    }
    if representations != EXPECTED_REPRESENTATIONS:
        raise AssertionError("publication representations changed")
    cache = cast(Mapping[str, int], result["cache"])
    if cache["hits"] + cache["misses"] != 42:
        raise AssertionError("cache summary does not cover 42 receipts")
    index = path / "index.json"
    assets = _asset_digests(path)
    domains = {state.name: _validate_state_domain(state) for state in publication.states()}
    return {
        "command": list(command),
        "mode": result["mode"],
        "started_at": started_at,
        "ended_at": _now(),
        "publication_path": str(path),
        "index_sha256": _sha256(index.read_bytes()),
        "document_sha256": publication.notebook.document_sha256,
        "states": list(EXPECTED_STATES),
        "outputs": list(EXPECTED_OUTPUTS),
        "assets": verification.assets,
        "asset_bytes": verification.bytes_verified,
        "representations": {
            name: {"codec": value[0], "media_type": value[1]}
            for name, value in representations.items()
        },
        "asset_digests": assets,
        "cache": dict(cache),
        "domain": domains,
        "cleanup": {"projection_receipts": 42, "verified": True},
    }


def _validate_state_domain(state: Any) -> dict[str, Any]:
    row_count = state.output("row_count").scalar()
    if not isinstance(row_count, int) or row_count <= 0:
        raise AssertionError(f"state {state.name} has an invalid row count")

    arrow_bytes = state.output("prices_arrow").asset_bytes()
    with pyarrow.ipc.open_file(BytesIO(arrow_bytes)) as reader:
        arrow = reader.read_all()
    parquet_blob = state.output("prices_parquet").blob_asset()
    parquet = pyarrow.parquet.read_table(BytesIO(parquet_blob.data))
    matrix = np.load(BytesIO(state.output("ohlc_matrix").asset_bytes()), allow_pickle=False)
    if arrow.num_rows != row_count or parquet.num_rows != row_count:
        raise AssertionError(f"state {state.name} table row counts disagree")
    if matrix.shape != (row_count, 4):
        raise AssertionError(f"state {state.name} NumPy shape disagrees")
    arrow_ohlc = np.column_stack(
        [
            arrow.column(name).combine_chunks().to_numpy(zero_copy_only=False)
            for name in ("Open", "High", "Low", "Close")
        ]
    )
    if not np.allclose(matrix, arrow_ohlc, equal_nan=True):
        raise AssertionError(f"state {state.name} NumPy values disagree with Arrow")
    arrow_symbols = sorted(set(arrow.column("Symbol").to_pylist()))
    parquet_symbols = sorted(set(parquet.column("Symbol").to_pylist()))
    if arrow_symbols != parquet_symbols:
        raise AssertionError(f"state {state.name} Arrow and Parquet symbols disagree")

    chart_blob = state.output("chart_vegalite").blob_asset()
    chart = json.loads(chart_blob.data)
    chart_symbols = sorted(_collect_field_values(chart, "Symbol"))
    expected_selected = sorted(cast(Sequence[str], state.inputs["symbols_selector"]))
    if chart_symbols != expected_selected:
        raise AssertionError(f"state {state.name} Vega-Lite symbols disagree")

    png_blob = state.output("chart_png").blob_asset()
    png_width, png_height = _png_dimensions(png_blob.data)
    widget_blob = state.output("dashboard").blob_asset()
    widget = json.loads(widget_blob.data)
    widget_text = json.dumps(widget, sort_keys=True)
    for symbol in cast(Sequence[str], state.inputs["symbols"]):
        if symbol not in widget_text:
            raise AssertionError(f"state {state.name} AnyWidget omitted {symbol}")
    expected_symbols = sorted(cast(Sequence[str], state.inputs["symbols"]))
    if arrow_symbols != expected_symbols:
        raise AssertionError(f"state {state.name} provider symbols disagree with inputs")
    dates = arrow.column("Date").to_pylist()
    return {
        "rows": row_count,
        "columns": arrow.column_names,
        "symbols": arrow_symbols,
        "date_min": str(min(dates)),
        "date_max": str(max(dates)),
        "npy_shape": list(matrix.shape),
        "npy_dtype": str(matrix.dtype),
        "png_width": png_width,
        "png_height": png_height,
        "vegalite_symbols": chart_symbols,
        "anywidget_bytes": len(widget_blob.data),
    }


def _cross_mode_checks(capture_path: Path, build_path: Path) -> dict[str, Any]:
    capture = open_publication(capture_path)
    build = open_publication(build_path)
    if capture.notebook.document_sha256 != build.notebook.document_sha256:
        raise AssertionError("ownership modes used different notebook documents")
    if capture.input_names != build.input_names or capture.output_names != build.output_names:
        raise AssertionError("ownership modes published different relations")
    matched_assets = 0
    compared_assets = 0
    for capture_state in capture.states():
        build_state = build.state(capture_state.name)
        if dict(capture_state.inputs) != dict(build_state.inputs):
            raise AssertionError("ownership modes normalized different state vectors")
        for output_name in capture.output_names:
            left = capture_state.output(output_name)
            right = build_state.output(output_name)
            if (left.codec, left.media_type) != (right.codec, right.media_type):
                raise AssertionError("ownership modes published different representations")
            if isinstance(left.descriptor, ScalarDescriptor):
                continue
            compared_assets += 1
            if left.descriptor.asset.sha256 == cast(Any, right.descriptor).asset.sha256:
                matched_assets += 1
    compact_width = cast(
        int,
        _validate_state_domain(capture.state("compact"))["png_width"],
    )
    baseline_width = cast(
        int,
        _validate_state_domain(capture.state("baseline"))["png_width"],
    )
    if compact_width >= baseline_width:
        raise AssertionError("compact chart PNG did not honor chart_width")
    if capture.state("weekly").inputs["interval"] != "1wk":
        raise AssertionError("weekly state interval changed")
    return {
        "document_sha256": capture.notebook.document_sha256,
        "states": list(capture_state.name for capture_state in capture.states()),
        "outputs": list(capture.output_names),
        "matched_assets": matched_assets,
        "compared_assets": compared_assets,
        "compact_png_width": compact_width,
        "baseline_png_width": baseline_width,
    }


def _require_warm_pair(
    cold_path: Path,
    warm_path: Path,
    cold: Mapping[str, Any],
    warm: Mapping[str, Any],
) -> None:
    if _tree_digests(cold_path) != _tree_digests(warm_path):
        raise AssertionError("warm publication differs byte for byte from cold publication")
    warm_cache = cast(Mapping[str, int], warm["cache"])
    if warm_cache["hits"] <= 0:
        raise AssertionError("warm publication restored no native marimo cache entries")
    if cold["document_sha256"] != warm["document_sha256"]:
        raise AssertionError("warm publication changed the notebook identity")


def _integrity_negative_test(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    assets = sorted((destination / "assets").iterdir())
    if not assets:
        raise AssertionError("live publication has no asset to tamper")
    payload = bytearray(assets[0].read_bytes())
    payload[len(payload) // 2] ^= 0x01
    assets[0].write_bytes(payload)
    try:
        open_publication(destination).verify()
    except Exception as error:
        if "SHA-256" not in str(error):
            raise
    else:
        raise AssertionError("Python verification accepted a tampered asset")


def _assemble_browser_root(paths: Paths) -> None:
    _run_checked(
        ["pnpm", "--filter", "@marimo-team/marimo-export-finance-demo", "build"],
        cwd=REPOSITORY,
        log=paths.logs / "finance-app-build.log",
    )
    static_root = paths.browser / "static"
    if static_root.exists():
        shutil.rmtree(static_root)
    shutil.copytree(REPOSITORY / "apps" / "finance-demo" / "dist", static_root)
    publications = static_root / "publications"
    for name in ("capture-cold", "capture-warm", "build-cold", "build-warm"):
        shutil.copytree(paths.publications / name, publications / name)
    shutil.copytree(
        paths.publications / "capture-cold", static_root / "relocated" / "deep" / "finance"
    )
    shutil.copytree(paths.root / "integrity-negative", static_root / "tampered")


def _wait_for_authored_cells(path: Path, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        source = path.read_text(encoding="utf-8")
        if all(marker in source for marker in AUTHORED_MARKERS):
            return
        time.sleep(0.05)
    raise TimeoutError("code-mode cells were not saved to the authored notebook")


def _validate_marimo_notebook(interpreter: Path, path: Path, log: Path) -> None:
    code = (
        "from marimo._ast.load import get_notebook_status; "
        f"result = get_notebook_status({str(path)!r}); "
        "assert result.status == 'valid', result"
    )
    _run_checked(
        [str(interpreter), "-c", code],
        cwd=path.parent,
        env=_clean_environment(interpreter),
        log=log,
    )


def _source_dependencies(source: str) -> tuple[str, ...]:
    marker = "# dependencies = ["
    start = source.find(marker)
    end = source.find("# ]", start)
    if start < 0 or end < 0:
        raise AssertionError("finance source has no PEP 723 dependency block")
    values: list[str] = []
    for line in source[start:end].splitlines()[1:]:
        stripped = line.strip()
        if stripped.startswith('#     "') and stripped.endswith('",'):
            values.append(stripped[7:-2])
    return tuple(values)


def _collect_field_values(value: object, field: str) -> set[str]:
    result: set[str] = set()
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            candidate = current.get(field)
            if isinstance(candidate, str):
                result.add(candidate)
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return result


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("PNG output has invalid framing")
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _asset_digests(path: Path) -> list[str]:
    return [
        item.name.split(".", 1)[0] for item in sorted((path / "assets").iterdir()) if item.is_file()
    ]


def _tree_digests(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): _sha256(item.read_bytes())
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _tree_digest_text(path: Path) -> str:
    if not path.exists():
        return ""
    return "\n".join(f"{digest}  {name}" for name, digest in _tree_digests(path).items()) + "\n"


def _matching_process_count(root: Path, executable: str) -> int:
    return len(_matching_process_ids(root, executable=executable))


def _matching_process_ids(root: Path, *, executable: str | None = None) -> tuple[int, ...]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="],
        capture_output=True,
        text=True,
        check=True,
    )
    current = os.getpid()
    matches: list[int] = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2:
            continue
        process_id = int(fields[0])
        command = fields[1]
        if process_id == current or str(root) not in command:
            continue
        program = Path(command.split(maxsplit=1)[0]).name.lower()
        if executable is not None and not program.startswith(executable.lower()):
            continue
        matches.append(process_id)
    return tuple(matches)


def _listening_socket_count(root: Path) -> int:
    listeners = 0
    for process_id in _matching_process_ids(root):
        result = subprocess.run(
            [
                "lsof",
                "-nP",
                "-a",
                "-p",
                str(process_id),
                "-iTCP",
                "-sTCP:LISTEN",
                "-Fn",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        listeners += sum(1 for line in result.stdout.splitlines() if line.startswith("n"))
    return listeners


def _wait_process_group_gone(process_group: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    raise RuntimeError("borrowed marimo process group survived shutdown")


def _assert_source_unchanged(source: Path, copy: Path, digest: str) -> None:
    if _sha256(source.read_bytes()) != digest or _sha256(copy.read_bytes()) != digest:
        raise AssertionError("finance source changed during acceptance")


def _clean_environment(interpreter: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.update(
        {
            "PATH": f"{interpreter.parent}{os.pathsep}{environment.get('PATH', '')}",
            "PYTHONNOUSERSITE": "1",
            "MARIMO_SKIP_UPDATE_CHECK": "1",
            "NO_COLOR": "1",
        }
    )
    return environment


def _cli_json(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log: Path,
) -> dict[str, Any]:
    completed = _run_process(command, cwd=cwd, env=env, log=log)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit {completed.returncode}: {command[1]}")
    value = json.loads(completed.stdout)
    if value.get("ok") is not True or not isinstance(value.get("result"), dict):
        raise RuntimeError(f"command returned an invalid result: {command[1]}")
    return cast(dict[str, Any], value["result"])


def _json_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log: Path,
) -> dict[str, Any]:
    completed = _run_process(command, cwd=cwd, env=env, log=log)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit {completed.returncode}")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("command returned a non-object JSON value")
    return cast(dict[str, Any], value)


def _run_checked(
    command: Sequence[str],
    *,
    cwd: Path,
    log: Path,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = _run_process(command, cwd=cwd, env=env or dict(os.environ), log=log)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit {completed.returncode}: {' '.join(command[:3])}"
        )
    return completed


def _run_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log: Path,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=dict(env),
        capture_output=True,
        text=True,
    )
    log.write_text(
        json.dumps(
            {
                "command": list(command[:3]),
                "returncode": completed.returncode,
                "stdout": completed.stdout[-16_000:],
                "stderr": completed.stderr[-16_000:],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return completed


def _redacted_command(command: Sequence[str], server: LiveServer) -> list[str]:
    result = []
    for value in command:
        value = value.replace(server.base_url, "http://127.0.0.1:<port>")
        value = value.replace(server.session_id, "<session>")
        value = value.replace(server.access_token, "<redacted>")
        result.append(value)
    return result


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as stream:
        stream.bind(("127.0.0.1", 0))
        return int(stream.getsockname()[1])


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())
