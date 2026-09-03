"""Close snapshot HTML over virtual and notebook public files."""

from __future__ import annotations

import base64
import html
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import msgspec

from marimo_export._json import JsonValue, canonical_bytes, json_value
from marimo_export._media_type import media_type_for_filename
from marimo_export.errors import OutputError

if TYPE_CHECKING:
    from marimo_export._marimo.compat.projections import ProjectionRecording

MAX_INLINE_FILE_BYTES = 10 * 1024 * 1024
MIMEBUNDLE_MIMETYPE = "application/vnd.marimo+mimebundle"

_HTML_MIMETYPES = frozenset({"text/html", "text/markdown"})
_URL_ATTRIBUTES = {
    "a": frozenset({"href"}),
    "area": frozenset({"href"}),
    "audio": frozenset({"src"}),
    "base": frozenset({"href"}),
    "button": frozenset({"formaction"}),
    "embed": frozenset({"src"}),
    "form": frozenset({"action"}),
    "iframe": frozenset({"src"}),
    "image": frozenset({"href", "xlink:href"}),
    "img": frozenset({"src"}),
    "input": frozenset({"formaction", "src"}),
    "link": frozenset({"href"}),
    "object": frozenset({"data"}),
    "script": frozenset({"href", "src", "xlink:href"}),
    "source": frozenset({"src"}),
    "track": frozenset({"src"}),
    "use": frozenset({"href", "xlink:href"}),
    "video": frozenset({"poster", "src"}),
}
_SRCSET_ATTRIBUTES = {
    "img": frozenset({"srcset"}),
    "source": frozenset({"srcset"}),
}
_CSS_URL = re.compile(
    r"url\(\s*(?P<quote>['\"]?)(?P<url>[^'\")]*?)(?P=quote)\s*\)",
    re.IGNORECASE,
)
_PUBLIC_REFERENCE = re.compile(r"(?:^|[\s,('\"])(?:\./)?public/")


def decode_mimebundle(value: object) -> dict[str, object] | None:
    decoded = value
    if isinstance(value, str):
        try:
            decoded = msgspec.json.decode(value)
        except msgspec.DecodeError:
            return None
    if not isinstance(decoded, dict) or any(not isinstance(key, str) for key in decoded):
        return None
    return cast(dict[str, object], decoded)


def mimebundle_entries(value: dict[str, object]) -> tuple[dict[str, object], ...]:
    nested = value.get("data")
    if isinstance(nested, dict) and all(isinstance(key, str) for key in nested):
        return (value, cast(dict[str, object], nested))
    return (value,)


def close_output_files(
    recording: ProjectionRecording,
    value: JsonValue,
    mimetype: str,
) -> JsonValue:
    if mimetype in _HTML_MIMETYPES and isinstance(value, str):
        return _close_html_files(recording, value)
    if mimetype != MIMEBUNDLE_MIMETYPE:
        return value
    bundle = decode_mimebundle(value)
    if bundle is None:
        return value
    for entries in mimebundle_entries(bundle):
        html_value = entries.get("text/html")
        if isinstance(html_value, str):
            entries["text/html"] = _close_html_files(recording, html_value)
    if isinstance(value, str):
        return canonical_bytes(cast(JsonValue, bundle)).decode("utf-8")
    return cast(JsonValue, bundle)


def _close_html_files(recording: ProjectionRecording, value: str) -> str:
    return _ResourceResolver(recording).close_html(value)


class _ResourceResolver:
    def __init__(self, recording: ProjectionRecording) -> None:
        from marimo._convert.common.dom_traversal import (
            _PUBLIC_FILE_PATTERN,
            _is_virtual_file_url,
            _parse_virtual_file_url,
            _resolve_public_file,
        )

        self._is_virtual_file_url = _is_virtual_file_url
        self._parse_virtual_file_url = _parse_virtual_file_url
        self._public_file_pattern = _PUBLIC_FILE_PATTERN
        self._resolve_public_file = _resolve_public_file
        context = recording.child._runtime_context
        registries: list[object] = []
        seen_contexts: set[int] = set()
        current = context
        while current is not None and id(current) not in seen_contexts:
            seen_contexts.add(id(current))
            registry = getattr(current, "virtual_file_registry", None)
            if registry is not None:
                registries.append(registry)
            current = getattr(current, "parent", None)
        self._registries = tuple(registries)
        filename = context.filename
        self._public_dir = (
            Path(filename).resolve().parent / "public"
            if isinstance(filename, str) and filename
            else None
        )
        self._closed: dict[str, str] = {}

    def close_html(self, value: str) -> str:
        parser = _HTMLResourceCloser(self)
        try:
            parser.feed(value)
            parser.close()
        except OutputError:
            raise
        except Exception as error:
            raise OutputError(
                "snapshot HTML resources could not be inspected",
                code="output_execution_failed",
            ) from error
        return parser.output()

    def is_known(self, value: str) -> bool:
        candidate = value.strip()
        return bool(
            self._is_virtual_file_url(candidate) or self._public_file_pattern.fullmatch(candidate)
        )

    def contains_known(self, value: str) -> bool:
        return "./@file/" in value or _PUBLIC_REFERENCE.search(value) is not None

    def close_url(self, value: str) -> str:
        candidate = value.strip()
        cached = self._closed.get(candidate)
        if cached is not None:
            return cached
        if self._is_virtual_file_url(candidate):
            data_url = self._close_virtual(candidate)
        elif self._public_file_pattern.fullmatch(candidate):
            data_url = self._close_public(candidate)
        else:
            return value
        self._closed[candidate] = data_url
        return data_url

    def close_srcset(self, value: str) -> str:
        candidates: list[str] = []
        for raw_candidate in value.split(","):
            match = re.fullmatch(r"(\s*)(\S+)(.*)", raw_candidate, re.DOTALL)
            if match is None:
                candidates.append(raw_candidate)
                continue
            prefix, url, descriptor = match.groups()
            candidates.append(f"{prefix}{self.close_url(url)}{descriptor}")
        rewritten = ",".join(candidates)
        if self.contains_known(rewritten):
            raise OutputError(
                "snapshot srcset contains an unclosed local file",
                code="output_execution_failed",
            )
        return rewritten

    def close_css(self, value: str) -> str:
        def replace(match: re.Match[str]) -> str:
            url = match.group("url").strip()
            if not self.is_known(url):
                return match.group(0)
            return f'url("{self.close_url(url)}")'

        rewritten = _CSS_URL.sub(replace, value)
        if self.contains_known(rewritten):
            raise OutputError(
                "snapshot CSS contains an unclosed local file",
                code="output_execution_failed",
            )
        return rewritten

    def _close_virtual(self, url: str) -> str:
        parsed = self._parse_virtual_file_url(url)
        if parsed is None:
            raise OutputError(
                f"virtual file {url!r} is invalid",
                code="output_execution_failed",
            )
        byte_length, filename = parsed
        if byte_length > MAX_INLINE_FILE_BYTES:
            raise OutputError(
                f"virtual file {url!r} exceeds the snapshot inline limit",
                code="output_execution_failed",
            )
        try:
            tracked = any(cast(Any, registry).has(filename) for registry in self._registries)
        except Exception as error:
            raise OutputError(
                f"virtual file {url!r} could not be verified",
                code="output_execution_failed",
            ) from error
        if not tracked:
            raise OutputError(
                f"virtual file {url!r} is not owned by the state run",
                code="output_execution_failed",
            )
        try:
            from marimo._runtime.virtual_file import read_virtual_file

            data = read_virtual_file(filename, byte_length)
        except Exception as error:
            raise OutputError(
                f"virtual file {url!r} could not be closed",
                code="output_execution_failed",
            ) from error
        if len(data) != byte_length:
            raise OutputError(
                f"virtual file {url!r} changed while it was closed",
                code="output_execution_failed",
            )
        return _data_url(filename, data)

    def _close_public(self, url: str) -> str:
        match = self._public_file_pattern.fullmatch(url)
        if match is None or self._public_dir is None:
            raise OutputError(
                f"public file {url!r} has no notebook directory",
                code="output_execution_failed",
            )
        resolved = self._resolve_public_file(self._public_dir, match.group(1))
        if resolved is None:
            raise OutputError(
                f"public file {url!r} could not be closed",
                code="output_execution_failed",
            )
        try:
            path_before = resolved.stat()
            if path_before.st_size > MAX_INLINE_FILE_BYTES:
                raise OutputError(
                    f"public file {url!r} exceeds the snapshot inline limit",
                    code="output_execution_failed",
                )
            with resolved.open("rb") as stream:
                opened_before = os.fstat(stream.fileno())
                data = stream.read(MAX_INLINE_FILE_BYTES + 1)
                opened_after = os.fstat(stream.fileno())
            path_after = resolved.stat()
        except OutputError:
            raise
        except OSError as error:
            raise OutputError(
                f"public file {url!r} could not be closed",
                code="output_execution_failed",
            ) from error
        if len(data) > MAX_INLINE_FILE_BYTES:
            raise OutputError(
                f"public file {url!r} exceeds the snapshot inline limit",
                code="output_execution_failed",
            )
        revisions = tuple(
            _file_revision(details)
            for details in (path_before, opened_before, opened_after, path_after)
        )
        if len(set(revisions)) != 1 or len(data) != opened_after.st_size:
            raise OutputError(
                f"public file {url!r} changed while it was closed",
                code="output_execution_failed",
            )
        return _data_url(resolved.name, data)


class _HTMLResourceCloser(HTMLParser):
    def __init__(self, resolver: _ResourceResolver) -> None:
        super().__init__(convert_charrefs=False)
        self._resolver = resolver
        self._output: list[str] = []
        self._script_depth = 0
        self._style_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._output.append(f"<{tag}{self._attributes(tag, attrs)}>")
        tag_name = tag.lower()
        if tag_name == "script":
            self._script_depth += 1
        if tag_name == "style":
            self._style_depth += 1

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._output.append(f"<{tag}{self._attributes(tag, attrs)} />")

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name == "script" and self._script_depth:
            self._script_depth -= 1
        if tag_name == "style" and self._style_depth:
            self._style_depth -= 1
        self._output.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._script_depth and self._resolver.contains_known(data):
            raise OutputError(
                "snapshot script contains an unclosed local file",
                code="output_execution_failed",
            )
        self._output.append(self._resolver.close_css(data) if self._style_depth else data)

    def handle_entityref(self, name: str) -> None:
        self._output.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._output.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self._output.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self._output.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self._output.append(f"<?{data}>")

    def output(self) -> str:
        return "".join(self._output)

    def _attributes(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> str:
        values: list[str] = []
        tag_name = tag.lower()
        for name, value in attrs:
            if value is None:
                values.append(name)
                continue
            rewritten = self._attribute(tag_name, name.lower(), value)
            values.append(f'{name}="{html.escape(rewritten, quote=True)}"')
        return f" {' '.join(values)}" if values else ""

    def _attribute(self, tag: str, name: str, value: str) -> str:
        if tag == "marimo-json-output" and name == "data-json-data":
            return _close_component_json(value, self._resolver)
        if tag == "iframe" and name == "srcdoc":
            return self._resolver.close_html(value)
        if name == "style":
            return self._resolver.close_css(value)
        if name in _SRCSET_ATTRIBUTES.get(tag, ()):
            return self._resolver.close_srcset(value)
        if name in _URL_ATTRIBUTES.get(tag, ()):
            rewritten = self._resolver.close_url(value)
            if self._resolver.contains_known(rewritten):
                raise OutputError(
                    f"snapshot attribute {tag}.{name} contains an unclosed local file",
                    code="output_execution_failed",
                )
            return rewritten
        if self._resolver.contains_known(value):
            raise OutputError(
                f"snapshot attribute {tag}.{name} contains an unclosed local file",
                code="output_execution_failed",
            )
        return value


def _close_component_json(value: str, resolver: _ResourceResolver) -> str:
    try:
        decoded = msgspec.json.decode(value)
    except msgspec.DecodeError as error:
        raise OutputError(
            "Marimo component resource data is invalid",
            code="output_execution_failed",
        ) from error
    rewritten = _close_component_value(decoded, resolver)
    return canonical_bytes(json_value(rewritten, "Marimo component resource data")).decode("utf-8")


def _close_component_value(value: object, resolver: _ResourceResolver) -> object:
    if isinstance(value, str):
        prefix = "text/html:"
        if value.startswith(prefix):
            return prefix + resolver.close_html(value[len(prefix) :])
        return value
    if isinstance(value, list):
        return [_close_component_value(item, resolver) for item in value]
    if isinstance(value, dict):
        return {str(key): _close_component_value(item, resolver) for key, item in value.items()}
    return value


def _data_url(filename: str, data: bytes) -> str:
    media_type = media_type_for_filename(filename, default="application/octet-stream")
    payload = base64.b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{payload}"


def _file_revision(details: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
        details.st_ctime_ns,
    )


__all__ = ["close_output_files"]
