"""Example-owned whole-notebook Markdown export helpers."""

from __future__ import annotations

import base64
import html
import importlib
import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from markdownify import markdownify as md
from moexport.notebook import NotebookReference, NotebookRunOptions, export_notebook
from moexport.query import open_export

NOTEBOOK_MARKDOWN_VALUE = "notebook"
NOTEBOOK_MARKDOWN_FORMAT = "linear"
NOTEBOOK_LINEAR_SCHEMA = "marimo.notebook.linear.v1"
_DEFAULT_INLINE_HTML_BYTES = 16_384
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class MarkdownExportResult:
    """Files written by a self-contained Markdown export."""

    output_path: Path
    media_dir: Path
    scenario_id: str
    bundle_id: str | None
    cell_count: int
    output_count: int
    media_files: tuple[Path, ...]


def notebook_markdown_spec(
    *,
    scenario_id: str = "default",
    state: Mapping[str, Any] | None = None,
    patches: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a spec that captures every notebook cell as a linear snapshot."""

    scenario: dict[str, Any] = {
        "id": scenario_id,
        "state": dict(state or {}),
    }
    if patches:
        scenario["patches"] = dict(patches)

    return {
        "scenarios": [scenario],
        "values": {
            NOTEBOOK_MARKDOWN_VALUE: {
                "source": {"snapshot": True},
                "artifacts": {
                    NOTEBOOK_MARKDOWN_FORMAT: {
                        "export": {
                            "type": "ref",
                            "ref": "moexport.exporters.notebook:linear",
                        },
                        "options": {
                            "include_source": True,
                            "include_empty_outputs": True,
                        },
                    }
                },
            }
        },
    }


def export_notebook_markdown(
    notebook: NotebookReference,
    output_dir: str | Path,
    *,
    scenario_id: str = "default",
    state: Mapping[str, Any] | None = None,
    patches: Mapping[str, Any] | None = None,
    to: str | Path | None = None,
    run: NotebookRunOptions | None = None,
    title: str | None = None,
    inline_html_bytes: int = _DEFAULT_INLINE_HTML_BYTES,
) -> MarkdownExportResult:
    """Capture a notebook and write `output.md` plus static media files."""

    spec = notebook_markdown_spec(
        scenario_id=scenario_id,
        state=state,
        patches=patches,
    )
    if to is not None:
        result = export_notebook(notebook, spec, to=to, run=run)
        root = Path(result.bundle_path).parent.parent
        return write_markdown_from_bundle(
            root,
            output_dir,
            scenario_id=scenario_id,
            title=title,
            inline_html_bytes=inline_html_bytes,
        )

    with TemporaryDirectory(prefix="moexport-markdown-") as directory:
        result = export_notebook(notebook, spec, to=directory, run=run)
        root = Path(result.bundle_path).parent.parent
        return write_markdown_from_bundle(
            root,
            output_dir,
            scenario_id=scenario_id,
            title=title,
            inline_html_bytes=inline_html_bytes,
        )


def write_markdown_from_bundle(
    bundle_root: str | Path,
    output_dir: str | Path,
    *,
    scenario_id: str = "default",
    bundle_id: str | None = None,
    title: str | None = None,
    inline_html_bytes: int = _DEFAULT_INLINE_HTML_BYTES,
) -> MarkdownExportResult:
    """Read a notebook snapshot artifact from a bundle and write Markdown."""

    bundle = open_export(bundle_root).bundle(bundle_id)
    entry = bundle.entry(
        scenario=scenario_id,
        value=NOTEBOOK_MARKDOWN_VALUE,
        format_id=NOTEBOOK_MARKDOWN_FORMAT,
    )
    path = entry.get("path")
    if not isinstance(path, str):
        raise FileNotFoundError("notebook snapshot entry has no resolved file path")

    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    scenario_rows = bundle.scenarios(scenario=scenario_id)
    scenario_state = scenario_rows[0].get("state", {}) if scenario_rows else {}
    return write_markdown_snapshot(
        snapshot,
        output_dir,
        scenario_id=scenario_id,
        bundle_id=bundle.id,
        scenario_state=scenario_state,
        title=title,
        inline_html_bytes=inline_html_bytes,
    )


def write_markdown_snapshot(
    snapshot: Mapping[str, Any],
    output_dir: str | Path,
    *,
    scenario_id: str = "default",
    scenario_state: Mapping[str, Any] | None = None,
    bundle_id: str | None = None,
    title: str | None = None,
    media_dir_name: str = "media",
    inline_html_bytes: int = _DEFAULT_INLINE_HTML_BYTES,
) -> MarkdownExportResult:
    """Write one linear notebook snapshot as `output.md` plus media files."""

    if snapshot.get("schema") != NOTEBOOK_LINEAR_SCHEMA:
        raise ValueError(f"expected {NOTEBOOK_LINEAR_SCHEMA} snapshot")

    cells = snapshot.get("cells")
    if not isinstance(cells, list):
        raise ValueError("notebook snapshot must contain a cells list")

    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    media_dir = output_root / media_dir_name
    if media_dir.exists():
        shutil.rmtree(media_dir)
    media_dir.mkdir(parents=True)

    context = _MarkdownContext(
        media_dir=media_dir,
        media_dir_name=media_dir_name,
        inline_html_bytes=inline_html_bytes,
    )
    markdown = _render_document(
        snapshot=snapshot,
        cells=cells,
        context=context,
        scenario_id=scenario_id,
        scenario_state=scenario_state,
        bundle_id=bundle_id,
        title=title,
    )

    output_path = output_root / "output.md"
    output_path.write_text(markdown, encoding="utf-8")
    return MarkdownExportResult(
        output_path=output_path,
        media_dir=media_dir,
        scenario_id=scenario_id,
        bundle_id=bundle_id,
        cell_count=len(cells),
        output_count=sum(_output_count(cell) for cell in cells),
        media_files=tuple(context.media_files),
    )


@dataclass
class _MarkdownContext:
    media_dir: Path
    media_dir_name: str
    inline_html_bytes: int
    media_files: list[Path] = field(default_factory=list)

    def write_media(self, filename: str, data: bytes) -> str:
        path = self.media_dir / filename
        path.write_bytes(data)
        self.media_files.append(path)
        return f"{self.media_dir_name}/{filename}"


def _render_document(
    *,
    snapshot: Mapping[str, Any],
    cells: list[Any],
    context: _MarkdownContext,
    scenario_id: str,
    scenario_state: Mapping[str, Any] | None,
    bundle_id: str | None,
    title: str | None,
) -> str:
    notebook = snapshot.get("notebook")
    notebook_name = (
        str(notebook.get("name"))
        if isinstance(notebook, Mapping) and notebook.get("name") is not None
        else "marimo notebook"
    )
    document_title = title or notebook_name
    output_count = sum(_output_count(cell) for cell in cells)

    parts = [
        "---",
        f'title: "{_yaml_string(document_title)}"',
        "format:",
        "  html:",
        "    toc: true",
        "    code-fold: false",
        "---",
        "",
        _markdown_style(),
        "",
        f"- Notebook: `{notebook_name}`",
        f"- Scenario: `{scenario_id}`",
        f"- Cells: `{len(cells)}`",
        f"- Outputs: `{output_count}`",
    ]
    if bundle_id is not None:
        parts.append(f"- Bundle: `{bundle_id}`")
    parts.append("")
    if scenario_state:
        parts.extend(
            [
                "<details>",
                "<summary>Scenario state</summary>",
                "",
                _code_fence(
                    "json",
                    json.dumps(scenario_state, ensure_ascii=False, indent=2),
                ),
                "",
                "</details>",
                "",
            ]
        )

    for ordinal, cell in enumerate(cells, start=1):
        if not isinstance(cell, Mapping):
            raise ValueError(f"cell {ordinal} is not an object")
        parts.extend(_render_cell(cell, ordinal=ordinal, context=context))

    return "\n".join(parts).rstrip() + "\n"


def _render_cell(
    cell: Mapping[str, Any],
    *,
    ordinal: int,
    context: _MarkdownContext,
) -> list[str]:
    cell_id = str(cell.get("id", f"cell-{ordinal}"))
    name = cell.get("name")

    source = cell.get("source")
    if not isinstance(source, str):
        source = ""

    parts = [
        "---",
        "",
        _cell_label(ordinal, name),
        f"<!-- marimo-cell: id={cell_id} index={cell.get('index', ordinal - 1)} -->",
        "",
        _code_fence("python", source),
        "",
    ]

    outputs = cell.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        return parts

    for index, output in enumerate(outputs, start=1):
        if not isinstance(output, Mapping):
            raise ValueError(f"output {index} for cell {ordinal} is not an object")
        output = cast(Mapping[str, Any], output)
        if len(outputs) > 1:
            parts.extend(
                [
                    f'<div class="moexport-output-label">Output {index}</div>',
                    "",
                ]
            )
        parts.extend(
            _render_output(
                output, cell_ordinal=ordinal, output_index=index, context=context
            )
        )
        parts.append("")
    return parts


def _cell_label(ordinal: int, name: object) -> str:
    label = f"Cell {ordinal:02d}"
    if isinstance(name, str) and name:
        label = f"{label} · <code>{html.escape(name)}</code>"
    return f'<div class="moexport-cell-label">{label}</div>'


def _render_output(
    output: Mapping[str, Any],
    *,
    cell_ordinal: int,
    output_index: int,
    context: _MarkdownContext,
) -> list[str]:
    mimetype = str(output.get("mimetype") or "text/plain")
    data = output.get("data")

    if mimetype == "text/markdown" and isinstance(data, str):
        return [_html_to_markdown(data) if _looks_like_html(data) else data.rstrip()]

    if mimetype == "text/html" and isinstance(data, str):
        payload = data.encode("utf-8")
        if len(payload) <= context.inline_html_bytes and "<script" not in data.lower():
            return [_html_to_markdown(data)]
        raw_href = context.write_media(
            _media_name(cell_ordinal, output_index, ".html"),
            payload,
        )
        preview = _marimo_mime_html_preview(data)
        if preview is not None:
            png_bytes = _vegalite_png(preview)
            if png_bytes is not None:
                image_href = context.write_media(
                    _media_name(cell_ordinal, output_index, ".png"),
                    png_bytes,
                )
                return [
                    f"[Open raw HTML output]({raw_href})",
                    "",
                    f"![Cell {cell_ordinal:02d} output {output_index}]({image_href})",
                ]
            else:
                return [
                    f"[Open raw HTML output]({raw_href})",
                    "",
                    _html_to_markdown(preview),
                ]
        return [
            f"[Open HTML output]({raw_href})",
        ]

    if mimetype.startswith("image/"):
        suffix = _image_suffix(mimetype)
        href = context.write_media(
            _media_name(cell_ordinal, output_index, suffix),
            _image_bytes(data),
        )
        return [f"![Cell {cell_ordinal:02d} output {output_index}]({href})"]

    if _is_json_mimetype(mimetype):
        text = json.dumps(data, ensure_ascii=False, indent=2)
        href = context.write_media(
            _media_name(cell_ordinal, output_index, ".json"),
            text.encode("utf-8"),
        )
        return [
            f"[Open JSON output]({href})",
            "",
            _code_fence("json", text),
        ]

    if isinstance(data, str):
        return [_code_fence(_fence_info(mimetype), data)]

    text = json.dumps(data, ensure_ascii=False, indent=2)
    href = context.write_media(
        _media_name(cell_ordinal, output_index, ".json"),
        text.encode("utf-8"),
    )
    return [f"[Open serialized output]({href})", "", _code_fence("json", text)]


def _markdown_style() -> str:
    return """\
<style>
.moexport-cell-label,
.moexport-output-label {
  color: #64748b;
  font-size: 0.875rem;
  line-height: 1.4;
  margin-bottom: 0.5rem;
}

.moexport-output-label {
  margin-top: 1rem;
}

.moexport-output-frame {
  width: 100%;
  min-height: 420px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
}
</style>"""


def _html_to_markdown(raw_html: str) -> str:
    cleaned = _STYLE_BLOCK_RE.sub("", raw_html)
    converted = md(cleaned, heading_style="ATX", bullets="-").strip()
    return converted or raw_html.rstrip()


def _looks_like_html(text: str) -> bool:
    return text.lstrip().startswith("<")


class _MarimoMimeRendererParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.html_outputs: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "marimo-mime-renderer":
            return

        data = dict(attrs).get("data-data")
        if data is None:
            return

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return

        html = payload.get("text/html") if isinstance(payload, dict) else None
        if isinstance(html, str) and html.strip():
            self.html_outputs.append(html)


def _marimo_mime_html_preview(html: str) -> str | None:
    parser = _MarimoMimeRendererParser()
    parser.feed(html)
    if not parser.html_outputs:
        return None
    return "\n".join(parser.html_outputs)


def _vegalite_png(html: str) -> bytes | None:
    spec = _extract_vegalite_spec(html)
    if spec is None:
        return None

    try:
        vl_convert = importlib.import_module("vl_convert")
    except ImportError as exc:
        raise RuntimeError(
            "Vega-Lite Markdown image export needs vl-convert-python. "
            "Install the capture package with the altair extra."
        ) from exc
    png = vl_convert.vegalite_to_png(
        vl_spec=json.dumps(spec, allow_nan=False),
        scale=2,
    )
    return bytes(png)


def _extract_vegalite_spec(html: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\}\s*\)\s*\(", html):
        start = match.end()
        while start < len(html) and html[start].isspace():
            start += 1
        if start >= len(html) or html[start] != "{":
            continue

        try:
            spec, end = decoder.raw_decode(html[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(spec, dict):
            continue

        tail = html[start + end : start + end + 120]
        if _looks_like_vegalite_spec(spec, tail):
            return cast(dict[str, Any], spec)
    return None


def _looks_like_vegalite_spec(spec: Mapping[str, Any], tail: str) -> bool:
    schema = spec.get("$schema")
    return (
        (isinstance(schema, str) and "vega-lite" in schema)
        or '"vega-lite"' in tail
        or ("mark" in spec and "encoding" in spec)
    )


def _output_count(cell: Any) -> int:
    if not isinstance(cell, Mapping):
        return 0
    outputs = cell.get("outputs")
    return len(outputs) if isinstance(outputs, list) else 0


def _media_name(cell_ordinal: int, output_index: int, suffix: str) -> str:
    return f"cell-{cell_ordinal:02d}-output-{output_index:02d}{suffix}"


def _image_suffix(mimetype: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/webp": ".webp",
    }.get(mimetype, ".img")


def _image_bytes(data: object) -> bytes:
    if isinstance(data, str):
        if data.startswith("data:"):
            _, _, data = data.partition(",")
        return base64.b64decode(data)
    raise TypeError("image output data must be a base64 string")


def _is_json_mimetype(mimetype: str) -> bool:
    return mimetype == "application/json" or mimetype.endswith("+json")


def _fence_info(mimetype: str) -> str:
    return {
        "text/plain": "text",
        "text/x-python": "python",
        "application/javascript": "javascript",
        "text/javascript": "javascript",
        "text/css": "css",
    }.get(mimetype, "text")


def _code_fence(info: str, text: str) -> str:
    longest = max(
        (len(match.group(0)) for match in re.finditer(r"`+", text)), default=0
    )
    fence = "`" * max(3, longest + 1)
    return f"{fence}{info}\n{text.rstrip()}\n{fence}"


def _escape_markdown(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")


def _yaml_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')
