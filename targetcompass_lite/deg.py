import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .executor import build_executor_contract, run_local_executor
from .validators import load_dataset_card


ROOT = Path(__file__).resolve().parents[1]
FORMAL_LIMMA_RUNNER = ROOT / "scripts" / "r" / "bulk_limma_deg.R"


def _find_rscript() -> str | None:
    found = shutil.which("Rscript")
    if found:
        return found
    roots = [Path("C:/Program Files/R"), Path("C:/Program Files (x86)/R")]
    for root in roots:
        if not root.exists():
            continue
        candidates = sorted(root.glob("R-*/bin/Rscript.exe"), reverse=True)
        if candidates:
            return str(candidates[0])
    return None


def _r_env() -> dict:
    env = os.environ.copy()
    user_lib = Path.home() / "Documents" / "R" / "win-library" / "4.6"
    if user_lib.exists():
        env["R_LIBS_USER"] = str(user_lib)
    return env


def _read_matrix(path: Path):
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        samples = [h for h in reader.fieldnames if h != "gene_symbol"]
        rows = []
        for row in reader:
            rows.append((row["gene_symbol"], {s: float(row[s]) for s in samples}))
    return samples, rows


def _expression_profile(rows: list[tuple[str, dict[str, float]]]) -> dict:
    values = []
    for _, row in rows[: min(len(rows), 2000)]:
        values.extend(row.values())
    if not values:
        return {
            "matrix_type": "unknown",
            "min_value": "",
            "max_value": "",
            "mean_value": "",
            "missing_values": 0,
            "negative_values": 0,
        }
    negative = sum(1 for value in values if value < 0)
    max_value = max(values)
    min_value = min(values)
    mean_value = _mean(values)
    integer_like = sum(1 for value in values if abs(value - round(value)) < 1e-6) / len(values)
    if min_value >= 0 and max_value > 50 and integer_like > 0.8:
        matrix_type = "rna_seq_count_like"
    elif min_value >= 0 and max_value <= 30:
        matrix_type = "microarray_or_log_expression_like"
    else:
        matrix_type = "continuous_expression_like"
    return {
        "matrix_type": matrix_type,
        "min_value": f"{min_value:.6g}",
        "max_value": f"{max_value:.6g}",
        "mean_value": f"{mean_value:.6g}",
        "missing_values": 0,
        "negative_values": negative,
    }


def _read_meta(path: Path):
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _mean(values):
    return sum(values) / len(values)


def _variance(values):
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _limma_available() -> tuple[bool, str]:
    rscript = _find_rscript()
    if not rscript:
        return False, "Rscript not found"
    if not FORMAL_LIMMA_RUNNER.exists():
        return False, "formal limma runner script not found"
    try:
        result = subprocess.run(
            [rscript, "-e", "suppressPackageStartupMessages(library(limma)); cat('ok')"],
            text=True,
            capture_output=True,
            env=_r_env(),
            timeout=20,
        )
    except Exception as exc:
        return False, f"Rscript check failed: {exc}"
    if result.returncode:
        return False, "limma package unavailable"
    return True, "Rscript and limma available"


def _select_deg_runner() -> dict:
    requested = os.environ.get("TARGETCOMPASS_DEG_RUNNER", "auto").strip().lower()
    if requested not in {"auto", "python", "formal"}:
        requested = "auto"
    available, reason = _limma_available()
    if requested == "python":
        return {"runner_type": "python_fallback", "reason": "forced by TARGETCOMPASS_DEG_RUNNER=python"}
    if requested == "formal":
        if not available:
            raise RuntimeError(f"formal DEG runner requested but unavailable: {reason}")
        return {"runner_type": "r_limma", "reason": reason}
    if available:
        return {"runner_type": "r_limma", "reason": reason}
    return {"runner_type": "python_fallback", "reason": reason}


def _candidate_batch_covariates(meta_rows: list[dict]) -> list[str]:
    if not meta_rows:
        return []
    covariates = []
    fields = set(meta_rows[0].keys())
    for field in ["batch"]:
        if field not in fields:
            continue
        values = {str(row.get(field, "")).strip().lower() for row in meta_rows}
        values.discard("")
        values.discard("unknown")
        if len(values) >= 2:
            covariates.append(field)
    return covariates


def _design_matrix(meta_rows: list[dict], case: str, control: str, covariates: list[str]) -> tuple[list[list[float]], list[str]]:
    rows = []
    columns = ["intercept", f"group:{case}_vs_{control}"]
    levels_by_covariate = {}
    for covariate in covariates:
        levels = sorted({row.get(covariate, "") for row in meta_rows})
        levels = [level for level in levels if level not in {"", "unknown"}]
        if len(levels) >= 2:
            levels_by_covariate[covariate] = levels[1:]
            columns.extend(f"{covariate}:{level}" for level in levels[1:])
    for row in meta_rows:
        group = row.get("group", "")
        if group not in {case, control}:
            continue
        design_row = [1.0, 1.0 if group == case else 0.0]
        for covariate, levels in levels_by_covariate.items():
            design_row.extend(1.0 if row.get(covariate, "") == level else 0.0 for level in levels)
        rows.append(design_row)
    return rows, columns


def _matrix_rank(matrix: list[list[float]], tol: float = 1e-9) -> int:
    if not matrix:
        return 0
    a = [row[:] for row in matrix]
    n_rows = len(a)
    n_cols = len(a[0])
    rank = 0
    for col in range(n_cols):
        pivot = None
        for row in range(rank, n_rows):
            if abs(a[row][col]) > tol:
                pivot = row
                break
        if pivot is None:
            continue
        a[rank], a[pivot] = a[pivot], a[rank]
        pivot_value = a[rank][col]
        a[rank] = [value / pivot_value for value in a[rank]]
        for row in range(n_rows):
            if row != rank and abs(a[row][col]) > tol:
                factor = a[row][col]
                a[row] = [value - factor * pivot_value for value, pivot_value in zip(a[row], a[rank])]
        rank += 1
        if rank == n_rows:
            break
    return rank


def _check_design_matrix(meta_rows: list[dict], case: str, control: str) -> dict:
    covariates = _candidate_batch_covariates(meta_rows)
    matrix, columns = _design_matrix(meta_rows, case, control, covariates)
    rank = _matrix_rank(matrix)
    full_rank = rank == len(columns)
    if not full_rank:
        fallback_matrix, fallback_columns = _design_matrix(meta_rows, case, control, [])
        fallback_rank = _matrix_rank(fallback_matrix)
        if fallback_rank != len(fallback_columns):
            raise ValueError(
                "design matrix is rank deficient even without batch covariates; check case/control metadata"
            )
        return {
            "columns": fallback_columns,
            "rank": fallback_rank,
            "full_rank": True,
            "batch_covariates": [],
            "dropped_batch_covariates": covariates,
            "warnings": [
                "Batch covariates were dropped because they were confounded with case/control group.",
                "Interpret DEG as an unadjusted association screen and review metadata before making claims.",
            ],
        }
    return {
        "columns": columns,
        "rank": rank,
        "full_rank": full_rank,
        "batch_covariates": covariates,
        "dropped_batch_covariates": [],
        "warnings": [],
    }


def _write_manifest(
    out_dir: Path,
    dataset_id: str,
    expr: Path,
    meta: Path,
    case: str,
    control: str,
    design: dict,
    runner: dict,
) -> None:
    limitations = []
    if runner["runner_type"] == "python_fallback":
        limitations = [
            "Python fallback uses a lightweight Welch-like effect screen.",
            "Use scripts/r/bulk_limma_deg.R for formal limma analysis when R dependencies are available.",
        ]
    (out_dir / "run_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "bulk_deg_run_manifest_v2",
                "script": "targetcompass_lite/deg.py",
                "runner_type": runner["runner_type"],
                "runner_reason": runner["reason"],
                "formal_runner": "scripts/r/bulk_limma_deg.R",
                "dataset_id": dataset_id,
                "input_hash": {"expression_matrix": _hash(expr), "metadata": _hash(meta)},
                "parameters": {
                    "case": case,
                    "control": control,
                    "batch_covariates": design["batch_covariates"],
                    "dropped_batch_covariates": design.get("dropped_batch_covariates", []),
                },
                "design": design,
                "limitations": [*limitations, *design.get("warnings", [])],
                "output_files": ["deg_results.tsv", "qc_summary.tsv", "qc_summary.json", "executor_manifest.json"],
                "executor_manifest": "executor_manifest.json",
                "status": "success",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_qc(
    out_dir: Path,
    dataset_id: str,
    samples: list[str],
    matrix: list[tuple[str, dict[str, float]]],
    meta_rows: list[dict],
    case_samples: list[str],
    control_samples: list[str],
    design: dict,
    runner: dict,
) -> None:
    profile = _expression_profile(matrix)
    duplicated_genes = len(matrix) - len({gene for gene, _ in matrix})
    qc = {
        "dataset_id": dataset_id,
        "sample_count": len(samples),
        "metadata_rows": len(meta_rows),
        "case_samples": len(case_samples),
        "control_samples": len(control_samples),
        "genes": len(matrix),
        "duplicated_gene_rows": duplicated_genes,
        "matrix_type": profile["matrix_type"],
        "min_value": profile["min_value"],
        "max_value": profile["max_value"],
        "mean_value": profile["mean_value"],
        "negative_values": profile["negative_values"],
        "design_rank": design["rank"],
        "design_columns": len(design["columns"]),
        "design_full_rank": design["full_rank"],
        "batch_covariates": design["batch_covariates"],
        "dropped_batch_covariates": design.get("dropped_batch_covariates", []),
        "design_warnings": design.get("warnings", []),
        "runner_type": runner["runner_type"],
        "runner_reason": runner["reason"],
        "qc_status": "PASS" if design["full_rank"] and case_samples and control_samples and not duplicated_genes else "REVIEW",
    }
    (out_dir / "qc_summary.json").write_text(json.dumps(qc, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out_dir / "qc_summary.tsv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["metric", "value"], delimiter="\t")
        writer.writeheader()
        for key, value in qc.items():
            writer.writerow({"metric": key, "value": ",".join(value) if isinstance(value, list) else value})


def _run_formal_limma(expr: Path, meta: Path, case: str, control: str, out_dir: Path, design: dict) -> Path:
    rscript = _find_rscript()
    if not rscript:
        raise RuntimeError("Rscript not found")
    result = subprocess.run(
        [
            rscript,
            str(FORMAL_LIMMA_RUNNER),
            str(expr),
            str(meta),
            case,
            control,
            str(out_dir),
            ",".join(design["batch_covariates"]),
        ],
        text=True,
        capture_output=True,
        env=_r_env(),
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(f"formal limma DEG failed: {result.stderr.strip() or result.stdout.strip()}")
    return out_dir / "deg_results.tsv"


def run_deg(project_dir: Path, dataset_id: str) -> Path:
    card_path = project_dir / "dataset_cards" / f"{dataset_id}.yaml"
    card = load_dataset_card(card_path)
    expr = project_dir / card["file_paths"]["expression_matrix"]
    meta = project_dir / card["file_paths"]["metadata"]
    case = card["contrast"]["case"]
    control = card["contrast"]["control"]
    samples, matrix = _read_matrix(expr)
    meta_rows = _read_meta(meta)
    meta_samples = [row["sample_id"] for row in meta_rows]
    if set(samples) != set(meta_samples):
        raise ValueError("sample IDs do not match between expression matrix and metadata")
    case_samples = [row["sample_id"] for row in meta_rows if row["group"] == case]
    control_samples = [row["sample_id"] for row in meta_rows if row["group"] == control]
    if not case_samples or not control_samples:
        raise ValueError("case/control labels are missing in metadata")
    design = _check_design_matrix(meta_rows, case, control)
    out_dir = project_dir / "results" / f"bulk_deg_{dataset_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "deg_results.tsv"
    runner = _select_deg_runner()
    contract = build_executor_contract(
        project_dir,
        module_id=f"bulk_deg_{dataset_id}",
        runner="targetcompass_lite.deg.run_deg",
        inputs={
            "dataset_card": str(card_path.relative_to(project_dir)),
            "expression_matrix": card["file_paths"]["expression_matrix"],
            "metadata": card["file_paths"]["metadata"],
        },
        parameters={
            "case": case,
            "control": control,
            "runner_type": runner["runner_type"],
            "batch_covariates": design["batch_covariates"],
            "dropped_batch_covariates": design.get("dropped_batch_covariates", []),
        },
        expected_outputs=[
            f"results/bulk_deg_{dataset_id}/deg_results.tsv",
            f"results/bulk_deg_{dataset_id}/qc_summary.tsv",
            f"results/bulk_deg_{dataset_id}/qc_summary.json",
            f"results/bulk_deg_{dataset_id}/run_manifest.json",
        ],
        nextflow_hint={"process": "bulk_limma_or_python_fallback"},
    )

    def operation() -> Path:
        nonlocal runner, result_path
        if runner["runner_type"] == "r_limma":
            try:
                result_path = _run_formal_limma(expr, meta, case, control, out_dir, design)
                _write_qc(out_dir, dataset_id, samples, matrix, meta_rows, case_samples, control_samples, design, runner)
                _write_manifest(out_dir, dataset_id, expr, meta, case, control, design, runner)
                return result_path
            except Exception as exc:
                runner = {
                    "runner_type": "python_fallback",
                    "reason": f"formal limma failed; used Python fallback. Reason: {exc}",
                }
                design.setdefault("warnings", []).append(str(runner["reason"]))
        scored = []
        for gene, values in matrix:
            case_vals = [values[s] for s in case_samples]
            ctrl_vals = [values[s] for s in control_samples]
            case_mean = _mean(case_vals)
            ctrl_mean = _mean(ctrl_vals)
            log_fc = math.log2((case_mean + 0.01) / (ctrl_mean + 0.01))
            se = math.sqrt((_variance(case_vals) / len(case_vals)) + (_variance(ctrl_vals) / len(ctrl_vals)) + 1e-9)
            t_stat = (case_mean - ctrl_mean) / se if se else 0.0
            p_value = min(1.0, math.exp(-abs(t_stat)))
            scored.append(
                {
                    "gene_symbol": gene,
                    "case_mean": f"{case_mean:.4f}",
                    "control_mean": f"{ctrl_mean:.4f}",
                    "logFC": f"{log_fc:.4f}",
                    "p_value": f"{p_value:.6g}",
                    "direction": "up" if log_fc > 0 else "down",
                }
            )
        scored.sort(key=lambda r: (float(r["p_value"]), -abs(float(r["logFC"])), r["gene_symbol"]))
        for rank, row in enumerate(scored, 1):
            row["adj_p_value"] = f"{min(1.0, float(row['p_value']) * len(scored) / rank):.6g}"
        with result_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["gene_symbol", "case_mean", "control_mean", "logFC", "p_value", "adj_p_value", "direction"],
                delimiter="\t",
            )
            writer.writeheader()
            writer.writerows(scored)
        _write_qc(out_dir, dataset_id, samples, matrix, meta_rows, case_samples, control_samples, design, runner)
        _write_manifest(out_dir, dataset_id, expr, meta, case, control, design, runner)
        return result_path

    result_path, _ = run_local_executor(project_dir, out_dir, contract, operation)
    return result_path
