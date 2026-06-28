import csv
import json
from pathlib import Path
from typing import Any

from .analysis_modules import ANALYSIS_MODULES, write_module_registry
from .evidence_planning import build_compatibility_decisions
from .schema_validation import load_schema, validate_object
from .validators import load_dataset_card
from .v4 import build_v4_manifest, content_hash


ANALYSIS_PLAN_SCHEMA_VERSION = "v0.2.evidence_driven_analysis_plan"
CODEX_TASK_PACKET_SCHEMA_VERSION = "v0.2.codex_task_packet"


METHOD_TO_MODULE = {
    "bulk_deg_limma_or_countlike_v1": "bulk_deg",
    "scrna_pseudobulk_deg_v1": "scrna_pseudobulk",
    "sasp_score_from_deg_v1": "sasp_score",
    "surface_secretome_annotation_v1": "annotation",
    "cell_type_evidence_v1": "cell_type_evidence",
}


METHOD_COMMANDS = {
    "bulk_deg_limma_or_countlike_v1": "run-deg --dataset {dataset_id}",
    "scrna_pseudobulk_deg_v1": "run-scrna-pseudobulk --dataset {dataset_id}",
    "sasp_score_from_deg_v1": "sasp-score",
    "surface_secretome_annotation_v1": "annotate",
    "cell_type_evidence_v1": "cell-type-evidence",
}


METHOD_GOALS = {
    "bulk_deg_limma_or_countlike_v1": "Run dataset-bound bulk differential expression after metadata and sample alignment checks.",
    "scrna_pseudobulk_deg_v1": "Run donor-aware scRNA/snRNA pseudobulk analysis without treating cells as biological replicates.",
    "sasp_score_from_deg_v1": "Compute SASP program evidence from reviewed upstream DEG outputs.",
    "surface_secretome_annotation_v1": "Annotate candidate genes for secreted, surface, ECD, and plasma membrane accessibility.",
    "cell_type_evidence_v1": "Link candidate genes to cell and tissue context with explicit provenance and claim limits.",
}


FORBIDDEN_ACTIONS = [
    "do not redefine the research question",
    "do not invent missing metadata, labels, matrices, or candidate genes",
    "do not change case/control labels without producing a repair task",
    "do not silently drop samples or genes",
    "do not make final biological conclusions beyond this task packet",
]


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
        "method_id": "bulk_deg_limma_or_countlike_v1",
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
            f"{out_dir}/executor_manifest.json",
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
        "method_id": "descriptive_evidence",
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
    lines.extend(bullets(module.get("assumptions", [])))
    lines.append("## Limitations")
    lines.extend(bullets(module.get("limitations", [])))
    lines.append("## Allowed Files")
    lines.extend(bullets(module.get("allowed_files", [])))
    lines.append("## Command")
    lines.append(module.get("command") or "Manual review; no automated command.")
    lines.append("## Downstream")
    lines.extend(bullets(module.get("downstream", [])))
    return "\n".join(lines) + "\n"


def build_plan(project_dir: Path) -> dict:
    if _can_build_evidence_driven_plan(project_dir):
        return _build_evidence_driven_plan(project_dir)
    return _build_legacy_plan(project_dir)


def _can_build_evidence_driven_plan(project_dir: Path) -> bool:
    return (project_dir / "research_spec.json").exists() and (project_dir / "dataset_cards").exists()


def _build_evidence_driven_plan(project_dir: Path) -> dict:
    compatibility = build_compatibility_decisions(project_dir)
    decisions = compatibility.get("decisions", [])
    profiles = {
        row.get("dataset_id", ""): row
        for row in _read_json(project_dir / "results" / "evidence_planning" / "dataset_profiles.json", {}).get("profiles", [])
    }
    contracts = {
        row.get("method_id", ""): row
        for row in _read_json(project_dir / "results" / "evidence_planning" / "method_contracts.json", {}).get("methods", [])
    }
    routes: list[dict[str, Any]] = []
    modules: list[dict[str, Any]] = []
    repair_tasks: list[dict[str, Any]] = []
    codex_packets: list[dict[str, Any]] = []

    pass_by_dataset: dict[str, list[dict[str, Any]]] = {}
    for decision in decisions:
        status = decision.get("decision", "")
        if status == "pass":
            pass_by_dataset.setdefault(decision.get("dataset_id", ""), []).append(decision)
        elif status == "repairable":
            repair_tasks.append(_repair_task(project_dir, decision, profiles.get(decision.get("dataset_id", ""), {})))

    for dataset_id in sorted(pass_by_dataset):
        route_decisions = sorted(pass_by_dataset[dataset_id], key=lambda row: _method_rank(row.get("method_id", "")))
        route_modules = []
        route_packets = []
        for decision in route_decisions:
            contract = contracts.get(decision.get("method_id", ""), {})
            profile = profiles.get(dataset_id, {})
            module = _module_from_decision(project_dir, decision, profile, contract)
            modules.append(module)
            route_modules.append(module["module_id"])
            packet = _task_packet_from_module(project_dir, module, decision, profile, contract)
            codex_packets.append(packet)
            route_packets.append(packet["task_id"])
        routes.append(
            {
                "route_id": f"route_{dataset_id}",
                "dataset_id": dataset_id,
                "source": profile.get("source", ""),
                "assay": profile.get("assay", ""),
                "decision_source": "CompatibilityDecision",
                "steps": route_modules,
                "task_packets": route_packets,
                "claim_limits": _dedupe([row.get("claim_limit", "") for row in route_decisions]),
                "warnings": _dedupe([warning for row in route_decisions for warning in row.get("warnings", [])]),
            }
        )

    if not routes and repair_tasks:
        routes.append(
            {
                "route_id": "repair_before_analysis",
                "dataset_id": "",
                "source": "",
                "assay": "",
                "decision_source": "CompatibilityDecision",
                "steps": [],
                "task_packets": [],
                "claim_limits": ["No formal analysis route until repair tasks pass compatibility checks."],
                "warnings": ["Only repairable datasets/methods were found."],
            }
        )

    plan = {
        "schema_version": ANALYSIS_PLAN_SCHEMA_VERSION,
        "plan_version": "0.4",
        "analysis_plan_id": "ap_" + content_hash({"project": project_dir.name, "compatibility": compatibility.get("summary", {})})[:16],
        "project_id": project_dir.name,
        "route_strategy": "evidence_plan_plus_dataset_method_compatibility",
        "evidence_plan_ref": "results/evidence_planning/evidence_plan.json",
        "dataset_profiles_ref": "results/evidence_planning/dataset_profiles.json",
        "method_contracts_ref": "results/evidence_planning/method_contracts.json",
        "compatibility_decisions_ref": "results/evidence_planning/compatibility_decisions.json",
        "module_registry": "analysis_module_registry.json",
        "available_modules": ANALYSIS_MODULES,
        "datasets": list(profiles.values()),
        "routes": routes,
        "repair_tasks": repair_tasks,
        "codex_task_packets": codex_packets,
        "modules": modules,
        "execution_order": [module["module_id"] for module in modules],
        "expected_outputs": _dedupe([output for module in modules for output in module.get("expected_outputs", [])]),
        "blocking_conditions": [
            "no CompatibilityDecision pass route",
            "repairable decisions require metadata/data correction before formal analysis",
            "failed compatibility decisions must not be executed by changing method assumptions",
            "Codex may execute only task packet scope and must not redefine scientific claims",
        ],
    }
    _validate_payload(plan, "analysis_plan.schema.json", "AnalysisPlan")
    for packet in codex_packets:
        _validate_payload(packet, "codex_task_packet.schema.json", "CodexTaskPacket")
    _write_plan_outputs(project_dir, plan, modules, codex_packets)
    build_v4_manifest(project_dir, plan)
    return plan


def _build_legacy_plan(project_dir: Path) -> dict:
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
    routes = [
        {
            "route_id": f"legacy_route_{module['dataset_id']}_{module['module']}",
            "dataset_id": module["dataset_id"],
            "source": "eligible_datasets.csv",
            "assay": module["module"],
            "decision_source": "legacy_screening",
            "steps": [module["module_id"]],
            "task_packets": [],
            "claim_limits": module.get("assumptions", []),
            "warnings": module.get("limitations", []),
        }
        for module in modules
    ]
    plan = {
        "schema_version": ANALYSIS_PLAN_SCHEMA_VERSION,
        "plan_version": "0.4",
        "analysis_plan_id": "ap_" + content_hash({"project": project_dir.name, "modules": modules})[:16],
        "project_id": project_dir.name,
        "route_strategy": "legacy_screening_fallback",
        "module_registry": "analysis_module_registry.json",
        "available_modules": ANALYSIS_MODULES,
        "datasets": datasets,
        "routes": routes,
        "repair_tasks": [],
        "codex_task_packets": [],
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
    _validate_payload(plan, "analysis_plan.schema.json", "AnalysisPlan")
    _write_plan_outputs(project_dir, plan, modules, [])
    build_v4_manifest(project_dir, plan)
    return plan


def _module_from_decision(project_dir: Path, decision: dict[str, Any], profile: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    dataset_id = decision.get("dataset_id", "")
    method_id = decision.get("method_id", "")
    module = METHOD_TO_MODULE.get(method_id, "unknown_method")
    module_id = f"ED_{module}_{dataset_id}"
    inputs = _inputs_for_method(project_dir, profile, method_id)
    params = dict(decision.get("recommended_parameters", {}) or {})
    params.setdefault("method_contract_id", method_id)
    params.setdefault("compatibility_decision", decision.get("decision", ""))
    outputs = _outputs_for_method(dataset_id, method_id, contract)
    command = ""
    if method_id == "bulk_deg_limma_or_countlike_v1":
        command = f"python tc_lite.py run-deg --project {project_dir.name} --dataset {dataset_id}"
    if method_id == "sasp_score_from_deg_v1":
        command = f"python tc_lite.py sasp-score --project {project_dir.name}"
    if method_id == "surface_secretome_annotation_v1":
        command = f"python tc_lite.py annotate --project {project_dir.name}"
    if method_id == "cell_type_evidence_v1":
        command = f"python tc_lite.py cell-type-evidence --project {project_dir.name}"
    return {
        "module_id": module_id,
        "module": module,
        "dataset_id": dataset_id,
        "method_id": method_id,
        "objective": METHOD_GOALS.get(method_id, contract.get("purpose", "")),
        "status": "planned",
        "runner": contract.get("runner", ""),
        "runner_type": "local_executor_or_task_packet",
        "command": command,
        "inputs": inputs,
        "parameters": params,
        "expected_outputs": outputs,
        "qc_checks": contract.get("qc_checks", []) or ["executor manifest is recorded"],
        "assumptions": [decision.get("claim_limit", "")] if decision.get("claim_limit") else [],
        "limitations": decision.get("warnings", []),
        "downstream": _downstream_for_method(method_id),
        "allowed_files": [
            f"projects/{project_dir.name}/dataset_cards/{dataset_id}.yaml",
            f"projects/{project_dir.name}/data/**",
            f"projects/{project_dir.name}/results/**",
        ],
        "compatibility": {
            "decision": decision.get("decision", ""),
            "matched_requirements": decision.get("matched_requirements", []),
            "unmet_requirements": decision.get("unmet_requirements", []),
            "next_actions": decision.get("next_actions", []),
        },
    }


def _task_packet_from_module(project_dir: Path, module: dict[str, Any], decision: dict[str, Any], profile: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    task_id = "ctp_" + content_hash({"module": module["module_id"], "decision": decision})[:16]
    outputs = module.get("expected_outputs", [])
    inputs = module.get("inputs", {})
    return {
        "schema_version": CODEX_TASK_PACKET_SCHEMA_VERSION,
        "task_id": task_id,
        "name": module["module_id"],
        "purpose": module["objective"],
        "goal": module["objective"],
        "dataset": {
            "dataset_id": module.get("dataset_id", ""),
            "source": profile.get("source", ""),
            "accession": profile.get("accession", ""),
            "data_type": profile.get("assay", ""),
            "species": profile.get("species", ""),
            "tissue": profile.get("tissue", ""),
        },
        "inputs": inputs,
        "method": {
            "method_contract_id": module.get("method_id", ""),
            "name": contract.get("method_name", module.get("module", "")),
            "runner": module.get("runner", ""),
            "parameters": module.get("parameters", {}),
            "claim_limit": decision.get("claim_limit", ""),
        },
        "expected_outputs": outputs,
        "acceptance_criteria": module.get("qc_checks", []) + [
            "all expected outputs are either produced or explicitly marked not applicable",
            "failure reason is structured if the task cannot run",
        ],
        "failure_condition": "Any required input is missing, metadata alignment fails, or QC cannot substantiate the method contract.",
        "forbidden_actions": FORBIDDEN_ACTIONS,
        "dependencies": _dependencies_for_method(module.get("method_id", ""), module.get("dataset_id", "")),
        "method_contract_id": module.get("method_id", ""),
        "input_artifacts": [str(value) for value in inputs.values() if value],
        "output_artifacts": outputs,
        "notes": "Generated from EvidencePlan + DatasetProfile + MethodContract + CompatibilityDecision; Codex executes only this small task scope.",
    }


def _repair_task(project_dir: Path, decision: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    dataset_id = decision.get("dataset_id", "")
    method_id = decision.get("method_id", "")
    return {
        "task_id": f"repair_{dataset_id}_{method_id}",
        "dataset_id": dataset_id,
        "method_id": method_id,
        "decision": decision.get("decision", ""),
        "missing": decision.get("unmet_requirements", []),
        "warnings": decision.get("warnings", []),
        "next_actions": decision.get("next_actions", []),
        "allowed_scope": [
            f"projects/{project_dir.name}/dataset_cards/{dataset_id}.yaml",
            f"projects/{project_dir.name}/data/**",
            f"projects/{project_dir.name}/results/evidence_planning/**",
        ],
        "profile_ref": f"DatasetProfile:{profile.get('dataset_id', dataset_id)}",
    }


def _inputs_for_method(project_dir: Path, profile: dict[str, Any], method_id: str) -> dict[str, Any]:
    dataset_id = profile.get("dataset_id", "")
    paths = profile.get("file_paths", {}) or {}
    common = {"dataset_card": f"dataset_cards/{dataset_id}.yaml"}
    if method_id == "bulk_deg_limma_or_countlike_v1":
        return {**common, "expression_matrix": paths.get("expression_matrix", ""), "metadata": paths.get("metadata", "")}
    if method_id == "scrna_pseudobulk_deg_v1":
        return {**common, "count_matrix": paths.get("expression_matrix", ""), "metadata": paths.get("metadata", "")}
    if method_id == "sasp_score_from_deg_v1":
        return {**common, "deg_results": f"results/bulk_deg_{dataset_id}/deg_results.tsv"}
    if method_id == "surface_secretome_annotation_v1":
        return {**common, "candidate_genes": "results/*/deg_results.tsv", "annotation_sources": "knowledge_base/annotations"}
    if method_id == "cell_type_evidence_v1":
        return {**common, "candidate_genes": "results/*/deg_results.tsv", "cell_type_sources": "knowledge_base/annotations"}
    return common


def _outputs_for_method(dataset_id: str, method_id: str, contract: dict[str, Any]) -> list[str]:
    if method_id == "bulk_deg_limma_or_countlike_v1":
        return [
            f"results/bulk_deg_{dataset_id}/deg_results.tsv",
            f"results/bulk_deg_{dataset_id}/qc_summary.tsv",
            f"results/bulk_deg_{dataset_id}/run_manifest.json",
            f"results/bulk_deg_{dataset_id}/executor_manifest.json",
        ]
    if method_id == "scrna_pseudobulk_deg_v1":
        return [
            f"results/scrna_pseudobulk_{dataset_id}/pseudobulk_matrix.tsv",
            f"results/scrna_pseudobulk_{dataset_id}/pseudobulk_metadata.tsv",
            f"results/scrna_pseudobulk_{dataset_id}/qc_summary.json",
            f"results/scrna_pseudobulk_{dataset_id}/run_manifest.json",
        ]
    if method_id == "sasp_score_from_deg_v1":
        return ["results/sasp_score/sasp_gene_scores.tsv", "results/sasp_score/sasp_dataset_scores.tsv", "results/sasp_score/run_manifest.json"]
    if method_id == "surface_secretome_annotation_v1":
        return ["results/annotation/accessibility_annotation.tsv", "results/annotation/unknown_review.tsv"]
    if method_id == "cell_type_evidence_v1":
        return ["results/cell_type_evidence/cell_type_evidence.tsv", "results/cell_type_evidence/cell_type_summary.json"]
    return list(contract.get("outputs", []) or ["results/task_output"])


def _downstream_for_method(method_id: str) -> list[str]:
    if method_id in {"bulk_deg_limma_or_countlike_v1", "scrna_pseudobulk_deg_v1"}:
        return ["sasp_score", "annotation", "cell_type_evidence", "evidence_import"]
    if method_id in {"sasp_score_from_deg_v1", "surface_secretome_annotation_v1", "cell_type_evidence_v1"}:
        return ["evidence_import", "scoring", "report"]
    return ["review"]


def _dependencies_for_method(method_id: str, dataset_id: str) -> list[str]:
    if method_id == "sasp_score_from_deg_v1":
        return [f"ED_bulk_deg_{dataset_id}", f"ED_scrna_pseudobulk_{dataset_id}"]
    if method_id in {"surface_secretome_annotation_v1", "cell_type_evidence_v1"}:
        return [f"ED_bulk_deg_{dataset_id}", f"ED_sasp_score_{dataset_id}"]
    return []


def _method_rank(method_id: str) -> int:
    order = {
        "bulk_deg_limma_or_countlike_v1": 10,
        "scrna_pseudobulk_deg_v1": 10,
        "sasp_score_from_deg_v1": 20,
        "surface_secretome_annotation_v1": 30,
        "cell_type_evidence_v1": 40,
    }
    return order.get(method_id, 99)


def _write_plan_outputs(project_dir: Path, plan: dict[str, Any], modules: list[dict[str, Any]], packets: list[dict[str, Any]]) -> None:
    write_module_registry(project_dir)
    (project_dir / "analysis_plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    out_dir = project_dir / "results" / "evidence_planning"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "analysis_plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "codex_task_packets.json").write_text(
        json.dumps({"schema_version": "v0.2.codex_task_packets", "project_id": project_dir.name, "packets": packets}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    work_orders = project_dir / "work_orders"
    work_orders.mkdir(exist_ok=True)
    for module in modules:
        (work_orders / f"{module['module_id']}.md").write_text(_work_order_text(module), encoding="utf-8")


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_payload(payload: dict[str, Any], schema_name: str, label: str) -> None:
    errors = validate_object(payload, load_schema(schema_name), label)
    if errors:
        raise ValueError("; ".join(errors))


def _dedupe(items: list[str]) -> list[str]:
    out = []
    seen = set()
    for item in items:
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return out
