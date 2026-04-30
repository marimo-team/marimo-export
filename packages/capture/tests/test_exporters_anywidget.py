from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from moexport.artifacts import Artifact, ArtifactData, JsonObject
from moexport.blobs import BlobContent, BlobRef, ContentAddressedBlobStore
from moexport.exporters import BundleExporterContext

anywidget_exporters = pytest.importorskip("moexport.exporters.anywidget")


class FakeAnyWidget:
    pass


class CapturingExporterContext:
    def __init__(
        self,
        *,
        scenario_id: str = "default",
        value_name: str = "widget",
        format_name: str = "bundle",
    ) -> None:
        self.scenario_id = scenario_id
        self.value_name = value_name
        self.format_name = format_name
        self.blobs: dict[str, bytes] = {}

    def write_blob(
        self,
        name: str,
        data: BlobContent,
        *,
        media_type: str | None = None,
    ) -> BlobRef:
        href = f"memory://{name}"
        blob = bytes(data)
        self.blobs[href] = blob
        return BlobRef(
            href=href,
            media_type=media_type,
            size=len(blob),
            sha256=f"sha-{name}",
        )

    def artifact(
        self,
        *,
        format: str,
        files: dict[str, BlobRef],
        entry: str | None = None,
        media_type: str | None = None,
        metadata: JsonObject | None = None,
    ) -> Artifact:
        return Artifact(
            format=format,
            media_type=media_type,
            data=ArtifactData(files=files, entry=entry),
            metadata=metadata,
        )


class DemoWidget(FakeAnyWidget):
    _esm = "export function render() {}"
    _css = ".widget { color: red; }"

    def __init__(self, *, count: int = 1) -> None:
        self._esm = self._esm
        self._css = self._css
        self._anywidget_id = "demo.DemoWidget"
        self.count = count

    def get_state(self, *, drop_defaults: bool = False) -> dict[str, object]:
        self.drop_defaults = drop_defaults
        return {
            "_anywidget_id": "ignored",
            "_esm": "ignored",
            "_model_name": "ignored",
            "count": self.count,
            "nested": [b"de"],
            "payload": b"abc",
        }


@pytest.fixture(autouse=True)
def fake_anywidget_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "anywidget",
        SimpleNamespace(AnyWidget=FakeAnyWidget),
    )


def _descriptor(artifact: Any, ctx: CapturingExporterContext) -> dict[str, Any]:
    blob = artifact.data.files["descriptor"]
    return json.loads(ctx.blobs[blob.href])


def _state_value(
    artifact: Any,
    ctx: CapturingExporterContext,
    key: str,
) -> Any:
    blob = artifact.data.files[f"state.{key}"]
    return json.loads(ctx.blobs[blob.href])


def test_bundle_exports_widget_assets_state_and_buffers() -> None:
    ctx = CapturingExporterContext()

    artifact = anywidget_exporters.bundle(DemoWidget(count=7), ctx)

    assert artifact.format == "anywidget.bundle.v1"
    assert artifact.media_type == "application/vnd.moexport.anywidget+json"
    assert artifact.data.type == "bundle"
    assert artifact.data.entry == "descriptor"
    assert set(artifact.data.files) == {
        "buffer_0",
        "buffer_1",
        "descriptor",
        "module",
        "state.count",
        "state.nested",
        "style",
    }
    assert artifact.metadata == {
        "anywidget_id": "demo.DemoWidget",
        "buffer_count": 2,
        "has_style": True,
        "state_keys": ["count", "nested"],
    }

    descriptor = _descriptor(artifact, ctx)
    assert descriptor["schema"] == "moexport.anywidget.bundle.v1"
    assert descriptor["anywidget_id"] == "demo.DemoWidget"
    assert sorted(descriptor["state"]) == ["count", "nested"]
    assert descriptor["state"]["count"]["media_type"] == "application/json"
    assert _state_value(artifact, ctx, "count") == 7
    assert _state_value(artifact, ctx, "nested") == [None]
    assert descriptor["assets"]["module"]["media_type"] == "text/javascript"
    assert descriptor["assets"]["style"]["media_type"] == "text/css"
    assert descriptor["buffers"][0]["path"] == ["nested", 0]
    assert descriptor["buffers"][1]["path"] == ["payload"]

    first_buffer = artifact.data.files["buffer_0"]
    second_buffer = artifact.data.files["buffer_1"]
    assert ctx.blobs[first_buffer.href] == b"de"
    assert ctx.blobs[second_buffer.href] == b"abc"


def test_bundle_accepts_marimo_anywidget_wrapper() -> None:
    class Wrapper:
        def __init__(self, widget: DemoWidget) -> None:
            self.widget = widget
            self.synced = False

        def _ensure_widget_synced(self) -> None:
            self.synced = True

    wrapper = Wrapper(DemoWidget(count=3))
    ctx = CapturingExporterContext()

    artifact = anywidget_exporters.bundle(wrapper, ctx)

    assert wrapper.synced
    assert _state_value(artifact, ctx, "count") == 3


def test_bundle_dedupes_identical_widget_files_across_value_names(
    tmp_path: Path,
) -> None:
    store = ContentAddressedBlobStore(tmp_path)
    first_ctx = BundleExporterContext(
        scenario_id="default",
        value_name="left_widget",
        format_name="bundle",
        blob_store=store,
    )
    second_ctx = BundleExporterContext(
        scenario_id="default",
        value_name="right_widget",
        format_name="bundle",
        blob_store=store,
    )

    first = anywidget_exporters.bundle(DemoWidget(count=1), first_ctx)
    second = anywidget_exporters.bundle(DemoWidget(count=1), second_ctx)

    assert first.data.files["descriptor"].href == second.data.files["descriptor"].href
    assert first.data.files["module"].href == second.data.files["module"].href
    assert first.data.files["style"].href == second.data.files["style"].href
    assert first.data.files["state.count"].href == second.data.files["state.count"].href
    assert (
        first.data.files["state.nested"].href == second.data.files["state.nested"].href
    )
    assert first.data.files["buffer_0"].href == second.data.files["buffer_0"].href
    assert (
        len([path for path in (tmp_path / "blobs").rglob("*") if path.is_file()]) == 7
    )


def test_bundle_rejects_relative_frontend_references() -> None:
    class ImportWidget(DemoWidget):
        _esm = 'import "./dep.js";\nexport function render() {}'
        _css = ""

    with pytest.raises(ValueError, match="relative imports"):
        anywidget_exporters.bundle(ImportWidget(), CapturingExporterContext())

    class CssWidget(DemoWidget):
        _esm = "export function render() {}"
        _css = '.widget { background-image: url("./bg.png"); }'

    with pytest.raises(ValueError, match=r"relative url\(\.\.\.\)"):
        anywidget_exporters.bundle(CssWidget(), CapturingExporterContext())


def test_bundle_rejects_non_widget_and_class_input() -> None:
    with pytest.raises(TypeError, match="AnyWidget export requires"):
        anywidget_exporters.bundle(object(), CapturingExporterContext())

    with pytest.raises(TypeError, match="instance, not a class"):
        anywidget_exporters.bundle(DemoWidget, CapturingExporterContext())


def test_bundle_validates_options_with_pydantic() -> None:
    with pytest.raises(ValidationError, match="extra"):
        anywidget_exporters.bundle(
            DemoWidget(),
            CapturingExporterContext(),
            unknown=True,
        )
