import csv
import json
import shutil
from pathlib import Path

from .db_adapters.contracts import DatabaseAdapterContext
from .db_adapters.registry import adapt_database


RESOURCE_TYPES = {"dataset_card", "annotation_table", "gene_set", "literature_card", "external_database"}

GENE_ALIASES = ["gene_symbol", "symbol", "gene", "gene_name", "hgnc_symbol", "target", "target_symbol", "approved_symbol"]
ROUTE_ALIASES = ["route", "target_route", "location", "subcellular_location", "category", "class"]
ACCESS_ALIASES = ["accessibility_status", "accessibility", "status", "supported", "is_accessible"]
SAFETY_ALIASES = ["safety_gate", "safety", "safety_status", "risk_status", "toxicity_flag"]
TERM_ID_ALIASES = ["term_id", "pathway_id", "set_id", "geneset_id", "id"]
TERM_NAME_ALIASES = ["term_name", "pathway_name", "set_name", "name", "description"]
GENES_ALIASES = ["genes", "gene_symbols", "members", "member_symbols", "gene_list"]
EVIDENCE_TYPE_ALIASES = ["evidence_type", "type", "source_type"]
EFFECT_ALIASES = ["effect_size", "score", "logfc", "logFC", "beta", "odds_ratio"]
P_VALUE_ALIASES = ["p_value", "pvalue", "p", "adj_p_value", "fdr", "q_value"]


def _registry_path(project_dir: Path) -> Path:
    return project_dir / "configs" / "knowledge_registry.json"


def load_registry(project_dir: Path) -> list[dict]:
    path = _registry_path(project_dir)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save_registry(project_dir: Path, resources: list[dict]) -> None:
    path = _registry_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(resources, indent=2, ensure_ascii=False), encoding="utf-8")


def add_resource(project_dir: Path, resource_id: str, resource_type: str, source_path: str, adapter: str = "copy") -> dict:
    resource_id = resource_id.strip()
    resource_type = resource_type.strip()
    source = Path(source_path.strip())
    if not resource_id:
        raise ValueError("resource_id is required")
    if resource_type not in RESOURCE_TYPES:
        raise ValueError(f"unsupported resource_type: {resource_type}")
    if not source.exists():
        raise ValueError(f"source_path does not exist: {source}")
    resources = [row for row in load_registry(project_dir) if row.get("resource_id") != resource_id]
    entry = {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "source_path": str(source),
        "adapter": adapter or "copy",
        "status": "registered",
    }
    resources.append(entry)
    save_registry(project_dir, resources)
    return entry


def remove_resource(project_dir: Path, resource_id: str) -> bool:
    resources = load_registry(project_dir)
    kept = [row for row in resources if row.get("resource_id") != resource_id]
    save_registry(project_dir, kept)
    return len(kept) != len(resources)


def adapt_resources(project_dir: Path) -> list[dict]:
    resources = load_registry(project_dir)
    adapted = []
    for row in resources:
        source = Path(row["source_path"])
        status = "adapted"
        destination = ""
        try:
            if row["resource_type"] == "dataset_card":
                destination = str(project_dir / "dataset_cards" / source.name)
                shutil.copy2(source, destination)
            elif row["resource_type"] == "literature_card":
                destination = str(project_dir / "literature_cards" / source.name)
                shutil.copy2(source, destination)
            elif row["resource_type"] == "gene_set":
                destination = str(project_dir / "knowledge_imports" / "gene_sets" / source.name)
                Path(destination).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            elif row["resource_type"] == "annotation_table":
                destination = str(project_dir / "knowledge_imports" / "annotation_tables" / source.name)
                Path(destination).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            else:
                destination = row["source_path"]
                status = "registered_external"
            normalized = normalize_resource(project_dir, {**row, "adapted_path": destination})
        except Exception as exc:
            status = f"failed: {exc}"
            normalized = {}
        adapted.append({**row, "status": status, "adapted_path": destination, **normalized})
    save_registry(project_dir, adapted)
    return adapted


def normalize_resource(project_dir: Path, row: dict) -> dict:
    source = Path(row.get("adapted_path") or row["source_path"])
    if row["resource_type"] == "annotation_table":
        return _normalize_annotation(project_dir, source, row)
    if row["resource_type"] == "gene_set":
        return _normalize_gene_set(project_dir, source, row)
    if row["resource_type"] == "external_database":
        return _normalize_external_database(project_dir, source, row)
    return {}


def _dialect(path: Path) -> str:
    if path.suffix.lower() == ".csv":
        return ","
    return "\t"


def _read_table(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = "," if sample.count(",") > sample.count("\t") else _dialect(path)
        return list(csv.DictReader(f, delimiter=delimiter))


def _field(row: dict, aliases: list[str], default: str = "") -> str:
    lower = {_norm_key(str(k)): k for k in row}
    for alias in aliases:
        key = lower.get(_norm_key(alias))
        if key is not None and row.get(key) not in (None, ""):
            return str(row[key]).strip()
    return default


def _norm_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _norm_route(value: str) -> str:
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


def _normalize_annotation(project_dir: Path, source: Path, resource: dict) -> dict:
    rows = _read_table(source)
    out_dir = project_dir / "knowledge_imports" / "normalized"
    out_dir.mkdir(parents=True, exist_ok=True)
    access_path = out_dir / f"{resource['resource_id']}_accessibility.tsv"
    safety_path = out_dir / f"{resource['resource_id']}_safety.tsv"
    access_n = 0
    safety_n = 0
    with access_path.open("w", newline="", encoding="utf-8") as access_f, safety_path.open(
        "w", newline="", encoding="utf-8"
    ) as safety_f:
        access_fields = ["gene_symbol", "route", "accessibility_status", "source"]
        safety_fields = ["gene_symbol", "safety_gate", "critical_tissue_flag", "note"]
        access_writer = csv.DictWriter(access_f, fieldnames=access_fields, delimiter="\t")
        safety_writer = csv.DictWriter(safety_f, fieldnames=safety_fields, delimiter="\t")
        access_writer.writeheader()
        safety_writer.writeheader()
        for raw in rows:
            gene = _field(raw, GENE_ALIASES)
            if not gene:
                continue
            route = _norm_route(_field(raw, ROUTE_ALIASES, "unknown"))
            accessibility = _field(raw, ACCESS_ALIASES, "SUPPORTED" if route != "unknown" else "UNKNOWN")
            safety = _field(raw, SAFETY_ALIASES, "")
            if route != "unknown" or accessibility:
                access_writer.writerow(
                    {
                        "gene_symbol": gene,
                        "route": route,
                        "accessibility_status": _norm_status(accessibility, supported="SUPPORTED"),
                        "source": resource["resource_id"],
                    }
                )
                access_n += 1
            if safety:
                safety_writer.writerow(
                    {
                        "gene_symbol": gene,
                        "safety_gate": _norm_safety(safety),
                        "critical_tissue_flag": _field(raw, ["critical_tissue_flag", "tissue_flag", "tissue"], ""),
                        "note": _field(raw, ["note", "notes", "comment", "description"], resource["resource_id"]),
                    }
                )
                safety_n += 1
    return {
        "normalized_accessibility": str(access_path),
        "normalized_safety": str(safety_path),
        "normalized_rows": access_n + safety_n,
    }


def _normalize_gene_set(project_dir: Path, source: Path, resource: dict) -> dict:
    rows = _read_table(source)
    out_dir = project_dir / "knowledge_imports" / "normalized"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{resource['resource_id']}_gene_sets.tsv"
    count = 0
    with out_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["term_id", "term_name", "genes", "source"]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for idx, raw in enumerate(rows, 1):
            genes = _field(raw, GENES_ALIASES)
            if not genes:
                gene = _field(raw, GENE_ALIASES)
                term = _field(raw, TERM_ID_ALIASES + TERM_NAME_ALIASES, resource["resource_id"])
                genes = gene
            else:
                term = _field(raw, TERM_ID_ALIASES + TERM_NAME_ALIASES, f"{resource['resource_id']}_{idx}")
            if not genes:
                continue
            term_id = _field(raw, TERM_ID_ALIASES, term).replace(" ", "_")
            writer.writerow(
                {
                    "term_id": term_id,
                    "term_name": _field(raw, TERM_NAME_ALIASES, term),
                    "genes": _join_genes(genes),
                    "source": resource["resource_id"],
                }
            )
            count += 1
    return {"normalized_gene_sets": str(out_path), "normalized_rows": count}


def _normalize_external_database(project_dir: Path, source: Path, resource: dict) -> dict:
    result = adapt_database(
        DatabaseAdapterContext(
            project_dir=project_dir,
            resource_id=resource["resource_id"],
            source_path=source,
            adapter=resource.get("adapter", "auto"),
        )
    )
    out = {
        "database_adapter": result.adapter_id,
        "adapter_message": result.message,
        "normalized_rows": result.row_count,
        "input_rows": result.input_rows,
        "dropped_rows": result.dropped_rows,
        "field_mapping": result.field_mapping or {},
    }
    for key, value in (result.normalized_outputs or {}).items():
        out[f"normalized_{key}"] = value
    if result.normalized_evidence:
        out["normalized_evidence"] = str(result.normalized_evidence)
    return out


def _norm_status(value: str, supported: str) -> str:
    text = str(value or "").lower()
    if text in {"1", "true", "yes", "y", "supported", "pass", "accessible"}:
        return supported
    if text in {"0", "false", "no", "n", "unsupported"}:
        return "UNKNOWN"
    return str(value or "UNKNOWN").upper()


def _norm_safety(value: str) -> str:
    text = str(value or "").lower()
    if text in {"pass", "safe", "low", "0", "false", "no"}:
        return "PASS"
    if text in {"fail", "unsafe", "high", "blocked"}:
        return "FAIL"
    return "REVIEW_REQUIRED"


def _join_genes(value: str) -> str:
    separators = [";", "|", " "]
    genes = str(value or "")
    for sep in separators:
        genes = genes.replace(sep, ",")
    return ",".join(gene.strip() for gene in genes.split(",") if gene.strip())
