from __future__ import annotations

import base64

import pytest
from marimo_export._file_urls import VirtualFileUrl
from marimo_export.exporters._anywidget_assets import portable_css, validate_embedded_esm


def _unread(virtual_file: VirtualFileUrl) -> bytes:
    raise AssertionError(f"unexpected virtual file read: {virtual_file}")


def test_embedded_esm_accepts_self_contained_and_remote_dependencies() -> None:
    validate_embedded_esm(
        r"""
        const ordinary = "import './string.js'";
        const template = `import("./template.js") ${"export * from './expression.js'"}`;
        // import "./line-comment.js";
        /* export * from "./block-comment.js"; */
        import "https://cdn.example.test/side-effect.js";
        import value from "data:text/javascript,export default 1";
        export { value as remote } from "http://cdn.example.test/re-export.js";
        async function load() {
          await import("HTTPS://cdn.example.test/dynamic.js");
        }
        const loader = { import() { return 1; } };
        new URL("https://cdn.example.test/asset.svg", import.meta.url);
        """
    )


def test_embedded_esm_rejects_unresolved_dependencies() -> None:
    for source in (
        'import "./child.js";',
        "import(moduleUrl);",
        'new URL("./asset.svg", import.meta.url);',
    ):
        with pytest.raises(ValueError, match=r"must use a literal data:"):
            validate_embedded_esm(source)


def test_css_accepts_remote_and_document_local_references() -> None:
    css = """
    /* url("./comment.svg"); @import "./comment.css"; */
    .label::before { content: "url('./content.svg')"; }
    .mask { mask: url(#mask); background: url(/host-asset.png); }
    .remote { background: URL("https://cdn.example.test/image.png"); }
    @import url("https://cdn.example.test/theme.css") screen;
    @import "data:text/css,.label%7Bcolor:green%7D";
    """

    assert portable_css(css, read_file=_unread) == css


def test_css_rejects_unresolved_assets() -> None:
    for css in (
        '.icon { background: url("./icon.svg"); }',
        ".icon { background: url(); }",
        "@import var(--theme);",
    ):
        with pytest.raises(ValueError, match="AnyWidget CSS"):
            portable_css(css, read_file=_unread)


@pytest.mark.parametrize("prefix", ["./@file/", "/@file/", "@file/"])
def test_css_inlines_marimo_virtual_files(prefix: str) -> None:
    content = b"<svg/>"
    reads: list[VirtualFileUrl] = []

    def read(virtual_file: VirtualFileUrl) -> bytes:
        reads.append(virtual_file)
        return content

    css = portable_css(
        f'.icon {{ background: url("{prefix}{len(content)}-icon.svg"); }}',
        read_file=read,
    )

    assert css == (
        f'.icon {{ background: url("data:image/svg+xml;base64,'
        f'{base64.b64encode(content).decode()}"); }}'
    )
    assert reads == [VirtualFileUrl(len(content), "icon.svg")]


def test_css_inlines_empty_virtual_files_without_reading() -> None:
    css = portable_css('.icon { background: url("@file/0-empty.txt"); }', read_file=_unread)

    assert css == '.icon { background: url("data:text/plain;base64,"); }'


def test_css_rejects_malformed_virtual_file_urls() -> None:
    for url in ("./@file/icon.svg", "/@file/-icon.svg", "@file/12-"):
        with pytest.raises(ValueError, match="malformed marimo virtual file URL"):
            portable_css(f'.icon {{ background: url("{url}"); }}', read_file=_unread)


def test_css_rejects_incomplete_or_imported_virtual_files() -> None:
    with pytest.raises(ValueError, match="declared 4 bytes but returned 1"):
        portable_css(
            '.icon { background: url("./@file/4-icon.svg"); }',
            read_file=lambda _virtual_file: b"x",
        )
    with pytest.raises(ValueError, match="virtual files must be bundled"):
        portable_css('@import "./@file/12-theme.css";', read_file=_unread)
