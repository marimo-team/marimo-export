from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pytest
from pydantic import ValidationError

from moexport.artifacts import Artifact, ArtifactData
from moexport.blobs import BlobRef
from moexport.export import CaptureResult
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
                            "code": "compute_start_date()",
                        },
                    },
                },
            ],
            "values": {
                "prices": {
                    "source": {"def": "df"},
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
                    "source": {"expr": "df.head(10)"},
                    "formats": {
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
    assert start.code == "compute_start_date()"

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
                    "source": {"def": "df"},
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


def test_export_spec_accepts_product_shaped_sources_and_formats() -> None:
    spec = parse_export_spec(
        {
            "values": {
                "prices": {
                    "source": {"expr": "df"},
                    "formats": ["arrow", "parquet"],
                },
                "change_desc": {
                    "source": {"cell": "change_desc"},
                    "formats": [{"html": {"filename": "change-desc.html"}}],
                },
                "chart": {
                    "source": {"expr": "symbols_chart"},
                    "formats": [
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
    assert spec.values["prices"].formats["arrow"].export == RefExport(
        type="ref",
        ref="moexport.exporters.dataframe:arrow",
    )
    assert spec.values["prices"].formats["parquet"].export == RefExport(
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
    assert spec.values["change_desc"].formats["html"].export == RefExport(
        type="ref",
        ref="moexport.exporters.core:html",
    )
    assert spec.values["change_desc"].formats["html"].options == {
        "filename": "change-desc.html"
    }
    assert spec.values["chart"].formats["vegalite"].export == RefExport(
        type="ref",
        ref="moexport.exporters.altair:vegalite",
    )
    assert spec.values["chart"].formats["png"].options == {"scale": 2}


def test_export_spec_rejects_unknown_format_shorthand() -> None:
    with pytest.raises(ValidationError, match="unknown built-in format"):
        parse_export_spec(
            {
                "values": {
                    "prices": {
                        "source": {"expr": "df"},
                        "formats": ["excel"],
                    }
                }
            }
        )


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({"def": "df", "extra": True}, "source.def does not accept"),
        ({"def": "df", "expr": "other"}, "source.def does not accept"),
        ({"cell": "summary", "extra": True}, "source.cell does not accept"),
        ({"cell": True}, "cell selector"),
        ({"cell": {"index": True}}, "valid integer"),
        ({"snapshot": False}, "source.snapshot must be true"),
        ({"snapshot": True, "include_source": "false"}, "valid boolean"),
        ({"snapshot": True, "include_internal_cells": 1}, "valid boolean"),
        ({"snapshot": True, "notebook": True}, "exactly one marker"),
        (
            {"report": {"cells": [{"name": "summary"}]}, "extra": True},
            "source.report does not accept",
        ),
        (
            {"report": {"cells": [{"name": "summary", "order": True}]}},
            "valid integer",
        ),
        (
            {"report": {"cells": [{"name": "summary"}], "include_source": "false"}},
            "valid boolean",
        ),
    ],
)
def test_export_spec_rejects_invalid_source_shorthand(
    source: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        parse_export_spec(
            {
                "values": {
                    "item": {
                        "source": source,
                        "formats": ["json"],
                    }
                }
            }
        )


def test_export_spec_rejects_unknown_format_list_fields() -> None:
    with pytest.raises(ValidationError, match="formats\\[0\\].format does not accept"):
        parse_export_spec(
            {
                "values": {
                    "prices": {
                        "source": {"expr": "df"},
                        "formats": [{"format": "json", "unexpected": True}],
                    }
                }
            }
        )


def test_export_spec_rejects_explicit_null_format_options() -> None:
    with pytest.raises(ValidationError, match="format options must be a JSON object"):
        parse_export_spec(
            {
                "values": {
                    "prices": {
                        "source": {"expr": "df"},
                        "formats": {
                            "custom": {
                                "export": {
                                    "type": "ref",
                                    "ref": "moexport.exporters.core:json",
                                },
                                "options": None,
                            }
                        },
                    }
                }
            }
        )


def test_export_spec_rejects_reserved_builtin_format_option_keys() -> None:
    with pytest.raises(ValidationError, match="reserved export or options keys"):
        parse_export_spec(
            {
                "values": {
                    "prices": {
                        "source": {"expr": "df"},
                        "formats": {"json": {"options": {}}},
                    }
                }
            }
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"scenarios": [{"id": b"default"}]},
        {"scenarios": [{"state": {b"width": 1200}}]},
        {"scenarios": [{"state": {"width": {"code": b"1200"}}}]},
        {"values": {b"prices": {"source": {"def": "df"}, "formats": ["json"]}}},
        {
            "values": {
                "prices": {
                    "source": {"type": "definition", "name": b"df"},
                    "formats": ["json"],
                }
            }
        },
        {
            "values": {
                "prices": {
                    "source": {"type": "expression", "expression": b"df"},
                    "formats": ["json"],
                }
            }
        },
        {
            "values": {
                "prices": {
                    "source": {"type": "cell_output", "cell": {"name": b"summary"}},
                    "formats": ["json"],
                }
            }
        },
        {
            "values": {
                "prices": {
                    "source": {
                        "type": "report",
                        "cells": [{"name": "summary", "label": b"Summary"}],
                    },
                    "formats": ["json"],
                }
            }
        },
        {
            "values": {
                "prices": {
                    "source": {"def": "df"},
                    "formats": {
                        "json": {
                            "export": {
                                "type": "ref",
                                "ref": b"moexport.exporters.core:json",
                            }
                        }
                    },
                }
            }
        },
        {
            "values": {
                "prices": {
                    "source": {"def": "df"},
                    "formats": {
                        "custom": {
                            "export": {
                                "type": "code",
                                "code": b"def export(value, ctx):\n    return value",
                            }
                        }
                    },
                }
            }
        },
    ],
)
def test_export_spec_rejects_bytes_in_public_string_fields(
    patch: dict[str, object],
) -> None:
    base_spec: dict[str, object] = {
        "values": {
            "prices": {
                "source": {"def": "df"},
                "formats": ["json"],
            }
        }
    }
    spec = {**base_spec, **patch}

    with pytest.raises(ValidationError, match="string"):
        parse_export_spec(spec)


def test_export_spec_loads_json_and_yaml_files(tmp_path: Path) -> None:
    value = {
        "scenarios": [{"id": "wide", "state": {"chart_width": 1200}}],
        "values": {
            "prices": {
                "source": {"def": "df"},
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
    source: {def: df}
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
    formats:
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
                            "code": "latest_market_close()",
                        }
                    },
                }
            ],
            "values": {
                "prices": {
                    "source": {"def": "df"},
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
        "code": "latest_market_close()",
    }


@pytest.mark.parametrize(
    "state_value",
    [
        {"type": "code", "expression": "1200"},
        {"type": "code"},
    ],
)
def test_export_spec_rejects_code_state_marker_objects(
    state_value: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="scenario code values use"):
        parse_export_spec(
            {
                "scenarios": [
                    {
                        "id": "computed",
                        "state": {"chart_width": state_value},
                    }
                ],
                "values": {
                    "prices": {
                        "source": {"def": "df"},
                        "formats": ["json"],
                    }
                },
            }
        )


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
                        "source": {"def": "df"},
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
                        "source": {"def": "df"},
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

    for field_name, field_schema in schema["properties"].items():
        assert field_schema.get("description"), field_name

    for definition_name, definition_schema in schema["$defs"].items():
        for field_name, field_schema in definition_schema.get("properties", {}).items():
            assert field_schema.get("description"), f"{definition_name}.{field_name}"

    png_schema = PngOptions.model_json_schema()
    assert png_schema["properties"]["scale"]["description"]
    assert png_schema["properties"]["vl_version"]["description"]

    for model in [Artifact, ArtifactData, BlobRef, CaptureResult]:
        for field_schema in model.model_json_schema()["properties"].values():
            assert field_schema.get("description")
