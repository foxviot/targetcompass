import argparse
import json
import shutil
from pathlib import Path

from .annotation import annotate_project
from .deg import run_deg
from .enrichment import run_enrichment
from .evidence_db import import_evidence
from .matching import match_project
from .paths import ensure_project_dirs, project_path
from .planning import build_plan
from .reporting import build_report
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


def cmd_demo(args) -> int:
    p = init_project(args.project)
    selected = set(getattr(args, "dataset", []) or []) or None
    rc = _print_errors(validate_research_spec(p / "research_spec.json"))
    if rc:
        return rc
    spec = json.loads((p / "research_spec.json").read_text(encoding="utf-8"))
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
    review_count = sum(1 for row in matches if row["match_status"] != "MATCH")
    print(f"matched {len(matches)} dataset card(s); {review_count} require review")
    plan = build_plan(p)
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
    annotate_project(p)
    import_evidence(p)
    score_project(p)
    html_path, docx_path = build_report(p)
    print(f"report: {html_path}")
    print(f"word-compatible report: {docx_path}")
    return 0


def main() -> None:
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
    p = sub.add_parser("match-datasets")
    p.add_argument("--project", required=True)
    p = sub.add_parser("run-deg")
    p.add_argument("--project", required=True)
    p.add_argument("--dataset", required=True)
    p = sub.add_parser("annotate")
    p.add_argument("--project", required=True)
    p = sub.add_parser("enrichment")
    p.add_argument("--project", required=True)
    p = sub.add_parser("import-evidence")
    p.add_argument("--project", required=True)
    p = sub.add_parser("score")
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
    elif args.cmd == "run-deg":
        print(run_deg(pdir, args.dataset))
    elif args.cmd == "annotate":
        print(annotate_project(pdir))
    elif args.cmd == "enrichment":
        print(run_enrichment(pdir))
    elif args.cmd == "import-evidence":
        print(import_evidence(pdir))
    elif args.cmd == "score":
        print(score_project(pdir))
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
    elif args.cmd == "system-status":
        from .system_status import system_status

        print(json.dumps(system_status(pdir), indent=2, ensure_ascii=False))
    elif args.cmd == "reset-demo":
        from .reset_demo import reset_demo_outputs

        print(json.dumps({"removed": reset_demo_outputs(pdir, keep_registry=not args.clear_registry)}, indent=2, ensure_ascii=False))
    elif args.cmd == "serve":
        from .webapp import run_server

        run_server(args.project, args.host, args.port)
