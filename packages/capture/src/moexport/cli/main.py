"""Click implementation for the `marimo-export` command."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import click

from moexport.cli._common import (
    echo_json,
    export_details,
    export_summary,
    load_spec,
    state_filters,
)
from moexport.notebook import (
    export_notebook,
    inspect_notebook_defs,
    read_notebook_source,
)
from moexport.query import open_export

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"], "max_content_width": 100}


def main(
    args: Sequence[str] | None = None,
    *,
    standalone_mode: bool = True,
    **extra: Any,
) -> Any:
    return cli.main(
        args=list(args) if args is not None else None,
        prog_name="marimo-export",
        standalone_mode=standalone_mode,
        **extra,
    )


@click.group(context_settings=CONTEXT_SETTINGS)
def cli() -> None:
    """Capture marimo notebooks into static bundles, then query the result.

    \b
    Start here:
      marimo-export notebook notebooks/finance.py --spec export.yaml --bundle out
      marimo-export query out

    \b
    The spec is JSON or YAML. Each value has a Python `source` expression
    evaluated in the notebook runtime, and each format references an exporter
    callable such as `moexport.exporters.dataframe:arrow`.

    \b
    Query progressively:
      marimo-export query out                         # catalog
      marimo-export query out scenarios --state chart_width=1200
      marimo-export query out source --scenario wide_chart
      marimo-export query out entries --value summary --format json --content
      marimo-export query out artifacts --value df --format arrow
      marimo-export query out files --media-type image/png
      marimo-export query out graph --scenario wide_chart
    """


@cli.command(
    "notebook",
    context_settings={
        **CONTEXT_SETTINGS,
        "allow_extra_args": True,
        "ignore_unknown_options": True,
    },
)
@click.argument("notebook", metavar="NOTEBOOK")
@click.option(
    "--spec",
    required=True,
    metavar="PATH|-",
    help="JSON/YAML export spec path, or '-' to read the spec from stdin.",
)
@click.option(
    "--bundle",
    metavar="ROOT",
    help="Static export root. Defaults to the spec's bundle.path or runtime default.",
)
@click.option(
    "--check/--no-check",
    default=True,
    show_default=True,
    help="Run marimo's notebook correctness check before execution.",
)
@click.option(
    "--run-arg",
    multiple=True,
    metavar="ARG",
    help="Argument exposed through mo.cli_args(). Repeat for multiple args.",
)
@click.option(
    "--full",
    is_flag=True,
    help="Print the full ExportResult, including evaluation and invocation traces.",
)
@click.argument("notebook_args", nargs=-1, type=click.UNPROCESSED)
def notebook(
    notebook: str,
    spec: str,
    bundle: str | None,
    check: bool,
    run_arg: tuple[str, ...],
    full: bool,
    notebook_args: tuple[str, ...],
) -> None:
    """Run one notebook and write a static export bundle.

    \b
    Read the notebook source before writing the spec. The spec is JSON or YAML:
      values.<name>.source    Python expression, e.g. `df` or `df.head()`
      formats.<name>.export   Python callable ref or inline code defining `export`

    \b
    Custom exporters are normal Python callables. They receive the Python value
    and write portable bundle blobs through the exporter context.
    """

    result = export_notebook(
        notebook,
        load_spec(spec),
        bundle=bundle,
        run={
            "args": [*run_arg, *notebook_args],
            "check": check,
        },
    )
    echo_json(export_details(result) if full else export_summary(result))


@cli.group("inspect")
def inspect() -> None:
    """Inspect a notebook before authoring an export spec.

    \b
    Use these commands to discover real defs and cell names before writing the spec:
      marimo-export inspect defs notebooks/finance.py
      marimo-export inspect source notebooks/finance.py
    """


@inspect.command("source")
@click.option(
    "--json", "as_json", is_flag=True, help="Print path/name metadata plus source text."
)
@click.argument("notebook", metavar="NOTEBOOK")
def inspect_source(notebook: str, as_json: bool) -> None:
    """Print resolved notebook source code."""

    source = read_notebook_source(notebook)
    if as_json:
        echo_json(source)
    else:
        click.echo(source["source"], nl=False)


@inspect.command("defs")
@click.argument("notebook", metavar="NOTEBOOK")
def inspect_defs(notebook: str) -> None:
    """List defs, refs, cells, and root defs discovered by marimo parsing."""

    echo_json(inspect_notebook_defs(notebook))


@cli.group("query", invoke_without_command=True)
@click.argument("path", metavar="PATH")
@click.pass_context
def query(ctx: click.Context, path: str) -> None:
    """Query a static export root, bundle directory, or manifest.

    \b
    With no subcommand, prints the catalog.

    \b
    Subcommands:
      query PATH  overview of bundles, notebooks, scenarios, values, formats
      scenarios   scenario rows, filterable by id and state values
      source      full notebook source stored in the bundle provenance
      artifacts   semantic artifact records for scenario x value x format
      entries     canonical artifact entry files, optionally with small content
      files       raw content-addressed blob files with semantic uses
      trace       latest invocation trace, optionally scoped to a scenario
      graph       notebook dependency graph metadata from the invocation trace
    """

    ctx.obj = {"path": path}
    if ctx.invoked_subcommand is None:
        echo_json(open_export(path).catalog())


@query.command("bundles")
@click.pass_obj
def query_bundles(obj: dict[str, str]) -> None:
    """List bundle summaries without expanding every artifact."""

    echo_json(open_export(obj["path"]).bundles())


@query.command("notebooks")
@click.pass_obj
def query_notebooks(obj: dict[str, str]) -> None:
    """List notebooks represented in an export root."""

    echo_json(open_export(obj["path"]).notebooks())


@query.command("source")
@click.option(
    "--json", "as_json", is_flag=True, help="Print metadata plus source text."
)
@click.option("--bundle", help="Bundle id or id prefix.")
@click.option("--scenario", help="Scenario id.")
@click.option(
    "--state-json",
    help="JSON object of scenario state filters, merged with --state.",
)
@click.option(
    "--state",
    multiple=True,
    metavar="KEY=JSON",
    help="Scenario state filter. Repeat for multiple keys.",
)
@click.pass_obj
def query_source(
    obj: dict[str, str],
    as_json: bool,
    bundle: str | None,
    scenario: str | None,
    state_json: str | None,
    state: tuple[str, ...],
) -> None:
    """Print the stored notebook source for a matching bundle/scenario."""

    try:
        source = open_export(obj["path"]).notebook_source(
            bundle=bundle,
            scenario=scenario,
            state=state_filters(
                state_json=state_json,
                state=state,
            ),
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    if as_json:
        echo_json(source)
    else:
        click.echo(source["text"], nl=False)


@query.command("bundle")
@click.argument("bundle_id", required=False)
@click.option("--summary", is_flag=True, help="Print only the bundle summary.")
@click.pass_obj
def query_bundle(
    obj: dict[str, str],
    bundle_id: str | None,
    summary: bool,
) -> None:
    """Show one bundle map: values, scenarios, artifacts, files, and traces."""

    bundle = open_export(obj["path"]).bundle(bundle_id)
    echo_json(bundle.summary() if summary else bundle.map())


def query_filters(
    *,
    include_format: bool,
    include_artifact: bool,
) -> Any:
    def decorator(fn: Any) -> Any:
        if include_artifact:
            fn = click.option(
                "--one",
                is_flag=True,
                help="Return one object and fail if the selector is empty or ambiguous.",
            )(fn)
            fn = click.option("--limit", type=int, help="Maximum rows to print.")(fn)
            fn = click.option("--media-type", help="MIME/media type.")(fn)
            fn = click.option("--format-id", help="Exporter-produced format id.")(fn)
        if include_format:
            fn = click.option("--format", help="Authored format name.")(fn)
        fn = click.option("--value", help="Exported value name.")(fn)
        fn = click.option(
            "--state-json",
            help="JSON object of scenario state filters, merged with --state.",
        )(fn)
        fn = click.option(
            "--state",
            multiple=True,
            metavar="KEY=JSON",
            help="Scenario state filter. Repeat for multiple keys.",
        )(fn)
        fn = click.option("--scenario", help="Scenario id.")(fn)
        fn = click.option("--bundle", help="Bundle id or id prefix.")(fn)
        return fn

    return decorator


@query.command("scenarios")
@query_filters(include_format=False, include_artifact=False)
@click.pass_obj
def query_scenarios(obj: dict[str, str], **filters: Any) -> None:
    """List scenario rows, optionally narrowed by structured filters."""

    echo_json(
        open_export(obj["path"]).scenarios(
            bundle=filters["bundle"],
            scenario=filters["scenario"],
            state=_state(filters),
            value=filters["value"],
        )
    )


@query.command("values")
@click.option("--bundle", help="Bundle id or id prefix.")
@click.option("--value", help="Exported value name.")
@click.pass_obj
def query_values(obj: dict[str, str], bundle: str | None, value: str | None) -> None:
    """List exported value expressions and authored formats."""

    echo_json(open_export(obj["path"]).values(bundle=bundle, value=value))


@query.command("formats")
@query_filters(include_format=True, include_artifact=False)
@click.pass_obj
def query_formats(obj: dict[str, str], **filters: Any) -> None:
    """List available formats grouped by value and format name."""

    echo_json(
        open_export(obj["path"]).formats(
            bundle=filters["bundle"],
            scenario=filters["scenario"],
            state=_state(filters),
            value=filters["value"],
            format=filters["format"],
        )
    )


@query.command("artifacts")
@query_filters(include_format=True, include_artifact=True)
@click.pass_obj
def query_artifacts(obj: dict[str, str], **filters: Any) -> None:
    """List artifact descriptors with metadata and entry paths."""

    export = open_export(obj["path"])
    try:
        if filters["one"]:
            echo_json(
                export.artifact(
                    bundle=filters["bundle"],
                    scenario=filters["scenario"],
                    state=_state(filters),
                    value=filters["value"],
                    format=filters["format"],
                    format_id=filters["format_id"],
                    media_type=filters["media_type"],
                )
            )
            return

        echo_json(
            export.artifacts(
                bundle=filters["bundle"],
                scenario=filters["scenario"],
                state=_state(filters),
                value=filters["value"],
                format=filters["format"],
                format_id=filters["format_id"],
                media_type=filters["media_type"],
                limit=filters["limit"],
            )
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@query.command("files")
@query_filters(include_format=True, include_artifact=True)
@click.option(
    "--dedupe/--no-dedupe",
    default=True,
    show_default=True,
    help="Group file rows by href and collect semantic uses.",
)
@click.pass_obj
def query_files(obj: dict[str, str], dedupe: bool, **filters: Any) -> None:
    """List raw blob files with semantic usage records."""

    export = open_export(obj["path"])
    try:
        if filters["one"]:
            echo_json(
                export.file(
                    bundle=filters["bundle"],
                    scenario=filters["scenario"],
                    state=_state(filters),
                    value=filters["value"],
                    format=filters["format"],
                    format_id=filters["format_id"],
                    media_type=filters["media_type"],
                    dedupe=dedupe,
                )
            )
            return

        echo_json(
            export.files(
                bundle=filters["bundle"],
                scenario=filters["scenario"],
                state=_state(filters),
                value=filters["value"],
                format=filters["format"],
                format_id=filters["format_id"],
                media_type=filters["media_type"],
                dedupe=dedupe,
                limit=filters["limit"],
            )
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@query.command("entries")
@query_filters(include_format=True, include_artifact=True)
@click.option(
    "--content",
    is_flag=True,
    help="Inline small JSON/text entry content. Binary files are described, not decoded.",
)
@click.option(
    "--max-bytes",
    default=65_536,
    show_default=True,
    type=click.IntRange(min=0),
    help="Maximum entry size to inline when --content is used.",
)
@click.pass_obj
def query_entries(
    obj: dict[str, str],
    content: bool,
    max_bytes: int,
    **filters: Any,
) -> None:
    """List artifact entry files with scenario/value/format metadata."""

    export = open_export(obj["path"])
    try:
        if filters["one"]:
            echo_json(
                export.entry(
                    bundle=filters["bundle"],
                    scenario=filters["scenario"],
                    state=_state(filters),
                    value=filters["value"],
                    format=filters["format"],
                    format_id=filters["format_id"],
                    media_type=filters["media_type"],
                    include_content=content,
                    max_bytes=max_bytes,
                )
            )
            return

        echo_json(
            export.entries(
                bundle=filters["bundle"],
                scenario=filters["scenario"],
                state=_state(filters),
                value=filters["value"],
                format=filters["format"],
                format_id=filters["format_id"],
                media_type=filters["media_type"],
                include_content=content,
                max_bytes=max_bytes,
                limit=filters["limit"],
            )
        )
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


@query.command("traces")
@click.option("--bundle", help="Bundle id or id prefix.")
@click.pass_obj
def query_traces(obj: dict[str, str], bundle: str | None) -> None:
    """List invocation trace files for one bundle."""

    echo_json(open_export(obj["path"]).bundle(bundle).traces())


def trace_options(fn: Any) -> Any:
    fn = click.option(
        "--invocation",
        help="Invocation id or sha256 prefix. Defaults to the latest invocation.",
    )(fn)
    fn = click.option("--scenario", help="Scenario id.")(fn)
    fn = click.option("--bundle", help="Bundle id or id prefix.")(fn)
    return fn


@query.command("trace")
@trace_options
@click.pass_obj
def query_trace(
    obj: dict[str, str],
    bundle: str | None,
    scenario: str | None,
    invocation: str | None,
) -> None:
    """Load an invocation trace, optionally scoped to one scenario."""

    echo_json(
        open_export(obj["path"])
        .bundle(bundle)
        .trace(scenario=scenario, invocation=invocation)
    )


@query.command("graph")
@trace_options
@click.pass_obj
def query_graph(
    obj: dict[str, str],
    bundle: str | None,
    scenario: str | None,
    invocation: str | None,
) -> None:
    """Load notebook graph metadata from invocation traces."""

    echo_json(
        open_export(obj["path"])
        .bundle(bundle)
        .graph(scenario=scenario, invocation=invocation)
    )


def _state(filters: dict[str, Any]) -> dict[str, Any]:
    return state_filters(
        state_json=filters["state_json"],
        state=filters["state"],
    )
