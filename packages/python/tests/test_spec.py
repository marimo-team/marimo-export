from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import marimo_export.spec as spec_module
import pytest
from jsonschema import Draft202012Validator, ValidationError
from marimo_export.errors import SpecError
from marimo_export.spec import SPEC_SCHEMA, ExportSpec, decode_spec, load_spec, spec_json_schema


def test_minimal_spec_adds_current_variant_and_normalizes_builtin_options() -> None:
    spec = decode_spec(
        {
            "schema": SPEC_SCHEMA,
            "outputs": {"summary": {"source": "summary", "formats": {"json": {}}}},
        }
    )

    assert [variant.name for variant in spec.variants] == ["current"]
    assert spec.outputs[0].source.kind == "global"
    assert spec.outputs[0].formats[0].options == {
        "indent": None,
        "sort_keys": True,
    }
    assert spec.wire() == {
        "schema": SPEC_SCHEMA,
        "variants": {"current": {}},
        "outputs": {
            "summary": {
                "source": "summary",
                "formats": {"json": {"options": {"indent": None, "sort_keys": True}}},
            }
        },
    }


def test_spec_supports_ui_variants_all_sources_and_custom_exporters() -> None:
    spec = decode_spec(
        {
            "schema": SPEC_SCHEMA,
            "variants": {"current": {}, "aapl": {"symbol_picker": ["AAPL"]}},
            "outputs": {
                "chart": {
                    "source": {"expression": "price_chart.properties(width=800)"},
                    "formats": {
                        "png": {"options": {"scale": 2}},
                        "geojson": {
                            "exporter": {
                                "import": "my_project.exports:geojson",
                                "version": "2",
                            },
                            "options": {"precision": 6},
                        },
                    },
                },
                "note": {
                    "source": {"cell": "market_note"},
                    "formats": {
                        "custom": {"exporter": {"variable": "export_note", "version": "1"}}
                    },
                },
            },
        }
    )

    assert spec.variants[1].controls == {"symbol_picker": ["AAPL"]}
    assert spec.outputs[0].source.kind == "expression"
    assert spec.outputs[0].formats[0].options == {"scale": 2}
    assert spec.outputs[0].formats[1].exporter.wire() == {
        "import": "my_project.exports:geojson",
        "version": "2",
    }
    assert spec.outputs[1].source.kind == "cell"
    assert spec.outputs[1].formats[0].exporter.wire() == {
        "variable": "export_note",
        "version": "1",
    }


def test_export_spec_decodes_direct_construction_and_rejects_internal_parts() -> None:
    wire = {
        "schema": SPEC_SCHEMA,
        "outputs": {"summary": {"source": "summary", "formats": {"json": {}}}},
    }

    assert ExportSpec(wire).wire() == decode_spec(wire).wire()
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        cast(Any, ExportSpec)(variants=(), outputs=())
    with pytest.raises(SpecError, match=r"spec\.schema"):
        ExportSpec({"schema": "invalid", "outputs": {}})


def test_export_spec_owns_canonical_bytes_and_detaches_nested_mappings() -> None:
    wire: dict[str, Any] = {
        "schema": SPEC_SCHEMA,
        "variants": {"current": {"symbol_picker": ["AAPL"]}},
        "outputs": {
            "summary": {
                "source": "summary",
                "formats": {
                    "custom": {
                        "exporter": {"variable": "export_summary", "version": "1"},
                        "options": {"layout": {"columns": ["price"]}},
                    }
                },
            }
        },
    }
    spec = ExportSpec.from_value(wire)
    expected = spec.wire()

    wire["variants"]["current"]["symbol_picker"][0] = "NVDA"
    controls = cast(Any, spec.variants[0].controls)
    controls["symbol_picker"][0] = "MSFT"
    options = cast(Any, spec.outputs[0].formats[0].options)
    options["layout"]["columns"].append("volume")
    returned = cast(Any, spec.wire())
    returned["variants"]["current"]["symbol_picker"][0] = "TSLA"

    assert spec.wire() == expected


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda root: root.update({"extra": True}), "does not accept: extra"),
        (
            lambda root: root["outputs"]["summary"].update({"extra": True}),
            "does not accept: extra",
        ),
        (
            lambda root: root["outputs"]["summary"]["formats"]["json"].update({"extra": True}),
            "does not accept: extra",
        ),
        (
            lambda root: root["outputs"]["summary"].update({"source": {"expression": "value +"}}),
            "valid Python expression",
        ),
        (
            lambda root: root["outputs"]["summary"]["formats"].update({"unknown": {}}),
            "unknown built-in exporter",
        ),
        (
            lambda root: root["outputs"]["summary"]["formats"]["json"].update(
                {"options": {"indent": -1}}
            ),
            "indent",
        ),
    ],
)
def test_spec_rejects_invalid_contracts(
    mutate: Callable[[dict[str, Any]], None], message: str
) -> None:
    root: dict[str, Any] = {
        "schema": SPEC_SCHEMA,
        "outputs": {"summary": {"source": "summary", "formats": {"json": {}}}},
    }
    mutate(root)

    with pytest.raises(SpecError, match=message):
        decode_spec(root)


def test_spec_validation_error_escapes_and_bounds_untrusted_field_names() -> None:
    field = "\x1b[31m" + "x" * 10_000
    root: dict[str, Any] = {
        "schema": SPEC_SCHEMA,
        "outputs": {"summary": {"source": "summary", "formats": {"json": {}}}},
        field: True,
    }

    with pytest.raises(SpecError) as captured:
        decode_spec(root)

    message = str(captured.value)
    assert "\x1b" not in message
    assert "\\x1b" in message
    assert len(message) <= 2_048


def test_spec_downstream_error_escapes_and_bounds_untrusted_option_names() -> None:
    field = "\x1b[31m" + "x" * 10_000
    root: dict[str, Any] = {
        "schema": SPEC_SCHEMA,
        "outputs": {
            "summary": {
                "source": "summary",
                "formats": {"json": {"options": {field: True}}},
            }
        },
    }

    with pytest.raises(SpecError) as captured:
        decode_spec(root)

    message = str(captured.value)
    assert message.startswith("spec.outputs.summary.formats.json.options does not accept: ")
    assert "\x1b" not in message
    assert "\\x1b" in message
    assert len(message) <= 2_048


@pytest.mark.parametrize(
    "exporter",
    [
        {"import": "project.exports:geojson"},
        {"variable": "export_geojson"},
    ],
)
def test_custom_exporter_requires_explicit_version(exporter: dict[str, str]) -> None:
    with pytest.raises(SpecError, match="version"):
        decode_spec(
            {
                "schema": SPEC_SCHEMA,
                "outputs": {
                    "regions": {
                        "source": "regions",
                        "formats": {"geojson": {"exporter": exporter}},
                    }
                },
            }
        )


def test_load_spec_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "export.yaml"
    path.write_text(
        """\
schema: marimo-export.spec.v1
outputs:
  report:
    source: report
    source: other
    formats:
      text: {}
""",
        encoding="utf-8",
    )

    with pytest.raises(SpecError, match="duplicate key 'source'"):
        load_spec(path)


def test_load_spec_escapes_and_bounds_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "export.yaml"
    key = "\\e" + "x" * 10_000
    path.write_text(f'? "{key}"\n: 1\n? "{key}"\n: 2\n', encoding="utf-8")

    with pytest.raises(SpecError) as captured:
        load_spec(path)

    message = str(captured.value)
    assert message.startswith("export spec contains duplicate key ")
    assert "\x1b" not in message
    assert "\\x1b" in message
    assert len(message) <= 2_048


def test_load_spec_escapes_surrogates_in_read_errors(tmp_path: Path) -> None:
    path = tmp_path / ("\ud800" + "x" * 300)

    with pytest.raises(SpecError) as captured:
        load_spec(path)

    message = str(captured.value)
    assert message.startswith("could not read export spec ")
    assert "\ud800" not in message
    assert "\\ud800" in message
    assert len(message) <= 2_048


def test_load_spec_escapes_and_bounds_yaml_parser_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "export.yaml"
    path.write_text("schema: invalid\n", encoding="utf-8")
    error = spec_module.yaml.YAMLError()
    cast(Any, error).problem = "\x1b[31m" + "x" * 10_000

    def fail_yaml(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise error

    monkeypatch.setattr(spec_module.yaml, "load", fail_yaml)

    with pytest.raises(SpecError) as captured:
        load_spec(path)

    message = str(captured.value)
    assert message.startswith("invalid YAML in export spec ")
    assert "\x1b" not in message
    assert "\\x1b" in message
    assert len(message) <= 2_048


def test_load_spec_rejects_files_larger_than_16_mib(tmp_path: Path) -> None:
    path = tmp_path / "export.yaml"
    with path.open("wb") as stream:
        stream.seek(16 * 1024 * 1024)
        stream.write(b" ")

    with pytest.raises(SpecError, match=r"exceeds 16777216 bytes"):
        load_spec(path)


def test_load_spec_rejects_yaml_deeper_than_256_nodes(tmp_path: Path) -> None:
    path = tmp_path / "export.yaml"
    path.write_text("[" * 257 + "0" + "]" * 257, encoding="utf-8")

    with pytest.raises(SpecError, match=r"maximum nesting depth of 256"):
        load_spec(path)


def test_load_spec_rejects_yaml_with_more_than_100000_nodes(tmp_path: Path) -> None:
    path = tmp_path / "export.yaml"
    path.write_text(
        "[" + ",".join("0" for _ in range(100_001)) + "]",
        encoding="utf-8",
    )

    with pytest.raises(SpecError, match=r"maximum node count of 100000"):
        load_spec(path)


def test_load_spec_translates_yaml_recursion_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "export.yaml"
    path.write_text("schema: invalid\n", encoding="utf-8")

    def fail_yaml(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RecursionError("recursive parser failure")

    monkeypatch.setattr(spec_module.yaml, "load", fail_yaml)

    with pytest.raises(SpecError, match=r"maximum nesting depth of 256"):
        load_spec(path)


def test_load_spec_expands_the_user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "export.yaml"
    path.write_text(
        """\
schema: marimo-export.spec.v1
outputs:
  summary:
    source: summary
    formats:
      json: {}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    assert load_spec("~/export.yaml").outputs[0].name == "summary"


def test_load_spec_translates_invalid_yaml_constructor_values(tmp_path: Path) -> None:
    path = tmp_path / "export.yaml"
    path.write_text(
        """
schema: marimo-export.spec.v1
outputs:
  summary:
    source: summary
    formats:
      json:
        options:
          value: 2001-13-40
""",
        encoding="utf-8",
    )

    with pytest.raises(SpecError, match="invalid YAML value"):
        load_spec(path)


def test_load_spec_translates_embedded_null_paths() -> None:
    with pytest.raises(SpecError, match="could not read export spec"):
        load_spec("\x00")


def test_spec_schema_is_strict_and_names_current_schema() -> None:
    schema = spec_json_schema()
    properties = cast(dict[str, object], schema["properties"])
    definitions = cast(dict[str, Any], schema["$defs"])
    number = next(item for item in definitions["json"]["anyOf"] if item.get("type") == "number")

    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert cast(dict[str, object], properties["schema"])["const"] == SPEC_SCHEMA
    assert number["minimum"] == -(2**53 - 1)
    assert number["maximum"] == 2**53 - 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda root: root["outputs"].update({"summary\n": root["outputs"].pop("summary")}),
        lambda root: root["outputs"]["summary"].update({"source": "summary\n"}),
        lambda root: root["outputs"]["summary"]["formats"]["custom"].update(
            {
                "exporter": {
                    "import": "project.exports:geojson\n",
                    "version": "1",
                }
            }
        ),
    ],
)
def test_spec_schema_rejects_names_and_references_with_final_newline(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    root: dict[str, Any] = {
        "schema": SPEC_SCHEMA,
        "outputs": {
            "summary": {
                "source": "summary",
                "formats": {"custom": {}},
            }
        },
    }
    mutate(root)

    with pytest.raises(ValidationError):
        Draft202012Validator(spec_json_schema()).validate(root)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda root: root["outputs"].update({" summary": root["outputs"].pop("summary")}),
        lambda root: root["outputs"]["summary"].update({"source": "not-valid"}),
        lambda root: root["outputs"]["summary"]["formats"]["custom"].update(
            {
                "exporter": {
                    "import": "project.exports",
                    "version": "1",
                }
            }
        ),
        lambda root: root.update({"variants": {"current": {"limit": 2**53}}}),
        lambda root: root["outputs"]["summary"]["formats"]["custom"].update(
            {
                "exporter": {"variable": "export_summary", "version": "1"},
                "options": {"nested": {"limit": 2**53}},
            }
        ),
    ],
)
def test_spec_schema_rejects_portable_contract_violations(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    root: dict[str, Any] = {
        "schema": SPEC_SCHEMA,
        "outputs": {
            "summary": {
                "source": "summary",
                "formats": {"custom": {}},
            }
        },
    }
    mutate(root)

    with pytest.raises(ValidationError):
        Draft202012Validator(spec_json_schema()).validate(root)


def test_export_spec_decoder_remains_authoritative_for_python_keywords() -> None:
    root: dict[str, Any] = {
        "schema": SPEC_SCHEMA,
        "outputs": {"summary": {"source": "class", "formats": {"json": {}}}},
    }

    Draft202012Validator(spec_json_schema()).validate(deepcopy(root))
    with pytest.raises(SpecError, match="Python identifier"):
        decode_spec(root)


def test_checked_in_spec_schema_is_fresh() -> None:
    repository = Path(__file__).parents[3]
    checked_in = json.loads((repository / "schemas/spec.v1.json").read_text(encoding="utf-8"))

    assert checked_in == spec_json_schema()
