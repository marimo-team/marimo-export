from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import marimo_export._identity as identity
import pytest


def test_implementation_identity_requires_restart_after_source_edits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "marimo_export"
    package.mkdir()
    identity_file = package / "_identity.py"
    identity_file.write_text("identity = 1\n", encoding="utf-8")
    source = package / "module.py"
    source.write_text("value = 1\n", encoding="utf-8")
    monkeypatch.setattr(identity, "__file__", str(identity_file))
    loaded = identity._compute_implementation_identity(package)
    monkeypatch.setattr(identity, "_LOADED_IMPLEMENTATION_IDENTITY", loaded)

    assert identity.implementation_identity() == loaded
    assert identity.require_implementation_stable() == loaded
    source.write_text("value = 2\n", encoding="utf-8")

    assert identity.implementation_identity() == loaded
    with pytest.raises(identity.ImplementationDriftError, match="restart") as raised:
        identity.require_implementation_stable()
    assert raised.value.loaded == loaded
    assert raised.value.current != loaded


def test_package_root_freezes_identity_before_lazy_module_imports(tmp_path: Path) -> None:
    package = tmp_path / "marimo_export"
    shutil.copytree(
        Path(identity.__file__).parent,
        package,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    script = """
import json
from pathlib import Path
import marimo_export.spec
from marimo_export._identity import implementation_identity, require_implementation_stable

loaded = implementation_identity()
client = Path(marimo_export.spec.__file__).with_name("client.py")
client.write_text(client.read_text() + "\\n# edited after spec import\\n")
import marimo_export.client
try:
    require_implementation_stable()
except RuntimeError as error:
    print(json.dumps({"loaded": loaded, "after": implementation_identity(), "error": str(error)}))
else:
    raise AssertionError("source drift was accepted")
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(tmp_path)

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=environment,
    )
    result = json.loads(completed.stdout)

    assert result["after"] == result["loaded"]
    assert "restart" in result["error"]
