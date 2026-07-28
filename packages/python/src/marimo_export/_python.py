from __future__ import annotations

import keyword

from marimo_export._json import json_string


def validate_import_reference(value: object, path: str) -> str:
    reference = json_string(value, path)
    if reference.count(":") != 1:
        raise ValueError(f"{path} must use module:object syntax")
    module, object_name = reference.split(":")
    parts = (*module.split("."), *object_name.split("."))
    if any(not part.isidentifier() or keyword.iskeyword(part) for part in parts):
        raise ValueError(f"{path} must use module:object syntax")
    return reference
