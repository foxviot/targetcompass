import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


PACKAGE_FILES = [
    "research_interest.md",
    "research_spec.json",
    "analysis_module_registry.json",
    "configs/agent_methods.json",
    "configs/knowledge_registry.json",
    "results/agent_trace.json",
    "results/run_status.json",
    "results/review_actions.tsv",
    "results/review_actions.jsonl",
    "results/review_queue.json",
    "results/approval_state.json",
    "results/adapter_audit/adapter_audit.tsv",
    "results/geo_discovery/geo_recommendations.json",
    "results/geo_discovery/geo_recommendations.tsv",
    "results/ideas/idea_batch.json",
    "results/ideas/feasibility_audit.json",
    "results/experiments/experiment_designs.json",
    "candidate_scores.csv",
    "dataset_match_report.csv",
    "eligible_datasets.csv",
    "reports/target_report.html",
    "reports/target_report.docx",
    "reports/target_report_structured.json",
]


def export_run_package(project_dir: Path) -> Path:
    out_dir = project_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package_path = out_dir / f"{project_dir.name}_run_package_{stamp}.zip"
    manifest = {
        "project": project_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
    }
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for relative in PACKAGE_FILES:
            path = project_dir / relative
            if path.exists():
                zf.write(path, relative)
                manifest["files"].append(relative)
        zf.writestr("package_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return package_path
