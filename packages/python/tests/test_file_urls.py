from __future__ import annotations

import os
from pathlib import Path

import pytest
from marimo._runtime.virtual_file import EMPTY_VIRTUAL_FILE, VirtualFile
from marimo_export._marimo.file_urls import PublicFileUrl, VirtualFilePrefix, VirtualFileUrl

RELATIVE = (VirtualFilePrefix.RELATIVE,)
EVERY_PREFIX = (VirtualFilePrefix.RELATIVE, VirtualFilePrefix.ROOT, VirtualFilePrefix.BARE)


def test_virtual_file_url_parses_marimo_file_names() -> None:
    assert VirtualFileUrl.parse("./@file/29676-test.png", RELATIVE) == VirtualFileUrl(
        29676, "test.png"
    )
    assert VirtualFileUrl.parse("./@file/29676-25241121-ZSE6dgpj.png", RELATIVE) == (
        VirtualFileUrl(29676, "25241121-ZSE6dgpj.png")
    )


def test_virtual_file_url_parses_the_urls_marimo_writes() -> None:
    registered = VirtualFile("12345-AbCdEfGh.png", b"abc")

    assert VirtualFileUrl.parse(registered.url, RELATIVE) == VirtualFileUrl(3, "12345-AbCdEfGh.png")
    assert VirtualFileUrl.parse(EMPTY_VIRTUAL_FILE.url, (VirtualFilePrefix.BARE,)) == (
        VirtualFileUrl(0, "empty.txt")
    )


def test_virtual_file_url_formats_the_relative_marimo_url() -> None:
    assert VirtualFileUrl(29676, "test.png").url == "./@file/29676-test.png"


@pytest.mark.parametrize(
    ("url", "accepted"),
    [
        ("./@file/3-a.js", RELATIVE),
        ("/@file/3-a.js", (VirtualFilePrefix.ROOT,)),
        ("@file/3-a.js", (VirtualFilePrefix.BARE,)),
        ("./@file/3-a.js", EVERY_PREFIX),
        ("/@file/3-a.js", EVERY_PREFIX),
        ("@file/3-a.js", EVERY_PREFIX),
    ],
)
def test_virtual_file_url_accepts_each_listed_prefix(
    url: str,
    accepted: tuple[VirtualFilePrefix, ...],
) -> None:
    assert VirtualFileUrl.parse(url, accepted) == VirtualFileUrl(3, "a.js")


@pytest.mark.parametrize("url", ["/@file/3-a.js", "@file/3-a.js", "../@file/3-a.js"])
def test_relative_prefix_rejects_other_virtual_file_forms(url: str) -> None:
    assert VirtualFileUrl.parse(url, RELATIVE) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/image.png",
        "./regular-file.png",
        "./@file/test.png",
        "./@file/-test.png",
        "./@file/12-",
        "./@file/12-line\nbreak.png",
        "./@file/12-a.png\n",
        f"./@file/{'9' * 21}-big.png",
    ],
)
def test_virtual_file_url_rejects_malformed_urls(url: str) -> None:
    assert VirtualFileUrl.parse(url, EVERY_PREFIX) is None


@pytest.mark.parametrize(
    ("url", "path"),
    [
        ("public/image.png", "image.png"),
        ("./public/image.png", "image.png"),
        ("public/assets/logo.svg", "assets/logo.svg"),
    ],
)
def test_public_file_url_parses_notebook_public_paths(url: str, path: str) -> None:
    assert PublicFileUrl.parse(url) == PublicFileUrl(path)


@pytest.mark.parametrize("url", ["/public/image.png", "public/", "publicity/image.png"])
def test_public_file_url_rejects_other_paths(url: str) -> None:
    assert PublicFileUrl.parse(url) is None


def test_public_file_resolves_nested_files(tmp_path: Path) -> None:
    nested = tmp_path / "public" / "assets" / "logo.svg"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"<svg/>")

    assert PublicFileUrl("assets/logo.svg").resolve(tmp_path) == nested.resolve()


@pytest.mark.parametrize("path", ["../secret.txt", "sub/../../secret.txt"])
def test_public_file_rejects_traversal_outside_public(tmp_path: Path, path: str) -> None:
    (tmp_path / "public" / "sub").mkdir(parents=True)
    (tmp_path / "secret.txt").write_bytes(b"shh")

    assert PublicFileUrl(path).resolve(tmp_path) is None


def test_public_file_resolves_traversal_that_stays_inside_public(tmp_path: Path) -> None:
    (tmp_path / "public" / "sub").mkdir(parents=True)
    image = tmp_path / "public" / "image.png"
    image.write_bytes(b"img")

    assert PublicFileUrl("sub/../image.png").resolve(tmp_path) == image.resolve()


def test_public_file_rejects_anchored_paths(tmp_path: Path) -> None:
    (tmp_path / "public").mkdir()
    inside = tmp_path / "public" / "image.png"
    inside.write_bytes(b"img")

    assert PublicFileUrl(str(inside)).resolve(tmp_path) is None
    assert PublicFileUrl("//host/share/image.png").resolve(tmp_path) is None


def test_public_file_rejects_directories_and_missing_files(tmp_path: Path) -> None:
    (tmp_path / "public").mkdir()

    assert PublicFileUrl(".").resolve(tmp_path) is None
    assert PublicFileUrl("missing.png").resolve(tmp_path) is None
    assert PublicFileUrl("image.png").resolve(tmp_path / "elsewhere") is None


def test_public_file_rejects_a_symlink_that_escapes(tmp_path: Path) -> None:
    public = tmp_path / "public"
    public.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"shh")
    _symlink(secret, public / "escape.txt")

    assert PublicFileUrl("escape.txt").resolve(tmp_path) is None


def test_public_file_follows_a_symlink_inside_public(tmp_path: Path) -> None:
    public = tmp_path / "public"
    public.mkdir()
    real = public / "real.png"
    real.write_bytes(b"img")
    _symlink(real, public / "link.png")

    assert PublicFileUrl("link.png").resolve(tmp_path) == real.resolve()


def test_public_file_rejects_a_symlink_loop(tmp_path: Path) -> None:
    public = tmp_path / "public"
    public.mkdir()
    _symlink(public / "b", public / "a")
    _symlink(public / "a", public / "b")

    assert PublicFileUrl("a").resolve(tmp_path) is None


def _symlink(target: Path, link: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
