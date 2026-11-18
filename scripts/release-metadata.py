"""Read coordinated release metadata; --sync updates version mirrors."""

import json
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
version = tomllib.loads(Path("packages/python/pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]
path = Path("packages/browser/package.json")
data = json.loads(path.read_text(encoding="utf-8"))
if "--sync" in sys.argv:
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
elif data["version"] != version:
    raise ValueError(f"Version mismatch in {path}")
print(json.dumps({"version": version}))
