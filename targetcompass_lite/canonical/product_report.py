from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any

from .artifacts import load_artifact_registry, register_artifact
from .backend_writer import write_bytes_artifact, write_json_artifact
from .schemas import now_iso


PRODUCT_REPORT_SCHEMA = "v5.product_report/0.1"


def build_productized_project_report(project_dir: str | Path, *, max_candidates: int = 10) -> dict[str, Any]:
    project_dir = Path(project_dir)
    candidates = _load_candidates(project_dir, max_candidates=max_candidates)
    cell_evidence = _load_cell_type_evidence(project_dir)
    failure_recovery = _read_json(project_dir / "v5" / "recovery" / "failure_recovery_report.json", {})
    canonical_manifest = _read_json(project_dir / "v5" / "reports" / "canonical_report_manifest.json", {})
    main_path = _read_json(project_dir / "v5" / "analysis_main_path" / "main_path_manifest.json", {})
    resource_gate = _read_json(project_dir / "v5" / "resource_discovery" / "resource_gate_report.json", {})
    artifacts = load_artifact_registry(project_dir)

    enriched = [_attach_candidate_context(row, cell_evidence) for row in candidates]
    manifest = {
        "schema_version": PRODUCT_REPORT_SCHEMA,
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "status": _report_status(canonical_manifest, main_path, resource_gate),
        "candidate_count": len(enriched),
        "top_candidates": enriched,
        "refs": {
            "candidate_scores": "candidate_scores.csv" if (project_dir / "candidate_scores.csv").exists() else "",
            "cell_type_evidence": "results/cell_type_evidence/cell_type_evidence.tsv" if (project_dir / "results" / "cell_type_evidence" / "cell_type_evidence.tsv").exists() else "",
            "canonical_report_manifest": "v5/reports/canonical_report_manifest.json" if canonical_manifest else "",
            "analysis_main_path": "v5/analysis_main_path/main_path_manifest.json" if main_path else "",
            "resource_gate": "v5/resource_discovery/resource_gate_report.json" if resource_gate else "",
            "failure_recovery": "v5/recovery/failure_recovery_report.json" if failure_recovery else "",
        },
        "evidence_chain": {
            "artifact_count": len(artifacts),
            "task_run_refs": canonical_manifest.get("task_run_refs", []),
            "qc_report_refs": canonical_manifest.get("qc_report_refs", []),
            "artifact_manifest_refs": canonical_manifest.get("artifact_manifest_refs", [])[:50],
            "claim_ceiling": canonical_manifest.get("claim_ceiling", {"max_allowed_claim": "association"}),
            "human_review_gate": canonical_manifest.get("human_review_gate", {"required": True, "reason": "not built"}),
        },
        "failures_and_recovery": failure_recovery.get("items", []),
        "limitations": _limitations(canonical_manifest, main_path, resource_gate, enriched),
        "experiment_suggestions": _experiment_suggestions(enriched),
    }
    html_body = _render_html(project_dir, manifest)
    write_json_artifact(project_dir, "v5/reports/product_report_manifest.json", manifest, producer="product_report", artifact_type="product_report_manifest")
    write_bytes_artifact(project_dir, "v5/reports/product_report.html", html_body.encode("utf-8"), producer="product_report", artifact_type="html_report")
    for rel, artifact_type in [
        ("v5/reports/product_report_manifest.json", "product_report_manifest"),
        ("v5/reports/product_report.html", "html_report"),
    ]:
        artifact = register_artifact(
            project_dir,
            rel,
            producer="evidence_synthesizer_reporter",
            artifact_type=artifact_type,
            expected_by_task_ids=["v5_product_report"],
            supports_subquestion_ids=["sq_v5_product_report"],
            producer_run_id=manifest["created_at"],
            qc_status="pass" if manifest["status"] in {"ready_for_review", "ready_for_signoff"} else "review_required",
            limitations=["Product report is for decision support and must be signed off by a human reviewer before external scientific claims."],
        )
        manifest.setdefault("artifact_ids", []).append(artifact["artifact_id"])
    write_json_artifact(project_dir, "v5/reports/product_report_manifest.json", manifest, producer="product_report", artifact_type="product_report_manifest")
    try:
        from targetcompass_lite.artifact_store import put_artifact

        manifest["object_store_records"] = [
            put_artifact(project_dir, "v5/reports/product_report.html", producer="product_report", artifact_type="html_report"),
            put_artifact(project_dir, "v5/reports/product_report_manifest.json", producer="product_report", artifact_type="product_report_manifest"),
        ]
        write_json_artifact(project_dir, "v5/reports/product_report_manifest.json", manifest, producer="product_report", artifact_type="product_report_manifest")
    except Exception as exc:
        manifest["object_store_warning"] = str(exc)
    return manifest


def _load_candidates(project_dir: Path, *, max_candidates: int) -> list[dict[str, Any]]:
    path = project_dir / "candidate_scores.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows.sort(key=lambda row: _score(row), reverse=True)
    selected = rows[:max_candidates]
    for index, row in enumerate(selected, start=1):
        row["rank"] = index
    return selected


def _load_cell_type_evidence(project_dir: Path) -> dict[str, list[dict[str, str]]]:
    path = project_dir / "results" / "cell_type_evidence" / "cell_type_evidence.tsv"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        gene = row.get("entity_symbol", "")
        if gene:
            grouped.setdefault(gene, []).append(row)
    return grouped


def _attach_candidate_context(row: dict[str, Any], cell_evidence: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
    gene = row.get("entity_symbol") or row.get("gene") or row.get("symbol") or ""
    score_json = _parse_json(row.get("score_json", "{}"))
    axes = (score_json.get("evidence_axis_coverage") or {})
    cells = cell_evidence.get(gene, [])
    return {
        "gene": gene,
        "route": row.get("route", ""),
        "final_score": row.get("final_score") or row.get("total_score") or row.get("score") or "",
        "tier": row.get("tier", ""),
        "hard_gate_status": row.get("hard_gate_status", ""),
        "safety_gate": row.get("safety_gate", ""),
        "evidence_refs": [item for item in str(row.get("evidence_refs", "")).split(";") if item],
        "covered_axes": axes.get("covered_axes", []),
        "missing_axes": axes.get("missing_axes", []),
        "coverage_fraction": axes.get("coverage_fraction", ""),
        "evidence_level_counts": score_json.get("evidence_level_counts", {}),
        "cell_type_evidence": [
            {
                "cell_type": item.get("cell_type", ""),
                "tissue": item.get("tissue", ""),
                "source": item.get("evidence_source", ""),
                "confidence": item.get("confidence", ""),
                "limitation": item.get("limitation", ""),
            }
            for item in cells[:3]
        ],
        "next_experiments": row.get("next_experiments", ""),
    }


def _report_status(canonical_manifest: dict[str, Any], main_path: dict[str, Any], resource_gate: dict[str, Any]) -> str:
    if not canonical_manifest and not main_path:
        return "draft_no_canonical_run"
    if main_path.get("status") == "blocked" or resource_gate.get("datasets_lockable_count", 0) == 0:
        return "candidate_review_required"
    if (canonical_manifest.get("human_review_gate") or {}).get("required", True):
        return "ready_for_review"
    return "ready_for_signoff"


def _limitations(canonical_manifest: dict[str, Any], main_path: dict[str, Any], resource_gate: dict[str, Any], candidates: list[dict[str, Any]]) -> list[str]:
    out = []
    if resource_gate and resource_gate.get("datasets_lockable_count", 0) == 0:
        out.append("No dataset is fully locked; candidate rankings must be treated as pending metadata correction and human review.")
    if main_path.get("status") == "blocked":
        out.append("Real-data main path is blocked; inspect v5/analysis_main_path/main_path_manifest.json for recovery actions.")
    if (canonical_manifest.get("human_review_gate") or {}).get("required", True):
        out.append("Human review gate is required before presenting candidates as project conclusions.")
    if not candidates:
        out.append("No candidate_scores.csv rows were available for ranking.")
    if candidates and any(row.get("missing_axes") for row in candidates):
        out.append("Some candidates lack one or more evidence axes; missing axes are shown per candidate.")
    return out or ["No major automated limitation detected; still require human scientific review before signoff."]


def _experiment_suggestions(candidates: list[dict[str, Any]]) -> list[dict[str, str]]:
    suggestions = []
    for row in candidates[:5]:
        gene = row.get("gene", "")
        if not gene:
            continue
        route = row.get("route", "candidate")
        suggestions.append(
            {
                "gene": gene,
                "suggestion": row.get("next_experiments")
                or f"Validate {gene} expression and {route} accessibility with qPCR/WB plus IF/flow in matched disease and control samples.",
            }
        )
    return suggestions


def _render_html(project_dir: Path, manifest: dict[str, Any]) -> str:
    rows = "".join(_candidate_row(row) for row in manifest.get("top_candidates", []))
    failures = "".join(
        "<li>"
        f"<strong>{html.escape(str(item.get('category', item.get('stage', 'recovery'))))}</strong>: "
        f"{html.escape(str(item.get('reason', '')))}"
        "</li>"
        for item in manifest.get("failures_and_recovery", [])[:8]
    )
    limitations = "".join(f"<li>{html.escape(item)}</li>" for item in manifest.get("limitations", []))
    experiments = "".join(
        f"<li><strong>{html.escape(item.get('gene', ''))}</strong>: {html.escape(item.get('suggestion', ''))}</li>"
        for item in manifest.get("experiment_suggestions", [])
    )
    chain = manifest.get("evidence_chain", {})
    gate = chain.get("human_review_gate", {})
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TargetCompass v5 Product Report - {html.escape(project_dir.name)}</title>
  <style>
    :root {{ --bg:#f7f8fb; --panel:#fff; --text:#111827; --muted:#64748b; --line:#e5e7eb; --blue:#2563eb; --green:#16a34a; --amber:#b45309; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Arial,sans-serif; }}
    main {{ max-width:1180px; margin:0 auto; padding:28px 22px 72px; }}
    section {{ background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:20px; margin-bottom:14px; }}
    h1 {{ margin:0; font-size:34px; letter-spacing:0; }}
    h2 {{ margin-top:0; }}
    .muted, small {{ color:var(--muted); line-height:1.5; }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .card {{ border:1px solid var(--line); border-radius:14px; padding:14px; background:#fbfdff; }}
    .card strong {{ display:block; font-size:22px; margin-top:4px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ padding:9px 7px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:13px; }}
    th {{ color:var(--muted); }}
    code {{ background:#eef2ff; border-radius:6px; padding:2px 5px; }}
    .pill {{ display:inline-flex; border-radius:999px; padding:3px 8px; background:#eef2ff; color:var(--blue); font-weight:700; font-size:12px; }}
    .warn {{ color:var(--amber); }}
    @media (max-width:840px) {{ .grid {{ grid-template-columns:1fr 1fr; }} main {{ padding:18px 12px; }} }}
  </style>
</head>
<body>
<main>
  <section>
    <h1>TargetCompass v5 项目报告</h1>
    <p class="muted">Project: <code>{html.escape(project_dir.name)}</code> · Status: <span class="pill">{html.escape(manifest.get("status", ""))}</span> · Created: {html.escape(manifest.get("created_at", ""))}</p>
    <p>本报告按 v5 canonical 证据链生成，展示候选排序、证据覆盖、失败恢复、限制和下一步实验建议。若 human review gate 未关闭，结论只能作为候选优先级，不能作为最终靶点声明。</p>
    <div class="grid">
      <div class="card"><small>候选数</small><strong>{html.escape(str(manifest.get("candidate_count", 0)))}</strong></div>
      <div class="card"><small>Artifact</small><strong>{html.escape(str(chain.get("artifact_count", 0)))}</strong></div>
      <div class="card"><small>TaskRun</small><strong>{html.escape(str(len(chain.get("task_run_refs", []))))}</strong></div>
      <div class="card"><small>Human gate</small><strong>{html.escape(str(gate.get("required", True)))}</strong></div>
    </div>
  </section>
  <section>
    <h2>候选排序</h2>
    <table><thead><tr><th>Rank</th><th>分子</th><th>Route</th><th>Score</th><th>Gate</th><th>证据轴</th><th>细胞/组织证据</th><th>建议实验</th></tr></thead><tbody>{rows or '<tr><td colspan="8">No candidate ranking available.</td></tr>'}</tbody></table>
  </section>
  <section>
    <h2>证据链与审批</h2>
    <p class="muted">Claim ceiling: <code>{html.escape(str((chain.get("claim_ceiling") or {}).get("max_allowed_claim", "association")))}</code> · Gate reason: {html.escape(str(gate.get("reason", "")))}</p>
    <p class="muted">Refs: <code>{html.escape(json.dumps(manifest.get("refs", {}), ensure_ascii=False))}</code></p>
  </section>
  <section>
    <h2>失败项与恢复建议</h2>
    <ul>{failures or '<li>No open failure recovery item recorded.</li>'}</ul>
  </section>
  <section>
    <h2>限制</h2>
    <ul>{limitations}</ul>
  </section>
  <section>
    <h2>实验建议</h2>
    <ul>{experiments or '<li>No candidate-specific experiment suggestion available.</li>'}</ul>
  </section>
</main>
</body>
</html>"""


def _candidate_row(row: dict[str, Any]) -> str:
    cells = "; ".join(
        f"{item.get('cell_type', '')} / {item.get('tissue', '')} ({item.get('source', '')})"
        for item in row.get("cell_type_evidence", [])
    )
    axes = "covered: " + ", ".join(row.get("covered_axes", [])[:5])
    if row.get("missing_axes"):
        axes += " | missing: " + ", ".join(row.get("missing_axes", [])[:4])
    gate = f"{row.get('tier', '')} / {row.get('hard_gate_status', '')} / {row.get('safety_gate', '')}"
    return (
        "<tr>"
        f"<td>{html.escape(str(row.get('rank', '')))}</td>"
        f"<td><strong>{html.escape(row.get('gene', ''))}</strong><small>{html.escape(str(row.get('evidence_level_counts', {})))}</small></td>"
        f"<td>{html.escape(row.get('route', ''))}</td>"
        f"<td>{html.escape(str(row.get('final_score', '')))}</td>"
        f"<td>{html.escape(gate)}</td>"
        f"<td>{html.escape(axes)}</td>"
        f"<td>{html.escape(cells or 'not resolved')}</td>"
        f"<td>{html.escape(row.get('next_experiments', '') or 'qPCR/WB; IF/flow; perturbation assay')}</td>"
        "</tr>"
    )


def _score(row: dict[str, Any]) -> float:
    for key in ["final_score", "total_score", "score"]:
        try:
            return float(row.get(key, "") or 0)
        except ValueError:
            continue
    return 0.0


def _parse_json(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback
