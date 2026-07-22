from __future__ import annotations

import base64
import mimetypes
import re
from html.parser import HTMLParser
from typing import cast

from marimo._convert.common.dom_traversal import replace_html_attributes
from marimo._messaging.mimetypes import KnownMimeType
from marimo._runtime.virtual_file import read_virtual_file
from marimo._utils.data_uri import build_data_url

_VIRTUAL_FILE_URL = re.compile(r"(?<![\w.-])(?:\./|/)?@file/\d+-[^\s\"'<>;&)\]]+")
_VIRTUAL_FILE_SRC = re.compile(r"^\./@file/(\d+)-(.+)$")
_VIRTUAL_FILE_MARKERS = ("./@file/", "/@file/", "@file/")
_PORTABLE_MEDIA_TAGS = {"img", "audio", "video"}


class _MarimoElementFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if self.tag is None and tag.lower().startswith("marimo-"):
            self.tag = tag

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


class _PortableMediaFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() not in _PORTABLE_MEDIA_TAGS:
            return
        for name, value in attrs:
            if name.lower() == "src" and value is not None and value.startswith("./@file/"):
                self.references.append(value)
                return

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)


def prepare_html_cache_text(text: str) -> str:
    """Resolve portable virtual media for projection cache identity."""

    finder = _PortableMediaFinder()
    finder.feed(text)
    if not finder.references:
        return text

    def inline(reference: str) -> str | None:
        match = _VIRTUAL_FILE_SRC.fullmatch(reference)
        if match is None:
            return None
        expected_size = int(match.group(1))
        try:
            contents = read_virtual_file(match.group(2), expected_size)
        except Exception as error:
            raise _virtual_file_error(reference) from error
        if len(contents) != expected_size:
            raise ValueError(
                "HTML projection read a truncated marimo virtual file: "
                f"{reference!r} declared {expected_size} bytes but returned "
                f"{len(contents)}"
            )
        media_type = mimetypes.guess_type(match.group(2))[0] or "text/plain"
        return build_data_url(
            cast(KnownMimeType, media_type),
            base64.b64encode(contents),
        )

    rendered = replace_html_attributes(
        html=text,
        allowed_tags=_PORTABLE_MEDIA_TAGS,
        allowed_attributes={"src"},
        replacer_fn=inline,
    )
    unresolved_media = _PortableMediaFinder()
    unresolved_media.feed(rendered)
    if unresolved_media.references:
        raise _virtual_file_error(unresolved_media.references[0])
    return rendered


def _virtual_file_reference(text: str) -> str | None:
    unresolved = _VIRTUAL_FILE_URL.search(text)
    if unresolved is not None:
        return unresolved.group(0)
    return next((marker for marker in _VIRTUAL_FILE_MARKERS if marker in text), None)


def _virtual_file_error(reference: str) -> ValueError:
    return ValueError(
        "HTML projection contains a marimo virtual-file reference that "
        f"cannot be published as a standalone fragment: {reference!r}"
    )


def prepare_html_projection(text: str) -> str:
    """Return a static HTML fragment independent of marimo runtime services."""

    rendered = prepare_html_cache_text(text)
    reference = _virtual_file_reference(rendered)
    if reference is not None:
        raise _virtual_file_error(reference)
    finder = _MarimoElementFinder()
    finder.feed(rendered)
    if finder.tag is not None:
        raise ValueError(
            f"HTML projection contains marimo runtime element <{finder.tag}>. "
            "Choose a dedicated portable exporter and frontend loader for this value."
        )
    return rendered
