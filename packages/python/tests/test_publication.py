from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from marimo_export.errors import PublicationError
from marimo_export.publication import (
    ASSET_CODEC,
    PUBLICATION_SCHEMA,
    PublicationIndex,
    publication_json_schema,
)

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _wire() -> dict[str, Any]:
    return {
        "schema": PUBLICATION_SCHEMA,
        "asset_codec": ASSET_CODEC,
        "notebook": {"filename": "finance.py", "document_sha256": _DIGEST_A},
        "producer": {"marimo": "0.24.0", "marimo_export": "0.1.0"},
        "variants": {
            "current": {
                "controls": {},
                "outputs": {
                    "summary": {
                        "formats": {
                            "json": {
                                "format_id": "json.v1",
                                "media_type": "application/json",
                                "metadata": {},
                                "asset": {
                                    "key": "project/abc/return.bin",
                                    "sha256": _DIGEST_B,
                                    "size": 42,
                                },
                            }
                        }
                    }
                },
            }
        },
    }


def test_publication_index_round_trips_and_enumerates_asset_closure() -> None:
    index = PublicationIndex.from_wire(_wire())

    assert PublicationIndex.from_bytes(index.to_bytes()).wire() == index.wire()
    assert index.notebook.filename == "finance.py"
    assert index.assets()[0].key == "project/abc/return.bin"


def test_publication_rejects_unknown_fields() -> None:
    wire = _wire()
    wire["extra"] = True

    with pytest.raises(PublicationError, match="does not accept: 'extra'"):
        PublicationIndex.from_wire(wire)


@pytest.mark.parametrize(
    "key",
    [
        "/return.bin",
        "../return.bin",
        "project/../return.bin",
        "project//return.bin",
        "project\\return.bin",
        "project/report:stream/return.bin",
        "project/report<draft>/return.bin",
        'project/report"draft/return.bin',
        "project/report|draft/return.bin",
        "project/report?draft/return.bin",
        "project/report*draft/return.bin",
        "project/C./return.bin",
        "project/C /return.bin",
        "project/CON.bin/return.bin",
        "project/prn/return.bin",
        "project/AUX.csv/return.bin",
        "project/NUL/return.bin",
        "project/COM1/return.bin",
        "project/lpt9.txt/return.bin",
        "project/COM¹/return.bin",
        "project/com².txt/return.bin",
        "project/LPT³/return.bin",
        "project/CONIN$/return.bin",
        "project/conout$.txt/return.bin",
        "project/return.json",
    ],
)
def test_publication_rejects_unsafe_cache_keys(key: str) -> None:
    wire = _wire()
    wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["asset"]["key"] = key

    with pytest.raises(PublicationError, match=r"portable relative \.bin cache key"):
        PublicationIndex.from_wire(wire)
    assert not Draft202012Validator(publication_json_schema()).is_valid(wire)


def test_publication_asset_key_length_matches_runtime_and_schema() -> None:
    wire = _wire()
    asset = wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["asset"]
    asset["key"] = "/".join(["x" * 255, "x" * 255, "x" * 255, "x" * 245, "return.bin"])

    assert len(asset["key"]) == 1_024
    assert PublicationIndex.from_wire(wire).assets()[0].key == asset["key"]
    assert Draft202012Validator(publication_json_schema()).is_valid(wire)

    asset["key"] = "/".join(["x" * 255, "x" * 255, "x" * 255, "x" * 246, "return.bin"])
    with pytest.raises(PublicationError, match=r"portable relative \.bin cache key"):
        PublicationIndex.from_wire(wire)
    assert not Draft202012Validator(publication_json_schema()).is_valid(wire)


@pytest.mark.parametrize(
    "filename",
    [
        "report.",
        "report ",
        "report:2026.py",
        "report?.py",
        "CON",
        "con.py",
        "PRN.txt",
        "AUX.csv",
        "nul.JSON",
        "COM1.py",
        "lpt9.txt",
        "back\\slash.py",
        "report\n.py",
        "report\x7f.py",
    ],
)
def test_publication_accepts_posix_notebook_provenance_filenames(filename: str) -> None:
    wire = _wire()
    wire["notebook"]["filename"] = filename

    assert PublicationIndex.from_wire(wire).notebook.filename == filename
    assert Draft202012Validator(publication_json_schema()).is_valid(wire)


@pytest.mark.parametrize("filename", ["", "folder/report.py", "report\x00.py"])
def test_publication_rejects_invalid_notebook_provenance_filenames(filename: str) -> None:
    wire = _wire()
    wire["notebook"]["filename"] = filename

    with pytest.raises(PublicationError):
        PublicationIndex.from_wire(wire)
    assert not Draft202012Validator(publication_json_schema()).is_valid(wire)


def test_publication_rejects_asset_sizes_outside_the_safe_integer_range() -> None:
    wire = _wire()
    wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["asset"]["size"] = 2**53

    with pytest.raises(PublicationError, match="JavaScript safe range"):
        PublicationIndex.from_wire(wire)


def test_publication_normalizes_mathematically_integral_asset_sizes() -> None:
    wire = _wire()
    wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["asset"]["size"] = 1.0

    index = PublicationIndex.from_wire(wire)

    assert index.assets()[0].size == 1
    assert isinstance(index.assets()[0].size, int)


def _wire_bytes_with_size_literal(literal: str) -> bytes:
    encoded = json.dumps(_wire(), separators=(",", ":"))
    return encoded.replace('"size":42', f'"size":{literal}', 1).encode("utf-8")


@pytest.mark.parametrize(
    "literal",
    ["1.00000000000000001", "9007199254740990.5", "9007199254740991.1"],
)
def test_publication_rejects_nonintegral_asset_size_lexemes(literal: str) -> None:
    with pytest.raises(PublicationError):
        PublicationIndex.from_bytes(_wire_bytes_with_size_literal(literal))


@pytest.mark.parametrize(("literal", "expected"), [("1.0", 1), ("1e0", 1), ("1.5e1", 15)])
def test_publication_accepts_integral_asset_size_lexemes(literal: str, expected: int) -> None:
    index = PublicationIndex.from_bytes(_wire_bytes_with_size_literal(literal))

    assert index.assets()[0].size == expected


def test_publication_rejects_conflicting_references_for_one_cache_key() -> None:
    wire = _wire()
    formats = wire["variants"]["current"]["outputs"]["summary"]["formats"]
    formats["text"] = {
        "format_id": "text.v1",
        "media_type": "text/plain",
        "metadata": {},
        "asset": {
            "key": "project/abc/return.bin",
            "sha256": _DIGEST_A,
            "size": 42,
        },
    }

    with pytest.raises(PublicationError, match="conflicting asset reference"):
        PublicationIndex.from_wire(wire)


def test_publication_bounds_conflicting_asset_diagnostics() -> None:
    wire = _wire()
    formats = wire["variants"]["current"]["outputs"]["summary"]["formats"]
    key = "/".join(["x" * 200, "x" * 200, "x" * 200, "x" * 200, "return.bin"])
    formats["json"]["asset"]["key"] = key
    formats["text"] = {
        "format_id": "text.v1",
        "media_type": "text/plain",
        "metadata": {},
        "asset": {
            "key": key,
            "sha256": _DIGEST_A,
            "size": 42,
        },
    }

    with pytest.raises(PublicationError) as captured:
        PublicationIndex.from_wire(wire)

    message = str(captured.value)
    assert "conflicting asset reference" in message
    assert len(message) < 700
    assert "x" * 200 not in message


def test_publication_rejects_conflicting_format_contracts_for_one_cache_key() -> None:
    wire = _wire()
    formats = wire["variants"]["current"]["outputs"]["summary"]["formats"]
    formats["text"] = {
        "format_id": "text.v1",
        "media_type": "text/plain",
        "metadata": {},
        "asset": formats["json"]["asset"],
    }

    with pytest.raises(PublicationError, match="conflicting format contract"):
        PublicationIndex.from_wire(wire)


def test_publication_compares_shared_asset_metadata_by_json_type() -> None:
    wire = _wire()
    formats = wire["variants"]["current"]["outputs"]["summary"]["formats"]
    formats["json"]["metadata"] = {"flag": True}
    formats["alternate"] = {
        "format_id": "json.v1",
        "media_type": "application/json",
        "metadata": {"flag": 1},
        "asset": formats["json"]["asset"],
    }

    with pytest.raises(PublicationError, match="conflicting format contract"):
        PublicationIndex.from_wire(wire)


def test_publication_treats_integer_and_float_as_the_same_shared_asset_metadata() -> None:
    wire = _wire()
    formats = wire["variants"]["current"]["outputs"]["summary"]["formats"]
    formats["json"]["metadata"] = {"scale": 1}
    formats["alternate"] = {
        "format_id": "json.v1",
        "media_type": "application/json",
        "metadata": {"scale": 1.0},
        "asset": formats["json"]["asset"],
    }

    assert len(PublicationIndex.from_wire(wire).assets()) == 1


def test_publication_reads_control_keys_as_public_names() -> None:
    wire = _wire()
    wire["variants"]["current"]["controls"] = {"market symbol": "AAPL", "class": 1}

    index = PublicationIndex.from_wire(wire)

    assert index.variants["current"].controls == {"market symbol": "AAPL", "class": 1}


def test_publication_index_detaches_nested_controls_and_metadata() -> None:
    wire = _wire()
    wire["variants"]["current"]["controls"] = {
        "symbol": {"values": ["AAPL"]},
    }
    wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["metadata"] = {
        "columns": ["price"]
    }
    index = PublicationIndex.from_wire(wire)
    expected = index.to_bytes()

    wire["variants"]["current"]["controls"]["symbol"]["values"].append("MSFT")
    wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["metadata"][
        "columns"
    ].append("change")
    controls = cast(Any, index.variants["current"].controls)
    controls["symbol"]["values"].append("NVDA")
    metadata = cast(
        Any,
        index.variants["current"].outputs["summary"].formats["json"].metadata,
    )
    metadata["columns"].append("volume")

    assert index.to_bytes() == expected
    assert len(index.assets()) == 1


def test_publication_translates_excessive_json_nesting() -> None:
    wire = _wire()
    nested: object = None
    for _ in range(300):
        nested = [nested]
    wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["metadata"] = {
        "nested": nested
    }

    with pytest.raises(PublicationError, match="maximum JSON nesting depth"):
        PublicationIndex.from_bytes(json.dumps(wire).encode("utf-8"))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("notebook", "document_sha256"), f"{_DIGEST_A}\n"),
        (
            ("variants", "current", "outputs", "summary", "formats", "json", "format_id"),
            "json.v1\n",
        ),
    ],
)
def test_publication_runtime_and_schema_reject_final_newlines(
    path: tuple[str, ...], value: str
) -> None:
    wire = _wire()
    current: dict[str, Any] = wire
    for segment in path[:-1]:
        current = current[segment]
    current[path[-1]] = value

    with pytest.raises(PublicationError):
        PublicationIndex.from_wire(wire)
    assert not Draft202012Validator(publication_json_schema()).is_valid(wire)


def test_publication_runtime_and_schema_reject_final_newlines_in_public_names() -> None:
    wire = _wire()
    variant = wire["variants"].pop("current")
    wire["variants"]["current\n"] = variant

    with pytest.raises(PublicationError):
        PublicationIndex.from_wire(wire)
    assert not Draft202012Validator(publication_json_schema()).is_valid(wire)


def test_publication_bounds_format_ids_to_255_ascii_bytes() -> None:
    wire = _wire()
    entry = wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]
    entry["format_id"] = "a" * 255

    assert PublicationIndex.from_wire(wire).wire() == wire
    assert Draft202012Validator(publication_json_schema()).is_valid(wire)

    entry["format_id"] += "a"
    with pytest.raises(PublicationError, match="at most 255 ASCII bytes"):
        PublicationIndex.from_wire(wire)
    assert not Draft202012Validator(publication_json_schema()).is_valid(wire)


def test_publication_bounds_media_types_to_1024_ascii_bytes() -> None:
    wire = _wire()
    entry = wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]
    entry["media_type"] = "text/plain;" + "a" * 1_013

    assert len(entry["media_type"]) == 1_024
    assert PublicationIndex.from_wire(wire).wire() == wire
    assert Draft202012Validator(publication_json_schema()).is_valid(wire)

    entry["media_type"] += "a"
    with pytest.raises(PublicationError, match="at most 1024 printable ASCII bytes"):
        PublicationIndex.from_wire(wire)
    assert not Draft202012Validator(publication_json_schema()).is_valid(wire)


def test_publication_index_accepts_large_format_metadata() -> None:
    wire = _wire()
    entry = wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]
    metadata = {"value": "a" * (256 * 1024)}
    entry["metadata"] = metadata

    index = PublicationIndex.from_wire(wire)

    published = index.variants["current"].outputs["summary"].formats["json"]
    assert published.metadata == metadata


def test_publication_errors_escape_and_bound_untrusted_field_names() -> None:
    wire = _wire()
    wire[f"\x1b[31m{'x' * 1_000}"] = True

    with pytest.raises(PublicationError) as captured:
        PublicationIndex.from_wire(wire)

    message = str(captured.value)
    assert "\x1b" not in message
    assert "\\x1b" in message
    assert len(message) < 700


def test_publication_rejects_overflowing_floats_at_runtime_and_in_schema() -> None:
    wire = _wire()
    metadata = wire["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["metadata"]
    metadata["value"] = float("inf")

    with pytest.raises(PublicationError, match="NaN or infinity"):
        PublicationIndex.from_wire(wire)
    assert not Draft202012Validator(publication_json_schema()).is_valid(wire)


def test_publication_schema_names_the_envelope_codec() -> None:
    schema = publication_json_schema()
    properties = cast(dict[str, object], schema["properties"])
    asset_codec = cast(dict[str, object], properties["asset_codec"])

    assert schema["additionalProperties"] is False
    assert asset_codec["const"] == ASSET_CODEC


def test_checked_in_publication_schema_is_fresh() -> None:
    repository = Path(__file__).parents[3]
    checked_in = json.loads(
        (repository / "schemas/publication.v1.json").read_text(encoding="utf-8")
    )

    assert checked_in == publication_json_schema()
