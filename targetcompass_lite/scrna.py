import csv
import hashlib
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
    min_donors_per_group: int = 1,
    case_group: str = "",
    control_group: str = "",
) -> Path:
    counts_path = project_dir / count_matrix
    metadata_path = project_dir / metadata
    samples, matrix = _read_matrix(counts_path)
    meta_rows = _read_metadata(metadata_path)
    meta_by_cell = {row["cell_id"]: row for row in meta_rows}
    missing = [cell for cell in samples if cell not in meta_by_cell]
    if missing:
        raise ValueError(f"single-cell metadata missing cell_id rows: {', '.join(missing[:5])}")
    required_columns = {"cell_id", donor_column, group_column}
    missing_columns = sorted(column for column in required_columns if column not in meta_rows[0])
    if missing_columns:
        raise ValueError("single-cell metadata missing required columns: " + ", ".join(missing_columns))
    if cell_type and cell_type_column not in meta_rows[0]:
        raise ValueError(f"single-cell metadata missing cell type column: {cell_type_column}")
    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    skipped = {
        "cell_type_filter": 0,
        "missing_donor_or_group": 0,
    }
    for cell in samples:
        row = meta_by_cell[cell]
        if cell_type and row.get(cell_type_column, "") != cell_type:
            skipped["cell_type_filter"] += 1
            continue
        donor = row.get(donor_column, "")
        group = row.get(group_column, "")
        if not donor or not group:
            skipped["missing_donor_or_group"] += 1
            continue
        buckets[(donor, group)].append(cell)
    donor_group_rows = [
        {
            "donor_id": donor,
            "group": group,
            "cell_count": len(cells),
            "retained": len(cells) >= min_cells_per_donor,
            "drop_reason": "" if len(cells) >= min_cells_per_donor else "below_min_cells_per_donor",
        }
        for (donor, group), cells in sorted(buckets.items())
    ]
    retained = {key: cells for key, cells in buckets.items() if len(cells) >= min_cells_per_donor}
    if len(retained) < 2:
        raise ValueError("pseudobulk requires at least two donor/group aggregates after filtering")
    donors_by_group: dict[str, set[str]] = defaultdict(set)
    cells_by_group: dict[str, int] = defaultdict(int)
    for donor, group in retained:
        donors_by_group[group].add(donor)
        cells_by_group[group] += len(retained[(donor, group)])
    underpowered_groups = sorted(group for group, donors in donors_by_group.items() if len(donors) < min_donors_per_group)
    if underpowered_groups:
        raise ValueError(
            "pseudobulk group(s) below min_donors_per_group="
            f"{min_donors_per_group}: "
            + ", ".join(f"{group}({len(donors_by_group[group])})" for group in underpowered_groups)
        )
    contrast = _resolve_contrast(sorted(donors_by_group), case_group, control_group)
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
        writer = csv.DictWriter(
            f,
            fieldnames=["sample_id", "donor_id", "group", "cell_count", "cell_type", "contrast_role"],
            delimiter="\t",
        )
        writer.writeheader()
        for donor, group in sorted(retained):
            writer.writerow(
                {
                    "sample_id": f"{donor}__{group}",
                    "donor_id": donor,
                    "group": group,
                    "cell_count": len(retained[(donor, group)]),
                    "cell_type": cell_type or "mixed",
                    "contrast_role": _contrast_role(group, contrast),
                }
            )
    donor_qc_out = out_dir / "donor_group_qc.tsv"
    with donor_qc_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["donor_id", "group", "cell_count", "retained", "drop_reason"], delimiter="\t")
        writer.writeheader()
        writer.writerows(donor_group_rows)
    group_qc_out = out_dir / "group_qc.tsv"
    group_rows = [
        {
            "group": group,
            "donor_count": len(donors),
            "cell_count": cells_by_group[group],
            "contrast_role": _contrast_role(group, contrast),
        }
        for group, donors in sorted(donors_by_group.items())
    ]
    with group_qc_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["group", "donor_count", "cell_count", "contrast_role"], delimiter="\t")
        writer.writeheader()
        writer.writerows(group_rows)
    warnings = []
    if not contrast["case_group"] or not contrast["control_group"]:
        warnings.append("no explicit two-group contrast resolved; downstream DEG must set case/control groups")
    if any(row["retained"] is False for row in donor_group_rows):
        warnings.append("some donor/group aggregates were dropped by min_cells_per_donor")
    qc = {
        "schema_version": "v4.scrna_pseudobulk_qc/0.2",
        "dataset_id": dataset_id,
        "input_cells": len(samples),
        "retained_cells": sum(len(cells) for cells in retained.values()),
        "pseudobulk_samples": len(pseudo_samples),
        "retained_donors": len({donor for donor, _group in retained}),
        "groups": group_rows,
        "skipped_cells": skipped,
        "genes": len(matrix),
        "cell_type": cell_type or "mixed",
        "min_cells_per_donor": min_cells_per_donor,
        "min_donors_per_group": min_donors_per_group,
        "contrast": contrast,
        "warnings": warnings,
        "status": "pass" if not warnings else "warning",
    }
    qc_summary = out_dir / "qc_summary.json"
    qc_summary.write_text(json.dumps(qc, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "schema_version": "v4.scrna_pseudobulk_manifest/0.2",
        "module_id": "scrna_pseudobulk_v1",
        "dataset_id": dataset_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "count_matrix": count_matrix,
            "metadata": metadata,
            "count_matrix_hash": _file_sha256(counts_path),
            "metadata_hash": _file_sha256(metadata_path),
        },
        "parameters": {
            "cell_type": cell_type,
            "donor_column": donor_column,
            "group_column": group_column,
            "cell_type_column": cell_type_column,
            "min_cells_per_donor": min_cells_per_donor,
            "min_donors_per_group": min_donors_per_group,
            "case_group": case_group,
            "control_group": control_group,
        },
        "outputs": {
            "matrix": str(matrix_out.relative_to(project_dir)),
            "metadata": str(metadata_out.relative_to(project_dir)),
            "donor_group_qc": str(donor_qc_out.relative_to(project_dir)),
            "group_qc": str(group_qc_out.relative_to(project_dir)),
            "qc_summary": str((out_dir / "qc_summary.json").relative_to(project_dir)),
        },
        "contrast": contrast,
        "limitation": "Donor-level pseudobulk aggregate; downstream DEG must use donor/sample aggregates, not cells as replicates.",
    }
    run_manifest = out_dir / "run_manifest.json"
    run_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [matrix_out, metadata_out, donor_qc_out, group_qc_out, qc_summary, run_manifest],
            producer=f"scrna_pseudobulk_{dataset_id}",
            artifact_type="scrna_pseudobulk_output",
            task_id=f"scrna_pseudobulk_{dataset_id}",
            qc_status="pass" if qc["status"] == "pass" else "pending",
        )
    except Exception:
        pass
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


def _resolve_contrast(groups: list[str], case_group: str, control_group: str) -> dict[str, str]:
    case_group = case_group.strip()
    control_group = control_group.strip()
    if case_group and control_group:
        missing = [group for group in [case_group, control_group] if group not in groups]
        if missing:
            raise ValueError("contrast group(s) not present after pseudobulk filtering: " + ", ".join(missing))
        return {"case_group": case_group, "control_group": control_group, "source": "explicit"}
    if len(groups) == 2:
        return {"case_group": groups[0], "control_group": groups[1], "source": "inferred_two_groups"}
    return {"case_group": "", "control_group": "", "source": "unresolved"}


def _contrast_role(group: str, contrast: dict[str, str]) -> str:
    if group == contrast.get("case_group"):
        return "case"
    if group == contrast.get("control_group"):
        return "control"
    return "other"


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
