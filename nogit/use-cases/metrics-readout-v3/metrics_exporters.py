from __future__ import annotations

import base64
import json
import math
from typing import Any

READOUT_SCHEMA = "metrics.readout.v1"
READOUT_MEDIA_TYPE = "application/vnd.marimo.metrics-readout+json"


def readout(value: Any, ctx: Any, **options: Any) -> Any:
    """Export selected report cells as one typed metrics artifact."""

    title = str(options.get("title") or "Metrics Readout")
    items = options.get("items")
    if not isinstance(items, list):
        raise TypeError("readout exporter requires an items list")

    cells = {cell.id: cell for cell in value.cells}
    records = [
        _read_item(cells, item)
        for item in items
        if isinstance(item, dict) and item.get("cell_id")
    ]
    payload = {
        "schema": READOUT_SCHEMA,
        "version": 1,
        "title": title,
        "notebook": _notebook_metadata(value.notebook),
        "items": records,
    }
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    blob = ctx.write_blob(
        "metrics-readout.json",
        encoded,
        media_type=READOUT_MEDIA_TYPE,
    )
    return ctx.artifact(
        format_id=READOUT_SCHEMA,
        media_type=READOUT_MEDIA_TYPE,
        files={"readout": blob},
        entry="readout",
        metadata={
            "kind": "metrics-readout",
            "item_count": len(records),
            "error_count": sum(1 for record in records if record["status"] == "error"),
        },
    )


def _notebook_metadata(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    metadata: dict[str, str] = {}
    for key in ("name", "sha256"):
        item = value.get(key)
        if isinstance(item, str):
            metadata[key] = item
    return metadata


def _read_item(cells: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    cell_id = str(item["cell_id"])
    record: dict[str, Any] = {
        "order": int(item.get("order", 0)),
        "label": str(item.get("label") or cell_id),
        "cell_id": cell_id,
    }
    cell = cells.get(cell_id)
    if cell is None:
        record.update(
            {
                "status": "error",
                "error": {
                    "type": "MissingCell",
                    "message": f"cell id {cell_id!r} was not captured",
                },
            }
        )
        return record

    cell_record = cell.to_json()
    record["cell"] = {
        "id": cell_record["id"],
        "index": cell_record["index"],
        "name": cell_record.get("name"),
    }
    outputs = cell_record.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        record["status"] = "empty"
        return record

    output = outputs[0]
    if not isinstance(output, dict):
        record.update(
            {
                "status": "error",
                "error": {
                    "type": "InvalidOutput",
                    "message": "captured output is not a JSON object",
                },
            }
        )
        return record

    if output.get("channel") == "error":
        error = output.get("data")
        record.update(
            {
                "status": "error",
                "error": error if isinstance(error, dict) else {"message": str(error)},
            }
        )
        return record

    record.update(
        {
            "status": "ok",
            "mimetype": str(output.get("mimetype") or ""),
            "data": _json_ready(output.get("data")),
        }
    )
    if output.get("traceback") is not None:
        record["traceback"] = _json_ready(output.get("traceback"))
    return record


def _json_ready(value: Any) -> Any:
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return str(value)
