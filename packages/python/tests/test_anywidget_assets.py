from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import marimo_export._marimo.compat.anywidget_assets as portable_assets
import pytest
from marimo_export.exporters._anywidget_payload import validate_anywidget_payload

_FIXTURE = Path(__file__).parent / "fixtures" / "anywidget-v1.json"


def _document() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text())


def _payload(document: dict[str, Any]) -> bytes:
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode()


def test_embedded_esm_ignores_dependency_text_outside_javascript_code() -> None:
    portable_assets.validate_embedded_esm(
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
    portable_assets.validate_embedded_esm(
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
        portable_assets.validate_embedded_esm('const value = `${await import("./child.js")}`;')


def test_embedded_esm_accepts_methods_named_import() -> None:
    portable_assets.validate_embedded_esm("const loader = { import() { return 1; } };")
    portable_assets.validate_embedded_esm("class Loader { import() { return 1; } }")


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
        'new URL("./asset.svg", import.meta.url, ignored);',
        "new URL(assetPath, import.meta.url);",
        "new URL(`https://cdn.example.test/${name}`, import.meta.url);",
        r'new U\u0052L("./asset.svg", import.meta.url);',
        r'import(".\x2fchild.js");',
    ],
)
def test_embedded_esm_rejects_nonportable_dependency_operands(source: str) -> None:
    with pytest.raises(ValueError, match=r"must use a literal data:|escaped identifiers"):
        portable_assets.validate_embedded_esm(source)


def test_css_ignores_comments_strings_fragments_and_absolute_paths() -> None:
    css = """
    /* url("./comment.svg"); @import "./comment.css"; */
    .label::before { content: "url('./content.svg')"; }
    .mask { mask: url(#mask); background: url(/host-asset.png); }
    .remote { background: URL("https://cdn.example.test/image.png"); }
    """

    assert portable_assets.portable_css(css) == css


def test_css_accepts_explicit_import_urls() -> None:
    css = """
    @import url("https://cdn.example.test/theme.css") screen;
    @import "data:text/css,.label%7Bcolor:green%7D";
    """

    assert portable_assets.portable_css(css) == css


@pytest.mark.parametrize(
    "css",
    [
        '.icon { background: url("./icon.svg"); }',
        '.icon { background: url("../icon.svg"); }',
        '.icon { background: url("icon.svg"); }',
        '.icon { background: url("//cdn.example.test/icon.svg"); }',
        '.icon { background: url("blob:https://example.test/icon"); }',
        r'.icon { background: u\72l("./icon.svg"); }',
        ".icon { background: url(); }",
        '@import "./theme.css";',
        "@import var(--theme);",
    ],
)
def test_css_rejects_unresolved_or_computed_assets(css: str) -> None:
    with pytest.raises(ValueError, match="AnyWidget CSS"):
        portable_assets.portable_css(css)


def test_css_inlines_marimo_virtual_file(monkeypatch: pytest.MonkeyPatch) -> None:
    content = b"<svg/>"

    def read(name: str, size: int) -> bytes:
        assert (name, size) == ("icon.svg", len(content))
        return content

    monkeypatch.setattr(portable_assets, "read_virtual_file", read)

    css = portable_assets.portable_css(
        f'.icon {{ background: url("./@file/{len(content)}-icon.svg"); }}'
    )

    assert f"data:image/svg+xml;base64,{base64.b64encode(content).decode()}" in css


def test_css_rejects_truncated_virtual_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portable_assets, "read_virtual_file", lambda _name, _size: b"x")

    with pytest.raises(ValueError, match="declared 4 bytes but returned 1"):
        portable_assets.portable_css('.icon { background: url("./@file/4-icon.svg"); }')


def test_css_rejects_virtual_stylesheet_import() -> None:
    with pytest.raises(ValueError, match="virtual files must be bundled"):
        portable_assets.portable_css('@import "./@file/12-theme.css";')


@pytest.mark.parametrize(
    "url",
    [
        "data:text/javascript,export%20default%20%7B%7D",
        "https://cdn.example.test/widget.js",
        "http://cdn.example.test/widget.js",
    ],
)
def test_anywidget_payload_accepts_self_contained_and_remote_modules(url: str) -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["esm_spec"]["url"] = url

    validation = validate_anywidget_payload(_payload(document))

    assert validation.root_model_id == "model-0"


@pytest.mark.parametrize(
    "url",
    [
        "./missing.js",
        "file:///tmp/widget.js",
        "blob:https://example.test/widget.js",
    ],
)
def test_anywidget_payload_rejects_unpublished_modules(url: str) -> None:
    document = _document()
    document["modelNotifications"][0]["message"]["esm_spec"]["url"] = url

    with pytest.raises(ValueError, match=r"missing virtual file|incompatible ESM URL protocol"):
        validate_anywidget_payload(_payload(document))


def test_anywidget_payload_requires_embedded_files_to_be_data_urls() -> None:
    document = _document()
    document["files"]["./@file/root.js"] = "https://cdn.example.test/root.js"

    with pytest.raises(ValueError, match="must contain a data URL"):
        validate_anywidget_payload(_payload(document))


def test_anywidget_payload_validation_does_not_mutate_bytes() -> None:
    payload = _payload(_document())
    before = bytes(payload)

    validate_anywidget_payload(payload)

    assert payload == before
