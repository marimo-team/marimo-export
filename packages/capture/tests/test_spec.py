from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from moexport.artifacts import Artifact, ArtifactData
from moexport.blobs import BlobRef
from moexport.export import ExportResult
from moexport.exporters.altair import PngOptions
from moexport.spec import (
    CodeExport,
    CodeStateValue,
    ExportSpec,
    load_export_spec,
    RefExport,
    parse_export_spec,
    parse_export_spec_text,
)


def test_export_spec_parses_finance_like_shape() -> None:
    spec = parse_export_spec(
        {
            "scenarios": [
                {"id": "default"},
                {
                    "id": "wide-chart",
                    "state": {"chart_width": 1200},
                },
                {
                    "id": "dynamic-window",
                    "state": {
                        "symbols": ["AAPL", "MSFT"],
                        "start": {
                            "type": "code",
                            "expression": "compute_start_date()",
                        },
                    },
                },
            ],
            "values": {
                "prices": {
                    "source": {"def": "df"},
                    "artifacts": {
                        "arrow": {
                            "export": {
                                "type": "ref",
                                "ref": "moexport.exporters.dataframe:arrow",
                            }
                        },
                        "parquet": {
                            "export": {
                                "type": "ref",
                                "ref": "moexport.exporters.dataframe:parquet",
                            }
                        },
                    },
                },
                "prices_preview": {
                    "source": {"expr": "df.head(10)"},
                    "artifacts": {
                        "custom": {
                            "export": {
                                "type": "code",
                                "code": (
                                    "def export(value, ctx, **options):\n"
                                    "    return {'format_id': 'custom.v1'}\n"
                                ),
                            },
                            "options": {"limit": 10},
                        }
                    },
                },
            },
        }
    )

    assert spec.scenarios[1].state["chart_width"] == 1200
    assert spec.scenarios[2].state["symbols"] == ["AAPL", "MSFT"]

    start = spec.scenarios[2].state["start"]
    assert isinstance(start, CodeStateValue)
    assert start.expression == "compute_start_date()"

    arrow = spec.values["prices"].artifacts["arrow"].export
    assert isinstance(arrow, RefExport)
    assert arrow.ref == "moexport.exporters.dataframe:arrow"

    custom = spec.values["prices_preview"].artifacts["custom"]
    assert isinstance(custom.export, CodeExport)
    assert custom.options == {"limit": 10}


def test_export_spec_defaults_to_one_default_scenario() -> None:
    spec = parse_export_spec(
        {
            "values": {
                "prices": {
                    "source": {"def": "df"},
                    "artifacts": {
                        "arrow": {
                            "export": {
                                "type": "ref",
                                "ref": "moexport.exporters.dataframe:arrow",
                            }
                        }
                    },
                }
            }
        }
    )

    assert [scenario.id for scenario in spec.scenarios] == ["default"]
    assert spec.scenarios[0].state == {}


def test_export_spec_accepts_product_shaped_sources_and_artifacts() -> None:
    spec = parse_export_spec(
        {
            "values": {
                "prices": {
                    "source": {"expr": "df"},
                    "artifacts": ["arrow", "parquet"],
                },
                "change_desc": {
                    "source": {"cell": "change_desc"},
                    "artifacts": [{"html": {"filename": "change-desc.html"}}],
                },
                "chart": {
                    "source": {"expr": "symbols_chart"},
                    "artifacts": [
                        "vegalite",
                        {"png": {"scale": 2}},
                    ],
                },
            }
        }
    )

    assert spec.values["prices"].source.model_dump(mode="json") == {
        "type": "expression",
        "expression": "df",
    }
    assert spec.values["prices"].artifacts["arrow"].export == RefExport(
        type="ref",
        ref="moexport.exporters.dataframe:arrow",
    )
    assert spec.values["prices"].artifacts["parquet"].export == RefExport(
        type="ref",
        ref="moexport.exporters.dataframe:parquet",
    )
    assert spec.values["change_desc"].source.model_dump(
        mode="json", exclude_none=True
    ) == {
        "type": "cell_output",
        "cell": {"name": "change_desc"},
        "on_error": "raise",
    }
    assert spec.values["change_desc"].artifacts["html"].export == RefExport(
        type="ref",
        ref="moexport.exporters.core:html",
    )
    assert spec.values["change_desc"].artifacts["html"].options == {
        "filename": "change-desc.html"
    }
    assert spec.values["chart"].artifacts["vegalite"].export == RefExport(
        type="ref",
        ref="moexport.exporters.altair:vegalite",
    )
    assert spec.values["chart"].artifacts["png"].options == {"scale": 2}


def test_export_spec_rejects_unknown_artifact_shorthand() -> None:
    with pytest.raises(ValidationError, match="unknown built-in artifact"):
        parse_export_spec(
            {
                "values": {
                    "prices": {
                        "source": {"expr": "df"},
                        "artifacts": ["excel"],
                    }
                }
            }
        )


def test_export_spec_loads_json_and_yaml_files(tmp_path: Path) -> None:
    value = {
        "scenarios": [{"id": "wide", "state": {"chart_width": 1200}}],
        "values": {
            "prices": {
                "source": {"def": "df"},
                "artifacts": {
                    "arrow": {
                        "export": {
                            "type": "ref",
                            "ref": "moexport.exporters.dataframe:arrow",
                        }
                    }
                },
            }
        },
    }
    json_path = tmp_path / "spec.json"
    yaml_path = tmp_path / "spec.yaml"
    json_path.write_text(json.dumps(value), encoding="utf-8")
    yaml_path.write_text(
        """
scenarios:
  - id: wide
    state:
      chart_width: 1200
values:
  prices:
    source: {def: df}
    artifacts:
      arrow:
        export:
          type: ref
          ref: moexport.exporters.dataframe:arrow
""".lstrip(),
        encoding="utf-8",
    )

    from_json = load_export_spec(json_path)
    from_yaml = load_export_spec(yaml_path)

    assert from_json.model_dump(mode="json") == from_yaml.model_dump(mode="json")


def test_checked_in_json_specs_match_yaml_sources(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    script_path = repo / "scripts" / "sync_specs.py"
    spec = importlib.util.spec_from_file_location("sync_specs", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    module.sync_specs(
        repo / "notebooks" / "export-specs" / "yaml",
        tmp_path,
    )

    json_dir = repo / "notebooks" / "export-specs" / "json"
    for generated in sorted(tmp_path.glob("*.json")):
        assert json.loads((json_dir / generated.name).read_text(encoding="utf-8")) == (
            json.loads(generated.read_text(encoding="utf-8"))
        )


def test_export_spec_parses_yaml_text_without_extension() -> None:
    spec = parse_export_spec_text(
        """
values:
  prices:
    source: {expr: df.head(10)}
    artifacts:
      arrow:
        export:
          type: ref
          ref: moexport.exporters.dataframe:arrow
""".lstrip()
    )

    assert spec.values["prices"].source.model_dump(mode="json") == {
        "type": "expression",
        "expression": "df.head(10)",
    }


def test_export_spec_serializes_code_state_stably() -> None:
    spec = parse_export_spec(
        {
            "scenarios": [
                {
                    "id": "computed",
                    "state": {
                        "end": {
                            "type": "code",
                            "expression": "latest_market_close()",
                        }
                    },
                }
            ],
            "values": {
                "prices": {
                    "source": {"def": "df"},
                    "artifacts": {
                        "arrow": {
                            "export": {
                                "type": "ref",
                                "ref": "moexport.exporters.dataframe:arrow",
                            }
                        }
                    },
                }
            },
        }
    )

    dumped = spec.model_dump(mode="json")
    assert dumped["scenarios"][0]["state"]["end"] == {
        "type": "code",
        "expression": "latest_market_close()",
    }


def test_export_spec_rejects_unknown_scenario_fields() -> None:
    with pytest.raises(ValidationError, match="unexpected"):
        parse_export_spec(
            {
                "scenarios": [
                    {
                        "id": "wide-chart",
                        "unexpected": {"chart_width": 1200},
                    }
                ],
                "values": {
                    "prices": {
                        "source": {"def": "df"},
                        "artifacts": {
                            "arrow": {
                                "export": {
                                    "type": "ref",
                                    "ref": "moexport.exporters.dataframe:arrow",
                                }
                            }
                        },
                    }
                },
            }
        )


def test_export_spec_rejects_duplicate_scenario_ids() -> None:
    with pytest.raises(ValidationError, match="scenario ids must be unique"):
        parse_export_spec(
            {
                "scenarios": [
                    {"id": "default"},
                    {"id": "default"},
                ],
                "values": {
                    "prices": {
                        "source": {"def": "df"},
                        "artifacts": {
                            "arrow": {
                                "export": {
                                    "type": "ref",
                                    "ref": "moexport.exporters.dataframe:arrow",
                                }
                            }
                        },
                    }
                },
            }
        )


def test_export_spec_rejects_bad_export_ref() -> None:
    with pytest.raises(ValidationError, match="module:object"):
        parse_export_spec(
            {
                "values": {
                    "prices": {
                        "source": {"def": "df"},
                        "artifacts": {
                            "arrow": {
                                "export": {
                                    "type": "ref",
                                    "ref": "moexport.exporters.dataframe.arrow",
                                }
                            }
                        },
                    }
                },
            }
        )


def test_export_spec_json_schema_preserves_field_descriptions() -> None:
    schema = ExportSpec.model_json_schema()

    for field_name, field_schema in schema["properties"].items():
        assert field_schema.get("description"), field_name

    for definition_name, definition_schema in schema["$defs"].items():
        for field_name, field_schema in definition_schema.get("properties", {}).items():
            assert field_schema.get("description"), f"{definition_name}.{field_name}"

    png_schema = PngOptions.model_json_schema()
    assert png_schema["properties"]["scale"]["description"]
    assert png_schema["properties"]["vl_version"]["description"]

    for model in [Artifact, ArtifactData, BlobRef, ExportResult]:
        for field_schema in model.model_json_schema()["properties"].values():
            assert field_schema.get("description")
