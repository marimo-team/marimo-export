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
    write_markdown_snapshot,
)


def test_notebook_markdown_spec_captures_whole_notebook() -> None:
    spec = notebook_markdown_spec(
        scenario_id="review",
        state={"symbol": "MSFT"},
    )

    assert spec["scenarios"] == [{"id": "review", "state": {"symbol": "MSFT"}}]
    assert spec["values"]["notebook"]["source"] == "mox.runtime().snapshot()"
    assert spec["values"]["notebook"]["formats"]["linear"]["export"] == {
        "type": "ref",
        "ref": "moexport.exporters.notebook:linear",
    }
    assert spec["values"]["notebook"]["formats"]["linear"]["options"] == {
        "include_source": True,
        "include_empty_outputs": True,
    }


def test_write_markdown_snapshot_preserves_inputs_outputs_and_media(
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
    assert '<div class="moexport-cell-label">Cell 01</div>' in markdown
    assert (
        '<div class="moexport-cell-label">Cell 02 · <code>render_chart</code></div>'
    ) in markdown
    assert '\n---\n\n<div class="moexport-cell-label">' in markdown
    assert "**Input**" not in markdown
    assert "**Output**" not in markdown
    assert "\n# demo.py\n" not in markdown
    assert "<summary>Scenario state</summary>" in markdown
    assert '"symbol": "MSFT"' in markdown
    assert "```python\nx = 1\nx\n```" in markdown
    assert "The value is `1`." in markdown
    assert "[Open HTML output](media/cell-02-output-01.html)" in markdown
    assert "iframe" not in markdown
    assert html.read_text(encoding="utf-8") == (
        "<script>window.ok = true;</script><div>chart</div>"
    )


def test_write_markdown_snapshot_extracts_marimo_mime_preview(
    tmp_path: Path,
) -> None:
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
                            "data-data='{&quot;text/html&quot;:"
                            "&quot;&lt;div id=&#92;&quot;chart&#92;&quot;&gt;"
                            "ok&lt;/div&gt;&quot;}'></marimo-mime-renderer>"
                        ),
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
    assert "[Open rendered preview]" not in markdown
    assert "iframe" not in markdown
    assert '<div id="chart">ok</div>' not in markdown
    assert "ok" in markdown


def test_write_markdown_snapshot_omits_empty_output_marker(
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

    assert "_No display output._" not in markdown
    assert "```python\nx = 1\n```" in markdown
    assert '<div class="moexport-cell-label">Cell 01</div>' in markdown


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

    assert '<span class="markdown prose">' not in markdown
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

    assert result.media_files == (raw, png)
    assert "[Open raw HTML output](media/cell-01-output-01.html)" in markdown
    assert "![Cell 01 output 1](media/cell-01-output-01.png)" in markdown
    assert "[Open rendered preview]" not in markdown
    assert "iframe" not in markdown
    assert png.read_bytes() == b"PNG"
    assert calls["scale"] == 2
    vl_spec = calls["vl_spec"]
    assert isinstance(vl_spec, str)
    assert json.loads(vl_spec)["mark"] == "line"
