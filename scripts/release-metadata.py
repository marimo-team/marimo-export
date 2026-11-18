"""Read coordinated release metadata; --sync updates version mirrors."""

import json
import subprocess
import sys
from pathlib import Path

version = subprocess.check_output(
    ["uv", "version", "--package", "marimo-export", "--frozen", "--short"], text=True
).strip()
path = Path("packages/browser/package.json")
data = json.loads(path.read_text(encoding="utf-8"))
if "--sync" in sys.argv:
    data["version"] = version
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
elif data["version"] != version:
    raise ValueError(f"Version mismatch in {path}")
print(json.dumps({"version": version}))
