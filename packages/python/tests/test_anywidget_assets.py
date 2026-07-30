from __future__ import annotations

import base64

import marimo_export._marimo.compat.anywidget_assets as portable_assets
import pytest


def test_embedded_esm_accepts_self_contained_and_remote_dependencies() -> None:
    portable_assets.validate_embedded_esm(
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
            portable_assets.validate_embedded_esm(source)


def test_css_accepts_remote_and_document_local_references() -> None:
    css = """
    /* url("./comment.svg"); @import "./comment.css"; */
    .label::before { content: "url('./content.svg')"; }
    .mask { mask: url(#mask); background: url(/host-asset.png); }
    .remote { background: URL("https://cdn.example.test/image.png"); }
    @import url("https://cdn.example.test/theme.css") screen;
    @import "data:text/css,.label%7Bcolor:green%7D";
    """

    assert portable_assets.portable_css(css) == css


def test_css_rejects_unresolved_assets() -> None:
    for css in (
        '.icon { background: url("./icon.svg"); }',
        ".icon { background: url(); }",
        "@import var(--theme);",
    ):
        with pytest.raises(ValueError, match="AnyWidget CSS"):
            portable_assets.portable_css(css)


def test_css_inlines_marimo_virtual_files(monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"<svg/>"

    def read(name: str, size: int) -> bytes:
        assert (name, size) == ("icon.svg", len(content))
        return content

    monkeypatch.setattr(portable_assets, "read_virtual_file", read)

    css = portable_assets.portable_css(
        f'.icon {{ background: url("./@file/{len(content)}-icon.svg"); }}'
    )

    assert f"data:image/svg+xml;base64,{base64.b64encode(content).decode()}" in css


def test_css_rejects_incomplete_or_imported_virtual_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(portable_assets, "read_virtual_file", lambda _name, _size: b"x")

    with pytest.raises(ValueError, match="declared 4 bytes but returned 1"):
        portable_assets.portable_css('.icon { background: url("./@file/4-icon.svg"); }')
    with pytest.raises(ValueError, match="virtual files must be bundled"):
        portable_assets.portable_css('@import "./@file/12-theme.css";')
