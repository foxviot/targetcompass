import csv
import json
from pathlib import Path

from .analysis_modules import ANALYSIS_MODULES, write_module_registry
from .validators import load_dataset_card
from .v4 import build_v4_manifest


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _dataset_entry(row: dict, card: dict) -> dict:
    summary = card.get("sample_summary", {})
    return {
        "dataset_id": row["dataset_id"],
        "grade": row["grade"],
        "modality": row["modality"],
        "source": card.get("source", "unknown"),
        "accession": card.get("accession", "unknown"),
        "organism": card.get("organism", "unknown"),
        "tissue": card.get("tissue", "unknown"),
        "recommended_use": _split_csv(row.get("recommended_use", "")),
        "sample_summary": {
            "case_n": int(summary.get("case_n") or 0),
            "control_n": int(summary.get("control_n") or 0),
            "donor_n": int(summary.get("donor_n") or 0),
        },
        "limitations": card.get("known_limitations", []),
        "screening_reasons": row.get("reasons", ""),
    }


def _bulk_deg_module(project_dir: Path, row: dict, card: dict) -> dict:
    dataset_id = row["dataset_id"]
    out_dir = f"results/bulk_deg_{dataset_id}"
    return {
        "module_id": f"P4_bulk_deg_{dataset_id}",
        "module": "bulk_deg",
        "dataset_id": dataset_id,
        "objective": "Estimate differential expression for the declared case/control contrast.",
        "status": "planned",
        "runner": "targetcompass_lite.deg.run_deg",
        "runner_type": "python_fallback",
        "formal_runner": "scripts/r/bulk_limma_deg.R",
        "command": f"python tc_lite.py run-deg --project {project_dir.name} --dataset {dataset_id}",
        "inputs": {
            "dataset_card": f"dataset_cards/{dataset_id}.yaml",
            "expression_matrix": card["file_paths"]["expression_matrix"],
            "metadata": card["file_paths"]["metadata"],
        },
        "parameters": {
            "case": card["contrast"]["case"],
            "control": card["contrast"]["control"],
            "method": "python_demo_welch_like_effect_screen",
            "p_adjustment": "benjamini_hochberg",
            "batch_covariates": [],
            "formal_method_available": "limma via scripts/r/bulk_limma_deg.R when local R dependencies are installed",
        },
        "expected_outputs": [
            f"{out_dir}/deg_results.tsv",
            f"{out_dir}/qc_summary.tsv",
            f"{out_dir}/run_manifest.json",
        ],
        "qc_checks": [
            "expression sample columns match metadata sample_id values",
            "case and control labels are present in metadata.group",
            "case_n >= 3 and control_n >= 3 preferred for MVP analysis",
            "run_manifest records input hashes",
        ],
        "assumptions": [
            "Rows are gene-level expression values keyed by gene_symbol.",
            "The MVP Python DEG is association-only and not a clinical/causal test.",
        ],
        "limitations": card.get("known_limitations", []),
        "downstream": ["annotation", "evidence_import", "scoring", "report"],
        "allowed_files": [
            "targetcompass_lite/deg.py",
            f"projects/{project_dir.name}/results/**",
        ],
    }


def _descriptive_module(project_dir: Path, row: dict, card: dict) -> dict:
    dataset_id = row["dataset_id"]
    return {
        "module_id": f"P3_descriptive_evidence_{dataset_id}",
        "module": "descriptive_evidence",
        "dataset_id": dataset_id,
        "objective": "Keep the dataset as contextual/descriptive evidence without running DEG.",
        "status": "planned",
        "runner": "manual_review",
        "command": "",
        "inputs": {
            "dataset_card": f"dataset_cards/{dataset_id}.yaml",
        },
        "parameters": {
            "recommended_use": _split_csv(row.get("recommended_use", "")),
            "blocked_use": card.get("blocked_use", []),
        },
        "expected_outputs": [
            "dataset_match_report.md",
            "reports/target_report.html",
        ],
        "qc_checks": [
            "dataset limitations are explicitly carried into the report",
            "no causal or DEG claim is made from descriptive-only evidence",
        ],
        "assumptions": [
            "The dataset can inform context but is not eligible for formal MVP computation.",
        ],
        "limitations": card.get("known_limitations", []),
        "downstream": ["report"],
        "allowed_files": [
            f"projects/{project_dir.name}/dataset_cards/{dataset_id}.yaml",
            f"projects/{project_dir.name}/reports/**",
        ],
    }


def _work_order_text(module: dict) -> str:
    def bullets(items: list[str]) -> list[str]:
        return [f"- {item}" for item in items] if items else ["- none"]

    lines = [
        f"# WorkOrder: {module['module_id']}",
        "## Objective",
        module["objective"],
        "## Dataset",
        f"- dataset_id: {module['dataset_id']}",
        f"- module: {module['module']}",
        f"- status: {module['status']}",
        "## Inputs",
    ]
    lines.extend(f"- {key}: {value}" for key, value in module["inputs"].items())
    lines.extend(
        [
            "## Parameters",
            "```json",
            json.dumps(module["parameters"], indent=2, ensure_ascii=False),
            "```",
            "## Expected Outputs",
        ]
    )
    lines.extend(bullets(module["expected_outputs"]))
    lines.append("## QC Checks")
    lines.extend(bullets(module["qc_checks"]))
    lines.append("## Assumptions")
    lines.extend(bullets(module["assumptions"]))
    lines.append("## Limitations")
    lines.extend(bullets(module["limitations"]))
    lines.append("## Allowed Files")
    lines.extend(bullets(module["allowed_files"]))
    lines.append("## Command")
    lines.append(module["command"] or "Manual review; no automated command.")
    lines.append("## Downstream")
    lines.extend(bullets(module["downstream"]))
    return "\n".join(lines) + "\n"


def build_plan(project_dir: Path) -> dict:
    eligible_path = project_dir / "eligible_datasets.csv"
    if not eligible_path.exists():
        raise FileNotFoundError("Run screen before plan.")
    datasets = []
    modules = []
    with eligible_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            card = load_dataset_card(Path(row["path"]))
            datasets.append(_dataset_entry(row, card))
            if row["modality"] == "bulk_expression" and row["grade"] in {"A", "B"}:
                modules.append(_bulk_deg_module(project_dir, row, card))
            elif row["grade"] == "C":
                modules.append(_descriptive_module(project_dir, row, card))
    plan = {
        "plan_version": "0.3",
        "project_id": project_dir.name,
        "module_registry": "analysis_module_registry.json",
        "available_modules": ANALYSIS_MODULES,
        "datasets": datasets,
        "modules": modules,
        "execution_order": [module["module_id"] for module in modules],
        "expected_outputs": [
            "results/*/deg_results.tsv",
            "results/annotation/accessibility_annotation.tsv",
            "evidence.sqlite",
            "candidate_scores.csv",
            "reports/target_report.html",
            "reports/target_report.docx",
        ],
        "blocking_conditions": [
            "no eligible datasets",
            "missing matrix",
            "sample metadata mismatch",
            "unsupported or low-confidence research direction",
            "dataset/spec low match requiring review",
        ],
    }
    write_module_registry(project_dir)
    (project_dir / "analysis_plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    work_orders = project_dir / "work_orders"
    work_orders.mkdir(exist_ok=True)
    for module in modules:
        (work_orders / f"{module['module_id']}.md").write_text(_work_order_text(module), encoding="utf-8")
    build_v4_manifest(project_dir, plan)
    return plan
