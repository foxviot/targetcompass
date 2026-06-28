import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_10x_h5_donor_pseudobulk(
    project_dir: Path,
    accession: str,
    metadata: str,
    raw_manifest: str = "",
    output_dataset_id: str = "",
) -> dict[str, Any]:
    accession = accession.upper().strip()
    dataset_id = output_dataset_id or f"{accession}_raw_pseudobulk"
    metadata_path = project_dir / metadata
    manifest_path = project_dir / raw_manifest if raw_manifest else project_dir / "data" / accession / "geo_raw_manifest.json"
    rows = _read_metadata(metadata_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    h5_by_row = _match_h5_files(project_dir, rows, manifest.get("h5_files", []))
    out_data = project_dir / "data" / dataset_id
    out_data.mkdir(parents=True, exist_ok=True)

    sample_vectors: dict[str, dict[str, float]] = {}
    qc_rows = []
    all_genes: set[str] = set()
    for row in rows:
        sample_id = row["sample_id"]
        h5_path = h5_by_row.get(sample_id)
        if not h5_path:
            qc_rows.append({**_qc_base(row), "status": "missing_h5", "genes": 0, "cells": 0, "nonzero_entries": 0, "h5_file": ""})
            continue
        vector, stats = _sum_10x_h5_by_gene(h5_path)
        sample_vectors[sample_id] = vector
        all_genes.update(vector)
        qc_rows.append({**_qc_base(row), **stats, "status": "pass", "h5_file": str(h5_path.relative_to(project_dir)).replace("\\", "/")})

    if len(sample_vectors) < 2:
        raise ValueError("10x H5 pseudobulk requires at least two matched samples")

    matrix_path = out_data / "expression_matrix.tsv"
    sample_ids = [row["sample_id"] for row in rows if row["sample_id"] in sample_vectors]
    with matrix_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["gene_symbol", *sample_ids])
        for gene in sorted(all_genes):
            writer.writerow([gene, *[_fmt(sample_vectors[sample].get(gene, 0.0)) for sample in sample_ids]])

    metadata_out = out_data / "metadata.tsv"
    fields = ["sample_id", "donor_id", "group", "condition", "cell_type", "geo_accession", "raw_h5_file", "source_modality"]
    with metadata_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            if row["sample_id"] not in sample_vectors:
                continue
            h5_path = h5_by_row[row["sample_id"]]
            writer.writerow(
                {
                    "sample_id": row["sample_id"],
                    "donor_id": row.get("donor_id", ""),
                    "group": row.get("group", ""),
                    "condition": row.get("condition", row.get("group", "")),
                    "cell_type": row.get("cell_type", "mixed_nuclei"),
                    "geo_accession": row.get("geo_accession_list_gsmxxx_eg_gsm5098661", row.get("geo_accession", "")),
                    "raw_h5_file": str(h5_path.relative_to(project_dir)).replace("\\", "/"),
                    "source_modality": "10x snRNA-seq RAW H5 donor pseudobulk",
                }
            )

    qc_path = out_data / "h5_pseudobulk_qc.tsv"
    with qc_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["sample_id", "donor_id", "group", "status", "genes", "cells", "nonzero_entries", "h5_file"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(qc_rows)

    groups = _group_counts([row for row in rows if row["sample_id"] in sample_vectors])
    card_path = project_dir / "dataset_cards" / f"{dataset_id}.yaml"
    _write_card(project_dir, card_path, dataset_id, accession, matrix_path, metadata_out, groups, len(sample_vectors), len(all_genes))
    run_manifest = {
        "schema_version": "v4.scrna_10x_h5_pseudobulk/0.1",
        "dataset_id": dataset_id,
        "accession": accession,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "metadata": metadata,
            "raw_manifest": str(manifest_path.relative_to(project_dir)).replace("\\", "/"),
            "metadata_hash": _file_sha256(metadata_path),
            "raw_manifest_hash": _file_sha256(manifest_path),
        },
        "outputs": {
            "expression_matrix": str(matrix_path.relative_to(project_dir)).replace("\\", "/"),
            "metadata": str(metadata_out.relative_to(project_dir)).replace("\\", "/"),
            "dataset_card": str(card_path.relative_to(project_dir)).replace("\\", "/"),
            "qc": str(qc_path.relative_to(project_dir)).replace("\\", "/"),
        },
        "samples": len(sample_vectors),
        "genes": len(all_genes),
        "groups": groups,
        "matched_h5": len(sample_vectors),
        "missing_h5": [row["sample_id"] for row in qc_rows if row["status"] != "pass"],
        "limitation": "Donor-level mixed-nuclei pseudobulk from 10x H5; cell-type-specific SASP requires cell type annotation.",
    }
    run_path = out_data / "run_manifest.json"
    run_path.write_text(json.dumps(run_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [matrix_path, metadata_out, qc_path, card_path, run_path],
            producer=f"scrna_10x_{dataset_id}",
            artifact_type="scrna_10x_output",
            task_id=f"scrna_10x_{dataset_id}",
            qc_status="pass",
        )
    except Exception:
        pass
    return run_manifest


def _sum_10x_h5_by_gene(path: Path) -> tuple[dict[str, float], dict[str, int]]:
    import h5py
    import scipy.sparse as sp

    with h5py.File(path, "r") as h5:
        matrix = h5["matrix"]
        shape = tuple(int(x) for x in matrix["shape"][:])
        sparse = sp.csc_matrix((matrix["data"][:], matrix["indices"][:], matrix["indptr"][:]), shape=shape)
        sums = sparse.sum(axis=1).A1
        names = [_decode(x).upper() for x in matrix["features"]["name"][:]]
        out: dict[str, float] = defaultdict(float)
        for gene, value in zip(names, sums):
            if gene:
                out[gene] += float(value)
        return dict(out), {"genes": len(out), "cells": shape[1], "nonzero_entries": int(len(matrix["data"]))}


def _match_h5_files(project_dir: Path, rows: list[dict[str, str]], h5_files: list[dict]) -> dict[str, Path]:
    out = {}
    for row in rows:
        sample_id = row.get("sample_id", "")
        tokens = [
            row.get("raw_file", ""),
            row.get("donor_id", ""),
            row.get("geo_accession_list_gsmxxx_eg_gsm5098661", ""),
            row.get("geo_accession", ""),
        ]
        for item in h5_files:
            name = str(item.get("name", ""))
            if any(token and (token in name or token.replace("Old", "").replace("You", "") in name) for token in tokens):
                out[sample_id] = project_dir / item["path"]
                break
    return out


def _read_metadata(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _group_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        group = row.get("group", "unknown")
        out[group] = out.get(group, 0) + 1
    return out


def _write_card(project_dir: Path, card: Path, dataset_id: str, accession: str, matrix: Path, metadata: Path, groups: dict[str, int], sample_n: int, gene_n: int) -> None:
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(
        f"""dataset_id: {dataset_id}
source: GEO
accession: {accession}
modality: bulk_expression
organism: human
tissue: skeletal muscle
contrast:
  case: old
  control: young
sample_summary:
  case_n: {groups.get("old", 0)}
  control_n: {groups.get("young", 0)}
  donor_n: {sample_n}
metadata_fields: [sample_id, donor_id, group, condition, cell_type, geo_accession, raw_h5_file, source_modality]
matrix_available: true
license_status: public
file_paths:
  expression_matrix: {str(matrix.relative_to(project_dir)).replace("\\", "/")}
  metadata: {str(metadata.relative_to(project_dir)).replace("\\", "/")}
known_limitations: [derived from 10x snRNA-seq RAW H5, donor-level mixed-nuclei pseudobulk, cell-type-specific SASP requires cell annotation]
recommended_use: [bulk_deg, enrichment, mixed_nuclei_snrna_pseudobulk]
blocked_use: [cell_type_specific_pseudobulk_without_cell_annotations]
""",
        encoding="utf-8",
    )


def _qc_base(row: dict[str, str]) -> dict[str, str]:
    return {"sample_id": row.get("sample_id", ""), "donor_id": row.get("donor_id", ""), "group": row.get("group", "")}


def _decode(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.6g}"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
