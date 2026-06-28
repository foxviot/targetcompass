import argparse
import json
import shutil
import sys
from pathlib import Path

from .annotation import annotate_project
from .agent_roles import write_agent_role_manifest
from .deg import run_deg
from .enrichment import run_enrichment
from .evidence_planning import build_compatibility_decisions, build_dataset_profiles, build_evidence_plan, build_evidence_planning_bundle, build_method_contracts
from .evidence_db import import_evidence
from .causal_evidence import grade_causal_evidence
from .matching import match_project
from .meta_analysis import run_meta_analysis
from .paths import ensure_project_dirs, project_path
from .planning import build_plan
from .reporting import build_report
from .role_runner import run_role
from .sasp_score import run_sasp_score
from .scoring import score_project
from .screening import screen_project
from .spec_builder import confirm_project_spec, readiness_errors, update_project_spec
from .validators import validate_dataset_card, validate_research_spec
from .v4 import finish_work_order_attempt, start_work_order_attempt


def init_project(project: str) -> Path:
    p = project_path(project)
    ensure_project_dirs(p)
    if not (p / "research_interest.md").exists():
        (p / "research_interest.md").write_text(
            "Prioritize candidate molecules for aging immunity and vascular aging research.\n",
            encoding="utf-8",
        )
    if not (p / "research_spec.json").exists():
        (p / "research_spec.json").write_text(
            json.dumps(
                {
                    "project_id": p.name,
                    "goal": "vaccine_candidate_target_prioritization",
                    "research_theme": "aging immunity and vascular aging",
                    "disease_scope": {
                        "canonical": "vascular aging",
                        "related_phenotypes": ["endothelial senescence", "arterial stiffness", "vascular inflammaging"],
                    },
                    "organisms": ["human", "mouse"],
                    "priority_tissues": ["artery", "vascular endothelium", "blood"],
                    "priority_cells": ["endothelial cell", "monocyte", "macrophage", "T cell"],
                    "target_routes": ["surface", "secreted", "ECD", "T_cell_peptide"],
                    "modalities_mvp": {
                        "required": ["bulk_expression", "accessibility_annotation", "safety_annotation"],
                        "optional": ["enrichment", "manual_genetic_evidence"],
                    },
                    "constraints": {
                        "causal_requirement": "preferred_not_mandatory",
                        "critical_normal_tissues": ["brain", "heart", "liver", "kidney", "hematopoietic_stem_cell"],
                        "claim_policy": "association_only_without_genetic_or_experimental_validation",
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return p


def _print_errors(errors: list[str]) -> int:
    if errors:
        for err in errors:
            print(f"ERROR: {err}")
        return 1
    print("OK")
    return 0


def _has_data_rows(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8") as f:
        next(f, None)
        return any(line.strip() for line in f)


def cmd_demo(args) -> int:
    p = init_project(args.project)
    selected = set(getattr(args, "dataset", []) or []) or None
    rc = _print_errors(validate_research_spec(p / "research_spec.json"))
    if rc:
        return rc
    spec = json.loads((p / "research_spec.json").read_text(encoding="utf-8"))
    run_role(
        p,
        "disease_normalizer",
        {"research_spec": "research_spec.json", "source": "demo_existing_spec"},
        lambda: spec,
        runner="tc_lite.demo:existing_research_spec",
    )
    ready_errors = readiness_errors(spec)
    if ready_errors:
        for err in ready_errors:
            print(f"ERROR: {err}")
        return 1
    card_paths = sorted((p / "dataset_cards").glob("*.yaml"))
    if selected is not None:
        available = {path.stem for path in card_paths}
        missing = sorted(selected - available)
        if missing:
            for dataset_id in missing:
                print(f"ERROR: selected dataset not found: {dataset_id}")
            return 1
        card_paths = [path for path in card_paths if path.stem in selected]
    if not card_paths:
        print("ERROR: no dataset cards selected")
        return 1
    if selected is not None:
        for out_dir in (p / "results").glob("bulk_deg_*"):
            dataset_id = out_dir.name.replace("bulk_deg_", "")
            if dataset_id not in selected:
                shutil.rmtree(out_dir)
    for card in card_paths:
        rc = _print_errors(validate_dataset_card(card))
        if rc:
            return rc
    rows = screen_project(p, selected)
    print(f"screened {len(rows)} dataset card(s)")
    matches = match_project(p, selected)
    planning_bundle = build_evidence_planning_bundle(p, selected)
    print(f"built evidence plan and {planning_bundle['profile_count']} dataset profile(s)")
    run_role(
        p,
        "dataset_scout",
        {"selected_datasets": sorted(selected) if selected else "all", "screening_report": "screening_report.md"},
        lambda: {"screened": len(rows), "matched": len(matches)},
        runner="tc_lite.demo:screen_and_match",
    )
    review_count = sum(1 for row in matches if row["match_status"] != "MATCH")
    print(f"matched {len(matches)} dataset card(s); {review_count} require review")
    plan, _ = run_role(
        p,
        "planner",
        {"eligible_datasets": "eligible_datasets.csv", "selected_datasets": sorted(selected) if selected else "all"},
        lambda: build_plan(p),
        runner="targetcompass_lite.planning.build_plan",
    )
    print(f"planned {len(plan['modules'])} module(s)")
    for module in plan["modules"]:
        if module["module"] == "bulk_deg":
            attempt = start_work_order_attempt(p, module["module_id"], "demo_run")
            try:
                result_path = run_deg(p, module["dataset_id"])
                finish_work_order_attempt(
                    p,
                    attempt["attempt_id"],
                    "success",
                    [
                        str(result_path.relative_to(p)),
                        f"results/bulk_deg_{module['dataset_id']}/qc_summary.json",
                        f"results/bulk_deg_{module['dataset_id']}/run_manifest.json",
                    ],
                )
            except Exception as exc:
                finish_work_order_attempt(p, attempt["attempt_id"], "failed", failure_reason=str(exc))
                raise
            print(f"ran DEG for {module['dataset_id']}")
        else:
            print(f"planned {module['module']} for {module['dataset_id']}")
    enrichment_path = run_enrichment(p)
    print(f"ran enrichment: {enrichment_path}")
    meta_path = run_meta_analysis(p)
    print(f"ran meta-analysis: {meta_path}")
    annotate_project(p)
    import_evidence(p)
    causal_path = grade_causal_evidence(p)
    print(f"ran causal evidence grading: {causal_path}")
    if _has_data_rows(causal_path):
        import_evidence(p)
    score_project(p)
    run_role(
        p,
        "result_reviewer",
        {"candidate_scores": "candidate_scores.csv", "qc": "results/*/qc_summary.json"},
        lambda: {"candidate_scores": "candidate_scores.csv", "review_queue": "results/review_queue.json"},
        runner="tc_lite.demo:result_review_summary",
    )
    html_path, docx_path = run_role(
        p,
        "report_writer",
        {"evidence_db": "evidence.sqlite", "scores": "candidate_scores.csv"},
        lambda: build_report(p),
        runner="targetcompass_lite.reporting.build_report",
    )[0]
    print(f"report: {html_path}")
    print(f"word-compatible report: {docx_path}")
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="tc_lite.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--project", required=True)
    p = sub.add_parser("validate-spec")
    p.add_argument("path")
    p = sub.add_parser("validate-dataset-card")
    p.add_argument("path")
    p = sub.add_parser("screen")
    p.add_argument("--project", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--project", required=True)
    p = sub.add_parser("evidence-plan")
    p.add_argument("--project", required=True)
    p = sub.add_parser("dataset-profile")
    p.add_argument("--project", required=True)
    p.add_argument("--dataset", action="append", default=[])
    p = sub.add_parser("method-contracts")
    p.add_argument("--project", required=True)
    p = sub.add_parser("compatibility")
    p.add_argument("--project", required=True)
    p.add_argument("--dataset", action="append", default=[])
    p = sub.add_parser("match-datasets")
    p.add_argument("--project", required=True)
    p = sub.add_parser("run-deg")
    p.add_argument("--project", required=True)
    p.add_argument("--dataset", required=True)
    p = sub.add_parser("annotate")
    p.add_argument("--project", required=True)
    p = sub.add_parser("enrichment")
    p.add_argument("--project", required=True)
    p = sub.add_parser("scrna-pseudobulk")
    p.add_argument("--project", required=True)
    p.add_argument("--dataset-id", required=True)
    p.add_argument("--count-matrix", required=True)
    p.add_argument("--metadata", required=True)
    p.add_argument("--cell-type", default="")
    p.add_argument("--donor-column", default="donor_id")
    p.add_argument("--group-column", default="group")
    p.add_argument("--cell-type-column", default="cell_type")
    p.add_argument("--min-cells-per-donor", type=int, default=1)
    p.add_argument("--min-donors-per-group", type=int, default=1)
    p.add_argument("--case-group", default="")
    p.add_argument("--control-group", default="")
    p = sub.add_parser("scrna-10x-pseudobulk")
    p.add_argument("--project", required=True)
    p.add_argument("--accession", required=True)
    p.add_argument("--metadata", required=True)
    p.add_argument("--raw-manifest", default="")
    p.add_argument("--output-dataset-id", default="")
    p = sub.add_parser("meta-analysis")
    p.add_argument("--project", required=True)
    p = sub.add_parser("causal-grade")
    p.add_argument("--project", required=True)
    p = sub.add_parser("genetic-coloc-mr")
    p.add_argument("--project", required=True)
    p.add_argument("--gwas-summary", required=True)
    p.add_argument("--qtl-summary", required=True)
    p.add_argument("--dataset-id", default="genetic")
    p.add_argument("--ld-reference", default="")
    p = sub.add_parser("import-evidence")
    p.add_argument("--project", required=True)
    p = sub.add_parser("evidence-migrate")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("evidence-snapshot")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("evidence-query")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--gene", default="")
    p.add_argument("--evidence-type", default="")
    p.add_argument("--source-dataset", default="")
    p.add_argument("--review-status", default="")
    p.add_argument("--limit", type=int, default=100)
    p = sub.add_parser("score")
    p.add_argument("--project", required=True)
    p = sub.add_parser("sasp-score")
    p.add_argument("--project", required=True)
    p = sub.add_parser("report")
    p.add_argument("--project", required=True)
    p = sub.add_parser("demo")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--dataset", action="append", default=[])
    p = sub.add_parser("parse-interest")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--text", default=None)
    p.add_argument("--parser", choices=["rule_based", "gpt"], default="rule_based")
    p.add_argument("--confirmed", action="store_true")
    p = sub.add_parser("confirm-spec")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("agent-run")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--text", required=True)
    p.add_argument("--parser", choices=["rule_based", "gpt"], default="rule_based")
    p.add_argument("--confirmed", action="store_true")
    p.add_argument("--dataset", action="append", default=[])
    p.add_argument("--ideas", type=int, default=6)
    p = sub.add_parser("knowledge-add")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--id", required=True)
    p.add_argument("--type", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--adapter", default="copy")
    p = sub.add_parser("knowledge-remove")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--id", required=True)
    p = sub.add_parser("knowledge-adapt")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("geo-import")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--accession", required=True)
    p.add_argument("--case-label", required=True)
    p.add_argument("--control-label", required=True)
    p.add_argument("--case-pattern", action="append", default=[])
    p.add_argument("--control-pattern", action="append", default=[])
    p.add_argument("--tissue", default="unknown")
    p.add_argument("--organism", default="human")
    p.add_argument("--platform-annotation", default=None)
    p.add_argument("--symbol-column", default=None)
    p.add_argument("--force-download", action="store_true")
    p = sub.add_parser("geo-import-auto")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--accession", required=True)
    p.add_argument("--tissue", default="unknown")
    p.add_argument("--organism", default="human")
    p.add_argument("--platform-annotation", default=None)
    p.add_argument("--symbol-column", default=None)
    p.add_argument("--force-download", action="store_true")
    p.add_argument("--case-hint", default="")
    p.add_argument("--control-hint", default="")
    p.add_argument("--case-label", default="")
    p.add_argument("--control-label", default="")
    p.add_argument("--min-confidence", type=int, default=55)
    p = sub.add_parser("geo-discover")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--query", default="")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--offline", action="store_true")
    p = sub.add_parser("geo-raw")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--accession", required=True)
    p.add_argument("--force-download", action="store_true")
    p.add_argument("--no-extract", action="store_true")
    p.add_argument("--extract-all", action="store_true")
    p.add_argument("--timeout", type=int, default=120)
    p = sub.add_parser("literature-validate")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--query", default="")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--timeout", type=int, default=20)
    p = sub.add_parser("fulltext-literature")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--pmid", action="append", default=[])
    p.add_argument("--pdf", action="append", default=[])
    p.add_argument("--text", action="append", default=[])
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--ocr", action="store_true")
    p.add_argument("--ocr-pages", type=int, default=3)
    p.add_argument("--ocr-lang", default="en")
    p = sub.add_parser("fulltext-llm-extract")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--max-docs", type=int, default=5)
    p.add_argument("--max-chars", type=int, default=14000)
    p.add_argument("--chunk-chars", type=int, default=4500)
    p.add_argument("--model", default="")
    p = sub.add_parser("database-validate")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--gene", action="append", default=[])
    p.add_argument("--query", default="type 2 diabetes skeletal muscle")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--no-adapt", action="store_true")
    p = sub.add_parser("database-retry")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--source", action="append", default=[])
    p.add_argument("--gene", action="append", default=[])
    p.add_argument("--query", default="type 2 diabetes skeletal muscle")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--no-adapt", action="store_true")
    p = sub.add_parser("recovery-manifest")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("cell-type-evidence")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--import-evidence", action="store_true")
    p = sub.add_parser("validation-delivery")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--output-root", default="D:/TargetCompass_validation_delivery")
    p.add_argument("--query", default="")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--timeout", type=int, default=45)
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--gene", action="append", default=[])
    p.add_argument("--db-query", default="type 2 diabetes skeletal muscle SASP")
    p = sub.add_parser("methods")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--set-query", default=None)
    p.add_argument("--set-audit", default=None)
    p.add_argument("--set-experiment", default=None)
    p = sub.add_parser("review")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--item-type", required=True)
    p.add_argument("--item-id", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--note", default="")
    p.add_argument("--reason", default="")
    p.add_argument("--report-ref", default="")
    p = sub.add_parser("review-queue")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("qc-review-queue")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("qc-review")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--work-order-id", required=True)
    p.add_argument("--action", choices=["approve", "reject", "needs_review"], required=True)
    p.add_argument("--reason", required=True)
    p.add_argument("--reviewer", default="human")
    p.add_argument("--report-ref", default="")
    p = sub.add_parser("approval-signoff")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--signer", default="human")
    p.add_argument("--reason", required=True)
    p.add_argument("--status", choices=["signed_off", "rejected"], default="signed_off")
    p = sub.add_parser("adapter-audit")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("export-package")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("v4-manifest")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("work-order-dag")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("consistency-check")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("mcp-gateway")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--list-tools", action="store_true")
    p.add_argument("--read-resource", default="")
    p.add_argument("--call-tool", default="")
    p.add_argument("--args-json", default="{}")
    p.add_argument("--token-json", default="")
    p.add_argument("--token-file", default="")
    p.add_argument("--token-env", default="TARGETCOMPASS_MCP_TOKEN")
    p = sub.add_parser("mcp-server")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--token-json", default="")
    p.add_argument("--token-file", default="")
    p.add_argument("--token-env", default="TARGETCOMPASS_MCP_TOKEN")
    p.add_argument("--client-id", default="mcp_stdio_client")
    p = sub.add_parser("mcp-http-server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8790)
    p = sub.add_parser("service-runtime")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("service-run")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--service-id", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8800)
    p = sub.add_parser("service-call")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--service-id", required=True)
    p.add_argument("--action", required=True)
    p.add_argument("--payload-json", default="{}")
    p.add_argument("--caller", default="mcp_gateway")
    p.add_argument("--trace-id", default="")
    p = sub.add_parser("service-audit")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--service-id", default="")
    p.add_argument("--caller", default="")
    p.add_argument("--status", default="")
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("service-deployment")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--base-port", type=int, default=8810)
    p = sub.add_parser("local-v4-prepare")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--base-port", type=int, default=8810)
    p = sub.add_parser("local-v4-verify")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--base-port", type=int, default=8810)
    p.add_argument("--start-services", action="store_true")
    p.add_argument("--wait-seconds", type=int, default=10)
    p.add_argument("--deepseek-test", action="store_true")
    p.add_argument("--nextflow-run", action="store_true")
    p.add_argument("--nextflow-analysis-run", action="store_true")
    p = sub.add_parser("local-backends-prepare")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("local-backends-check")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--no-migrate", action="store_true")
    p.add_argument("--bucket", default="")
    p = sub.add_parser("local-backends-sync")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--bucket", default="")
    p = sub.add_parser("v5-backends-activate")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--bucket", default="")
    p = sub.add_parser("test-suite")
    p.add_argument("--suite", choices=["quick", "full", "e2e"], default="quick")
    p.add_argument("--list", action="store_true")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--timeout-seconds", type=int, default=None)
    p = sub.add_parser("orchestration-graph")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("orchestration-run")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--role-id", default="")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("llm-task")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--role-id", required=True)
    p.add_argument("--prompt", default="")
    p.add_argument("--input-refs-json", default="{}")
    p.add_argument("--model", default="")
    p.add_argument("--purpose", default="")
    p = sub.add_parser("llm-audit")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--role-id", default="")
    p.add_argument("--status", default="")
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("external-agent-run")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--agent-root", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--use-llm", action="store_true")
    p.add_argument("--model", default="")
    p = sub.add_parser("external-packets-run")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--packet-file", required=True)
    p = sub.add_parser("mcp-client-config")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--base-url", default="")
    p.add_argument("--token-env", default="TARGETCOMPASS_MCP_TOKEN")
    p = sub.add_parser("mcp-token")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--principal", required=True)
    p.add_argument("--role", choices=["local_admin", "reviewer", "agent_reader", "agent_operator"], default="agent_reader")
    p.add_argument("--scopes", default="")
    p.add_argument("--token-id", default="")
    p = sub.add_parser("mcp-policy")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--default-role", default="")
    p.add_argument("--require-token", choices=["true", "false", ""], default="")
    p = sub.add_parser("mcp-audit")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--principal", default="")
    p.add_argument("--tool", default="")
    p.add_argument("--status", default="")
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("registry-snapshot")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("orchestrator-submit")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--run-type", default="typed_orchestration")
    p.add_argument("--idempotency-key", default="")
    p.add_argument("--role-id", default="")
    p.add_argument("--force", action="store_true")
    p.add_argument("--partial-stage", default="")
    p.add_argument("--module-id", default="")
    p.add_argument("--work-order-id", default="")
    p = sub.add_parser("orchestrator-status")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--run-id", default="")
    p = sub.add_parser("orchestrator-cancel")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--run-id", default="")
    p.add_argument("--reason", default="user_requested")
    p = sub.add_parser("orchestrator-resume")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--run-id", default="")
    p = sub.add_parser("orchestrator-partial")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--stage", required=True)
    p = sub.add_parser("task-registry")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("codex-queue-sync")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("codex-queue-claim")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--worker-id", default="local_codex_worker")
    p.add_argument("--task-id", default="")
    p = sub.add_parser("codex-queue-execute")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--worker-id", default="local_codex_worker")
    p.add_argument("--task-id", default="")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("codex-queue-run")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--worker-id", default="local_codex_worker")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("nextflow-plane")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--validate", action="store_true")
    p = sub.add_parser("nextflow-tasks")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--module-id", action="append", default=[])
    p = sub.add_parser("nextflow-run")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--profile", default="local")
    p.add_argument("--module-id", action="append", default=[])
    p.add_argument("--nextflow-bin", default="nextflow")
    p.add_argument("--resume", action="store_true")
    p = sub.add_parser("nextflow-bootstrap")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--download", action="store_true")
    p.add_argument("--install-runtime", action="store_true")
    p.add_argument("--url", default="https://get.nextflow.io")
    p = sub.add_parser("nextflow-smoke")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--nextflow-bin", default="wsl")
    p = sub.add_parser("container-policy")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("container-build")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--image-tag", default="targetcompass-lite:local")
    p.add_argument("--docker-bin", default="auto")
    p.add_argument("--base-image", default="python:3.11-slim")
    p.add_argument("--build-arg", action="append", default=[])
    p.add_argument("--network", default="")
    p = sub.add_parser("container-digest")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--image-tag", default="targetcompass-lite:local")
    p.add_argument("--docker-bin", default="auto")
    p = sub.add_parser("codex-workspace")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--work-order-id", required=True)
    p = sub.add_parser("codex-prepare-worktree")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--codex-job-id", required=True)
    p = sub.add_parser("codex-run-tests")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--codex-job-id", required=True)
    p = sub.add_parser("codex-register-patch")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--codex-job-id", required=True)
    p.add_argument("--patch-path", required=True)
    p.add_argument("--summary", default="")
    p = sub.add_parser("codex-register-test")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--codex-job-id", required=True)
    p.add_argument("--command", required=True)
    p.add_argument("--status", choices=["passed", "failed", "skipped"], required=True)
    p.add_argument("--stdout-ref", default="")
    p.add_argument("--stderr-ref", default="")
    p.add_argument("--duration-seconds", type=float, default=None)
    p = sub.add_parser("codex-record-result")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--codex-job-id", required=True)
    p.add_argument("--status", choices=["success", "failed", "cancelled", "needs_review"], required=True)
    p.add_argument("--artifact", action="append", default=[])
    p.add_argument("--failure-reason", default="")
    p = sub.add_parser("codex-merge-result")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--result-id", required=True)
    p.add_argument("--actor", default="cli")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("codex-engineering")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("pilotdeck-memory-init")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--source-doc", default="")
    p.add_argument("--actor", default="codex")
    p = sub.add_parser("v5-memory")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--action", choices=["init", "update", "rollback", "versions", "agent-context", "dashboard", "rollback-drill", "scenarios"], default="versions")
    p.add_argument("--actor", default="codex")
    p.add_argument("--key", default="")
    p.add_argument("--value-json", default="{}")
    p.add_argument("--version-id", default="")
    p.add_argument("--agent-id", default="question_normalizer")
    p.add_argument("--reason", default="")
    p = sub.add_parser("v5-wetlab-protocol")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--action", choices=["build", "bundle", "sop", "signoff", "signoffs"], default="build")
    p.add_argument("--actor", default="protocol_builder")
    p.add_argument("--max-protocols", type=int, default=5)
    p.add_argument("--protocol-id", default="")
    p.add_argument("--signer", default="human")
    p.add_argument("--decision", choices=["approved", "rejected", "needs_revision"], default="needs_revision")
    p.add_argument("--reason", default="")
    p = sub.add_parser("v5-run-local")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--question", required=True)
    p.add_argument("--source", action="append", default=[])
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--max-analysis-packets", type=int, default=None)
    p.add_argument("--control-plane-only", action="store_true")
    p = sub.add_parser("v5-report-manifest")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("v5-doctor")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--no-build-report", action="store_true")
    p = sub.add_parser("v5-service-control")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--preferred-port", type=int, default=None)
    p = sub.add_parser("v5-test-matrix")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--question-count", type=int, default=10)
    p = sub.add_parser("v5-release-acceptance")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--question-count", type=int, default=50)
    p = sub.add_parser("v5-pre-release-scripts")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--question-count", type=int, default=50)
    p = sub.add_parser("v5-real-question-validation")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--question-count", type=int, default=10)
    p.add_argument("--output-name", default="")
    p.add_argument("--source", action="append", default=[])
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--timeout-seconds-per-question", type=int, default=90)
    p.add_argument("--execute-registered-modules", action="store_true")
    p.add_argument("--max-retries", type=int, default=0)
    p.add_argument("--no-fallback", action="store_true")
    p.add_argument("--isolated-projects", action="store_true")
    p.add_argument("--no-auto-export", action="store_true")
    p = sub.add_parser("v5-freeze-delivery")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--release-label", default="v5-local-dev-acceptance")
    p = sub.add_parser("v5-access")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--action", choices=["init", "create-user", "set-member", "issue-token", "revoke-token", "audit", "readiness"], default="readiness")
    p.add_argument("--actor", default="local_owner")
    p.add_argument("--user-id", default="")
    p.add_argument("--display-name", default="")
    p.add_argument("--role", choices=["owner", "admin", "operator", "reviewer", "viewer"], default="viewer")
    p.add_argument("--status", default="active")
    p.add_argument("--token-id", default="")
    p.add_argument("--ttl-minutes", type=int, default=1440)
    p.add_argument("--scopes", default="")
    p.add_argument("--reason", default="")
    p = sub.add_parser("v5-access-dashboard")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("v5-storage-primary-gate")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("v5-matrix-path-validation")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("v5-storage-migration")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--action", choices=["plan", "migrate", "demo-slim"], default="plan")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--no-evidence", action="store_true")
    p = sub.add_parser("v5-platform-p1-readiness")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("v5-platform-p2-readiness")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("artifact-store")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--action", choices=["summary", "verify", "download-manifest", "put"], default="summary")
    p.add_argument("--path", default="")
    p.add_argument("--artifact-store-id", default="")
    p.add_argument("--producer", default="cli")
    p.add_argument("--artifact-type", default="artifact")
    p = sub.add_parser("v5-resource-gate")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--accept-suggested", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p = sub.add_parser("v5-analysis-main-path")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--question", default="")
    p.add_argument("--accession", default="")
    p.add_argument("--source", default="geo")
    p.add_argument("--case-label", default="")
    p.add_argument("--control-label", default="")
    p.add_argument("--case-pattern", default="")
    p.add_argument("--control-pattern", default="")
    p.add_argument("--tissue", default="")
    p.add_argument("--organism", default="")
    p.add_argument("--platform-annotation", default="")
    p.add_argument("--symbol-column", default="")
    p.add_argument("--max-analysis-packets", type=int, default=None)
    p.add_argument("--force-download", action="store_true")
    p = sub.add_parser("v5-product-report")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--max-candidates", type=int, default=10)
    p = sub.add_parser("v5-recovery-report")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("v5-literature-pipeline")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--query", default="")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--batch-size", type=int, default=10)
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--use-llm", action="store_true")
    p.add_argument("--fulltext-limit", type=int, default=5)
    p.add_argument("--fulltext-llm", action="store_true")
    p.add_argument("--model", default="")
    p = sub.add_parser("v5-llm-roles")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--question", default="")
    p.add_argument("--model", default="")
    p.add_argument("--max-retries", type=int, default=1)
    p.add_argument("--no-fallback", action="store_true")
    p = sub.add_parser("v5-codex-worker-registry")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("v5-nextflow-profiles")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("v5-production-acceptance")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--target", choices=["auth", "installer", "nextflow", "codex", "all"], default="all")
    p.add_argument("--profile", default="local")
    p.add_argument("--codex-samples", type=int, default=5)
    p.add_argument("--real-codex", action="store_true")
    p = sub.add_parser("v5-pilotdeck-console")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("system-status")
    p.add_argument("--project", default="vascular_aging_demo")
    p = sub.add_parser("reset-demo")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--clear-registry", action="store_true")
    p = sub.add_parser("serve")
    p.add_argument("--project", default="vascular_aging_demo")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.cmd == "init":
        print(init_project(args.project))
        raise SystemExit(0)
    if args.cmd == "validate-spec":
        raise SystemExit(_print_errors(validate_research_spec(Path(args.path))))
    if args.cmd == "validate-dataset-card":
        raise SystemExit(_print_errors(validate_dataset_card(Path(args.path))))
    pdir = project_path(getattr(args, "project", ""))
    if args.cmd == "screen":
        print(screen_project(pdir))
    elif args.cmd == "match-datasets":
        print(json.dumps(match_project(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "plan":
        print(json.dumps(build_plan(pdir), indent=2))
    elif args.cmd == "evidence-plan":
        print(json.dumps(build_evidence_plan(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "dataset-profile":
        selected = set(args.dataset or []) or None
        print(json.dumps(build_dataset_profiles(pdir, selected), indent=2, ensure_ascii=False))
    elif args.cmd == "method-contracts":
        print(json.dumps(build_method_contracts(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "compatibility":
        selected = set(args.dataset or []) or None
        print(json.dumps(build_compatibility_decisions(pdir, selected), indent=2, ensure_ascii=False))
    elif args.cmd == "run-deg":
        print(run_deg(pdir, args.dataset))
    elif args.cmd == "annotate":
        print(annotate_project(pdir))
    elif args.cmd == "enrichment":
        print(run_enrichment(pdir))
    elif args.cmd == "scrna-pseudobulk":
        from .scrna import run_scrna_pseudobulk

        print(
            run_scrna_pseudobulk(
                pdir,
                args.dataset_id,
                args.count_matrix,
                args.metadata,
                cell_type=args.cell_type,
                donor_column=args.donor_column,
                group_column=args.group_column,
                cell_type_column=args.cell_type_column,
                min_cells_per_donor=args.min_cells_per_donor,
                min_donors_per_group=args.min_donors_per_group,
                case_group=args.case_group,
                control_group=args.control_group,
            )
        )
    elif args.cmd == "scrna-10x-pseudobulk":
        from .scrna_10x import build_10x_h5_donor_pseudobulk

        print(
            json.dumps(
                build_10x_h5_donor_pseudobulk(
                    pdir,
                    args.accession,
                    args.metadata,
                    raw_manifest=args.raw_manifest,
                    output_dataset_id=args.output_dataset_id,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "meta-analysis":
        from .meta_analysis import run_meta_analysis

        print(run_meta_analysis(pdir))
    elif args.cmd == "causal-grade":
        from .causal_evidence import grade_causal_evidence

        print(grade_causal_evidence(pdir))
    elif args.cmd == "genetic-coloc-mr":
        from .genetic import run_genetic_coloc_mr

        print(run_genetic_coloc_mr(pdir, args.gwas_summary, args.qtl_summary, dataset_id=args.dataset_id, ld_reference=args.ld_reference))
    elif args.cmd == "import-evidence":
        print(import_evidence(pdir))
    elif args.cmd == "evidence-migrate":
        from .evidence_db import migrate_evidence_db

        print(json.dumps(migrate_evidence_db(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "evidence-snapshot":
        from .evidence_db import build_evidence_db_snapshot

        print(json.dumps(build_evidence_db_snapshot(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "evidence-query":
        from .evidence_db import query_evidence_items

        print(
            json.dumps(
                query_evidence_items(
                    pdir,
                    gene=args.gene,
                    evidence_type=args.evidence_type,
                    source_dataset=args.source_dataset,
                    review_status=args.review_status,
                    limit=args.limit,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "score":
        print(score_project(pdir))
    elif args.cmd == "sasp-score":
        print(json.dumps(run_sasp_score(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "report":
        print(build_report(pdir))
    elif args.cmd == "demo":
        raise SystemExit(cmd_demo(args))
    elif args.cmd == "parse-interest":
        interest = args.text
        if interest is None:
            interest = (pdir / "research_interest.md").read_text(encoding="utf-8")
        spec = update_project_spec(pdir, interest, parser=args.parser, confirmed=args.confirmed)
        print(json.dumps(spec, indent=2, ensure_ascii=False))
    elif args.cmd == "confirm-spec":
        print(json.dumps(confirm_project_spec(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "agent-run":
        from .agent import TargetDiscoveryAgent

        result = TargetDiscoveryAgent(args.project).run(args.text, args.parser, args.dataset, args.confirmed, args.ideas)
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        raise SystemExit(0 if result.status == "success" else 1)
    elif args.cmd == "knowledge-add":
        from .knowledge import add_resource

        print(json.dumps(add_resource(pdir, args.id, args.type, args.path, args.adapter), indent=2, ensure_ascii=False))
    elif args.cmd == "knowledge-remove":
        from .knowledge import remove_resource

        print(json.dumps({"removed": remove_resource(pdir, args.id)}, indent=2))
    elif args.cmd == "knowledge-adapt":
        from .knowledge import adapt_resources

        print(json.dumps(adapt_resources(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "geo-import":
        from .geo_importer import GeoImportError, geo_status_path, import_geo_series

        try:
            result = import_geo_series(
                pdir,
                args.accession,
                args.case_label,
                args.control_label,
                args.case_pattern,
                args.control_pattern,
                tissue=args.tissue,
                organism=args.organism,
                platform_annotation=Path(args.platform_annotation) if args.platform_annotation else None,
                symbol_column=args.symbol_column,
                force_download=args.force_download,
            )
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        except GeoImportError as exc:
            print(
                json.dumps(
                    {"status": "failed", "error": exc.to_dict(), "status_file": str(geo_status_path(pdir, args.accession))},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            raise SystemExit(1)
    elif args.cmd == "geo-discover":
        from .geo_discovery import discover_geo_datasets

        print(
            json.dumps(
                discover_geo_datasets(pdir, limit=args.limit, query=args.query, online=not args.offline),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "geo-import-auto":
        from .geo_importer import GeoImportError, geo_status_path, import_geo_series_auto

        try:
            result = import_geo_series_auto(
                pdir,
                args.accession,
                tissue=args.tissue,
                organism=args.organism,
                platform_annotation=Path(args.platform_annotation) if args.platform_annotation else None,
                symbol_column=args.symbol_column,
                force_download=args.force_download,
                case_hint=args.case_hint,
                control_hint=args.control_hint,
                case_label=args.case_label,
                control_label=args.control_label,
                min_confidence=args.min_confidence,
            )
            print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
        except GeoImportError as exc:
            print(
                json.dumps(
                    {"status": "failed", "error": exc.to_dict(), "status_file": str(geo_status_path(pdir, args.accession))},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            raise SystemExit(1)
    elif args.cmd == "geo-raw":
        from .geo_raw import prepare_geo_raw

        print(
            json.dumps(
                prepare_geo_raw(
                    pdir,
                    args.accession,
                    force_download=args.force_download,
                    extract=not args.no_extract,
                    extract_h5_only=not args.extract_all,
                    timeout=args.timeout,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "literature-validate":
        from .literature_validation import run_literature_validation

        print(
            json.dumps(
                run_literature_validation(
                    pdir,
                    query=args.query,
                    limit=args.limit,
                    batch_size=args.batch_size,
                    use_llm=not args.no_llm,
                    timeout=args.timeout,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "fulltext-literature":
        from .fulltext_literature import run_fulltext_literature

        print(
            json.dumps(
                run_fulltext_literature(
                    pdir,
                    pmid=args.pmid,
                    pdf=args.pdf,
                    text=args.text,
                    limit=args.limit,
                    timeout=args.timeout,
                    ocr=args.ocr,
                    ocr_pages=args.ocr_pages,
                    ocr_lang=args.ocr_lang,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "fulltext-llm-extract":
        from .fulltext_llm_extraction import run_fulltext_llm_extraction

        print(
            json.dumps(
                run_fulltext_llm_extraction(
                    pdir,
                    max_docs=args.max_docs,
                    max_chars=args.max_chars,
                    chunk_chars=args.chunk_chars,
                    model=args.model,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "database-validate":
        from .database_validation import validate_online_databases

        print(
            json.dumps(
                validate_online_databases(
                    pdir,
                    genes=args.gene,
                    query=args.query,
                    limit=args.limit,
                    timeout=args.timeout,
                    adapt=not args.no_adapt,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "database-retry":
        from .recovery_center import retry_database_sources

        print(
            json.dumps(
                retry_database_sources(
                    pdir,
                    sources=args.source,
                    genes=args.gene,
                    query=args.query,
                    limit=args.limit,
                    timeout=args.timeout,
                    adapt=not args.no_adapt,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "recovery-manifest":
        from .recovery_center import build_recovery_manifest

        print(json.dumps(build_recovery_manifest(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "cell-type-evidence":
        from .cell_type_evidence import build_cell_type_evidence

        result = build_cell_type_evidence(pdir)
        if args.import_evidence:
            import_evidence(pdir)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "validation-delivery":
        from .validation_delivery import run_validation_delivery

        print(
            json.dumps(
                run_validation_delivery(
                    args.project,
                    output_root=args.output_root,
                    query=args.query,
                    limit=args.limit,
                    batch_size=args.batch_size,
                    use_llm=not args.no_llm,
                    timeout=args.timeout,
                    genes=args.gene,
                    db_query=args.db_query,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "methods":
        from .methods import available_methods, load_method_config, save_method_config

        config = load_method_config(pdir)
        updates = {
            "query": args.set_query,
            "audit": args.set_audit,
            "experiment": args.set_experiment,
        }
        if any(updates.values()):
            config = save_method_config(pdir, {**config, **{k: v for k, v in updates.items() if v}})
        print(json.dumps({"selected": config, "available": available_methods()}, indent=2, ensure_ascii=False))
    elif args.cmd == "review":
        from .review import record_review

        print(
            json.dumps(
                record_review(pdir, args.item_type, args.item_id, args.action, args.note, reason=args.reason, report_ref=args.report_ref),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "review-queue":
        from .review import build_review_queue

        print(json.dumps(build_review_queue(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "qc-review-queue":
        from .qc_review import build_qc_review_queue

        print(json.dumps(build_qc_review_queue(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "qc-review":
        from .qc_review import apply_qc_review

        print(
            json.dumps(
                apply_qc_review(
                    pdir,
                    args.work_order_id,
                    args.action,
                    args.reason,
                    reviewer=args.reviewer,
                    report_ref=args.report_ref,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "approval-signoff":
        from .review import final_signoff

        print(json.dumps(final_signoff(pdir, signer=args.signer, reason=args.reason, status=args.status), indent=2, ensure_ascii=False))
    elif args.cmd == "adapter-audit":
        from .adapter_audit import build_adapter_audit

        print(build_adapter_audit(pdir))
    elif args.cmd == "export-package":
        from .package import export_run_package

        print(export_run_package(pdir))
    elif args.cmd == "v4-manifest":
        from .v4 import build_v4_manifest

        print(json.dumps(build_v4_manifest(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "work-order-dag":
        from .work_order_dag import build_work_order_dag

        print(json.dumps(build_work_order_dag(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "consistency-check":
        from .consistency import run_consistency_check

        print(json.dumps(run_consistency_check(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "mcp-gateway":
        from .mcp_gateway import build_mcp_gateway, call_tool, read_resource
        from .mcp_policy import parse_token
        from .mcp_sessions import load_token_from_sources

        token = load_token_from_sources(args.token_json, args.token_file, args.token_env) or None
        principal = parse_token(pdir, token, actor="cli") if token else None
        if args.list_tools:
            print(json.dumps(build_mcp_gateway(pdir, principal=principal)["tools"], indent=2, ensure_ascii=False))
        elif args.read_resource:
            print(json.dumps(read_resource(pdir, args.read_resource, actor="cli", token=token), indent=2, ensure_ascii=False))
        elif args.call_tool:
            print(json.dumps(call_tool(pdir, args.call_tool, json.loads(args.args_json), actor="cli", token=token), indent=2, ensure_ascii=False))
        else:
            print(json.dumps(build_mcp_gateway(pdir, principal=principal), indent=2, ensure_ascii=False))
    elif args.cmd == "mcp-server":
        from .mcp_server import run_stdio_server
        from .mcp_sessions import load_token_from_sources

        token = load_token_from_sources(args.token_json, args.token_file, args.token_env) or None
        run_stdio_server(args.project, token=token, client_id=args.client_id)
    elif args.cmd == "mcp-http-server":
        from .mcp_http_server import run_http_server

        run_http_server(args.host, args.port)
    elif args.cmd == "service-runtime":
        from .services import service_runtime_manifest

        print(json.dumps(service_runtime_manifest(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "service-run":
        from .services import run_service

        run_service(args.service_id, args.host, args.port)
    elif args.cmd == "service-call":
        from .services import dispatch_service_request

        print(
            json.dumps(
                dispatch_service_request(
                    args.service_id,
                    args.action,
                    pdir,
                    json.loads(args.payload_json),
                    caller=args.caller,
                    trace_id=args.trace_id,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "service-audit":
        from .services import query_service_audit

        print(json.dumps(query_service_audit(pdir, service_id=args.service_id, caller=args.caller, status=args.status, limit=args.limit), indent=2, ensure_ascii=False))
    elif args.cmd == "service-deployment":
        from .service_deployment import build_service_deployment

        print(json.dumps(build_service_deployment(pdir, host=args.host, base_port=args.base_port), indent=2, ensure_ascii=False))
    elif args.cmd == "local-v4-prepare":
        from .local_v4_delivery import prepare_local_v4_delivery

        print(json.dumps(prepare_local_v4_delivery(pdir, host=args.host, base_port=args.base_port), indent=2, ensure_ascii=False))
    elif args.cmd == "local-v4-verify":
        from .local_v4_delivery import verify_local_v4_delivery

        print(
            json.dumps(
                verify_local_v4_delivery(
                    pdir,
                    host=args.host,
                    base_port=args.base_port,
                    deepseek_test=args.deepseek_test,
                    nextflow_run=args.nextflow_run,
                    nextflow_analysis_run=args.nextflow_analysis_run,
                    start_services=args.start_services,
                    wait_seconds=args.wait_seconds,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "local-backends-prepare":
        from .local_backends import prepare_local_backend_stack

        print(json.dumps(prepare_local_backend_stack(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "local-backends-check":
        from .local_backends import check_local_backends

        print(json.dumps(check_local_backends(pdir, migrate=not args.no_migrate, bucket=args.bucket), indent=2, ensure_ascii=False))
    elif args.cmd == "local-backends-sync":
        from .local_backends import sync_local_backends

        print(json.dumps(sync_local_backends(pdir, bucket=args.bucket), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-backends-activate":
        from .local_backends import activate_v5_local_backends

        print(json.dumps(activate_v5_local_backends(pdir, bucket=args.bucket), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-run-local":
        from .canonical.local_demo_runner import run_v5_local_demo

        print(
            json.dumps(
                run_v5_local_demo(
                    pdir,
                    args.question,
                    sources=tuple(args.source or ["geo", "sra", "pubmed", "europe_pmc"]),
                    limit=args.limit,
                    execute_registered_modules=not args.control_plane_only,
                    max_analysis_packets=args.max_analysis_packets,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "v5-report-manifest":
        from .canonical.report_manifest import build_canonical_report_manifest

        print(json.dumps(build_canonical_report_manifest(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-doctor":
        from .canonical.doctor import run_v5_doctor

        result = run_v5_doctor(pdir, build_report=not args.no_build_report)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        raise SystemExit(0 if result.get("status") in {"PASS", "WARN"} else 1)
    elif args.cmd == "v5-access":
        from .canonical.access_control import (
            access_readiness,
            create_user,
            initialize_access_control,
            issue_access_token,
            query_access_audit,
            revoke_access_token,
            set_project_member,
        )

        scopes = [item.strip() for item in args.scopes.split(",") if item.strip()] if args.scopes else None
        if args.action == "init":
            result = initialize_access_control(pdir, owner_id=args.user_id or args.actor, owner_name=args.display_name or args.user_id or args.actor)
        elif args.action == "create-user":
            result = create_user(pdir, args.user_id, args.display_name, actor=args.actor)
        elif args.action == "set-member":
            result = set_project_member(pdir, args.user_id, args.role, actor=args.actor, status=args.status)
        elif args.action == "issue-token":
            result = issue_access_token(pdir, args.user_id, actor=args.actor, ttl_minutes=args.ttl_minutes, scopes=scopes, token_id=args.token_id)
        elif args.action == "revoke-token":
            result = revoke_access_token(pdir, args.token_id, actor=args.actor, reason=args.reason)
        elif args.action == "audit":
            result = query_access_audit(pdir, actor=args.user_id, status=args.status if args.status != "active" else "", limit=50)
        else:
            result = access_readiness(pdir)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "v5-access-dashboard":
        from .canonical.access_admin import build_access_admin_dashboard

        print(json.dumps(build_access_admin_dashboard(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-service-control":
        from .platform_service_control import build_service_control_manifest

        print(json.dumps(build_service_control_manifest(pdir, preferred_port=args.preferred_port), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-test-matrix":
        from .test_suites import build_platform_test_matrix

        print(json.dumps(build_platform_test_matrix(pdir, question_count=args.question_count), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-release-acceptance":
        from .release_acceptance import build_release_acceptance_manifest

        print(json.dumps(build_release_acceptance_manifest(pdir, question_count=args.question_count), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-pre-release-scripts":
        from .platform_config import write_pre_release_scripts

        print(json.dumps(write_pre_release_scripts(pdir, question_count=args.question_count), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-real-question-validation":
        from .canonical.real_question_validation import run_real_question_validation

        sources = args.source or ["geo", "pubmed", "europe_pmc"]
        print(
            json.dumps(
                run_real_question_validation(
                    pdir,
                    question_count=args.question_count,
                    output_name=args.output_name,
                    sources=sources,
                    limit=args.limit,
                    timeout_seconds_per_question=args.timeout_seconds_per_question,
                    execute_registered_modules=args.execute_registered_modules,
                    max_retries=args.max_retries,
                    fallback_to_local=not args.no_fallback,
                    isolated_projects=args.isolated_projects,
                    auto_export=not args.no_auto_export,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "v5-freeze-delivery":
        from .delivery_release import freeze_v5_development_delivery

        print(json.dumps(freeze_v5_development_delivery(pdir, release_label=args.release_label), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-storage-primary-gate":
        from .canonical.storage_primary_gate import build_storage_primary_gate

        print(json.dumps(build_storage_primary_gate(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-matrix-path-validation":
        from .canonical.matrix_path_validation import build_matrix_path_validation

        print(json.dumps(build_matrix_path_validation(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-storage-migration":
        from .storage_migration import build_demo_slim_storage_manifest, build_storage_migration_plan, migrate_legacy_outputs_to_primary_backends

        if args.action == "migrate":
            result = migrate_legacy_outputs_to_primary_backends(pdir, limit=args.limit, sync_evidence=not args.no_evidence)
        elif args.action == "demo-slim":
            result = build_demo_slim_storage_manifest(pdir, migrate=True, limit=args.limit)
        else:
            result = build_storage_migration_plan(pdir)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "v5-platform-p1-readiness":
        from .platform_admin import build_platform_p1_readiness

        print(json.dumps(build_platform_p1_readiness(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-platform-p2-readiness":
        from .platform_admin import build_platform_p2_readiness

        print(json.dumps(build_platform_p2_readiness(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "artifact-store":
        from .artifact_store import artifact_store_summary, build_download_manifest, put_artifact, verify_artifact

        if args.action == "summary":
            result = artifact_store_summary(pdir)
        elif args.action == "verify":
            result = verify_artifact(pdir, relative_path=args.path, artifact_store_id=args.artifact_store_id)
        elif args.action == "download-manifest":
            result = build_download_manifest(pdir, relative_path=args.path, artifact_store_id=args.artifact_store_id)
        else:
            if not args.path:
                raise SystemExit("--path is required for artifact-store --action put")
            result = put_artifact(pdir, args.path, producer=args.producer, artifact_type=args.artifact_type)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "v5-resource-gate":
        from .canonical.resource_gate import apply_suggested_resource_corrections, build_resource_gate_report

        result = apply_suggested_resource_corrections(pdir, limit=args.limit) if args.accept_suggested else build_resource_gate_report(pdir)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "v5-analysis-main-path":
        from .canonical.analysis_main_path import run_v5_analysis_main_path

        print(
            json.dumps(
                run_v5_analysis_main_path(
                    pdir,
                    question=args.question,
                    accession=args.accession,
                    source=args.source,
                    case_label=args.case_label,
                    control_label=args.control_label,
                    case_pattern=args.case_pattern,
                    control_pattern=args.control_pattern,
                    tissue=args.tissue,
                    organism=args.organism,
                    platform_annotation=args.platform_annotation,
                    symbol_column=args.symbol_column,
                    max_analysis_packets=args.max_analysis_packets,
                    force_download=args.force_download,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "v5-product-report":
        from .canonical.product_report import build_productized_project_report

        print(json.dumps(build_productized_project_report(pdir, max_candidates=args.max_candidates), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-recovery-report":
        from .canonical.failure_recovery import build_v5_failure_recovery_report

        print(json.dumps(build_v5_failure_recovery_report(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-literature-pipeline":
        from .canonical.literature_pipeline import run_v5_literature_pipeline

        print(
            json.dumps(
                run_v5_literature_pipeline(
                    pdir,
                    query=args.query,
                    limit=args.limit,
                    batch_size=args.batch_size,
                    timeout=args.timeout,
                    use_llm=args.use_llm,
                    fulltext_limit=args.fulltext_limit,
                    run_fulltext_llm=args.fulltext_llm,
                    model=args.model,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "v5-llm-roles":
        from .canonical.llm_orchestrator import run_canonical_llm_roles

        print(
            json.dumps(
                run_canonical_llm_roles(
                    pdir,
                    user_question=args.question,
                    model_by_agent={agent: args.model for agent in []} if not args.model else {agent: args.model for agent in [
                        "question_normalizer",
                        "scope_resolver",
                        "evidence_plan_builder",
                        "resource_discovery_agent",
                        "method_adapter_workorder_compiler",
                        "result_auditor",
                        "evidence_synthesizer_reporter",
                    ]},
                    max_retries=args.max_retries,
                    fallback_to_local=not args.no_fallback,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "v5-codex-worker-registry":
        from .canonical.codex_worker_registry import refresh_codex_worker_registry

        print(json.dumps(refresh_codex_worker_registry(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-nextflow-profiles":
        from .canonical.nextflow_production import build_nextflow_module_profiles

        print(json.dumps(build_nextflow_module_profiles(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-production-acceptance":
        from .platform_admin import build_platform_production_readiness
        from .production_acceptance import (
            prepare_auth_production_contract,
            run_codex_worker_large_sample_acceptance,
            run_nextflow_large_sample_acceptance,
            validate_windows_installer_release,
        )

        result = {"schema_version": "v5.production_acceptance_command/0.1", "project_id": pdir.name, "target": args.target, "results": {}}
        if args.target in {"auth", "all"}:
            result["results"]["auth"] = prepare_auth_production_contract(pdir)
        if args.target in {"installer", "all"}:
            result["results"]["installer"] = validate_windows_installer_release(pdir)
        if args.target in {"nextflow", "all"}:
            result["results"]["nextflow"] = run_nextflow_large_sample_acceptance(pdir, profile=args.profile)
        if args.target in {"codex", "all"}:
            result["results"]["codex"] = run_codex_worker_large_sample_acceptance(pdir, sample_count=args.codex_samples, real_codex=args.real_codex)
        result["production_readiness"] = build_platform_production_readiness(pdir)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "v5-pilotdeck-console":
        from .canonical.pilotdeck_console import build_pilotdeck_console

        print(json.dumps(build_pilotdeck_console(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "test-suite":
        from .test_suites import list_test_suites, run_test_suite

        if args.list:
            print(json.dumps(list_test_suites(), indent=2, ensure_ascii=False))
        else:
            result = run_test_suite(args.suite, fail_fast=args.fail_fast, timeout_seconds=args.timeout_seconds)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            raise SystemExit(0 if result["status"] == "PASS" else 1)
    elif args.cmd == "orchestration-graph":
        from .orchestration_graph import build_typed_orchestration_graph

        print(json.dumps(build_typed_orchestration_graph(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "orchestration-run":
        from .orchestration_graph import run_typed_orchestration

        print(json.dumps(run_typed_orchestration(pdir, role_id=args.role_id, force=args.force, actor="cli"), indent=2, ensure_ascii=False))
    elif args.cmd == "llm-task":
        from .llm_gateway import prepare_llm_task_packet

        print(
            json.dumps(
                prepare_llm_task_packet(
                    pdir,
                    args.role_id,
                    prompt=args.prompt,
                    input_refs=json.loads(args.input_refs_json),
                    model=args.model,
                    purpose=args.purpose,
                    actor="cli",
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "llm-audit":
        from .llm_gateway import query_llm_audit

        print(json.dumps(query_llm_audit(pdir, role_id=args.role_id, status=args.status, limit=args.limit), indent=2, ensure_ascii=False))
    elif args.cmd == "external-agent-run":
        from .external_agent_adapter import run_bioinfo_agent_adapter

        print(json.dumps(run_bioinfo_agent_adapter(pdir, args.question, Path(args.agent_root), use_llm=args.use_llm, model=args.model), indent=2, ensure_ascii=False))
    elif args.cmd == "external-packets-run":
        from .external_task_runner import run_external_codex_task_packets

        print(json.dumps(run_external_codex_task_packets(pdir, Path(args.packet_file)), indent=2, ensure_ascii=False))
    elif args.cmd == "mcp-client-config":
        from .mcp_sessions import build_mcp_client_config

        print(json.dumps(build_mcp_client_config(pdir, base_url=args.base_url, token_env=args.token_env), indent=2, ensure_ascii=False))
    elif args.cmd == "mcp-token":
        from .mcp_sessions import create_token

        scopes = [s.strip() for s in args.scopes.split(",") if s.strip()] if args.scopes else None
        print(json.dumps(create_token(pdir, args.principal, args.role, scopes=scopes, token_id=args.token_id), indent=2, ensure_ascii=False))
    elif args.cmd == "mcp-policy":
        from .mcp_policy import write_default_policy
        from .mcp_sessions import update_policy

        require_token = None if args.require_token == "" else args.require_token == "true"
        policy = update_policy(pdir, default_role=args.default_role, require_token=require_token) if args.default_role or require_token is not None else write_default_policy(pdir)
        print(json.dumps(policy, indent=2, ensure_ascii=False))
    elif args.cmd == "mcp-audit":
        from .mcp_sessions import query_mcp_audit

        print(json.dumps(query_mcp_audit(pdir, principal=args.principal, tool_id=args.tool, status=args.status, limit=args.limit), indent=2, ensure_ascii=False))
    elif args.cmd == "registry-snapshot":
        from .registry_snapshots import build_registry_snapshots

        print(json.dumps(build_registry_snapshots(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "orchestrator-submit":
        from .orchestrator import submit_orchestrator_run

        print(
            json.dumps(
                submit_orchestrator_run(
                    pdir,
                    run_type=args.run_type,
                    idempotency_key=args.idempotency_key,
                    role_id=args.role_id,
                    force=args.force,
                    partial_stage=args.partial_stage,
                    module_id=args.module_id,
                    work_order_id=args.work_order_id,
                    actor="cli",
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "orchestrator-status":
        from .orchestrator import get_orchestrator_status

        print(json.dumps(get_orchestrator_status(pdir, args.run_id), indent=2, ensure_ascii=False))
    elif args.cmd == "orchestrator-cancel":
        from .orchestrator import cancel_orchestrator_run

        print(json.dumps(cancel_orchestrator_run(pdir, args.run_id, reason=args.reason), indent=2, ensure_ascii=False))
    elif args.cmd == "orchestrator-resume":
        from .orchestrator import resume_orchestrator_run

        print(json.dumps(resume_orchestrator_run(pdir, args.run_id, actor="cli"), indent=2, ensure_ascii=False))
    elif args.cmd == "orchestrator-partial":
        from .orchestrator import partial_rerun_orchestrator

        print(json.dumps(partial_rerun_orchestrator(pdir, args.stage, actor="cli"), indent=2, ensure_ascii=False))
    elif args.cmd == "task-registry":
        from .task_registry import build_task_registry

        print(json.dumps(build_task_registry(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-queue-sync":
        from .codex_task_queue import sync_codex_task_queue

        print(json.dumps(sync_codex_task_queue(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-queue-claim":
        from .codex_task_queue import claim_codex_task

        print(json.dumps(claim_codex_task(pdir, worker_id=args.worker_id, task_id=args.task_id), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-queue-execute":
        from .codex_task_queue import execute_codex_queue_task

        print(json.dumps(execute_codex_queue_task(pdir, task_id=args.task_id, worker_id=args.worker_id, force=args.force), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-queue-run":
        from .codex_task_queue import execute_codex_queue

        print(json.dumps(execute_codex_queue(pdir, worker_id=args.worker_id, limit=args.limit, force=args.force), indent=2, ensure_ascii=False))
    elif args.cmd == "nextflow-plane":
        from .nextflow_plane import build_nextflow_execution_plane, validate_nextflow_execution_plane

        manifest = build_nextflow_execution_plane(pdir)
        validation = validate_nextflow_execution_plane(pdir) if args.validate else None
        print(json.dumps({"manifest": manifest, "validation": validation}, indent=2, ensure_ascii=False))
    elif args.cmd == "nextflow-tasks":
        from .nextflow_runner import build_nextflow_tasks

        print(json.dumps(build_nextflow_tasks(pdir, args.module_id or None), indent=2, ensure_ascii=False))
    elif args.cmd == "nextflow-run":
        from .nextflow_runner import run_nextflow_local

        print(
            json.dumps(
                run_nextflow_local(
                    pdir,
                    profile=args.profile,
                    module_ids=args.module_id or None,
                    nextflow_bin=args.nextflow_bin,
                    resume=args.resume,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "nextflow-bootstrap":
        from .nextflow_bootstrap import bootstrap_nextflow

        print(json.dumps(bootstrap_nextflow(pdir, download=args.download, nextflow_url=args.url, install_runtime=args.install_runtime), indent=2, ensure_ascii=False))
    elif args.cmd == "nextflow-smoke":
        from .nextflow_runner import run_nextflow_smoke

        print(json.dumps(run_nextflow_smoke(pdir, nextflow_bin=args.nextflow_bin), indent=2, ensure_ascii=False))
    elif args.cmd == "container-policy":
        from .container_plane import build_container_mount_policy, write_apptainer_recipe

        print(json.dumps({"mount_policy": build_container_mount_policy(pdir), "apptainer_recipe": str(write_apptainer_recipe(pdir))}, indent=2, ensure_ascii=False))
    elif args.cmd == "container-build":
        from .container_plane import build_docker_image

        build_args = {}
        for item in args.build_arg:
            if "=" not in item:
                raise SystemExit(f"--build-arg must be KEY=VALUE, got: {item}")
            key, value = item.split("=", 1)
            build_args[key] = value
        print(
            json.dumps(
                build_docker_image(
                    pdir,
                    image_tag=args.image_tag,
                    docker_bin=args.docker_bin,
                    base_image=args.base_image,
                    build_args=build_args,
                    network=args.network,
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "container-digest":
        from .container_plane import inspect_image_digest

        print(json.dumps(inspect_image_digest(pdir, image_tag=args.image_tag, docker_bin=args.docker_bin), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-workspace":
        from .codex_engineering import create_isolated_workspace

        print(json.dumps(create_isolated_workspace(pdir, args.work_order_id, actor="cli"), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-prepare-worktree":
        from .codex_engineering import prepare_git_worktree

        print(json.dumps(prepare_git_worktree(pdir, args.codex_job_id, actor="cli"), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-run-tests":
        from .codex_engineering import run_codex_task_tests

        print(json.dumps(run_codex_task_tests(pdir, args.codex_job_id, actor="cli"), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-register-patch":
        from .codex_engineering import register_codex_patch

        print(json.dumps(register_codex_patch(pdir, args.codex_job_id, args.patch_path, summary=args.summary, actor="cli"), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-register-test":
        from .codex_engineering import register_codex_test_result

        print(
            json.dumps(
                register_codex_test_result(
                    pdir,
                    args.codex_job_id,
                    args.command,
                    args.status,
                    stdout_ref=args.stdout_ref,
                    stderr_ref=args.stderr_ref,
                    duration_seconds=args.duration_seconds,
                    actor="cli",
                ),
                indent=2,
                ensure_ascii=False,
            )
        )
    elif args.cmd == "codex-record-result":
        from .codex_engineering import record_codex_result

        print(json.dumps(record_codex_result(pdir, args.codex_job_id, args.status, args.artifact, args.failure_reason, actor="cli"), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-merge-result":
        from .codex_engineering import apply_approved_codex_result

        print(json.dumps(apply_approved_codex_result(pdir, args.result_id, actor=args.actor, dry_run=args.dry_run), indent=2, ensure_ascii=False))
    elif args.cmd == "codex-engineering":
        from .codex_engineering import load_codex_engineering

        print(json.dumps(load_codex_engineering(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "pilotdeck-memory-init":
        from .canonical.memory_palace import install_pilotdeck_memory

        print(json.dumps(install_pilotdeck_memory(pdir, source_doc=args.source_doc, actor=args.actor), indent=2, ensure_ascii=False))
    elif args.cmd == "v5-memory":
        from .canonical.memory_palace import (
            build_agent_memory_context,
            build_memory_audit_dashboard,
            install_pilotdeck_memory,
            list_memory_versions,
            rollback_memory,
            run_memory_rollback_drill,
            run_memory_usage_scenarios,
            update_memory_entry,
        )

        if args.action == "init":
            result = install_pilotdeck_memory(pdir, actor=args.actor)
        elif args.action == "update":
            result = update_memory_entry(pdir, args.key, json.loads(args.value_json), actor=args.actor, reason=args.reason)
        elif args.action == "rollback":
            result = rollback_memory(pdir, args.version_id, actor=args.actor, reason=args.reason)
        elif args.action == "agent-context":
            result = build_agent_memory_context(pdir, args.agent_id)
        elif args.action == "dashboard":
            result = build_memory_audit_dashboard(pdir, actor=args.actor)
        elif args.action == "rollback-drill":
            result = run_memory_rollback_drill(pdir, actor=args.actor)
        elif args.action == "scenarios":
            result = run_memory_usage_scenarios(pdir, actor=args.actor)
        else:
            result = {"schema_version": "v5.memory_versions/0.1", "project_id": pdir.name, "versions": list_memory_versions(pdir)}
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "v5-wetlab-protocol":
        from .canonical.wet_lab_protocol import build_wet_lab_sop_bundle, build_wet_lab_protocol_bundle, build_wet_lab_protocols, load_wet_lab_signoffs, signoff_wet_lab_protocol

        if args.action == "build":
            result = build_wet_lab_protocols(pdir, actor=args.actor, max_protocols=args.max_protocols)
        elif args.action == "bundle":
            result = build_wet_lab_protocol_bundle(pdir, actor=args.actor, max_protocols=args.max_protocols)
        elif args.action == "sop":
            result = build_wet_lab_sop_bundle(pdir, actor=args.actor, max_protocols=args.max_protocols)
        elif args.action == "signoff":
            result = signoff_wet_lab_protocol(pdir, args.protocol_id, signer=args.signer, decision=args.decision, reason=args.reason)
        else:
            result = {"schema_version": "v5.wet_lab_protocol_signoffs/0.1", "project_id": pdir.name, "signoffs": load_wet_lab_signoffs(pdir)}
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.cmd == "system-status":
        from .system_status import system_status

        print(json.dumps(system_status(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "reset-demo":
        from .reset_demo import reset_demo_outputs

        print(json.dumps({"removed": reset_demo_outputs(pdir, keep_registry=not args.clear_registry)}, indent=2, ensure_ascii=False))
    elif args.cmd == "serve":
        from .webapp import run_server

        run_server(args.project, args.host, args.port)
