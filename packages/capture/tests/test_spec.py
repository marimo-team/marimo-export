from __future__ import annotations

import json
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
            "notebook": "notebooks/finance.py",
            "bundle": "examples/finance/export_bundle",
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
                    "source": "df",
                    "formats": {
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
                    "source": "df.head(10)",
                    "formats": {
                        "custom": {
                            "export": {
                                "type": "code",
                                "code": (
                                    "def export(value, ctx, **options):\n"
                                    "    return {'format': 'custom.v1'}\n"
                                ),
                            },
                            "options": {"limit": 10},
                        }
                    },
                },
            },
        }
    )

    assert spec.notebook == "notebooks/finance.py"
    assert spec.bundle is not None
    assert spec.bundle.path == "examples/finance/export_bundle"
    assert spec.scenarios[1].state["chart_width"] == 1200
    assert spec.scenarios[2].state["symbols"] == ["AAPL", "MSFT"]

    start = spec.scenarios[2].state["start"]
    assert isinstance(start, CodeStateValue)
    assert start.expression == "compute_start_date()"

    arrow = spec.values["prices"].formats["arrow"].export
    assert isinstance(arrow, RefExport)
    assert arrow.ref == "moexport.exporters.dataframe:arrow"

    custom = spec.values["prices_preview"].formats["custom"]
    assert isinstance(custom.export, CodeExport)
    assert custom.options == {"limit": 10}


def test_export_spec_defaults_to_one_default_scenario() -> None:
    spec = parse_export_spec(
        {
            "values": {
                "prices": {
                    "source": "df",
                    "formats": {
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


def test_export_spec_loads_json_and_yaml_files(tmp_path: Path) -> None:
    value = {
        "scenarios": [{"id": "wide", "state": {"chart_width": 1200}}],
        "values": {
            "prices": {
                "source": "df",
                "formats": {
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
    source: df
    formats:
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


def test_export_spec_parses_yaml_text_without_extension() -> None:
    spec = parse_export_spec_text(
        """
values:
  prices:
    source: df.head(10)
    formats:
      arrow:
        export:
          type: ref
          ref: moexport.exporters.dataframe:arrow
""".lstrip()
    )

    assert spec.values["prices"].source == "df.head(10)"


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
                    "source": "df",
                    "formats": {
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


def test_export_spec_rejects_old_overrides_name() -> None:
    with pytest.raises(ValidationError, match="overrides"):
        parse_export_spec(
            {
                "scenarios": [
                    {
                        "id": "wide-chart",
                        "overrides": {"chart_width": 1200},
                    }
                ],
                "values": {
                    "prices": {
                        "source": "df",
                        "formats": {
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
                        "source": "df",
                        "formats": {
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
                        "source": "df",
                        "formats": {
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

    for field_schema in schema["properties"].values():
        assert field_schema.get("description")

    for definition_name in [
        "BundleSpec",
        "CodeExport",
        "CodeStateValue",
        "FormatSpec",
        "RefExport",
        "ScenarioSpec",
        "ValueSpec",
    ]:
        properties = schema["$defs"][definition_name]["properties"]
        for field_schema in properties.values():
            assert field_schema.get("description")

    png_schema = PngOptions.model_json_schema()
    assert png_schema["properties"]["scale"]["description"]
    assert png_schema["properties"]["vl_version"]["description"]

    for model in [Artifact, ArtifactData, BlobRef, ExportResult]:
        for field_schema in model.model_json_schema()["properties"].values():
            assert field_schema.get("description")
