from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .backend_writer import write_json_artifact
from .nextflow_execution import load_task_runs
from .schemas import now_iso


FAILURE_RECOVERY_SCHEMA_VERSION = "v5.failure_recovery/0.1"


def build_v5_failure_recovery_report(project_dir: str | Path, *, write: bool = True) -> dict[str, Any]:
    project_dir = Path(project_dir)
    resource_bundle = _read_json(project_dir / "v5" / "resource_discovery" / "resource_discovery_bundle.json", {})
    local_execution = _read_json(project_dir / "v5" / "local_execution" / "local_execution_bundle.json", {})
    alignment = _read_json(project_dir / "v5" / "reports" / "question_alignment_report.json", {})
    report_manifest = _read_json(project_dir / "v5" / "reports" / "canonical_report_manifest.json", {})
    task_runs = load_task_runs(project_dir)

    items: list[dict[str, Any]] = []
    items.extend(_dataset_not_found_items(project_dir, resource_bundle))
    items.extend(_metadata_insufficient_items(project_dir, resource_bundle))
    items.extend(_literature_without_omics_items(project_dir, resource_bundle))
    items.extend(_analysis_mismatch_items(project_dir, task_runs, local_execution))
    items.extend(_claim_ceiling_items(project_dir, alignment, report_manifest))

    report = {
        "schema_version": FAILURE_RECOVERY_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "status": "review_required" if items else "clear",
        "item_count": len(items),
        "items": items,
        "source_refs": {
            "resource_discovery": "v5/resource_discovery/resource_discovery_bundle.json",
            "local_execution": "v5/local_execution/local_execution_bundle.json",
            "question_alignment": "v5/reports/question_alignment_report.json",
            "report_manifest": "v5/reports/canonical_report_manifest.json",
        },
    }
    if write:
        _write_json(project_dir, "v5/recovery/failure_recovery_report.json", report)
    return report


def _dataset_not_found_items(project_dir: Path, bundle: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = bundle.get("resource_candidates", [])
    dataset_candidates = [row for row in candidates if row.get("resource_type") == "dataset"]
    attempted_dataset_sources = [
        row.get("source")
        for row in bundle.get("query_attempts", [])
        if row.get("source") in {"geo", "sra", "arrayexpress", "cellxgene"}
    ]
    if dataset_candidates or not attempted_dataset_sources:
        return []
    return [
        _item(
            item_id="dataset_not_found",
            category="resource_discovery",
            severity="high",
            reason="No dataset candidate was found from GEO/SRA/ArrayExpress/cellxgene searches.",
            impact="The core omics analysis path cannot proceed without a usable dataset or user-supplied data.",
            actions=[
                "Relax disease/tissue/cell-type terms and rerun resource discovery.",
                "Add a known GEO/SRA/ArrayExpress/cellxgene accession manually.",
                "Import local expression matrix and metadata if the public dataset is unavailable.",
            ],
            commands=[
                f"python tc_lite.py v5-run-local --project {project_dir.name} --question \"<broader question>\" --source geo --source sra --source arrayexpress --source cellxgene --limit 10 --control-plane-only",
                f"python tc_lite.py geo-import-auto --project {project_dir.name} --gse <GSE_ID>",
            ],
            refs=["v5/resource_discovery/resource_discovery_bundle.json"],
        )
    ]


def _metadata_insufficient_items(project_dir: Path, bundle: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = {row.get("resource_candidate_id", ""): row for row in bundle.get("dataset_profiles", [])}
    items = []
    for candidate in bundle.get("resource_candidates", []):
        if candidate.get("resource_type") != "dataset":
            continue
        profile = profiles.get(candidate.get("resource_candidate_id", ""), {})
        issues = []
        if candidate.get("verified") is not True or candidate.get("source_status") != "metadata_verified":
            issues.append("metadata_not_verified")
        for field in ["group_metadata_status", "sample_size_status"]:
            if profile.get(field) in {"not_assessed", "unknown", "", None}:
                issues.append(field)
        if profile.get("organism") in {"unknown", "", None}:
            issues.append("organism_unknown")
        if profile.get("tissue") in {"unknown", "", None}:
            issues.append("tissue_unknown")
        if not issues:
            continue
        items.append(
            _item(
                item_id=f"metadata_insufficient:{candidate.get('accession', candidate.get('resource_candidate_id', ''))}",
                category="resource_discovery",
                severity="medium",
                reason=f"Dataset metadata is not analysis-ready: {', '.join(sorted(set(issues)))}.",
                impact="The dataset cannot be locked or routed to bulk/scRNA methods until metadata is corrected.",
                actions=[
                    "Inspect metadata columns and choose case/control/grouping fields.",
                    "Confirm sample size, organism, tissue, and platform.",
                    "If metadata is absent, provide a manual correction file or choose another dataset.",
                ],
                commands=[
                    f"python tc_lite.py v5-resource-gate --project {project_dir.name}",
                    f"python tc_lite.py geo-import-auto --project {project_dir.name} --gse <GSE_ID>",
                ],
                refs=["v5/resource_discovery/resource_discovery_bundle.json", "v5/resource_discovery/resource_gate_report.json"],
            )
        )
    return items


def _literature_without_omics_items(project_dir: Path, bundle: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = bundle.get("resource_candidates", [])
    literature = [row for row in candidates if row.get("resource_type") == "literature"]
    datasets = [row for row in candidates if row.get("resource_type") == "dataset"]
    if not literature or datasets:
        return []
    return [
        _item(
            item_id="literature_without_omics",
            category="resource_discovery",
            severity="medium",
            reason="Literature candidates exist, but no usable omics dataset candidate was found.",
            impact="The system can run literature validation, but should not claim data-driven target discovery from literature alone.",
            actions=[
                "Use literature as validation/background only.",
                "Rerun dataset discovery with broader terms or add a known dataset accession.",
                "Keep report claim ceiling at association/background until omics evidence exists.",
            ],
            commands=[
                f"python tc_lite.py v5-literature-pipeline --project {project_dir.name} --query \"<query>\" --limit 10 --fulltext-limit 3",
                f"python tc_lite.py v5-run-local --project {project_dir.name} --question \"<broader question>\" --source geo --source sra --limit 10 --control-plane-only",
            ],
            refs=["v5/resource_discovery/resource_discovery_bundle.json"],
        )
    ]


def _analysis_mismatch_items(project_dir: Path, task_runs: list[dict[str, Any]], local_execution: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    failed_runs = [row for row in task_runs if str(row.get("result_status", "")).lower() in {"failed", "fail", "review_required"}]
    for row in failed_runs:
        module = str(row.get("module", ""))
        reason = str(row.get("failure_reason", ""))
        text = f"{module} {reason}".lower()
        if not any(term in text for term in ["scrna", "single", "bulk", "metadata", "matrix", "sample", "donor", "group", "mismatch"]):
            continue
        items.append(
            _item(
                item_id=f"analysis_input_mismatch:{row.get('task_run_id', row.get('task_id', 'unknown'))}",
                category="execution",
                severity="high",
                reason=reason or f"{module} task did not complete cleanly.",
                impact="The selected analysis method may not match the available matrix/metadata structure.",
                actions=[
                    "Check whether the dataset is bulk, microarray, scRNA, or snRNA.",
                    "For scRNA, confirm donor, group, and cell-type columns before pseudobulk.",
                    "For bulk, confirm expression matrix rows map to gene symbols and metadata sample IDs match matrix columns.",
                    "Rerun only the failed task after correcting metadata.",
                ],
                commands=[
                    f"python tc_lite.py v5-run-local --project {project_dir.name} --question \"<same question>\" --max-analysis-packets 1",
                    f"python tc_lite.py v5-resource-gate --project {project_dir.name}",
                ],
                refs=[f"v5/task_runs/{row.get('task_run_id', '')}.json"],
            )
        )
    if local_execution.get("failed_count", 0) and not items:
        items.append(
            _item(
                item_id="local_execution_failed",
                category="execution",
                severity="high",
                reason=f"{local_execution.get('failed_count')} local execution task(s) failed.",
                impact="At least one analysis output should not enter evidence scoring until reviewed.",
                actions=["Open local_execution_bundle.json and inspect failed task_results.", "Rerun only the failed module after correction."],
                commands=[f"python tc_lite.py v5-run-local --project {project_dir.name} --question \"<same question>\""],
                refs=["v5/local_execution/local_execution_bundle.json"],
            )
        )
    return items


def _claim_ceiling_items(project_dir: Path, alignment: dict[str, Any], report_manifest: dict[str, Any]) -> list[dict[str, Any]]:
    violations = alignment.get("claim_ceiling_violations") or []
    if not violations:
        return []
    return [
        _item(
            item_id="claim_ceiling_violation",
            category="report",
            severity="critical",
            reason=f"{len(violations)} claim(s) exceed the configured evidence claim ceiling.",
            impact="Report cannot be signed out until claims are downgraded or stronger evidence is added.",
            actions=[
                "Downgrade claims to the current ceiling, usually association/background.",
                "Add stronger omics/genetic/experimental evidence before making causal or therapeutic claims.",
                "Keep human review gate required.",
            ],
            commands=[
                f"python tc_lite.py v5-report-manifest --project {project_dir.name}",
                f"python tc_lite.py approval-signoff --project {project_dir.name} --decision needs_revision --reason \"claim ceiling violation\"",
            ],
            refs=["v5/reports/question_alignment_report.json", "v5/reports/canonical_report_manifest.json"],
            extra={"violations": violations, "human_review_gate": report_manifest.get("human_review_gate", {})},
        )
    ]


def _item(
    *,
    item_id: str,
    category: str,
    severity: str,
    reason: str,
    impact: str,
    actions: list[str],
    commands: list[str],
    refs: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {
        "item_id": item_id,
        "category": category,
        "severity": severity,
        "reason": reason,
        "impact": impact,
        "recovery_actions": actions,
        "rerun_commands": commands,
        "source_refs": refs,
        "status": "open",
    }
    if extra:
        row.update(extra)
    return row


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _write_json(project_dir: Path, relative_path: str, payload: dict[str, Any]) -> None:
    write_json_artifact(project_dir, relative_path, payload, producer="v5_failure_recovery", artifact_type="failure_recovery_report")
