import csv
from pathlib import Path


GENE_ALIASES = ["gene_symbol", "symbol", "gene", "gene_name", "hgnc_symbol", "target", "target_symbol", "approved_symbol"]
ROUTE_ALIASES = ["route", "target_route", "location", "subcellular_location", "category", "class"]
EVIDENCE_TYPE_ALIASES = ["evidence_type", "type", "source_type"]
EFFECT_ALIASES = ["effect_size", "score", "logfc", "logFC", "beta", "odds_ratio"]
P_VALUE_ALIASES = ["p_value", "pvalue", "p", "adj_p_value", "fdr", "q_value"]


EVIDENCE_FIELDS = [
    "entity_symbol",
    "route",
    "evidence_type",
    "direction",
    "effect_size",
    "p_value",
    "quality_score",
    "source_dataset",
    "limitation",
]


def norm_key(value: str) -> str:
    return "".join(ch for ch in str(value).lower() if ch.isalnum())


def field(row: dict, aliases: list[str], default: str = "") -> str:
    lower = {norm_key(k): k for k in row}
    for alias in aliases:
        key = lower.get(norm_key(alias))
        if key is not None and row.get(key) not in (None, ""):
            return str(row[key]).strip()
    return default


def detect_field_mapping(columns: list[str]) -> dict[str, str]:
    sample = {column: "" for column in columns}
    mapping = {}
    for target, aliases in {
        "entity_symbol": GENE_ALIASES,
        "route": ROUTE_ALIASES,
        "evidence_type": EVIDENCE_TYPE_ALIASES,
        "effect_size": EFFECT_ALIASES,
        "p_value": P_VALUE_ALIASES,
        "direction": ["direction", "effect_direction", "association"],
        "quality_score": ["quality_score", "confidence", "confidence_score"],
    }.items():
        lower = {norm_key(k): k for k in sample}
        for alias in aliases:
            key = lower.get(norm_key(alias))
            if key is not None:
                mapping[target] = key
                break
    return mapping


def norm_route(value: str) -> str:
    text = (value or "").lower()
    if any(token in text for token in ["secret", "cytokine", "chemokine", "extracellular"]):
        return "secreted"
    if any(token in text for token in ["surface", "membrane", "cell membrane", "plasma membrane"]):
        return "surface"
    if "ecd" in text or "domain" in text:
        return "ECD"
    if "peptide" in text or "epitope" in text:
        return "T_cell_peptide"
    return value or "unknown"


def normalized_evidence_row(raw: dict, resource_id: str) -> dict:
    return {
        "entity_symbol": field(raw, GENE_ALIASES),
        "route": norm_route(field(raw, ROUTE_ALIASES, "")),
        "evidence_type": field(raw, EVIDENCE_TYPE_ALIASES, "external_database"),
        "direction": field(raw, ["direction", "effect_direction", "association"], ""),
        "effect_size": field(raw, EFFECT_ALIASES, ""),
        "p_value": field(raw, P_VALUE_ALIASES, ""),
        "quality_score": field(raw, ["quality_score", "confidence", "confidence_score"], "0.5"),
        "source_dataset": resource_id,
        "limitation": field(raw, ["limitation", "note", "description"], "external database; requires review"),
    }


def write_evidence(path: Path, rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=EVIDENCE_FIELDS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            if not row.get("entity_symbol"):
                continue
            writer.writerow({field_name: row.get(field_name, "") for field_name in EVIDENCE_FIELDS})
            count += 1
    return count
