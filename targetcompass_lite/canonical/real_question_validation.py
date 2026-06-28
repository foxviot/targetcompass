from __future__ import annotations

import html
import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..package import export_run_package
from ..paths import PROJECTS, ensure_project_dirs
from ..test_suites import PLATFORM_E2E_QUESTIONS
from .llm_orchestrator import run_canonical_llm_roles
from .local_demo_runner import run_v5_local_demo
from .product_report import build_productized_project_report


REAL_QUESTION_VALIDATION_SCHEMA = "v5.real_question_validation/0.1"

DEFAULT_REAL_QUESTION_TOPICS = PLATFORM_E2E_QUESTIONS + [
    "Which cell types in chronic kidney disease show SASP-high surface-accessible target candidates?",
    "Do COPD lung stromal cells have secreted SASP ligands with disease-associated expression?",
    "Are heart failure cardiac fibroblasts enriched for senescence-associated surface markers?",
    "Which rheumatoid arthritis synovial cell populations express targetable SASP-related molecules?",
    "Do inflammatory bowel disease intestinal stromal cells show SASP-high candidate cytokines?",
    "Are liver fibrosis hepatic stellate cells enriched for surface-accessible senescence targets?",
    "Which psoriasis skin cell populations show inflammatory SASP ligand expression?",
    "Do chronic wound fibroblasts contain SASP-high cells with targetable membrane proteins?",
    "Are multiple sclerosis lesion immune cells enriched for SASP-associated surface molecules?",
    "Which cancer-associated fibroblast states show SASP-high secreted or surface candidates?",
    "Do aged hematopoietic niche cells show targetable SASP-associated factors?",
    "Are Parkinson disease vascular or glial cells enriched for inflammatory SASP signals?",
    "Which diabetic nephropathy kidney cell populations express SASP-related surface molecules?",
    "Do systemic sclerosis fibroblasts show disease-associated surface markers linked to SASP?",
    "Are aged bone marrow stromal cells enriched for senescence-associated secreted factors?",
    "Which pulmonary fibrosis epithelial or stromal cells have SASP-high candidate targets?",
    "Do sepsis survivor immune cells show persistent SASP-like surface marker signatures?",
    "Are cachexia skeletal muscle cells enriched for senescence-associated secreted molecules?",
    "Which osteoporotic bone stromal cells show SASP-high surface marker candidates?",
    "Do aged skin fibroblast subsets express targetable SASP-associated membrane proteins?",
    "Are hypertensive vascular smooth muscle cells enriched for SASP-high target candidates?",
    "Which NAFLD liver cell populations show inflammatory SASP ligand expression?",
    "Do lupus kidney immune or stromal cells show surface-accessible SASP-associated candidates?",
    "Are chronic pancreatitis stromal cells enriched for senescence-associated secreted factors?",
    "Which asthma airway epithelial cells show disease-associated SASP signals?",
    "Do frailty-associated blood immune cells express SASP-related surface targets?",
    "Are aged retinal vascular cells enriched for inflammatory surface marker candidates?",
    "Which endometriosis stromal cell states show SASP-high candidate molecules?",
    "Do chronic viral infection immune cells show SASP-like secreted ligand signatures?",
    "Are aged adipose stromal cells enriched for targetable inflammatory surface molecules?",
    "Which myositis muscle cell populations show SASP-high secreted or surface candidates?",
    "Do aneurysm vascular wall cells express SASP-associated targetable surface markers?",
    "Are chronic liver disease macrophage states enriched for SASP-related ligands?",
    "Which aged cartilage chondrocyte states show targetable SASP candidate molecules?",
    "Do diabetic wound endothelial cells express SASP-high surface-accessible markers?",
    "Are chronic transplant rejection tissue cells enriched for inflammatory SASP targets?",
    "Which long COVID immune cell states show persistent SASP-like molecule expression?",
    "Do aged prostate stromal cells show SASP-high secreted factor candidates?",
    "Are glaucoma optic nerve head cells enriched for senescence-associated surface molecules?",
    "Which chronic urticaria skin immune cells express SASP-related surface candidates?",
]


def run_real_question_validation(
    project_dir: str | Path,
    *,
    question_count: int = 10,
    output_name: str = "",
    sources: list[str] | tuple[str, ...] = ("geo", "pubmed", "europe_pmc"),
    limit: int = 3,
    timeout_seconds_per_question: int = 90,
    execute_registered_modules: bool = False,
    max_retries: int = 0,
    fallback_to_local: bool = True,
    isolated_projects: bool = False,
    auto_export: bool = True,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    count = max(1, min(int(question_count), 50))
    questions = _questions(count)
    run_id = output_name or f"real_question_e2e_{count}"
    out_dir = project_dir / "v5" / "validation" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    progress_path = out_dir / "validation_progress.json"
    for index, question in enumerate(questions, start=1):
        qdir = out_dir / f"q{index:02d}"
        qdir.mkdir(parents=True, exist_ok=True)
        run_project_dir = _prepare_question_project(project_dir, run_id, index, question) if isolated_projects else qdir
        row = _run_one_question(
            run_project_dir,
            question,
            index=index,
            sources=sources,
            limit=limit,
            timeout_seconds=timeout_seconds_per_question,
            execute_registered_modules=execute_registered_modules,
            max_retries=max_retries,
            fallback_to_local=fallback_to_local,
            auto_export=auto_export,
        )
        row["validation_folder"] = str(qdir).replace("\\", "/")
        if isolated_projects:
            row["isolated_project_id"] = run_project_dir.name
            row["isolated_project_path"] = str(run_project_dir).replace("\\", "/")
            _write_question_index(qdir, row)
        rows.append(row)
        progress_path.write_text(json.dumps(_summary(project_dir, out_dir, rows, started, count, in_progress=True), indent=2, ensure_ascii=False), encoding="utf-8")

    summary = _summary(project_dir, out_dir, rows, started, count, in_progress=False)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "summary.md").write_text(_summary_md(summary), encoding="utf-8")
    (out_dir / "summary.html").write_text(_summary_html(summary), encoding="utf-8")
    return summary


def _run_one_question(
    qdir: Path,
    question: str,
    *,
    index: int,
    sources: list[str] | tuple[str, ...],
    limit: int,
    timeout_seconds: int,
    execute_registered_modules: bool,
    max_retries: int,
    fallback_to_local: bool,
    auto_export: bool,
) -> dict[str, Any]:
    started = time.monotonic()
    llm_status = "not_run"
    resource_status = "not_run"
    errors: list[str] = []
    llm: dict[str, Any] = {}
    local: dict[str, Any] = {}
    try:
        llm = run_canonical_llm_roles(
            qdir,
            user_question=question,
            max_retries=max_retries,
            fallback_to_local=fallback_to_local,
        )
        llm_status = str(llm.get("status", "completed"))
    except Exception as exc:  # pragma: no cover - network and provider errors are environment dependent.
        llm_status = "failed"
        errors.append(f"llm_roles: {exc}")
    if time.monotonic() - started > timeout_seconds:
        errors.append(f"question exceeded soft timeout after LLM stage: {timeout_seconds}s")
    try:
        local = run_v5_local_demo(
            qdir,
            question,
            sources=list(sources),
            limit=limit,
            execute_registered_modules=execute_registered_modules,
        )
        resource_status = str(local.get("status", "completed"))
    except Exception as exc:  # pragma: no cover - network and provider errors are environment dependent.
        resource_status = "failed"
        errors.append(f"resource_discovery: {exc}")

    _copy_if_exists(qdir / "v5" / "llm_roles" / "llm_orchestration_run.json", qdir / "llm_orchestration_run.json")
    _copy_if_exists(qdir / "v5" / "local_demo" / "local_demo_run.json", qdir / "local_demo_run.json")
    _copy_if_exists(qdir / "v5" / "resource_discovery" / "resource_discovery_bundle.json", qdir / "resource_discovery_bundle.json")
    _copy_if_exists(qdir / "v5" / "resource_discovery" / "resource_gate_report.json", qdir / "resource_gate_report.json")
    report_manifest: dict[str, Any] = {}
    export_package = ""
    export_error = ""
    if auto_export:
        try:
            report_manifest = build_productized_project_report(qdir)
            export_package = str(export_run_package(qdir)).replace("\\", "/")
        except Exception as exc:
            export_error = str(exc)
            errors.append(f"report_export: {exc}")

    bundle = _read_json(qdir / "resource_discovery_bundle.json", {})
    gate = _read_json(qdir / "resource_gate_report.json", {})
    agent_runs = llm.get("agent_runs", []) if isinstance(llm, dict) else []
    fallback_count = sum(1 for row in agent_runs if row.get("fallback_used"))
    return {
        "index": index,
        "question": question,
        "status": "completed" if not errors else "review_required",
        "llm_status": llm_status,
        "resource_status": resource_status,
        "agent_run_count": len(agent_runs),
        "llm_fallback_count": fallback_count,
        "candidate_count": bundle.get("candidate_count", len(bundle.get("resource_candidates", []))),
        "verified_candidate_count": bundle.get("verified_candidate_count", 0),
        "datasets_lockable_count": gate.get("datasets_lockable_count", 0),
        "manual_review_count": gate.get("manual_review_count", 0),
        "errors": errors,
        "duration_seconds": round(time.monotonic() - started, 3),
        "folder": str(qdir).replace("\\", "/"),
        "product_report_status": report_manifest.get("status", "not_built") if auto_export else "disabled",
        "product_report_ref": "v5/reports/product_report.html" if report_manifest else "",
        "export_package": export_package,
        "export_error": export_error,
    }


def _summary(project_dir: Path, out_dir: Path, rows: list[dict[str, Any]], started: float, expected_count: int, *, in_progress: bool) -> dict[str, Any]:
    return {
        "schema_version": REAL_QUESTION_VALIDATION_SCHEMA,
        "project_id": project_dir.name,
        "status": "running" if in_progress else ("PASS" if all(not row.get("errors") for row in rows) and len(rows) == expected_count else "REVIEW"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "question_count": len(rows),
        "expected_question_count": expected_count,
        "duration_seconds": round(time.monotonic() - started, 3),
        "output_dir": str(out_dir).replace("\\", "/"),
        "totals": {
            "llm_failures": sum(1 for row in rows if row.get("llm_status") == "failed"),
            "resource_failures": sum(1 for row in rows if row.get("resource_status") == "failed"),
            "resource_candidates": sum(int(row.get("candidate_count", 0) or 0) for row in rows),
            "verified_candidates": sum(int(row.get("verified_candidate_count", 0) or 0) for row in rows),
            "lockable_datasets": sum(int(row.get("datasets_lockable_count", 0) or 0) for row in rows),
            "manual_review_items": sum(int(row.get("manual_review_count", 0) or 0) for row in rows),
            "llm_fallbacks": sum(int(row.get("llm_fallback_count", 0) or 0) for row in rows),
        },
        "rows": rows,
        "isolated_project_count": sum(1 for row in rows if row.get("isolated_project_id")),
        "export_package_count": sum(1 for row in rows if row.get("export_package")),
        "acceptance_note": "Strict dataset gate may produce zero lockable datasets; that is acceptable when metadata/grouping/sample-size evidence is insufficient.",
    }


def _summary_md(summary: dict[str, Any]) -> str:
    lines = [
        "# v5 Real Question Validation",
        "",
        f"- Status: `{summary.get('status')}`",
        f"- Questions: {summary.get('question_count')}/{summary.get('expected_question_count')}",
        f"- Duration seconds: {summary.get('duration_seconds')}",
        f"- Output dir: `{summary.get('output_dir')}`",
        "",
        "## Totals",
        "",
    ]
    for key, value in summary.get("totals", {}).items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Questions", ""])
    for row in summary.get("rows", []):
        lines.append(f"### q{int(row.get('index', 0)):02d}")
        lines.append(f"- Question: {row.get('question')}")
        lines.append(f"- LLM: `{row.get('llm_status')}`, resource: `{row.get('resource_status')}`")
        lines.append(f"- Candidates: {row.get('candidate_count')}, lockable datasets: {row.get('datasets_lockable_count')}")
        if row.get("isolated_project_id"):
            lines.append(f"- Project: `{row.get('isolated_project_id')}`")
        if row.get("product_report_ref"):
            lines.append(f"- Report: `{row.get('product_report_ref')}`")
        if row.get("export_package"):
            lines.append(f"- Export: `{row.get('export_package')}`")
        if row.get("errors"):
            lines.append(f"- Errors: {'; '.join(row.get('errors', []))}")
        lines.append("")
    return "\n".join(lines)


def _summary_html(summary: dict[str, Any]) -> str:
    totals = "".join(
        f"<div class=\"card\"><small>{html.escape(str(key))}</small><strong>{html.escape(str(value))}</strong></div>"
        for key, value in summary.get("totals", {}).items()
    )
    rows = "".join(
        "<tr>"
        f"<td>q{int(row.get('index', 0)):02d}</td>"
        f"<td>{html.escape(str(row.get('question', '')))}</td>"
        f"<td>{html.escape(str(row.get('llm_status', '')))}</td>"
        f"<td>{html.escape(str(row.get('resource_status', '')))}</td>"
        f"<td>{html.escape(str(row.get('candidate_count', 0)))}</td>"
        f"<td>{html.escape(str(row.get('datasets_lockable_count', 0)))}</td>"
        f"<td>{html.escape(str(row.get('isolated_project_id', '')))}</td>"
        f"<td>{html.escape(str(row.get('export_package', '')))}</td>"
        f"<td>{html.escape('; '.join(row.get('errors', [])))}</td>"
        "</tr>"
        for row in summary.get("rows", [])
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>v5 Real Question Validation</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; background: #f6f7f9; color: #15171a; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ background: white; border: 1px solid #e3e6eb; border-radius: 12px; padding: 14px; }}
    .card small {{ display: block; color: #667085; }}
    .card strong {{ font-size: 24px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 12px; overflow: hidden; }}
    th, td {{ border-bottom: 1px solid #edf0f3; padding: 10px; text-align: left; vertical-align: top; }}
    th {{ background: #f0f2f5; }}
    code {{ background: #eef1f5; padding: 2px 5px; border-radius: 5px; }}
  </style>
</head>
<body>
  <h1>v5 Real Question Validation</h1>
  <p>Status: <code>{html.escape(str(summary.get('status')))}</code> · Questions: {html.escape(str(summary.get('question_count')))} / {html.escape(str(summary.get('expected_question_count')))} · Duration: {html.escape(str(summary.get('duration_seconds')))}s</p>
  <p>Output: <code>{html.escape(str(summary.get('output_dir')))}</code></p>
  <p>Isolated projects: <code>{html.escape(str(summary.get('isolated_project_count', 0)))}</code> 路 Export packages: <code>{html.escape(str(summary.get('export_package_count', 0)))}</code></p>
  <div class="grid">{totals}</div>
  <table>
    <thead><tr><th>ID</th><th>Question</th><th>LLM</th><th>Resource</th><th>Candidates</th><th>Lockable</th><th>Project</th><th>Export</th><th>Errors</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
  <p>{html.escape(str(summary.get('acceptance_note', '')))}</p>
</body>
</html>
"""


def _questions(count: int) -> list[str]:
    questions = list(DEFAULT_REAL_QUESTION_TOPICS)
    while len(questions) < count:
        questions.append(f"Validation question {len(questions) + 1}: disease tissue SASP surface marker discovery.")
    return questions[:count]


def _copy_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copyfile(src, dst)


def _prepare_question_project(parent_project_dir: Path, run_id: str, index: int, question: str) -> Path:
    project_id = _safe_project_id(f"{parent_project_dir.name}_{run_id}_q{index:02d}")
    target = PROJECTS / project_id
    if target.exists():
        shutil.rmtree(target)
    ensure_project_dirs(target)
    (target / "research_spec.json").write_text(
        json.dumps({"project_id": project_id, "confirmed": False, "research_question": question}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (target / "research_interest.md").write_text(question + "\n", encoding="utf-8")
    meta = {
        "schema_version": "v5.validation_isolated_project/0.1",
        "project_id": project_id,
        "parent_project_id": parent_project_dir.name,
        "validation_run_id": run_id,
        "question_index": index,
        "question": question,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (target / "v5").mkdir(parents=True, exist_ok=True)
    (target / "v5" / "project_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def _write_question_index(qdir: Path, row: dict[str, Any]) -> None:
    payload = {
        "schema_version": "v5.validation_question_index/0.1",
        "question": row.get("question", ""),
        "isolated_project_id": row.get("isolated_project_id", ""),
        "isolated_project_path": row.get("isolated_project_path", ""),
        "product_report_ref": row.get("product_report_ref", ""),
        "export_package": row.get("export_package", ""),
        "status": row.get("status", ""),
    }
    (qdir / "question_project_index.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _safe_project_id(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value.strip())
    text = re.sub(r"_+", "_", text)
    return text[:96].strip("_") or "validation_project"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
