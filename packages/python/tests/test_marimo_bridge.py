from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

import pytest
from marimo._messaging.cell_output import CellChannel, CellOutput
from marimo._save.stubs.lazy_stub import BlobAsset
from marimo_export._json import JsonObject, json_object
from marimo_export._marimo import bridge, code_mode
from marimo_export._marimo.cache import CacheAssetReceipt, CacheAssetRef
from marimo_export._marimo.compat import MarimoCapabilities
from marimo_export.errors import ProjectionError


class _LiveContext:
    def __init__(self) -> None:
        self.globals: dict[str, object] = {"summary": {"rows": 3}}

    async def __aenter__(self) -> _LiveContext:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args


class _CellLiveContext(_LiveContext):
    def __init__(self, output: CellOutput) -> None:
        super().__init__()

        def exporter(value: object, **options: object) -> object:
            return value, options

        self.globals["custom_exporter"] = exporter
        cell = type(
            "Cell",
            (),
            {"id": "cell-report", "name": "report", "output": output},
        )()
        self.cells = {"cell-report": cell, "report": cell}


@dataclass(frozen=True)
class _Ticket:
    id: str
    asset: CacheAssetRef

    def wire(self) -> dict[str, Any]:
        return {
            "ticket": self.id,
            "expires_at_ms": 1_750_000_300_000,
            "assets": [
                {
                    **self.asset.wire(),
                    "url": "./@file/97-projection.bin",
                }
            ],
        }


@dataclass(frozen=True)
class _ExporterAvailability:
    name: str
    version: str
    available: bool
    extra: str | None


def _request(operation: str, params: object, request_id: str = "request-1") -> str:
    if operation == "capture" and isinstance(params, Mapping):
        params = {
            **params,
            "maximum_publication_bytes": params.get(
                "maximum_publication_bytes",
                512 * 1024 * 1024,
            ),
        }
    return json.dumps(
        {
            "schema": bridge.BRIDGE_SCHEMA,
            "client_version": bridge._package_version(),
            "request_id": request_id,
            "operation": operation,
            "params": params,
        }
    )


def _inspection() -> code_mode.LiveInspection:
    return code_mode.LiveInspection(
        notebook=code_mode.NotebookInspection(
            filename="finance.py",
            path="/srv/notebooks/finance.py",
            document_sha256="a" * 64,
        ),
        globals=(
            code_mode.GlobalInspection("summary", "builtins.dict"),
            code_mode.GlobalInspection("symbol", "marimo.ui.dropdown"),
            code_mode.GlobalInspection("horizon", "marimo.ui.slider"),
        ),
        cells=(),
        controls=(
            code_mode.ControlInspection("symbol", "dropdown", ["MSFT"], False, {}),
            code_mode.ControlInspection("horizon", "slider", 30, False, {}),
        ),
    )


def _receipt(
    disposition: Literal["hit", "miss", "skipped"] = "miss",
) -> CacheAssetReceipt:
    blob = BlobAsset(
        data=b'{"rows":3}',
        media_type="application/json",
        filename="summary.json",
        metadata={
            "format_id": "json.v1",
            "metadata_json": b'{"encoding":"utf-8"}',
        },
    )
    envelope = b"native-marimo-blob-asset-envelope"
    return CacheAssetReceipt(
        asset=CacheAssetRef(
            key="project/abc/return.bin",
            sha256=hashlib.sha256(envelope).hexdigest(),
            size=len(envelope),
        ),
        envelope=envelope,
        blob=blob,
        disposition=disposition,
    )


def _install_live_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[JsonObject], list[JsonObject]]:
    inspection = _inspection()
    applied: list[JsonObject] = []
    restored: list[JsonObject] = []

    async def inspect_live() -> code_mode.LiveInspection:
        return inspection

    async def snapshot_controls(names: Iterable[str]) -> code_mode.ControlSnapshot:
        assert set(names) == {"symbol", "horizon"}
        return code_mode.ControlSnapshot(MappingProxyType({"symbol": ["MSFT"], "horizon": 30}))

    async def apply_controls(
        values: Mapping[str, object],
        sources: Iterable[object] = (),
    ) -> code_mode.AppliedControls:
        tuple(sources)
        vector = json_object(values)
        applied.append(vector)
        return code_mode.AppliedControls(values=vector, outputs={})

    async def restore_controls(
        snapshot: code_mode.ControlSnapshot,
        sources: Iterable[object] = (),
    ) -> code_mode.AppliedControls:
        tuple(sources)
        restored.append(dict(snapshot.values))
        return code_mode.AppliedControls(values=snapshot.values, outputs={})

    async def restore_cell_state(snapshot: code_mode.CellStateSnapshot) -> None:
        assert snapshot == code_mode.CellStateSnapshot(frozenset())

    monkeypatch.setattr(
        bridge,
        "require_capabilities",
        lambda: MarimoCapabilities("0.23.14", ("blob-asset",)),
    )
    monkeypatch.setattr(bridge.code_mode, "inspect_live", inspect_live)
    monkeypatch.setattr(bridge.code_mode, "snapshot_controls", snapshot_controls)
    monkeypatch.setattr(bridge.code_mode, "apply_controls", apply_controls)
    monkeypatch.setattr(bridge.code_mode, "restore_controls", restore_controls)
    monkeypatch.setattr(
        bridge.code_mode,
        "snapshot_cell_state",
        lambda: code_mode.CellStateSnapshot(frozenset()),
    )
    monkeypatch.setattr(bridge.code_mode, "restore_cell_state", restore_cell_state)
    monkeypatch.setattr(bridge, "get_code_mode_context", _LiveContext)
    return applied, restored


def test_bridge_rejects_client_kernel_version_mismatch() -> None:
    request = json.dumps(
        {
            "schema": bridge.BRIDGE_SCHEMA,
            "client_version": "different-version",
            "request_id": "request-1",
            "operation": "inspect",
            "params": {},
        }
    )

    response = json.loads(asyncio.run(bridge.dispatch_json(request)))

    assert response == {
        "schema": bridge.BRIDGE_SCHEMA,
        "request_id": "request-1",
        "ok": False,
        "error": {
            "code": "session_error",
            "message": ("marimo-export versions differ between the client and attached kernel"),
            "details": {
                "client_version": "different-version",
                "kernel_version": bridge._package_version(),
            },
        },
    }


def test_capture_keeps_full_control_state_private_and_publishes_declared_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied, restored = _install_live_seams(monkeypatch)
    original = _inspection()
    inspection = code_mode.LiveInspection(
        notebook=original.notebook,
        globals=(
            *original.globals,
            code_mode.GlobalInspection("notes", "builtins.str"),
            code_mode.GlobalInspection("password", "marimo.ui.text"),
        ),
        cells=original.cells,
        controls=(
            *original.controls,
            code_mode.ControlInspection("notes", "text", "private-note", False, {}),
            code_mode.ControlInspection("password", "text", None, True, {}),
        ),
    )
    exporter_resolutions: list[str] = []
    resolve_exporter = bridge._resolve_exporter
    receipts = iter((_receipt("miss"), _receipt("hit")))

    async def inspect_live() -> code_mode.LiveInspection:
        return inspection

    async def snapshot_controls(names: Iterable[str]) -> code_mode.ControlSnapshot:
        assert set(names) == {"symbol", "horizon", "notes", "password"}
        return code_mode.ControlSnapshot(
            json_object(
                {
                    "symbol": ["MSFT"],
                    "horizon": 30,
                    "notes": "private-note",
                    "password": "private-password",
                }
            )
        )

    def tracked_resolve_exporter(*args: Any, **kwargs: Any) -> Any:
        format_spec = args[0]
        exporter_resolutions.append(format_spec.name)
        return resolve_exporter(*args, **kwargs)

    async def project_and_cache(*args: object, **kwargs: object) -> CacheAssetReceipt:
        del args, kwargs
        return next(receipts)

    asset = _receipt().asset
    monkeypatch.setattr(bridge.code_mode, "inspect_live", inspect_live)
    monkeypatch.setattr(bridge.code_mode, "snapshot_controls", snapshot_controls)
    monkeypatch.setattr(bridge, "project_and_cache", project_and_cache)
    monkeypatch.setattr(bridge, "_resolve_exporter", tracked_resolve_exporter)
    monkeypatch.setattr(
        bridge,
        "create_ticket",
        lambda values: _Ticket("b" * 32, next(iter(values)).asset),
    )

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "variants": {
                                "current": {},
                                "aapl": {"symbol": ["AAPL"]},
                            },
                            "outputs": {
                                "summary": {
                                    "source": "summary",
                                    "formats": {"json": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                    },
                )
            )
        )
    )

    assert response["ok"] is True, response
    data = response["data"]
    assert data["ticket"] == "b" * 32
    assert data["cache"] == {"hits": 1, "misses": 1, "skipped": 0}
    assert list(data["index"]["variants"]) == ["aapl", "current"]
    assert data["index"]["variants"]["current"]["controls"] == {
        "symbol": ["MSFT"],
    }
    assert data["index"]["variants"]["aapl"]["controls"] == {
        "symbol": ["AAPL"],
    }
    assert "horizon" not in json.dumps(data["index"])
    assert "private-note" not in json.dumps(response)
    assert "private-password" not in json.dumps(response)
    entry = data["index"]["variants"]["current"]["outputs"]["summary"]["formats"]["json"]
    assert entry == {
        "format_id": "json.v1",
        "media_type": "application/json",
        "metadata": {"encoding": "utf-8"},
        "asset": asset.wire(),
    }
    assert applied == [
        {"symbol": ["AAPL"]},
        {},
    ]
    assert restored == [
        {
            "symbol": ["MSFT"],
            "horizon": 30,
            "notes": "private-note",
            "password": "private-password",
        },
        {
            "symbol": ["MSFT"],
            "horizon": 30,
            "notes": "private-note",
            "password": "private-password",
        },
        {
            "symbol": ["MSFT"],
            "horizon": 30,
            "notes": "private-note",
            "password": "private-password",
        },
    ]
    assert exporter_resolutions == ["json"]


def test_capture_preflights_builtin_availability_before_control_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied, restored = _install_live_seams(monkeypatch)
    builtin_exporter = bridge._builtin_exporter
    monkeypatch.setattr(
        bridge,
        "_builtin_exporter",
        lambda name: (
            _ExporterAvailability(
                name=name,
                version="vegalite.png.v1",
                available=False,
                extra="png",
            )
            if name == "png"
            else builtin_exporter(name)
        ),
    )

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "variants": {"aapl": {"symbol": ["AAPL"]}},
                            "outputs": {
                                "chart": {
                                    "source": "summary",
                                    "formats": {"json": {}, "png": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                    },
                )
            )
        )
    )

    assert response == {
        "schema": bridge.BRIDGE_SCHEMA,
        "request_id": "request-1",
        "ok": False,
        "error": {
            "code": "projection_error",
            "message": (
                "built-in exporter 'png' is unavailable in the attached notebook environment"
            ),
            "details": {"exporter": "png", "extra": "png"},
        },
    }
    assert applied == []
    assert restored == []


@pytest.mark.parametrize(
    ("source", "kind", "name"),
    [
        ("missing_global", "global", "missing_global"),
        ({"cell": "missing-cell"}, "cell", "missing-cell"),
    ],
)
def test_capture_preflights_named_sources_before_exporters_or_notebook_state(
    monkeypatch: pytest.MonkeyPatch,
    source: object,
    kind: str,
    name: str,
) -> None:
    applied, restored = _install_live_seams(monkeypatch)
    snapshot_called = False
    exporters_preflighted = False

    async def snapshot_controls(names: Iterable[str]) -> code_mode.ControlSnapshot:
        nonlocal snapshot_called
        tuple(names)
        snapshot_called = True
        raise AssertionError("snapshot must not run")

    async def preflight_exporters(spec: object) -> dict[object, object]:
        nonlocal exporters_preflighted
        del spec
        exporters_preflighted = True
        raise AssertionError("exporter preflight must not run")

    monkeypatch.setattr(bridge.code_mode, "snapshot_controls", snapshot_controls)
    monkeypatch.setattr(bridge, "_preflight_exporters", preflight_exporters)

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "variants": {"attempt": {"symbol": ["AAPL"]}},
                            "outputs": {
                                "typo": {
                                    "source": source,
                                    "formats": {"json": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                    },
                )
            )
        )
    )

    assert response["error"] == {
        "code": "selection_error",
        "message": f"notebook {kind} {name!r} is unavailable",
        "details": {"kind": kind, "source": name},
    }
    assert snapshot_called is False
    assert exporters_preflighted is False
    assert applied == []
    assert restored == []


def test_capture_evaluates_expressions_after_variant_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied, restored = _install_live_seams(monkeypatch)

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "variants": {"aapl": {"symbol": ["AAPL"]}},
                            "outputs": {
                                "typo": {
                                    "source": {"expression": "missing_name"},
                                    "formats": {"json": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                    },
                )
            )
        )
    )

    assert response["error"]["code"] == "selection_error"
    assert response["error"]["details"] == {
        "kind": "expression",
        "source": "missing_name",
    }
    assert applied == [{"symbol": ["AAPL"]}]
    assert len(restored) == 2


def test_cell_sources_pass_rendered_payloads_to_custom_exporters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_seams(monkeypatch)
    rendered = CellOutput(
        channel=CellChannel.OUTPUT,
        mimetype="application/json",
        data={"rows": 3},
        timestamp=99.0,
    )
    original = _inspection()
    inspection = code_mode.LiveInspection(
        notebook=original.notebook,
        globals=(
            *original.globals,
            code_mode.GlobalInspection("custom_exporter", "builtins.function"),
        ),
        cells=(
            code_mode.CellInspection(
                "cell-report",
                "report",
                "idle",
                True,
                "application/json",
            ),
        ),
        controls=original.controls,
    )
    captured: list[tuple[str, object]] = []
    receipts = iter((_receipt(), _receipt()))

    async def inspect_live() -> code_mode.LiveInspection:
        return inspection

    def resolve_exporter(
        format_spec: Any,
        live_globals: Mapping[str, object],
    ) -> Any:
        del live_globals
        name = format_spec.name

        def unused_exporter(value: object, **options: object) -> Any:
            del value, options
            raise AssertionError("projection cache seam must intercept the exporter")

        return bridge._ResolvedExporter(
            function=unused_exporter,
            reference=name,
            version="1",
        )

    async def project_and_cache(
        value: object,
        exporter: Any,
        options: Mapping[str, object],
    ) -> CacheAssetReceipt:
        assert options == {}
        captured.append((exporter.reference, value))
        return next(receipts)

    monkeypatch.setattr(bridge.code_mode, "inspect_live", inspect_live)
    monkeypatch.setattr(bridge, "get_code_mode_context", lambda: _CellLiveContext(rendered))
    monkeypatch.setattr(bridge, "_resolve_exporter", resolve_exporter)
    monkeypatch.setattr(bridge, "project_and_cache", project_and_cache)
    monkeypatch.setattr(
        bridge,
        "create_ticket",
        lambda values: _Ticket("b" * 32, next(iter(values)).asset),
    )

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "outputs": {
                                "report": {
                                    "source": {"cell": "report"},
                                    "formats": {
                                        "imported": {
                                            "exporter": {
                                                "import": "project.exporters:emit",
                                                "version": "1",
                                            }
                                        },
                                        "variable": {
                                            "exporter": {
                                                "variable": "custom_exporter",
                                                "version": "1",
                                            }
                                        },
                                    },
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                    },
                )
            )
        )
    )

    assert response["ok"] is True, response
    assert captured == [
        ("imported", {"rows": 3}),
        ("variable", {"rows": 3}),
    ]
    assert all(not isinstance(value, CellOutput) for _, value in captured)


def test_capture_rejects_sensitive_variant_before_snapshot_or_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied, restored = _install_live_seams(monkeypatch)
    original = _inspection()
    inspection = code_mode.LiveInspection(
        notebook=original.notebook,
        globals=(
            *original.globals,
            code_mode.GlobalInspection("password", "marimo.ui.form"),
        ),
        cells=original.cells,
        controls=(
            *original.controls,
            code_mode.ControlInspection("password", "text", None, True, {}),
        ),
    )
    snapshot_called = False
    projected = False

    async def inspect_live() -> code_mode.LiveInspection:
        return inspection

    async def snapshot_controls(names: Iterable[str]) -> code_mode.ControlSnapshot:
        nonlocal snapshot_called
        tuple(names)
        snapshot_called = True
        raise AssertionError("snapshot must not run")

    async def project_and_cache(*args: object, **kwargs: object) -> CacheAssetReceipt:
        nonlocal projected
        del args, kwargs
        projected = True
        return _receipt()

    monkeypatch.setattr(bridge.code_mode, "inspect_live", inspect_live)
    monkeypatch.setattr(bridge.code_mode, "snapshot_controls", snapshot_controls)
    monkeypatch.setattr(bridge, "project_and_cache", project_and_cache)

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "variants": {"attempt": {"password": "private-password"}},
                            "outputs": {
                                "summary": {
                                    "source": "summary",
                                    "formats": {"json": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                    },
                )
            )
        )
    )

    assert response["error"] == {
        "code": "selection_error",
        "message": "sensitive controls cannot be used as variant inputs",
        "details": {"controls": ["password"]},
    }
    assert "private-password" not in json.dumps(response)
    assert snapshot_called is False
    assert projected is False
    assert applied == []
    assert restored == []


def test_capture_publishes_empty_controls_when_spec_declares_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_seams(monkeypatch)

    async def project_and_cache(*args: object, **kwargs: object) -> CacheAssetReceipt:
        del args, kwargs
        return _receipt()

    monkeypatch.setattr(bridge, "project_and_cache", project_and_cache)
    monkeypatch.setattr(
        bridge,
        "create_ticket",
        lambda values: _Ticket("b" * 32, next(iter(values)).asset),
    )

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "outputs": {
                                "summary": {
                                    "source": "summary",
                                    "formats": {"json": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                    },
                )
            )
        )
    )

    current = response["data"]["index"]["variants"]["current"]
    assert current["controls"] == {}
    assert "symbol" not in current["controls"]
    assert "horizon" not in current["controls"]


def test_capture_preserves_projection_error_when_restore_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_seams(monkeypatch)
    restore_calls = 0
    cell_restore_calls = 0
    snapshot_calls = 0

    async def snapshot_controls(names: Iterable[str]) -> code_mode.ControlSnapshot:
        nonlocal snapshot_calls
        assert set(names) == {"symbol", "horizon"}
        snapshot_calls += 1
        if snapshot_calls == 2:
            assert cell_restore_calls == 1
        return code_mode.ControlSnapshot(MappingProxyType({"symbol": ["MSFT"], "horizon": 30}))

    async def restore_controls(
        snapshot: code_mode.ControlSnapshot,
        sources: Iterable[object] = (),
    ) -> code_mode.AppliedControls:
        nonlocal restore_calls
        del sources
        restore_calls += 1
        if restore_calls > 1:
            raise RuntimeError("restore failed")
        return code_mode.AppliedControls(values=snapshot.values, outputs={})

    async def project_and_cache(*args: object, **kwargs: object) -> CacheAssetReceipt:
        del args, kwargs
        raise ProjectionError(
            "projection failed",
            details={"output": "summary"},
        )

    async def restore_cell_state(snapshot: code_mode.CellStateSnapshot) -> None:
        nonlocal cell_restore_calls
        del snapshot
        cell_restore_calls += 1
        raise RuntimeError("cell state restore failed")

    monkeypatch.setattr(bridge.code_mode, "restore_controls", restore_controls)
    monkeypatch.setattr(bridge.code_mode, "restore_cell_state", restore_cell_state)
    monkeypatch.setattr(bridge.code_mode, "snapshot_controls", snapshot_controls)
    monkeypatch.setattr(bridge, "project_and_cache", project_and_cache)

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "outputs": {
                                "summary": {
                                    "source": "summary",
                                    "formats": {"json": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                    },
                )
            )
        )
    )

    assert response == {
        "schema": bridge.BRIDGE_SCHEMA,
        "request_id": "request-1",
        "ok": False,
        "error": {
            "code": "projection_error",
            "message": "projection failed",
            "details": {
                "output": "summary",
                "restoration": {
                    "failures": [
                        {
                            "operation": "restore_controls",
                            "exception_type": "RuntimeError",
                            "message": "control restoration failed",
                        },
                        {
                            "operation": "restore_cell_state",
                            "exception_type": "RuntimeError",
                            "message": "cell state restore failed",
                        },
                    ],
                    "expected_controls": {},
                    "best_known_controls": {},
                    "controls_observed_after_cleanup": True,
                },
            },
        },
    }
    assert restore_calls == 2
    assert cell_restore_calls == 1
    assert snapshot_calls == 2


def test_capture_rejects_publication_index_over_caller_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_seams(monkeypatch)
    ticket_created = False

    async def project_and_cache(*args: object, **kwargs: object) -> CacheAssetReceipt:
        del args, kwargs
        return _receipt()

    def create_ticket(values: Iterable[CacheAssetReceipt]) -> _Ticket:
        nonlocal ticket_created
        tuple(values)
        ticket_created = True
        return _Ticket("b" * 32, _receipt().asset)

    monkeypatch.setattr(bridge, "project_and_cache", project_and_cache)
    monkeypatch.setattr(bridge, "create_ticket", create_ticket)

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "outputs": {
                                "summary": {
                                    "source": "summary",
                                    "formats": {"json": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 1,
                    },
                )
            )
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "capture_error"
    assert response["error"]["message"] == ("publication index exceeds maximum_index_bytes")
    assert response["error"]["details"]["limit"] == 1
    assert response["error"]["details"]["size"] > 1
    assert ticket_created is False


def test_capture_rejects_publication_closure_over_caller_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_seams(monkeypatch)
    ticket_created = False

    async def project_and_cache(*args: object, **kwargs: object) -> CacheAssetReceipt:
        del args, kwargs
        return _receipt()

    def create_ticket(values: Iterable[CacheAssetReceipt]) -> _Ticket:
        nonlocal ticket_created
        tuple(values)
        ticket_created = True
        return _Ticket("b" * 32, _receipt().asset)

    monkeypatch.setattr(bridge, "project_and_cache", project_and_cache)
    monkeypatch.setattr(bridge, "create_ticket", create_ticket)

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "outputs": {
                                "summary": {
                                    "source": "summary",
                                    "formats": {"json": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                        "maximum_publication_bytes": 1,
                    },
                )
            )
        )
    )

    assert response["ok"] is False
    assert response["error"]["code"] == "transfer_error"
    assert response["error"]["message"] == ("publication exceeds maximum_publication_bytes")
    assert response["error"]["details"]["limit"] == 1
    assert ticket_created is False


def test_publication_size_counts_duplicate_cache_keys_once() -> None:
    receipt = _receipt()

    bridge._validate_publication_size(
        10,
        [receipt, receipt],
        10 + receipt.asset.size,
    )


def test_capture_reports_control_mismatch_after_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_seams(monkeypatch)
    snapshot_calls = 0

    async def snapshot_controls(names: Iterable[str]) -> code_mode.ControlSnapshot:
        nonlocal snapshot_calls
        assert set(names) == {"symbol", "horizon"}
        snapshot_calls += 1
        symbol = ["MSFT"] if snapshot_calls == 1 else ["GOOG"]
        return code_mode.ControlSnapshot(json_object({"symbol": symbol, "horizon": 30}))

    async def project_and_cache(*args: object, **kwargs: object) -> CacheAssetReceipt:
        del args, kwargs
        return _receipt()

    monkeypatch.setattr(bridge.code_mode, "snapshot_controls", snapshot_controls)
    monkeypatch.setattr(bridge, "project_and_cache", project_and_cache)

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "outputs": {
                                "summary": {
                                    "source": "summary",
                                    "formats": {"json": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                    },
                )
            )
        )
    )

    restoration = response["error"]["details"]["restoration"]
    assert response["error"]["code"] == "capture_error"
    assert restoration["failures"] == [
        {
            "operation": "verify_controls",
            "exception_type": "ControlStateMismatch",
            "message": "restored controls differ from the captured input vector",
            "details": {
                "expected": {},
                "actual": {},
                "mismatched_controls": [{"name": "symbol", "sensitive": False}],
                "mismatched_controls_truncated": False,
            },
        }
    ]
    assert restoration["best_known_controls"] == {}
    assert restoration["controls_observed_after_cleanup"] is True


def test_capture_bounds_mismatch_names_and_never_reports_control_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_seams(monkeypatch)
    controls = tuple(
        code_mode.ControlInspection(
            f"control_{index:03d}",
            "form" if index == 0 else "text",
            None if index == 0 else f"inspect-private-{index:03d}",
            index == 0,
            {},
        )
        for index in range(70)
    )
    inspection = code_mode.LiveInspection(
        notebook=_inspection().notebook,
        globals=(code_mode.GlobalInspection("summary", "builtins.dict"),),
        cells=(),
        controls=controls,
    )
    snapshot_calls = 0

    async def inspect_live() -> code_mode.LiveInspection:
        return inspection

    async def snapshot_controls(names: Iterable[str]) -> code_mode.ControlSnapshot:
        nonlocal snapshot_calls
        assert set(names) == {control.name for control in controls}
        snapshot_calls += 1
        state = "before" if snapshot_calls == 1 else "after"
        return code_mode.ControlSnapshot(
            json_object({control.name: f"{state}-private-{control.name}" for control in controls})
        )

    async def project_and_cache(*args: object, **kwargs: object) -> CacheAssetReceipt:
        del args, kwargs
        return _receipt()

    monkeypatch.setattr(bridge.code_mode, "inspect_live", inspect_live)
    monkeypatch.setattr(bridge.code_mode, "snapshot_controls", snapshot_controls)
    monkeypatch.setattr(bridge, "project_and_cache", project_and_cache)

    response = json.loads(
        asyncio.run(
            bridge.dispatch_json(
                _request(
                    "capture",
                    {
                        "spec": {
                            "schema": "marimo-export.spec.v1",
                            "outputs": {
                                "summary": {
                                    "source": "summary",
                                    "formats": {"json": {}},
                                }
                            },
                        },
                        "maximum_index_bytes": 16 * 1024 * 1024,
                    },
                )
            )
        )
    )

    encoded = json.dumps(response)
    details = response["error"]["details"]["restoration"]["failures"][0]["details"]
    assert response["error"]["code"] == "capture_error"
    assert details["expected"] == {}
    assert details["actual"] == {}
    assert len(details["mismatched_controls"]) == 64
    assert details["mismatched_controls"][0] == {
        "name": "control_000",
        "sensitive": True,
    }
    assert details["mismatched_controls"][-1] == {
        "name": "control_063",
        "sensitive": False,
    }
    assert details["mismatched_controls_truncated"] is True
    assert "before-private" not in encoded
    assert "after-private" not in encoded
    assert "inspect-private" not in encoded


def test_inspect_and_release_use_strict_bridge_envelopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_live_seams(monkeypatch)
    monkeypatch.setattr(
        bridge,
        "_BUILTIN_EXPORTERS",
        (
            _ExporterAvailability("text", "text.v1", True, None),
            _ExporterAvailability("arrow", "dataframe.arrow.v1", False, "dataframe"),
        ),
    )
    monkeypatch.setattr(bridge, "_package_version", lambda: "1.2.3")
    released: list[str] = []
    monkeypatch.setattr(
        bridge,
        "release",
        lambda ticket: released.append(ticket) is None,
    )

    inspect = json.loads(asyncio.run(bridge.dispatch_json(_request("inspect", {}))))
    release = json.loads(
        asyncio.run(bridge.dispatch_json(_request("release", {"ticket": "c" * 32})))
    )
    duplicate = (
        '{"schema":"marimo-export.bridge.v1","request_id":"first",'
        '"request_id":"second","operation":"inspect","params":{}}'
    )
    invalid = json.loads(asyncio.run(bridge.dispatch_json(duplicate)))

    assert set(inspect["data"]) == {
        "notebook",
        "globals",
        "cells",
        "controls",
        "builtin_exporters",
        "marimo_version",
        "marimo_export_version",
    }
    assert inspect["data"]["marimo_version"] == "0.23.14"
    assert inspect["data"]["marimo_export_version"] == "1.2.3"
    assert inspect["data"]["globals"] == [
        {"name": "horizon", "python_type": "marimo.ui.slider"},
        {"name": "summary", "python_type": "builtins.dict"},
        {"name": "symbol", "python_type": "marimo.ui.dropdown"},
    ]
    assert inspect["data"]["builtin_exporters"] == [
        {
            "name": "arrow",
            "format_id": "dataframe.arrow.v1",
            "available": False,
            "extra": "dataframe",
        },
        {
            "name": "text",
            "format_id": "text.v1",
            "available": True,
            "extra": None,
        },
    ]
    assert release["data"] == {"released": True}
    assert released == ["c" * 32]
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "spec_error"
    assert "duplicate key" in invalid["error"]["message"]
