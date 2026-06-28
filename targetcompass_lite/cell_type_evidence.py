import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .knowledge import load_registry
from .v4 import content_hash


CELL_TYPE_SCHEMA = "v4.cell_type_evidence/0.1"

GENE_ALIASES = ["gene_symbol", "entity_symbol", "gene", "symbol", "official gene symbol", "marker", "markers", "hgnc_symbol"]
CELL_ALIASES = ["cell_type", "cell type", "cellName", "cell_name", "cell", "celltype", "cell types"]
TISSUE_ALIASES = ["tissue", "organ", "organism part", "anatomical entity", "sample", "location"]
SPECIES_ALIASES = ["species", "organism"]
CONFIDENCE_ALIASES = ["confidence", "score", "specificity", "level", "evidence_score"]
SOURCE_ALIASES = ["source", "database", "resource", "source_dataset"]
CONTEXT_ALIASES = ["sentence_or_context", "context", "note", "description", "evidence", "result_sentence"]


def build_cell_type_evidence(project_dir: Path) -> dict[str, Any]:
    out_dir = project_dir / "results" / "cell_type_evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    rows.extend(_hpa_rows(project_dir))
    rows.extend(_marker_resource_rows(project_dir))
    rows.extend(_scrna_rows(project_dir))
    rows.extend(_fulltext_llm_rows(project_dir))
    rows = _dedupe(rows)
    evidence_path = out_dir / "cell_type_evidence.tsv"
    _write_tsv(evidence_path, _fields(), rows)
    summary = _summary(project_dir, rows, evidence_path)
    summary_path = out_dir / "cell_type_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [evidence_path, summary_path],
            producer="cell_type_evidence",
            artifact_type="cell_type_evidence_output",
            task_id="cell_type_evidence",
        )
    except Exception:
        pass
    return summary


def _hpa_rows(project_dir: Path) -> list[dict[str, Any]]:
    rows = []
    path = project_dir / "results" / "database_validation" / "hpa.tsv"
    if not path.exists():
        return rows
    for raw in _read_table(path):
        gene = _field(raw, GENE_ALIASES + ["Gene"]).upper()
        if not gene:
            continue
        tissue_specificity = _field(raw, ["RNA tissue specificity", "Tissue specificity", "RNA specificity"])
        context = "; ".join(item for item in [_field(raw, ["Subcellular location"]), tissue_specificity] if item)
        cell_types = _extract_cell_types_from_text(context)
        if not cell_types:
            cell_types = ["not cell-type resolved"]
        for cell_type in cell_types:
            rows.append(
                _row(
                    project_dir,
                    gene,
                    cell_type,
                    tissue=tissue_specificity,
                    evidence_source="HPA",
                    evidence_type="hpa_cell_type_context",
                    evidence_level="L2_database",
                    confidence=0.45 if cell_type == "not cell-type resolved" else 0.62,
                    artifact_path=path,
                    context=context or "HPA row available; no explicit cell type field in downloaded record.",
                    limitation="HPA search output may describe tissue/subcellular context rather than single-cell-resolved expression.",
                )
            )
    return rows


def _marker_resource_rows(project_dir: Path) -> list[dict[str, Any]]:
    out = []
    for resource in load_registry(project_dir):
        name = " ".join(str(resource.get(key, "")) for key in ["resource_id", "adapter", "source_path"]).lower()
        if not any(token in name for token in ["panglao", "cellmarker", "cell_marker", "marker"]):
            continue
        source = Path(resource.get("adapted_path") or resource.get("source_path", ""))
        if not source.exists() or source.suffix.lower() not in {".tsv", ".csv", ".txt"}:
            continue
        source_name = "PanglaoDB" if "panglao" in name else "CellMarker" if "cellmarker" in name or "cell_marker" in name else resource.get("resource_id", "marker_database")
        for raw in _read_table(source):
            genes = _split_genes(_field(raw, GENE_ALIASES))
            cell_type = _field(raw, CELL_ALIASES)
            if not genes or not cell_type:
                continue
            tissue = _field(raw, TISSUE_ALIASES)
            context = _field(raw, CONTEXT_ALIASES, f"{source_name} marker record")
            confidence = _confidence(_field(raw, CONFIDENCE_ALIASES), default=0.7)
            for gene in genes:
                out.append(
                    _row(
                        project_dir,
                        gene.upper(),
                        cell_type,
                        tissue=tissue,
                        evidence_source=str(source_name),
                        evidence_type="marker_database_cell_type",
                        evidence_level="L2_database",
                        confidence=confidence,
                        artifact_path=source,
                        context=context,
                        limitation="Marker database evidence indicates cell identity association, not disease-specific expression change.",
                    )
                )
    return out


def _scrna_rows(project_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for manifest_path in sorted((project_dir / "results").glob("scrna_pseudobulk_*/run_manifest.json")):
        manifest = _read_json(manifest_path, {})
        metadata_rel = manifest.get("outputs", {}).get("metadata", "")
        matrix_rel = manifest.get("outputs", {}).get("matrix", "")
        metadata_path = project_dir / metadata_rel if metadata_rel else manifest_path.parent / "pseudobulk_metadata.tsv"
        matrix_path = project_dir / matrix_rel if matrix_rel else manifest_path.parent / "pseudobulk_matrix.tsv"
        cell_types = sorted({row.get("cell_type", "") for row in _read_table(metadata_path) if row.get("cell_type", "")})
        genes = _matrix_genes(matrix_path)
        for cell_type in cell_types:
            for gene in genes[:5000]:
                rows.append(
                    _row(
                        project_dir,
                        gene.upper(),
                        cell_type,
                        tissue=manifest.get("parameters", {}).get("tissue", ""),
                        evidence_source="scRNA pseudobulk",
                        evidence_type="scrna_pseudobulk_cell_type",
                        evidence_level="L3_omics",
                        confidence=0.8,
                        artifact_path=manifest_path,
                        context=f"{gene} is present in pseudobulk matrix for {cell_type}.",
                        limitation=manifest.get("limitation", "Pseudobulk presence does not prove disease specificity without downstream differential analysis."),
                    )
                )
    return rows


def _fulltext_llm_rows(project_dir: Path) -> list[dict[str, Any]]:
    rows = []
    path = project_dir / "results" / "fulltext_literature" / "llm_extraction" / "fulltext_llm_extractions.json"
    data = _read_json(path, {})
    docs = data.get("documents", data.get("extractions", [])) if isinstance(data, dict) else []
    for doc in docs if isinstance(docs, list) else []:
        artifact = doc.get("artifact_path", "") or doc.get("source_artifact", "")
        doc_cell_types = _normalize_cell_types(doc.get("cell_types", []))
        results = doc.get("results", [])
        if isinstance(results, dict):
            results = results.get("items", [])
        for result in results if isinstance(results, list) else []:
            genes = _split_genes(str(result.get("gene") or result.get("gene_symbol") or result.get("molecule") or result.get("entity_symbol") or ""))
            result_cells = _normalize_cell_types(result.get("cell_type") or result.get("cell_types") or [])
            cell_types = result_cells or doc_cell_types
            if not genes or not cell_types:
                continue
            context = result.get("result_sentence") or result.get("sentence") or result.get("context") or ""
            confidence = _confidence(result.get("confidence", ""), default=0.72)
            for gene in genes:
                for cell_type in cell_types:
                    rows.append(
                        _row(
                            project_dir,
                            gene.upper(),
                            cell_type,
                            tissue=result.get("tissue", "") or doc.get("tissue", ""),
                            evidence_source="fulltext_llm_extraction",
                            evidence_type="fulltext_cell_type_result",
                            evidence_level="L1_fulltext",
                            confidence=confidence,
                            artifact_path=project_dir / artifact if artifact else path,
                            context=context,
                            limitation="Cell type was extracted by LLM from full text and needs human review against the source sentence.",
                        )
                    )
    return rows


def _row(
    project_dir: Path,
    gene: str,
    cell_type: str,
    tissue: str,
    evidence_source: str,
    evidence_type: str,
    evidence_level: str,
    confidence: float,
    artifact_path: Path,
    context: str,
    limitation: str,
) -> dict[str, Any]:
    rel = _rel(artifact_path, project_dir)
    payload = {
        "project_id": project_dir.name,
        "entity_symbol": gene.strip().upper(),
        "entity_type": "gene",
        "cell_type": cell_type.strip(),
        "tissue": tissue.strip(),
        "evidence_source": evidence_source,
        "evidence_type": "cell_type_expression",
        "source_evidence_type": evidence_type,
        "evidence_level": evidence_level,
        "confidence": f"{max(0.0, min(float(confidence), 1.0)):.3f}",
        "quality_score": f"{max(0.0, min(float(confidence), 1.0)):.3f}",
        "source_dataset": evidence_source,
        "artifact_path": rel,
        "sentence_or_context": context.strip(),
        "limitation": limitation,
        "review_status": "PENDING",
        "run_id": "cell_type_evidence",
        "module_version": "cell_type_evidence_v1",
        "created_at": _now(),
    }
    payload["evidence_id"] = "celltype_" + content_hash({k: payload[k] for k in ["project_id", "entity_symbol", "cell_type", "evidence_source", "artifact_path", "sentence_or_context"]})[:18]
    return payload


def _summary(project_dir: Path, rows: list[dict[str, Any]], evidence_path: Path) -> dict[str, Any]:
    by_gene: dict[str, list[dict[str, Any]]] = {}
    by_source: dict[str, int] = {}
    for row in rows:
        by_source[row["evidence_source"]] = by_source.get(row["evidence_source"], 0) + 1
        by_gene.setdefault(row["entity_symbol"], []).append(
            {
                "cell_type": row["cell_type"],
                "tissue": row["tissue"],
                "evidence_source": row["evidence_source"],
                "confidence": row["confidence"],
                "artifact_path": row["artifact_path"],
                "context": row["sentence_or_context"],
                "limitation": row["limitation"],
            }
        )
    payload = {
        "schema_version": CELL_TYPE_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "row_count": len(rows),
        "gene_count": len(by_gene),
        "by_source": by_source,
        "evidence_tsv": _rel(evidence_path, project_dir),
        "cell_type_by_gene": by_gene,
    }
    payload["summary_hash"] = content_hash(payload)
    return payload


def _fields() -> list[str]:
    return [
        "evidence_id",
        "project_id",
        "entity_symbol",
        "entity_type",
        "cell_type",
        "tissue",
        "evidence_source",
        "evidence_type",
        "source_evidence_type",
        "evidence_level",
        "confidence",
        "quality_score",
        "source_dataset",
        "artifact_path",
        "sentence_or_context",
        "limitation",
        "review_status",
        "run_id",
        "module_version",
        "created_at",
    ]


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for row in rows:
        key = (row.get("entity_symbol"), row.get("cell_type"), row.get("evidence_source"), row.get("artifact_path"), row.get("sentence_or_context"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _extract_cell_types_from_text(text: str) -> list[str]:
    tokens = [
        "endothelial cell",
        "smooth muscle cell",
        "skeletal muscle cell",
        "myotube",
        "myoblast",
        "fibroblast",
        "macrophage",
        "monocyte",
        "T cell",
        "B cell",
        "adipocyte",
        "hepatocyte",
        "epithelial cell",
        "stromal cell",
        "satellite cell",
    ]
    lower = text.lower()
    return [token for token in tokens if token.lower() in lower]


def _normalize_cell_types(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = [str(item) for item in value]
    else:
        raw = re.split(r"[;,|]", str(value or ""))
    return [item.strip() for item in raw if item.strip() and item.strip().lower() not in {"unknown", "not reported", "none"}]


def _split_genes(value: str) -> list[str]:
    return [item.strip().upper() for item in re.split(r"[;,/| ]+", value or "") if item.strip() and item.strip().upper() not in {"NA", "N/A", "UNKNOWN"}]


def _matrix_genes(path: Path) -> list[str]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        next(f, None)
        return [line.split("\t", 1)[0].strip() for line in f if line.strip()]


def _confidence(value: Any, default: float) -> float:
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value or "").strip().lower()
        if text in {"high", "enhanced", "supported", "true"}:
            return 0.85
        if text in {"medium", "moderate"}:
            return 0.65
        if text in {"low", "weak"}:
            return 0.4
        try:
            number = float(text)
        except ValueError:
            return default
    if number > 1:
        number = number / 100.0
    return max(0.0, min(number, 1.0))


def _read_table(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = "," if sample.count(",") > sample.count("\t") else "\t"
        return list(csv.DictReader(f, delimiter=delimiter))


def _field(row: dict[str, Any], aliases: list[str], default: str = "") -> str:
    lower = {_norm_key(str(k)): k for k in row}
    for alias in aliases:
        key = lower.get(_norm_key(alias))
        if key is not None and row.get(key) not in (None, ""):
            return str(row[key]).strip()
    return default


def _norm_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _write_tsv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _rel(path: Path, project_dir: Path) -> str:
    try:
        return str(path.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
