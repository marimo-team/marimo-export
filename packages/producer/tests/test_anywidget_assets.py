from __future__ import annotations

import base64

import marimo_export._marimo._anywidget_assets as assets
import marimo_export._marimo.anywidget as anywidget_module
import pytest
from marimo._messaging.notification import EsmSpec


def test_embedded_esm_ignores_dependency_text_outside_javascript_code() -> None:
    assets.validate_embedded_esm(
        r"""
        const ordinary = "import './string.js'";
        const template = `import("./template.js") ${"export * from './expression.js'"}`;
        const pattern = /import\(['"]\.\/regex\.js['"]\)/;
        // import "./line-comment.js";
        /* export * from "./block-comment.js"; */
        if (ordinary) /import\(['"]\.\/conditional\.js['"]\)/.test(ordinary);
        export function render() {}
        """
    )


def test_embedded_esm_accepts_explicit_remote_and_data_dependencies() -> None:
    assets.validate_embedded_esm(
        """
        import "https://cdn.example.test/side-effect.js";
        import value from "data:text/javascript,export default 1";
        export { value as remote } from "http://cdn.example.test/re-export.js";
        async function load() {
          await import("HTTPS://cdn.example.test/dynamic.js");
        }
        new URL("https://cdn.example.test/asset.svg", import.meta.url);
        new URL(runtimePath, document.baseURI);
        """
    )


def test_embedded_esm_scans_template_expressions() -> None:
    with pytest.raises(ValueError, match="dynamic import must use a literal"):
        assets.validate_embedded_esm('const value = `${await import("./child.js")}`;')


def test_embedded_esm_scans_template_expressions_after_regex_literals() -> None:
    with pytest.raises(ValueError, match="dynamic import must use a literal"):
        assets.validate_embedded_esm('const value = `${/}/.test(text) && import("./child.js")}`;')


def test_embedded_esm_ignores_regex_after_control_block() -> None:
    assets.validate_embedded_esm(r'if (ready) {} /import\(["\']\.\/child\.js["\']\)/.test(text);')


def test_local_export_list_is_not_treated_as_a_reexport() -> None:
    assets.validate_embedded_esm(
        """
        const local = 1;
        export { local }
        const from = "./ordinary-string.js";
        """
    )


def test_embedded_esm_preserves_escaped_identifiers() -> None:
    assets.validate_embedded_esm(r"const caf\u00e9 = 1; export { caf\u00e9 };")


def test_embedded_esm_accepts_object_method_named_import() -> None:
    assets.validate_embedded_esm("const loader = { import() { return 1; } };")


def test_embedded_esm_accepts_class_method_named_import() -> None:
    assets.validate_embedded_esm("class Loader { import() { return 1; } }")


def test_embedded_esm_accepts_multiline_method_named_import() -> None:
    assets.validate_embedded_esm(
        """
        const loader = {
          import(moduleUrl)
          {
            return moduleUrl;
          }
        };
        """
    )


def test_embedded_esm_accepts_object_property_named_import_without_semicolons() -> None:
    assets.validate_embedded_esm(
        """
        const config = { import: "ordinary" }
        const from = "ordinary";
        export function render() {}
        """
    )


def test_embedded_esm_rejects_dynamic_import_followed_by_asi_block() -> None:
    with pytest.raises(ValueError, match="dynamic import must use a literal"):
        assets.validate_embedded_esm(
            """
            async function load(moduleUrl) {
              import(moduleUrl)
              {}
            }
            """
        )


@pytest.mark.parametrize(
    "source",
    [
        'import "./child.js";',
        'import value from "widget-package";',
        'export * from "/root.js";',
        'import "//cdn.example.test/module.js";',
        'import "#package-import";',
        'import "blob:https://example.test/module";',
        'import("file:///tmp/module.js");',
        "import(moduleUrl);",
        'import("https://cdn.example.test/" + moduleName);',
        'new URL("./asset.svg", import.meta.url);',
        'new URL("./asset.svg", import.meta.url,);',
        'new URL("./asset.svg", import.meta.url, ignored);',
        "new URL(assetPath, import.meta.url);",
        "new URL(`https://cdn.example.test/${name}`, import.meta.url);",
        r'new U\u0052L("./asset.svg", import.meta.url);',
        r'import(".\x2fchild.js");',
    ],
)
def test_embedded_esm_rejects_nonportable_dependency_operands(source: str) -> None:
    with pytest.raises(ValueError, match=r"must use a literal data:|escaped identifiers"):
        assets.validate_embedded_esm(source)


def test_css_ignores_comments_and_ordinary_strings() -> None:
    css = """
    /* url("./comment.svg"); @import "./comment.css"; */
    .label::before { content: "url('./content.svg')"; }
    .mask { mask: url(#mask); background: url(/host-asset.png); }
    .remote { background: URL("https://cdn.example.test/image.png"); }
    """

    assert assets.portable_css(css) == css


def test_css_preserves_escaped_selectors() -> None:
    css = r".sm\:block { display: block; }"

    assert assets.portable_css(css) == css


def test_css_accepts_explicit_import_urls() -> None:
    css = """
    @import url("https://cdn.example.test/theme.css") screen;
    @import "data:text/css,.label%7Bcolor:green%7D";
    """

    assert assets.portable_css(css) == css


@pytest.mark.parametrize(
    "css",
    [
        '.icon { background: url("./icon.svg"); }',
        '.icon { background: url("../icon.svg"); }',
        '.icon { background: url("icon.svg"); }',
        '.icon { background: url("//cdn.example.test/icon.svg"); }',
        '.icon { background: url("blob:https://example.test/icon"); }',
        r'.icon { background: u\72l("./icon.svg"); }',
        r'.icon { background: url("/\40 file/icon.svg"); }',
        ".icon { background: url(); }",
        '@import "./theme.css";',
        "@import var(--theme);",
    ],
)
def test_css_rejects_unresolved_or_computed_assets(css: str) -> None:
    with pytest.raises(ValueError, match="AnyWidget CSS"):
        assets.portable_css(css)


def test_css_inlines_marimo_virtual_file(monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"<svg/>"

    def read(name: str, size: int) -> bytes:
        assert (name, size) == ("icon.svg", len(content))
        return content

    monkeypatch.setattr(assets, "read_virtual_file", read)

    css = assets.portable_css(f'.icon {{ background: url("./@file/{len(content)}-icon.svg"); }}')

    assert f"data:image/svg+xml;base64,{base64.b64encode(content).decode()}" in css


def test_css_inlines_empty_marimo_virtual_file_without_store_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_read(name: str, size: int) -> bytes:
        raise AssertionError((name, size))

    monkeypatch.setattr(assets, "read_virtual_file", fail_read)

    assert assets.portable_css('.empty { src: url("@file/0-empty.svg"); }') == (
        '.empty { src: url("data:image/svg+xml;base64,"); }'
    )


def test_css_rejects_malformed_virtual_file() -> None:
    with pytest.raises(ValueError, match="malformed marimo virtual file URL"):
        assets.portable_css('.icon { background: url("./@file/not-a-size-icon.svg"); }')


def test_css_rejects_truncated_virtual_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assets, "read_virtual_file", lambda _name, _size: b"x")

    with pytest.raises(ValueError, match="declared 4 bytes but returned 1"):
        assets.portable_css('.icon { background: url("./@file/4-icon.svg"); }')


def test_css_rejects_virtual_stylesheet_import() -> None:
    with pytest.raises(ValueError, match="virtual files must be bundled"):
        assets.portable_css('@import "./@file/12-theme.css";')


def test_embedded_esm_rejects_invalid_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anywidget_module, "read_virtual_file", lambda _name, _size: b"\xff")

    with pytest.raises(ValueError, match="must contain UTF-8 JavaScript"):
        anywidget_module._canonical_esm_spec(EsmSpec(url="./@file/1-widget.js", hash="unused"), {})


def test_embedded_esm_rejects_truncated_virtual_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(anywidget_module, "read_virtual_file", lambda _name, _size: b"x")

    with pytest.raises(ValueError, match="declared 2 bytes but returned 1"):
        anywidget_module._canonical_esm_spec(EsmSpec(url="./@file/2-widget.js", hash="unused"), {})


def test_embedded_esm_rejects_hash_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(anywidget_module, "read_virtual_file", lambda _name, _size: b"x")

    with pytest.raises(ValueError, match="do not match marimo's model code hash"):
        anywidget_module._canonical_esm_spec(EsmSpec(url="./@file/1-widget.js", hash="wrong"), {})
