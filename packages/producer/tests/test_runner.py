from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import hashlib
import json
import sys
import threading
import types
from pathlib import Path
from typing import Any

import pytest
from marimo._ast.app_config import _AppConfig
from marimo._config.config import DEFAULT_CONFIG
from marimo._messaging.notification import ModelClose, ModelLifecycleNotification
from marimo._messaging.serde import deserialize_kernel_message
from marimo._messaging.types import KernelMessage, KernelStreams, NoopStream, Stream
from marimo._runtime.cell_lifecycle_registry import CellLifecycleRegistry
from marimo._runtime.commands import AppMetadata
from marimo._runtime.context import get_context
from marimo._runtime.executor.lifecycles import cached as cached_module
from marimo._runtime.kernel_lifecycle import KernelArgs, kernel_session
from marimo._save.stubs import CUSTOM_STUBS
from marimo._session.model import SessionMode
from marimo_export._marimo.cache import read_payload
from marimo_export._marimo.context import NotebookSnapshot
from marimo_export._marimo.runner import run_scenario_in_child
from marimo_export.plan import decode_plan
from marimo_export.projection.synthetic_cells import projection_binding


class _RecordingStream(Stream):
    def __init__(self) -> None:
        self.messages: list[KernelMessage] = []
        self.stop_calls = 0

    def write(self, data: KernelMessage) -> None:
        self.messages.append(data)

    def stop(self) -> None:
        self.stop_calls += 1

    def copy_for_thread(self) -> Stream:
        return self


def test_html_projection_inlines_virtual_media_on_cold_and_warm_runs(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    image = mo.image(b"image bytes", alt="embedded asset")
    return (image,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {"image": {"source": "image", "formats": {"html": {}}}},
    }

    _, cold = _execute(tmp_path, source, plan)
    _, warm = _execute(tmp_path, source, plan)

    payload = cold[("image", "html")]
    assert payload == warm[("image", "html")]
    assert b"@file/" not in payload
    assert b"data:image/png;base64,aW1hZ2UgYnl0ZXM=" in payload


def test_html_projection_hit_survives_cached_virtual_media(tmp_path: Path) -> None:
    counter = tmp_path / "html-exporter-calls"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    image = mo.image(b"image bytes", alt="embedded asset")
    return (image,)

@app.cell
def _():
    from pathlib import Path
    from marimo_export.projection.exporters import html
    def export_image(value):
        counter = Path({str(counter)!r})
        calls = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(calls + 1))
        return html(value)
    return (export_image,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {
            "image": {
                "source": "image",
                "formats": {"html": {"exporter": {"definition": "export_image", "version": "1"}}},
            }
        },
    }

    _, cold = _execute(tmp_path, source, plan)
    _, warm = _execute(tmp_path, source, plan)

    assert cold[("image", "html")] == warm[("image", "html")]
    assert b"data:image/png;base64,aW1hZ2UgYnl0ZXM=" in warm[("image", "html")]
    assert counter.read_text() == "1"


def test_changed_html_projection_recovers_cached_virtual_media(tmp_path: Path) -> None:
    counter = tmp_path / "html-exporter-calls"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    image = mo.image(b"image bytes", alt="embedded asset")
    return (image,)

@app.cell
def _():
    from pathlib import Path
    from marimo_export.projection.exporters import html
    def export_image(value):
        counter = Path({str(counter)!r})
        calls = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(calls + 1))
        return html(value)
    return (export_image,)

if __name__ == "__main__":
    app.run()
"""

    def plan(version: str) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "image": {
                    "source": "image",
                    "formats": {
                        "html": {
                            "exporter": {
                                "definition": "export_image",
                                "version": version,
                            }
                        }
                    },
                }
            },
        }

    _, cold = _execute(tmp_path, source, plan("1"))
    _, warm = _execute(tmp_path, source, plan("2"))

    assert b"data:image/png;base64,aW1hZ2UgYnl0ZXM=" in cold[("image", "html")]
    assert b"data:image/png;base64,aW1hZ2UgYnl0ZXM=" in warm[("image", "html")]
    assert counter.read_text() == "2"


def test_changed_projection_repairs_html_closed_over_by_functions(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    image = mo.image(b"image bytes", alt="embedded asset")
    return (image,)

@app.cell
def _(image):
    def get_image():
        return image
    return (get_image,)

@app.cell
def _(image):
    from marimo_export.projection.exporters import html
    def export_image(value):
        del value
        return html(image)
    return (export_image,)

@app.cell
def _():
    marker = "ignored"
    return (marker,)

if __name__ == "__main__":
    app.run()
"""

    def plan(version: str) -> object:
        expression = "get_image()" if version == "1" else "get_image() if True else None"
        return {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "expression_image": {
                    "source": {"expression": expression},
                    "formats": {"html": {}},
                },
                "exporter_image": {
                    "source": "marker",
                    "formats": {
                        "html": {
                            "exporter": {
                                "definition": "export_image",
                                "version": version,
                            }
                        }
                    },
                },
            },
        }

    _, cold = _execute(tmp_path, source, plan("1"))
    _, warm = _execute(tmp_path, source, plan("2"))

    for key in (("expression_image", "html"), ("exporter_image", "html")):
        assert cold[key] == warm[key]
        assert b"@file/" not in warm[key]
        assert b"data:image/png;base64,aW1hZ2UgYnl0ZXM=" in warm[key]


def test_html_media_repair_materializes_unpicklable_ancestors(tmp_path: Path) -> None:
    counter = tmp_path / "html-exporter-calls"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import threading
    return (threading,)

@app.cell
def _(threading):
    class Asset:
        def __init__(self):
            self.data = b"image bytes"
            self.lock = threading.Lock()
    asset = Asset()
    return (asset,)

@app.cell
def _(asset):
    import marimo as mo
    image = mo.image(asset.data, alt="embedded asset")
    return (image,)

@app.cell
def _():
    from pathlib import Path
    from marimo_export.projection.exporters import html
    def export_image(value):
        counter = Path({str(counter)!r})
        calls = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(calls + 1))
        return html(value)
    return (export_image,)

if __name__ == "__main__":
    app.run()
"""

    def plan(version: str) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "image": {
                    "source": "image",
                    "formats": {
                        "html": {
                            "exporter": {
                                "definition": "export_image",
                                "version": version,
                            }
                        }
                    },
                }
            },
        }

    _, cold = _execute(tmp_path, source, plan("1"))
    _, warm = _execute(tmp_path, source, plan("2"))

    assert b"data:image/png;base64,aW1hZ2UgYnl0ZXM=" in cold[("image", "html")]
    assert b"data:image/png;base64,aW1hZ2UgYnl0ZXM=" in warm[("image", "html")]
    assert counter.read_text() == "2"


@pytest.mark.parametrize(
    ("bridge", "source_name"),
    [
        (
            """@app.cell
def _(image):
    alias = image
    return (alias,)
""",
            "alias",
        ),
        (
            """@app.cell
def _(image):
    images = [image]
    return (images,)
""",
            "images",
        ),
    ],
)
def test_html_media_repair_follows_aliases_and_containers(
    tmp_path: Path,
    bridge: str,
    source_name: str,
) -> None:
    counter = tmp_path / "html-exporter-calls"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    image = mo.image(b"image bytes", alt="embedded asset")
    return (image,)

{bridge}
@app.cell
def _():
    from pathlib import Path
    from marimo_export.projection.exporters import html
    def export_image(value):
        counter = Path({str(counter)!r})
        calls = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(calls + 1))
        image = value[0] if isinstance(value, list) else value
        return html(image)
    return (export_image,)

if __name__ == "__main__":
    app.run()
"""

    def plan(version: str) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "image": {
                    "source": source_name,
                    "formats": {
                        "html": {
                            "exporter": {
                                "definition": "export_image",
                                "version": version,
                            }
                        }
                    },
                }
            },
        }

    _execute(tmp_path, source, plan("1"))
    _, warm = _execute(tmp_path, source, plan("2"))

    assert b"data:image/png;base64,aW1hZ2UgYnl0ZXM=" in warm[("image", "html")]
    assert counter.read_text() == "2"


def test_html_projection_does_not_mutate_marimo_custom_stub_registry(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    markup = mo.md("**portable**")
    return (markup,)

if __name__ == "__main__":
    app.run()
"""
    before = dict(CUSTOM_STUBS)

    _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {"markup": {"source": "markup", "formats": {"html": {}}}},
        },
    )

    assert before == CUSTOM_STUBS


def test_anywidget_projection_uses_native_cache_for_canonical_payload_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marimo_export._marimo import anywidget as anywidget_module
    from marimo_export.projection import synthetic_cells

    calls = {"prepare": 0, "project": 0}
    original_prepare = synthetic_cells.prepare_anywidget
    original_project = synthetic_cells.project_prepared_anywidget

    def counted_prepare(value: object) -> bytes:
        calls["prepare"] += 1
        return original_prepare(value)

    def counted_project(*args: Any, **kwargs: Any):
        calls["project"] += 1
        return original_project(*args, **kwargs)

    monkeypatch.setattr(synthetic_cells, "prepare_anywidget", counted_prepare)
    monkeypatch.setattr(
        synthetic_cells,
        "project_prepared_anywidget",
        counted_project,
    )

    source_template = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    seed = 7
    return (seed,)

@app.cell
def _(seed):
    import anywidget
    import traitlets

    class Child(anywidget.AnyWidget):
        _esm = "export function render() {}"
        value = traitlets.Int().tag(sync=True)

    class Parent(anywidget.AnyWidget):
        _esm = __PARENT_ESM__
        _css = __PARENT_CSS__
        child = traitlets.Instance(Child).tag(sync=True)

    child = Child(value=seed)
    parent = Parent(child=child)
    return child, parent

if __name__ == "__main__":
    app.run()
"""

    def source(esm: str, css: str) -> str:
        return source_template.replace("__PARENT_ESM__", repr(esm)).replace(
            "__PARENT_CSS__", repr(css)
        )

    def plan(seed: int) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "inputs": {"seed": {"definition": "seed", "default": seed}},
            "outputs": {
                "widget": {
                    "source": "parent",
                    "formats": {"anywidget": {}},
                }
            },
        }

    base_esm = "export function render() {}"
    base_css = ".parent { color: rebeccapurple; }"
    _, cold = _execute(tmp_path, source(base_esm, base_css), plan(7))
    _, warm = _execute(tmp_path, source(base_esm, base_css), plan(7))
    _, state_changed = _execute(tmp_path, source(base_esm, base_css), plan(8))
    _, esm_changed = _execute(
        tmp_path,
        source('export function render({ el }) { el.dataset.variant = "changed"; }', base_css),
        plan(7),
    )
    _, css_changed = _execute(
        tmp_path,
        source(base_esm, ".parent { color: darkcyan; }"),
        plan(7),
    )

    assert cold[("widget", "anywidget")] == warm[("widget", "anywidget")]
    changed_payloads = {
        state_changed[("widget", "anywidget")],
        esm_changed[("widget", "anywidget")],
        css_changed[("widget", "anywidget")],
    }
    assert cold[("widget", "anywidget")] not in changed_payloads
    assert len(changed_payloads) == 3
    assert calls == {"prepare": 5, "project": 4}
    document = json.loads(cold[("widget", "anywidget")])
    assert document["rootModelId"] == "model-0"
    assert len(document["modelNotifications"]) >= 2
    assert "anywidget:model-1" in json.dumps(document["modelNotifications"][0])
    assert document["schema"] == anywidget_module.ANYWIDGET_PAYLOAD_SCHEMA


def test_anywidget_capture_detaches_retained_comm_after_failure(tmp_path: Path) -> None:
    probe_name = "_marimo_export_retained_widget_failure"
    probe: Any = types.ModuleType(probe_name)
    probe.comm = None
    probe.started = False
    sys.modules[probe_name] = probe
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import anywidget
    import traitlets
    import {probe_name} as probe
    from marimo._plugins.ui._impl.anywidget.init import init_marimo_widget
    from marimo._plugins.ui._impl.comm import MarimoComm

    class RetainedWidget(anywidget.AnyWidget):
        _esm = "export function render() {{}}"
        value = traitlets.Int(1).tag(sync=True)

    widget = RetainedWidget()
    if not isinstance(widget.comm, MarimoComm):
        init_marimo_widget(widget)
    probe.comm = widget.comm
    return probe, widget

@app.cell
async def _(probe, widget):
    probe.started = True
    raise RuntimeError("scenario failed")
    return

if __name__ == "__main__":
    app.run()
"""
    notebook_directory = tmp_path / "failure"
    notebook_directory.mkdir()
    path = notebook_directory / "notebook.py"
    encoded = source.encode()
    path.write_bytes(encoded)
    snapshot = NotebookSnapshot(
        name=path.name,
        source_sha256=hashlib.sha256(encoded).hexdigest(),
        path=path,
        source=encoded,
    )
    plan = decode_plan(
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {"widget": {"source": "widget", "formats": {"anywidget": {}}}},
        }
    )
    stream = _RecordingStream()
    args = KernelArgs(
        streams=KernelStreams(stream=stream, stdout=None, stderr=None, stdin=None),
        debugger=None,
        configs={},
        app_metadata=AppMetadata(
            query_params={},
            cli_args={},
            app_config=_AppConfig(),
            filename=str(path),
            argv=[],
        ),
        user_config=copy.deepcopy(DEFAULT_CONFIG),
        mode=SessionMode.EDIT,
        control_queue=asyncio.Queue(),
        set_ui_element_queue=asyncio.Queue(),
        virtual_file_storage="shared_memory",
    )

    previous_argv = sys.argv
    try:
        with kernel_session(args) as (_, context):
            with pytest.raises(RuntimeError, match="scenario failed"):
                asyncio.run(run_scenario_in_child(plan, plan.scenarios[0], snapshot))

            assert context.children == []
            assert context.app_kernel_runner_registry.size == 0
            notifications = [deserialize_kernel_message(message) for message in stream.messages]
            assert any(
                isinstance(notification, ModelLifecycleNotification)
                and isinstance(notification.message, ModelClose)
                for notification in notifications
            )
            retained_comm = probe.comm
            assert retained_comm is not None
            forwarded = len(stream.messages)
            retained_comm.send({"method": "custom", "content": {"late": True}})
            assert len(stream.messages) == forwarded
            assert stream.stop_calls == 0
    finally:
        sys.argv = previous_argv
        sys.modules.pop(probe_name, None)

    assert stream.stop_calls == 1


def test_anywidget_capture_detaches_clones_after_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marimo_export._marimo import runner as runner_module
    from marimo_export._marimo.anywidget import install_anywidget_capture

    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    value = 1
    return (value,)

if __name__ == "__main__":
    app.run()
"""
    path = tmp_path / "notebook.py"
    encoded = source.encode()
    path.write_bytes(encoded)
    snapshot = NotebookSnapshot(
        name=path.name,
        source_sha256=hashlib.sha256(encoded).hexdigest(),
        path=path,
        source=encoded,
    )
    plan = decode_plan(
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {"value": {"source": "value", "formats": {"json": {}}}},
        }
    )
    retained: dict[str, Stream] = {}

    async def cancel_after_capture(runner: Any, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        install_anywidget_capture(runner)
        retained["stream"] = runner._runtime_context.stream.copy_for_thread()
        raise asyncio.CancelledError

    monkeypatch.setattr(runner_module, "_execute_scenario", cancel_after_capture)
    stream = _RecordingStream()
    args = KernelArgs(
        streams=KernelStreams(stream=stream, stdout=None, stderr=None, stdin=None),
        debugger=None,
        configs={},
        app_metadata=AppMetadata(
            query_params={},
            cli_args={},
            app_config=_AppConfig(),
            filename=str(path),
            argv=[],
        ),
        user_config=copy.deepcopy(DEFAULT_CONFIG),
        mode=SessionMode.EDIT,
        control_queue=asyncio.Queue(),
        set_ui_element_queue=asyncio.Queue(),
        virtual_file_storage="shared_memory",
    )

    with kernel_session(args) as (_, context):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(run_scenario_in_child(plan, plan.scenarios[0], snapshot))
        assert context.children == []
        assert context.app_kernel_runner_registry.size == 0
        retained["stream"].write(KernelMessage(b"late"))
        assert stream.messages == []
        assert stream.stop_calls == 0

    assert stream.stop_calls == 1


def test_html_content_changes_projection_cache_identity(tmp_path: Path) -> None:
    counter = tmp_path / "html-exporter-calls"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    text = "first"
    return (text,)

@app.cell
def _(text):
    import marimo as mo
    markup = mo.md(text)
    return (markup,)

@app.cell
def _():
    from pathlib import Path
    from marimo_export.projection.exporters import html
    def export_html(value):
        counter = Path({str(counter)!r})
        calls = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(calls + 1))
        return html(value)
    return (export_html,)

if __name__ == "__main__":
    app.run()
"""

    def plan(text: str) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "inputs": {"text": {"definition": "text", "default": text}},
            "outputs": {
                "markup": {
                    "source": "markup",
                    "formats": {
                        "html": {
                            "exporter": {
                                "definition": "export_html",
                                "version": "1",
                            }
                        }
                    },
                }
            },
        }

    _, first = _execute(tmp_path, source, plan("first"))
    _, second = _execute(tmp_path, source, plan("second"))

    assert first[("markup", "html")] != second[("markup", "html")]
    assert b"first" in first[("markup", "html")]
    assert b"second" in second[("markup", "html")]
    assert counter.read_text() == "2"


def test_html_projection_rejects_runtime_virtual_file_dependencies(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    download = mo.download(b"report", filename="report.txt")
    return (download,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {"download": {"source": "download", "formats": {"html": {}}}},
    }

    with pytest.raises(ValueError, match="cannot be published as a standalone fragment"):
        _execute(tmp_path, source, plan)


def test_html_projection_rejects_marimo_runtime_elements(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    table = mo.ui.table([{"name": "Ada", "score": 10}])
    return (table,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {"table": {"source": "table", "formats": {"html": {}}}},
    }

    with pytest.raises(ValueError, match=r"marimo runtime element <marimo-ui-element>"):
        _execute(tmp_path, source, plan)


def test_child_runner_preserves_relaxed_execution_type(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    from marimo._runtime.context.types import get_context
    execution_type = get_context()._kernel.execution_type
    return (execution_type,)

if __name__ == "__main__":
    app.run()
"""
    _, payloads = _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "execution_type": {
                    "source": "execution_type",
                    "formats": {"json": {}},
                }
            },
        },
    )

    assert payloads[("execution_type", "json")] == b'"relaxed"'


def test_child_runner_disables_inherited_autoreload(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    from marimo._runtime.context import get_context
    child_autoreload_off = get_context()._kernel.autoreload_manager.watcher is None
    return (child_autoreload_off,)

if __name__ == "__main__":
    app.run()
"""
    config: Any = copy.deepcopy(DEFAULT_CONFIG)
    config.setdefault("runtime", {})["auto_reload"] = "autorun"

    _, payloads = _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "autoreload": {
                    "source": "child_autoreload_off",
                    "formats": {"json": {}},
                }
            },
        },
        user_config=config,
    )

    assert payloads[("autoreload", "json")] == b"true"


def test_child_runner_uses_root_arguments_without_accumulating_paths(tmp_path: Path) -> None:
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import sys
    observed_argv = list(sys.argv) if {str(tmp_path)!r} else []
    return (observed_argv,)

if __name__ == "__main__":
    app.run()
"""

    _, payloads = _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {"argv": {"source": "observed_argv", "formats": {"json": {}}}},
        },
        root_argv=["--flag", "value"],
    )

    expected = f'["{tmp_path / "notebook.py"}", "--flag", "value"]'.encode()
    assert payloads[("argv", "json")] == expected, payloads[("argv", "json")].decode()


def test_user_arguments_do_not_reuse_ambient_native_cache(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--value", required=True)
    observed = parser.parse_args().value
    return (observed,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {"observed": {"source": "observed", "formats": {"json": {}}}},
    }

    _, first = _execute(tmp_path, source, plan, root_argv=["--value", "first"])
    _, second = _execute(tmp_path, source, plan, root_argv=["--value", "second"])

    assert first[("observed", "json")] == b'"first"'
    assert second[("observed", "json")] == b'"second"'


def test_child_runner_serializes_parallel_scenarios(tmp_path: Path) -> None:
    probe: Any = types.ModuleType("_marimo_export_concurrency_probe")
    probe.active = 0
    probe.max_active = 0
    sys.modules[probe.__name__] = probe
    snapshots: list[NotebookSnapshot] = []
    for index in range(2):
        path = tmp_path / f"n{index}.py"
        source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
async def _():
    import asyncio
    import _marimo_export_concurrency_probe as probe
    from marimo._runtime.context import get_context
    probe.active += 1
    probe.max_active = max(probe.max_active, probe.active)
    before = get_context().filename
    try:
        await asyncio.sleep(0.03)
        after = get_context().filename
    finally:
        probe.active -= 1
    observed = {{"after": after, "before": before, "marker": "n{index}"}}
    return (observed,)

if __name__ == "__main__":
    app.run()
"""
        encoded = source.encode()
        path.write_bytes(encoded)
        snapshots.append(
            NotebookSnapshot(
                name=path.name,
                source_sha256=hashlib.sha256(encoded).hexdigest(),
                path=path,
                source=encoded,
            )
        )

    plan = decode_plan(
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {"observed": {"source": "observed", "formats": {"json": {}}}},
        }
    )
    streams = KernelStreams(stream=NoopStream(), stdout=None, stderr=None, stdin=None)
    args = KernelArgs(
        streams=streams,
        debugger=None,
        configs={},
        app_metadata=AppMetadata(
            query_params={},
            cli_args={},
            app_config=_AppConfig(),
            filename=str(tmp_path / "root.py"),
            argv=[],
        ),
        user_config=copy.deepcopy(DEFAULT_CONFIG),
        mode=SessionMode.EDIT,
        control_queue=asyncio.Queue(),
        set_ui_element_queue=asyncio.Queue(),
        virtual_file_storage="shared_memory",
    )

    process_argv = sys.argv
    try:
        with kernel_session(args) as (_, context):
            root = get_context()
            attached_argv = context.argv

            async def execute() -> list[Any]:
                return await asyncio.gather(
                    *(
                        run_scenario_in_child(plan, plan.scenarios[0], snapshot)
                        for snapshot in snapshots
                    )
                )

            indexes = asyncio.run(execute())
            observed = [
                json.loads(
                    read_payload(
                        index.outputs["observed"]["json"].payload.key,
                        index.outputs["observed"]["json"].payload.sha256,
                        index.outputs["observed"]["json"].payload.size,
                    )
                )
                for index in indexes
            ]
            assert get_context() is root
            assert sys.argv is attached_argv
            assert context.children == []
            assert context.app_kernel_runner_registry.size == 0
    finally:
        sys.argv = process_argv
        sys.modules.pop(probe.__name__, None)

    assert probe.max_active == 1
    assert observed == [
        {"after": str(snapshot.path), "before": str(snapshot.path), "marker": f"n{index}"}
        for index, snapshot in enumerate(snapshots)
    ]


def test_child_runner_serializes_scenarios_across_root_contexts(tmp_path: Path) -> None:
    probe: Any = types.ModuleType("_marimo_export_process_probe")
    probe.active = 0
    probe.max_active = 0
    probe.lock = threading.Lock()
    sys.modules[probe.__name__] = probe

    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
async def _():
    import asyncio
    import sys
    import _marimo_export_process_probe as probe
    from marimo._runtime.context import get_context
    with probe.lock:
        probe.active += 1
        probe.max_active = max(probe.max_active, probe.active)
    before = {"argv": list(sys.argv), "filename": get_context().filename, "path0": sys.path[0]}
    try:
        await asyncio.sleep(0.03)
        after = {"argv": list(sys.argv), "filename": get_context().filename, "path0": sys.path[0]}
    finally:
        with probe.lock:
            probe.active -= 1
    observed = {"after": after, "before": before}
    return (observed,)

if __name__ == "__main__":
    app.run()
"""
    plan = decode_plan(
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {"observed": {"source": "observed", "formats": {"json": {}}}},
        }
    )
    ready = threading.Barrier(2)
    complete = threading.Barrier(2)

    def execute(name: str) -> dict[str, Any]:
        root = tmp_path / name
        root.mkdir()
        path = root / "notebook.py"
        encoded = source.encode()
        path.write_bytes(encoded)
        snapshot = NotebookSnapshot(
            name=path.name,
            source_sha256=hashlib.sha256(encoded).hexdigest(),
            path=path,
            source=encoded,
        )
        streams = KernelStreams(stream=NoopStream(), stdout=None, stderr=None, stdin=None)
        args = KernelArgs(
            streams=streams,
            debugger=None,
            configs={},
            app_metadata=AppMetadata(
                query_params={},
                cli_args={},
                app_config=_AppConfig(),
                filename=str(path),
                argv=["--owner", name],
            ),
            user_config=copy.deepcopy(DEFAULT_CONFIG),
            mode=SessionMode.EDIT,
            control_queue=asyncio.Queue(),
            set_ui_element_queue=asyncio.Queue(),
            virtual_file_storage="shared_memory",
        )
        with kernel_session(args):
            ready.wait()
            index = asyncio.run(run_scenario_in_child(plan, plan.scenarios[0], snapshot))
            entry = index.outputs["observed"]["json"]
            observed = json.loads(
                read_payload(entry.payload.key, entry.payload.sha256, entry.payload.size)
            )
            complete.wait()
            return observed

    process_argv = sys.argv
    process_path = sys.path[:]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, ("a", "b")))
    finally:
        sys.argv = process_argv
        sys.path[:] = process_path
        sys.modules.pop(probe.__name__, None)

    assert probe.max_active == 1
    for name, result in zip(("a", "b"), results, strict=True):
        expected_path = str(tmp_path / name / "notebook.py")
        expected = {
            "argv": [expected_path, "--owner", name],
            "filename": expected_path,
            "path0": str(tmp_path / name),
        }
        assert result == {"after": expected, "before": expected}


def test_nested_app_receives_parent_user_arguments_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = "nested_argv_fixture"
    nested_path = tmp_path / f"{module_name}.py"
    nested_path.write_text(
        """import marimo

app = marimo.App()

@app.cell
def _():
    import argparse
    import sys
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--flag", required=True)
    nested_argv = list(sys.argv)
    nested_flag = parser.parse_args().flag
    return nested_argv, nested_flag
"""
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(module_name, None)
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import {module_name}
    return ({module_name},)

@app.cell
async def _({module_name}):
    embedded = await {module_name}.app.embed()
    observed = {{
        "argv": embedded.defs["nested_argv"],
        "flag": embedded.defs["nested_flag"],
    }}
    return (observed,)

if __name__ == "__main__":
    app.run()
"""

    try:
        _, payloads = _execute(
            tmp_path,
            source,
            {
                "schema": "marimo-export.plan.v1",
                "outputs": {"observed": {"source": "observed", "formats": {"json": {}}}},
            },
            root_argv=["--flag", "value"],
        )
    finally:
        sys.modules.pop(module_name, None)

    assert json.loads(payloads[("observed", "json")]) == {
        "argv": [str(nested_path), "--flag", "value"],
        "flag": "value",
    }


def test_child_runner_releases_context_and_registry_after_success(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    value = 1
    return (value,)

if __name__ == "__main__":
    app.run()
"""

    _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {"value": {"source": "value", "formats": {"json": {}}}},
        },
    )


def test_child_runner_releases_context_and_registry_after_failure(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    raise RuntimeError("scenario failed")
    value = 1
    return (value,)

if __name__ == "__main__":
    app.run()
"""

    with pytest.raises(RuntimeError, match="scenario failed"):
        _execute(
            tmp_path,
            source,
            {
                "schema": "marimo-export.plan.v1",
                "outputs": {"value": {"source": "value", "formats": {"json": {}}}},
            },
        )


def test_child_runner_releases_nested_embedded_apps_between_scenarios(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = tmp_path / "nested-runs"
    module_name = "nested_app_fixture"
    (tmp_path / f"{module_name}.py").write_text(
        f"""import marimo

app = marimo.App()

@app.cell
def _():
    try:
        with open({str(counter)!r}) as _handle:
            _calls = int(_handle.read())
    except FileNotFoundError:
        _calls = 0
    with open({str(counter)!r}, "w") as _handle:
        _handle.write(str(_calls + 1))
    answer = 41
    return (answer,)
"""
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop(module_name, None)
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import {module_name}
    return ({module_name},)

@app.cell
def _():
    nonce = 0
    return (nonce,)

@app.cell
async def _({module_name}, nonce):
    import sys
    before = list(sys.argv)
    embedded = await {module_name}.app.embed()
    after = list(sys.argv)
    observed_argv = {{"after": after, "before": before}}
    value = embedded.defs["answer"] + nonce * 0
    return observed_argv, value

if __name__ == "__main__":
    app.run()
"""

    def plan(nonce: int) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "inputs": {"nonce": {"definition": "nonce", "default": nonce}},
            "outputs": {
                "argv": {"source": "observed_argv", "formats": {"json": {}}},
                "value": {"source": "value", "formats": {"json": {}}},
            },
        }

    try:
        _, first = _execute(tmp_path, source, plan(1), root_argv=["--flag", "value"])
        _, second = _execute(tmp_path, source, plan(2), root_argv=["--flag", "value"])
    finally:
        sys.modules.pop(module_name, None)

    assert first[("value", "json")] == b"41"
    assert second[("value", "json")] == b"41"
    expected_argv = [str(tmp_path / "notebook.py"), "--flag", "value"]
    assert json.loads(first[("argv", "json")]) == {
        "after": expected_argv,
        "before": expected_argv,
    }
    assert json.loads(second[("argv", "json")]) == {
        "after": expected_argv,
        "before": expected_argv,
    }
    assert counter.read_text() == "2"


def test_child_runner_attempts_every_lifecycle_disposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    first_image = mo.image({f"first-{tmp_path}".encode()!r})
    return (first_image,)

@app.cell
def _(mo):
    second_image = mo.image({f"second-{tmp_path}".encode()!r})
    return (second_image,)

@app.cell
def _():
    answer = 42
    return (answer,)

if __name__ == "__main__":
    app.run()
"""
    original = CellLifecycleRegistry.dispose
    disposed: list[Any] = []

    def fail_first_disposal(
        registry: CellLifecycleRegistry,
        cell_id: Any,
        deletion: bool,
    ) -> None:
        if deletion and cell_id in registry.registry:
            disposed.append(cell_id)
            if len(disposed) == 1:
                raise RuntimeError("first disposal failed")
        original(registry, cell_id, deletion)

    monkeypatch.setattr(CellLifecycleRegistry, "dispose", fail_first_disposal)

    with pytest.raises(RuntimeError, match="first disposal failed"):
        _execute(
            tmp_path,
            source,
            {
                "schema": "marimo-export.plan.v1",
                "outputs": {"answer": {"source": "answer", "formats": {"json": {}}}},
            },
        )

    assert len(disposed) >= 2


def test_ui_input_bypasses_invalid_downstream_default(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    divisor = mo.ui.slider(0, 10, value=0)
    return (divisor,)

@app.cell
def _(divisor):
    result = 10 / divisor.value
    return (result,)

if __name__ == "__main__":
    app.run()
"""
    _, payloads = _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "inputs": {"divisor": {"ui": "divisor", "default": 2}},
            "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
        },
    )

    assert payloads[("answer", "json")] == b"5.0"


def test_recreated_ui_input_is_reapplied_before_projection(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    primary = mo.ui.slider(0, 10, value=0)
    return (primary,)

@app.cell
def _(mo, primary):
    secondary = mo.ui.slider(0, 20, value=primary.value)
    return (secondary,)

@app.cell
def _(primary, secondary):
    result = [primary.value, secondary.value]
    return (result,)

if __name__ == "__main__":
    app.run()
"""
    _, payloads = _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "inputs": {
                "primary": {"ui": "primary", "default": 3},
                "secondary": {"ui": "secondary", "default": 7},
            },
            "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
        },
    )

    assert payloads[("answer", "json")] == b"[3, 7]"


def test_ui_inputs_initialize_dependent_creators_in_scenario_order(
    tmp_path: Path,
) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    primary = mo.ui.slider(0, 10, value=0)
    return (primary,)

@app.cell
def _(mo, primary):
    if primary.value < 3:
        raise ValueError("secondary requires primary >= 3")
    secondary = mo.ui.slider(0, primary.value, value=3)
    return (secondary,)

@app.cell
def _(primary, secondary):
    result = [primary.value, secondary.value]
    return (result,)

if __name__ == "__main__":
    app.run()
"""

    _, payloads = _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "inputs": {
                "primary": {"ui": "primary", "default": 5},
                "secondary": {"ui": "secondary", "default": 4},
            },
            "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
        },
    )

    assert payloads[("answer", "json")] == b"[5, 4]"


def test_ui_creator_runs_once_when_an_earlier_root_marks_it_stale(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "ui-creator-runs.txt"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    state, set_state = mo.state(0)
    return mo, set_state, state

@app.cell
def _(set_state):
    set_state(1)

@app.cell
def _(mo, state):
    from pathlib import Path
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    control = mo.ui.slider(0, 10, value=state())
    return (control,)

@app.cell
def _(control):
    result = control.value
    return (result,)

if __name__ == "__main__":
    app.run()
"""

    _, payloads = _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
        },
    )

    assert payloads[("answer", "json")] == b"1"
    assert counter.read_text() == "1"


def test_state_driven_ui_recreation_converges_after_multiple_passes(
    tmp_path: Path,
) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    step, set_step = mo.state(0, allow_self_loops=True)
    return mo, set_step, step

@app.cell
def _(mo, set_step, step):
    def advance(_value):
        if step() < 3:
            set_step(step() + 1)
    control = mo.ui.slider(0, 10, value=0, on_change=advance)
    return (control,)

@app.cell
def _(control, step):
    result = [control.value, step()]
    return (result,)

if __name__ == "__main__":
    app.run()
"""

    _, payloads = _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "inputs": {"control": {"ui": "control", "default": 6}},
            "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
        },
    )

    assert payloads[("answer", "json")] == b"[6, 3]"


def test_equal_ui_alias_values_are_applied_once(tmp_path: Path) -> None:
    counter = tmp_path / "alias-updates.txt"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    from pathlib import Path
    counter = Path({str(counter)!r})
    def count_update(_value):
        updates = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(updates))
    control = mo.ui.slider(0, 10, value=0, on_change=count_update)
    alias = control
    return alias, control

@app.cell
def _(alias, control):
    result = [control.value, alias.value]
    return (result,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "inputs": {
            "control": {"ui": "control", "default": 4},
            "alias": {"ui": "alias", "default": 4},
        },
        "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
    }

    _, payloads = _execute(tmp_path, source, plan)

    assert payloads[("answer", "json")] == b"[4, 4]"
    assert counter.read_text() == "1"


def test_conflicting_ui_alias_values_fail_before_mutation(tmp_path: Path) -> None:
    counter = tmp_path / "alias-updates.txt"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    from pathlib import Path
    counter = Path({str(counter)!r})
    def count_update(_value):
        counter.write_text("updated")
    control = mo.ui.slider(0, 10, value=0, on_change=count_update)
    alias = control
    return alias, control

@app.cell
def _(control):
    result = control.value
    return (result,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "inputs": {
            "control": {"ui": "control", "default": 4},
            "alias": {"ui": "alias", "default": 7},
        },
        "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
    }

    with pytest.raises(
        ValueError,
        match=r"alias one element with conflicting values: 'control', 'alias'",
    ):
        _execute(tmp_path, source, plan)

    assert not counter.exists()


def test_warm_state_pair_is_relinked_before_ui_callback(tmp_path: Path) -> None:
    counter = tmp_path / "state-runs.txt"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    from pathlib import Path
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    state, set_state = mo.state(0)
    return set_state, state

@app.cell
def _(mo, set_state):
    control = mo.ui.slider(0, 10, value=0, on_change=set_state)
    return (control,)

@app.cell
def _(control, state):
    observed = [control.value, state()]
    return (observed,)

if __name__ == "__main__":
    app.run()
"""

    def plan(value: int) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "inputs": {"control": {"ui": "control", "default": value}},
            "outputs": {"answer": {"source": "observed", "formats": {"json": {}}}},
        }

    _, cold = _execute(tmp_path, source, plan(1))
    _, warm = _execute(tmp_path, source, plan(2))

    assert cold[("answer", "json")] == b"[1, 1]"
    assert warm[("answer", "json")] == b"[2, 2]"
    assert counter.read_text() == "2"


def test_warm_nested_state_pair_is_relinked_before_consumption(tmp_path: Path) -> None:
    counter = tmp_path / "state-runs.txt"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    from pathlib import Path
    from types import SimpleNamespace
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    state, set_state = mo.state(0)
    bundle = SimpleNamespace(state=state, set_state=set_state)
    return (bundle,)

@app.cell
def _(bundle):
    bundle.set_state(bundle.state() + 1)

@app.cell
def _(bundle):
    observed = bundle.state()
    return (observed,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {"answer": {"source": "observed", "formats": {"json": {}}}},
    }

    _, cold = _execute(tmp_path, source, plan)
    _, warm = _execute(tmp_path, source, plan)

    assert cold[("answer", "json")] == b"1"
    assert warm[("answer", "json")] == b"1"
    assert counter.read_text() == "2"


def test_state_pair_repair_preserves_projection_cache_hits(tmp_path: Path) -> None:
    state_counter = tmp_path / "state-runs.txt"
    projection_counter = tmp_path / "projection-runs.txt"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    from pathlib import Path as StatePath
    counter = StatePath({str(state_counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    state, set_state = mo.state(0)
    return set_state, state

@app.cell
def _(mo, set_state):
    control = mo.ui.slider(0, 10, value=0, on_change=set_state)
    return (control,)

@app.cell
def _():
    publish_value = 7
    return (publish_value,)

@app.cell
def _():
    from pathlib import Path as ProjectionPath
    from marimo_export import Projection
    def export_value(value):
        counter = ProjectionPath({str(projection_counter)!r})
        runs = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(runs))
        return Projection(
            str(value).encode(),
            format_id="value.v1",
            media_type="text/plain",
        )
    return (export_value,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "inputs": {"control": {"ui": "control", "default": 3}},
        "outputs": {
            "answer": {
                "source": "publish_value",
                "formats": {"custom": {"exporter": {"definition": "export_value"}}},
            }
        },
    }

    _, cold = _execute(tmp_path, source, plan)
    _, warm = _execute(tmp_path, source, plan)

    assert cold[("answer", "custom")] == b"7"
    assert warm[("answer", "custom")] == b"7"
    assert state_counter.read_text() == "2"
    assert projection_counter.read_text() == "1"


def test_state_setter_consumer_executes_again_for_a_new_pre_state(tmp_path: Path) -> None:
    counter = tmp_path / "setter-runs.txt"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    initial = 0
    return (initial,)

@app.cell
def _(initial):
    import marimo as mo
    state, set_state = mo.state(initial)
    return set_state, state

@app.cell
def _(set_state):
    setter_alias = set_state
    return (setter_alias,)

@app.cell
def _(setter_alias):
    from pathlib import Path
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    setter_alias(lambda value: value + 1)

@app.cell
def _(state):
    observed = state()
    return (observed,)

if __name__ == "__main__":
    app.run()
"""

    def plan(initial: int) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "inputs": {"initial": {"definition": "initial", "default": initial}},
            "outputs": {"answer": {"source": "observed", "formats": {"json": {}}}},
        }

    _, cold = _execute(tmp_path, source, plan(1))
    _, warm = _execute(tmp_path, source, plan(10))

    assert cold[("answer", "json")] == b"2"
    assert warm[("answer", "json")] == b"11"
    assert counter.read_text() == "2"


def test_state_getter_consumer_keeps_its_native_cache_hit(tmp_path: Path) -> None:
    counter = tmp_path / "getter-runs.txt"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    state, set_state = mo.state(10)
    return set_state, state

@app.cell
def _(state):
    from pathlib import Path
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    observed = state()
    return (observed,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {"answer": {"source": "observed", "formats": {"json": {}}}},
    }

    _, cold = _execute(tmp_path, source, plan)
    _, warm = _execute(tmp_path, source, plan)

    assert cold[("answer", "json")] == b"10"
    assert warm[("answer", "json")] == b"10"
    assert counter.read_text() == "1"


def test_direct_state_pair_consumer_replays_its_native_cache_hit(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "paired-state-runs.txt"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    initial = 1
    return (initial,)

@app.cell
def _(initial):
    import marimo as mo
    state, set_state = mo.state(initial)
    return set_state, state

@app.cell
def _(set_state, state):
    from pathlib import Path
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    set_state(state() + 1)

@app.cell
def _(state):
    observed = state()
    return (observed,)

if __name__ == "__main__":
    app.run()
"""

    def plan(initial: int) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "inputs": {"initial": {"definition": "initial", "default": initial}},
            "outputs": {"answer": {"source": "observed", "formats": {"json": {}}}},
        }

    _, first = _execute(tmp_path, source, plan(1))
    _, second = _execute(tmp_path, source, plan(10))
    _, repeated = _execute(tmp_path, source, plan(1))

    assert first[("answer", "json")] == b"2"
    assert second[("answer", "json")] == b"11"
    assert repeated[("answer", "json")] == b"2"
    assert counter.read_text() == "2"


def test_transitive_state_setter_consumer_executes_live_on_warm_run(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "hidden-setter-runs.txt"
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    state, set_state = mo.state(1)
    return set_state, state

@app.cell
def _(set_state):
    def increment():
        set_state(lambda value: value + 1)
    return (increment,)

@app.cell
def _(increment, state):
    from pathlib import Path
    counter = Path({str(counter)!r})
    runs = int(counter.read_text()) + 1 if counter.exists() else 1
    counter.write_text(str(runs))
    before = state()
    increment()
    mutation = [before, state()]
    return (mutation,)

@app.cell
def _(mutation, state):
    observed = [mutation, state()]
    return (observed,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {"answer": {"source": "observed", "formats": {"json": {}}}},
    }

    _, cold = _execute(tmp_path, source, plan)
    _, warm = _execute(tmp_path, source, plan)

    assert cold[("answer", "json")] == b"[[1, 2], 2]"
    assert warm[("answer", "json")] == b"[[1, 2], 2]"
    assert counter.read_text() == "2"


def test_transient_authored_failure_clears_after_successful_rerun(
    tmp_path: Path,
) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    state, set_state = mo.state(0, allow_self_loops=True)
    return set_state, state

@app.cell
def _(set_state, state):
    current = state()
    if current == 0:
        set_state(1)
        raise RuntimeError("transient failure")
    result = current
    return (result,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
    }

    _, payloads = _execute(tmp_path, source, plan)

    assert payloads[("answer", "json")] == b"1"


def test_persistent_authored_failure_preserves_its_exception(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    raise LookupError("persistent failure")
    result = 1
    return (result,)

if __name__ == "__main__":
    app.run()
"""
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
    }

    with pytest.raises(LookupError, match="persistent failure"):
        _execute(tmp_path, source, plan)


def test_ui_recreation_cycle_fails_with_target_and_cell(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    return (mo,)

@app.cell
def _(mo):
    def replace(_value):
        globals()["control"] = mo.ui.slider(0, 10, value=0, on_change=replace)
    control = mo.ui.slider(0, 10, value=0, on_change=replace)
    return (control,)

@app.cell
def _(control):
    result = control.value
    return (result,)

if __name__ == "__main__":
    app.run()
"""

    with pytest.raises(
        RuntimeError,
        match=r"UI inputs did not converge.*'control' \([^)]+\)",
    ):
        _execute(
            tmp_path,
            source,
            {
                "schema": "marimo-export.plan.v1",
                "inputs": {"control": {"ui": "control", "default": 2}},
                "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
            },
        )


def test_equivalent_projection_aliases_execute_one_synthetic_cell(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    calls = []
    value = 42
    return calls, value

@app.cell
def _(calls):
    from marimo_export import Projection
    def count(value):
        calls.append(value)
        return Projection(
            str(len(calls)).encode(),
            format_id="count.v1",
            media_type="text/plain",
        )
    return (count,)

if __name__ == "__main__":
    app.run()
"""
    _, payloads = _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "answer": {
                    "source": "value",
                    "formats": {
                        "first": {"exporter": {"definition": "count"}},
                        "second": {"exporter": {"definition": "count"}},
                    },
                }
            },
        },
    )

    assert payloads == {("answer", "first"): b"1", ("answer", "second"): b"1"}


def test_projection_cache_token_does_not_capture_authored_definition(tmp_path: Path) -> None:
    first_plan = decode_plan(
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {"value": {"source": "value", "formats": {"json": {}}}},
        }
    )
    output = first_plan.outputs[0]
    binding = projection_binding(
        output_name=output.name,
        format_name=output.formats[0].name,
        source=output.source,
        format_plan=output.formats[0],
    )
    token_name = binding.cell.cache_token_name
    source = f"""import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    {token_name} = "authored"
    value = 42
    return {token_name}, value

if __name__ == "__main__":
    app.run()
"""

    _, payloads = _execute(
        tmp_path,
        source,
        {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "value": {"source": "value", "formats": {"json": {}}},
                "authored": {"source": token_name, "formats": {"json": {}}},
            },
        },
    )

    assert payloads[("value", "json")] == b"42"
    assert payloads[("authored", "json")] == b'"authored"'


def test_unpicklable_source_restores_cached_terminal_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = tmp_path / "exporter-calls"
    monkeypatch.setenv("MARIMO_EXPORT_TEST_COUNTER", str(counter))
    source = _unpicklable_box_source()
    plan = {
        "schema": "marimo-export.plan.v1",
        "outputs": {
            "box": {
                "source": "obj",
                "formats": {
                    "custom": {
                        "exporter": {
                            "definition": "export_box",
                            "version": "1",
                        }
                    }
                },
            }
        },
    }
    attempts: list[tuple[str, bool, bool]] = []
    original = cached_module.cache_attempt_from_hash

    def traced_cache_attempt(
        module: Any,
        graph: Any,
        cell_id: Any,
        scope: dict[str, Any],
        **kwargs: Any,
    ) -> Any:
        attempt = original(module, graph, cell_id, scope, **kwargs)
        if "marimo_export.projection.synthetic_cells" in graph.cells[cell_id].code:
            source_is_stub = getattr(type(scope.get("obj")), "__marimo_unhashable__", False) is True
            attempts.append((attempt.cache_type, attempt.hit, source_is_stub))
        return attempt

    monkeypatch.setattr(cached_module, "cache_attempt_from_hash", traced_cache_attempt)

    _, cold_payloads = _execute(tmp_path, source, plan)
    _, warm_payloads = _execute(tmp_path, source, plan)

    assert cold_payloads[("box", "custom")] == b"7"
    assert warm_payloads[("box", "custom")] == b"7"
    assert counter.read_text() == "1"
    assert attempts == [
        ("ExecutionPath", False, False),
        ("ExecutionPath", True, True),
    ]


def test_exporter_version_miss_reruns_unpicklable_source_producer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = tmp_path / "exporter-calls"
    monkeypatch.setenv("MARIMO_EXPORT_TEST_COUNTER", str(counter))

    def plan(version: str) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "box": {
                    "source": "obj",
                    "formats": {
                        "custom": {
                            "exporter": {
                                "definition": "export_box",
                                "version": version,
                            }
                        }
                    },
                }
            },
        }

    _, first = _execute(tmp_path, _unpicklable_box_source(), plan("1"))
    _, second = _execute(tmp_path, _unpicklable_box_source(), plan("2"))

    assert first[("box", "custom")] == b"7"
    assert second[("box", "custom")] == b"7"
    assert counter.read_text() == "2"


def test_exporter_version_miss_materializes_nested_unpicklable_producers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = tmp_path / "exporter-calls"
    monkeypatch.setenv("MARIMO_EXPORT_TEST_COUNTER", str(counter))
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import threading
    return (threading,)

@app.cell
def _(threading):
    class Source:
        def __init__(self):
            self.value = 7
            self.lock = threading.Lock()
    source = Source()
    return (source,)

@app.cell
def _(source, threading):
    class Box:
        def __init__(self, value):
            self.value = value
            self.lock = threading.Lock()
    obj = Box(source.value)
    return (obj,)

@app.cell
def _():
    import os
    from pathlib import Path
    from marimo_export import Projection
    def export_box(value):
        counter = Path(os.environ["MARIMO_EXPORT_TEST_COUNTER"])
        calls = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(calls + 1))
        return Projection(
            str(value.value).encode(),
            format_id="box.v1",
            media_type="text/plain",
        )
    return (export_box,)

if __name__ == "__main__":
    app.run()
"""

    def plan(version: str) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "outputs": {
                "box": {
                    "source": "obj",
                    "formats": {
                        "custom": {
                            "exporter": {
                                "definition": "export_box",
                                "version": version,
                            }
                        }
                    },
                }
            },
        }

    _, first = _execute(tmp_path, source, plan("1"))
    _, second = _execute(tmp_path, source, plan("2"))

    assert first[("box", "custom")] == b"7"
    assert second[("box", "custom")] == b"7"
    assert counter.read_text() == "2"


def test_input_dependent_miss_reruns_unpicklable_source_producer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = tmp_path / "exporter-calls"
    monkeypatch.setenv("MARIMO_EXPORT_TEST_COUNTER", str(counter))
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    multiplier = 1
    return (multiplier,)

@app.cell
def _():
    import threading
    return (threading,)

@app.cell
def _(threading):
    class Box:
        def __init__(self, value):
            self.value = value
            self.lock = threading.Lock()
    obj = Box(7)
    return (obj,)

@app.cell
def _():
    import os
    from pathlib import Path
    from marimo_export import Projection
    def export_box(value):
        obj, multiplier = value
        counter = Path(os.environ["MARIMO_EXPORT_TEST_COUNTER"])
        calls = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(calls + 1))
        return Projection(
            str(obj.value * multiplier).encode(),
            format_id="box.v1",
            media_type="text/plain",
        )
    return (export_box,)

if __name__ == "__main__":
    app.run()
"""

    def plan(multiplier: int) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "inputs": {
                "multiplier": {
                    "definition": "multiplier",
                    "default": multiplier,
                }
            },
            "outputs": {
                "box": {
                    "source": {"expression": "(obj, multiplier)"},
                    "formats": {
                        "custom": {
                            "exporter": {
                                "definition": "export_box",
                                "version": "1",
                            }
                        }
                    },
                }
            },
        }

    _, first = _execute(tmp_path, source, plan(2))
    _, second = _execute(tmp_path, source, plan(3))

    assert first[("box", "custom")] == b"14"
    assert second[("box", "custom")] == b"21"
    assert counter.read_text() == "2"


def test_authored_miss_materializes_nested_unpicklable_producers(tmp_path: Path) -> None:
    source = """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    multiplier = 1
    return (multiplier,)

@app.cell
def _():
    import threading
    return (threading,)

@app.cell
def _(threading):
    class Box:
        def __init__(self):
            self.value = 7
            self.lock = threading.Lock()
    box = Box()
    return (box,)

@app.cell
def _(box):
    wrapped = [box]
    return (wrapped,)

@app.cell
def _(multiplier, wrapped):
    result = wrapped[0].value * multiplier
    return (result,)

if __name__ == "__main__":
    app.run()
"""

    def plan(multiplier: int) -> object:
        return {
            "schema": "marimo-export.plan.v1",
            "inputs": {
                "multiplier": {
                    "definition": "multiplier",
                    "default": multiplier,
                }
            },
            "outputs": {"answer": {"source": "result", "formats": {"json": {}}}},
        }

    _, cold = _execute(tmp_path, source, plan(2))
    _, warm = _execute(tmp_path, source, plan(3))

    assert cold[("answer", "json")] == b"14"
    assert warm[("answer", "json")] == b"21"


def _unpicklable_box_source() -> str:
    return """import marimo

__generated_with = "0.23.14"
app = marimo.App()

@app.cell
def _():
    import threading
    return (threading,)

@app.cell
def _(threading):
    class Box:
        def __init__(self, value):
            self.value = value
            self.lock = threading.Lock()
    obj = Box(7)
    return (obj,)

@app.cell
def _():
    import os
    from pathlib import Path
    from marimo_export import Projection
    def export_box(value):
        counter = Path(os.environ["MARIMO_EXPORT_TEST_COUNTER"])
        calls = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(calls + 1))
        return Projection(
            str(value.value).encode(),
            format_id="box.v1",
            media_type="text/plain",
        )
    return (export_box,)

if __name__ == "__main__":
    app.run()
"""


def _execute(
    tmp_path: Path,
    source: str,
    plan_value: object,
    *,
    user_config: Any | None = None,
    root_argv: list[str] | None = None,
) -> tuple[object, dict[tuple[str, str], bytes]]:
    path = tmp_path / "notebook.py"
    encoded = source.encode()
    path.write_bytes(encoded)
    snapshot = NotebookSnapshot(
        name=path.name,
        source_sha256=hashlib.sha256(encoded).hexdigest(),
        path=path,
        source=encoded,
    )
    plan = decode_plan(plan_value)
    streams = KernelStreams(
        stream=NoopStream(),
        stdout=None,
        stderr=None,
        stdin=None,
    )
    control_queue: asyncio.Queue[Any] = asyncio.Queue()
    ui_queue: asyncio.Queue[Any] = asyncio.Queue()
    args = KernelArgs(
        streams=streams,
        debugger=None,
        configs={},
        app_metadata=AppMetadata(
            query_params={},
            cli_args={},
            app_config=_AppConfig(),
            filename=str(path),
            argv=[] if root_argv is None else root_argv,
        ),
        user_config=copy.deepcopy(DEFAULT_CONFIG if user_config is None else user_config),
        mode=SessionMode.EDIT,
        control_queue=control_queue,
        set_ui_element_queue=ui_queue,
        virtual_file_storage="shared_memory",
    )

    process_argv = sys.argv
    try:
        with kernel_session(args) as (_, context):
            attached_argv = context.argv
            try:
                result = asyncio.run(run_scenario_in_child(plan, plan.scenarios[0], snapshot))
                payloads = {
                    (output_name, format_name): read_payload(
                        entry.payload.key,
                        entry.payload.sha256,
                        entry.payload.size,
                    )
                    for output_name, formats in result.outputs.items()
                    for format_name, entry in formats.items()
                }
            finally:
                assert sys.argv is attached_argv
                assert context.children == []
                assert context.app_kernel_runner_registry.size == 0
    finally:
        sys.argv = process_argv
    return result, payloads
