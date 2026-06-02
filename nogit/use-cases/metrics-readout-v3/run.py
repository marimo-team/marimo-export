from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import html
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

USECASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = next(
    parent
    for parent in USECASE_DIR.parents
    if (parent / "packages" / "capture").is_dir()
)
CAPTURE_PACKAGE = REPO_ROOT / "packages" / "capture"
CAPTURE_SRC = CAPTURE_PACKAGE / "src"
PLAN_PATH = USECASE_DIR / "readout_plan.json"
SPEC_PATH = USECASE_DIR / "metrics-readout.spec.json"
BUNDLE_ROOT = USECASE_DIR / "bundle"
OUTPUT_DIR = USECASE_DIR / "output"
RUN_REPORT_PATH = USECASE_DIR / "run-report.json"
READOUT_SCHEMA = "metrics.readout.v1"
INSIDE_UV_ENV = "METRICS_READOUT_V3_INSIDE_UV"
STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
MARIMO_RENDERER_RE = re.compile(
    r"<marimo-mime-renderer\b[^>]*>\s*</marimo-mime-renderer>",
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class RenderStats:
    items: int = 0
    rendered: int = 0
    empty: int = 0
    errors: int = 0
    images: int = 0
    json_assets: int = 0
    html_assets: int = 0
    unsupported_outputs: int = 0
    assets: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RenderContext:
    assets_dir: Path
    stats: RenderStats = field(default_factory=RenderStats)

    def write_asset(self, filename: str, data: bytes) -> str:
        path = self.assets_dir / filename
        path.write_bytes(data)
        href = f"assets/{filename}"
        self.stats.assets.append(href)
        return href


@dataclass
class RenderedBlock:
    html: list[str] = field(default_factory=list)
    markdown: list[str] = field(default_factory=list)


class MarimoRendererParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.renderers: list[dict[str, Any]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag != "marimo-mime-renderer":
            return
        attr_map = dict(attrs)
        mime = loads_json_attr(attr_map.get("data-mime"))
        payload = loads_json_attr(attr_map.get("data-data"))
        if isinstance(mime, str):
            self.renderers.append({"mime": mime, "data": payload})


def main() -> None:
    ensure_uv_environment()

    parser = argparse.ArgumentParser(
        description="Capture master-metrics.py and write the weekly metrics report.",
    )
    parser.add_argument("--server", default="http://localhost:8787")
    parser.add_argument("--notebook", default="master-metrics.py")
    parser.add_argument("--session-id")
    parser.add_argument("--token")
    parser.add_argument("--reuse-runtime", action="store_true")
    parser.add_argument("--plan", type=Path, default=PLAN_PATH)
    parser.add_argument("--spec", type=Path, default=SPEC_PATH)
    parser.add_argument("--bundle", type=Path, default=BUNDLE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--run-report", type=Path, default=RUN_REPORT_PATH)
    parser.add_argument("--skip-capture", action="store_true")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    plan = load_plan(args.plan)
    spec = build_spec(plan)
    args.spec.write_text(json.dumps(spec, indent=2), encoding="utf-8")

    capture_report: dict[str, Any] | None = None
    if not args.skip_capture:
        capture_report = capture_bundle(args, spec)

    payload, manifest_path = read_readout_payload(args.bundle)
    stats = write_outputs(
        payload,
        manifest_path=manifest_path,
        output_dir=args.output_dir,
    )
    write_reader_check(args.output_dir)

    report = {
        "server": args.server.rstrip("/"),
        "notebook": args.notebook,
        "plan": display_path(args.plan),
        "spec": display_path(args.spec),
        "bundle": display_path(args.bundle),
        "manifest_path": display_path(manifest_path),
        "output": {
            "html": display_path(args.output_dir / "index.html"),
            "markdown": display_path(args.output_dir / "metrics-readout.md"),
            "reader_check": display_path(args.output_dir / "reader-check.html"),
        },
        "capture": capture_report,
        "stats": stats.__dict__,
    }
    args.run_report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    if args.strict and stats.errors:
        raise SystemExit(f"report contains {stats.errors} captured cell error(s)")


def ensure_uv_environment() -> None:
    if os.environ.get(INSIDE_UV_ENV) == "1":
        return
    env = os.environ.copy()
    env[INSIDE_UV_ENV] = "1"
    os.execvpe(
        "uv",
        [
            "uv",
            "run",
            "--project",
            str(CAPTURE_PACKAGE),
            "--extra",
            "all",
            "python",
            str(Path(__file__).resolve()),
            *sys.argv[1:],
        ],
        env,
    )


def load_plan(path: Path) -> dict[str, Any]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise TypeError("readout plan must be a JSON object")
    items = plan.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("readout plan must contain at least one item")
    return plan


def build_spec(plan: dict[str, Any]) -> dict[str, Any]:
    scenario_id = str(plan.get("scenario_id") or "current")
    title = str(plan.get("title") or "Metrics Readout")
    items = [item for item in plan["items"] if isinstance(item, dict) and item.get("cell_id")]
    report_cells = [
        {
            "id": str(item["cell_id"]),
            "label": str(item.get("label") or item["cell_id"]),
            "order": int(item.get("order", 0)),
        }
        for item in items
    ]
    return {
        "scenarios": [
            {
                "id": scenario_id,
                "state": dict(plan.get("state") or {}),
            }
        ],
        "provenance": {
            "source": "hash",
            "spec": "embed",
        },
        "values": {
            "readout": {
                "source": {
                    "report": {
                        "cells": report_cells,
                        "include_source": False,
                        "on_error": "record",
                    }
                },
                "formats": [
                    {
                        "format": "metrics",
                        "export": {
                            "type": "ref",
                            "ref": "metrics_exporters:readout",
                        },
                        "options": {
                            "title": title,
                            "items": items,
                        },
                    }
                ],
            }
        },
    }


def capture_bundle(args: argparse.Namespace, spec: dict[str, Any]) -> dict[str, Any]:
    from moexport.client import Runtime, connect

    bundle_root = args.bundle.resolve()
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle_root.mkdir(parents=True)

    client = connect(
        args.server.rstrip("/"),
        notebook=args.notebook,
        session_id=args.session_id,
        token=args.token,
        runtime=Runtime(
            package=f"moexport[all] @ file://{CAPTURE_PACKAGE}",
            force=not args.reuse_runtime,
        ),
    )
    result = client.export(
        spec=spec,
        to=bundle_root,
        paths=[CAPTURE_SRC, USECASE_DIR],
        timeout=240,
    )
    return {
        "session": {
            "session_id": result.session_id,
            "name": result.session_name,
        },
        "bundle_path": display_path(result.bundle_path),
        "manifest_path": display_path(result.manifest_path),
        "invocation_path": display_path(result.invocation_path),
        "invocation_index_path": display_path(result.invocation_index_path),
        "value_count": len(result.manifest["values"]),
        "scenario_count": len(result.manifest["scenarios"]),
    }


def display_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    for root in (USECASE_DIR, REPO_ROOT):
        try:
            return str(resolved.relative_to(root))
        except ValueError:
            continue
    return resolved.name


def read_readout_payload(path: Path) -> tuple[dict[str, Any], Path]:
    from moexport.query import open_export

    export = open_export(path)
    format_row = export.format(value="readout", format="metrics")
    entry = export.entry(
        value="readout",
        format="metrics",
        include_content=True,
        max_bytes=64 * 1024 * 1024,
    )
    manifest_path = format_row.get("manifest_path")
    if not isinstance(manifest_path, str):
        raise ValueError("readout format did not expose a manifest path")
    content = entry.get("content")
    if not isinstance(content, dict) or content.get("type") != "json":
        raise ValueError("readout entry must be a JSON artifact")
    payload = content.get("value")
    if not isinstance(payload, dict):
        raise ValueError("readout entry JSON must be an object")
    if payload.get("schema") != READOUT_SCHEMA:
        raise ValueError(f"expected {READOUT_SCHEMA}, got {payload.get('schema')!r}")
    return payload, Path(manifest_path)


def write_outputs(
    payload: dict[str, Any],
    *,
    manifest_path: Path,
    output_dir: Path,
) -> RenderStats:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True)
    context = RenderContext(assets_dir=assets_dir)

    title = str(payload.get("title") or "Metrics Readout")
    notebook = payload.get("notebook") if isinstance(payload.get("notebook"), dict) else {}
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("readout payload must contain an items list")

    html_blocks: list[str] = []
    markdown_blocks: list[str] = [
        f"# {title}",
        "",
        f"_Generated from `{notebook.get('name', 'running notebook')}` on {generated_at}._",
        "",
    ]

    for item in sorted((item for item in items if isinstance(item, dict)), key=item_order):
        context.stats.items += 1
        block = render_item(item, context)
        if block.html:
            html_blocks.extend(block.html)
        if block.markdown:
            markdown_blocks.extend(block.markdown)
            markdown_blocks.append("")

    markdown_blocks.extend(markdown_notes(payload, manifest_path=manifest_path, stats=context.stats))
    (output_dir / "metrics-readout.md").write_text(
        clean_markdown("\n".join(markdown_blocks)),
        encoding="utf-8",
    )
    (output_dir / "index.html").write_text(
        report_html(
            title=title,
            notebook_name=str(notebook.get("name") or "running notebook"),
            generated_at=generated_at,
            manifest_path=manifest_path,
            blocks=html_blocks,
            stats=context.stats,
        ),
        encoding="utf-8",
    )
    (output_dir / "render-report.json").write_text(
        json.dumps(context.stats.__dict__, indent=2),
        encoding="utf-8",
    )
    return context.stats


def item_order(item: dict[str, Any]) -> tuple[int, str]:
    order = item.get("order")
    return (int(order) if isinstance(order, int) else 0, str(item.get("cell_id") or ""))


def render_item(item: dict[str, Any], context: RenderContext) -> RenderedBlock:
    label = str(item.get("label") or item.get("cell_id") or "Untitled")
    cell_id = str(item.get("cell_id") or "cell")
    status = item.get("status")
    if status == "error":
        context.stats.errors += 1
        error = item.get("error") if isinstance(item.get("error"), dict) else {}
        context.stats.diagnostics.append(
            {
                "cell_id": cell_id,
                "label": label,
                "type": str(error.get("type") or "Error"),
                "message": str(error.get("message") or "unknown error"),
            }
        )
        return RenderedBlock()
    if status == "empty":
        context.stats.empty += 1
        return RenderedBlock()
    if status != "ok":
        return RenderedBlock()

    mimetype = str(item.get("mimetype") or "text/plain")
    data = item.get("data")
    block = RenderedBlock()

    if mimetype == "text/markdown" and isinstance(data, str):
        if looks_like_html(data):
            block = render_html(data, cell_id=cell_id, context=context)
        else:
            text = demote_headings(data.strip())
            if text:
                block.html.append(markdownish_to_html(text))
                block.markdown.append(text)
    elif mimetype == "text/html" and isinstance(data, str):
        block = render_html(data, cell_id=cell_id, context=context)
    elif mimetype.startswith("image/") and isinstance(data, str):
        href = context.write_asset(f"{cell_id}{image_suffix(mimetype)}", image_bytes(data))
        context.stats.images += 1
        block.html.append(
            f'<figure class="artifact"><img src="{html.escape(href)}" alt="{html.escape(label)}"></figure>'
        )
        block.markdown.append(f"![{label}]({href})")
    else:
        context.stats.unsupported_outputs += 1
        context.stats.diagnostics.append(
            {
                "cell_id": cell_id,
                "label": label,
                "type": "UnsupportedOutput",
                "message": mimetype,
            }
        )

    if block.html or block.markdown:
        context.stats.rendered += 1
        block.html.insert(
            0,
            f'<section class="report-item" data-cell="{html.escape(cell_id)}"><div class="item-label">{html.escape(label)}</div>',
        )
        block.html.append("</section>")
    return block


def render_html(raw_html: str, *, cell_id: str, context: RenderContext) -> RenderedBlock:
    renderers = marimo_renderers(raw_html)
    prose_html = MARIMO_RENDERER_RE.sub("", STYLE_BLOCK_RE.sub("", raw_html)).strip()
    block = RenderedBlock()
    if prose_html:
        cleaned = sanitize_fragment(prose_html)
        text = demote_headings(html_to_markdownish(cleaned))
        if cleaned.strip():
            block.html.append(cleaned)
        if text:
            block.markdown.append(text)

    chart_index = 0
    for renderer in renderers:
        mime = str(renderer.get("mime") or "")
        payload = renderer.get("data")
        if "vegalite" in mime:
            spec = parse_vegalite_payload(payload)
            if spec is None:
                context.stats.unsupported_outputs += 1
                continue
            chart_index += 1
            chart = write_vegalite_assets(
                spec,
                cell_id=cell_id,
                chart_index=chart_index,
                context=context,
            )
            block.html.extend(chart.html)
            block.markdown.extend(chart.markdown)
            continue
        if mime == "text/html" and isinstance(payload, str):
            nested = render_html(payload, cell_id=cell_id, context=context)
            block.html.extend(nested.html)
            block.markdown.extend(nested.markdown)
            continue
        context.stats.unsupported_outputs += 1

    if block.html or block.markdown:
        return block

    href = context.write_asset(f"{cell_id}.html", raw_html.encode("utf-8"))
    context.stats.html_assets += 1
    return RenderedBlock(
        html=[f'<p><a href="{html.escape(href)}">Open HTML output</a></p>'],
        markdown=[f"[Open HTML output]({href})"],
    )


def write_vegalite_assets(
    spec: dict[str, Any],
    *,
    cell_id: str,
    chart_index: int,
    context: RenderContext,
) -> RenderedBlock:
    stem = f"{cell_id}-chart-{chart_index:02d}"
    normalized = normalize_container_widths(spec)
    spec_href = context.write_asset(
        f"{stem}.vl.json",
        json.dumps(normalized, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    context.stats.json_assets += 1

    try:
        import vl_convert

        png = vl_convert.vegalite_to_png(
            vl_spec=json.dumps(normalized, allow_nan=False),
            scale=2,
        )
    except Exception as exc:
        context.stats.unsupported_outputs += 1
        return RenderedBlock(
            html=[
                f'<p><a href="{html.escape(spec_href)}">Open Vega-Lite spec</a></p>',
                f'<p class="diagnostic">PNG rendering failed: {html.escape(type(exc).__name__)}: {html.escape(str(exc))}</p>',
            ],
            markdown=[
                f"[Open Vega-Lite spec]({spec_href})",
                f"> PNG rendering failed: {type(exc).__name__}: {exc}",
            ],
        )

    png_href = context.write_asset(f"{stem}.png", bytes(png))
    context.stats.images += 1
    return RenderedBlock(
        html=[
            '<figure class="artifact chart">',
            f'<img src="{html.escape(png_href)}" alt="{html.escape(cell_id)} chart {chart_index}">',
            f'<figcaption><a href="{html.escape(spec_href)}">Vega-Lite spec</a></figcaption>',
            "</figure>",
        ],
        markdown=[
            f"![{cell_id} chart {chart_index}]({png_href})",
            f"[Vega-Lite spec]({spec_href})",
        ],
    )


def markdown_notes(
    payload: dict[str, Any],
    *,
    manifest_path: Path,
    stats: RenderStats,
) -> list[str]:
    notes = [
        "## Export Notes",
        "",
        f"- Bundle manifest: `{display_path(manifest_path)}`",
        f"- Bundle schema: `{READOUT_SCHEMA}`",
        f"- Planned items: `{len(payload.get('items', []))}`",
        f"- Rendered items: `{stats.rendered}`",
        f"- Visualization images: `{stats.images}`",
        f"- Diagnostics: `{len(stats.diagnostics)}`",
    ]
    if stats.diagnostics:
        notes.extend(["", "## Diagnostics", ""])
        for diagnostic in stats.diagnostics:
            notes.append(
                f"- `{diagnostic['cell_id']}` {diagnostic['label']}: "
                f"{diagnostic['type']}: {diagnostic['message']}"
            )
    return notes


def report_html(
    *,
    title: str,
    notebook_name: str,
    generated_at: str,
    manifest_path: Path,
    blocks: list[str],
    stats: RenderStats,
) -> str:
    diagnostics = ""
    if stats.diagnostics:
        rows = "\n".join(
            "<tr>"
            f"<td>{html.escape(item['cell_id'])}</td>"
            f"<td>{html.escape(item['label'])}</td>"
            f"<td>{html.escape(item['type'])}</td>"
            f"<td>{html.escape(item['message'])}</td>"
            "</tr>"
            for item in stats.diagnostics
        )
        diagnostics = (
            '<section class="diagnostics">'
            "<h2>Diagnostics</h2>"
            "<table><thead><tr><th>Cell</th><th>Label</th><th>Type</th><th>Message</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
            "</section>"
        )
    body = "\n".join(blocks)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172033;
      --muted: #5c667a;
      --line: #d9e0ea;
      --panel: #ffffff;
      --page: #f6f8fb;
      --blue: #2857d8;
      --green: #157f52;
      --gold: #9a6500;
      --red: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font: 15px/1.55 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--page);
    }}
    header {{
      padding: 32px 28px 20px;
      background: #fff;
      border-bottom: 1px solid var(--line);
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px 20px 48px; }}
    h1 {{ margin: 0 0 8px; font-size: clamp(28px, 5vw, 48px); line-height: 1.05; letter-spacing: 0; }}
    h2 {{ margin: 32px 0 12px; font-size: 24px; letter-spacing: 0; }}
    h3 {{ margin: 24px 0 10px; font-size: 18px; letter-spacing: 0; }}
    p {{ margin: 0 0 12px; }}
    a {{ color: var(--blue); }}
    .subhead {{ color: var(--muted); margin: 0; }}
    .metrics {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 10px;
      margin-top: 20px;
      max-width: 760px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: var(--panel);
    }}
    .metric strong {{ display: block; font-size: 24px; line-height: 1; }}
    .metric span {{ color: var(--muted); font-size: 12px; }}
    .report-item {{
      margin: 18px 0;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }}
    .item-label {{
      display: inline-flex;
      margin-bottom: 12px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }}
    .artifact {{ margin: 14px 0 18px; }}
    .artifact img {{
      display: block;
      width: min(100%, 980px);
      height: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }}
    figcaption {{ margin-top: 6px; color: var(--muted); font-size: 13px; }}
    .diagnostics {{
      margin-top: 28px;
      padding: 18px;
      border: 1px solid #f0c4bd;
      border-radius: 8px;
      background: #fff8f6;
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }}
    th {{ color: var(--muted); font-weight: 650; }}
    footer {{ color: var(--muted); font-size: 12px; padding: 0 20px 28px; max-width: 1180px; margin: 0 auto; }}
    @media (max-width: 720px) {{
      header {{ padding: 24px 18px 18px; }}
      main {{ padding: 18px 12px 32px; }}
      .report-item {{ padding: 14px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(title)}</h1>
    <p class="subhead">Generated from <code>{html.escape(notebook_name)}</code> on {html.escape(generated_at)}</p>
    <div class="metrics" aria-label="Report summary">
      <div class="metric"><strong>{stats.rendered}</strong><span>Rendered items</span></div>
      <div class="metric"><strong>{stats.images}</strong><span>Image assets</span></div>
      <div class="metric"><strong>{stats.json_assets}</strong><span>Spec assets</span></div>
      <div class="metric"><strong>{len(stats.diagnostics)}</strong><span>Diagnostics</span></div>
    </div>
  </header>
  <main>
    {body}
    {diagnostics}
  </main>
  <footer>
    Bundle manifest: <code>{html.escape(display_path(manifest_path))}</code>
  </footer>
</body>
</html>
"""


def write_reader_check(output_dir: Path) -> None:
    (output_dir / "reader-check.html").write_text(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Metrics Readout Reader Check</title>
  <style>
    body { margin: 0; font: 15px/1.5 system-ui, sans-serif; color: #172033; background: #f6f8fb; }
    main { max-width: 760px; margin: 0 auto; padding: 32px 20px; }
    .panel { background: #fff; border: 1px solid #d9e0ea; border-radius: 8px; padding: 18px; }
    dt { color: #5c667a; margin-top: 12px; }
    dd { margin: 4px 0 0; font-weight: 650; }
    code { overflow-wrap: anywhere; }
  </style>
</head>
<body>
  <main>
    <h1>Reader Check</h1>
    <div class="panel">
      <p id="status">Loading export bundle...</p>
      <dl id="facts"></dl>
    </div>
  </main>
  <script type="importmap">
    {
      "imports": {
        "fflate": "../../../../node_modules/fflate/esm/browser.js"
      }
    }
  </script>
  <script type="module">
    import { readExport } from "../../../../packages/reader/dist/index.js";

    const status = document.querySelector("#status");
    const facts = document.querySelector("#facts");
    const exportRoot = new URL("../bundle/", window.location.href);

    try {
      const exp = await readExport({ root: exportRoot });
      const scenario = exp.scenarios()[0];
      const entry = exp.get({ scenario, value: "readout", format: "metrics" });
      const payload = await entry.json();
      status.textContent = "readExport loaded the latest metrics bundle.";
      facts.innerHTML = [
        ["Bundle id", exp.id],
        ["Scenario", scenario],
        ["Notebook", exp.notebook.name ?? "running notebook"],
        ["Values", exp.values().join(", ")],
        ["Formats", exp.formats("readout").join(", ")],
        ["Readout items", String(Array.isArray(payload.items) ? payload.items.length : 0)],
        ["Source spec hash", exp.sourceSpecSha256 ?? "not recorded"],
      ].map(([label, value]) => `<dt>${label}</dt><dd><code>${value}</code></dd>`).join("");
    } catch (error) {
      status.textContent = `Reader check failed: ${error instanceof Error ? error.message : String(error)}`;
      status.style.color = "#b42318";
    }
  </script>
</body>
</html>
""",
        encoding="utf-8",
    )


def marimo_renderers(raw_html: str) -> list[dict[str, Any]]:
    parser = MarimoRendererParser()
    parser.feed(raw_html)
    return parser.renderers


def loads_json_attr(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return html.unescape(value)


def parse_vegalite_payload(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def normalize_container_widths(spec: dict[str, Any]) -> dict[str, Any]:
    cloned = copy.deepcopy(spec)

    def visit(value: Any) -> Any:
        if isinstance(value, dict):
            for key, item in list(value.items()):
                value[key] = 960 if key == "width" and item == "container" else visit(item)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = visit(item)
        return value

    return visit(cloned)


def sanitize_fragment(raw_html: str) -> str:
    return raw_html.replace("\u2014", " - ")


def html_to_markdownish(raw_html: str) -> str:
    text = raw_html
    text = re.sub(r"</h([1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(
        r"<h([1-6])[^>]*>",
        lambda match: "\n" + "#" * min(6, int(match.group(1)) + 1) + " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = TAG_RE.sub("", text)
    return clean_markdown(html.unescape(text))


def markdownish_to_html(markdown: str) -> str:
    parts: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = len(heading.group(1))
            parts.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
        else:
            parts.append(f"<p>{html.escape(stripped)}</p>")
    return "\n".join(parts)


def demote_headings(markdown: str) -> str:
    lines = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})(\s+.*)$", line)
        if match:
            hashes = "#" * min(6, len(match.group(1)) + 1)
            lines.append(f"{hashes}{match.group(2)}")
        else:
            lines.append(line)
    return "\n".join(lines).strip()


def clean_markdown(markdown: str) -> str:
    normalized = markdown.replace("\u2014", " - ")
    return re.sub(r"\n{3,}", "\n\n", normalized).strip() + "\n"


def looks_like_html(text: str) -> bool:
    return text.lstrip().startswith("<")


def image_suffix(mimetype: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/webp": ".webp",
    }.get(mimetype, ".img")


def image_bytes(data: str) -> bytes:
    if data.startswith("data:"):
        _, _, data = data.partition(",")
    return base64.b64decode(data)


if __name__ == "__main__":
    main()
