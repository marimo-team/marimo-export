from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from marimo_export import ExportSpec, OutputSpec, build, open_export
from marimo_export._json import decode_json_object
from marimo_export.errors import SpecError


def test_ordinary_override_reaches_multiline_final_expression_and_value_projection(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def inputs():
    amount = 1; (
        amount * 2
    )
    return (amount,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="selected",
            states={"selected": {"amount": 4}},
            outputs={
                "value": OutputSpec.json("amount"),
                "cell": OutputSpec.cell("inputs"),
            },
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    state = open_export(result.path).state("selected")
    snapshot = cast(
        dict[str, Any],
        decode_json_object(state.output("cell").asset_bytes(), "cell snapshot"),
    )

    assert state.output("value").json() == 4
    assert snapshot["output"] == {
        "channel": "output",
        "mimetype": "text/html",
        "data": '<pre class="text-xs">8</pre>',
    }


def test_ordinary_override_preserves_unrelated_final_expression(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def inputs():
    amount = 1
    "fixed output"
    return (amount,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="selected",
            states={"selected": {"amount": 4}},
            outputs={
                "value": OutputSpec.json("amount"),
                "cell": OutputSpec.cell("inputs"),
            },
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    state = open_export(result.path).state("selected")
    snapshot = cast(
        dict[str, Any],
        decode_json_object(state.output("cell").asset_bytes(), "cell snapshot"),
    )

    assert state.output("value").json() == 4
    assert snapshot["output"] == {
        "channel": "output",
        "mimetype": "text/html",
        "data": '<pre class="text-xs">&#x27;fixed output&#x27;</pre>',
    }


def test_ordinary_override_preserves_trailing_semicolon_output_suppression(
    tmp_path: Path,
) -> None:
    notebook = tmp_path / "notebook.py"
    notebook.write_text(
        """
import marimo

app = marimo.App()


@app.cell
def inputs():
    amount = 1; amount;
    return (amount,)


if __name__ == "__main__":
    app.run()
""".lstrip(),
        encoding="utf-8",
    )
    result = build(
        notebook,
        spec=ExportSpec(
            default_state="selected",
            states={"selected": {"amount": 4}},
            outputs={
                "value": OutputSpec.json("amount"),
                "cell": OutputSpec.cell("inputs"),
            },
        ),
        output=tmp_path / "export",
        timeout=30,
    )
    state = open_export(result.path).state("selected")
    snapshot = cast(
        dict[str, Any],
        decode_json_object(state.output("cell").asset_bytes(), "cell snapshot"),
    )

    assert state.output("value").json() == 4
    assert snapshot["output"] is None


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
