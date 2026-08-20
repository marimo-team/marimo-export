from __future__ import annotations

import ast
from dataclasses import replace

import pytest
from marimo._ast.compiler import compile_cell
from marimo._save.hash import hash_cell_impl
from marimo._types.ids import CellId_t
from marimo_export import ExportSpec, OutputSpec
from marimo_export._execution import (
    Baseline,
    CellDefinition,
    Definition,
    PlannedOutput,
    create_execution_plan,
    ordinary_cell_code,
    output_cell_code,
    snapshot_token_name,
)
from marimo_export._execution.plan import exporter_token_name
from marimo_export._json import canonical_bytes, sha256_bytes
from marimo_export.errors import SpecError
from marimo_export.exporters import altair, importable


def _baseline() -> Baseline:
    return Baseline(
        definitions={
            "symbols": Definition(
                name="symbols",
                cell_id="cell-inputs",
                siblings=("chart_width", "interval", "symbols"),
                kind="ordinary",
                python_type="builtins.list",
                value=["AAPL", "MSFT"],
            ),
            "interval": Definition(
                name="interval",
                cell_id="cell-inputs",
                siblings=("chart_width", "interval", "symbols"),
                kind="ordinary",
                python_type="builtins.str",
                value="1d",
            ),
            "chart_width": Definition(
                name="chart_width",
                cell_id="cell-inputs",
                siblings=("chart_width", "interval", "symbols"),
                kind="ordinary",
                python_type="builtins.int",
                value=800,
            ),
            "selector": Definition(
                name="selector",
                cell_id="cell-ui",
                siblings=("selector",),
                kind="ui",
                python_type="marimo.ui.multiselect",
                value=object(),
                frontend_value=["AAPL"],
                control_paths={"selector-control": ()},
            ),
            "df": Definition(
                name="df",
                cell_id="cell-df",
                siblings=("df",),
                kind="ordinary",
                python_type="polars.DataFrame",
                value=object(),
                input_dependencies=("selector",),
            ),
        },
        cells=(),
        document_sha256="a" * 64,
        filename="finance.py",
    )


def _spec() -> ExportSpec:
    return ExportSpec(
        default_state="focus",
        states={
            "wide": {"symbols": ["AAPL", "MSFT", "GOOGL"]},
            "baseline": {},
            "focus": {"selector": ["MSFT"]},
        },
        outputs={"prices": OutputSpec.value("df")},
    )


def test_sparse_rows_normalize_in_canonical_state_order() -> None:
    spec = _spec()
    plan = create_execution_plan(spec, _baseline())

    assert tuple(state.aliases for state in plan.states) == (
        ("baseline",),
        ("focus",),
        ("wide",),
    )
    assert dict(plan.states[0].inputs) == {
        "symbols": ["AAPL", "MSFT"],
        "selector": ["AAPL"],
    }
    assert dict(plan.states[1].inputs) == {
        "symbols": ["AAPL", "MSFT"],
        "selector": ["MSFT"],
    }
    assert plan.states[0].fingerprint != plan.states[1].fingerprint
    assert plan.inputs == ("selector", "symbols")
    assert plan.default_alias == "focus"
    assert plan.default_fingerprint == plan.states[1].fingerprint
    assert plan.spec_sha256 == sha256_bytes(canonical_bytes(spec.to_value()))
    assert plan.state_code == f"{plan.state_name} = {plan.default_fingerprint!r}"


def test_ordinary_overrides_target_the_authored_definition_cell() -> None:
    plan = create_execution_plan(_spec(), _baseline())
    state = plan.states[-1]

    assert dict(state.ordinary_values) == {
        "symbols": ["AAPL", "MSFT", "GOOGL"],
    }
    assert dict(plan.ordinary_cells) == {"cell-inputs": ("symbols",)}
    assert dict(state.ui_updates) == {"selector": ["AAPL"]}


def test_ordinary_override_precedes_the_authored_final_expression() -> None:
    code = ordinary_cell_code(
        "shared = []\nsymbols = ('AAPL',)\nshared",
        ("symbols",),
        {"symbols": ["MSFT", "GOOGL"]},
    )
    cell = compile_cell(code, cell_id=CellId_t("inputs"))
    module = ast.parse(code)

    assert ast.unparse(module.body[-2]) == "symbols = ['MSFT', 'GOOGL']"
    assert ast.unparse(module.body[-1]) == "shared"
    assert cell.defs == {"shared", "symbols"}


def test_ordinary_override_splices_same_line_and_multiline_source() -> None:
    same_line = ordinary_cell_code("amount = 1; amount  # shown", ("amount",), {"amount": 4})
    multiline = ordinary_cell_code(
        "amount = 1\n(\n    amount * 2  # shown\n)\n",
        ("amount",),
        {"amount": 4},
    )

    assert same_line == "amount = 1; amount = 4; amount  # shown"
    assert multiline == "amount = 1\namount = 4; (\n    amount * 2  # shown\n)\n"


def test_ordinary_override_preserves_authored_traceback_line() -> None:
    code = ordinary_cell_code(
        "amount = 1\n# keep this line\n1 / 0  # authored failure\n",
        ("amount",),
        {"amount": 4},
    )

    try:
        exec(compile(code, "cell.py", "exec"), {})
    except ZeroDivisionError as error:
        traceback = error.__traceback__
        assert traceback is not None
        assert traceback.tb_next is not None
        assert traceback.tb_next.tb_lineno == 3
    else:
        raise AssertionError("transient cell did not execute its final expression")
    assert "# keep this line" in code
    assert "# authored failure" in code


def test_ordinary_override_keeps_trailing_semicolon_suppression() -> None:
    code = ordinary_cell_code(
        "amount = 1; amount;  # suppress output\n",
        ("amount",),
        {"amount": 4},
    )

    assert code == "amount = 1; amount;  # suppress output\namount = 4;\n"


def test_equal_normalized_vectors_share_one_state_and_retain_every_alias() -> None:
    spec = ExportSpec(
        default_state="two",
        states={"one": {}, "two": {"symbols": ["AAPL", "MSFT"]}},
        outputs={"prices": OutputSpec.value("df")},
    )

    plan = create_execution_plan(spec, _baseline())

    assert len(plan.states) == 1
    assert plan.states[0].aliases == ("one", "two")
    assert dict(plan.states[0].inputs) == {
        "selector": ["AAPL"],
        "symbols": ["AAPL", "MSFT"],
    }
    assert plan.default_alias == "two"
    assert plan.default_fingerprint == plan.states[0].fingerprint


def test_inputs_are_inferred_from_selected_output_dependencies_and_state_keys() -> None:
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}, "wide": {"symbols": ["MSFT"]}},
        outputs={"prices": OutputSpec.value("df")},
    )

    plan = create_execution_plan(spec, _baseline())

    assert plan.inputs == ("selector", "symbols")
    assert dict(plan.states[0].inputs) == {
        "selector": ["AAPL"],
        "symbols": ["AAPL", "MSFT"],
    }


def _baseline_with_binary_output_widget() -> Baseline:
    baseline = _baseline()
    definitions = dict(baseline.definitions)
    definitions["selector"] = replace(
        definitions["selector"],
        control_paths={"selector-control": ()},
    )
    definitions["binary_widget"] = Definition(
        name="binary_widget",
        cell_id="cell-widget",
        siblings=("binary_widget",),
        kind="ui",
        python_type="example.BinaryAnyWidget",
        value=object(),
        frontend_value=None,
        portable_input=False,
        control_paths={"binary-control": ()},
        input_dependencies=("selector",),
    )
    return Baseline(
        definitions=definitions,
        cells=baseline.cells,
        document_sha256=baseline.document_sha256,
        filename=baseline.filename,
    )


def test_output_ui_infers_dependencies_without_becoming_an_input() -> None:
    baseline = _baseline_with_binary_output_widget()
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"widget": OutputSpec.value("binary_widget")},
    )

    plan = create_execution_plan(spec, baseline)

    assert plan.inputs == ("selector",)
    assert dict(plan.states[0].inputs) == {"selector": ["AAPL"]}


def test_nonportable_ui_authored_as_an_input_is_rejected() -> None:
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {"binary_widget": {}}},
        outputs={"widget": OutputSpec.value("binary_widget")},
    )

    with pytest.raises(SpecError) as raised:
        create_execution_plan(spec, _baseline_with_binary_output_widget())

    assert raised.value.code == "spec_input_invalid"
    assert raised.value.details == {
        "input": "binary_widget",
        "python_type": "example.BinaryAnyWidget",
    }


def test_selected_ui_output_is_inferred_as_its_direct_control_root() -> None:
    baseline = _baseline()
    definitions = dict(baseline.definitions)
    definitions["selector"] = replace(
        definitions["selector"],
        control_paths={"selector-control": ()},
    )
    baseline = replace(baseline, definitions=definitions)
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"selector": OutputSpec.value("selector")},
    )

    plan = create_execution_plan(spec, baseline)

    assert plan.inputs == ("selector",)


def test_selected_ui_output_uses_the_canonical_alias_root() -> None:
    baseline = _baseline()
    definitions = dict(baseline.definitions)
    definitions.update(
        {
            "alias": Definition(
                name="alias",
                cell_id="cell-alias",
                siblings=("alias",),
                kind="ui",
                python_type="example.AliasControl",
                value=object(),
                frontend_value="AAPL",
                control_paths={"shared-control": ()},
                input_dependencies=("child",),
            ),
            "child": Definition(
                name="child",
                cell_id="cell-child",
                siblings=("child",),
                kind="ui",
                python_type="example.ChildControl",
                value=object(),
                frontend_value="AAPL",
                control_paths={"shared-control": ()},
            ),
        }
    )
    baseline = replace(baseline, definitions=definitions)
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"child": OutputSpec.value("child")},
    )

    plan = create_execution_plan(spec, baseline)

    assert plan.inputs == ("alias",)


def test_selected_ui_output_uses_the_composite_control_owner() -> None:
    baseline = _baseline()
    definitions = dict(baseline.definitions)
    definitions.update(
        {
            "child": Definition(
                name="child",
                cell_id="cell-child",
                siblings=("child",),
                kind="ui",
                python_type="example.ChildControl",
                value=object(),
                frontend_value="AAPL",
                control_paths={"child-control": ()},
            ),
            "controls": Definition(
                name="controls",
                cell_id="cell-controls",
                siblings=("controls",),
                kind="ui",
                python_type="example.CompositeControl",
                value=object(),
                frontend_value={"child": "AAPL"},
                control_paths={"child-control": (), "controls-root": ()},
                input_dependencies=("child",),
            ),
        }
    )
    baseline = replace(baseline, definitions=definitions)
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"child": OutputSpec.value("child")},
    )

    plan = create_execution_plan(spec, baseline)

    assert plan.inputs == ("controls",)


def test_complete_cell_dependencies_are_inferred_as_inputs() -> None:
    baseline = _baseline()
    baseline = Baseline(
        definitions=baseline.definitions,
        cells=(
            CellDefinition(
                id="cell-summary",
                name="summary",
                code_sha256="d" * 64,
                config={},
                input_dependencies=("selector",),
            ),
        ),
        document_sha256=baseline.document_sha256,
        filename=baseline.filename,
    )
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {}},
        outputs={"summary": OutputSpec.cell("summary")},
    )

    plan = create_execution_plan(spec, baseline)

    assert plan.inputs == ("selector",)
    assert dict(plan.states[0].inputs) == {"selector": ["AAPL"]}


def test_unknown_state_input_is_rejected_during_planning() -> None:
    spec = ExportSpec(
        default_state="baseline",
        states={"baseline": {"missing": 1}},
        outputs={"prices": OutputSpec.value("df")},
    )

    with pytest.raises(SpecError) as raised:
        create_execution_plan(spec, _baseline())

    assert raised.value.code == "spec_definition_missing"
    assert raised.value.details == {"definitions": ["missing"]}


def test_output_cell_reads_state_and_source_without_definitions() -> None:
    code = output_cell_code(
        PlannedOutput(
            name='chart "main"',
            source=OutputSpec.value("symbols_chart").source,
            exporter=None,
        ),
        "marimo_export_state_0123456789abcdef",
        implementation_identity="a" * 64,
        document_sha256="d" * 64,
        producer_identity="marimo:0.23.16",
    )
    cell = compile_cell(code, cell_id=CellId_t("planned_output"))

    assert cell.defs == set()
    assert cell.refs == {
        "marimo_export_state_0123456789abcdef",
        "symbols_chart",
    }


def test_exporter_output_cell_is_a_deterministic_marimo_leaf() -> None:
    code = output_cell_code(
        PlannedOutput(
            name="snapshot",
            source=OutputSpec.value("performance", altair.png(scale=2)).source,
            exporter=altair.png(scale=2),
        ),
        "marimo_export_state_0123456789abcdef",
        implementation_identity="a" * 64,
        document_sha256="d" * 64,
        producer_identity="marimo:0.23.16",
        exporter_identity="b" * 64,
    )
    tree = ast.parse(code)
    cell = compile_cell(code, cell_id=CellId_t("planned_output"))

    assert cell.defs == set()
    assert cell.refs == {
        "marimo_export_state_0123456789abcdef",
        "performance",
    }
    implementation = tree.body[1]
    assert isinstance(implementation, ast.Assign)
    assert ast.unparse(implementation) == (
        f"_marimo_export_implementation_identity = 'sha256:{'a' * 64}'"
    )
    document = tree.body[2]
    assert isinstance(document, ast.Assign)
    assert ast.unparse(document) == (f"_marimo_export_document_identity = 'sha256:{'d' * 64}'")
    producer = tree.body[3]
    assert isinstance(producer, ast.Assign)
    assert ast.unparse(producer) == "_marimo_export_producer_identity = 'marimo:0.23.16'"
    projection = tree.body[4]
    assert isinstance(projection, ast.Assign)
    identity = tree.body[5]
    assert isinstance(identity, ast.Assign)
    assert ast.unparse(identity) == (f"_marimo_export_exporter_identity = 'sha256:{'b' * 64}'")
    imported = tree.body[6]
    assert isinstance(imported, ast.ImportFrom)
    assert imported.module == "marimo_export.exporters._runtime.altair"
    resolver_import = tree.body[7]
    assert isinstance(resolver_import, ast.ImportFrom)
    assert resolver_import.module == "marimo_export._marimo.compat.projections"
    blob_import = tree.body[8]
    assert isinstance(blob_import, ast.ImportFrom)
    assert blob_import.module == "marimo_export._marimo.blob"
    call = tree.body[9]
    assert isinstance(call, ast.Expr)
    assert isinstance(call.value, ast.Call)
    assert ast.unparse(call.value) == (
        "_marimo_export_native_blob_asset("
        "_marimo_export_exporter(_marimo_export_resolve_value(performance, ()), scale=2))"
    )


def test_custom_exporter_output_cell_uses_an_explicit_importable_callable() -> None:
    code = output_cell_code(
        PlannedOutput(
            name="summary",
            source=OutputSpec.value(
                "result",
                importable(
                    "acme.exports:encode",
                    options={
                        "columns": ["a", "b"],
                        "config": {"compact": True},
                    },
                ),
            ).source,
            exporter=importable(
                "acme.exports:encode",
                options={
                    "columns": ["a", "b"],
                    "config": {"compact": True},
                },
            ),
        ),
        "marimo_export_state_0123456789abcdef",
        implementation_identity="a" * 64,
        document_sha256="d" * 64,
        producer_identity="marimo:0.23.16",
        exporter_identity="c" * 64,
        exporter_token=exporter_token_name(
            importable(
                "acme.exports:encode",
                options={
                    "columns": ["a", "b"],
                    "config": {"compact": True},
                },
            )
        ),
    )

    assert "from acme.exports import encode as _marimo_export_exporter" not in code
    assert "invoke_prepared_exporter as _marimo_export_invoke_exporter" in code
    assert f"_marimo_export_implementation_identity = 'sha256:{'a' * 64}'" in code
    assert f"_marimo_export_exporter_identity = 'sha256:{'c' * 64}'" in code
    assert "marimo_export_exporter_" in code
    assert "to_native_blob_asset as _marimo_export_native_blob_asset" in code
    assert "_marimo_export_resolve_value(result, ())" in code
    assert "{'columns': ['a', 'b'], 'config': {'compact': True}}" in code


def test_rendered_and_complete_cell_leaves_embed_the_implementation_identity() -> None:
    outputs = (
        PlannedOutput(
            name="rendered",
            source=OutputSpec.output("result").source,
            exporter=None,
            owner_cell_id="cell-result",
        ),
        PlannedOutput(
            name="complete",
            source=OutputSpec.cell("summary").source,
            exporter=None,
            cell=CellDefinition(
                id="cell-summary",
                name="summary",
                code_sha256="d" * 64,
                config={},
            ),
            owner_cell_id="cell-summary",
        ),
    )

    for position, output in enumerate(outputs):
        code = output_cell_code(
            output,
            "marimo_export_state_0123456789abcdef",
            implementation_identity="a" * 64,
            document_sha256="d" * 64,
            producer_identity="marimo:0.23.16",
        )
        cell = compile_cell(code, cell_id=CellId_t(f"planned-output-{position}"))

        assert f"_marimo_export_implementation_identity = 'sha256:{'a' * 64}'" in code
        assert cell.defs == set()
        assert snapshot_token_name(output) in cell.refs


def test_projection_runtime_identities_change_the_native_cell_hash() -> None:
    planned = PlannedOutput(
        name="summary",
        source=OutputSpec.value("result").source,
        exporter=None,
    )
    identities = (
        ("a" * 64, "d" * 64, "marimo:0.23.16"),
        ("b" * 64, "d" * 64, "marimo:0.23.16"),
        ("a" * 64, "e" * 64, "marimo:0.23.16"),
        ("a" * 64, "d" * 64, "marimo:0.24.0"),
    )
    cells = tuple(
        compile_cell(
            output_cell_code(
                planned,
                "marimo_export_state_0123456789abcdef",
                implementation_identity=implementation_identity,
                document_sha256=document_sha256,
                producer_identity=producer_identity,
            ),
            cell_id=CellId_t(f"projection-{index}"),
        )
        for index, (implementation_identity, document_sha256, producer_identity) in enumerate(
            identities
        )
    )

    assert all(cell.defs == set() for cell in cells)
    assert len({hash_cell_impl(cell) for cell in cells}) == len(identities)
