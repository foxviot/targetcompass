import json
from pathlib import Path

from .schema_validation import load_schema, validate_object
from .yamlmini import load_yaml


RESEARCH_SPEC_REQUIRED = [
    "project_id",
    "goal",
    "research_theme",
    "disease_scope",
    "organisms",
    "priority_tissues",
    "priority_cells",
    "target_routes",
    "modalities_mvp",
    "constraints",
]

DATASET_CARD_REQUIRED = [
    "dataset_id",
    "source",
    "accession",
    "modality",
    "organism",
    "tissue",
    "contrast",
    "sample_summary",
    "metadata_fields",
    "matrix_available",
    "license_status",
    "file_paths",
]


def _require(obj: dict, fields: list[str], label: str) -> list[str]:
    errors = []
    for field in fields:
        if field not in obj:
            errors.append(f"{label}.{field}: missing required field")
        elif obj[field] in (None, "", []):
            errors.append(f"{label}.{field}: must not be empty")
    return errors


def load_research_spec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_dataset_card(path: Path) -> dict:
    return load_yaml(path)


def validate_research_spec(path: Path) -> list[str]:
    spec = load_research_spec(path)
    errors = validate_object(spec, load_schema("research_spec.schema.json"), "ResearchSpec")
    return _dedupe_errors(errors)


def validate_dataset_card(path: Path) -> list[str]:
    card = load_dataset_card(path)
    errors = validate_object(card, load_schema("dataset_card.schema.json"), "DatasetCard")
    paths = card.get("file_paths", {})
    if card.get("modality") == "bulk_expression":
        if not paths.get("expression_matrix"):
            errors.append("DatasetCard.file_paths.expression_matrix: missing required field")
        if not paths.get("metadata"):
            errors.append("DatasetCard.file_paths.metadata: missing required field")
    return _dedupe_errors(errors)


def _dedupe_errors(errors: list[str]) -> list[str]:
    seen = set()
    out = []
    for err in errors:
        if err not in seen:
            out.append(err)
            seen.add(err)
    return out
