from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
from pathlib import Path

import marimo_export._directory as directory_module
import marimo_export._directory_target as target_module
import pytest
from marimo_export.delivery import stage
from marimo_export.errors import NotebookExportError

_XATTRS_AVAILABLE = sys.platform == "darwin" or hasattr(os, "setxattr")


def _transaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    def exchange(first: Path, second: Path) -> bool:
        if mode == "fallback":
            return False
        temporary = tmp_path / "exchange"
        os.replace(first, temporary)
        os.replace(second, first)
        os.replace(temporary, second)
        return True

    monkeypatch.setattr(directory_module, "_exchange_directories", exchange)


def _xattr_name() -> str:
    return (
        "com.marimo-export.directory-identity"
        if sys.platform == "darwin"
        else "user.marimo_export_directory_identity"
    )


def _set_xattr(path: Path, value: bytes) -> None:
    if sys.platform == "darwin":
        subprocess.run(
            ["xattr", "-w", _xattr_name(), value.decode("ascii"), str(path)],
            check=True,
        )
        return
    set_attribute = getattr(os, "setxattr", None)
    if set_attribute is None:
        pytest.skip("extended attributes unavailable")
    try:
        set_attribute(path, _xattr_name(), value, follow_symlinks=False)
    except OSError as error:
        if error.errno in {
            errno.ENOSYS,
            getattr(errno, "ENOTSUP", errno.ENOSYS),
            getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
        }:
            pytest.skip("test filesystem has no extended attributes")
        raise


def _get_xattr(path: Path) -> bytes:
    if sys.platform == "darwin":
        return subprocess.check_output(
            ["xattr", "-p", _xattr_name(), str(path)],
        ).rstrip(b"\n")
    get_attribute = getattr(os, "getxattr", None)
    if get_attribute is None:
        pytest.skip("extended attributes unavailable")
    return get_attribute(path, _xattr_name(), follow_symlinks=False)


@pytest.mark.skipif(not _XATTRS_AVAILABLE, reason="extended attributes unavailable")
@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_directory_transaction_rejects_root_xattr_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    output = tmp_path / "site"
    output.mkdir()
    _transaction(monkeypatch, tmp_path, transaction)

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        _set_xattr(output, b"changed")
        with pytest.raises(NotebookExportError) as raised:
            staged.commit()

    assert raised.value.code == "destination_changed"
    assert _get_xattr(output) == b"changed"


@pytest.mark.skipif(not _XATTRS_AVAILABLE, reason="extended attributes unavailable")
@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_directory_transaction_preserves_preexisting_root_xattrs_across_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    output = tmp_path / "site"
    output.mkdir()
    _set_xattr(output, b"stable")
    _transaction(monkeypatch, tmp_path, transaction)

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        staged.commit()

    assert output.joinpath("index.html").read_text(encoding="utf-8") == "new"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS extended ACL contract")
@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_directory_transaction_rejects_root_acl_drift_on_macos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    output = tmp_path / "site"
    output.mkdir()
    _transaction(monkeypatch, tmp_path, transaction)

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        subprocess.run(
            ["chmod", "+a", "everyone allow readattr", str(output)],
            check=True,
        )
        with pytest.raises(NotebookExportError) as raised:
            staged.commit()

    assert raised.value.code == "destination_changed"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS extended ACL contract")
@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_directory_transaction_preserves_preexisting_acl_across_move_on_macos(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    output = tmp_path / "site"
    output.mkdir()
    subprocess.run(
        ["chmod", "+a", "everyone allow readattr", str(output)],
        check=True,
    )
    _transaction(monkeypatch, tmp_path, transaction)

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        staged.commit()

    assert output.joinpath("index.html").read_text(encoding="utf-8") == "new"


@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_directory_transaction_rejects_root_flag_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    change_flags = getattr(os, "chflags", None)
    no_dump = getattr(stat, "UF_NODUMP", None)
    if not callable(change_flags) or not isinstance(no_dump, int):
        pytest.skip("BSD root flags unavailable")

    output = tmp_path / "site"
    output.mkdir()
    _transaction(monkeypatch, tmp_path, transaction)
    original_flags = getattr(output.stat(), "st_flags", None)
    if not isinstance(original_flags, int):
        pytest.skip("BSD root flags unavailable")

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        change_flags(output, original_flags ^ no_dump)
        with pytest.raises(NotebookExportError) as raised:
            staged.commit()

    assert raised.value.code == "destination_changed"
    change_flags(output, original_flags)


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL contract")
@pytest.mark.parametrize("transaction", ["exchange", "fallback"])
def test_directory_transaction_rejects_root_dacl_drift_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transaction: str,
) -> None:
    output = tmp_path / "site"
    output.mkdir()
    _transaction(monkeypatch, tmp_path, transaction)

    with stage(output, replace=True) as staged:
        staged.path.joinpath("index.html").write_text("new", encoding="utf-8")
        subprocess.run(
            ["icacls", str(output), "/inheritance:d"],
            check=True,
            capture_output=True,
            text=True,
        )
        with pytest.raises(NotebookExportError) as raised:
            staged.commit()

    assert raised.value.code == "destination_changed"


def test_directory_security_inspection_failure_is_not_a_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "site"
    output.mkdir()

    def unavailable(_path: Path, _metadata: os.stat_result):
        raise PermissionError("security metadata unavailable")

    monkeypatch.setattr(target_module, "directory_security_identity", unavailable)
    with pytest.raises(NotebookExportError) as raised:
        stage(output, replace=True)

    assert raised.value.code == "export_commit_failed"
