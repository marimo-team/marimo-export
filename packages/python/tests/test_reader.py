from __future__ import annotations

import inspect
import os
import tracemalloc
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import marimo_export.reader as reader_module
import msgspec
import pytest
from marimo_export import (
    NotebookProvenance,
    ProducerProvenance,
    Publication,
    PublishedFormat,
    PublishedOutput,
    PublishedVariant,
)
from marimo_export._format import MAX_FORMAT_METADATA_JSON_BYTES
from marimo_export._json import canonical_bytes, sha256_bytes
from marimo_export.errors import IntegrityError, PublicationError
from marimo_export.publication import ASSET_CODEC, PUBLICATION_SCHEMA
from marimo_export.reader import open_publication


def _publication(
    root: Path,
    *,
    data: bytes = b'{"answer":42}',
    media_type: str = "application/json",
    format_id: str = "json.v1",
    metadata: dict[str, object] | None = None,
    filename: str | None = "summary.json",
    envelope_extra: dict[str, object] | None = None,
) -> tuple[Path, dict[str, Any], Path]:
    metadata = metadata or {}
    envelope: dict[str, object] = {
        "data": data,
        "media_type": media_type,
        "filename": filename,
        "metadata": {
            "format_id": format_id,
            "metadata_json": canonical_bytes(metadata),
        },
    }
    if envelope_extra:
        envelope.update(envelope_extra)
    encoded = msgspec.msgpack.encode(envelope)
    key = "project/abc/return.bin"
    asset = {"key": key, "sha256": sha256_bytes(encoded), "size": len(encoded)}
    index: dict[str, Any] = {
        "schema": PUBLICATION_SCHEMA,
        "asset_codec": ASSET_CODEC,
        "notebook": {"filename": "finance.py", "document_sha256": "a" * 64},
        "producer": {"marimo": "0.24.0", "marimo_export": "0.1.0"},
        "variants": {
            "current": {
                "controls": {"symbol_picker": ["AAPL"]},
                "outputs": {
                    "summary": {
                        "formats": {
                            "json": {
                                "format_id": format_id,
                                "media_type": media_type,
                                "metadata": metadata,
                                "asset": asset,
                            }
                        }
                    }
                },
            }
        },
    }
    asset_path = root / "cache" / key
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(encoded)
    (root / "index.json").write_bytes(canonical_bytes(index))
    return root, index, asset_path


def _replace_envelope(
    root: Path,
    index: dict[str, Any],
    asset_path: Path,
    encoded: bytes,
) -> None:
    entry = index["variants"]["current"]["outputs"]["summary"]["formats"]["json"]
    entry["asset"] = {
        "key": entry["asset"]["key"],
        "sha256": sha256_bytes(encoded),
        "size": len(encoded),
    }
    asset_path.write_bytes(encoded)
    (root / "index.json").write_bytes(canonical_bytes(index))


def _replace_asset_key(root: Path, index: dict[str, Any], key: str) -> None:
    entry = index["variants"]["current"]["outputs"]["summary"]["formats"]["json"]
    entry["asset"]["key"] = key
    (root / "index.json").write_bytes(canonical_bytes(index))


def test_reader_navigates_and_decodes_verified_blob_asset(tmp_path: Path) -> None:
    root, _, _ = _publication(tmp_path / "publication", metadata={"rows": 1})

    publication = open_publication(root)
    variant = publication.variant("current")
    output = variant.output("summary")
    published = output.format("json")

    assert isinstance(publication, Publication)
    assert isinstance(publication.notebook, NotebookProvenance)
    assert isinstance(publication.producer, ProducerProvenance)
    assert isinstance(variant, PublishedVariant)
    assert isinstance(output, PublishedOutput)
    assert isinstance(published, PublishedFormat)
    assert publication.variant_names == ("current",)
    assert publication.variant("current").controls == {"symbol_picker": ["AAPL"]}
    description = publication.describe()
    variants = cast(dict[str, Any], description["variants"])
    assert variants["current"]["outputs"]["summary"]["formats"]["json"] == {
        "format_id": "json.v1",
        "media_type": "application/json",
        "metadata": {"rows": 1},
    }
    variants["current"]["controls"]["symbol_picker"][0] = "NVDA"
    assert publication.describe()["variants"] != variants
    assert published.format_id == "json.v1"
    assert published.media_type == "application/json"
    assert published.metadata == {"rows": 1}
    assert published.filename == "summary.json"
    assert published.bytes() == b'{"answer":42}'
    assert published.text() == '{"answer":42}'
    assert published.json() == {"answer": 42}
    assert publication.verify() == 1
    with pytest.raises(AttributeError):
        cast(Any, variant).name = "changed"
    with pytest.raises(AttributeError):
        cast(Any, output).variant = "changed"
    with pytest.raises(AttributeError):
        cast(Any, published).output = "changed"


@pytest.mark.skipif(os.name != "posix", reason="requires a literal POSIX filename backslash")
def test_reader_accepts_backslashes_in_posix_publication_root(tmp_path: Path) -> None:
    root, _, _ = _publication(tmp_path / "publication\\archive")

    assert open_publication(root).verify() == 1


@pytest.mark.parametrize(
    "key",
    [
        "project/return.bin:payload.bin",
        "project/report?draft/return.bin",
        "project/trailing./return.bin",
        "project/CON/return.bin",
        "project/CONIN$/return.bin",
        "project/conout$.txt/return.bin",
        "project/lpt9.txt/return.bin",
        "project/COM¹/return.bin",
        "project/lpt³.txt/return.bin",
    ],
)
def test_reader_rejects_nonportable_asset_key_components(tmp_path: Path, key: str) -> None:
    root, index, _ = _publication(tmp_path / "publication")
    _replace_asset_key(root, index, key)

    with pytest.raises(PublicationError):
        open_publication(root).verify()


def test_reader_verifies_digest_before_decoding(tmp_path: Path) -> None:
    root, _, asset_path = _publication(tmp_path / "publication")
    original = asset_path.read_bytes()
    asset_path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))

    published = open_publication(root).variant("current").output("summary").format("json")
    with pytest.raises(IntegrityError, match="SHA-256"):
        published.bytes()


def test_reader_rereads_an_asset_after_a_successful_operation(tmp_path: Path) -> None:
    root, _, asset_path = _publication(tmp_path / "publication")
    publication = open_publication(root)
    published = publication.variant("current").output("summary").format("json")
    original = asset_path.read_bytes()

    assert published.bytes() == b'{"answer":42}'
    asset_path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))

    with pytest.raises(IntegrityError, match="SHA-256"):
        published.bytes()


def test_reader_verify_retains_one_asset_envelope_at_a_time(tmp_path: Path) -> None:
    root, index, asset_path = _publication(
        tmp_path / "publication",
        data=b"x" * (1024 * 1024),
    )
    encoded = asset_path.read_bytes()
    formats = index["variants"]["current"]["outputs"]["summary"]["formats"]
    entry = formats.pop("json")
    for number in range(3):
        key = f"project/asset-{number}/return.bin"
        copied = deepcopy(entry)
        copied["asset"]["key"] = key
        formats[f"json_{number}"] = copied
        target = root / "cache" / key
        target.parent.mkdir(parents=True)
        target.write_bytes(encoded)
    (root / "index.json").write_bytes(canonical_bytes(index))
    publication = open_publication(root)

    tracemalloc.start()
    try:
        assert publication.verify() == 3
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < len(encoded) * 1.5


def test_reader_verifies_size_before_decoding(tmp_path: Path) -> None:
    root, _, asset_path = _publication(tmp_path / "publication")
    asset_path.write_bytes(asset_path.read_bytes()[:-1])

    with pytest.raises(IntegrityError, match="unexpected size"):
        open_publication(root).verify()


def test_reader_bounds_asset_allocations_by_default_or_explicit_limit(
    tmp_path: Path,
) -> None:
    root, _, asset_path = _publication(tmp_path / "publication")

    with pytest.raises(PublicationError, match="byte read limit"):
        open_publication(root, max_asset_bytes=asset_path.stat().st_size - 1).verify()

    assert open_publication(root, max_asset_bytes=asset_path.stat().st_size).verify() == 1


def test_reader_bounds_index_allocations_by_default_or_explicit_limit(tmp_path: Path) -> None:
    root, _, _ = _publication(tmp_path / "publication")
    size = (root / "index.json").stat().st_size

    with pytest.raises(PublicationError, match="byte read limit"):
        open_publication(root, max_index_bytes=size - 1)

    assert open_publication(root, max_index_bytes=size).verify() == 1


@pytest.mark.parametrize(
    "name",
    ["max_index_bytes", "max_asset_bytes", "max_publication_bytes"],
)
@pytest.mark.parametrize("value", [True, 0, 2**53])
def test_reader_requires_positive_safe_integer_read_limits(
    tmp_path: Path,
    name: str,
    value: object,
) -> None:
    root, _, _ = _publication(tmp_path / "publication")

    with pytest.raises(TypeError, match=f"{name} must be a positive safe integer"):
        if name == "max_index_bytes":
            open_publication(root, max_index_bytes=cast(int, value))
        elif name == "max_asset_bytes":
            open_publication(root, max_asset_bytes=cast(int, value))
        else:
            open_publication(root, max_publication_bytes=cast(int, value))


def test_reader_defaults_to_a_64_mib_asset_limit() -> None:
    parameter = inspect.signature(open_publication).parameters["max_asset_bytes"]

    assert parameter.default == 64 * 1024 * 1024


def test_reader_defaults_to_a_512_mib_publication_limit() -> None:
    parameter = inspect.signature(open_publication).parameters["max_publication_bytes"]

    assert parameter.default == 512 * 1024 * 1024


def test_reader_bounds_the_unique_publication_closure_before_asset_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _, asset_path = _publication(tmp_path / "publication")
    closure_bytes = (root / "index.json").stat().st_size + asset_path.stat().st_size

    def fail_read(*args: object, **kwargs: object) -> bytes:
        del args, kwargs
        raise AssertionError("asset bytes must not be read during closure preflight")

    monkeypatch.setattr(reader_module, "read_cache_asset", fail_read)

    with pytest.raises(PublicationError, match="publication closure exceeds"):
        open_publication(root, max_publication_bytes=closure_bytes - 1)


def test_reader_counts_a_shared_asset_once_in_the_publication_closure(
    tmp_path: Path,
) -> None:
    root, index, asset_path = _publication(tmp_path / "publication")
    formats = index["variants"]["current"]["outputs"]["summary"]["formats"]
    formats["json_copy"] = deepcopy(formats["json"])
    index_bytes = canonical_bytes(index)
    (root / "index.json").write_bytes(index_bytes)
    closure_bytes = len(index_bytes) + asset_path.stat().st_size

    publication = open_publication(root, max_publication_bytes=closure_bytes)

    assert publication.verify() == 1


def test_reader_rejects_blob_asset_fields_outside_the_codec(tmp_path: Path) -> None:
    root, _, _ = _publication(
        tmp_path / "publication",
        envelope_extra={"unexpected": True},
    )

    with pytest.raises(IntegrityError, match="valid BlobAsset envelope"):
        open_publication(root).verify()


def test_reader_rejects_duplicate_messagepack_fields(tmp_path: Path) -> None:
    root, index, asset_path = _publication(tmp_path / "publication")
    encoded = asset_path.read_bytes()
    duplicate = (
        bytes([0x85])
        + encoded[1:]
        + msgspec.msgpack.encode("data")
        + msgspec.msgpack.encode(b'{"answer":42}')
    )
    _replace_envelope(root, index, asset_path, duplicate)

    with pytest.raises(IntegrityError, match="valid BlobAsset envelope"):
        open_publication(root).verify()


def test_reader_requires_canonical_blob_asset_field_order(tmp_path: Path) -> None:
    root, index, asset_path = _publication(tmp_path / "publication")
    envelope = cast(dict[str, object], msgspec.msgpack.decode(asset_path.read_bytes()))
    reordered = msgspec.msgpack.encode(
        {
            "metadata": envelope["metadata"],
            "filename": envelope["filename"],
            "media_type": envelope["media_type"],
            "data": envelope["data"],
        }
    )
    _replace_envelope(root, index, asset_path, reordered)

    with pytest.raises(IntegrityError, match="valid BlobAsset envelope"):
        open_publication(root).verify()


@pytest.mark.parametrize("metadata_json", [b'{"x":1,"x":2}', b"[]"])
def test_reader_requires_strict_metadata_json(tmp_path: Path, metadata_json: bytes) -> None:
    root, index, asset_path = _publication(tmp_path / "publication", metadata={"x": 1})
    envelope = cast(dict[str, Any], msgspec.msgpack.decode(asset_path.read_bytes()))
    envelope["metadata"]["metadata_json"] = metadata_json
    encoded = msgspec.msgpack.encode(envelope)
    _replace_envelope(root, index, asset_path, encoded)

    with pytest.raises(IntegrityError, match="metadata must be JSON-compatible"):
        open_publication(root).verify()


def test_reader_accepts_noncanonical_strict_metadata_json(tmp_path: Path) -> None:
    root, index, asset_path = _publication(tmp_path / "publication", metadata={"x": 1})
    envelope = cast(dict[str, Any], msgspec.msgpack.decode(asset_path.read_bytes()))
    envelope["metadata"]["metadata_json"] = b'{ "x": 1 }'
    encoded = msgspec.msgpack.encode(envelope)
    _replace_envelope(root, index, asset_path, encoded)

    assert open_publication(root).verify() == 1


def test_reader_rejects_oversized_blob_metadata_before_json_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, index, asset_path = _publication(tmp_path / "publication")
    envelope = cast(dict[str, Any], msgspec.msgpack.decode(asset_path.read_bytes()))
    overhead = len(canonical_bytes({"value": ""}))
    envelope["metadata"]["metadata_json"] = canonical_bytes(
        {"value": "x" * (MAX_FORMAT_METADATA_JSON_BYTES - overhead + 1)}
    )
    encoded = msgspec.msgpack.encode(envelope)
    _replace_envelope(root, index, asset_path, encoded)

    def unexpected_decode(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("oversized metadata reached JSON decoding")

    monkeypatch.setattr(reader_module, "decode_json_object", unexpected_decode)

    with pytest.raises(IntegrityError, match="valid BlobAsset envelope"):
        open_publication(root).verify()


def test_reader_accepts_blob_metadata_json_at_the_byte_limit(
    tmp_path: Path,
) -> None:
    overhead = len(canonical_bytes({"value": ""}))
    metadata: dict[str, object] = {"value": "x" * (MAX_FORMAT_METADATA_JSON_BYTES - overhead)}
    root, _, _ = _publication(tmp_path / "publication", metadata=metadata)

    assert open_publication(root).verify() == 1


def test_reader_rejects_index_and_envelope_disagreement(tmp_path: Path) -> None:
    root, index, _ = _publication(tmp_path / "publication")
    index["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["media_type"] = (
        "text/plain"
    )
    (root / "index.json").write_bytes(canonical_bytes(index))

    with pytest.raises(IntegrityError, match="media type disagrees"):
        open_publication(root).verify()


def test_reader_bounds_mismatch_diagnostics_for_a_large_asset(tmp_path: Path) -> None:
    root, index, _ = _publication(
        tmp_path / "publication",
        data=b"x" * (63 * 1024 * 1024),
    )
    index["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["media_type"] = (
        "text/plain"
    )
    (root / "index.json").write_bytes(canonical_bytes(index))

    with pytest.raises(IntegrityError) as raised:
        open_publication(root).verify()

    assert len(str(raised.value)) < 2_048
    assert raised.value.details == {}


def test_reader_compares_metadata_by_json_type(tmp_path: Path) -> None:
    root, index, _ = _publication(tmp_path / "publication", metadata={"flag": True})
    index["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["metadata"] = {"flag": 1}
    (root / "index.json").write_bytes(canonical_bytes(index))

    with pytest.raises(IntegrityError, match="metadata disagrees"):
        open_publication(root).verify()


def test_reader_treats_integer_and_float_as_the_same_json_number(tmp_path: Path) -> None:
    root, index, _ = _publication(tmp_path / "publication", metadata={"scale": 1.0})
    index["variants"]["current"]["outputs"]["summary"]["formats"]["json"]["metadata"] = {"scale": 1}
    (root / "index.json").write_bytes(canonical_bytes(index))

    assert open_publication(root).verify() == 1


@pytest.mark.parametrize(
    "media_type",
    [
        'text/plain; charset="utf-8',
        'text/plain; charset=utf-8"',
    ],
)
def test_reader_rejects_unbalanced_charset_quotes(tmp_path: Path, media_type: str) -> None:
    root, _, _ = _publication(
        tmp_path / "publication",
        data=b"plain text",
        media_type=media_type,
    )
    published = open_publication(root).variant("current").output("summary").format("json")

    with pytest.raises(PublicationError, match="invalid text charset"):
        published.text()


def test_reader_accepts_balanced_charset_quotes(tmp_path: Path) -> None:
    root, _, _ = _publication(
        tmp_path / "publication",
        data=b"plain text",
        media_type='text/plain; charset="utf-8"',
    )

    assert (
        open_publication(root).variant("current").output("summary").format("json").text()
        == "plain text"
    )


@pytest.mark.parametrize(
    "media_type",
    ["text/plain; charset=", 'text/plain; charset=""'],
)
def test_reader_rejects_an_empty_declared_charset(tmp_path: Path, media_type: str) -> None:
    root, _, _ = _publication(
        tmp_path / "publication",
        data=b"plain text",
        media_type=media_type,
    )

    published = open_publication(root).variant("current").output("summary").format("json")
    with pytest.raises(PublicationError, match="invalid text charset"):
        published.text()


def test_reader_text_accepts_utf8_and_rejects_other_charsets(tmp_path: Path) -> None:
    root, _, _ = _publication(
        tmp_path / "publication",
        data=b"\xff\xfeA\x00",
        media_type="text/plain; charset=utf-16",
    )
    published = open_publication(root).variant("current").output("summary").format("json")

    assert published.bytes() == b"\xff\xfeA\x00"
    with pytest.raises(PublicationError, match=r"use bytes\(\)"):
        published.text()


def test_reader_verifies_integrity_before_rejecting_a_charset(tmp_path: Path) -> None:
    root, _, asset_path = _publication(
        tmp_path / "publication",
        data=b"text",
        media_type="text/plain; charset=utf-16",
    )
    original = asset_path.read_bytes()
    asset_path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    published = open_publication(root).variant("current").output("summary").format("json")

    with pytest.raises(IntegrityError, match="SHA-256"):
        published.text()


def test_reader_text_consumes_a_utf8_bom_while_json_rejects_it(tmp_path: Path) -> None:
    root, _, _ = _publication(
        tmp_path / "publication",
        data=b"\xef\xbb\xbfplain text",
        media_type="text/plain; charset=utf-8",
    )
    published = open_publication(root).variant("current").output("summary").format("json")

    assert published.text() == "plain text"
    with pytest.raises(PublicationError, match="valid JSON"):
        published.json()


def test_reader_json_applies_an_explicit_value_limit(tmp_path: Path) -> None:
    root, _, _ = _publication(
        tmp_path / "publication",
        data=b"[0,1]",
    )
    published = open_publication(root).variant("current").output("summary").format("json")

    assert published.json(max_values=3) == [0, 1]
    with pytest.raises(PublicationError, match="valid JSON"):
        published.json(max_values=2)
    with pytest.raises(TypeError, match="positive safe integer"):
        published.json(max_values=True)


def test_reader_requires_a_named_format(tmp_path: Path) -> None:
    root, index, _ = _publication(tmp_path / "publication")
    json_entry = index["variants"]["current"]["outputs"]["summary"]["formats"]["json"]
    index["variants"]["current"]["outputs"]["summary"]["formats"]["alternate"] = json_entry
    (root / "index.json").write_bytes(canonical_bytes(index))

    output = open_publication(root).variant("current").output("summary")

    assert set(output.formats) == {"json", "alternate"}
    with pytest.raises(PublicationError, match="has no format") as raised:
        output.format("missing")

    assert raised.value.code == "not_found"
    assert raised.value.details == {
        "kind": "format",
        "name": "missing",
        "name_truncated": False,
        "available": ["alternate", "json"],
        "available_count": 2,
        "available_truncated": False,
    }


def test_reader_bounds_not_found_available_names_by_count(tmp_path: Path) -> None:
    root, index, _ = _publication(tmp_path / "publication")
    formats = index["variants"]["current"]["outputs"]["summary"]["formats"]
    entry = formats.pop("json")
    names = [f"format-{index:02d}" for index in range(20)]
    formats.update({name: deepcopy(entry) for name in reversed(names)})
    (root / "index.json").write_bytes(canonical_bytes(index))

    output = open_publication(root).variant("current").output("summary")
    with pytest.raises(PublicationError) as raised:
        output.format("missing")

    assert raised.value.details == {
        "kind": "format",
        "name": "missing",
        "name_truncated": False,
        "available": names[:16],
        "available_count": 20,
        "available_truncated": True,
    }


def test_reader_bounds_not_found_available_names_by_utf8_bytes(tmp_path: Path) -> None:
    root, index, _ = _publication(tmp_path / "publication")
    formats = index["variants"]["current"]["outputs"]["summary"]["formats"]
    entry = formats.pop("json")
    names = ["a" * 1_024, "b" * 1_024, "😀", "z"]
    formats.update({name: deepcopy(entry) for name in reversed(names)})
    (root / "index.json").write_bytes(canonical_bytes(index))

    output = open_publication(root).variant("current").output("summary")
    with pytest.raises(PublicationError) as raised:
        output.format("missing")

    details = raised.value.details
    available = cast(list[str], details["available"])
    assert available == names[:2]
    assert details["available_count"] == 4
    assert details["available_truncated"] is True
    assert sum(len(name.encode("utf-8")) for name in available) == 2_048


def test_reader_bounds_and_escapes_the_requested_name(tmp_path: Path) -> None:
    root, _, _ = _publication(tmp_path / "publication")
    requested = "\x1b[31m" + "😀" * 1_000

    with pytest.raises(PublicationError) as raised:
        open_publication(root).variant(requested)

    details = raised.value.details
    bounded_name = cast(str, details["name"])
    assert details["name_truncated"] is True
    assert len(bounded_name.encode("utf-8")) <= 2_048
    assert bounded_name.endswith("...")
    assert "\x1b" not in bounded_name
    assert r"\x1b" in bounded_name
    assert "\x1b" not in str(raised.value)
    assert len(str(raised.value)) <= 4_096


def test_reader_sorts_numeric_and_unicode_navigation_names(tmp_path: Path) -> None:
    root, index, _ = _publication(tmp_path / "publication")
    variant = index["variants"]["current"]
    output = variant["outputs"]["summary"]
    format_entry = output["formats"]["json"]
    output["formats"] = {
        "😀": deepcopy(format_entry),
        "10": deepcopy(format_entry),
        "\ue000": deepcopy(format_entry),
        "2": deepcopy(format_entry),
    }
    variant["outputs"] = {
        "😀": deepcopy(output),
        "10": deepcopy(output),
        "\ue000": deepcopy(output),
        "2": deepcopy(output),
    }
    index["variants"] = {
        "😀": deepcopy(variant),
        "10": deepcopy(variant),
        "\ue000": deepcopy(variant),
        "2": deepcopy(variant),
    }
    (root / "index.json").write_bytes(canonical_bytes(index))

    publication = open_publication(root)
    expected = ("10", "2", "\ue000", "😀")

    assert publication.variant_names == expected
    assert publication.variant("10").outputs == expected
    assert publication.variant("10").output("10").formats == expected
    assert tuple(cast(dict[str, Any], publication.describe()["variants"])) == expected

    with pytest.raises(PublicationError) as raised:
        publication.variant("missing")
    assert raised.value.details["available"] == list(expected)
    assert raised.value.details["available_count"] == 4
    assert raised.value.details["available_truncated"] is False


@pytest.mark.parametrize(
    "filename",
    [
        "result\x7f.json",
        "CON.json",
        "report.json:secret",
        "report?.json",
        "report.json.",
        "é" * 128,
    ],
)
def test_reader_rejects_nonportable_blob_filenames(
    tmp_path: Path,
    filename: str,
) -> None:
    root, _, _ = _publication(tmp_path / "publication", filename=filename)
    published = open_publication(root).variant("current").output("summary").format("json")

    with pytest.raises(IntegrityError, match="filename is invalid"):
        _ = published.filename


def test_reader_accepts_a_255_byte_blob_filename(tmp_path: Path) -> None:
    filename = f"{'é' * 127}a"
    root, _, _ = _publication(tmp_path / "publication", filename=filename)

    published = open_publication(root).variant("current").output("summary").format("json")

    assert published.filename == filename


def test_reader_rejects_assets_that_resolve_outside_cache(tmp_path: Path) -> None:
    root, _, asset_path = _publication(tmp_path / "publication")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(asset_path.read_bytes())
    asset_path.unlink()
    try:
        asset_path.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")

    with pytest.raises(PublicationError, match="read securely"):
        open_publication(root).verify()
