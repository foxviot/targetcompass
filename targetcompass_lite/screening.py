import csv
from pathlib import Path

from .validators import load_dataset_card, validate_dataset_card


def _resolve(project_dir: Path | None, value: str) -> Path:
    p = Path(value)
    if p.is_absolute() or project_dir is None:
        return p
    return project_dir / p


def validate_bulk_files(card: dict, project_dir: Path | None = None) -> list[str]:
    errors = []
    paths = card.get("file_paths", {})
    expr_path = _resolve(project_dir, paths.get("expression_matrix", ""))
    meta_path = _resolve(project_dir, paths.get("metadata", ""))
    if not expr_path.exists():
        return [f"expression matrix file not found: {expr_path}"]
    if not meta_path.exists():
        return [f"metadata file not found: {meta_path}"]

    with expr_path.open(encoding="utf-8") as f:
        reader = csv.reader(f, delimiter="\t")
        header = next(reader, [])
    if not header or header[0] != "gene_symbol":
        errors.append("expression matrix first column must be gene_symbol")
        expr_samples = []
    else:
        expr_samples = header[1:]
    if not expr_samples:
        errors.append("expression matrix must contain at least one sample column")

    with meta_path.open(encoding="utf-8") as f:
        meta_reader = csv.DictReader(f, delimiter="\t")
        fields = meta_reader.fieldnames or []
        meta_rows = list(meta_reader)
    for field in ["sample_id", "group"]:
        if field not in fields:
            errors.append(f"metadata missing required column: {field}")
    meta_samples = [row.get("sample_id", "") for row in meta_rows]
    if not meta_samples:
        errors.append("metadata must contain at least one sample")
    if len(meta_samples) != len(set(meta_samples)):
        errors.append("metadata sample_id values must be unique")
    if expr_samples and meta_samples and set(expr_samples) != set(meta_samples):
        errors.append("expression matrix sample columns do not match metadata sample_id values")

    groups = {row.get("group", "") for row in meta_rows}
    contrast = card.get("contrast", {})
    for label in [contrast.get("case"), contrast.get("control")]:
        if label and label not in groups:
            errors.append(f"contrast label not found in metadata group column: {label}")
    return errors


def metadata_quality(card: dict, project_dir: Path | None = None) -> dict:
    paths = card.get("file_paths", {})
    meta_path = _resolve(project_dir, paths.get("metadata", ""))
    if not meta_path.exists():
        return {"score": 0, "label": "missing", "notes": "metadata file not found"}
    with meta_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fields = reader.fieldnames or []
        rows = list(reader)
    score = 0
    notes = []
    for field in ["sample_id", "group"]:
        if field in fields:
            score += 25
        else:
            notes.append(f"missing {field}")
    optional = ["batch", "sex", "age", "donor_id"]
    present_optional = [field for field in optional if field in fields]
    score += min(30, len(present_optional) * 10)
    if rows and "sample_id" in fields and len({row.get("sample_id", "") for row in rows}) == len(rows):
        score += 10
    if rows and "group" in fields and len({row.get("group", "") for row in rows}) >= 2:
        score += 10
    score = min(100, score)
    if score >= 80:
        label = "high"
    elif score >= 60:
        label = "medium"
    elif score > 0:
        label = "low"
    else:
        label = "missing"
    if present_optional:
        notes.append("optional fields: " + ",".join(present_optional))
    return {"score": score, "label": label, "notes": "; ".join(notes) or "required metadata present"}


def source_class(card: dict) -> str:
    source = str(card.get("source", "")).lower()
    if "fixture" in source:
        return "fixture"
    if source in {"geo", "arrayexpress", "sra"} or card.get("accession", "").upper().startswith(("GSE", "ERP", "SRP")):
        return "real_public"
    return "local_or_manual"


def screen_card(path: Path, project_dir: Path | None = None) -> dict:
    errors = validate_dataset_card(path)
    card = load_dataset_card(path)
    reasons = []
    grade = "D"
    if errors:
        reasons.extend(errors)
    elif card.get("license_status") not in {"public", "authorized"}:
        reasons.append("license is not public or authorized")
    elif not card.get("matrix_available"):
        reasons.append("expression matrix is unavailable")
    elif card.get("modality") == "bulk_expression":
        file_errors = validate_bulk_files(card, project_dir)
        if file_errors:
            reasons.extend(file_errors)
            grade = "D"
        else:
            contrast = card.get("contrast", {})
            summary = card.get("sample_summary", {})
            if not contrast.get("case") or not contrast.get("control"):
                reasons.append("case/control contrast is incomplete")
            elif int(summary.get("case_n") or 0) < 3 or int(summary.get("control_n") or 0) < 3:
                grade = "B"
                reasons.append("small sample size; formal DEG allowed with limitations")
            else:
                grade = "A"
                reasons.append("bulk expression dataset is analyzable")
    else:
        grade = "C"
        reasons.append("non-bulk dataset kept as descriptive evidence")
    return {
        "dataset_id": card.get("dataset_id", path.stem),
        "path": str(path),
        "grade": grade,
        "modality": card.get("modality", "unknown"),
        "source_class": source_class(card),
        "metadata_quality_score": metadata_quality(card, project_dir)["score"] if card.get("modality") == "bulk_expression" else "",
        "metadata_quality_label": metadata_quality(card, project_dir)["label"] if card.get("modality") == "bulk_expression" else "not_applicable",
        "recommended_use": ",".join(card.get("recommended_use", [])),
        "reasons": "; ".join(reasons),
    }


def screen_project(project_dir: Path, selected_ids: set[str] | None = None) -> list[dict]:
    rows = []
    for path in sorted((project_dir / "dataset_cards").glob("*.yaml")):
        if selected_ids is not None and path.stem not in selected_ids:
            continue
        rows.append(screen_card(path, project_dir))
    report = project_dir / "screening_report.md"
    with report.open("w", encoding="utf-8") as f:
        f.write("# Dataset Screening Report\n\n")
        for row in rows:
            f.write(f"## {row['dataset_id']}\n")
            f.write(f"- Grade: {row['grade']}\n")
            f.write(f"- Modality: {row['modality']}\n")
            f.write(f"- Reason: {row['reasons']}\n\n")
    csv_path = project_dir / "eligible_datasets.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "dataset_id",
                "grade",
                "modality",
                "source_class",
                "metadata_quality_score",
                "metadata_quality_label",
                "recommended_use",
                "path",
                "reasons",
            ],
        )
        writer.writeheader()
        for row in rows:
            if row["grade"] in {"A", "B", "C"}:
                writer.writerow(row)
    return rows
