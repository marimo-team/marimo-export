"""Render evaluate trace metadata as a Mermaid dataflow chart.

``mox.evaluate(...)`` returns JSON-shaped metadata for each result. This module
is the presentation layer for that metadata: it does not inspect the running
runtime and can render traces from either fresh results or stored provenance.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from typing import Any

JsonMapping = Mapping[str, Any]

_STATUS_LABELS = {
    "executed": "recomputed",
    "cached": "cache hit",
    "pruned": "override supplied",
    "skipped": "skipped",
    "needed": "needed",
    "inactive": "not touched",
}


def trace_mermaid(metadata: JsonMapping) -> str:
    """Return a Mermaid flowchart for one ``mox.evaluate`` result metadata."""

    graph = _expect_mapping(metadata.get("graph"), "metadata.graph")
    execution = _expect_mapping(metadata.get("execution"), "metadata.execution")
    target = _expect_mapping(metadata.get("target", {}), "metadata.target")

    nodes = _expect_sequence(graph.get("nodes"), "metadata.graph.nodes")
    edges = _expect_sequence(graph.get("edges", ()), "metadata.graph.edges")
    steps = _expect_sequence(execution.get("steps", ()), "metadata.execution.steps")
    root_names = {
        str(name)
        for name in _expect_sequence(target.get("root_names", ()), "root_names")
    }
    step_by_cell = {
        str(step["cell_id"]): step
        for step in steps
        if isinstance(step, Mapping) and "cell_id" in step
    }

    node_ids: dict[str, str] = {}
    lines = [
        "%%{init: {'flowchart': {'htmlLabels': true, 'curve': 'basis'}, "
        "'theme': 'base', 'themeVariables': {'fontFamily': 'Inter, ui-sans-serif, system-ui', "
        "'primaryColor': '#f8fafc', 'lineColor': '#94a3b8'}} }%%",
        "flowchart TD",
        f'  trace_summary["{_summary_label(metadata)}"]',
    ]

    for node in nodes:
        node_mapping = _expect_mapping(node, "metadata.graph.nodes[]")
        cell_id = str(node_mapping.get("cell_id", ""))
        if not cell_id:
            continue
        node_id = _node_dom_id(cell_id)
        node_ids[cell_id] = node_id
        step = step_by_cell.get(cell_id)
        lines.append(f'  {node_id}["{_cell_label(node_mapping, step, root_names)}"]')

    for edge in edges:
        edge_mapping = _expect_mapping(edge, "metadata.graph.edges[]")
        source = node_ids.get(str(edge_mapping.get("from", "")))
        target_node = node_ids.get(str(edge_mapping.get("to", "")))
        if source is not None and target_node is not None:
            lines.append(f"  {source} -.-> {target_node}")

    lines.extend(_class_defs())
    lines.append("class trace_summary summary;")

    for node in nodes:
        node_mapping = _expect_mapping(node, "metadata.graph.nodes[]")
        cell_id = str(node_mapping.get("cell_id", ""))
        node_id = node_ids.get(cell_id)
        if node_id is None:
            continue
        css_class = _node_class(node_mapping, root_names)
        lines.append(f"class {node_id} {css_class};")

    return "\n".join(lines)


def _expect_mapping(value: Any, label: str) -> JsonMapping:
    if isinstance(value, Mapping):
        return value
    raise TypeError(f"{label} must be a mapping")


def _expect_sequence(value: Any, label: str) -> Sequence[Any]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise TypeError(f"{label} must be a sequence")
    return value


def _node_dom_id(cell_id: str) -> str:
    return "cell_" + re.sub(r"[^a-zA-Z0-9_]", "_", cell_id)


def _node_class(node: JsonMapping, root_names: set[str]) -> str:
    defs = {str(name) for name in _expect_sequence(node.get("defs", ()), "node.defs")}
    if defs & root_names:
        return "target"
    status = str(node.get("status", "inactive"))
    if status in _STATUS_LABELS:
        return status
    return "inactive"


def _summary_label(metadata: JsonMapping) -> str:
    execution = _expect_mapping(metadata.get("execution"), "metadata.execution")
    target = _expect_mapping(metadata.get("target", {}), "metadata.target")
    stats = _expect_mapping(execution.get("stats", {}), "metadata.execution.stats")
    root_names = ", ".join(
        str(name)
        for name in _expect_sequence(target.get("root_names", ()), "root_names")
    )
    expression_refs = ", ".join(
        str(name)
        for name in _expect_sequence(
            target.get("expression_refs", ()), "expression_refs"
        )
    )
    title = root_names or expression_refs or "expression"

    lines = [
        "<b>mox.evaluate trace</b>",
        f"<span>{_escape(title)}</span>",
        (
            "<small>"
            f"required {_escape(stats.get('required', 0))} | "
            f"executed {_escape(stats.get('executed', 0))} | "
            f"cached {_escape(stats.get('cached', 0))} | "
            f"pruned {_escape(stats.get('pruned', 0))}"
            "</small>"
        ),
        f"<small>{_escape(execution.get('elapsed_ms', 0))} ms</small>",
    ]
    return "<br/>".join(lines)


def _cell_label(
    node: JsonMapping,
    step: JsonMapping | None,
    root_names: set[str],
) -> str:
    cell_id = str(node.get("cell_id", ""))
    short_id = str(node.get("short_id") or cell_id[:8])
    status = str(node.get("status", "inactive"))
    status_label = _STATUS_LABELS.get(status, status)
    defs = [str(value) for value in _expect_sequence(node.get("defs", ()), "node.defs")]
    refs = [str(value) for value in _expect_sequence(node.get("refs", ()), "node.refs")]
    preview = [
        str(value)
        for value in _expect_sequence(node.get("preview", ()), "node.preview")
        if str(value).strip()
    ]
    overridden = [
        str(value)
        for value in _expect_sequence(
            node.get("overridden_defs", ()), "node.overridden_defs"
        )
    ]
    target_defs = sorted(set(defs) & root_names)

    lines = [
        f"<b>cell {_escape(short_id)}</b>",
        f"<span>{_escape(status_label)}</span>",
    ]
    for line in preview[:4]:
        lines.append(f"<code>{_escape(line)}</code>")

    lines.append(f"<small>defs: {_escape(_join_short(defs))}</small>")
    lines.append(f"<small>refs: {_escape(_join_short(refs))}</small>")
    if target_defs:
        lines.append(f"<small>target: {_escape(_join_short(target_defs))}</small>")
    if overridden:
        lines.append(f"<small>overrides: {_escape(_join_short(overridden))}</small>")
    if node.get("skip_reason"):
        lines.append(f"<small>skip: {_escape(node['skip_reason'])}</small>")
    if step is not None:
        if step.get("elapsed_ms"):
            lines.append(f"<small>{_escape(step['elapsed_ms'])} ms</small>")
        if step.get("output_preview") is not None:
            lines.append(f"<small>output: {_escape(step['output_preview'])}</small>")

    return "<br/>".join(lines)


def _join_short(values: Sequence[str], *, limit: int = 6) -> str:
    if not values:
        return "-"
    shown = list(values[:limit])
    if len(values) > limit:
        shown.append(f"+{len(values) - limit}")
    return ", ".join(shown)


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _class_defs() -> list[str]:
    return [
        "classDef summary fill:#ffffff,stroke:#334155,stroke-width:2px,color:#0f172a;",
        "classDef target fill:#dbeafe,stroke:#2563eb,stroke-width:3px,color:#172554;",
        "classDef executed fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#052e16;",
        "classDef cached fill:#ccfbf1,stroke:#0d9488,stroke-width:2px,color:#042f2e;",
        "classDef pruned fill:#fef3c7,stroke:#d97706,stroke-width:2px,color:#451a03;",
        "classDef skipped fill:#ffe4e6,stroke:#e11d48,stroke-width:2px,color:#4c0519;",
        "classDef needed fill:#f8fafc,stroke:#64748b,stroke-width:2px,color:#0f172a;",
        "classDef inactive fill:#f8fafc,stroke:#e2e8f0,color:#64748b;",
    ]
