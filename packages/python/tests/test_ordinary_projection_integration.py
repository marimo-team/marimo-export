from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from marimo_export import ExportSpec, OutputSpec, build, open_export
from marimo_export._json import decode_json_object
from marimo_export.errors import SpecError


def test_ordinary_overrides_preserve_expression_output_contracts(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def multiline():
    multiline_amount = 1; (
        multiline_amount * 2
    )
    return (multiline_amount,)


@app.cell
def fixed():
    fixed_amount = 1
    "fixed output"
    return (fixed_amount,)


@app.cell
def suppressed():
    suppressed_amount = 1; suppressed_amount;
    return (suppressed_amount,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="selected",
            states={
                "selected": {
                    "fixed_amount": 4,
                    "multiline_amount": 4,
                    "suppressed_amount": 4,
                }
            },
            outputs={
                "fixed_cell": OutputSpec.cell("fixed"),
                "fixed_value": OutputSpec.json("fixed_amount"),
                "multiline_cell": OutputSpec.cell("multiline"),
                "multiline_value": OutputSpec.json("multiline_amount"),
                "suppressed_cell": OutputSpec.cell("suppressed"),
                "suppressed_value": OutputSpec.json("suppressed_amount"),
            },
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    state = open_export(result.path).state("selected")
    fixed = cast(
        dict[str, Any],
        decode_json_object(state.output("fixed_cell").asset_bytes(), "cell snapshot"),
    )
    multiline = cast(
        dict[str, Any],
        decode_json_object(state.output("multiline_cell").asset_bytes(), "cell snapshot"),
    )
    suppressed = cast(
        dict[str, Any],
        decode_json_object(state.output("suppressed_cell").asset_bytes(), "cell snapshot"),
    )

    assert state.output("fixed_value").json() == 4
    assert state.output("multiline_value").json() == 4
    assert state.output("suppressed_value").json() == 4
    assert fixed["output"] == {
        "channel": "output",
        "mimetype": "text/html",
        "data": '<pre class="text-xs">&#x27;fixed output&#x27;</pre>',
    }
    assert multiline["output"] == {
        "channel": "output",
        "mimetype": "text/html",
        "data": '<pre class="text-xs">8</pre>',
    }
    assert suppressed["output"] is None


def test_ordinary_override_rejects_named_assignment_in_final_expression(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def inputs():
    (scale := 1)
    return (scale,)


@app.cell
def report(scale):
    answer = scale
    return (answer,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(SpecError) as raised:
        build(
            notebook,
            spec=ExportSpec(
                default_state="selected",
                states={"selected": {"scale": 2}},
                outputs={
                    "value": OutputSpec.json("answer"),
                    "cell": OutputSpec.cell("inputs"),
                },
            ),
            output=tmp_path / "export",
            timeout=30,
        )

    assert raised.value.code == "spec_input_invalid"
    assert raised.value.details == {"inputs": ["scale"]}
