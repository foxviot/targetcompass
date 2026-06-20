import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AGENT_ROLES = [
    {
        "role_id": "disease_normalizer",
        "stage": "generation",
        "worker": "gpt_or_rule_based",
        "purpose": "Normalize the user research request into DiseaseSpec and ResearchSpec boundaries.",
        "input_refs": ["research_interest.md"],
        "output_refs": ["research_spec.json", "v4/disease_spec.json"],
        "schema": "ResearchSpec",
    },
    {
        "role_id": "dataset_scout",
        "stage": "verification",
        "worker": "gpt_guided_local_tools",
        "purpose": "Find and qualify datasets without approving the analysis plan.",
        "input_refs": ["research_spec.json", "dataset_cards/*.yaml"],
        "output_refs": ["dataset_match_report.csv", "results/geo_discovery/geo_recommendations.json"],
        "schema": "DatasetCandidate",
    },
    {
        "role_id": "planner",
        "stage": "verification",
        "worker": "deterministic_planner",
        "purpose": "Select registered modules and compile AnalysisPlan.",
        "input_refs": ["eligible_datasets.csv", "analysis_module_registry.json"],
        "output_refs": ["analysis_plan.json", "v4/work_orders.json"],
        "schema": "AnalysisPlan",
    },
    {
        "role_id": "method_reviewer",
        "stage": "initial_review",
        "worker": "gpt_or_local_audit",
        "purpose": "Review ResearchSpec readiness, method fit, and work order safety before execution.",
        "input_refs": ["research_spec.json", "analysis_plan.json", "v4/work_orders.json"],
        "output_refs": ["results/review_queue.json", "results/review_actions.jsonl"],
        "schema": "ReviewDecision",
    },
    {
        "role_id": "result_reviewer",
        "stage": "final_review",
        "worker": "gpt_or_local_audit",
        "purpose": "Review QC, rejected rows, hard gates, and unknown annotations.",
        "input_refs": ["results/*/qc_summary.json", "results/evidence_import/import_summary.json", "candidate_scores.csv"],
        "output_refs": ["reports/target_report_structured.json"],
        "schema": "ReviewDecision",
    },
    {
        "role_id": "causal_reviewer",
        "stage": "final_review",
        "worker": "reserved",
        "purpose": "Grade GWAS/QTL/coloc/MR evidence when genetic modules are available.",
        "input_refs": ["results/genetics/*"],
        "output_refs": ["results/causal_review.json"],
        "schema": "CausalReviewDecision",
    },
    {
        "role_id": "report_writer",
        "stage": "report",
        "worker": "deterministic_writer",
        "purpose": "Write report only from accepted/flagged evidence, scores, and audit records.",
        "input_refs": ["evidence.sqlite", "candidate_scores.csv", "results/scoring/target_score_manifest.json"],
        "output_refs": ["reports/target_report.html", "reports/target_report_structured.json"],
        "schema": "Report",
    },
]


def role_manifest_path(project_dir: Path) -> Path:
    return project_dir / "v4" / "agent_roles.json"


def write_agent_role_manifest(project_dir: Path, observations: dict[str, Any] | None = None) -> dict[str, Any]:
    observations = observations or {}
    decisions = []
    for role in AGENT_ROLES:
        payload = {
            **role,
            "status": _role_status(project_dir, role),
            "decision_id": _decision_id(project_dir, role["role_id"], observations.get(role["role_id"], {})),
            "observations": observations.get(role["role_id"], {}),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        decisions.append(payload)
    manifest = {
        "schema_version": "v4.agent_roles/0.1",
        "project_id": project_dir.name,
        "policy": {
            "generator_cannot_approve_itself": True,
            "scoring_engine_is_deterministic": True,
            "role_outputs_must_be_schema_validated": True,
        },
        "roles": decisions,
    }
    path = role_manifest_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def _role_status(project_dir: Path, role: dict[str, Any]) -> str:
    outputs = role.get("output_refs", [])
    existing = 0
    for ref in outputs:
        if "*" in ref:
            if list(project_dir.glob(ref)):
                existing += 1
        elif (project_dir / ref).exists():
            existing += 1
    if existing == len(outputs):
        return "complete"
    if existing:
        return "partial"
    return "pending"


def _decision_id(project_dir: Path, role_id: str, observation: dict[str, Any]) -> str:
    payload = json.dumps({"project": project_dir.name, "role": role_id, "observation": observation}, sort_keys=True, ensure_ascii=False)
    return "decision_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
