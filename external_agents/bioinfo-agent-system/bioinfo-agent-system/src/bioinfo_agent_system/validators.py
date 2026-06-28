from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SUPPORTED_SCHEMA_KEYS = {
    "$schema",
    "title",
    "description",
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "const",
    "additionalProperties",
    "minItems",
    "maxItems",
    "minLength",
    "minimum",
    "maximum",
}


class ValidationError(ValueError):
    pass


def load_json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Invalid JSON in {path}: {exc}") from exc


def validate_schema_file(schema_path: Path) -> dict[str, Any]:
    schema = load_json_file(schema_path)
    _validate_schema_structure(schema, schema_path.as_posix())
    return schema


def validate_data_file(data_path: Path, schema_path: Path) -> Any:
    schema = validate_schema_file(schema_path)
    data = load_json_file(data_path)
    validate_data_against_schema(data, schema, data_path.as_posix())
    return data


def validate_data_against_schema(
    value: Any, schema: dict[str, Any], context: str = "$"
) -> None:
    if "enum" in schema and value not in schema["enum"]:
        raise ValidationError(f"{context}: {value!r} is not in enum {schema['enum']!r}")
    if "const" in schema and value != schema["const"]:
        raise ValidationError(f"{context}: {value!r} does not equal const {schema['const']!r}")

    schema_type = schema.get("type")
    if schema_type is not None and not _matches_type(value, schema_type):
        raise ValidationError(
            f"{context}: expected type {schema_type!r}, got {type(value).__name__}"
        )

    if schema_type == "object":
        _validate_object(value, schema, context)
    elif schema_type == "array":
        _validate_array(value, schema, context)
    elif schema_type == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValidationError(
                f"{context}: string length {len(value)} is below {schema['minLength']}"
            )
    elif schema_type == "integer":
        _validate_numeric(value, schema, context)
    elif schema_type == "number":
        _validate_numeric(value, schema, context)


def validate_schema_catalog(schema_paths: list[Path]) -> None:
    for schema_path in schema_paths:
        validate_schema_file(schema_path)


def _validate_schema_structure(schema: Any, context: str) -> None:
    if not isinstance(schema, dict):
        raise ValidationError(f"{context}: schema must be a JSON object")

    unsupported_keys = set(schema) - SUPPORTED_SCHEMA_KEYS
    if unsupported_keys:
        raise ValidationError(
            f"{context}: unsupported schema keys {sorted(unsupported_keys)!r}"
        )

    if "type" in schema and schema["type"] not in {
        "object",
        "array",
        "string",
        "integer",
        "number",
        "boolean",
        "null",
    }:
        raise ValidationError(f"{context}: unsupported schema type {schema['type']!r}")

    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValidationError(f"{context}: properties must be an object")
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise ValidationError(f"{context}: required must be a list of strings")
        for key in required:
            if key not in properties:
                raise ValidationError(f"{context}: required key {key!r} missing from properties")
        for key, child in properties.items():
            _validate_schema_structure(child, f"{context}.properties.{key}")

    if schema.get("type") == "array" and "items" in schema:
        _validate_schema_structure(schema["items"], f"{context}.items")

    if "enum" in schema:
        if not isinstance(schema["enum"], list) or not schema["enum"]:
            raise ValidationError(f"{context}: enum must be a non-empty list")

    if "minItems" in schema and (
        not isinstance(schema["minItems"], int) or schema["minItems"] < 0
    ):
        raise ValidationError(f"{context}: minItems must be a non-negative integer")

    if "minLength" in schema and (
        not isinstance(schema["minLength"], int) or schema["minLength"] < 0
    ):
        raise ValidationError(f"{context}: minLength must be a non-negative integer")


def _matches_type(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(
            value, bool
        )
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return False


def _validate_object(value: dict[str, Any], schema: dict[str, Any], context: str) -> None:
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for key in required:
        if key not in value:
            raise ValidationError(f"{context}: missing required key {key!r}")

    additional_properties = schema.get("additionalProperties", True)
    for key, child_value in value.items():
        if key in properties:
            validate_data_against_schema(child_value, properties[key], f"{context}.{key}")
            continue
        if additional_properties is False:
            raise ValidationError(f"{context}: unexpected key {key!r}")
        if isinstance(additional_properties, dict):
            validate_data_against_schema(
                child_value, additional_properties, f"{context}.{key}"
            )


def _validate_array(value: list[Any], schema: dict[str, Any], context: str) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        raise ValidationError(
            f"{context}: array length {len(value)} is below {schema['minItems']}"
        )
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        raise ValidationError(
            f"{context}: array length {len(value)} exceeds {schema['maxItems']}"
        )
    item_schema = schema.get("items")
    if item_schema is None:
        return
    for index, item in enumerate(value):
        validate_data_against_schema(item, item_schema, f"{context}[{index}]")


def _validate_numeric(value: int | float, schema: dict[str, Any], context: str) -> None:
    if "minimum" in schema and value < schema["minimum"]:
        raise ValidationError(f"{context}: {value} is below {schema['minimum']}")
    if "maximum" in schema and value > schema["maximum"]:
        raise ValidationError(f"{context}: {value} exceeds {schema['maximum']}")
