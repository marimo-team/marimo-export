from __future__ import annotations

import html as html_lib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent))

from notebook_markdown import (  # noqa: E402
    NOTEBOOK_LINEAR_SCHEMA,
    notebook_markdown_spec,
    write_markdown_from_bundle,
    write_markdown_snapshot,
)


def assert_in_order(text: str, *needles: str) -> None:
    position = -1
    for needle in needles:
        next_position = text.find(needle, position + 1)
        assert next_position >= 0, needle
        position = next_position


def test_notebook_markdown_spec_captures_whole_notebook() -> None:
    spec = notebook_markdown_spec(
        scenario_id="review",
        state={"symbol": "MSFT"},
    )

    assert spec["scenarios"] == [{"id": "review", "state": {"symbol": "MSFT"}}]
    assert spec["values"]["notebook"]["source"] == {"snapshot": True}
    assert spec["values"]["notebook"]["formats"] == [
        {
            "format": "linear",
            "export": {
                "type": "ref",
                "ref": "moexport.exporters.notebook:linear",
            },
            "options": {
                "include_source": True,
                "include_empty_outputs": True,
            },
        }
    ]

    patched = notebook_markdown_spec(
        scenario_id="review",
        state={"symbol": "MSFT", "selector.value": ["MSFT"]},
    )
    assert patched["scenarios"] == [
        {
            "id": "review",
            "state": {"symbol": "MSFT", "selector.value": ["MSFT"]},
        }
    ]


def test_write_markdown_snapshot_preserves_source_outputs_state_and_media(
    tmp_path: Path,
) -> None:
    snapshot = {
        "schema": NOTEBOOK_LINEAR_SCHEMA,
        "version": 1,
        "notebook": {
            "name": "demo.py",
            "path": "demo.py",
            "sha256": "abc123",
        },
        "cells": [
            {
                "index": 0,
                "id": "intro",
                "name": None,
                "source": "x = 1\nx",
                "outputs": [
                    {
                        "channel": "output",
                        "mimetype": "text/markdown",
                        "data": "## Result\n\nThe value is `1`.",
                    }
                ],
            },
            {
                "index": 1,
                "id": "chart",
                "name": "render_chart",
                "source": "display_chart()",
                "outputs": [
                    {
                        "channel": "output",
                        "mimetype": "text/html",
                        "data": "<script>window.ok = true;</script><div>chart</div>",
                    }
                ],
            },
        ],
    }

    result = write_markdown_snapshot(
        snapshot,
        tmp_path,
        scenario_id="review",
        scenario_state={"symbol": "MSFT"},
        bundle_id="sha256-demo",
    )

    markdown = result.output_path.read_text(encoding="utf-8")
    html = tmp_path / "media" / "cell-02-output-01.html"

    assert result.cell_count == 2
    assert result.output_count == 2
    assert result.media_files == (html,)
    assert_in_order(markdown, "Cell 01", "```python\nx = 1\nx\n```", "The value is `1`.")
    assert_in_order(
        markdown,
        "Cell 02",
        "render_chart",
        "```python\ndisplay_chart()\n```",
        "[Open HTML output](media/cell-02-output-01.html)",
    )
    assert_in_order(markdown, "Scenario state", '"symbol": "MSFT"')
    assert html.read_text(encoding="utf-8") == (
        "<script>window.ok = true;</script><div>chart</div>"
    )


def test_write_markdown_from_bundle_selects_authored_linear_format(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    bundle = export_root / "bundles" / "sha256-demo"
    blob = export_root / "blobs" / "sha256" / "no" / "te" / "snapshot"
    snapshot = {
        "schema": NOTEBOOK_LINEAR_SCHEMA,
        "version": 1,
        "notebook": {"name": "demo.py"},
        "cells": [
            {
                "index": 0,
                "id": "intro",
                "source": "x = 1",
                "outputs": [
                    {
                        "channel": "output",
                        "mimetype": "text/markdown",
                        "data": "bundle output",
                    }
                ],
            }
        ],
    }
    payload = json.dumps(snapshot).encode("utf-8")
    blob.parent.mkdir(parents=True)
    blob.write_bytes(payload)
    manifest = {
        "schema": "moexport.bundle.v1",
        "version": 1,
        "id": "sha256-demo",
        "sha256": "demo",
        "notebook": {"name": "demo.py", "source": None},
        "scenario_set": {"id": "sha256-scenarios", "sha256": "scenarios"},
        "capture": {"id": "sha256-capture", "request_sha256": "request"},
        "values": {
            "notebook": {
                "source": {"type": "snapshot"},
                "formats": ["linear"],
            }
        },
        "scenarios": [
            {
                "id": "default",
                "state": {},
                "values": {
                    "notebook": {
                        "linear": {
                            "format_id": NOTEBOOK_LINEAR_SCHEMA,
                            "media_type": "application/json",
                            "data": {
                                "type": "bundle",
                                "files": {
                                    "notebook": {
                                        "href": "blobs/sha256/no/te/snapshot",
                                        "media_type": "application/json",
                                        "size": len(payload),
                                        "sha256": "snapshot",
                                    }
                                },
                                "entry": "notebook",
                            },
                            "metadata": {},
                        }
                    }
                },
            }
        ],
        "provenance": {},
    }
    (bundle / "manifest.json").parent.mkdir(parents=True)
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = write_markdown_from_bundle(export_root, tmp_path / "markdown")

    markdown = result.output_path.read_text(encoding="utf-8")
    assert "```python\nx = 1\n```" in markdown
    assert "bundle output" in markdown


def test_write_markdown_snapshot_extracts_marimo_mime_preview(
    tmp_path: Path,
) -> None:
    html_payload = (
        "<marimo-mime-renderer "
        "data-data='{&quot;text/html&quot;:"
        "&quot;&lt;div id=&#92;&quot;chart&#92;&quot;&gt;"
        "ok&lt;/div&gt;&quot;}'></marimo-mime-renderer>"
    )
    snapshot = {
        "schema": NOTEBOOK_LINEAR_SCHEMA,
        "version": 1,
        "notebook": {"name": "chart.py"},
        "cells": [
            {
                "index": 0,
                "id": "chart",
                "source": "chart",
                "outputs": [
                    {
                        "channel": "output",
                        "mimetype": "text/html",
                        "data": html_payload,
                    }
                ],
            }
        ],
    }

    result = write_markdown_snapshot(snapshot, tmp_path, inline_html_bytes=1)

    markdown = result.output_path.read_text(encoding="utf-8")
    raw = tmp_path / "media" / "cell-01-output-01.html"

    assert result.media_files == (raw,)
    assert "[Open raw HTML output](media/cell-01-output-01.html)" in markdown
    assert "ok" in markdown
    assert raw.read_text(encoding="utf-8") == html_payload


def test_write_markdown_snapshot_keeps_code_for_cells_without_output(
    tmp_path: Path,
) -> None:
    snapshot = {
        "schema": NOTEBOOK_LINEAR_SCHEMA,
        "version": 1,
        "notebook": {"name": "empty.py"},
        "cells": [
            {
                "index": 0,
                "id": "setup",
                "source": "x = 1",
                "outputs": [],
            }
        ],
    }

    result = write_markdown_snapshot(snapshot, tmp_path)

    markdown = result.output_path.read_text(encoding="utf-8")

    assert "```python\nx = 1\n```" in markdown
    assert "Cell 01" in markdown


def test_write_markdown_snapshot_markdownifies_html_shaped_markdown_output(
    tmp_path: Path,
) -> None:
    snapshot = {
        "schema": NOTEBOOK_LINEAR_SCHEMA,
        "version": 1,
        "notebook": {"name": "prose.py"},
        "cells": [
            {
                "index": 0,
                "id": "prose",
                "source": "mo.md('hi')",
                "outputs": [
                    {
                        "channel": "output",
                        "mimetype": "text/markdown",
                        "data": (
                            '<span class="markdown prose">'
                            "<h2>Greeting</h2>"
                            '<span class="paragraph">Hello <code>AAPL</code></span>'
                            "</span>"
                        ),
                    }
                ],
            }
        ],
    }

    result = write_markdown_snapshot(snapshot, tmp_path)

    markdown = result.output_path.read_text(encoding="utf-8")

    assert "## Greeting" in markdown
    assert "Hello `AAPL`" in markdown


def test_write_markdown_snapshot_rasterizes_marimo_vegalite_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_vegalite_to_png(**kwargs: object) -> bytes:
        calls.update(kwargs)
        return b"PNG"

    monkeypatch.setitem(
        sys.modules,
        "vl_convert",
        SimpleNamespace(vegalite_to_png=fake_vegalite_to_png),
    )

    inner_html = """
<script type="text/javascript">
  (function(spec, embedOpt) {} )(
    {"$schema": "https://vega.github.io/schema/vega-lite/v6.4.1.json",
     "data": {"values": [{"x": 1, "y": 2}]},
     "mark": "line",
     "encoding": {"x": {"field": "x", "type": "quantitative"},
                  "y": {"field": "y", "type": "quantitative"}}},
    {"mode": "vega-lite"}
  );
</script>
"""
    data = html_lib.escape(json.dumps({"text/html": inner_html}), quote=True)
    snapshot = {
        "schema": NOTEBOOK_LINEAR_SCHEMA,
        "version": 1,
        "notebook": {"name": "chart.py"},
        "cells": [
            {
                "index": 0,
                "id": "chart",
                "source": "chart",
                "outputs": [
                    {
                        "channel": "output",
                        "mimetype": "text/html",
                        "data": (
                            "<marimo-mime-renderer "
                            f"data-data='{data}'></marimo-mime-renderer>"
                        ),
                    }
                ],
            }
        ],
    }

    result = write_markdown_snapshot(snapshot, tmp_path, inline_html_bytes=1)

    markdown = result.output_path.read_text(encoding="utf-8")
    raw = tmp_path / "media" / "cell-01-output-01.html"
    png = tmp_path / "media" / "cell-01-output-01.png"

    assert set(result.media_files) == {raw, png}
    assert "[Open raw HTML output](media/cell-01-output-01.html)" in markdown
    assert "![Cell 01 output 1](media/cell-01-output-01.png)" in markdown
    assert png.read_bytes() == b"PNG"
    vl_spec = calls["vl_spec"]
    assert isinstance(vl_spec, str)
    assert json.loads(vl_spec)["mark"] == "line"
