import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def validate_object(obj: dict, schema: dict, label: str) -> list[str]:
    return _validate(obj, schema, label)


def _validate(value: Any, schema: dict, path: str) -> list[str]:
    errors = []
    expected_type = schema.get("type")
    if expected_type and not _is_type(value, expected_type):
        errors.append(f"{path}: expected {expected_type}")
        return errors

    if expected_type == "object":
        required = schema.get("required", [])
        for field in required:
            if not isinstance(value, dict) or field not in value:
                errors.append(f"{path}.{field}: missing required field")
            elif value[field] in (None, "", []):
                errors.append(f"{path}.{field}: must not be empty")
        properties = schema.get("properties", {})
        if isinstance(value, dict):
            for field, child_schema in properties.items():
                if field in value and value[field] not in (None, ""):
                    errors.extend(_validate(value[field], child_schema, f"{path}.{field}"))

    if expected_type == "array":
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            errors.append(f"{path}: must contain at least {schema['minItems']} item(s)")
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(value):
                errors.extend(_validate(item, item_schema, f"{path}[{idx}]"))

    if expected_type == "string":
        if "minLength" in schema and len(value) < int(schema["minLength"]):
            errors.append(f"{path}: must not be empty")
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: must be one of {', '.join(schema['enum'])}")

    if expected_type in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: must be >= {schema['minimum']}")

    return errors


def _is_type(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True
