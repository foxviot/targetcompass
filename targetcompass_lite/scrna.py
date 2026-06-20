import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def run_scrna_pseudobulk(
    project_dir: Path,
    dataset_id: str,
    count_matrix: str,
    metadata: str,
    cell_type: str = "",
    donor_column: str = "donor_id",
    group_column: str = "group",
    cell_type_column: str = "cell_type",
    min_cells_per_donor: int = 1,
) -> Path:
    counts_path = project_dir / count_matrix
    metadata_path = project_dir / metadata
    samples, matrix = _read_matrix(counts_path)
    meta_rows = _read_metadata(metadata_path)
    meta_by_cell = {row["cell_id"]: row for row in meta_rows}
    missing = [cell for cell in samples if cell not in meta_by_cell]
    if missing:
        raise ValueError(f"single-cell metadata missing cell_id rows: {', '.join(missing[:5])}")
    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for cell in samples:
        row = meta_by_cell[cell]
        if cell_type and row.get(cell_type_column, "") != cell_type:
            continue
        donor = row.get(donor_column, "")
        group = row.get(group_column, "")
        if not donor or not group:
            continue
        buckets[(donor, group)].append(cell)
    retained = {key: cells for key, cells in buckets.items() if len(cells) >= min_cells_per_donor}
    if len(retained) < 2:
        raise ValueError("pseudobulk requires at least two donor/group aggregates after filtering")
    out_dir = project_dir / "results" / f"scrna_pseudobulk_{dataset_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    pseudo_samples = [f"{donor}__{group}" for donor, group in sorted(retained)]
    matrix_out = out_dir / "pseudobulk_matrix.tsv"
    with matrix_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["gene_symbol", *pseudo_samples])
        for gene, values in matrix.items():
            by_cell = dict(zip(samples, values))
            writer.writerow([gene, *[sum(by_cell[cell] for cell in retained[key]) for key in sorted(retained)]])
    metadata_out = out_dir / "pseudobulk_metadata.tsv"
    with metadata_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["sample_id", "donor_id", "group", "cell_count", "cell_type"], delimiter="\t")
        writer.writeheader()
        for donor, group in sorted(retained):
            writer.writerow(
                {
                    "sample_id": f"{donor}__{group}",
                    "donor_id": donor,
                    "group": group,
                    "cell_count": len(retained[(donor, group)]),
                    "cell_type": cell_type or "mixed",
                }
            )
    qc = {
        "schema_version": "v4.scrna_pseudobulk_qc/0.1",
        "dataset_id": dataset_id,
        "input_cells": len(samples),
        "retained_cells": sum(len(cells) for cells in retained.values()),
        "pseudobulk_samples": len(pseudo_samples),
        "genes": len(matrix),
        "cell_type": cell_type or "mixed",
        "min_cells_per_donor": min_cells_per_donor,
        "status": "pass",
    }
    (out_dir / "qc_summary.json").write_text(json.dumps(qc, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "schema_version": "v4.scrna_pseudobulk_manifest/0.1",
        "module_id": "scrna_pseudobulk_v1",
        "dataset_id": dataset_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"count_matrix": count_matrix, "metadata": metadata},
        "outputs": {
            "matrix": str(matrix_out.relative_to(project_dir)),
            "metadata": str(metadata_out.relative_to(project_dir)),
            "qc_summary": str((out_dir / "qc_summary.json").relative_to(project_dir)),
        },
        "limitation": "Donor-level pseudobulk aggregate; downstream DEG must use donor/sample aggregates, not cells as replicates.",
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return matrix_out


def _read_matrix(path: Path) -> tuple[list[str], dict[str, list[float]]]:
    with path.open(encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader)
        samples = header[1:]
        matrix = {}
        for row in reader:
            matrix[row[0]] = [float(value or 0) for value in row[1:]]
    return samples, matrix


def _read_metadata(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    if not rows or "cell_id" not in rows[0]:
        raise ValueError("single-cell metadata must contain cell_id")
    return rows
