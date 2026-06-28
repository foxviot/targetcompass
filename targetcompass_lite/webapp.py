import csv
import html
import json
import os
import socket
import threading
import urllib.parse
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent import TargetDiscoveryAgent
from .adapter_audit import build_adapter_audit
from .annotation import annotate_project
from .cli import init_project
from .codex_engineering import load_codex_engineering
from .codex_task_queue import claim_codex_task, execute_codex_queue, execute_codex_queue_task, sync_codex_task_queue
from .consistency import run_consistency_check
from .db_adapters import available_database_adapters
from .database_validation import validate_online_databases
from .cell_type_evidence import build_cell_type_evidence
from .enrichment import run_enrichment
from .evidence_db import build_evidence_db_snapshot, import_evidence, migrate_evidence_db, query_evidence_items
from .experiment_design import design_experiments
from .geo_discovery import discover_geo_datasets, load_recommendations
from .geo_importer import GeoImportError, geo_status_path, import_geo_series, import_geo_series_auto
from .ideas import load_ideas
from .i18n import LANGUAGE_LABELS, SUPPORTED_LANGUAGES, get_language, set_language, translator
from .knowledge import adapt_resources, add_resource, load_registry, remove_resource
from .matching import match_project
from .methods import (
    available_project_methods,
    delete_markdown_method,
    install_markdown_method,
    list_markdown_methods,
    load_method_config,
    save_method_config,
)
from .mcp_sessions import check_external_auth_readiness, create_token, load_sessions, load_token_registry, query_mcp_audit, update_policy
from .observability import build_observability_manifest
from .mcp_policy import write_default_policy
from .package import export_run_package
from .platform_config import (
    load_platform_config,
    platform_readiness,
    save_platform_config,
    service_status,
    write_update_manifest,
)
from .platform_service_control import build_service_control_manifest
from .platform_admin import build_backend_primary_status, build_data_cache_manifest, build_platform_p1_readiness, build_platform_p2_readiness, build_platform_production_readiness, cleanup_data_cache, query_platform_audit
from .production_storage import build_production_storage_readiness
from .project_manager import (
    archive_project,
    create_project,
    delete_project,
    export_project,
    import_project,
    list_projects,
)
from .release_acceptance import build_release_acceptance_manifest
from .qc_review import apply_qc_review, apply_qc_review_batch, build_qc_review_queue
from .reporting import build_report
from .recovery_center import build_recovery_manifest, retry_database_sources
from .review import build_review_queue, final_signoff, load_approval_state, load_reviews, record_review
from .evidence_index import evidence_trace_detail, query_evidence_trace
from .nextflow_runner import build_nextflow_tasks, run_nextflow_local
from .orchestrator import get_orchestrator_status, partial_rerun_orchestrator
from .reset_demo import reset_demo_outputs
from .run_state import new_run_id, read_status, request_cancel, write_status
from .scoring import score_project
from .secrets import apply_project_secrets, clear_openai_api_key, llm_provider_summary, masked_openai_key, save_llm_provider, save_openai_api_key
from .service_topology import build_service_topology
from .status_ui import build_status_center
from .system_status import system_status
from .validators import load_dataset_card
from .v4 import build_v4_manifest, load_codex_task_packet, load_v4_work_orders, read_work_order_attempts
from .work_order_dag import load_work_order_dag
from .role_runner import load_role_runs


class _Args:
    def __init__(self, project: str, dataset: list[str] | None = None):
        self.project = project
        self.dataset = dataset or []


def _read_text(path: Path, fallback: str = "") -> str:
    return path.read_text(encoding="utf-8") if path.exists() else fallback


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _read_json_dir(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [_read_json(item, {}) for item in sorted(path.glob("*.json")) if item.is_file()]


def _safe_project_id(value: str) -> str:
    cleaned = "".join(ch for ch in value.strip() if ch.isalnum() or ch in {"_", "-"})
    if cleaned in {"", ".", ".."}:
        return ""
    return cleaned[:80]


def _load_v5_codex_queue(project_dir: Path) -> dict[str, list[dict]]:
    root = project_dir / "v5" / "codex"
    return {queue: _read_json_dir(root / queue) for queue in ["pending", "approved", "claimed", "completed", "failed"]}


def _count_by(rows: list[dict], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field, "") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return " · ".join(f"{key}:{value}" for key, value in sorted(counts.items()))


def _status_path(project_dir: Path) -> Path:
    return project_dir / "results" / "run_status.json"


def _ui_theme_path(project_dir: Path) -> Path:
    return project_dir / "configs" / "ui_theme.json"


def _read_theme(project_dir: Path) -> str:
    path = _ui_theme_path(project_dir)
    if not path.exists():
        return "light"
    try:
        theme = json.loads(path.read_text(encoding="utf-8")).get("theme", "light")
    except json.JSONDecodeError:
        return "light"
    return theme if theme in {"light", "dark"} else "light"


def _write_theme(project_dir: Path, theme: str) -> str:
    selected = theme if theme in {"light", "dark"} else "light"
    path = _ui_theme_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"theme": selected}, indent=2), encoding="utf-8")
    return selected


def _parse_multipart_form(headers, body: bytes) -> dict[str, dict[str, str | bytes]]:
    content_type = headers.get("Content-Type", "")
    raw = b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    message = BytesParser(policy=email_policy).parsebytes(raw)
    fields: dict[str, dict[str, str | bytes]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename() or ""
        payload = part.get_payload(decode=True) or b""
        fields[name] = {"filename": filename, "content": payload}
    return fields


def _write_status(
    project_dir: Path,
    status: str,
    message: str,
    stdout: str = "",
    stderr: str = "",
    stages: list[dict] | None = None,
) -> None:
    write_status(project_dir, status, message, stdout, stderr, stages)


def _read_status(project_dir: Path) -> dict:
    return read_status(project_dir)


def _dataset_controls(project_dir: Path) -> str:
    controls = []
    for idx, path in enumerate(sorted((project_dir / "dataset_cards").glob("*.yaml")), 1):
        card = load_dataset_card(path)
        dataset_id = card.get("dataset_id", path.stem)
        source = card.get("source", "unknown")
        title = " / ".join([dataset_id, card.get("tissue", "unknown"), card.get("accession", "unknown")])
        controls.append(
            '<label class="dataset-card">'
            f'<input type="checkbox" name="dataset" value="{html.escape(dataset_id)}" checked>'
            '<span class="dataset-toggle"></span>'
            '<span class="dataset-copy">'
            f'<span class="dataset-index">0{idx}</span>'
            f'<strong>{html.escape(title)}</strong>'
            f'<small>{html.escape(source)} · {html.escape(card.get("modality", "unknown"))}</small>'
            "</span>"
            "</label>"
        )
    if not controls:
        return '<p class="muted">No dataset cards found.</p>'
    return "".join(controls)


def _parser_options() -> str:
    has_key = bool(os.environ.get("OPENAI_API_KEY"))
    gpt_selected = " selected" if has_key else ""
    rule_selected = "" if has_key else " selected"
    return (
        f'<option value="gpt"{gpt_selected}>GPT generator via OpenAI API</option>'
        f'<option value="rule_based"{rule_selected}>Local deterministic fallback</option>'
    )


def _run_status(project_dir: Path) -> str:
    status = _read_status(project_dir)
    css = html.escape(status.get("status", "idle"))
    stdout = html.escape(status.get("stdout", "").strip())
    stderr = html.escape(status.get("stderr", "").strip())
    logs = ""
    if stdout:
        logs += f"<details><summary>stdout</summary><pre>{stdout}</pre></details>"
    if stderr:
        logs += f"<details open><summary>stderr</summary><pre>{stderr}</pre></details>"
    failure = status.get("failure_reason", "")
    failure_html = f'<p class="muted">Failure: {html.escape(failure)}</p>' if failure else ""
    stages = status.get("stages", [])
    stage_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('name', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        "</tr>"
        for row in stages[-8:]
    )
    stage_table = (
        "<details><summary>stage status</summary><table><thead><tr><th>Stage</th><th>Status</th><th>Message</th></tr></thead>"
        f"<tbody>{stage_rows}</tbody></table></details>"
        if stage_rows
        else ""
    )
    controls = """
      <form class="mini-form" method="post">
        <div class="actions">
          <button class="ghost" type="submit" formaction="/run/cancel">Cancel run</button>
          <button class="ghost" type="submit" formaction="/run/rerun">Rerun last request</button>
          <select name="partial_stage">
            <option value="annotation">Recompute annotation</option>
            <option value="enrichment">Recompute enrichment</option>
            <option value="evidence">Reimport evidence</option>
            <option value="scoring">Recompute scoring</option>
            <option value="report">Rebuild report</option>
          </select>
          <button class="ghost" type="submit" formaction="/run/partial">Run selected step</button>
        </div>
      </form>
    """
    return (
        f'<div class="status {css}"><span></span><strong>{html.escape(status.get("status", "idle"))}</strong>'
        f'<p>{html.escape(status.get("message", ""))}</p>'
        f'<p class="muted">run_id: {html.escape(status.get("run_id", "")) or "none"} · active_stage: {html.escape(status.get("active_stage", "")) or "none"}</p>'
        f"</div>{failure_html}{stage_table}{logs}{controls}"
    )


def _run_status(project_dir: Path) -> str:
    center = build_status_center(project_dir)
    orchestrator = get_orchestrator_status(project_dir)
    orch_run = orchestrator.get("selected_run", {})
    status = center["run"]
    css = html.escape(status.get("status", "idle"))
    stdout = html.escape(status.get("stdout", "").strip())
    stderr = html.escape(status.get("stderr", "").strip())
    logs = ""
    if stdout:
        logs += f"<details><summary>stdout log</summary><pre>{stdout}</pre></details>"
    if stderr:
        logs += f"<details open><summary>stderr log</summary><pre>{stderr}</pre></details>"
    stage_cards = "".join(
        '<div class="audit-card">'
        f'<span class="pill {html.escape(row.get("status", "pending").lower())}">{html.escape(row.get("status", "pending").upper())}</span>'
        f'<strong>{html.escape(row.get("name", "").replace("_", " ").title())}</strong>'
        f'<small>{html.escape(row.get("message") or row.get("purpose") or "Waiting for this stage.")}</small>'
        "</div>"
        for row in center["stage_cards"]
    )
    recovery_cards = "".join(
        '<div class="idea-row">'
        f'<span class="pill {"failed" if "failed" in row.get("title", "").lower() else "review"}">ACTION</span>'
        "<div>"
        f'<strong>{html.escape(row.get("title", ""))}</strong>'
        f'<small>{html.escape(row.get("message", ""))}</small>'
        + (
            "<ul>"
            + "".join(f"<li>{html.escape(action)}</li>" for action in row.get("actions", [])[:4])
            + "</ul>"
            if row.get("actions")
            else ""
        )
        + (f'<small>Status file: <code>{html.escape(row.get("status_file", ""))}</code></small>' if row.get("status_file") else "")
        + "</div></div>"
        for row in center["recovery"]
    )
    geo_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('accession', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('code', '') or row.get('stage', ''))}</td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        f"<td><code>{html.escape(row.get('path', ''))}</code></td>"
        "</tr>"
        for row in center["geo_statuses"]
    )
    geo_table = (
        "<details><summary>GEO status files</summary><table><thead><tr><th>GSE</th><th>Status</th><th>Code/Stage</th><th>Message</th><th>File</th></tr></thead>"
        f"<tbody>{geo_rows}</tbody></table></details>"
        if geo_rows
        else ""
    )
    orch_html = (
        '<div class="audit-card">'
        '<small>Orchestrator Run API</small>'
        f'<strong>{html.escape(orchestrator.get("status", "idle"))}</strong>'
        f'<small>run: <code>{html.escape(orchestrator.get("orchestrator_run_id", "") or "none")}</code></small>'
        f'<small>idempotency: <code>{html.escape(orch_run.get("idempotency_key", "") or "none")}</code></small>'
        f'<small>state: <code>{html.escape(", ".join(orchestrator.get("state_refs", {}).values()))}</code></small>'
        "</div>"
    )
    partial_buttons = "".join(
        f'<button class="ghost" name="partial_stage" value="{html.escape(action["id"])}" type="submit" title="{html.escape(action["description"])}">{html.escape(action["label"])}</button>'
        for action in center["partial_actions"]
    )
    controls = f"""
      <form class="mini-form" method="post">
        <div class="actions">
          <button class="ghost" type="submit" formaction="/run/cancel">Cancel run</button>
          <button class="ghost" type="submit" formaction="/run/rerun">Rerun last request</button>
        </div>
      </form>
      <form class="mini-form" method="post" action="/run/partial">
        <div class="actions">{partial_buttons}</div>
      </form>
    """
    return (
        f'<div class="status {css}"><span></span><strong>{html.escape(status.get("status", "idle"))}</strong>'
        f'<p>{html.escape(status.get("message", ""))}</p>'
        f'<p class="muted">run_id: {html.escape(status.get("run_id", "")) or "none"} · active_stage: {html.escape(status.get("active_stage", "")) or "none"} · status file: <code>{html.escape(center.get("run_status_file", ""))}</code> · attempts: <code>{html.escape(status.get("work_order_attempts", ""))}</code></p>'
        f'</div><h3>Orchestrator</h3>{orch_html}<h3>Stage cards</h3><div class="audit-grid">{stage_cards}</div><h3>Recovery center</h3>{recovery_cards}{geo_table}{logs}{controls}'
    )


def _spec_summary(project_dir: Path) -> str:
    spec_path = project_dir / "research_spec.json"
    if not spec_path.exists():
        return "<p>No ResearchSpec yet.</p>"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    metadata = spec.get("parser_metadata", {})
    tiles = [
        ("Disease", spec.get("disease_scope", {}).get("canonical", "unknown")),
        ("Organism", ", ".join(spec.get("organisms", []))),
        ("Tissue", ", ".join(spec.get("priority_tissues", []))),
        ("Route", ", ".join(spec.get("target_routes", []))),
        ("Parser", f"{metadata.get('parser_version', 'manual_or_legacy')} / {metadata.get('confidence', 'unknown')}"),
        ("Confirmed", str(metadata.get("confirmed", True))),
    ]
    return "".join(
        f'<div class="spec-tile"><small>{html.escape(k)}</small><strong>{html.escape(v)}</strong></div>' for k, v in tiles
    )


def _match_summary(project_dir: Path) -> str:
    try:
        rows = match_project(project_dir)
    except Exception as exc:
        return f"<p>Dataset match check unavailable: {html.escape(str(exc))}</p>"
    items = []
    for row in rows:
        css = row["match_status"].lower()
        items.append(
            "<tr>"
            f"<td>{html.escape(row['dataset_id'])}</td>"
            f'<td><span class="pill {css}">{html.escape(row["match_status"])}</span></td>'
            f"<td>{row['match_score']}</td>"
            f"<td>{html.escape(row['warnings'] or row['reasons'] or 'none')}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>Dataset</th><th>Status</th><th>Score</th><th>Review note</th></tr></thead>"
        f"<tbody>{''.join(items)}</tbody></table>"
    )


def _audit_panel(project_dir: Path) -> str:
    scores = _read_csv(project_dir / "candidate_scores.csv")
    matches = _read_csv(project_dir / "dataset_match_report.csv")
    unknown_review = _read_text(project_dir / "results" / "annotation" / "unknown_review.tsv")
    review_count = sum(1 for row in matches if row.get("match_status") != "MATCH")
    hard_gate_count = sum(1 for row in scores[:20] if row.get("hard_gate_status") != "PASS")
    unknown_rows = max(0, len([line for line in unknown_review.splitlines() if line.strip()]) - 1)
    cards = [
        ("Spec", "PASS", "ResearchSpec schema and confirmation gate"),
        ("Dataset", "REVIEW" if review_count else "PASS", f"{review_count} dataset match warning(s)"),
        ("Candidate", "REVIEW" if hard_gate_count else "PASS", f"{hard_gate_count} top candidate hard-gate issue(s)"),
        ("Annotation", "REVIEW" if unknown_rows else "PASS", f"{unknown_rows} UNKNOWN annotation row(s)"),
    ]
    return "".join(
        '<div class="audit-card">'
        f'<span class="pill {status.lower()}">{status}</span>'
        f"<strong>{html.escape(title)}</strong>"
        f"<small>{html.escape(note)}</small>"
        "</div>"
        for title, status, note in cards
    )


def _agent_trace(project_dir: Path) -> str:
    trace_path = project_dir / "results" / "agent_trace.json"
    if not trace_path.exists():
        return '<p class="muted">No agent trace yet.</p>'
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    items = []
    for stage in trace.get("stages", []):
        status = html.escape(stage.get("status", "unknown"))
        items.append(
            '<div class="timeline-item">'
            f'<span class="timeline-dot {status}"></span>'
            "<div>"
            f'<strong>{html.escape(stage.get("name", "stage").replace("_", " ").title())}</strong>'
            f'<small>{html.escape(stage.get("message", ""))}</small>'
            "</div>"
            "</div>"
        )
    return "".join(items) if items else '<p class="muted">No agent stages recorded.</p>'


def _idea_panel(project_dir: Path) -> str:
    ideas = load_ideas(project_dir)[:8]
    if not ideas:
        return '<p class="muted">No generated ideas yet.</p>'
    rows = []
    for idea in ideas:
        status = idea.get("execution_status", "review")
        rows.append(
            '<div class="idea-row">'
            f'<span class="pill {html.escape(status)}">{html.escape(status.upper())}</span>'
            "<div>"
            f'<strong>{html.escape(idea.get("title", ""))}</strong>'
            f'<small>{html.escape(idea.get("route", ""))} · feasibility {idea.get("feasibility_score", 0)} · data {idea.get("data_fit_score", 0)}</small>'
            "</div>"
            "</div>"
        )
    return "".join(rows)


def _idea_review_panel(project_dir: Path) -> str:
    _, t = translator(project_dir)
    ideas = load_ideas(project_dir)[:8]
    if not ideas:
        return '<p class="muted">No generated ideas yet.</p>'
    rows = []
    for idea in ideas:
        status = idea.get("execution_status", "review")
        idea_id = html.escape(idea.get("idea_id", ""))
        rows.append(
            '<div class="idea-row">'
            f'<span class="pill {html.escape(status)}">{html.escape(status.upper())}</span>'
            "<div>"
            f'<strong>{html.escape(idea.get("title", ""))}</strong>'
            f'<small>{html.escape(idea.get("route", ""))} · feasibility {idea.get("feasibility_score", 0)} · data {idea.get("data_fit_score", 0)} · review {html.escape(idea.get("review_status", "pending"))}</small>'
            '<form class="mini-form review-form" method="post" action="/review">'
            '<input type="hidden" name="item_type" value="idea">'
            f'<input type="hidden" name="item_id" value="{idea_id}">'
            f'<input type="hidden" name="report_ref" value="reports/target_report.html#idea-{idea_id}">'
            f'<input type="text" name="reason" placeholder="审批理由">'
            f'<input type="text" name="note" placeholder="{html.escape(t("review_note"))}">'
            f'<button class="small-button" name="action" value="approve" type="submit">{html.escape(t("approve"))}</button>'
            f'<button class="small-button ghost" name="action" value="needs_review" type="submit">{html.escape(t("review"))}</button>'
            f'<button class="small-button ghost" name="action" value="reject" type="submit">{html.escape(t("reject"))}</button>'
            "</form>"
            "</div>"
            "</div>"
        )
    return "".join(rows)


def _experiment_panel(project_dir: Path) -> str:
    path = project_dir / "results" / "experiments" / "experiment_designs.json"
    if not path.exists():
        return '<p class="muted">No experiment designs yet.</p>'
    designs = json.loads(path.read_text(encoding="utf-8"))[:5]
    return "".join(
        '<div class="idea-row">'
        '<span class="pill pass">DESIGN</span>'
        "<div>"
        f'<strong>{html.escape(design.get("title", ""))}</strong>'
        f'<small>{html.escape(design.get("objective", ""))}</small>'
        "</div>"
        "</div>"
        for design in designs
    )


def _knowledge_panel(project_dir: Path) -> str:
    _, t = translator(project_dir)
    resources = load_registry(project_dir)
    validation = _read_json(project_dir / "results" / "database_validation" / "online_database_validation.json", {})
    validation_html = _database_validation_panel(validation)
    if not resources:
        return '<p class="muted">No custom knowledge or database resources registered.</p>' + validation_html
    rows = []
    for row in resources:
        resource_id = html.escape(row.get("resource_id", ""))
        rows.append(
            '<div class="resource-row">'
            "<div>"
            f'<strong>{resource_id}</strong>'
            f'<small>{html.escape(row.get("resource_type", ""))} · {html.escape(row.get("status", "registered"))}</small>'
            "</div>"
            '<form class="mini-form" method="post" action="/knowledge/delete">'
            f'<input type="hidden" name="resource_id" value="{resource_id}">'
            f'<button class="small-button" type="submit">{html.escape(t("remove"))}</button>'
            "</form>"
            "</div>"
        )
    return "".join(rows) + validation_html


def _database_validation_panel(validation: dict) -> str:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('source_id', ''))}</td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(str(row.get('row_count', 0)))}</td>"
        f"<td>{html.escape(row.get('adapter', ''))}</td>"
        f"<td><code>{html.escape(row.get('source_path', ''))}</code></td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        "</tr>"
        for row in validation.get("sources", [])
    )
    table = (
        "<table><thead><tr><th>Source</th><th>Status</th><th>Rows</th><th>Adapter</th><th>File</th><th>Message</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if rows
        else '<p class="muted">No online database validation run yet.</p>'
    )
    return (
        "<details open><summary>Online database validation</summary>"
        f'<p class="muted">success: {html.escape(str(validation.get("success_count", 0)))} / {html.escape(str(validation.get("source_count", 0)))} · adapted: {html.escape(str(validation.get("adapted_count", 0)))} · artifact: <code>results/database_validation/online_database_validation.json</code></p>'
        '<form class="mini-form" method="post" action="/database/validate">'
        '<label for="db_validate_genes">Genes</label>'
        '<input id="db_validate_genes" name="genes" type="text" placeholder="IL6,CXCL8,CCL2,TNF,AEBP1">'
        '<label for="db_validate_query">Disease / trait query</label>'
        '<input id="db_validate_query" name="query" type="text" placeholder="type 2 diabetes skeletal muscle">'
        '<div class="actions"><button type="submit">Validate online databases</button></div>'
        "</form>"
        + table
        + "</details>"
    )


def _review_panel(project_dir: Path) -> str:
    reviews = load_reviews(project_dir)[-6:]
    if not reviews:
        return '<p class="muted">No manual review actions yet.</p>'
    return "".join(
        '<div class="idea-row">'
        f'<span class="pill {html.escape(row.get("action", ""))}">{html.escape(row.get("action", "").upper())}</span>'
        "<div>"
        f'<strong>{html.escape(row.get("item_type", ""))}: {html.escape(row.get("item_id", ""))}</strong>'
        f'<small>{html.escape(row.get("review_id", ""))} · {html.escape(row.get("reason", row.get("note", "")))} · {html.escape(row.get("report_ref", ""))}</small>'
        "</div>"
        "</div>"
        for row in reviews
    )


def _approval_panel(project_dir: Path) -> str:
    queue = build_review_queue(project_dir)
    state = load_approval_state(project_dir)
    items = queue.get("items", [])[:6]
    queue_html = (
        "".join(
            '<div class="idea-row">'
            f'<span class="pill review">{html.escape(row.get("review_status", "pending").upper())}</span>'
            "<div>"
            f'<strong>{html.escape(row.get("title", row.get("item_id", "")))}</strong>'
            f'<small>{html.escape(row.get("item_id", ""))} · {html.escape(row.get("report_ref", ""))}</small>'
            "</div>"
            "</div>"
            for row in items
        )
        if items
        else '<p class="muted">Review queue is empty.</p>'
    )
    return f"""
    <div class="method-grid">
      <div><small>Approval status</small><strong>{html.escape(state.get("status", "draft"))}</strong></div>
      <div><small>Review queue</small><strong>{queue.get("queue_count", 0)}</strong></div>
      <div><small>Approved</small><strong>{queue.get("approved_count", 0)}</strong></div>
      <div><small>Rejected</small><strong>{queue.get("rejected_count", 0)}</strong></div>
    </div>
    {queue_html}
    <form class="mini-form review-form" method="post" action="/approval/signoff">
      <input type="text" name="signer" placeholder="signer" value="human">
      <input type="text" name="reason" placeholder="final signoff reason">
      <button class="small-button" name="status" value="signed_off" type="submit">Final signoff</button>
      <button class="small-button ghost" name="status" value="rejected" type="submit">Reject package</button>
    </form>
    """


def _database_adapter_options() -> str:
    return "".join(
        f'<option value="{html.escape(adapter["adapter_id"])}">{html.escape(adapter["label"])}</option>'
        for adapter in available_database_adapters()
    )


def _split_patterns(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _geo_import_panel() -> str:
    return """
    <form class="mini-form" method="post" action="/geo/discover">
      <label for="geo_discovery_query">GEO auto-discovery query override</label>
      <input id="geo_discovery_query" name="query" type="text" placeholder="optional; leave empty to use ResearchSpec">
      <label for="geo_discovery_limit">Recommendation count</label>
      <input id="geo_discovery_limit" name="limit" type="number" min="1" max="20" value="8">
      <div class="actions">
        <button type="submit">Recommend GEO datasets</button>
      </div>
    </form>
    """


def _geo_recommendation_panel(project_dir: Path) -> str:
    rows = load_recommendations(project_dir)
    if not rows:
        return '<p class="muted">No GEO recommendations yet. Run auto-discovery to generate candidate GSE datasets.</p>'
    body = []
    for row in rows[:10]:
        reasons = "; ".join(row.get("reasons", []))
        warnings = "; ".join(row.get("warnings", []))
        body.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('accession', '')))}</td>"
            f"<td>{html.escape(str(row.get('score', '')))}</td>"
            f"<td>{html.escape(str(row.get('title', '')))}</td>"
            f"<td>{html.escape(str(row.get('organism', '')))}</td>"
            f"<td>{html.escape(str(row.get('sample_count', '')))}</td>"
            f"<td>{html.escape(str(row.get('import_status', '')))}</td>"
            f"<td>{html.escape(reasons or warnings or 'manual review required')}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr><th>GSE</th><th>Score</th><th>Title</th><th>Organism</th><th>Samples</th><th>Status</th><th>Reason</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


def _geo_recovery_center(project_dir: Path) -> str:
    statuses = build_status_center(project_dir).get("geo_statuses", [])
    if not statuses:
        return '<p class="muted">No GEO/GSE import status yet. Import or auto-discover a dataset to populate recovery guidance.</p>'
    cards = []
    for row in statuses:
        status = row.get("status", "unknown")
        code = row.get("code") or row.get("stage") or status
        details = row.get("details", {}) or {}
        actions = row.get("recovery_actions", [])
        detail_items = []
        for key in ["case_n", "control_n", "assigned_samples", "confidence", "best_column", "sample_count", "matrix_rows"]:
            if key in details:
                detail_items.append(f"{key}: {details[key]}")
        if details.get("warnings"):
            detail_items.append("warnings: " + "; ".join(str(item) for item in details.get("warnings", [])[:3]))
        recovery_list = "".join(f"<li>{html.escape(str(item))}</li>" for item in row.get("recovery", [])[:5])
        cards.append(
            '<div class="recovery-card">'
            '<div class="recovery-card-head">'
            f'<div><strong>{html.escape(row.get("accession", ""))}</strong><small>{html.escape(row.get("stage", ""))} · <code>{html.escape(row.get("path", ""))}</code></small></div>'
            f'<span class="pill {html.escape(status)}">{html.escape(code)}</span>'
            "</div>"
            f'<p>{html.escape(row.get("message", ""))}</p>'
            + (f'<ul>{recovery_list}</ul>' if recovery_list else "")
            + (f'<small>{html.escape(" | ".join(detail_items))}</small>' if detail_items else "")
            + _geo_recovery_action_forms(row, actions)
            + "</div>"
        )
    return '<div class="recovery-grid">' + "".join(cards) + "</div>"


def _geo_recovery_action_forms(row: dict, actions: list[dict[str, str]]) -> str:
    accession = html.escape(str(row.get("accession", "")))
    forms = []
    for action in actions[:5]:
        mode = action.get("mode", "auto")
        if action.get("route") == "/geo/import":
            forms.append(
                '<form class="mini-form recovery-form" method="post" action="/geo/import">'
                f'<input type="hidden" name="accession" value="{accession}">'
                '<input type="text" name="case_label" placeholder="case label">'
                '<input type="text" name="control_label" placeholder="control label">'
                '<input type="text" name="case_patterns" placeholder="case keywords / regex">'
                '<input type="text" name="control_patterns" placeholder="control keywords / regex">'
                '<input type="text" name="tissue" placeholder="tissue">'
                '<input type="hidden" name="organism" value="human">'
                f'<button class="small-button ghost" type="submit">{html.escape(action["label"])}</button>'
                "</form>"
            )
            continue
        confidence = "35" if mode == "low_confidence" else "55"
        force = "1" if mode == "auto_force" else ""
        forms.append(
            '<form class="mini-form recovery-form" method="post" action="/geo/import-auto">'
            f'<input type="hidden" name="accession" value="{accession}">'
            f'<input type="hidden" name="force_download" value="{force}">'
            f'<input type="number" name="min_confidence" min="0" max="100" value="{confidence}" title="minimum confidence">'
            '<input type="text" name="case_hint" placeholder="case hint">'
            '<input type="text" name="control_hint" placeholder="control hint">'
            '<input type="text" name="platform_annotation" placeholder="optional platform annotation path">'
            '<input type="hidden" name="organism" value="human">'
            f'<button class="small-button ghost" type="submit">{html.escape(action["label"])}</button>'
            "</form>"
        )
    return '<div class="recovery-actions">' + "".join(forms) + "</div>"


def _geo_import_form() -> str:
    return """
    <form class="mini-form" method="post" action="/geo/import-auto">
      <label for="geo_auto_accession">Auto import GEO / GSE accession</label>
      <input id="geo_auto_accession" name="accession" type="text" placeholder="GSE312006">
      <label for="geo_auto_case_hint">Optional case hint</label>
      <input id="geo_auto_case_hint" name="case_hint" type="text" placeholder="senescent, aged, disease">
      <label for="geo_auto_control_hint">Optional control hint</label>
      <input id="geo_auto_control_hint" name="control_hint" type="text" placeholder="young, control, normal">
      <label for="geo_auto_case_label">Optional output case label</label>
      <input id="geo_auto_case_label" name="case_label" type="text" placeholder="senescent">
      <label for="geo_auto_control_label">Optional output control label</label>
      <input id="geo_auto_control_label" name="control_label" type="text" placeholder="young">
      <label for="geo_auto_tissue">Tissue</label>
      <input id="geo_auto_tissue" name="tissue" type="text" placeholder="vascular endothelium">
      <label for="geo_auto_organism">Organism</label>
      <input id="geo_auto_organism" name="organism" type="text" value="human">
      <label for="geo_auto_platform_annotation">Optional local platform annotation path</label>
      <input id="geo_auto_platform_annotation" name="platform_annotation" type="text" placeholder="D:\\path\\to\\GPLxxxx.annot.gz">
      <label for="geo_auto_min_confidence">Minimum inference confidence</label>
      <input id="geo_auto_min_confidence" name="min_confidence" type="number" min="0" max="100" value="55">
      <div class="actions">
        <button type="submit">Auto import and infer groups</button>
      </div>
    </form>
    <form class="mini-form" method="post" action="/geo/import">
      <label for="geo_accession">GEO / GSE accession</label>
      <input id="geo_accession" name="accession" type="text" placeholder="GSE43292">
      <label for="geo_case_label">Case group label</label>
      <input id="geo_case_label" name="case_label" type="text" placeholder="senescent">
      <label for="geo_control_label">Control group label</label>
      <input id="geo_control_label" name="control_label" type="text" placeholder="young">
      <label for="geo_case_patterns">Case keywords / regex, comma separated</label>
      <input id="geo_case_patterns" name="case_patterns" type="text" placeholder="senescent, aged, atheroma">
      <label for="geo_control_patterns">Control keywords / regex, comma separated</label>
      <input id="geo_control_patterns" name="control_patterns" type="text" placeholder="young, control, intact">
      <label for="geo_tissue">Tissue</label>
      <input id="geo_tissue" name="tissue" type="text" placeholder="vascular endothelium">
      <label for="geo_organism">Organism</label>
      <input id="geo_organism" name="organism" type="text" value="human">
      <label for="geo_platform_annotation">Optional local platform annotation path</label>
      <input id="geo_platform_annotation" name="platform_annotation" type="text" placeholder="D:\\path\\to\\GPLxxxx.annot.gz">
      <div class="actions">
        <button type="submit">Import GEO dataset</button>
      </div>
    </form>
    """


def _geo_error_message(project_dir: Path, accession: str, exc: GeoImportError) -> str:
    recovery = " ".join(f"[{idx + 1}] {item}" for idx, item in enumerate(exc.recovery))
    return (
        f"GEO import failed ({exc.code}) at {exc.stage}: {exc.message} "
        f"Recovery: {recovery} Status file: {geo_status_path(project_dir, accession)}"
    )


def _method_select(project_dir: Path, stage: str) -> str:
    selected = load_method_config(project_dir).get(stage, "")
    options = []
    for method in available_project_methods(project_dir).get(stage, []):
        method_id = str(method["method_id"])
        marker = " selected" if method_id == selected else ""
        options.append(f'<option value="{html.escape(method_id)}"{marker}>{html.escape(str(method["label"]))}</option>')
    return "".join(options)


def _method_panel(project_dir: Path) -> str:
    config = load_method_config(project_dir)
    return (
        '<div class="method-grid">'
        f'<div><small>Query</small><strong>{html.escape(config.get("query", ""))}</strong></div>'
        f'<div><small>Audit</small><strong>{html.escape(config.get("audit", ""))}</strong></div>'
        f'<div><small>Experiment</small><strong>{html.escape(config.get("experiment", ""))}</strong></div>'
        f'<div><small>Disease normalizer</small><strong>{html.escape(config.get("disease_normalizer", ""))}</strong></div>'
        f'<div><small>Dataset scout</small><strong>{html.escape(config.get("dataset_scout", ""))}</strong></div>'
        f'<div><small>Planner</small><strong>{html.escape(config.get("planner", ""))}</strong></div>'
        f'<div><small>Reviewers</small><strong>{html.escape(config.get("method_reviewer", ""))}</strong></div>'
        "</div>"
    )


def _role_model_config_path(project_dir: Path) -> Path:
    return project_dir / "configs" / "role_models.json"


def _role_execution_backend_config_path(project_dir: Path) -> Path:
    return project_dir / "configs" / "role_execution_backends.json"


def _load_role_model_config(project_dir: Path) -> dict[str, str]:
    path = _role_model_config_path(project_dir)
    if not path.exists():
        return {}
    return _read_json(path, {})


def _save_role_model_config(project_dir: Path, config: dict[str, str]) -> dict[str, str]:
    normalized = {key: value.strip() for key, value in config.items() if value.strip()}
    path = _role_model_config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    return normalized


def _load_role_execution_backend_config(project_dir: Path) -> dict[str, str]:
    path = _role_execution_backend_config_path(project_dir)
    if not path.exists():
        return {}
    loaded = _read_json(path, {})
    return {key: value for key, value in loaded.items() if value in {"auto", "local", "llm", "codex"}}


def _save_role_execution_backend_config(project_dir: Path, config: dict[str, str]) -> dict[str, str]:
    normalized = {key: value for key, value in config.items() if value in {"auto", "local", "llm", "codex"}}
    path = _role_execution_backend_config_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    return normalized


def _v4_role_method_fields(project_dir: Path) -> str:
    role_labels = [
        ("disease_normalizer", "Disease Normalizer"),
        ("dataset_scout", "Dataset Scout"),
        ("planner", "Planner"),
        ("method_reviewer", "Method Reviewer"),
        ("result_reviewer", "Result Reviewer"),
        ("causal_reviewer", "Causal Reviewer"),
        ("report_writer", "Report Writer"),
    ]
    model_config = _load_role_model_config(project_dir)
    backend_config = _load_role_execution_backend_config(project_dir)
    blocks = []
    for stage, label in role_labels:
        model = model_config.get(stage, "local")
        backend = backend_config.get(stage, "auto")
        backend_options = "".join(
            f'<option value="{value}"{" selected" if value == backend else ""}>{value}</option>'
            for value in ["auto", "local", "llm", "codex"]
        )
        blocks.append(
            '<div class="method-role-row">'
            f'<label for="{html.escape(stage)}_method">{html.escape(label)} method</label>'
            f'<select id="{html.escape(stage)}_method" name="{html.escape(stage)}">{_method_select(project_dir, stage)}</select>'
            f'<label for="{html.escape(stage)}_backend">{html.escape(label)} backend</label>'
            f'<select id="{html.escape(stage)}_backend" name="backend__{html.escape(stage)}">{backend_options}</select>'
            f'<label for="{html.escape(stage)}_model">{html.escape(label)} model</label>'
            f'<input id="{html.escape(stage)}_model" name="model__{html.escape(stage)}" type="text" value="{html.escape(model)}" placeholder="local / gpt-4.1 / reviewer-model">'
            "</div>"
        )
    return "".join(blocks)


def _markdown_method_panel(project_dir: Path) -> str:
    rows = []
    for method in list_markdown_methods(project_dir):
        rows.append(
            '<div class="resource-row">'
            "<div>"
            f'<strong>{html.escape(method["label"])}</strong>'
            f'<small>{html.escape(method["stage"])} · <code>{html.escape(method["method_id"])}</code></small>'
            f'<small>{html.escape(method["path"])}</small>'
            "</div>"
            '<form class="mini-form" method="post" action="/methods/delete">'
            f'<input type="hidden" name="method_id" value="{html.escape(method["method_id"])}">'
            '<button class="small-button ghost" type="submit">Delete</button>'
            "</form>"
            "</div>"
        )
    registered = "".join(rows) if rows else '<p class="muted">No Markdown methods registered yet.</p>'
    return f"""
      {registered}
      <form class="mini-form upload-form" method="post" action="/methods/upload" enctype="multipart/form-data">
        <label for="method_stage">Target stage</label>
        <select id="method_stage" name="stage">
          <option value="query">生成 / Query</option>
          <option value="audit">初审复核 / Audit</option>
          <option value="experiment">实验设计 / Experiment</option>
          <option value="disease_normalizer">Disease Normalizer</option>
          <option value="dataset_scout">Dataset Scout</option>
          <option value="planner">Planner</option>
          <option value="method_reviewer">Method Reviewer</option>
          <option value="result_reviewer">Result Reviewer</option>
          <option value="causal_reviewer">Causal Reviewer</option>
          <option value="report_writer">Report Writer</option>
        </select>
        <label for="method_file">Drag or choose Markdown skill / agent method</label>
        <input id="method_file" name="method_file" type="file" accept=".md,text/markdown,text/plain">
        <div class="actions">
          <button type="submit">Register Markdown method</button>
        </div>
      </form>
    """


def _v4_work_order_panel(project_dir: Path) -> str:
    if not (project_dir / "v4" / "work_orders.json").exists() and (project_dir / "analysis_plan.json").exists():
        try:
            build_v4_manifest(project_dir)
        except Exception:
            pass
    orders = load_v4_work_orders(project_dir)
    resources = _read_json(project_dir / "v4" / "mcp_resources.json", {}).get("resources", [])
    if not orders:
        return (
            '<p class="muted">No v4 WorkOrders yet. Run planning or Agent workflow first.</p>'
            + _consistency_check_panel(project_dir)
            + _v5_pilotdeck_panel(project_dir)
            + _evidence_db_audit_panel(project_dir)
            + _evidence_trace_index_panel(project_dir)
            + _codex_task_queue_panel(project_dir)
            + _codex_engineering_panel(project_dir)
            + _production_platform_panel(project_dir)
            + _role_runs_panel(project_dir)
            + _orchestration_graph_panel(project_dir)
            + _mcp_gateway_panel(project_dir)
            + _registry_snapshot_panel(project_dir)
            + _executor_manifest_panel(project_dir)
            + _agent_roles_panel(project_dir)
        )
    cards = []
    for order in orders:
        status = order.get("status", "compiled")
        review_status = order.get("review_status", "pending")
        command = order.get("command", "") or "manual / codex"
        packet = load_codex_task_packet(project_dir, order)
        packet_html = ""
        if packet:
            allowed = ", ".join(packet.get("allowed_paths", []))
            tests = "; ".join(packet.get("tests", []))
            packet_html = (
                "<details><summary>Codex task packet</summary>"
                f'<small>job: <code>{html.escape(packet.get("codex_job_id", ""))}</code></small>'
                f'<small>baseline: <code>{html.escape(packet.get("baseline_commit", ""))}</code></small>'
                f'<small>allowed: {html.escape(allowed)}</small>'
                f'<small>tests: {html.escape(tests)}</small>'
                f'<small>release gate: {html.escape(packet.get("release_gate", ""))}</small>'
                "</details>"
                + _review_form("codex_task", packet.get("codex_job_id", ""), "Approve Codex task")
            )
        cards.append(
            '<div class="idea-row">'
            f'<span class="pill {html.escape(status)}">{html.escape(status.upper())}</span>'
            "<div>"
            f'<strong>{html.escape(order.get("module_id", ""))}</strong>'
            f'<small>{html.escape(order.get("work_order_type", ""))} · dataset {html.escape(order.get("dataset_id", ""))} · review {html.escape(review_status)}</small>'
            f'<small>id: <code>{html.escape(order.get("work_order_id", ""))}</code> · backend {html.escape(order.get("target_backend", ""))}</small>'
            f'<small>command: <code>{html.escape(command)}</code></small>'
            + _review_form("work_order", order.get("work_order_id", ""), "Approve work order")
            + packet_html
            + "</div>"
            + "</div>"
        )
    resource_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('uri', ''))}</td>"
        f"<td><code>{html.escape(row.get('path', ''))}</code></td>"
        f"<td><code>{html.escape(row.get('content_hash', '')[:12])}</code></td>"
        "</tr>"
        for row in resources
    )
    resource_table = (
        "<details><summary>MCP Resource manifest</summary>"
        "<table><thead><tr><th>URI</th><th>Path</th><th>Hash</th></tr></thead>"
        f"<tbody>{resource_rows}</tbody></table></details>"
        if resource_rows
        else ""
    )
    attempts = read_work_order_attempts(project_dir).get("attempts", [])
    attempt_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('attempt_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('module_id', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('run_id', ''))}</td>"
        f"<td>{html.escape('; '.join(row.get('artifacts', [])[:3]))}</td>"
        f"<td>{html.escape(row.get('failure_reason', ''))}</td>"
        "</tr>"
        for row in attempts[-12:]
    )
    attempt_table = (
        "<details open><summary>WorkOrder attempts</summary>"
        "<table><thead><tr><th>Attempt</th><th>Module</th><th>Status</th><th>Run</th><th>Artifacts</th><th>Failure</th></tr></thead>"
        f"<tbody>{attempt_rows}</tbody></table></details>"
        if attempt_rows
        else '<p class="muted">No WorkOrder attempts recorded yet.</p>'
    )
    return (
        "".join(cards)
        + attempt_table
        + _v5_pilotdeck_panel(project_dir)
        + _work_order_dag_panel(project_dir)
        + _consistency_check_panel(project_dir)
        + _evidence_db_audit_panel(project_dir)
        + _evidence_trace_index_panel(project_dir)
        + _codex_task_queue_panel(project_dir)
        + _codex_engineering_panel(project_dir)
        + _production_platform_panel(project_dir)
        + _role_runs_panel(project_dir)
        + _orchestration_graph_panel(project_dir)
        + _nextflow_execution_panel(project_dir)
        + _mcp_gateway_panel(project_dir)
        + _registry_snapshot_panel(project_dir)
        + _executor_manifest_panel(project_dir)
        + _agent_roles_panel(project_dir)
        + resource_table
    )


def _evidence_trace_index_panel(project_dir: Path) -> str:
    index = _read_json(project_dir / "v4" / "evidence_review_report_index.json", {})
    items = index.get("items", [])
    query_gene = _read_json(project_dir / "v4" / "evidence_trace_last_query.json", {}).get("gene", "")
    query_result = query_evidence_trace(project_dir, gene=query_gene) if query_gene else {"items": []}
    search = (
        '<form class="mini-form" method="post" action="/evidence-trace/query">'
        '<input type="text" name="gene" placeholder="Gene or target" value="' + html.escape(query_gene) + '">'
        '<button type="submit">Search trace</button>'
        "</form>"
    )
    result_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('evidence_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('entity_symbol', ''))}</td>"
        f"<td>{html.escape(row.get('evidence_type', ''))}</td>"
        f"<td>{html.escape(str(len(row.get('review_items', []))))}</td>"
        f"<td>{html.escape(str(len(row.get('report_refs', []))))}</td>"
        f'<td><a class="button ghost small-button" href="/evidence-trace?evidence_id={urllib.parse.quote(row.get("evidence_id", ""))}">Open</a></td>'
        "</tr>"
        for row in query_result.get("items", [])[:20]
    )
    result_table = (
        "<table><thead><tr><th>Evidence</th><th>Gene</th><th>Type</th><th>Reviews</th><th>Reports</th><th>Detail</th></tr></thead>"
        f"<tbody>{result_rows}</tbody></table>"
        if query_gene
        else ""
    )
    if not items:
        return '<p class="muted">No Evidence -> Review -> Report index recorded yet.</p>' + search + result_table
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('evidence_id', ''))}</code><small>{html.escape(row.get('entity_symbol', ''))}</small></td>"
        f"<td>{html.escape(row.get('evidence_type', ''))}</td>"
        f"<td><code>{html.escape(row.get('artifact_path', ''))}</code></td>"
        f"<td>{html.escape(str(len(row.get('review_items', []))))}</td>"
        f"<td>{html.escape(str(len(row.get('report_refs', []))))}</td>"
        f"<td>{html.escape(str(row.get('review_status') or ''))}</td>"
        f'<td><a class="button ghost small-button" href="/evidence-trace?evidence_id={urllib.parse.quote(row.get("evidence_id", ""))}">Open</a></td>'
        "</tr>"
        for row in items[:20]
    )
    return (
        "<details open><summary>Evidence -> Review -> Report index</summary>"
        f'<p class="muted">evidence: {html.escape(str(index.get("evidence_count", 0)))} · review items: {html.escape(str(index.get("review_item_count", 0)))} · report refs: {html.escape(str(index.get("report_ref_count", 0)))} · index: <code>{html.escape(index.get("index_id", ""))}</code></p>'
        + search
        + result_table
        + "<table><thead><tr><th>Evidence</th><th>Type</th><th>Artifact</th><th>Reviews</th><th>Report refs</th><th>Status</th><th>Detail</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></details>"
    )


def _evidence_db_audit_panel(project_dir: Path) -> str:
    snapshot = _read_json(project_dir / "v4" / "evidence_db_snapshot.json", {})
    migration = _read_json(project_dir / "v4" / "evidence_db_migration.json", {})
    storage = _read_json(project_dir / "v4" / "storage_backend_manifest.json", {})
    storage_readiness = _read_json(project_dir / "v4" / "production_storage_readiness.json", {})
    last_query = _read_json(project_dir / "v4" / "evidence_db_last_query.json", {})
    query = last_query.get("query", {}) if isinstance(last_query, dict) else {}
    indexes = snapshot.get("indexes", []) if isinstance(snapshot, dict) else []
    migrations = snapshot.get("migrations", []) if isinstance(snapshot, dict) else []
    required_index_count = 7
    cards = [
        ("Rows", snapshot.get("row_count", "not built"), "EvidenceItem records in evidence.sqlite"),
        ("Schema", snapshot.get("evidence_schema_version", migration.get("evidence_schema_version", "not migrated")), "Current Evidence DB schema"),
        ("Indexes", f"{len(indexes)} / {required_index_count}", "Production query indexes"),
        ("Snapshot", str(snapshot.get("snapshot_hash", ""))[:16] or "not built", "Versioned DB state hash"),
    ]
    card_html = "".join(
        '<div class="audit-card">'
        f"<small>{html.escape(title)}</small>"
        f"<strong>{html.escape(str(value))}</strong>"
        f"<small>{html.escape(desc)}</small>"
        "</div>"
        for title, value, desc in cards
    )
    index_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('name', ''))}</code></td>"
        f"<td>{html.escape(', '.join(row.get('columns', [])))}</td>"
        f"<td>{html.escape(str(row.get('unique', False)))}</td>"
        "</tr>"
        for row in indexes
    )
    index_table = (
        "<details><summary>Evidence DB indexes</summary>"
        "<table><thead><tr><th>Index</th><th>Columns</th><th>Unique</th></tr></thead>"
        f"<tbody>{index_rows}</tbody></table></details>"
        if index_rows
        else '<p class="muted">No Evidence DB index snapshot yet.</p>'
    )
    migration_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('migration_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('schema_version', ''))}</td>"
        f"<td>{html.escape(row.get('applied_at', ''))}</td>"
        f"<td>{html.escape(row.get('description', ''))}</td>"
        "</tr>"
        for row in migrations[-5:]
    )
    migration_table = (
        "<details><summary>Migration history</summary>"
        "<table><thead><tr><th>Migration</th><th>Schema</th><th>Applied</th><th>Description</th></tr></thead>"
        f"<tbody>{migration_rows}</tbody></table></details>"
        if migration_rows
        else '<p class="muted">No migration history recorded yet.</p>'
    )
    query_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('evidence_id', ''))}</code><small>{html.escape(row.get('entity_symbol', ''))}</small></td>"
        f"<td>{html.escape(row.get('evidence_type', ''))}</td>"
        f"<td>{html.escape(row.get('source_dataset', '') or '')}</td>"
        f"<td>{html.escape(str(row.get('review_status') or ''))}</td>"
        f"<td>{html.escape(str(row.get('quality_score') or ''))}</td>"
        f'<td><a class="button ghost small-button" href="/evidence-trace?evidence_id={urllib.parse.quote(row.get("evidence_id", ""))}">Trace</a></td>'
        "</tr>"
        for row in last_query.get("items", [])[:25]
    )
    query_table = (
        "<table><thead><tr><th>Evidence</th><th>Type</th><th>Dataset</th><th>Status</th><th>Quality</th><th>Trace</th></tr></thead>"
        f"<tbody>{query_rows}</tbody></table>"
        if query_rows
        else '<p class="muted">No Evidence DB query result yet.</p>'
    )
    active_storage = storage.get("active_backends", {}) if isinstance(storage, dict) else {}
    storage_table = (
        "<details><summary>Storage backend contract</summary>"
        "<table><thead><tr><th>Layer</th><th>Backend</th><th>State</th></tr></thead><tbody>"
        f"<tr><td>Evidence DB</td><td>{html.escape(active_storage.get('evidence_db', 'not built'))}</td><td>{html.escape(str(storage.get('sqlite_local', {}).get('exists', False)))}</td></tr>"
        f"<tr><td>Report artifacts</td><td>{html.escape(active_storage.get('report_artifacts', 'not built'))}</td><td>{html.escape(str(len(storage.get('object_store_contract', {}).get('required_objects', []))))} object(s)</td></tr>"
        f"<tr><td>Object store</td><td>{html.escape(active_storage.get('object_store', 'local_filesystem'))}</td><td>{html.escape(str(storage.get('object_store_contract', {}).get('enabled', False)))}</td></tr>"
        "</tbody></table></details>"
        if storage
        else '<p class="muted">No storage backend manifest yet.</p>'
    )
    storage_readiness_html = _readiness_table(
        "Production storage readiness",
        storage_readiness,
        "v4/production_storage_readiness.json",
        "/production/storage-readiness",
        "Refresh storage readiness",
    )
    return (
        "<details open><summary>Evidence DB production audit</summary>"
        '<p class="muted">Query, migrate, snapshot, and audit the production Evidence DB contract used by Agent and Report services.</p>'
        f'<div class="audit-grid">{card_html}</div>'
        '<form class="mini-form" method="post" action="/evidence-db/query">'
        f'<input type="text" name="gene" placeholder="Gene" value="{html.escape(query.get("gene", ""))}">'
        f'<input type="text" name="evidence_type" placeholder="Evidence type" value="{html.escape(query.get("evidence_type", ""))}">'
        f'<input type="text" name="source_dataset" placeholder="Dataset" value="{html.escape(query.get("source_dataset", ""))}">'
        f'<input type="text" name="review_status" placeholder="Review status" value="{html.escape(query.get("review_status", ""))}">'
        '<div class="actions">'
        '<button type="submit">Query Evidence DB</button>'
        '<button class="ghost" type="submit" formaction="/evidence-db/migrate">Run migration</button>'
        '<button class="ghost" type="submit" formaction="/evidence-db/snapshot">Build snapshot</button>'
        '<button class="ghost" type="submit" formaction="/consistency-check">Run consistency check</button>'
        "</div></form>"
        f'<p class="muted">last query: {html.escape(str(last_query.get("match_count", 0)))} match(es) · artifact: <code>v4/evidence_db_last_query.json</code></p>'
        + query_table
        + storage_table
        + storage_readiness_html
        + index_table
        + migration_table
        + "</details>"
    )


def _consistency_check_panel(project_dir: Path) -> str:
    check = _read_json(project_dir / "v4" / "consistency_check.json", {})
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('check', ''))}</td>"
        f"<td><span class=\"pill {'pass' if row.get('status') == 'PASS' else 'review'}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('detail', ''))}</td>"
        "</tr>"
        for row in check.get("checks", [])
    )
    table = (
        "<table><thead><tr><th>Check</th><th>Status</th><th>Detail</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if rows
        else '<p class="muted">No consistency check has been run yet.</p>'
    )
    return (
        "<details open><summary>Consistency check</summary>"
        f'<p class="muted">status: {html.escape(check.get("status", "not_run"))} · artifact: <code>v4/consistency_check.json</code></p>'
        '<form class="mini-form" method="post" action="/consistency-check"><button type="submit">Run consistency check</button></form>'
        + table
        + "</details>"
    )


def _readiness_table(title: str, payload: dict, artifact: str, action: str, button: str) -> str:
    if not payload:
        return (
            f"<details><summary>{html.escape(title)}</summary>"
            f'<p class="muted">No artifact yet. Expected: <code>{html.escape(artifact)}</code></p>'
            f'<form class="mini-form" method="post" action="{html.escape(action)}"><button type="submit">{html.escape(button)}</button></form>'
            "</details>"
        )
    summary = payload.get("summary", {}) if isinstance(payload.get("summary", {}), dict) else {}
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('check_id', ''))}</code></td>"
        f"<td><span class=\"pill {html.escape(str(row.get('status', '')).lower())}\">{html.escape(str(row.get('status', '')))}</span></td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        f"<td>{html.escape(row.get('remediation', ''))}</td>"
        "</tr>"
        for row in payload.get("checks", [])
    )
    table = (
        "<table><thead><tr><th>Check</th><th>Status</th><th>Message</th><th>Fix</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if rows
        else '<p class="muted">No check rows recorded.</p>'
    )
    return (
        f"<details open><summary>{html.escape(title)}</summary>"
        f'<p class="muted">status: {html.escape(payload.get("status", "unknown"))} · hash: <code>{html.escape((payload.get("readiness_hash") or payload.get("release_gate_hash") or payload.get("observability_hash") or payload.get("topology_hash") or "")[:16])}</code> · artifact: <code>{html.escape(artifact)}</code></p>'
        + (
            f'<p class="muted">ready: {html.escape(str(summary.get("ready_to_merge_count", "")))} · blocked: {html.escape(str(summary.get("blocked_count", "")))}</p>'
            if summary
            else ""
        )
        + f'<form class="mini-form" method="post" action="{html.escape(action)}"><button type="submit">{html.escape(button)}</button></form>'
        + table
        + "</details>"
    )


def _evidence_trace_detail_page(project_dir: Path, gene: str = "", evidence_id: str = "") -> bytes:
    detail = evidence_trace_detail(project_dir, gene=gene, evidence_id=evidence_id)
    evidence_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('evidence_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('entity_symbol', ''))}</td>"
        f"<td>{html.escape(row.get('evidence_type', ''))}</td>"
        f"<td>{html.escape(row.get('source_dataset', ''))}</td>"
        f"<td><code>{html.escape(row.get('artifact_path', ''))}</code></td>"
        f"<td>{html.escape(str(row.get('review_status') or ''))}</td>"
        "</tr>"
        for row in detail.get("evidence_items", [])
    )
    review_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('source', ''))}</td>"
        f"<td>{html.escape(row.get('item_type', ''))}</td>"
        f"<td><code>{html.escape(row.get('item_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('review_status', ''))}</td>"
        f"<td>{html.escape(row.get('reason', ''))}</td>"
        f"<td><code>{html.escape(row.get('report_ref', ''))}</code></td>"
        "</tr>"
        for row in detail.get("review_items", [])
    )
    report_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('gene', ''))}</td>"
        f"<td><code>{html.escape(row.get('score_id', ''))}</code></td>"
        f"<td><code>{html.escape(row.get('evidence_snapshot_id', ''))}</code></td>"
        f"<td>{html.escape('; '.join(row.get('evidence_refs', [])))}</td>"
        f"<td><code>{html.escape(row.get('report_ref', ''))}</code></td>"
        "</tr>"
        for row in detail.get("report_refs", [])
    )
    node_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('work_order_id', ''))}</code><small>{html.escape(row.get('module_id', ''))}</small></td>"
        f"<td>{html.escape(row.get('module', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(str(len(row.get('outputs', []))))}</td>"
        f"<td>{html.escape(str(len(row.get('evidence_writes', []))))}</td>"
        "</tr>"
        for row in detail.get("work_order_nodes", [])
    )
    artifact_rows = "".join(f"<tr><td><code>{html.escape(path)}</code></td></tr>" for path in detail.get("artifacts", []))
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Evidence trace detail</title>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; background: #f5f5f7; color: #1d1d1f; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 30px 22px 64px; }}
    section {{ background: rgba(255,255,255,.86); border: 1px solid rgba(60,60,67,.16); border-radius: 20px; padding: 18px; margin: 16px 0; box-shadow: 0 18px 60px rgba(0,0,0,.08); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ border-bottom: 1px solid rgba(60,60,67,.16); padding: 9px; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ color: #6e6e73; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
    a.button {{ display:inline-block; border-radius:999px; padding:10px 14px; border:1px solid rgba(60,60,67,.16); text-decoration:none; color:#007aff; background:white; }}
    small {{ display:block; color:#6e6e73; }}
  </style>
</head>
<body>
<main>
  <a class="button" href="/">Back</a>
  <h1>Evidence trace detail</h1>
  <p>Matches: {html.escape(str(detail.get("match_count", 0)))} | gene: {html.escape(gene)} | evidence_id: {html.escape(evidence_id)}</p>
  <section><h2>EvidenceItem</h2><table><thead><tr><th>ID</th><th>Gene</th><th>Type</th><th>Dataset</th><th>Artifact</th><th>Status</th></tr></thead><tbody>{evidence_rows}</tbody></table></section>
  <section><h2>ReviewItem</h2><table><thead><tr><th>Source</th><th>Type</th><th>ID</th><th>Status</th><th>Reason</th><th>Report ref</th></tr></thead><tbody>{review_rows}</tbody></table></section>
  <section><h2>ReportRef</h2><table><thead><tr><th>Gene</th><th>Score</th><th>Snapshot</th><th>Evidence refs</th><th>Report ref</th></tr></thead><tbody>{report_rows}</tbody></table></section>
  <section><h2>WorkOrder / DAG node</h2><table><thead><tr><th>WorkOrder</th><th>Module</th><th>Status</th><th>Outputs</th><th>Evidence writes</th></tr></thead><tbody>{node_rows}</tbody></table></section>
  <section><h2>Artifact</h2><table><tbody>{artifact_rows}</tbody></table></section>
</main>
</body>
</html>"""
    return html_text.encode("utf-8")


def _work_order_dag_panel(project_dir: Path) -> str:
    dag = load_work_order_dag(project_dir)
    orchestrator = _read_json(project_dir / "v4" / "orchestrator_runs.json", {"runs": []})
    dispatch_by_node = _latest_dispatch_by_node(orchestrator.get("runs", []))
    rows = "".join(
        "<tr>"
        + _dag_node_cells(node, dispatch_by_node.get(node.get("node_id", ""), {}))
        + "</tr>"
        for node in dag.get("nodes", [])[-20:]
    )
    detail_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(node.get('node_id', ''))}</code><small>{html.escape(node.get('module_id', ''))}</small></td>"
        + _dag_dispatch_detail_cells(node, dispatch_by_node.get(node.get("node_id", ""), {}))
        + "</tr>"
        for node in dag.get("nodes", [])[-20:]
    )
    input_cards = "".join(
        _dag_input_recovery_card(node, dispatch_by_node.get(node.get("node_id", ""), {}))
        for node in dag.get("nodes", [])[-20:]
    )
    if not rows:
        return '<p class="muted">No WorkOrder DAG recorded yet.</p>'
    summary = ", ".join(f"{key}: {value}" for key, value in dag.get("status_summary", {}).items())
    return (
        "<details open><summary>WorkOrder DAG</summary>"
        f'<p class="muted">nodes: {html.escape(str(dag.get("node_count", 0)))} · edges: {html.escape(str(dag.get("edge_count", 0)))} · {html.escape(summary)}</p>'
        "<table><thead><tr><th>Node</th><th>Module</th><th>Status</th><th>Executor</th><th>Resume</th><th>Outputs</th><th>Evidence</th><th>Recovery</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        '<h3>Input parsing and recovery advice</h3>'
        '<p class="muted">Each node shows declared/inferred inputs, missing artifacts, failure reason, and the next human or agent action.</p>'
        f'<div class="dag-recovery-grid">{input_cards}</div>'
        "<details><summary>Executor dispatch details</summary>"
        "<table><thead><tr><th>Node</th><th>Manifest</th><th>Artifacts</th><th>Failure</th><th>Dependencies</th></tr></thead>"
        f"<tbody>{detail_rows}</tbody></table></details></details>"
    )


def _latest_dispatch_by_node(runs: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for run in runs:
        result = run.get("result", {})
        for row in result.get("node_results", []) if isinstance(result, dict) else []:
            node_id = row.get("node_id", "")
            if node_id:
                latest[node_id] = row
    return latest


def _dag_node_cells(node: dict, dispatch: dict) -> str:
    executor = dispatch.get("executor", {})
    latest = node.get("latest_attempt", {}) or {}
    recovery = dispatch.get("recovery", {}) or latest.get("metadata", {}).get("recovery", {})
    backend = executor.get("backend", "") or latest.get("metadata", {}).get("executor_dispatch", {}).get("backend", "") or "not_run"
    status = dispatch.get("status") or node.get("status", "")
    recovery_text = recovery.get("suggested_action", "") or dispatch.get("reason", "") or latest.get("failure_reason", "")
    return (
        f"<td><code>{html.escape(node.get('node_id', ''))}</code><small>{html.escape(node.get('module_id', ''))}</small></td>"
        f"<td>{html.escape(node.get('module', ''))}</td>"
        f"<td><span class=\"pill {html.escape(str(status).lower())}\">{html.escape(str(status))}</span></td>"
        f"<td>{html.escape(backend)}<small>{html.escape(executor.get('module_id', ''))}</small></td>"
        f"<td><code>{html.escape(dispatch.get('resume_key') or node.get('resume_key', ''))}</code></td>"
        f"<td>{html.escape(str(len(node.get('outputs', []))))}</td>"
        f"<td>{html.escape(str(len(node.get('evidence_writes', []))))}</td>"
        f"<td>{html.escape(recovery_text[:180])}</td>"
    )


def _dag_dispatch_detail_cells(node: dict, dispatch: dict) -> str:
    executor = dispatch.get("executor", {})
    manifest = executor.get("executor_manifest") or executor.get("nextflow_manifest") or ""
    artifacts = executor.get("artifacts") or dispatch.get("artifacts") or [row.get("path", "") for row in node.get("outputs", [])]
    failure = executor.get("failure_reason") or dispatch.get("reason") or (node.get("latest_attempt", {}) or {}).get("failure_reason", "")
    return (
        f"<td><code>{html.escape(manifest)}</code><small>{html.escape(executor.get('backend', ''))}</small></td>"
        f"<td>{html.escape('; '.join(str(item) for item in artifacts[:6] if item))}</td>"
        f"<td>{html.escape(str(failure)[:220])}</td>"
        f"<td>{html.escape('; '.join(node.get('dependencies', [])))}</td>"
    )


def _dag_input_recovery_card(node: dict, dispatch: dict) -> str:
    resolution = node.get("input_resolution", {}) or {}
    latest = node.get("latest_attempt", {}) or {}
    recovery = dispatch.get("recovery", {}) or latest.get("metadata", {}).get("recovery", {}) or {}
    missing = resolution.get("missing", []) or []
    resolved = resolution.get("resolved", []) or []
    failure = (
        dispatch.get("reason")
        or dispatch.get("failure_reason")
        or latest.get("failure_reason")
        or recovery.get("reason")
        or ("missing input artifacts" if missing else "")
    )
    advice_items = _dag_recovery_items(recovery)
    advice_items.extend(
        item if isinstance(item, dict) else {"message": str(item)}
        for item in (resolution.get("recovery", []) or [])
    )
    input_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('key', '')))}</td>"
        f"<td><span class=\"pill {html.escape(str(row.get('status', '')).lower())}\">{html.escape(str(row.get('status', '')))}</span></td>"
        f"<td>{html.escape(str(row.get('source', '')))}</td>"
        f"<td><code>{html.escape(str(row.get('path') or row.get('declared') or ''))}</code></td>"
        "</tr>"
        for row in resolved[:8]
    )
    if not input_rows:
        input_rows = '<tr><td colspan="4">No declared input artifacts.</td></tr>'
    missing_html = "".join(
        '<li>'
        f'<strong>{html.escape(str(row.get("key", "")))}</strong>'
        f' <small>{html.escape(str(row.get("declared") or row.get("source") or ""))}</small>'
        '</li>'
        for row in missing[:8]
    )
    advice_html = "".join(
        '<li>'
        f'{html.escape(str(item.get("message") or item.get("suggested_action") or item))}'
        '</li>'
        for item in advice_items[:6]
    )
    artifact_resolution_ref = node.get("input_resolution_ref", "") or _recovery_artifact_ref(recovery)
    status = dispatch.get("status") or node.get("status", "")
    return (
        '<div class="dag-recovery-card">'
        '<div class="dag-recovery-head">'
        f'<div><strong>{html.escape(node.get("module_id", "") or node.get("node_id", ""))}</strong>'
        f'<small><code>{html.escape(node.get("work_order_id", ""))}</code> · {html.escape(node.get("module", ""))}</small></div>'
        f'<span class="pill {html.escape(str(status).lower())}">{html.escape(str(status))}</span>'
        '</div>'
        f'<p><strong>Failure reason:</strong> {html.escape(str(failure or "No failure recorded."))}</p>'
        '<details open><summary>Input parsing</summary>'
        '<table><thead><tr><th>Input</th><th>Status</th><th>Source</th><th>Path / declared</th></tr></thead>'
        f'<tbody>{input_rows}</tbody></table></details>'
        + (f'<div class="warning-box"><strong>Missing inputs</strong><ul>{missing_html}</ul></div>' if missing_html else '<p class="muted">No missing inputs detected by resolver.</p>')
        + (f'<div><strong>Recovery advice</strong><ul>{advice_html}</ul></div>' if advice_html else '<p class="muted">No recovery advice recorded.</p>')
        + f'<small>Resolution: <code>{html.escape(str(artifact_resolution_ref or "not written"))}</code></small>'
        '<form class="mini-form" method="post" action="/orchestrator/submit">'
        '<input type="hidden" name="run_type" value="work_order_dag">'
        f'<input type="hidden" name="work_order_id" value="{html.escape(node.get("work_order_id", ""))}">'
        '<input type="hidden" name="force" value="true">'
        '<button class="ghost" type="submit">Retry this node</button>'
        '</form>'
        '</div>'
    )


def _dag_recovery_items(recovery: dict) -> list[dict]:
    items = recovery.get("items", []) if isinstance(recovery, dict) else []
    if items:
        return [item if isinstance(item, dict) else {"message": str(item)} for item in items]
    if isinstance(recovery, dict) and recovery.get("suggested_action"):
        return [{"message": recovery.get("suggested_action", "")}]
    if isinstance(recovery, dict) and recovery.get("recommendation"):
        return [{"message": recovery.get("recommendation", "")}]
    return []


def _recovery_artifact_ref(recovery: dict) -> str:
    if not isinstance(recovery, dict):
        return ""
    return str(recovery.get("artifact_resolution") or recovery.get("artifact_resolution_ref") or "")


def _codex_engineering_panel(project_dir: Path) -> str:
    data = load_codex_engineering(project_dir)
    closure = _read_json(project_dir / "v4" / "codex_engineering" / "engineering_closure.json", {})
    release_gate = _read_json(project_dir / "v4" / "codex_engineering" / "release_gate.json", {})
    sbom = _read_json(project_dir / "v4" / "codex_engineering" / "sbom_manifest.json", {})
    workspaces = {row.get("codex_job_id", ""): row for row in data.get("workspaces", [])}
    patches_by_job: dict[str, list[dict]] = {}
    tests_by_job: dict[str, list[dict]] = {}
    merges_by_result = {row.get("result_id", ""): row for row in data.get("merges", [])}
    for row in data.get("patches", []):
        patches_by_job.setdefault(row.get("codex_job_id", ""), []).append(row)
    for row in data.get("tests", []):
        tests_by_job.setdefault(row.get("codex_job_id", ""), []).append(row)
    rows = []
    for result in data.get("results", [])[-12:]:
        job_id = result.get("codex_job_id", "")
        workspace = workspaces.get(job_id, {})
        tests = tests_by_job.get(job_id, [])
        test_status = ", ".join(row.get("status", "") for row in tests[-3:]) or "none"
        merge = merges_by_result.get(result.get("result_id", ""), {})
        merge_actions = ""
        if result.get("merge_status") == "approved_for_merge":
            merge_actions = (
                '<form class="inline-form" method="post" action="/codex/merge-result">'
                f'<input type="hidden" name="result_id" value="{html.escape(result.get("result_id", ""))}">'
                '<input type="hidden" name="dry_run" value="1">'
                '<button class="ghost small-button" type="submit">Dry run</button>'
                "</form>"
                '<form class="inline-form" method="post" action="/codex/merge-result">'
                f'<input type="hidden" name="result_id" value="{html.escape(result.get("result_id", ""))}">'
                '<button class="small-button" type="submit">Merge</button>'
                "</form>"
            )
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(result.get('result_id', ''))}</code><small>{html.escape(job_id)}</small></td>"
            f"<td>{html.escape(result.get('status', ''))}</td>"
            f"<td>{html.escape(str(len(patches_by_job.get(job_id, []))))}</td>"
            f"<td>{html.escape(test_status)}</td>"
            f"<td><code>{html.escape(workspace.get('workspace_path', ''))}</code></td>"
            f"<td>{html.escape(result.get('merge_status', ''))}</td>"
            f"<td>{html.escape(result.get('review_status', ''))}</td>"
            f"<td><code>{html.escape(result.get('evidence_snapshot_hash', '')[:12])}</code></td>"
            f"<td><code>{html.escape(result.get('merge_ref', '') or (('v4/codex_engineering/merge_registry.json#' + merge.get('merge_id', '')) if merge else ''))}</code></td>"
            "<td>"
            + _review_form("codex_result", result.get("result_id", ""), "Approve result")
            + merge_actions
            + "</td>"
            "</tr>"
        )
    if not rows and not data.get("workspaces"):
        return '<p class="muted">No Codex engineering runs recorded yet.</p>'
    workspace_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('codex_job_id', ''))}</code></td>"
        f"<td><code>{html.escape(row.get('workspace_path', ''))}</code></td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(str(len(row.get('copied_inputs', []))))}</td>"
        "</tr>"
        for row in data.get("workspaces", [])[-12:]
    )
    workspace_table = (
        "<details><summary>Isolated workspaces</summary>"
        "<table><thead><tr><th>Codex job</th><th>Workspace</th><th>Status</th><th>Copied inputs</th></tr></thead>"
        f"<tbody>{workspace_rows}</tbody></table></details>"
        if workspace_rows
        else ""
    )
    result_table = (
        "<details open><summary>Codex engineering loop</summary>"
        f'<p class="muted">closure: <code>{html.escape("v4/codex_engineering/engineering_closure.json" if closure else "not built")}</code> · approved for merge: {html.escape(str(closure.get("approved_for_merge_count", 0) if closure else 0))}</p>'
        "<table><thead><tr><th>Result</th><th>Status</th><th>Patches</th><th>Tests</th><th>Workspace</th><th>Merge gate</th><th>Review</th><th>Evidence snapshot</th><th>Merge ref</th><th>Action</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></details>"
        if rows
        else '<p class="muted">No Codex execution results recorded yet.</p>'
    )
    release_html = _readiness_table(
        "Codex engineering release gate",
        release_gate,
        "v4/codex_engineering/release_gate.json",
        "/codex/release-gate",
        "Refresh release gate",
    )
    sbom_html = (
        "<details><summary>SBOM contract</summary>"
        f'<p class="muted">status: {html.escape(sbom.get("status", "not built"))} · artifact: <code>v4/codex_engineering/sbom_manifest.json</code></p>'
        '<form class="mini-form" method="post" action="/codex/sbom"><button type="submit">Build SBOM contract</button></form>'
        f'<p class="muted">{html.escape(sbom.get("production_gap", ""))}</p>'
        "</details>"
        if sbom
        else '<details><summary>SBOM contract</summary><p class="muted">No SBOM contract yet.</p><form class="mini-form" method="post" action="/codex/sbom"><button type="submit">Build SBOM contract</button></form></details>'
    )
    return result_table + workspace_table + release_html + sbom_html


def _codex_task_queue_panel(project_dir: Path) -> str:
    queue = _read_json(project_dir / "v4" / "codex_task_queue.json", {})
    task_registry = _read_json(project_dir / "v4" / "task_registry.json", {})
    patches = _read_json(project_dir / "v4" / "codex_task_queue_patches.json", {"patches": []}).get("patches", [])
    tests = _read_json(project_dir / "v4" / "codex_task_queue_tests.json", {"tests": []}).get("tests", [])
    results = _read_json(project_dir / "v4" / "codex_task_queue_results.json", {"results": []}).get("results", [])
    tasks = queue.get("tasks", []) if isinstance(queue, dict) else []
    summary = queue.get("status_summary", {}) if isinstance(queue, dict) else {}
    summary_text = ", ".join(f"{key}: {value}" for key, value in summary.items()) or "not synced"
    registry_summary = task_registry.get("status_summary", {}) if isinstance(task_registry, dict) else {}
    registry_text = ", ".join(f"{key}: {value}" for key, value in registry_summary.items()) or "not built"
    registry_tasks = task_registry.get("tasks", []) if isinstance(task_registry, dict) else []
    display_tasks = registry_tasks or tasks
    attention_tasks = [row for row in display_tasks if str(row.get("status", "")) not in {"succeeded", "qc_passed"}]
    completed_tasks = [row for row in display_tasks if str(row.get("status", "")) in {"succeeded", "qc_passed"}]
    overview = _task_queue_overview_cards(queue, task_registry, results, tests, patches)
    attention_rows = _codex_task_rows(attention_tasks[:12])
    completed_rows = _codex_task_rows(completed_tasks[-8:])
    if not attention_rows:
        attention_rows = '<tr><td colspan="8">No task needs attention.</td></tr>'
    if not completed_rows:
        completed_rows = '<tr><td colspan="8">No completed task records yet.</td></tr>'
    result_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('result_record_id', ''))}</code><small>{html.escape(row.get('task_id', ''))}</small></td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td><code>{html.escape(row.get('orchestrator_run_id', ''))}</code></td>"
        f"<td>{html.escape('; '.join(str(item) for item in row.get('artifacts', [])[:4]))}</td>"
        f"<td>{html.escape(row.get('failure_reason', ''))}</td>"
        "</tr>"
        for row in results[-8:]
    )
    test_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('test_record_id', ''))}</code><small>{html.escape(row.get('task_id', ''))}</small></td>"
        f"<td>{html.escape(row.get('command', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('failure_reason', ''))}</td>"
        "</tr>"
        for row in tests[-8:]
    )
    patch_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('patch_record_id', ''))}</code><small>{html.escape(row.get('task_id', ''))}</small></td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('summary', ''))}</td>"
        "</tr>"
        for row in patches[-8:]
    )
    records_html = (
        "<details><summary>Queue patch / test / result registry</summary>"
        "<h3>Results</h3>"
        + ("<table><thead><tr><th>Result</th><th>Status</th><th>Orchestrator</th><th>Artifacts</th><th>Failure</th></tr></thead><tbody>" + result_rows + "</tbody></table>" if result_rows else '<p class="muted">No queue result records.</p>')
        + "<h3>Tests</h3>"
        + ("<table><thead><tr><th>Test</th><th>Command</th><th>Status</th><th>Failure</th></tr></thead><tbody>" + test_rows + "</tbody></table>" if test_rows else '<p class="muted">No queue test records.</p>')
        + "<h3>Patches</h3>"
        + ("<table><thead><tr><th>Patch</th><th>Status</th><th>Summary</th></tr></thead><tbody>" + patch_rows + "</tbody></table>" if patch_rows else '<p class="muted">No queue patch records.</p>')
        + "</details>"
    )
    return (
        "<details open><summary>Codex Task Queue</summary>"
        '<p class="muted">CodexTaskPacket execution desk. Review items are separated from completed runs so the next action is visible.</p>'
        + overview
        + f'<p class="muted">queue: {html.escape(str(queue.get("task_count", 0) if isinstance(queue, dict) else 0))} task(s) · {html.escape(summary_text)} · artifact: <code>v4/codex_task_queue.json</code></p>'
        + f'<p class="muted">Task Registry as source of truth: {html.escape(str(task_registry.get("task_count", 0) if isinstance(task_registry, dict) else 0))} task(s) · {html.escape(registry_text)} · artifact: <code>v4/task_registry.json</code></p>'
        + '<form class="mini-form" method="post" action="/codex-queue/sync">'
        + '<div class="actions">'
        + '<button type="submit">Sync packets</button>'
        + '<button class="ghost" type="submit" formaction="/codex-queue/run">Run next task</button>'
        + '<input type="hidden" name="worker_id" value="ui_codex_worker">'
        + '<input type="hidden" name="limit" value="1">'
        + '<input type="hidden" name="force" value="1">'
        + "</div></form>"
        + '<form class="mini-form" method="post" action="/codex-queue/execute">'
        + '<input type="text" name="task_id" placeholder="task_id or module_id for one task">'
        + '<input type="hidden" name="worker_id" value="ui_codex_worker">'
        + '<input type="hidden" name="force" value="1">'
        + '<button class="ghost" type="submit">Execute selected task</button>'
        + "</form>"
        + "<h3>Needs attention</h3>"
        + "<table><thead><tr><th>Task</th><th>Kind</th><th>Status</th><th>WorkOrder</th><th>Worker</th><th>Result ref</th><th>QC -> Evidence DB</th><th>Action</th></tr></thead>"
        + f"<tbody>{attention_rows}</tbody></table>"
        + "<details><summary>Recent completed tasks</summary>"
        + "<table><thead><tr><th>Task</th><th>Kind</th><th>Status</th><th>WorkOrder</th><th>Worker</th><th>Result ref</th><th>QC -> Evidence DB</th><th>Action</th></tr></thead>"
        + f"<tbody>{completed_rows}</tbody></table></details>"
        + _qc_review_queue_panel(project_dir)
        + records_html
        + "</details>"
    )


def _task_queue_overview_cards(queue: dict, task_registry: dict, results: list[dict], tests: list[dict], patches: list[dict]) -> str:
    registry_summary = task_registry.get("status_summary", {}) if isinstance(task_registry, dict) else {}
    ready = int(registry_summary.get("qc_passed", 0)) + int(queue.get("status_summary", {}).get("succeeded", 0) if isinstance(queue, dict) else 0)
    review = sum(int(registry_summary.get(key, 0)) for key in ["qc_review_required", "engineering_review_required", "queue_needs_review"])
    failed = sum(int(registry_summary.get(key, 0)) for key in ["failed", "qc_failed", "queue_failed"])
    running = sum(int(registry_summary.get(key, 0)) for key in ["running", "queue_running", "queue_claimed"])
    cards = [
        ("Ready", ready, "Completed execution or QC pass"),
        ("Review", review, "Human or reviewer-agent action needed"),
        ("Failed", failed, "Recovery action needed"),
        ("Records", f"{len(results)}/{len(tests)}/{len(patches)}", "result / test / patch"),
    ]
    if running:
        cards.insert(1, ("Running", running, "Currently claimed or executing"))
    return '<div class="audit-grid">' + "".join(
        '<div class="audit-card">'
        f"<small>{html.escape(str(title))}</small>"
        f"<strong>{html.escape(str(value))}</strong>"
        f"<small>{html.escape(desc)}</small>"
        "</div>"
        for title, value, desc in cards
    ) + "</div>"


def _codex_task_rows(rows: list[dict]) -> str:
    return "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('task_id', ''))}</code><small>{html.escape(row.get('module_id', ''))}</small></td>"
        f"<td>{html.escape(row.get('task_kind', ''))}</td>"
        f"<td><span class=\"pill {html.escape(str(row.get('status', '')).lower())}\">{html.escape(str(row.get('status', '')))}</span></td>"
        f"<td><code>{html.escape(row.get('work_order_id', ''))}</code></td>"
        f"<td>{html.escape(((row.get('queue', {}) or {}).get('claim', {}) or (row.get('claim', {}) or {})).get('worker_id', ''))}</td>"
        f"<td><code>{html.escape((row.get('refs', {}) or {}).get('queue_result') or (row.get('refs', {}) or {}).get('result', ''))}</code></td>"
        f"<td><span class=\"pill {html.escape(str((row.get('qc_gate', {}) or {}).get('status', '')).lower())}\">{html.escape((row.get('qc_gate', {}) or {}).get('evidence_import', ''))}</span><small>{html.escape((row.get('qc_gate', {}) or {}).get('reason', ''))}</small></td>"
        "<td>"
        '<form class="inline-form" method="post" action="/codex-queue/claim">'
        f'<input type="hidden" name="task_id" value="{html.escape(row.get("task_id", ""))}">'
        '<input type="hidden" name="worker_id" value="ui_codex_worker">'
        '<button class="ghost small-button" type="submit">Claim</button>'
        "</form>"
        '<form class="inline-form" method="post" action="/codex-queue/execute">'
        f'<input type="hidden" name="task_id" value="{html.escape(row.get("task_id", ""))}">'
        '<input type="hidden" name="worker_id" value="ui_codex_worker">'
        '<input type="hidden" name="force" value="1">'
        '<button class="ghost small-button" type="submit">Execute</button>'
        "</form>"
        "</td>"
        "</tr>"
        for row in rows
    )


def _qc_review_queue_panel(project_dir: Path) -> str:
    queue = _read_json(project_dir / "v4" / "qc_review_queue.json", {})
    if not queue:
        try:
            queue = build_qc_review_queue(project_dir)
        except Exception:
            queue = {}
    rows = "".join(
        "<tr>"
        f'<td><input type="checkbox" name="work_order_id" value="{html.escape(row.get("item_id", ""))}"></td>'
        f"<td><code>{html.escape(row.get('item_id', ''))}</code><small>{html.escape(row.get('module_id', ''))}</small>"
        f'<small><a href="/qc-review/detail?item_id={urllib.parse.quote(row.get("item_id", ""))}">Open detail</a></small></td>'
        f"<td>{html.escape(row.get('dataset_id', ''))}</td>"
        f"<td><span class=\"pill review\">{html.escape(row.get('evidence_import', ''))}</span><small>{html.escape(row.get('reason', ''))}</small></td>"
        f"<td><code>{html.escape(row.get('qc_report', ''))}</code></td>"
        f"<td>{html.escape(str((row.get('evidence_summary', {}) or {}).get('match_count', 0)))}</td>"
        "<td>"
        + _qc_review_form(row, "approve", "Approve QC")
        + _qc_review_form(row, "needs_review", "Keep review")
        + _qc_review_form(row, "reject", "Reject evidence")
        + "</td>"
        "</tr>"
        for row in queue.get("items", [])[:12]
    )
    table = (
        '<form class="mini-form" method="post" action="/qc-review/batch">'
        '<div class="actions">'
        '<select name="action"><option value="approve">Approve selected</option><option value="needs_review">Keep selected in review</option><option value="reject">Reject selected</option></select>'
        '<input type="text" name="reason" placeholder="required batch reason">'
        '<button class="ghost" type="submit">Apply to selected</button>'
        "</div>"
        "<table><thead><tr><th></th><th>QC item</th><th>Dataset</th><th>Gate</th><th>QC report</th><th>Evidence rows</th><th>Decision</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></form>"
        if rows
        else '<p class="muted">No QC review items. All computational evidence is either allowed, rejected, or not yet imported.</p>'
    )
    return (
        "<details open><summary>QC review gate</summary>"
        f'<p class="muted">pending: {html.escape(str(queue.get("queue_count", 0) if isinstance(queue, dict) else 0))} · artifact: <code>v4/qc_review_queue.json</code></p>'
        '<form class="mini-form" method="post" action="/qc-review/build"><button class="ghost" type="submit">Refresh QC review queue</button></form>'
        + table
        + "</details>"
    )


def _qc_review_form(row: dict, action: str, label: str) -> str:
    return (
        '<form class="inline-form" method="post" action="/qc-review/apply">'
        f'<input type="hidden" name="work_order_id" value="{html.escape(row.get("item_id", ""))}">'
        f'<input type="hidden" name="action" value="{html.escape(action)}">'
        f'<input type="hidden" name="report_ref" value="{html.escape(row.get("report_ref", ""))}">'
        '<input type="text" name="reason" placeholder="required reason">'
        f'<button class="ghost small-button" type="submit">{html.escape(label)}</button>'
        "</form>"
    )


def _qc_review_detail_page(project_dir: Path, item_id: str) -> str:
    queue = _read_json(project_dir / "v4" / "qc_review_queue.json", {})
    if not queue:
        try:
            queue = build_qc_review_queue(project_dir)
        except Exception as exc:
            return _page(project_dir, f"QC review queue is not available: {html.escape(str(exc))}")
    item = next(
        (
            row
            for row in queue.get("items", [])
            if item_id in {row.get("item_id", ""), row.get("task_id", ""), row.get("module_id", "")}
        ),
        None,
    )
    if not item:
        return _page(project_dir, f"QC review item not found: {html.escape(item_id)}")
    qc_report = _read_json(project_dir / item.get("qc_report", ""), {})
    layer_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('layer', '')))}</td>"
        f"<td><span class=\"pill {html.escape(str(row.get('status', '')).lower())}\">{html.escape(str(row.get('status', '')))}</span></td>"
        f"<td>{html.escape('; '.join(str(x) for x in row.get('messages', []) or row.get('warnings', []) or []))}</td>"
        "</tr>"
        for row in qc_report.get("layers", [])
    )
    evidence_summary = item.get("evidence_summary", {}) or {}
    status_rows = "".join(
        f"<tr><td>{html.escape(str(status))}</td><td>{html.escape(str(count))}</td></tr>"
        for status, count in (evidence_summary.get("by_review_status", {}) or {}).items()
    )
    artifacts = "".join(f"<li><code>{html.escape(str(path))}</code></li>" for path in qc_report.get("artifacts", []))
    body = "".join(
        [
            '<section class="panel">',
            "<h2>QC Review Detail</h2>",
            f'<p class="muted"><a href="/">Back to dashboard</a> · item <code>{html.escape(item.get("item_id", ""))}</code> · task <code>{html.escape(item.get("task_id", ""))}</code></p>',
            '<div class="audit-grid">',
            f'<div class="audit-card"><small>Module</small><strong>{html.escape(item.get("module_id", ""))}</strong><small>{html.escape(item.get("dataset_id", ""))}</small></div>',
            f'<div class="audit-card"><small>Gate</small><strong>{html.escape(item.get("evidence_import", ""))}</strong><small>{html.escape(item.get("reason", ""))}</small></div>',
            f'<div class="audit-card"><small>Evidence Rows</small><strong>{html.escape(str(evidence_summary.get("match_count", 0)))}</strong><small>matched by artifact/QC report</small></div>',
            f'<div class="audit-card"><small>QC Report</small><strong>{html.escape(str(qc_report.get("overall_status", "")))}</strong><small>{html.escape(item.get("qc_report", ""))}</small></div>',
            "</div>",
            "<h3>Four-layer QC</h3>",
            "<table><thead><tr><th>Layer</th><th>Status</th><th>Notes</th></tr></thead><tbody>" + layer_rows + "</tbody></table>" if layer_rows else '<p class="muted">No layer details found in QC report.</p>',
            "<h3>Evidence Status</h3>",
            "<table><thead><tr><th>Review status</th><th>Rows</th></tr></thead><tbody>" + status_rows + "</tbody></table>" if status_rows else '<p class="muted">No matched EvidenceItem rows yet.</p>',
            "<h3>Artifacts</h3>",
            f"<ul>{artifacts}</ul>" if artifacts else '<p class="muted">No artifacts listed.</p>',
            "<h3>Decision</h3>",
            _qc_review_form(item, "approve", "Approve QC"),
            _qc_review_form(item, "needs_review", "Keep review"),
            _qc_review_form(item, "reject", "Reject evidence"),
            "</section>",
        ]
    )
    return _standalone_page(project_dir, body, title="v5 Canonical Flow")


def _production_platform_panel(project_dir: Path) -> str:
    observability = _read_json(project_dir / "v4" / "observability_manifest.json", {})
    topology = _read_json(project_dir / "v4" / "service_topology.json", {})
    obs_rows = ""
    if observability:
        obs_rows = "".join(
            "<tr>"
            f"<td>{html.escape(key)}</td>"
            f"<td><code>{html.escape(str(value.get('source', '')))}</code></td>"
            f"<td>{html.escape(str(value.get('count', 0)))}</td>"
            "</tr>"
            for key, value in observability.get("signals", {}).items()
        )
    topo_rows = ""
    if topology:
        topo_rows = "".join(
            "<tr>"
            f"<td><code>{html.escape(row.get('service_id', ''))}</code></td>"
            f"<td>{html.escape(row.get('process_model', ''))}</td>"
            f"<td>{html.escape(str(len(row.get('endpoints', []))))}</td>"
            f"<td>{html.escape(', '.join(row.get('may_call', [])))}</td>"
            "</tr>"
            for row in topology.get("nodes", [])
        )
    return (
        "<details open><summary>v4 production platform</summary>"
        '<div class="actions">'
        '<form class="mini-form" method="post" action="/observability/build"><button type="submit">Build observability manifest</button></form>'
        '<form class="mini-form" method="post" action="/services/topology"><button type="submit">Build service topology</button></form>'
        "</div>"
        f'<p class="muted">observability: <code>{"v4/observability_manifest.json" if observability else "not built"}</code> · service topology: <code>{"v4/service_topology.json" if topology else "not built"}</code></p>'
        + (
            "<details open><summary>Observability signals</summary><table><thead><tr><th>Signal</th><th>Source</th><th>Count</th></tr></thead>"
            f"<tbody>{obs_rows}</tbody></table></details>"
            if obs_rows
            else '<p class="muted">No observability manifest yet.</p>'
        )
        + (
            "<details open><summary>Service topology</summary><table><thead><tr><th>Service</th><th>Process</th><th>Endpoints</th><th>May call</th></tr></thead>"
            f"<tbody>{topo_rows}</tbody></table></details>"
            if topo_rows
            else '<p class="muted">No service topology artifact yet.</p>'
        )
        + "</details>"
    )


def _v5_pilotdeck_panel(project_dir: Path) -> str:
    state = _read_json(project_dir / "v5" / "project_state.json", {})
    events = _read_jsonl(project_dir / "v5" / "events.jsonl")
    try:
        from .canonical.backend_access import load_artifact_registry_preferred

        artifact_query = load_artifact_registry_preferred(project_dir)
        artifact_registry = artifact_query.get("artifacts", [])
    except Exception:
        artifact_query = {"source": "local_filesystem", "backend_status": "FALLBACK"}
        artifact_registry = _read_jsonl(project_dir / "v5" / "artifact_registry.jsonl")
    task_runs = _read_json_dir(project_dir / "v5" / "task_runs")
    qc_reports = _read_json_dir(project_dir / "v5" / "qc_reports")
    local_execution = _read_json(project_dir / "v5" / "local_execution" / "local_execution_bundle.json", {})
    try:
        from .canonical.report_manifest import build_canonical_flow_view

        canonical_flow = build_canonical_flow_view(project_dir)
    except Exception:
        canonical_flow = {"flow": [], "human_review_required": True}
    codex_queue = _load_v5_codex_queue(project_dir)
    backend_check = _read_json(project_dir / "v4" / "local_backend_check.json", {})
    backend_sync = _read_json(project_dir / "v4" / "local_backend_sync.json", {})
    storage_readiness = _read_json(project_dir / "v4" / "production_storage_readiness.json", {})
    v5_active_backends = _read_json(project_dir / "v5" / "active_backends.json", {})
    try:
        from .canonical.pilotdeck_console import build_pilotdeck_console

        pilotdeck_console = build_pilotdeck_console(project_dir)
    except Exception:
        pilotdeck_console = {}
    policy = _read_json(project_dir / "v4" / "mcp_policy.json", {})
    tokens = _read_json(project_dir / "v4" / "mcp_token_registry.json", {"tokens": []}).get("tokens", [])
    sessions = _read_json(project_dir / "v4" / "mcp_sessions.json", {"sessions": []}).get("sessions", [])

    task_status = _count_by(task_runs, "result_status")
    qc_status = _count_by(qc_reports, "overall_status")
    artifact_status = _count_by(artifact_registry, "qc_status")
    queue_status = {name: len(rows) for name, rows in codex_queue.items()}
    cards = [
        ("Stage", state.get("current_stage", "not_initialized"), "v5/project_state.json"),
        ("Events", len(events), "append-only event log"),
        ("TaskRun", len(task_runs), _format_counts(task_status)),
        ("QCReport", len(qc_reports), _format_counts(qc_status)),
        ("Artifacts", len(artifact_registry), _format_counts(artifact_status)),
        ("Artifact backend", artifact_query.get("source", "local_filesystem"), artifact_query.get("backend_status", "FALLBACK")),
        ("Codex queue", sum(queue_status.values()), _format_counts(queue_status)),
        ("PostgreSQL", backend_check.get("postgres", {}).get("status", "not_checked"), backend_check.get("active_backends", {}).get("evidence_db", "sqlite_local")),
        ("MinIO/S3", backend_check.get("minio", {}).get("status", "not_checked"), backend_check.get("active_backends", {}).get("object_store", "local_filesystem")),
        ("RBAC roles", len(policy.get("roles", {})), f"tokens {len(tokens)} · sessions {len(sessions)}"),
        ("v5 active backend", v5_active_backends.get("status", "not_activated"), _format_counts(v5_active_backends.get("active_backends", {}))),
        ("Console", pilotdeck_console.get("run_history", {}).get("nextflow_status", "not_built"), "v5/pilotdeck/console.json"),
    ]
    card_html = "".join(
        '<div class="audit-card">'
        f"<small>{html.escape(str(title))}</small>"
        f"<strong>{html.escape(str(value))}</strong>"
        f"<small>{html.escape(str(desc))}</small>"
        "</div>"
        for title, value, desc in cards
    )
    task_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('task_run_id', ''))}</code><small>{html.escape(row.get('task_id', ''))}</small></td>"
        f"<td>{html.escape(row.get('executor', ''))}</td>"
        f"<td><span class=\"pill {html.escape(str(row.get('result_status', '')).lower())}\">{html.escape(str(row.get('result_status', '')))}</span></td>"
        f"<td><code>{html.escape(row.get('qc_report_ref', ''))}</code></td>"
        f"<td>{html.escape('; '.join(row.get('artifact_refs', [])[:4]))}</td>"
        f"<td>{html.escape(row.get('failure_reason', '')[:180])}</td>"
        "</tr>"
        for row in task_runs[-12:]
    )
    qc_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('qc_report_id', ''))}</code><small>{html.escape(row.get('task_id', ''))}</small></td>"
        f"<td><span class=\"pill {html.escape(str(row.get('overall_status', '')).lower())}\">{html.escape(str(row.get('overall_status', '')))}</span></td>"
        f"<td>{html.escape(str(len(row.get('checks', []))))}</td>"
        f"<td>{html.escape('; '.join(check.get('check_id', '') + ':' + check.get('status', '') for check in row.get('checks', [])[:4]))}</td>"
        "</tr>"
        for row in qc_reports[-12:]
    )
    artifact_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('artifact_id', ''))}</code><small>{html.escape(row.get('artifact_type', ''))}</small></td>"
        f"<td><code>{html.escape(row.get('path', ''))}</code></td>"
        f"<td>{html.escape(str(row.get('exists', False)))}</td>"
        f"<td>{html.escape(row.get('qc_status', ''))}</td>"
        f"<td>{html.escape(row.get('source_backend', artifact_query.get('source', 'local_filesystem')))}</td>"
        f"<td><code>{html.escape(str(row.get('checksum_sha256', ''))[:12])}</code></td>"
        "</tr>"
        for row in artifact_registry[-12:]
    )
    queue_rows = "".join(
        "<tr>"
        f"<td>{html.escape(queue)}</td>"
        f"<td>{html.escape(str(len(rows)))}</td>"
        f"<td>{html.escape('; '.join(row.get('task_id', '') for row in rows[:4]))}</td>"
        "</tr>"
        for queue, rows in codex_queue.items()
    )
    local_execution_summary = (
        "<details open><summary>Local registered-module execution</summary>"
        f'<p class="muted">status: <code>{html.escape(local_execution.get("status", "not_run"))}</code> · '
        f'tasks: {html.escape(str(local_execution.get("task_count", 0)))} · '
        f'completed: {html.escape(str(local_execution.get("completed_count", 0)))} · '
        f'failed: {html.escape(str(local_execution.get("failed_count", 0)))} · '
        f'post-analysis: <code>{html.escape(local_execution.get("post_analysis", {}).get("status", "not_run"))}</code></p>'
        '<small>Bundle: <code>v5/local_execution/local_execution_bundle.json</code> · Packets: <code>v5/task_packets/registered_analysis_task_packets.json</code></small>'
        "</details>"
    )
    flow_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('agent_id', ''))}</code><small>{html.escape(row.get('display_name', ''))}</small></td>"
        f"<td>{html.escape(', '.join(row.get('input_refs', [])[:4]))}<small>out: {html.escape(', '.join(row.get('output_refs', [])[:4]))}</small></td>"
        f"<td><code>{html.escape(row.get('handoff_id', '') or 'not_written')}</code><small>to {html.escape(str(row.get('to_agent', '') or 'end'))}</small></td>"
        f"<td>{html.escape(row.get('claim_ceiling', ''))}</td>"
        f"<td><span class=\"pill {html.escape(str(row.get('human_gate', {}).get('status', '')).lower())}\">{html.escape(str(row.get('human_gate', {}).get('status', '')))}</span><small>{html.escape(row.get('human_gate', {}).get('reason', ''))}</small></td>"
        "</tr>"
        for row in canonical_flow.get("flow", [])
    )
    flow_panel = (
        "<details open><summary>Canonical agent workflow</summary>"
        f'<p class="muted">stage: <code>{html.escape(canonical_flow.get("current_stage", "not_initialized"))}</code> · '
        f'human review required: <code>{html.escape(str(canonical_flow.get("human_review_required", True)))}</code> · '
        f'report manifest: <code>{html.escape(canonical_flow.get("report_manifest_ref", "") or "not_built")}</code></p>'
        "<table><thead><tr><th>Agent</th><th>Refs</th><th>Handoff</th><th>Claim ceiling</th><th>Human gate</th></tr></thead>"
        f"<tbody>{flow_rows or '<tr><td colspan=\"5\">No canonical flow records yet.</td></tr>'}</tbody></table>"
        "</details>"
    )
    backend_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('check_id', ''))}</td>"
        f"<td><span class=\"pill {html.escape(str(row.get('status', '')).lower())}\">{html.escape(str(row.get('status', '')))}</span></td>"
        f"<td>{html.escape(row.get('message', '') or row.get('remediation', ''))}</td>"
        "</tr>"
        for row in (backend_check.get("checks", []) or storage_readiness.get("checks", []))[:12]
    )
    role_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(role)}</code></td>"
        f"<td>{html.escape(', '.join(scopes))}</td>"
        "</tr>"
        for role, scopes in sorted(policy.get("roles", {}).items())
    )
    command_block = (
        "python tc_lite.py local-backends-prepare --project "
        + project_dir.name
        + "\n"
        + f"powershell -ExecutionPolicy Bypass -File {project_dir / 'infra' / 'local_backends' / 'start_local_backends.ps1'}\n"
        + "python tc_lite.py local-backends-check --project "
        + project_dir.name
        + "\n"
        + "python tc_lite.py local-backends-sync --project "
        + project_dir.name
    )
    default_question = _read_text(project_dir / "research_interest.md").strip()
    return (
        "<details open><summary>v5 PilotDeck control plane</summary>"
        '<p class="muted">Canonical v5 execution visibility: TaskRun, QCReport, Artifact Registry, Codex worker queue, PostgreSQL/MinIO readiness, and project-scoped RBAC.</p>'
        '<form class="mini-form" method="post" action="/v5/run-local">'
        '<label for="v5_question">v5 local validation question</label>'
        f'<input id="v5_question" name="question" type="text" value="{html.escape(default_question)}" placeholder="Research question for v5 local run">'
        '<div class="actions">'
        '<button type="submit">Run v5 local full workflow</button>'
        '<button class="ghost" name="sources" value="geo,sra,pubmed,europe_pmc" type="submit">Run with real resource discovery</button>'
        '<a class="button ghost" href="/v5/flow">Open canonical flow page</a>'
        '<a class="button ghost" href="/v5/console">Open PilotDeck console</a>'
        '<a class="button ghost" href="/v5/resource-gate">Dataset gate</a>'
        '<a class="button ghost" href="/v5/analysis-main-path">Analysis main path</a>'
        '<a class="button ghost" href="/v5/product-report">Product report</a>'
        '<a class="button ghost" href="/v5/setup">Setup</a>'
        '<a class="button ghost" href="/v5/services">Services</a>'
        '<a class="button ghost" href="/v5/update">Update</a>'
        '<a class="button ghost" href="/v5/projects">Projects</a>'
        '<a class="button ghost" href="/v5/platform-readiness">P1 readiness</a>'
        '<a class="button ghost" href="/v5/platform-p2-readiness">P2 readiness</a>'
        '<a class="button ghost" href="/v5/access">Access</a>'
        '<a class="button ghost" href="/v5/audit">Audit</a>'
        '<a class="button ghost" href="/v5/cache">Data cache</a>'
        '<a class="button ghost" href="/v5/backend-writes">Backend writes</a>'
        '<a class="button ghost" href="/v5/artifacts">Artifacts</a>'
        '<a class="button ghost" href="/v5/evidence-claims">Evidence / claims</a>'
        '<a class="button ghost" href="/v5/wetlab">Wet-lab signoff</a>'
        '</div></form>'
        f'<div class="audit-grid">{card_html}</div>'
        f"{local_execution_summary}"
        f"{flow_panel}"
        "<details open><summary>Execution trace</summary>"
        "<table><thead><tr><th>TaskRun</th><th>Executor</th><th>Status</th><th>QC</th><th>Artifacts</th><th>Failure</th></tr></thead>"
        f"<tbody>{task_rows or '<tr><td colspan=\"6\">No v5 TaskRun records yet.</td></tr>'}</tbody></table></details>"
        "<details><summary>QC reports</summary>"
        "<table><thead><tr><th>QCReport</th><th>Status</th><th>Checks</th><th>Summary</th></tr></thead>"
        f"<tbody>{qc_rows or '<tr><td colspan=\"4\">No v5 QCReport records yet.</td></tr>'}</tbody></table></details>"
        "<details><summary>Artifact Registry</summary>"
        "<table><thead><tr><th>Artifact</th><th>Path</th><th>Exists</th><th>QC</th><th>Backend</th><th>SHA256</th></tr></thead>"
        f"<tbody>{artifact_rows or '<tr><td colspan=\"6\">No v5 artifact registry entries yet.</td></tr>'}</tbody></table></details>"
        "<details><summary>Codex worker queue</summary>"
        "<table><thead><tr><th>Queue</th><th>Count</th><th>Recent tasks</th></tr></thead>"
        f"<tbody>{queue_rows}</tbody></table></details>"
        "<details><summary>PostgreSQL / MinIO backend readiness</summary>"
        f'<p class="muted">check: <code>{html.escape("v4/local_backend_check.json" if backend_check else "not_checked")}</code> · sync: <code>{html.escape("v4/local_backend_sync.json" if backend_sync else "not_synced")}</code> · v5 active: <code>{html.escape(v5_active_backends.get("status", "not_activated"))}</code> · storage readiness: <code>{html.escape(storage_readiness.get("status", "not_built"))}</code></p>'
        f"<pre>{html.escape(command_block)}</pre>"
        '<form class="mini-form" method="post" action="/v5/backends/activate"><div class="actions"><button class="ghost" type="submit">Activate v5 PostgreSQL / MinIO backends</button></div></form>'
        "<table><thead><tr><th>Check</th><th>Status</th><th>Message</th></tr></thead>"
        f"<tbody>{backend_rows or '<tr><td colspan=\"3\">Run local backend check or storage readiness first.</td></tr>'}</tbody></table></details>"
        "<details><summary>Multi-user / project permission model</summary>"
        f'<p class="muted">policy: <code>{html.escape("v4/mcp_policy.json" if policy else "not_initialized")}</code> · registered tokens: {html.escape(str(len(tokens)))} · sessions: {html.escape(str(len(sessions)))}</p>'
        "<table><thead><tr><th>Role</th><th>Scopes</th></tr></thead>"
        f"<tbody>{role_rows or '<tr><td colspan=\"2\">No RBAC policy artifact yet. Open MCP Gateway or create a token to initialize.</td></tr>'}</tbody></table></details>"
        "</details>"
    )


def _v5_flow_page(project_dir: Path) -> bytes:
    try:
        from .canonical.report_manifest import build_canonical_flow_view, build_canonical_report_manifest

        manifest = build_canonical_report_manifest(project_dir)
        flow = build_canonical_flow_view(project_dir)
    except Exception as exc:
        return _page(project_dir, f"v5 flow page failed: {html.escape(str(exc))}")
    rows = []
    for index, row in enumerate(flow.get("flow", []), start=1):
        rows.append(
            "<tr>"
            f"<td><strong>{index}. {html.escape(row.get('display_name', ''))}</strong><small><code>{html.escape(row.get('agent_id', ''))}</code></small></td>"
            f"<td>{html.escape(row.get('responsibility', '')[:260])}</td>"
            f"<td><small>in: {html.escape(', '.join(row.get('input_refs', [])))}</small><small>out: {html.escape(', '.join(row.get('output_refs', [])))}</small></td>"
            f"<td><code>{html.escape(row.get('handoff_id', '') or 'not_written')}</code><small>to {html.escape(str(row.get('to_agent', '') or 'end'))}</small></td>"
            f"<td>{html.escape(row.get('claim_ceiling', ''))}</td>"
            f"<td><span class=\"pill {html.escape(str(row.get('human_gate', {}).get('status', '')).lower())}\">{html.escape(str(row.get('human_gate', {}).get('status', '')))}</span><small>{html.escape(row.get('human_gate', {}).get('reason', ''))}</small></td>"
            "</tr>"
        )
    checks = "".join(
        "<tr>"
        f"<td>{html.escape(check.get('check_id', ''))}</td>"
        f"<td><span class=\"pill {html.escape(check.get('status', '').lower())}\">{html.escape(check.get('status', ''))}</span></td>"
        "</tr>"
        for check in manifest.get("consistency_checks", [])
    )
    body = f"""
    <main class="app-shell">
      <section class="app-section">
        <h1>v5 Canonical Flow</h1>
        <p class="muted">Project stage: <code>{html.escape(flow.get("current_stage", ""))}</code> · Report manifest: <code>v5/reports/canonical_report_manifest.json</code> · Human review required: <code>{html.escape(str(flow.get("human_review_required", True)))}</code></p>
        <div class="actions"><a class="button ghost" href="/">Back to PilotDeck</a><a class="button ghost" href="/report">Open report</a></div>
        <div class="audit-grid">
          <div class="audit-card"><small>TaskRun</small><strong>{html.escape(str(flow.get("task_run_count", 0)))}</strong><small>v5 task runs</small></div>
          <div class="audit-card"><small>QCReport</small><strong>{html.escape(str(flow.get("qc_report_count", 0)))}</strong><small>QC reports</small></div>
          <div class="audit-card"><small>Artifacts</small><strong>{html.escape(str(flow.get("artifact_count", 0)))}</strong><small>artifact manifests</small></div>
          <div class="audit-card"><small>Gate</small><strong>{html.escape(manifest.get("status", ""))}</strong><small>{html.escape(manifest.get("human_review_gate", {}).get("reason", ""))}</small></div>
        </div>
        <table><thead><tr><th>Agent</th><th>Responsibility</th><th>Object refs</th><th>Handoff</th><th>Claim ceiling</th><th>Human gate</th></tr></thead><tbody>{''.join(rows)}</tbody></table>
        <h2>Manifest consistency</h2>
        <table><thead><tr><th>Check</th><th>Status</th></tr></thead><tbody>{checks}</tbody></table>
      </section>
    </main>
    """
    return _standalone_page(project_dir, body, title="v5 Canonical Flow")


def _v5_console_page(project_dir: Path) -> bytes:
    try:
        from .canonical.pilotdeck_console import build_pilotdeck_console

        console = build_pilotdeck_console(project_dir)
    except Exception as exc:
        return _page(project_dir, f"v5 console failed: {html.escape(str(exc))}")
    history = console.get("run_history", {})
    approval = console.get("approval_detail", {})
    recovery = console.get("failure_recovery", {})
    artifacts = console.get("artifact_drilldown", {}).get("recent_artifacts", [])
    evidence = console.get("evidence_drilldown", {}).get("items", [])
    task_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('task_run_id', ''))}</code><small>{html.escape(row.get('task_id', ''))}</small></td>"
        f"<td>{html.escape(row.get('executor', ''))}</td>"
        f"<td>{html.escape(row.get('result_status', ''))}</td>"
        f"<td>{html.escape(row.get('failure_reason', '')[:160])}</td>"
        "</tr>"
        for row in history.get("recent_task_runs", [])
    )
    artifact_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('artifact_id', ''))}</code></td>"
        f"<td><code>{html.escape(row.get('path', ''))}</code></td>"
        f"<td>{html.escape(row.get('artifact_type', ''))}</td>"
        f"<td>{html.escape(row.get('qc_status', ''))}</td>"
        "</tr>"
        for row in artifacts
    )
    evidence_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('entity_symbol', ''))}</td>"
        f"<td>{html.escape(row.get('evidence_type', ''))}</td>"
        f"<td>{html.escape(row.get('evidence_level', ''))}</td>"
        f"<td>{html.escape(row.get('source_dataset', ''))}</td>"
        "</tr>"
        for row in evidence
    )
    recovery_rows = "".join(_v5_recovery_row(row) for row in recovery.get("items", []))
    body = f"""
    <main class="app-shell">
      <section class="app-section">
        <h1>v5 PilotDeck Console</h1>
        <p class="muted">Project: <code>{html.escape(console.get("project_id", ""))}</code> · Nextflow: <code>{html.escape(history.get("nextflow_status", "not_run"))}</code> · LLM: <code>{html.escape(history.get("llm_orchestration_status", "not_run"))}</code> · Approval: <code>{html.escape(approval.get("approval_status", "draft"))}</code></p>
        <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/flow">Canonical flow</a><form class="mini-form" method="post" action="/v5/pilotdeck-console"><button type="submit">Refresh console</button></form></div>
        <div class="audit-grid">
          <div class="audit-card"><small>Task runs</small><strong>{html.escape(str(history.get("task_run_count", 0)))}</strong><small>run history</small></div>
          <div class="audit-card"><small>Open recovery</small><strong>{html.escape(str(recovery.get("open_count", 0)))}</strong><small>failure recovery</small></div>
          <div class="audit-card"><small>Artifacts</small><strong>{html.escape(str(console.get("artifact_drilldown", {}).get("artifact_count", 0)))}</strong><small>artifact drill-down</small></div>
          <div class="audit-card"><small>Evidence query</small><strong>{html.escape(str(console.get("evidence_drilldown", {}).get("match_count", 0)))}</strong><small>last evidence search</small></div>
        </div>
        <details open><summary>Run history</summary><table><thead><tr><th>TaskRun</th><th>Executor</th><th>Status</th><th>Failure</th></tr></thead><tbody>{task_rows or '<tr><td colspan="4">No TaskRun records.</td></tr>'}</tbody></table></details>
        <details open><summary>Failure recovery</summary><table><thead><tr><th>Category</th><th>Severity</th><th>Reason</th><th>Recovery</th><th>Suggested command</th></tr></thead><tbody>{recovery_rows or '<tr><td colspan="5">No open recovery items.</td></tr>'}</tbody></table></details>
        <details open><summary>Artifact drill-down</summary><table><thead><tr><th>Artifact</th><th>Path</th><th>Type</th><th>QC</th></tr></thead><tbody>{artifact_rows or '<tr><td colspan="4">No artifacts.</td></tr>'}</tbody></table></details>
        <details open><summary>Evidence drill-down</summary><table><thead><tr><th>Gene</th><th>Type</th><th>Level</th><th>Source</th></tr></thead><tbody>{evidence_rows or '<tr><td colspan="4">Run an Evidence DB query to populate this panel.</td></tr>'}</tbody></table></details>
      </section>
    </main>
    """
    return _standalone_page(project_dir, body, title="v5 PilotDeck Console")


def _v5_recovery_row(row: dict) -> str:
    category = row.get("category") or row.get("stage") or ""
    severity = row.get("severity") or row.get("status") or ""
    actions = row.get("recovery_actions") or row.get("actions") or []
    if isinstance(actions, list):
        action_text = "; ".join(str(item) for item in actions[:2])
    else:
        action_text = str(actions)
    commands = row.get("rerun_commands") or row.get("commands") or []
    if isinstance(commands, list):
        command_text = commands[0] if commands else ""
    else:
        command_text = row.get("command") or str(commands)
    return (
        "<tr>"
        f"<td>{html.escape(str(category))}</td>"
        f"<td>{html.escape(str(severity))}</td>"
        f"<td>{html.escape(str(row.get('reason', ''))[:220])}</td>"
        f"<td>{html.escape(action_text[:220])}</td>"
        f"<td><code>{html.escape(str(command_text)[:220])}</code></td>"
        "</tr>"
    )


def _v5_resource_gate_page(project_dir: Path) -> bytes:
    try:
        from .canonical.resource_gate import build_resource_gate_report

        gate = build_resource_gate_report(project_dir)
    except Exception as exc:
        return _page(project_dir, f"v5 resource gate failed: {html.escape(str(exc))}")
    rows = []
    for item in gate.get("gate_items", []):
        status = _zh_ui_text(item.get("gate_status", ""))
        issues = "、".join(_zh_ui_text(row) for row in item.get("blocking_issues", [])) or "无"
        suggestions = "；".join(_zh_ui_text(row) for row in item.get("recovery_suggestions", [])[:3])
        missing = "、".join(_zh_ui_text(row) for row in item.get("missing_required_fields", [])) or "无"
        matrix_preview = item.get("matrix_parse_preview", {}) or {}
        metadata_preview_rows = "".join(
            "<tr>"
            f"<td><code>{html.escape(str(row.get('name', '')))}</code></td>"
            f"<td>{html.escape(str(row.get('non_empty', 0)))}</td>"
            f"<td>{html.escape(str(row.get('unique_count', 0)))}</td>"
            f"<td>{html.escape(json.dumps(row.get('value_counts', {}), ensure_ascii=False))}</td>"
            "</tr>"
            for row in item.get("metadata_value_preview", [])[:12]
        )
        required_rows = "".join(
            "<tr>"
            f"<td>{html.escape(_zh_ui_text(field))}</td>"
            f"<td><span class=\"pill {html.escape(str(row.get('status', '')).lower())}\">{html.escape(_zh_ui_text(row.get('status', '')))}</span></td>"
            f"<td>{html.escape(_zh_ui_text(row.get('value', '')))}</td>"
            f"<td>{html.escape(_zh_ui_text(row.get('hint', '')))}</td>"
            "</tr>"
            for field, row in (item.get("required_fields_status", {}) or {}).items()
        )
        correction = item.get("manual_correction", {}) or {}
        suggested = item.get("suggested_manual_correction", {}) or {}
        def _prefill(field: str) -> str:
            return str(correction.get(field) or suggested.get(field) or "")
        candidate_id = item.get("resource_candidate_id", "")
        form = f"""
        <form class="mini-form" method="post" action="/v5/resource-correction">
          <input type="hidden" name="resource_candidate_id" value="{html.escape(candidate_id)}">
          <div class="method-role-row">
            <label>分组列</label><input name="group_column" value="{html.escape(_prefill("group_column"))}" placeholder="diagnosis / condition / group">
            <label>病例标签</label><input name="case_label" value="{html.escape(_prefill("case_label"))}" placeholder="case / disease">
            <label>对照标签</label><input name="control_label" value="{html.escape(_prefill("control_label"))}" placeholder="control / healthy">
          </div>
          <div class="method-role-row">
            <label>物种</label><input name="organism" value="{html.escape(_prefill("organism"))}" placeholder="Homo sapiens">
            <label>组织/细胞类型</label><input name="tissue" value="{html.escape(_prefill("tissue"))}" placeholder="skeletal muscle">
            <label>平台</label><input name="platform" value="{html.escape(_prefill("platform"))}" placeholder="GPL570 / RNA-seq / cellxgene">
          </div>
          <div class="method-role-row">
            <label>样本数</label><input name="sample_count" value="{html.escape(_prefill("sample_count"))}" placeholder="24">
            <label>备注</label><input name="notes" value="{html.escape(_prefill("notes"))}" placeholder="人工元数据复核备注">
            <div class="actions"><button type="submit">保存纠错并重新检查</button></div>
          </div>
        </form>
        """
        run_form = ""
        if item.get("can_enter_datasets_locked"):
            correction = item.get("manual_correction", {}) or {}
            run_form = f"""
            <form class="mini-form" method="post" action="/v5/analysis-main-path/run">
              <input type="hidden" name="accession" value="{html.escape(str(item.get("accession", "")))}">
              <input type="hidden" name="source" value="{html.escape(str(item.get("source_database", "geo")))}">
              <input type="hidden" name="case_label" value="{html.escape(str(correction.get("case_label", "")))}">
              <input type="hidden" name="control_label" value="{html.escape(str(correction.get("control_label", "")))}">
              <input type="hidden" name="tissue" value="{html.escape(str(correction.get("tissue", "")))}">
              <input type="hidden" name="organism" value="{html.escape(str(correction.get("organism", "")))}">
              <div class="actions"><button type="submit">运行锁库分析</button><small>使用已锁库的元数据纠错结果，并路由到 GEO 导入、矩阵解析和已注册分析。</small></div>
            </form>
            """
        rows.append(
            "<details open><summary>"
            f"<strong>{html.escape(str(item.get('accession', '') or candidate_id))}</strong> "
            f"<span class=\"pill {html.escape(status.lower())}\">{html.escape(status)}</span> "
            f"<small>{html.escape(_zh_ui_text(item.get('source_database', '')))} · 可锁库：{html.escape('是' if item.get('can_enter_datasets_locked', False) else '否')}</small>"
            "</summary>"
            "<table><tbody>"
            f"<tr><th>候选项</th><td><code>{html.escape(candidate_id)}</code></td></tr>"
            f"<tr><th>阻塞问题</th><td>{html.escape(issues)}</td></tr>"
            f"<tr><th>缺失必填字段</th><td>{html.escape(missing)}</td></tr>"
            f"<tr><th>矩阵可解析</th><td>{html.escape('是' if item.get('matrix_parse_ready') else '否')} · {html.escape(_zh_ui_text(item.get('matrix_parse_status', '')))}</td></tr>"
            f"<tr><th>矩阵路径</th><td><code>{html.escape(str(matrix_preview.get('expected_expression_matrix', '')))}</code><br><code>{html.escape(str(matrix_preview.get('expected_metadata', '')))}</code></td></tr>"
            f"<tr><th>下一步操作</th><td>{html.escape(_zh_ui_text(item.get('next_human_action', '')))}</td></tr>"
            f"<tr><th>恢复建议</th><td>{html.escape(suggestions)}</td></tr>"
            f"<tr><th>原因</th><td>{html.escape(_zh_ui_text(item.get('reason', '')))}</td></tr>"
            "</tbody></table>"
            + (f"<table><thead><tr><th>必填字段</th><th>状态</th><th>当前值</th><th>如何修复</th></tr></thead><tbody>{required_rows}</tbody></table>" if required_rows else "")
            + (f"<details><summary>metadata 值预览</summary><table><thead><tr><th>列名</th><th>非空</th><th>唯一值</th><th>候选取值</th></tr></thead><tbody>{metadata_preview_rows}</tbody></table></details>" if metadata_preview_rows else "")
            + form
            + run_form
            + "</details>"
        )
    body = f"""
    <main class="app-shell">
      <section class="app-section">
        <h1>v5 数据集锁库</h1>
        <p class="muted">用于 GEO/SRA/ArrayExpress/cellxgene 候选数据集的人工元数据纠错。只有分组、样本量、物种、组织和平台经人工确认后，数据集才能进入 DATASETS_LOCKED。</p>
        <div class="actions"><a class="button ghost" href="/">返回</a><form class="mini-form" method="post" action="/v5/resource-gate"><button type="submit">重新检查锁库条件</button></form></div>
        <div class="audit-grid">
          <div class="audit-card"><small>候选数据集</small><strong>{html.escape(str(gate.get("candidate_count", 0)))}</strong><small>资源候选数</small></div>
          <div class="audit-card"><small>人工复核</small><strong>{html.escape(str(gate.get("manual_review_count", 0)))}</strong><small>待人工纠错队列</small></div>
          <div class="audit-card"><small>可锁库</small><strong>{html.escape(str(gate.get("datasets_lockable_count", 0)))}</strong><small>已满足 DATASETS_LOCKED 条件</small></div>
          <div class="audit-card"><small>矩阵可解析</small><strong>{html.escape(str(gate.get("matrix_parse_ready_count", 0)))}</strong><small>已找到矩阵与 metadata</small></div>
          <div class="audit-card"><small>纠错记录</small><strong>{html.escape(str(gate.get("manual_correction_count", 0)))}</strong><small>人工记录</small></div>
        </div>
        {''.join(rows) or '<p class="muted">还没有资源候选。请先运行一次 v5 本地流程。</p>'}
      </section>
    </main>
    """
    return _standalone_page(project_dir, body, title="v5 数据集锁库")


def _v5_analysis_main_path_page(project_dir: Path) -> bytes:
    manifest = _read_json(project_dir / "v5" / "analysis_main_path" / "main_path_manifest.json", {})
    gate = _read_json(project_dir / "v5" / "resource_discovery" / "resource_gate_report.json", {})
    selected = manifest.get("selected_dataset", {})
    stages = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('stage', ''))}</td>"
        f"<td><span class=\"pill {html.escape(str(row.get('status', '')).lower())}\">{html.escape(str(row.get('status', '')))}</span></td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        "</tr>"
        for row in manifest.get("stages", [])
    )
    recovery = "".join(_v5_recovery_row(row) for row in manifest.get("recovery", []))
    default_question = _read_text(project_dir / "research_interest.md").strip()
    body = f"""
    <main class="app-shell">
      <section class="app-section">
        <h1>v5 Analysis Main Path</h1>
        <p class="muted">One-button real-data path: resource lock → download/parse/metadata alignment → registered local analysis → QC → Evidence/Artifact/Report refs. If metadata is not lockable, the run stops with recovery advice.</p>
        <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/resource-gate">Dataset gate</a><a class="button ghost" href="/v5/product-report">Product report</a></div>
        <form class="mini-form" method="post" action="/v5/analysis-main-path/run">
          <label>Question</label>
          <input name="question" value="{html.escape(default_question)}" placeholder="Research question">
          <div class="method-role-row">
            <label>Accession</label><input name="accession" value="{html.escape(str(selected.get("accession", "")))}" placeholder="optional GSE accession">
            <label>Case hint / label</label><input name="case_label" value="{html.escape(str(selected.get("case_label", "")))}" placeholder="case/disease label">
            <label>Control hint / label</label><input name="control_label" value="{html.escape(str(selected.get("control_label", "")))}" placeholder="control/healthy label">
          </div>
          <div class="method-role-row">
            <label>Tissue</label><input name="tissue" value="{html.escape(str(selected.get("tissue", "")))}" placeholder="skeletal muscle">
            <label>Organism</label><input name="organism" value="{html.escape(str(selected.get("organism", "")))}" placeholder="human">
            <label>Max packets</label><input name="max_analysis_packets" value="" placeholder="optional number">
          </div>
          <label><input type="checkbox" name="force_download" value="1"> Force GEO download</label>
          <div class="actions"><button type="submit">Run analysis main path</button></div>
        </form>
        <div class="audit-grid">
          <div class="audit-card"><small>Status</small><strong>{html.escape(manifest.get("status", "not_run"))}</strong><small><code>v5/analysis_main_path/main_path_manifest.json</code></small></div>
          <div class="audit-card"><small>Dataset</small><strong>{html.escape(str(selected.get("accession", "not_selected")))}</strong><small>{html.escape(str(selected.get("selection_mode", "")))}</small></div>
          <div class="audit-card"><small>Task packets</small><strong>{html.escape(str(manifest.get("task_packet_count", 0)))}</strong><small>{html.escape(manifest.get("execution_status", "not_run"))}</small></div>
          <div class="audit-card"><small>Lockable datasets</small><strong>{html.escape(str(gate.get("datasets_lockable_count", 0)))}</strong><small>from resource gate</small></div>
        </div>
        <details open><summary>Stages</summary><table><thead><tr><th>Stage</th><th>Status</th><th>Message</th></tr></thead><tbody>{stages or '<tr><td colspan="3">No main-path run yet.</td></tr>'}</tbody></table></details>
        <details open><summary>Recovery</summary><table><thead><tr><th>Category</th><th>Severity</th><th>Reason</th><th>Recovery</th><th>Suggested command</th></tr></thead><tbody>{recovery or '<tr><td colspan="5">No recovery item.</td></tr>'}</tbody></table></details>
      </section>
    </main>
    """
    return _standalone_page(project_dir, body, title="v5 Analysis Main Path")


def _v5_product_report_page(project_dir: Path) -> bytes:
    manifest = _read_json(project_dir / "v5" / "reports" / "product_report_manifest.json", {})
    report_path = project_dir / "v5" / "reports" / "product_report.html"
    candidates = manifest.get("top_candidates", [])
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('rank', '')))}</td>"
        f"<td><strong>{html.escape(row.get('gene', ''))}</strong><small>{html.escape(row.get('route', ''))}</small></td>"
        f"<td>{html.escape(str(row.get('final_score', '')))}</td>"
        f"<td>{html.escape(str(row.get('tier', '')))} / {html.escape(str(row.get('hard_gate_status', '')))}</td>"
        f"<td>{html.escape(', '.join(row.get('covered_axes', [])[:5]))}</td>"
        f"<td>{html.escape(', '.join(row.get('missing_axes', [])[:5]))}</td>"
        "</tr>"
        for row in candidates
    )
    limitations = "".join(f"<li>{html.escape(item)}</li>" for item in manifest.get("limitations", []))
    report_link = f'<a class="button" href="/v5/product-report/html">Open generated HTML report</a>' if report_path.exists() else ""
    body = f"""
    <main class="app-shell">
      <section class="app-section">
        <h1>v5 Product Report</h1>
        <p class="muted">Formal project-facing report: conclusion summary, candidate ranking, evidence chain, failure recovery, limitations, experiment suggestions, and export references.</p>
        <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/analysis-main-path">Analysis main path</a><form class="mini-form" method="post" action="/v5/product-report/build"><button type="submit">Build product report</button></form>{report_link}</div>
        <div class="audit-grid">
          <div class="audit-card"><small>Status</small><strong>{html.escape(manifest.get("status", "not_built"))}</strong><small><code>v5/reports/product_report_manifest.json</code></small></div>
          <div class="audit-card"><small>Candidates</small><strong>{html.escape(str(manifest.get("candidate_count", 0)))}</strong><small>ranked candidates</small></div>
          <div class="audit-card"><small>Artifacts</small><strong>{html.escape(str((manifest.get("evidence_chain") or {}).get("artifact_count", 0)))}</strong><small>registered artifacts</small></div>
          <div class="audit-card"><small>Human gate</small><strong>{html.escape(str(((manifest.get("evidence_chain") or {}).get("human_review_gate") or {}).get("required", True)))}</strong><small>review required before final claim</small></div>
        </div>
        <details open><summary>Candidate ranking</summary><table><thead><tr><th>Rank</th><th>Gene</th><th>Score</th><th>Gate</th><th>Covered axes</th><th>Missing axes</th></tr></thead><tbody>{rows or '<tr><td colspan="6">Build product report after scoring.</td></tr>'}</tbody></table></details>
        <details open><summary>Limitations</summary><ul>{limitations or '<li>No product report limitations recorded yet.</li>'}</ul></details>
      </section>
    </main>
    """
    return _standalone_page(project_dir, body, title="v5 Product Report")


def _v5_projects_page(project_dir: Path) -> bytes:
    registry = list_projects(project_dir.parent)
    root = Path(registry.get("root", project_dir.parent))
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('project_id', ''))}</code><small>{html.escape('archived' if row.get('archived') else 'active')}</small></td>"
        f"<td>{html.escape(row.get('stage', 'not_initialized'))}</td>"
        f"<td><code>{html.escape(row.get('path', ''))}</code></td>"
        "<td>"
        f"<form class=\"mini-form\" method=\"post\" action=\"/v5/projects/archive\"><input type=\"hidden\" name=\"project_id\" value=\"{html.escape(row.get('project_id', ''))}\"><input type=\"hidden\" name=\"archived\" value=\"{'0' if row.get('archived') else '1'}\"><button class=\"small-button ghost\" type=\"submit\">{'Unarchive' if row.get('archived') else 'Archive'}</button></form>"
        f"<form class=\"mini-form\" method=\"post\" action=\"/v5/projects/export\"><input type=\"hidden\" name=\"project_id\" value=\"{html.escape(row.get('project_id', ''))}\"><button class=\"small-button ghost\" type=\"submit\">Export</button></form>"
        f"<form class=\"mini-form\" method=\"post\" action=\"/v5/projects/delete\"><input type=\"hidden\" name=\"project_id\" value=\"{html.escape(row.get('project_id', ''))}\"><input type=\"hidden\" name=\"backup\" value=\"1\"><button class=\"small-button ghost\" type=\"submit\">Delete with backup</button></form>"
        "</td>"
        "</tr>"
        for row in registry.get("projects", [])
    )
    project_options = "".join(
        f'<option value="{html.escape(row.get("project_id", ""))}">{html.escape(row.get("project_id", ""))}</option>'
        for row in registry.get("projects", [])
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Projects</h1>
      <p class="muted">Current project root: <code>{html.escape(str(root))}</code>. Creating, cloning, importing and archiving operate on local project folders; switching the active UI project still requires starting the server for that project.</p>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/setup">Setup</a><a class="button ghost" href="/v5/services">Services</a></div>
      <div class="audit-grid">
        <form class="mini-form" method="post" action="/v5/projects/create">
          <label>New project id</label>
          <input name="project_id" type="text" placeholder="new_project_id">
          <label>Clone from template project</label>
          <select name="template_project"><option value="">Blank project</option>{project_options}</select>
          <button type="submit">Create project</button>
        </form>
        <form class="mini-form" method="post" action="/v5/projects/import">
          <label>Import project zip path</label>
          <input name="zip_path" type="text" placeholder="D:/path/project_export.zip">
          <label>Imported project id</label>
          <input name="project_id" type="text" placeholder="optional_new_id">
          <button type="submit">Import project</button>
        </form>
      </div>
      <table><thead><tr><th>Project</th><th>Stage</th><th>Path</th><th>Actions</th></tr></thead><tbody>{rows or '<tr><td colspan="4">No projects found.</td></tr>'}</tbody></table>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="Projects")


def _v5_setup_page(project_dir: Path) -> bytes:
    from .platform_config import build_post_install_setup_wizard

    cfg = load_platform_config(project_dir)
    wizard = build_post_install_setup_wizard(project_dir)
    readiness = platform_readiness(project_dir)
    llm = cfg.get("llm", {})
    docker = cfg.get("docker", {})
    backend = _read_json(project_dir / "v5" / "platform" / "backend_primary_status.json", {})
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('check_id', ''))}</td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        f"<td>{html.escape(row.get('remediation', ''))}</td>"
        "</tr>"
        for row in readiness.get("checks", [])
    )
    step_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('step_id', ''))}</code><small>{html.escape(row.get('label', ''))}</small></td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('help', ''))}</td>"
        "</tr>"
        for row in wizard.get("steps", [])
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Setup Wizard</h1>
      <p class="muted">Configure local v5 runtime without exposing secrets in the UI. API keys are stored in <code>configs/secrets.local.json</code> and are excluded from project export.</p>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/services">Service status</a><a class="button ghost" href="/v5/projects">Projects</a></div>
      <div class="audit-grid">
        <div class="audit-card"><small>Readiness</small><strong>{html.escape(readiness.get("status", ""))}</strong><small>v5/platform/platform_readiness.json</small></div>
        <div class="audit-card"><small>LLM key</small><strong>{html.escape(llm.get("api_key_status", "not_set"))}</strong><small>{html.escape(llm.get("provider", ""))} · {html.escape(llm.get("model", ""))}</small></div>
        <div class="audit-card"><small>UI port</small><strong>{html.escape(str(cfg.get("ui_port", "")))}</strong><small>local browser endpoint</small></div>
        <div class="audit-card"><small>Docker backend</small><strong>{html.escape("enabled" if docker.get("enabled") else "disabled")}</strong><small>{html.escape(docker.get("compose_project", ""))}</small></div>
        <div class="audit-card"><small>Backend path</small><strong>{html.escape(backend.get("overall_status", "unknown"))}</strong><small>{html.escape(json.dumps(backend.get("active_backends", {}), ensure_ascii=False))}</small></div>
      </div>
      <form class="mini-form" method="post" action="/v5/setup/save">
        <label>LLM provider</label>
        <select name="llm_provider"><option value="deepseek" {"selected" if llm.get("provider") == "deepseek" else ""}>DeepSeek / OpenAI-compatible</option><option value="openai" {"selected" if llm.get("provider") == "openai" else ""}>OpenAI</option></select>
        <label>Base URL</label>
        <input name="llm_base_url" type="text" value="{html.escape(llm.get("base_url", ""))}" placeholder="https://api.deepseek.com">
        <label>Model</label>
        <input name="llm_model" type="text" value="{html.escape(llm.get("model", ""))}" placeholder="deepseek-chat">
        <label>API key</label>
        <input name="openai_api_key" type="password" value="" placeholder="leave blank to keep existing key">
        <label>UI port</label>
        <input name="ui_port" type="number" value="{html.escape(str(cfg.get("ui_port", 8801)))}">
        <label><input name="docker_enabled" type="checkbox" value="1" {"checked" if docker.get("enabled") else ""}> Enable Docker PostgreSQL/MinIO backend</label>
        <label>Rscript path</label>
        <input name="rscript_path" type="text" value="{html.escape(cfg.get("r", {}).get("rscript_path", ""))}" placeholder="Rscript">
        <label>Nextflow path</label>
        <input name="nextflow_path" type="text" value="{html.escape(cfg.get("nextflow", {}).get("nextflow_path", ""))}" placeholder="nextflow">
        <button type="submit">Save and check</button>
      </form>
      <details open><summary>安装后配置步骤</summary><table><thead><tr><th>Step</th><th>Status</th><th>说明</th></tr></thead><tbody>{step_rows}</tbody></table></details>
      <table><thead><tr><th>Check</th><th>Status</th><th>Message</th><th>Recovery</th></tr></thead><tbody>{rows}</tbody></table>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="v5 Setup")


def _v5_services_page(project_dir: Path) -> bytes:
    readiness = platform_readiness(project_dir)
    status = service_status(project_dir)
    control = build_service_control_manifest(project_dir)
    backend = _read_json(project_dir / "v5" / "active_backends.json", {})
    service_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('check_id', ''))}</td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        "</tr>"
        for row in readiness.get("checks", [])
    )
    recovery_rows = "".join(f"<li>{html.escape(item)}</li>" for item in status.get("recovery", []))
    launch_command = f"python tc_lite.py serve --project {project_dir.name} --port {status.get('ui', {}).get('port', 8801)}"
    repair_commands = [
        ("Doctor", f"python tc_lite.py v5-doctor --project {project_dir.name}"),
        ("Nextflow repair", f"python tc_lite.py nextflow-bootstrap --project {project_dir.name} --download --install-runtime"),
        ("Docker backend repair", f"python tc_lite.py local-backends-prepare --project {project_dir.name} && python tc_lite.py v5-backends-activate --project {project_dir.name}"),
        ("Rscript repair", "Install R 4.x, then set Rscript path in the Setup Wizard."),
    ]
    repair_rows = "".join(
        "<tr>"
        f"<td>{html.escape(label)}</td>"
        f"<td><code>{html.escape(command)}</code></td>"
        "</tr>"
        for label, command in repair_commands
    )
    command_rows = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td><code>{html.escape(command)}</code></td>"
        "</tr>"
        for name, command in control.get("commands", {}).items()
    )
    service_recovery_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('issue', ''))}</td>"
        f"<td>{html.escape(row.get('action', ''))}</td>"
        "</tr>"
        for row in control.get("recovery", [])
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Service Manager</h1>
      <p class="muted">Health: <code>{html.escape(status.get("health", ""))}</code> · URL: <a href="{html.escape(status.get("ui", {}).get("url", ""))}">{html.escape(status.get("ui", {}).get("url", ""))}</a> · Backend: <code>{html.escape(backend.get("status", "not_activated"))}</code></p>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/setup">Setup</a><form class="mini-form" method="post" action="/v5/services/refresh"><button type="submit">Refresh health</button></form><form class="mini-form" method="post" action="/v5/backends/activate"><button class="ghost" type="submit">Activate backends</button></form></div>
      <div class="audit-grid">
        <div class="audit-card"><small>UI service</small><strong>{html.escape(status.get("health", ""))}</strong><small>port {html.escape(str(status.get("ui", {}).get("port", "")))}</small></div>
        <div class="audit-card"><small>Logs</small><strong>linked</strong><small>{html.escape(status.get("logs", {}).get("project_run_status", ""))}</small></div>
        <div class="audit-card"><small>Evidence DB</small><strong>{html.escape(backend.get("active_backends", {}).get("evidence_db", "sqlite_local"))}</strong><small>active backend</small></div>
        <div class="audit-card"><small>Object store</small><strong>{html.escape(backend.get("active_backends", {}).get("object_store", "local_filesystem"))}</strong><small>active backend</small></div>
        <div class="audit-card"><small>Recovered port</small><strong>{html.escape(str(control.get("selected_port", "")))}</strong><small>conflict: {html.escape(str(control.get("port_conflict", False)))}</small></div>
      </div>
      <details open><summary>Service control commands</summary><table><thead><tr><th>Action</th><th>Command</th></tr></thead><tbody>{command_rows}</tbody></table></details>
      <details open><summary>Port conflict recovery</summary><table><thead><tr><th>Issue</th><th>Action</th></tr></thead><tbody>{service_recovery_rows}</tbody></table></details>
      <details open><summary>Start / restart command</summary><pre>{html.escape(launch_command)}</pre><p class="muted">The web process cannot safely terminate itself from a form request; use this command if a port is occupied or after changing port settings.</p></details>
      <details open><summary>Diagnostic repair actions</summary><table><thead><tr><th>Action</th><th>Command / instruction</th></tr></thead><tbody>{repair_rows}</tbody></table></details>
      <details open><summary>Recovery advice</summary><ul>{recovery_rows or '<li>No open service recovery item.</li>'}</ul></details>
      <table><thead><tr><th>Check</th><th>Status</th><th>Message</th></tr></thead><tbody>{service_rows}</tbody></table>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="v5 Services")


def _v5_update_page(project_dir: Path) -> bytes:
    manifest = write_update_manifest(project_dir)
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Update</h1>
      <p class="muted">Version: <code>{html.escape(manifest.get("current_version", ""))}</code> · Packages found: <code>{html.escape(str(manifest.get("package_count", 0)))}</code></p>
      <div class="actions"><a class="button ghost" href="/">Back</a><form class="mini-form" method="post" action="/v5/update/manifest"><button type="submit">Refresh manifest</button></form></div>
      <div class="audit-grid">
        <div class="audit-card"><small>Latest package</small><strong>{html.escape(Path(manifest.get("latest_package", "")).name or "none")}</strong><small>{html.escape(manifest.get("latest_package", ""))}</small></div>
        <div class="audit-card"><small>User data</small><strong>preserved</strong><small>projects are kept unless reset is explicitly requested</small></div>
        <div class="audit-card"><small>Backup policy</small><strong>{html.escape(str(manifest.get("policy", {}).get("backup_before_update", True)))}</strong><small>backup before update</small></div>
        <div class="audit-card"><small>Manifest</small><strong>written</strong><small>v5/platform/update_manifest.json</small></div>
      </div>
      <details open><summary>Upgrade policy</summary><pre>{html.escape(json.dumps(manifest.get("policy", {}), indent=2, ensure_ascii=False))}</pre></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="v5 Update")


def _v5_access_page(project_dir: Path) -> bytes:
    from .canonical.access_control import access_readiness, load_access_registry, query_access_audit
    from .canonical.access_admin import build_access_admin_dashboard

    readiness = access_readiness(project_dir)
    dashboard = build_access_admin_dashboard(project_dir)
    registry = load_access_registry(project_dir)
    audit = query_access_audit(project_dir, limit=30)
    users = {row.get("user_id", ""): row for row in registry.get("users", [])}
    role_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(role)}</code></td>"
        f"<td>{html.escape(', '.join(perms))}</td>"
        "</tr>"
        for role, perms in sorted(registry.get("roles", {}).items())
    )
    user_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('user_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('display_name', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('created_at', ''))}</td>"
        "</tr>"
        for row in registry.get("users", [])
    )
    member_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('user_id', ''))}</code><small>{html.escape(users.get(row.get('user_id', ''), {}).get('display_name', ''))}</small></td>"
        f"<td>{html.escape(row.get('role', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('created_at', ''))}</td>"
        "</tr>"
        for row in registry.get("members", [])
    )
    token_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('token_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('user_id', ''))}</td>"
        f"<td>{html.escape(row.get('role', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('expires_at', ''))}</td>"
        f"<td>{html.escape(', '.join(row.get('scopes', [])[:6]))}</td>"
        "</tr>"
        for row in registry.get("tokens", [])
    )
    audit_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('created_at', ''))}</td>"
        f"<td>{html.escape(row.get('actor', ''))}</td>"
        f"<td>{html.escape(row.get('action', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('reason', ''))}</td>"
        "</tr>"
        for row in audit.get("events", [])
    )
    action_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('priority', ''))}</td>"
        f"<td>{html.escape(row.get('action', ''))}</td>"
        "</tr>"
        for row in dashboard.get("actions_required", [])
    )
    gap_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('priority', ''))}</td>"
        f"<td><code>{html.escape(row.get('gap', ''))}</code></td>"
        f"<td>{html.escape(row.get('next_step', ''))}</td>"
        "</tr>"
        for row in dashboard.get("productization_gaps", [])
    )
    capability_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('capability', ''))}</td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('entrypoint', ''))}</td>"
        "</tr>"
        for row in dashboard.get("admin_capabilities", [])
    )
    role_coverage_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('role', ''))}</code></td>"
        f"<td><span class=\"pill {'pass' if row.get('covered') else 'review'}\">{html.escape('covered' if row.get('covered') else 'missing')}</span></td>"
        f"<td>{html.escape(str(row.get('active_member_count', 0)))}</td>"
        f"<td>{html.escape(', '.join(row.get('permissions', [])[:8]))}</td>"
        "</tr>"
        for row in dashboard.get("role_coverage", [])
    )
    lifecycle = dashboard.get("token_lifecycle_summary", {})
    lifecycle_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(status)}</code></td>"
        f"<td>{html.escape(str(count))}</td>"
        "</tr>"
        for status, count in sorted(lifecycle.get("by_lifecycle_status", {}).items())
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Access Control</h1>
      <p class="muted">Status: <code>{html.escape(readiness.get("status", ""))}</code> · Registry: <code>{html.escape(readiness.get("registry_ref", ""))}</code> · Audit: <code>{html.escape(readiness.get("audit_ref", ""))}</code></p>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/audit">Audit search</a><form class="mini-form" method="post" action="/v5/access/dashboard"><button type="submit">Refresh access dashboard</button></form></div>
      <div class="audit-grid">
        <div class="audit-card"><small>Users</small><strong>{html.escape(str(readiness.get("summary", {}).get("user_count", 0)))}</strong><small>local identities</small></div>
        <div class="audit-card"><small>Members</small><strong>{html.escape(str(readiness.get("summary", {}).get("active_member_count", 0)))}</strong><small>active project members</small></div>
        <div class="audit-card"><small>Tokens</small><strong>{html.escape(str(readiness.get("summary", {}).get("active_token_count", 0)))}</strong><small>active scoped tokens</small></div>
        <div class="audit-card"><small>Audit events</small><strong>{html.escape(str(readiness.get("summary", {}).get("audit_event_count", 0)))}</strong><small>permission decisions</small></div>
        <div class="audit-card"><small>Login session</small><strong>local scaffold</strong><small>OIDC/Vault production login remains on /v5/production-readiness</small></div>
        <div class="audit-card"><small>Token rotation</small><strong>{html.escape('required' if lifecycle.get('rotation_required') else 'clean')}</strong><small>{html.escape(str(lifecycle.get('scoped_token_count', 0)))} scoped token(s)</small></div>
      </div>
      <details open><summary>Admin actions required</summary><table><thead><tr><th>Priority</th><th>Action</th></tr></thead><tbody>{action_rows or '<tr><td colspan="2">No access action required.</td></tr>'}</tbody></table></details>
      <details open><summary>Productization gaps</summary><table><thead><tr><th>Priority</th><th>Gap</th><th>Next step</th></tr></thead><tbody>{gap_rows or '<tr><td colspan="3">No platform access gap detected by this dashboard.</td></tr>'}</tbody></table></details>
      <div class="audit-grid">
        <form class="mini-form" method="post" action="/v5/access/user">
          <label>User id</label><input name="user_id" type="text" placeholder="pi_user">
          <label>Display name</label><input name="display_name" type="text" placeholder="Professor">
          <button type="submit">Create user</button>
        </form>
        <form class="mini-form" method="post" action="/v5/access/member">
          <label>User id</label><input name="user_id" type="text" placeholder="pi_user">
          <label>Role</label><select name="role"><option>owner</option><option>admin</option><option>operator</option><option>reviewer</option><option>viewer</option></select>
          <label>Status</label><select name="status"><option>active</option><option>inactive</option></select>
          <button type="submit">Set member role</button>
        </form>
        <form class="mini-form" method="post" action="/v5/access/token">
          <label>User id</label><input name="user_id" type="text" placeholder="pi_user">
          <label>TTL minutes</label><input name="ttl_minutes" type="number" value="1440">
          <label>Scopes optional</label><input name="scopes" type="text" placeholder="project:read,audit:read">
          <button type="submit">Issue token</button>
        </form>
        <form class="mini-form" method="post" action="/v5/access/token/revoke">
          <label>Token id</label><input name="token_id" type="text" placeholder="v5tok_...">
          <label>Reason</label><input name="reason" type="text" placeholder="rotated">
          <button class="ghost" type="submit">Revoke token</button>
        </form>
      </div>
      <details open><summary>Users</summary><table><thead><tr><th>User</th><th>Name</th><th>Status</th><th>Created</th></tr></thead><tbody>{user_rows or '<tr><td colspan="4">No users.</td></tr>'}</tbody></table></details>
      <details open><summary>Members</summary><table><thead><tr><th>User</th><th>Role</th><th>Status</th><th>Created</th></tr></thead><tbody>{member_rows or '<tr><td colspan="4">No members.</td></tr>'}</tbody></table></details>
      <details open><summary>Token lifecycle</summary><table><thead><tr><th>Token</th><th>User</th><th>Role</th><th>Status</th><th>Expires</th><th>Scopes</th></tr></thead><tbody>{token_rows or '<tr><td colspan="6">No tokens.</td></tr>'}</tbody></table></details>
      <details open><summary>Token lifecycle summary</summary><table><thead><tr><th>Status</th><th>Count</th></tr></thead><tbody>{lifecycle_rows or '<tr><td colspan="2">No token lifecycle records.</td></tr>'}</tbody></table></details>
      <details open><summary>Role coverage and permissions</summary><table><thead><tr><th>Role</th><th>Coverage</th><th>Active members</th><th>Permissions</th></tr></thead><tbody>{role_coverage_rows}</tbody></table></details>
      <details><summary>Admin capability map</summary><table><thead><tr><th>Capability</th><th>Status</th><th>Entrypoint</th></tr></thead><tbody>{capability_rows}</tbody></table></details>
      <details><summary>Role permissions</summary><table><thead><tr><th>Role</th><th>Permissions</th></tr></thead><tbody>{role_rows}</tbody></table></details>
      <details><summary>Access audit</summary><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Status</th><th>Reason</th></tr></thead><tbody>{audit_rows or '<tr><td colspan="5">No audit events.</td></tr>'}</tbody></table></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="Access Control")


def _v5_storage_page(project_dir: Path) -> bytes:
    status = _read_json(project_dir / "v5" / "platform" / "backend_primary_status.json", {})
    if not status:
        status = build_backend_primary_status(project_dir)
    from .storage_migration import load_demo_slim_storage_manifest, load_storage_migration_plan

    migration = load_storage_migration_plan(project_dir)
    slim = load_demo_slim_storage_manifest(project_dir)
    finding_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('root', ''))}</td>"
        f"<td>{html.escape(str(row.get('file_count', 0)))}</td>"
        f"<td>{html.escape(str(row.get('artifact_store_registered_count', 0)))}</td>"
        f"<td>{html.escape(str(row.get('unregistered_count', 0)))}</td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        "</tr>"
        for row in status.get("legacy_writer_findings", [])
    )
    migration_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('relative_path', ''))}</code></td>"
        f"<td>{html.escape(row.get('artifact_type', ''))}</td>"
        f"<td>{html.escape(str(row.get('size_bytes', 0)))}</td>"
        f"<td>{html.escape(row.get('root', ''))}</td>"
        "</tr>"
        for row in migration.get("missing_artifacts", [])[:30]
    )
    history_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('generated_at', ''))}</td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(str(row.get('migrated_artifact_count', 0)))}</td>"
        f"<td>{html.escape(str(row.get('failed_artifact_count', 0)))}</td>"
        f"<td>{html.escape(row.get('evidence_repository_status', ''))}</td>"
        "</tr>"
        for row in migration.get("history_summary", {}).get("recent_batches", [])
    )
    gap_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('severity', ''))}</td>"
        f"<td><code>{html.escape(row.get('gap_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('meaning', ''))}</td>"
        "</tr>"
        for row in migration.get("primary_path_gaps", [])
    )
    action_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('priority', ''))}</td>"
        f"<td>{html.escape(row.get('action', ''))}</td>"
        "</tr>"
        for row in migration.get("actions", [])
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Production Storage</h1>
      <p class="muted">Overall: <code>{html.escape(status.get("overall_status", ""))}</code> · Active backends: <code>{html.escape(json.dumps(status.get("active_backends", {}), ensure_ascii=False))}</code> · View mode: <code>cached</code></p>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/backend-writes">Backend writes</a><form class="mini-form" method="post" action="/v5/storage/refresh"><button type="submit">Refresh storage status</button></form><form class="mini-form" method="post" action="/v5/storage/demo-slim"><button class="ghost" type="submit">Build demo slim storage</button></form><form class="mini-form" method="post" action="/v5/storage/migrate"><button class="ghost" type="submit">Migrate legacy outputs</button></form></div>
      <div class="audit-grid">
        <div class="audit-card"><small>Evidence DB</small><strong>{html.escape(status.get("evidence_repository", {}).get("backend", ""))}</strong><small>{html.escape(status.get("evidence_repository", {}).get("status", ""))}</small></div>
        <div class="audit-card"><small>ArtifactStore</small><strong>{html.escape(str(status.get("object_store", {}).get("artifact_store_count", 0)))}</strong><small>registered artifacts</small></div>
        <div class="audit-card"><small>Object URI</small><strong>{html.escape(str(status.get("object_store", {}).get("object_uri_count", 0)))}</strong><small>MinIO/S3 references</small></div>
        <div class="audit-card"><small>Backend writes</small><strong>{html.escape(str(status.get("backend_writer", {}).get("write_count", 0)))}</strong><small>JSON/artifact writer events</small></div>
      </div>
      <details open><summary>Legacy writer migration coverage</summary><table><thead><tr><th>Root</th><th>Files</th><th>Registered</th><th>Remaining</th><th>Status</th></tr></thead><tbody>{finding_rows or '<tr><td colspan="5">No local output roots found.</td></tr>'}</tbody></table></details>
      <details open><summary>Storage migration plan</summary>
        <p class="muted">Status: <code>{html.escape(migration.get("status", ""))}</code> · Missing artifacts: <code>{html.escape(str(migration.get("artifact_store_missing_count", 0)))}</code> · SQLite evidence rows: <code>{html.escape(str(migration.get("sqlite_evidence_row_count", 0)))}</code> · Progress: <code>{html.escape(str(migration.get("migration_progress", {}).get("percent_complete", 0)))}%</code> · Source: <code>{html.escape(migration.get("cache_policy", {}).get("source_ref", "fresh_scan"))}</code></p>
        <table><thead><tr><th>Priority</th><th>Action</th></tr></thead><tbody>{action_rows}</tbody></table>
        <table><thead><tr><th>Severity</th><th>Primary path gap</th><th>Meaning</th></tr></thead><tbody>{gap_rows}</tbody></table>
        <table><thead><tr><th>Path</th><th>Type</th><th>Bytes</th><th>Root</th></tr></thead><tbody>{migration_rows or '<tr><td colspan="4">No missing artifacts in scanned roots.</td></tr>'}</tbody></table>
      </details>
      <details open><summary>Migration batch history</summary><p class="muted">Batches: <code>{html.escape(str(migration.get("history_summary", {}).get("batch_count", 0)))}</code> · Total migrated: <code>{html.escape(str(migration.get("history_summary", {}).get("total_migrated_artifacts", 0)))}</code> · Failed: <code>{html.escape(str(migration.get("history_summary", {}).get("total_failed_artifacts", 0)))}</code></p><table><thead><tr><th>Time</th><th>Status</th><th>Migrated</th><th>Failed</th><th>Evidence sync</th></tr></thead><tbody>{history_rows or '<tr><td colspan="5">No migration batch has run.</td></tr>'}</tbody></table></details>
      <details open><summary>Professor demo slim storage</summary>
        <p class="muted">Status: <code>{html.escape(slim.get("status", "not_built"))}</code> · Effective artifacts: <code>{html.escape(str(slim.get("effective_artifact_count", 0)))}</code> · Missing effective: <code>{html.escape(str(slim.get("effective_missing_count", 0)))}</code> · Excluded historical: <code>{html.escape(str(slim.get("excluded_historical_legacy_count", 0)))}</code></p>
        <p class="muted">Manifest: <code>{html.escape(slim.get("manifest_ref", "v5/platform/demo_slim_storage_manifest.json"))}</code></p>
      </details>
      <details><summary>Raw status JSON</summary><pre>{html.escape(json.dumps(status, indent=2, ensure_ascii=False))}</pre></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="Production Storage")


def _v5_audit_page(project_dir: Path, source: str = "all", status: str = "", actor: str = "") -> bytes:
    query = query_platform_audit(project_dir, source=source or "all", status=status, actor=actor, limit=100)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('timestamp', ''))}</td>"
        f"<td>{html.escape(row.get('source', ''))}</td>"
        f"<td>{html.escape(row.get('actor', ''))}</td>"
        f"<td>{html.escape(row.get('action', ''))}</td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('reason', '')[:220])}</td>"
        "</tr>"
        for row in query.get("events", [])
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Platform Audit</h1>
      <p class="muted">Matched events: <code>{html.escape(str(query.get("match_count", 0)))}</code> · Sources: access / service / LLM / backend / artifact.</p>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/access">Access</a><a class="button ghost" href="/v5/storage">Storage</a></div>
      <form class="mini-form" method="get" action="/v5/audit">
        <label>Source</label><select name="source"><option value="all">all</option><option value="access">access</option><option value="service">service</option><option value="llm">llm</option><option value="backend">backend</option><option value="artifact">artifact</option></select>
        <label>Status</label><input name="status" type="text" value="{html.escape(status)}" placeholder="allowed / denied / success / failed / PASS">
        <label>Actor / caller</label><input name="actor" type="text" value="{html.escape(actor)}" placeholder="local_owner">
        <button type="submit">Search audit</button>
      </form>
      <table><thead><tr><th>Time</th><th>Source</th><th>Actor</th><th>Action</th><th>Status</th><th>Reason</th></tr></thead><tbody>{rows or '<tr><td colspan="6">No audit events matched.</td></tr>'}</tbody></table>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="Platform Audit")


def _v5_cache_page(project_dir: Path) -> bytes:
    manifest = build_data_cache_manifest(project_dir)
    root_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('label', ''))}</td>"
        f"<td><code>{html.escape(row.get('path', ''))}</code></td>"
        f"<td>{html.escape(str(row.get('file_count', 0)))}</td>"
        f"<td>{html.escape(str(row.get('size_bytes', 0)))}</td>"
        f"<td>{html.escape(row.get('newest_mtime', ''))}</td>"
        "</tr>"
        for row in manifest.get("roots", [])
    )
    missing_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('relative_path', ''))}</code></td>"
        f"<td><code>{html.escape(row.get('object_uri', ''))}</code></td>"
        f"<td>{html.escape('; '.join(row.get('recovery', {}).get('steps', [])))}</td>"
        "</tr>"
        for row in manifest.get("missing_artifacts", [])
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Data Cache</h1>
      <p class="muted">Total cache size: <code>{html.escape(str(manifest.get("total_size_bytes", 0)))}</code> bytes · Missing artifacts: <code>{html.escape(str(manifest.get("missing_artifact_count", 0)))}</code>.</p>
      <div class="actions"><a class="button ghost" href="/">Back</a><form class="mini-form" method="post" action="/v5/cache/refresh"><button type="submit">Refresh cache manifest</button></form></div>
      <div class="actions">
        <form class="mini-form" method="post" action="/v5/cache/cleanup"><input type="hidden" name="target" value="last_download_manifest"><input type="hidden" name="dry_run" value="1"><button class="ghost" type="submit">Dry-run clear download manifests</button></form>
        <form class="mini-form" method="post" action="/v5/cache/cleanup"><input type="hidden" name="target" value="external_mock_runs"><input type="hidden" name="dry_run" value="1"><button class="ghost" type="submit">Dry-run clear mock runs</button></form>
      </div>
      <details open><summary>Cache roots</summary><table><thead><tr><th>Root</th><th>Path</th><th>Files</th><th>Bytes</th><th>Newest</th></tr></thead><tbody>{root_rows}</tbody></table></details>
      <details open><summary>Missing / recoverable artifacts</summary><table><thead><tr><th>Path</th><th>Object URI</th><th>Recovery</th></tr></thead><tbody>{missing_rows or '<tr><td colspan="3">No missing artifact recovery items.</td></tr>'}</tbody></table></details>
      <details><summary>Cleanup policy</summary><pre>{html.escape(json.dumps(manifest.get("cleanup_policy", {}), indent=2, ensure_ascii=False))}</pre></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="Data Cache")


def _v5_platform_readiness_page(project_dir: Path) -> bytes:
    readiness = build_platform_p1_readiness(project_dir)
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('check_id', ''))}</code></td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        f"<td>{html.escape(row.get('recovery', ''))}</td>"
        "</tr>"
        for row in readiness.get("checks", [])
    )
    page_links = "".join(
        f'<a class="button ghost" href="{html.escape(url)}">{html.escape(label.replace("_", " ").title())}</a>'
        for label, url in readiness.get("pages", {}).items()
    )
    remaining = "".join(f"<li>{html.escape(item)}</li>" for item in readiness.get("remaining_work", []))
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>P1 Platform Readiness</h1>
      <p class="muted">Status: <code>{html.escape(readiness.get("status", ""))}</code> · Manifest: <code>v5/platform/p1_readiness.json</code></p>
      <div class="actions"><a class="button ghost" href="/">Back</a>{page_links}</div>
      <table><thead><tr><th>Area</th><th>Status</th><th>What is available</th><th>Next action</th></tr></thead><tbody>{rows}</tbody></table>
      <details open><summary>Remaining work</summary><ul>{remaining or '<li>No P1 readiness blocker detected.</li>'}</ul></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="P1 Platform Readiness")


def _v5_platform_p2_readiness_page(project_dir: Path) -> bytes:
    readiness = build_platform_p2_readiness(project_dir)
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('check_id', ''))}</code></td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        f"<td>{html.escape(row.get('recovery', ''))}</td>"
        f"<td><pre>{html.escape(json.dumps(row.get('details', {}), indent=2, ensure_ascii=False))}</pre></td>"
        "</tr>"
        for row in readiness.get("checks", [])
    )
    page_links = "".join(
        f'<a class="button ghost" href="{html.escape(url)}">{html.escape(label.replace("_", " ").title())}</a>'
        for label, url in readiness.get("pages", {}).items()
    )
    blockers = "".join(f"<li>{html.escape(item)}</li>" for item in readiness.get("production_blockers", []))
    remaining = "".join(f"<li>{html.escape(item)}</li>" for item in readiness.get("remaining_work", []))
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>P2 Platform Readiness</h1>
      <p class="muted">Status: <code>{html.escape(readiness.get("status", ""))}</code> · Manifest: <code>v5/platform/p2_readiness.json</code></p>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/platform-readiness">P1 readiness</a>{page_links}</div>
      <table><thead><tr><th>Area</th><th>Status</th><th>Current capability</th><th>Next action</th><th>Details</th></tr></thead><tbody>{rows}</tbody></table>
      <details open><summary>Production blockers</summary><ul>{blockers or '<li>No P2 production blocker detected by this manifest.</li>'}</ul></details>
      <details open><summary>Remaining work</summary><ul>{remaining or '<li>No remaining P2 action detected.</li>'}</ul></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="P2 Platform Readiness")


def _v5_production_readiness_page(project_dir: Path) -> bytes:
    readiness = build_platform_production_readiness(project_dir)
    checks_by_id = {row.get("check_id", ""): row for row in readiness.get("checks", [])}
    prod_steps = [
        ("01", "身份与密钥", "OIDC/Vault/session 验证替代本地 token fallback。", checks_by_id.get("formal_auth_oidc_vault_sessions", {}).get("status", "REVIEW")),
        ("02", "存储主路径", "EvidenceRepository 与 ArtifactStore 走 PostgreSQL/MinIO。", checks_by_id.get("postgres_minio_primary_only", {}).get("status", "REVIEW")),
        ("03", "执行平面", "Nextflow 已验收；Codex Worker 需要可调用 subprocess/remote worker。", checks_by_id.get("codex_worker_large_sample_validation", {}).get("status", "REVIEW")),
        ("04", "安装发布", "Windows 安装器、离线缓存、干净机 smoke。", checks_by_id.get("windows_gui_installer_release", {}).get("status", "REVIEW")),
    ]
    prod_guide_rows = "".join(
        "<div class=\"guide-step\">"
        f"<span class=\"guide-index\">{html.escape(index)}</span>"
        f"<div><strong>{html.escape(title)}</strong><small>{html.escape(desc)}</small><div class=\"status-line\"><span class=\"pill {html.escape(status.lower())}\">{html.escape(status)}</span></div></div>"
        "</div>"
        for index, title, desc, status in prod_steps
    )
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('check_id', ''))}</code></td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        f"<td>{html.escape(row.get('recovery', ''))}</td>"
        f"<td><pre>{html.escape(json.dumps(row.get('details', {}), indent=2, ensure_ascii=False))}</pre></td>"
        "</tr>"
        for row in readiness.get("checks", [])
    )
    page_links = "".join(
        f'<a class="button ghost" href="{html.escape(url)}">{html.escape(label.replace("_", " ").title())}</a>'
        for label, url in readiness.get("pages", {}).items()
    )
    blockers = "".join(f"<li>{html.escape(item)}</li>" for item in readiness.get("production_blockers", []))
    scope = "".join(f"<li>{html.escape(item)}</li>" for item in readiness.get("scope", []))
    body = f"""
    <main class="app-shell"><section class="app-section">
      <div class="page-head">
        <div>
          <div class="page-kicker">Production Control Plane</div>
          <h1>v5 Production Readiness</h1>
          <p class="page-meta">Status: <code>{html.escape(readiness.get("status", ""))}</code> · Manifest: <code>v5/platform/production_readiness.json</code></p>
        </div>
      </div>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/platform-p2-readiness">P2 readiness</a><a class="button ghost" href="/v5/release-acceptance">Release acceptance</a>{page_links}</div>
      <div class="guide-panel">
        <div class="guide-summary">
          <small>生产化状态</small>
          <strong>{html.escape(readiness.get("status", ""))}</strong>
          <small>{html.escape(str(len(readiness.get("production_blockers", []))))} 个生产阻塞项仍需验收；已通过的能力保持可追溯 manifest。</small>
        </div>
        <div class="guide-steps">{prod_guide_rows}</div>
      </div>
      <details open><summary>Production scope</summary><ul>{scope}</ul></details>
      <table><thead><tr><th>Area</th><th>Status</th><th>Production requirement</th><th>Next action</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table>
      <details open><summary>Production blockers</summary><ul>{blockers or '<li>No production blocker detected by this manifest.</li>'}</ul></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="v5 Production Readiness")


def _v5_release_acceptance_page(project_dir: Path) -> bytes:
    manifest = build_release_acceptance_manifest(project_dir, question_count=50)
    blockers = [row for row in manifest.get("checks", []) if row.get("status") != "PASS"]
    checks_by_id = {row.get("check_id", ""): row for row in manifest.get("checks", [])}
    def _gate_status(check_id: str) -> str:
        return checks_by_id.get(check_id, {}).get("status", "REVIEW")
    guide_steps = [
        ("01", "基础回归", "quick / full / e2e 测试已固定为发布前门槛。", _gate_status("quick_regression") if all(_gate_status(item) == "PASS" for item in ["quick_regression", "full_regression", "e2e_regression"]) else "REVIEW"),
        ("02", "真实问题长测", "50 个真实研究方向验证资源发现、LLM、报告导出稳定性。", _gate_status("real_question_longrun")),
        ("03", "真实数据主路径", "GEO 已通过；SRA/cellxgene 需真实矩阵 adapter 验收。", _gate_status("real_data_main_path")),
        ("04", "安装交付", "干净 Windows/VM 安装、启动、停止、卸载仍需记录。", _gate_status("clean_windows_installer_smoke")),
    ]
    guide_rows = "".join(
        "<div class=\"guide-step\">"
        f"<span class=\"guide-index\">{html.escape(index)}</span>"
        f"<div><strong>{html.escape(title)}</strong><small>{html.escape(desc)}</small><div class=\"status-line\"><span class=\"pill {html.escape(status.lower())}\">{html.escape(status)}</span></div></div>"
        "</div>"
        for index, title, desc, status in guide_steps
    )
    blocker_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('check_id', ''))}</code></td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('recovery', '') or 'No action required.')}</td>"
        "</tr>"
        for row in blockers
    )
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('check_id', ''))}</code></td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td><code>{html.escape(row.get('ref', ''))}</code></td>"
        f"<td>{html.escape(row.get('recovery', ''))}</td>"
        "</tr>"
        for row in manifest.get("checks", [])
    )
    scenario_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('scenario_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('category', ''))}</td>"
        f"<td>{html.escape(row.get('expected_behavior', '') or ', '.join(row.get('expected_artifacts', []) or row.get('checks', [])))}</td>"
        "</tr>"
        for row in manifest.get("test_matrix", {}).get("scenarios", [])
    )
    command_rows = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td><code>{html.escape(command)}</code></td>"
        "</tr>"
        for name, command in manifest.get("commands", {}).items()
    )
    data_matrix_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('source', ''))}</code><small>{html.escape(row.get('latest_accession', ''))}</small></td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('required_path', ''))}</td>"
        f"<td>{html.escape(row.get('next_step', ''))}</td>"
        "</tr>"
        for row in manifest.get("real_data_validation_matrix", {}).get("rows", [])
    )
    scripts = _read_json(project_dir / "v5" / "platform" / "pre_release_scripts.json", {})
    body = f"""
    <main class="app-shell"><section class="app-section">
      <div class="page-head">
        <div>
          <div class="page-kicker">v5 Delivery Gate</div>
          <h1>Release Acceptance</h1>
          <p class="page-meta">Status: <code>{html.escape(manifest.get("status", ""))}</code> · Manifest: <code>v5/platform/release_acceptance.json</code> · Matrix: <code>{html.escape(manifest.get("test_matrix_ref", ""))}</code></p>
        </div>
      </div>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/production-readiness">Production readiness</a><form class="mini-form" method="post" action="/v5/release-acceptance/refresh"><button type="submit">Refresh acceptance</button></form></div>
      <div class="guide-panel">
        <div class="guide-summary">
          <small>当前交付状态</small>
          <strong>{html.escape(manifest.get("status", ""))}</strong>
          <small>{html.escape(str(len(blockers)))} 个阻塞项需要在最终交付前处理。GEO 主路径已通过，SRA/cellxgene 和干净机安装仍需验收记录。</small>
        </div>
        <div class="guide-steps">{guide_rows}</div>
      </div>
      <div class="audit-grid">
        <div class="audit-card"><small>Release gate</small><strong>{html.escape(manifest.get("status", ""))}</strong><small>truthful pre-delivery status</small></div>
        <div class="audit-card"><small>阻塞项</small><strong>{html.escape(str(len(blockers)))}</strong><small>最终交付前必须处理</small></div>
        <div class="audit-card"><small>Question matrix</small><strong>{html.escape(str(manifest.get("test_matrix", {}).get("question_count", 0)))}</strong><small>real-question validation target</small></div>
        <div class="audit-card"><small>Suites</small><strong>{html.escape(str(len(manifest.get("test_matrix", {}).get("suites", {}))))}</strong><small>quick / full / e2e</small></div>
        <div class="audit-card"><small>Scenarios</small><strong>{html.escape(str(len(manifest.get("test_matrix", {}).get("scenarios", []))))}</strong><small>failure and report gates</small></div>
        <div class="audit-card"><small>Pre-release script</small><strong>{html.escape("ready" if scripts.get("scripts") else "missing")}</strong><small>{html.escape(Path(scripts.get("scripts", {}).get("powershell", "")).name)}</small></div>
      </div>
      <details open><summary>交付前阻塞项</summary><table><thead><tr><th>Gate</th><th>Status</th><th>Required action</th></tr></thead><tbody>{blocker_rows or '<tr><td colspan="3">No blocker detected.</td></tr>'}</tbody></table></details>
      <table><thead><tr><th>Gate</th><th>Status</th><th>Evidence ref</th><th>Required action</th></tr></thead><tbody>{rows}</tbody></table>
      <details open><summary>真实数据主路径验证矩阵</summary><table><thead><tr><th>Source</th><th>Status</th><th>Required path</th><th>Next step</th></tr></thead><tbody>{data_matrix_rows}</tbody></table></details>
      <details open><summary>发布前固定测试矩阵</summary><table><thead><tr><th>Scenario</th><th>Category</th><th>Expected behavior</th></tr></thead><tbody>{scenario_rows}</tbody></table></details>
      <details open><summary>验收命令</summary><table><thead><tr><th>Step</th><th>Command</th></tr></thead><tbody>{command_rows}</tbody></table></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="Release Acceptance")


def _v5_backend_writes_page(project_dir: Path) -> bytes:
    from .artifact_store import artifact_store_summary, load_artifact_store
    from .canonical.backend_writer import backend_write_summary, load_backend_writes

    writes = load_backend_writes(project_dir)
    summary = backend_write_summary(project_dir)
    store_rows = load_artifact_store(project_dir)
    store_summary = artifact_store_summary(project_dir)
    write_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('relative_path', ''))}</code><small>{html.escape(row.get('artifact_type', ''))}</small></td>"
        f"<td>{html.escape(row.get('producer', ''))}</td>"
        f"<td>{html.escape(row.get('primary_backend', ''))}</td>"
        f"<td>{html.escape((row.get('primary_write', {}) or {}).get('status', ''))}</td>"
        f"<td><code>{html.escape((row.get('primary_write', {}) or {}).get('uri', ''))}</code></td>"
        f"<td><code>{html.escape(str(row.get('sha256', ''))[:12])}</code></td>"
        "</tr>"
        for row in writes[-80:]
    )
    store_html = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('artifact_store_id', ''))}</code><small>{html.escape(row.get('artifact_type', ''))}</small></td>"
        f"<td><code>{html.escape(row.get('relative_path', ''))}</code></td>"
        f"<td>{html.escape(row.get('object_backend', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td><code>{html.escape(row.get('object_uri', ''))}</code></td>"
        f"<td>{html.escape(str(row.get('size_bytes', 0)))}</td>"
        "</tr>"
        for row in store_rows[-80:]
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Backend Write Details</h1>
      <p class="muted">Backend writes: {html.escape(str(summary.get("write_count", 0)))} · MinIO pass: {html.escape(str(summary.get("minio_primary_pass_count", 0)))} · ArtifactStore: {html.escape(str(store_summary.get("artifact_store_count", 0)))} · Object URI: {html.escape(str(store_summary.get("object_uri_count", 0)))}</p>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/artifacts">Artifacts</a></div>
      <details open><summary>Backend writer records</summary><table><thead><tr><th>Path</th><th>Producer</th><th>Backend</th><th>Status</th><th>URI</th><th>SHA256</th></tr></thead><tbody>{write_rows or '<tr><td colspan="6">No backend writes.</td></tr>'}</tbody></table></details>
      <details open><summary>ArtifactStore records</summary><table><thead><tr><th>Store id</th><th>Path</th><th>Backend</th><th>Status</th><th>Object URI</th><th>Bytes</th></tr></thead><tbody>{store_html or '<tr><td colspan="6">No ArtifactStore records.</td></tr>'}</tbody></table></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="Backend Write Details")


def _v5_artifacts_page(project_dir: Path, selected_path: str = "") -> bytes:
    from .artifact_store import build_download_manifest, load_artifact_store, verify_artifact
    from .canonical.backend_access import load_artifact_registry_preferred

    registry = load_artifact_registry_preferred(project_dir).get("artifacts", [])
    store = load_artifact_store(project_dir)
    store_by_path = {row.get("relative_path", ""): row for row in store}
    rows = []
    for row in registry[-100:]:
        path = row.get("path", "")
        store_row = store_by_path.get(path, {})
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(row.get('artifact_id', ''))}</code><small>{html.escape(row.get('artifact_type', ''))}</small></td>"
            f"<td><code>{html.escape(path)}</code></td>"
            f"<td>{html.escape(row.get('qc_status', ''))}</td>"
            f"<td>{html.escape(row.get('source_backend', ''))}</td>"
            f"<td><code>{html.escape(store_row.get('object_uri', row.get('object_store_ref', '')))}</code></td>"
            f"<td><a class=\"button ghost small-button\" href=\"/v5/artifacts?path={urllib.parse.quote(path)}\">Verify</a></td>"
            "</tr>"
        )
    selected_html = ""
    if selected_path:
        verification = verify_artifact(project_dir, relative_path=selected_path)
        manifest = build_download_manifest(project_dir, relative_path=selected_path) if verification.get("status") != "MISSING_RECORD" else {}
        selected_html = f"<details open><summary>Selected artifact verification</summary><pre>{html.escape(json.dumps({'verification': verification, 'download_manifest': manifest}, indent=2, ensure_ascii=False))}</pre></details>"
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Artifact Drill-down</h1>
      <p class="muted">Artifact Registry + ArtifactStore joined view. Each row can be verified against local cache and object store checksum.</p>
      <div class="actions"><a class="button ghost" href="/">Back</a><a class="button ghost" href="/v5/backend-writes">Backend writes</a></div>
      {selected_html}
      <table><thead><tr><th>Artifact</th><th>Path</th><th>QC</th><th>Backend</th><th>Object URI</th><th>Action</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6">No artifacts.</td></tr>'}</tbody></table>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="Artifact Drill-down")


def _v5_evidence_claims_page(project_dir: Path) -> bytes:
    evidence_query = _read_json(project_dir / "v4" / "evidence_db_last_query.json", {})
    trace_query = _read_json(project_dir / "v4" / "evidence_trace_last_query.json", {})
    reports = _find_json_objects(project_dir / "v5", ["*alignment*.json", "*claim*.json"])
    evidence_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('evidence_id', row.get('evidence_item_id', '')))}</code></td>"
        f"<td>{html.escape(row.get('entity_symbol', ''))}</td>"
        f"<td>{html.escape(row.get('evidence_type', ''))}</td>"
        f"<td>{html.escape(row.get('review_status', ''))}</td>"
        f"<td><a class=\"button ghost small-button\" href=\"/evidence-trace?evidence_id={urllib.parse.quote(row.get('evidence_id', row.get('evidence_item_id', '')))}\">Trace</a></td>"
        "</tr>"
        for row in evidence_query.get("items", [])
    )
    trace_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('evidence_id', row.get('evidence_item_id', '')))}</code></td>"
        f"<td>{html.escape(row.get('entity_symbol', ''))}</td>"
        f"<td>{html.escape(row.get('report_ref', ''))}</td>"
        f"<td>{html.escape(row.get('review_status', ''))}</td>"
        "</tr>"
        for row in trace_query.get("items", [])
    )
    report_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(path)}</code></td>"
        f"<td>{html.escape(obj.get('final_decision', obj.get('status', '')))}</td>"
        f"<td>{html.escape(str(len(obj.get('unsupported_claims', []))))}</td>"
        f"<td>{html.escape(str(len(obj.get('claim_ceiling_violations', []))))}</td>"
        f"<td>{html.escape('; '.join(str(item.get('claim_id', '')) for item in obj.get('unsupported_claims', [])[:4]))}</td>"
        "</tr>"
        for path, obj in reports[-50:]
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Evidence / Claim Drill-down</h1>
      <p class="muted">Shows current Evidence DB query, trace references, and canonical alignment/claim audit reports.</p>
      <div class="actions"><a class="button ghost" href="/">Back</a><form class="mini-form" method="post" action="/evidence-db/query"><input name="gene" placeholder="gene"><button type="submit">Query evidence</button></form></div>
      <details open><summary>Evidence items</summary><table><thead><tr><th>Evidence</th><th>Gene</th><th>Type</th><th>Review</th><th>Trace</th></tr></thead><tbody>{evidence_rows or '<tr><td colspan="5">Run an evidence query first.</td></tr>'}</tbody></table></details>
      <details open><summary>Report trace references</summary><table><thead><tr><th>Evidence</th><th>Gene</th><th>Report ref</th><th>Review</th></tr></thead><tbody>{trace_rows or '<tr><td colspan="4">No trace query yet.</td></tr>'}</tbody></table></details>
      <details open><summary>Claim alignment reports</summary><table><thead><tr><th>Report</th><th>Decision</th><th>Unsupported</th><th>Ceiling violations</th><th>Claims</th></tr></thead><tbody>{report_rows or '<tr><td colspan="5">No alignment reports found.</td></tr>'}</tbody></table></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="Evidence / Claim Drill-down")


def _v5_wetlab_page(project_dir: Path) -> bytes:
    from .canonical.wet_lab_protocol import build_wet_lab_sop_bundle, build_wet_lab_protocol_bundle, load_wet_lab_signoffs

    manifest = _read_json(project_dir / "v5" / "wet_lab_protocols" / "wet_lab_protocol_bundle.json", {})
    if not manifest:
        manifest = build_wet_lab_protocol_bundle(project_dir, max_protocols=5)
    sop_bundle = _read_json(project_dir / "v5" / "wet_lab_protocols" / "wet_lab_sop_bundle.json", {})
    if not sop_bundle and manifest.get("protocols"):
        sop_bundle = build_wet_lab_sop_bundle(project_dir, max_protocols=5)
    summary = manifest.get("signoff_summary", {})
    signoffs = load_wet_lab_signoffs(project_dir)
    signoff_by_protocol: dict[str, list[dict]] = {}
    for row in signoffs:
        signoff_by_protocol.setdefault(row.get("protocol_id", ""), []).append(row)
    protocol_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('protocol_id', ''))}</code><small>{html.escape(row.get('candidate_gene', ''))}</small></td>"
        f"<td>{html.escape(row.get('risk_grade', ''))}</td>"
        f"<td>{html.escape(row.get('human_review_gate', {}).get('status', ''))}<small>{html.escape(', '.join(row.get('approval_requirements', {}).get('allowed_approver_roles', [])))}</small></td>"
        f"<td>{html.escape(str(len(signoff_by_protocol.get(row.get('protocol_id', ''), []))))}</td>"
        f"<td><form class=\"mini-form\" method=\"post\" action=\"/v5/wetlab/signoff\"><input type=\"hidden\" name=\"protocol_id\" value=\"{html.escape(row.get('protocol_id', ''))}\"><input name=\"signer\" placeholder=\"reviewer\"><select name=\"decision\"><option>needs_revision</option><option>approved</option><option>rejected</option></select><input name=\"reason\" placeholder=\"reason required\"><button class=\"small-button ghost\" type=\"submit\">Sign off</button></form></td>"
        "</tr>"
        for row in manifest.get("protocols", [])
    )
    signoff_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('signoff_id', ''))}</code></td>"
        f"<td><code>{html.escape(row.get('protocol_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('signer', ''))}</td>"
        f"<td>{html.escape(row.get('decision', ''))}</td>"
        f"<td>{html.escape(row.get('reason', ''))}</td>"
        "</tr>"
        for row in signoffs[-50:]
    )
    sop_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('sop_id', ''))}</code><small>{html.escape(row.get('candidate_gene', ''))}</small></td>"
        f"<td>{html.escape(row.get('sop_status', ''))}</td>"
        f"<td>{html.escape(row.get('pre_execution_gate', {}).get('approval_state', ''))}</td>"
        f"<td>{html.escape(row.get('claim_boundary', ''))}</td>"
        f"<td>{html.escape(', '.join(row.get('recordkeeping_requirements', [])[:4]))}</td>"
        "</tr>"
        for row in sop_bundle.get("sops", [])
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>Wet-lab Protocol Signoff</h1>
      <p class="muted">Protocol drafts are review-required validation plans; they do not authorize wet-lab execution until signed off. Bundle: <code>v5/wet_lab_protocols/wet_lab_protocol_bundle.json</code></p>
      <div class="audit-grid">
        <div class="audit-card"><small>Status</small><strong>{html.escape(manifest.get('status', ''))}</strong><small>controlled validation plan</small></div>
        <div class="audit-card"><small>Protocols</small><strong>{html.escape(str(manifest.get('protocol_count', 0)))}</strong><small>drafts</small></div>
        <div class="audit-card"><small>Signed out</small><strong>{html.escape(str(summary.get('signed_out_count', 0)))}</strong><small>approved</small></div>
        <div class="audit-card"><small>Needs revision</small><strong>{html.escape(str(summary.get('needs_revision_count', 0)))}</strong><small>review queue</small></div>
        <div class="audit-card"><small>SOP bundle</small><strong>{html.escape(sop_bundle.get('status', 'not_built'))}</strong><small>{html.escape(str(sop_bundle.get('sop_count', 0)))} governance SOP(s)</small></div>
      </div>
      <div class="actions"><a class="button ghost" href="/">Back</a><form class="mini-form" method="post" action="/v5/wetlab/build"><button type="submit">Rebuild protocol drafts</button></form><form class="mini-form" method="post" action="/v5/wetlab/sop"><button class="ghost" type="submit">Build auditable SOP bundle</button></form></div>
      <details open><summary>Protocol drafts</summary><table><thead><tr><th>Protocol</th><th>Risk</th><th>Gate</th><th>Signoffs</th><th>Action</th></tr></thead><tbody>{protocol_rows or '<tr><td colspan="5">No candidate protocols.</td></tr>'}</tbody></table></details>
      <details open><summary>Auditable SOP governance</summary><table><thead><tr><th>SOP</th><th>Status</th><th>Approval</th><th>Claim boundary</th><th>Records required</th></tr></thead><tbody>{sop_rows or '<tr><td colspan="5">No SOP bundle built.</td></tr>'}</tbody></table></details>
      <details open><summary>Signoff history</summary><table><thead><tr><th>Signoff</th><th>Protocol</th><th>Signer</th><th>Decision</th><th>Reason</th></tr></thead><tbody>{signoff_rows or '<tr><td colspan="5">No signoffs.</td></tr>'}</tbody></table></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="Wet-lab Protocol Signoff")


def _v5_memory_page(project_dir: Path) -> bytes:
    from .canonical.memory_palace import build_memory_audit_dashboard

    dashboard = build_memory_audit_dashboard(project_dir, actor="web_ui")
    version_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('version_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('created_at', ''))}</td>"
        f"<td>{html.escape(row.get('actor', ''))}</td>"
        f"<td>{html.escape(row.get('reason', ''))}</td>"
        f"<td><code>{html.escape(str(row.get('memory_hash', ''))[:16])}</code></td>"
        "</tr>"
        for row in dashboard.get("versions", [])
    )
    event_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('created_at', ''))}</td>"
        f"<td>{html.escape(row.get('actor', ''))}</td>"
        f"<td><code>{html.escape(row.get('event_type', ''))}</code></td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        "</tr>"
        for row in dashboard.get("recent_events", [])[-20:]
    )
    diff_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('key', ''))}</code></td>"
        f"<td>{html.escape(row.get('change_type', ''))}</td>"
        f"<td><code>{html.escape(str(row.get('before_hash', ''))[:12])}</code></td>"
        f"<td><code>{html.escape(str(row.get('after_hash', ''))[:12])}</code></td>"
        "</tr>"
        for row in dashboard.get("last_diff", {}).get("changes", [])
    )
    drill = dashboard.get("rollback_drill", {})
    scenarios = dashboard.get("usage_scenarios", {})
    scenario_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('scenario_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('agent_id', ''))}</td>"
        f"<td>{html.escape(row.get('artifact_ref', ''))}</td>"
        "</tr>"
        for row in scenarios.get("scenarios", [])
    )
    body = f"""
    <main class="app-shell"><section class="app-section">
      <h1>长期 Memory 审计</h1>
      <p class="muted">Memory 只作为 Agent 上下文，不替代 Evidence DB。Manifest: <code>v5/memory_palace/memory_audit_dashboard.json</code></p>
      <div class="actions">
        <a class="button ghost" href="/">返回</a>
        <a class="button ghost" href="/v5/production-readiness">生产化验收</a>
        <form class="mini-form" method="post" action="/v5/memory/refresh"><button type="submit">刷新审计</button></form>
        <form class="mini-form" method="post" action="/v5/memory/rollback-drill"><button class="ghost" type="submit">执行回滚演练</button></form>
        <form class="mini-form" method="post" action="/v5/memory/scenarios"><button class="ghost" type="submit">运行使用场景演练</button></form>
      </div>
      <div class="audit-grid">
        <div class="audit-card"><small>状态</small><strong>{html.escape(dashboard.get("status", ""))}</strong><small>{html.escape(dashboard.get("active_version_id", ""))}</small></div>
        <div class="audit-card"><small>版本数</small><strong>{html.escape(str(dashboard.get("version_count", 0)))}</strong><small>版本 diff 可追溯</small></div>
        <div class="audit-card"><small>事件数</small><strong>{html.escape(str(dashboard.get("event_count", 0)))}</strong><small>写入 / 回滚 / 上下文构建</small></div>
        <div class="audit-card"><small>回滚演练</small><strong>{html.escape(drill.get("status", "not_recorded"))}</strong><small>{html.escape(drill.get("restored_to", ""))}</small></div>
        <div class="audit-card"><small>使用场景</small><strong>{html.escape(scenarios.get("status", "not_recorded"))}</strong><small>{html.escape(str(len(scenarios.get("agent_context_refs", []))))} agent contexts</small></div>
      </div>
      <details open><summary>版本列表</summary><table><thead><tr><th>Version</th><th>Created</th><th>Actor</th><th>Reason</th><th>Hash</th></tr></thead><tbody>{version_rows or '<tr><td colspan="5">No memory versions.</td></tr>'}</tbody></table></details>
      <details open><summary>最近 diff</summary><table><thead><tr><th>Key</th><th>Change</th><th>Before</th><th>After</th></tr></thead><tbody>{diff_rows or '<tr><td colspan="4">No diff yet. Update memory or run refresh after at least two versions.</td></tr>'}</tbody></table></details>
      <details open><summary>使用场景演练</summary><table><thead><tr><th>Scenario</th><th>Status</th><th>Agent</th><th>Artifact</th></tr></thead><tbody>{scenario_rows or '<tr><td colspan="4">No usage scenarios recorded.</td></tr>'}</tbody></table></details>
      <details open><summary>审计事件</summary><table><thead><tr><th>Time</th><th>Actor</th><th>Event</th><th>Message</th></tr></thead><tbody>{event_rows or '<tr><td colspan="4">No memory events.</td></tr>'}</tbody></table></details>
      <details><summary>科学证据边界</summary><p>{html.escape(dashboard.get("scientific_evidence_policy", ""))}</p></details>
    </section></main>
    """
    return _standalone_page(project_dir, body, title="长期 Memory 审计")


def _find_json_objects(root: Path, patterns: list[str]) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    if not root.exists():
        return rows
    for pattern in patterns:
        for path in sorted(root.rglob(pattern)):
            if path.is_file():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if isinstance(payload, dict):
                    rows.append((str(path.relative_to(root.parent)).replace("\\", "/"), payload))
    return rows


def _executor_manifest_panel(project_dir: Path) -> str:
    manifests = sorted((project_dir / "results").glob("bulk_deg_*/executor_manifest.json"))
    if not manifests:
        return '<p class="muted">No local executor manifests recorded yet.</p>'
    rows = []
    for path in manifests:
        data = _read_json(path, {})
        rows.append(
            "<tr>"
            f"<td>{html.escape(data.get('module_id', ''))}</td>"
            f"<td>{html.escape(data.get('backend', ''))}</td>"
            f"<td>{html.escape(data.get('status', ''))}</td>"
            f"<td><code>{html.escape(data.get('resume_key', ''))}</code></td>"
            f"<td>{html.escape(str(len(data.get('artifacts', []))))}</td>"
            f"<td><code>{html.escape(str(path.relative_to(project_dir)))}</code></td>"
            "</tr>"
        )
    return (
        "<details><summary>Local executor manifests</summary>"
        "<table><thead><tr><th>Module</th><th>Backend</th><th>Status</th><th>Resume key</th><th>Artifacts</th><th>Manifest</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></details>"
    )


def _nextflow_execution_panel(project_dir: Path) -> str:
    tasks = _read_json(project_dir / "workflows" / "target_discovery" / "tasks.json", {})
    run = _read_json(project_dir / "workflows" / "target_discovery" / "nextflow_run_manifest.json", {})
    artifacts = run.get("artifacts", [])
    artifact_rows = "".join(f"<li><code>{html.escape(path)}</code></li>" for path in artifacts[:10])
    failed_tasks = run.get("recovery", {}).get("failed_tasks", [])
    failed_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('process', ''))}</td>"
        f"<td>{html.escape(row.get('name', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('exit', ''))}</td>"
        "</tr>"
        for row in failed_tasks[:10]
    )
    return f"""
      <details open><summary>Nextflow execution</summary>
        <p class="muted">tasks: {html.escape(str(tasks.get("task_count", 0)))} · status: {html.escape(run.get("status", "not_run"))} · profile: {html.escape(run.get("profile", "local"))}</p>
        <form class="mini-form" method="post">
          <div class="actions">
            <button class="ghost" type="submit" formaction="/nextflow/tasks">Generate tasks.json</button>
            <button class="ghost" type="submit" formaction="/nextflow/run">Run local profile</button>
            <button class="ghost" name="resume" value="1" type="submit" formaction="/nextflow/run">Resume run</button>
          </div>
        </form>
        <small>Run manifest: <code>workflows/target_discovery/nextflow_run_manifest.json</code></small>
        <ul>{artifact_rows}</ul>
        <p class="muted">{html.escape(run.get("failure_reason", ""))}</p>
        <p class="muted">{html.escape(run.get("recovery", {}).get("recommendation", ""))}</p>
        <table><thead><tr><th>Process</th><th>Name</th><th>Status</th><th>Exit</th></tr></thead><tbody>{failed_rows}</tbody></table>
      </details>
    """


def _recovery_manifest_panel(project_dir: Path) -> str:
    manifest = _read_json(project_dir / "results" / "recovery" / "recovery_manifest.json", {"items": [], "open_count": 0, "retryable_count": 0})
    rows = []
    for row in manifest.get("items", [])[:12]:
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(row.get('stage', ''))}</code><small>{html.escape(row.get('item_id', ''))}</small></td>"
            f"<td>{html.escape(row.get('status', ''))}</td>"
            f"<td>{html.escape(row.get('reason', ''))}<small>{html.escape(row.get('suggested_action', ''))}</small></td>"
            f"<td>{html.escape(row.get('manual_correction', ''))}</td>"
            f"<td><code>{html.escape(row.get('source_artifact', ''))}</code></td>"
            "</tr>"
        )
    body = "".join(rows) or '<tr><td colspan="5">No recovery manifest yet.</td></tr>'
    return f"""
    <p class="muted">Open: {html.escape(str(manifest.get("open_count", 0)))} · Retryable: {html.escape(str(manifest.get("retryable_count", 0)))}</p>
    <form class="mini-form" method="post" action="/recovery/build">
      <div class="actions"><button type="submit">Rebuild recovery manifest</button></div>
    </form>
    <form class="mini-form" method="post" action="/database/retry">
      <label for="database_retry_sources">Database sources to retry</label>
      <input id="database_retry_sources" name="sources" type="text" placeholder="uniprot,hpa,reactome or leave empty for all">
      <label for="database_retry_genes">Genes</label>
      <input id="database_retry_genes" name="genes" type="text" placeholder="IL6,CXCL8,CCL2">
      <label for="database_retry_query">Disease/query</label>
      <input id="database_retry_query" name="query" type="text" value="type 2 diabetes skeletal muscle">
      <div class="actions"><button type="submit">Retry database validation</button></div>
    </form>
    <form class="mini-form upload-form" method="post" action="/fulltext/upload" enctype="multipart/form-data">
      <label for="fulltext_pdf">Full-text missing correction: upload PDF</label>
      <input id="fulltext_pdf" name="pdf_file" type="file" accept=".pdf,.txt">
      <label><input type="checkbox" name="ocr" value="1" checked> Enable OCR for scanned PDF</label>
      <div class="actions">
        <button type="submit">Upload and parse full text</button>
        <button class="ghost" type="submit" formaction="/fulltext/llm-extract">Run full-text LLM extraction</button>
      </div>
    </form>
    <table><thead><tr><th>Stage</th><th>Status</th><th>Reason / suggestion</th><th>Manual correction</th><th>Artifact</th></tr></thead><tbody>{body}</tbody></table>
    """


def _cell_type_evidence_panel(project_dir: Path) -> str:
    summary = _read_json(project_dir / "results" / "cell_type_evidence" / "cell_type_summary.json", {"row_count": 0, "gene_count": 0, "by_source": {}, "cell_type_by_gene": {}})
    rows = []
    for gene, items in list(summary.get("cell_type_by_gene", {}).items())[:10]:
        for item in items[:4]:
            rows.append(
                "<tr>"
                f"<td><code>{html.escape(gene)}</code></td>"
                f"<td>{html.escape(item.get('cell_type', ''))}</td>"
                f"<td>{html.escape(item.get('tissue', ''))}</td>"
                f"<td>{html.escape(item.get('evidence_source', ''))}</td>"
                f"<td>{html.escape(str(item.get('confidence', '')))}</td>"
                f"<td><code>{html.escape(item.get('artifact_path', ''))}</code></td>"
                "</tr>"
            )
    body = "".join(rows) or '<tr><td colspan="6">No cell-type evidence yet. Add HPA/PanglaoDB/CellMarker/scRNA/full-text inputs, then rebuild.</td></tr>'
    return f"""
    <p class="muted">Rows: {html.escape(str(summary.get("row_count", 0)))} · Genes: {html.escape(str(summary.get("gene_count", 0)))} · Sources: {html.escape(json.dumps(summary.get("by_source", {}), ensure_ascii=False))}</p>
    <form class="mini-form" method="post" action="/cell-type-evidence/build">
      <div class="actions">
        <button type="submit">Build cell-type evidence</button>
        <button class="ghost" type="submit" formaction="/cell-type-evidence/build-import">Build and import to Evidence DB</button>
      </div>
    </form>
    <table><thead><tr><th>Gene</th><th>Cell type</th><th>Tissue</th><th>Source</th><th>Confidence</th><th>Artifact</th></tr></thead><tbody>{body}</tbody></table>
    """


def _agent_roles_panel(project_dir: Path) -> str:
    manifest = _read_json(project_dir / "v4" / "agent_roles.json", {})
    roles = manifest.get("roles", [])
    if not roles:
        return '<p class="muted">No v4 Agent role manifest yet.</p>'
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('role_id', ''))}</td>"
        f"<td>{html.escape(row.get('worker', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('schema', ''))}</td>"
        f"<td><code>{html.escape(row.get('decision_id', ''))}</code></td>"
        "</tr>"
        for row in roles
    )
    return (
        "<details><summary>v4 Agent role split</summary>"
        "<table><thead><tr><th>Role</th><th>Worker</th><th>Status</th><th>Schema</th><th>Decision</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></details>"
    )


def _role_runs_panel(project_dir: Path) -> str:
    runs = load_role_runs(project_dir).get("runs", [])
    if not runs:
        return '<p class="muted">No v4 role runs recorded yet.</p>'
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(row.get('role_id', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('executor_backend', 'unknown'))}</td>"
        f"<td><code>{html.escape(row.get('role_run_id', ''))}</code></td>"
        f"<td><code>{html.escape(row.get('input_packet', ''))}</code></td>"
        f"<td><code>{html.escape(row.get('output_packet', ''))}</code></td>"
        f"<td><code>{html.escape(row.get('log', ''))}</code></td>"
        f"<td>{html.escape(row.get('failure_reason', ''))}</td>"
        "</tr>"
        for row in runs[-12:]
    )
    return (
        "<details open><summary>v4 Role runs</summary>"
        "<table><thead><tr><th>Role</th><th>Status</th><th>Backend</th><th>Run</th><th>Input</th><th>Output</th><th>Log</th><th>Failure</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></details>"
    )


def _orchestration_graph_panel(project_dir: Path) -> str:
    graph = _read_json(project_dir / "v4" / "typed_orchestration_graph.json", {})
    latest_runs = _latest_role_runs(project_dir)
    backend_config = _load_role_execution_backend_config(project_dir)
    if not graph:
        return """
        <details><summary>Typed orchestration graph</summary>
          <p class="muted">No typed orchestration graph recorded yet.</p>
          <form class="mini-form" method="post" action="/orchestration-graph">
            <div class="actions"><button class="ghost" type="submit">Build typed graph</button></div>
          </form>
        </details>
        """
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('role_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('schema', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(str(row.get('schema_valid', False)))}</td>"
        f"<td>{html.escape(row.get('selected_method', ''))}</td>"
        f"<td>{html.escape(backend_config.get(row.get('role_id', ''), 'auto'))}</td>"
        f"<td>{html.escape(_actual_backend(latest_runs.get(row.get('role_id', ''), {})))}</td>"
        f"<td>{html.escape(row.get('selected_model', ''))}</td>"
        f"<td>{_artifact_badges(latest_runs.get(row.get('role_id', ''), {}))}</td>"
        f"<td>{html.escape(_fallback_summary(latest_runs.get(row.get('role_id', ''), {}), row))}</td>"
        f"<td>{html.escape('; '.join(row.get('schema_errors', [])[:2]))}</td>"
        "</tr>"
        for row in graph.get("nodes", [])
    )
    return (
        _external_agent_panel(project_dir) +
        "<details open><summary>Typed orchestration graph</summary>"
        f'<p class="muted">graph: <code>{html.escape(graph.get("graph_hash", "")[:16])}</code> · strict schemas: {html.escape(str(len(graph.get("role_schemas", {}))))} · edges: {html.escape(str(len(graph.get("edges", []))))}</p>'
        '<form class="mini-form" method="post" action="/orchestration-graph"><div class="actions"><button class="ghost" type="submit">Refresh typed graph</button></div></form>'
        '<form class="mini-form" method="post" action="/orchestration-run">'
        '<input type="text" name="role_id" placeholder="optional role_id for partial rerun">'
        '<label><input type="checkbox" name="force" value="1"> Force rerun valid nodes</label>'
        '<div class="actions"><button class="ghost" type="submit">Run typed orchestration</button></div>'
        '</form>'
        "<table><thead><tr><th>Role</th><th>Schema</th><th>Status</th><th>Schema valid</th><th>Method</th><th>Configured backend</th><th>Actual backend</th><th>Model</th><th>Artifacts</th><th>Fallback</th><th>Errors</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></details>"
    )


def _external_agent_panel(project_dir: Path) -> str:
    latest = _read_json(project_dir / "external_agent_runs" / "bioinfo_agent_system" / "latest_adapter_run.json", {})
    if not latest:
        return """
        <details><summary>External agent planner</summary>
          <p class="muted">No external agent run recorded yet.</p>
        </details>
        """
    packets = _read_json(project_dir / "external_agent_runs" / "bioinfo_agent_system" / "codex_task_packets.json", {"packets": []}).get("packets", [])
    rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('task_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('name', ''))}</td>"
        f"<td>{html.escape(row.get('method_contract_id', ''))}</td>"
        f"<td>{html.escape(', '.join(row.get('dependencies', [])))}</td>"
        f"<td>{html.escape('; '.join(row.get('output_artifacts', [])))}</td>"
        "</tr>"
        for row in packets[:12]
    )
    native = latest.get("native", {})
    return (
        "<details open><summary>External agent planner</summary>"
        f'<p class="muted">adapter: <code>{html.escape(latest.get("adapter_id", ""))}</code> · mode: {html.escape(latest.get("mode", ""))} · status: {html.escape(latest.get("status", ""))}</p>'
        f'<p class="muted">question: {html.escape(latest.get("question", ""))}</p>'
        f'<p class="muted">native status: {html.escape(native.get("status", ""))} · reason: {html.escape(native.get("failure_reason", ""))}</p>'
        f'<p class="muted">plan: <code>{html.escape(latest.get("plan_ref", ""))}</code> · packets: <code>{html.escape(latest.get("codex_task_packets_ref", ""))}</code></p>'
        "<table><thead><tr><th>Task</th><th>Name</th><th>Method contract</th><th>Depends on</th><th>Outputs</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></details>"
    )


def _latest_role_runs(project_dir: Path) -> dict[str, dict]:
    latest = {}
    for row in load_role_runs(project_dir).get("runs", []):
        latest[row.get("role_id", "")] = row
    return latest


def _actual_backend(role_run: dict) -> str:
    return role_run.get("executor_backend") or role_run.get("execution_dispatch", {}).get("executor_backend", "pending")


def _artifact_badges(role_run: dict) -> str:
    artifacts = role_run.get("execution_dispatch", {}).get("artifacts", {})
    if not isinstance(artifacts, dict) or not artifacts:
        return '<span class="muted">none</span>'
    labels = []
    for key, value in list(artifacts.items())[:4]:
        if value:
            labels.append(f'<code>{html.escape(key)}:{html.escape(str(value).split("/")[-1])}</code>')
    return " ".join(labels) or '<span class="muted">none</span>'


def _fallback_summary(role_run: dict, graph_node: dict) -> str:
    dispatch = role_run.get("execution_dispatch", {})
    fallback = dispatch.get("llm_fallback", {}) if isinstance(dispatch, dict) else {}
    if fallback.get("triggered"):
        return "LLM fallback: " + str(fallback.get("failure_reason", ""))[:120]
    return graph_node.get("fallback_policy", {}).get("fallback_method", "")


def _mcp_gateway_panel(project_dir: Path) -> str:
    tools = _read_json(project_dir / "v4" / "mcp_tools.json", {}).get("tools", [])
    audit = _read_json(project_dir / "v4" / "mcp_call_audit_summary.json", {})
    policy = write_default_policy(project_dir)
    sessions = load_sessions(project_dir).get("sessions", [])
    tokens = load_token_registry(project_dir).get("tokens", [])
    readiness = _read_json(project_dir / "v4" / "mcp_external_auth_readiness.json", {})
    audit_query = _read_json(project_dir / "v4" / "mcp_audit_last_query.json", {})
    if not audit_query:
        audit_query = query_mcp_audit(project_dir, limit=20)
    if not tools and not audit and not sessions and not tokens:
        readiness = readiness or check_external_auth_readiness(project_dir)
        return (
            '<p class="muted">No local MCP gateway contract recorded yet.</p>'
            + _mcp_control_forms(policy)
            + _mcp_auth_readiness_html(readiness)
        )
    tool_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('tool_id', ''))}</code></td>"
        f"<td><code>{html.escape(row.get('required_scope', ''))}</code></td>"
        f"<td>{html.escape(row.get('risk', ''))}</td>"
        f"<td>{html.escape(str(row.get('requires_review', False)))}</td>"
        f"<td>{html.escape(row.get('output_schema', ''))}</td>"
        f"<td><code>{html.escape(row.get('contract_hash', '')[:12])}</code></td>"
        "</tr>"
        for row in tools
    )
    calls = audit.get("latest_calls", [])
    call_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('tool_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('actor', ''))}</td>"
        f"<td>{html.escape(row.get('principal', ''))}</td>"
        f"<td>{html.escape(row.get('role', ''))}</td>"
        f"<td>{html.escape(row.get('risk', ''))}</td>"
        f"<td>{html.escape(row.get('failure_reason', ''))}</td>"
        "</tr>"
        for row in calls[-8:]
    )
    session_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('session_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('client_id', ''))}</td>"
        f"<td>{html.escape(row.get('principal', ''))}</td>"
        f"<td>{html.escape(row.get('role', ''))}</td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('last_seen_at', ''))}</td>"
        "</tr>"
        for row in sessions[-12:]
    )
    token_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('token_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('principal', ''))}</td>"
        f"<td>{html.escape(row.get('role', ''))}</td>"
        f"<td>{html.escape(', '.join(row.get('scopes', [])))}</td>"
        f"<td><code>{html.escape(row.get('token_hash', '')[:12])}</code></td>"
        "</tr>"
        for row in tokens[-12:]
    )
    decision_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('allow', '')))}</td>"
        f"<td>{html.escape(row.get('principal', ''))}</td>"
        f"<td>{html.escape(row.get('action', ''))}</td>"
        f"<td><code>{html.escape(row.get('required_scope', ''))}</code></td>"
        f"<td>{html.escape(row.get('reason', ''))}</td>"
        "</tr>"
        for row in audit_query.get("latest_policy_decisions", [])[-10:]
    )
    queried_call_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('tool_id', ''))}</code></td>"
        f"<td>{html.escape(row.get('status', ''))}</td>"
        f"<td>{html.escape(row.get('principal', ''))}</td>"
        f"<td>{html.escape(row.get('role', ''))}</td>"
        f"<td>{html.escape(row.get('failure_reason', ''))}</td>"
        "</tr>"
        for row in audit_query.get("calls", [])[-20:]
    )
    readiness = readiness or check_external_auth_readiness(project_dir)
    return (
        "<details open><summary>Local MCP Gateway</summary>"
        f"<p class=\"muted\">calls: {html.escape(str(audit.get('call_count', 0)))} · failures: {html.escape(str(audit.get('failure_count', 0)))} · default role: {html.escape(policy.get('default_role', ''))} · require token: {html.escape(str(policy.get('require_token_for_external_clients', False)))}</p>"
        + _mcp_control_forms(policy)
        + _mcp_auth_readiness_html(readiness)
        + "<h3>Tool contracts</h3>"
        "<table><thead><tr><th>Tool</th><th>Scope</th><th>Risk</th><th>Review</th><th>Output</th><th>Hash</th></tr></thead>"
        f"<tbody>{tool_rows}</tbody></table>"
        "<details open><summary>Client sessions</summary>"
        "<table><thead><tr><th>Session</th><th>Client</th><th>Principal</th><th>Role</th><th>Status</th><th>Last seen</th></tr></thead>"
        f"<tbody>{session_rows}</tbody></table></details>"
        "<details><summary>Registered token descriptors</summary>"
        "<table><thead><tr><th>Token</th><th>Principal</th><th>Role</th><th>Scopes</th><th>Hash</th></tr></thead>"
        f"<tbody>{token_rows}</tbody></table></details>"
        "<details open><summary>MCP call audit</summary>"
        "<table><thead><tr><th>Tool</th><th>Status</th><th>Actor</th><th>Principal</th><th>Role</th><th>Risk</th><th>Failure</th></tr></thead>"
        f"<tbody>{call_rows}</tbody></table></details>"
        "<details open><summary>Audit query result</summary>"
        f"<p class=\"muted\">matched calls: {html.escape(str(audit_query.get('call_count', 0)))} · policy decisions: {html.escape(str(audit_query.get('decision_count', 0)))}</p>"
        "<table><thead><tr><th>Tool</th><th>Status</th><th>Principal</th><th>Role</th><th>Failure</th></tr></thead>"
        f"<tbody>{queried_call_rows}</tbody></table>"
        "<h3>Policy decisions</h3>"
        "<table><thead><tr><th>Allow</th><th>Principal</th><th>Action</th><th>Required scope</th><th>Reason</th></tr></thead>"
        f"<tbody>{decision_rows}</tbody></table></details></details>"
    )


def _mcp_auth_readiness_html(readiness: dict) -> str:
    readiness_summary = readiness.get("summary", {})
    readiness_rows = "".join(
        "<tr>"
        f"<td><code>{html.escape(row.get('check_id', ''))}</code></td>"
        f"<td><span class=\"pill {html.escape(row.get('status', '').lower())}\">{html.escape(row.get('status', ''))}</span></td>"
        f"<td>{html.escape(row.get('severity', ''))}</td>"
        f"<td>{html.escape(row.get('message', ''))}</td>"
        f"<td>{html.escape(row.get('remediation', ''))}</td>"
        "</tr>"
        for row in readiness.get("checks", [])
    )
    return (
        "<h3>External auth readiness</h3>"
        + '<div class="method-grid">'
        + f'<div><small>Status</small><strong>{html.escape(readiness.get("status", "unknown"))}</strong></div>'
        + f'<div><small>Auth mode</small><strong>{html.escape(readiness.get("active_auth_mode", ""))}</strong></div>'
        + f'<div><small>Warnings</small><strong>{html.escape(str(readiness_summary.get("warning_count", 0)))}</strong></div>'
        + f'<div><small>Failures</small><strong>{html.escape(str(readiness_summary.get("failure_count", 0)))}</strong></div>'
        + "</div>"
        + '<p class="muted">artifact: <code>v4/mcp_external_auth_readiness.json</code></p>'
        + "<details open><summary>Readiness checks</summary>"
        + "<table><thead><tr><th>Check</th><th>Status</th><th>Severity</th><th>Message</th><th>Fix</th></tr></thead>"
        + f"<tbody>{readiness_rows}</tbody></table></details>"
    )


def _mcp_control_forms(policy: dict) -> str:
    roles = ["local_admin", "reviewer", "agent_reader", "agent_operator"]
    role_options = "".join(
        f'<option value="{html.escape(role)}"{" selected" if role == policy.get("default_role") else ""}>{html.escape(role)}</option>'
        for role in roles
    )
    token_role_options = "".join(f'<option value="{html.escape(role)}">{html.escape(role)}</option>' for role in roles)
    require_checked = " checked" if policy.get("require_token_for_external_clients") else ""
    return f"""
      <div class="mcp-console">
        <form class="mini-form" method="post" action="/mcp/policy">
          <label for="mcp_default_role">Default local role</label>
          <select id="mcp_default_role" name="default_role">{role_options}</select>
          <label><input type="checkbox" name="require_token" value="1"{require_checked}> Require token for external clients</label>
          <div class="actions"><button type="submit">Save MCP policy</button></div>
        </form>
        <form class="mini-form" method="post" action="/mcp/token">
          <label for="mcp_principal">Client principal</label>
          <input id="mcp_principal" name="principal" type="text" placeholder="dataset-scout-agent">
          <label for="mcp_role">Role</label>
          <select id="mcp_role" name="role">{token_role_options}</select>
          <label for="mcp_scopes">Scopes override</label>
          <input id="mcp_scopes" name="scopes" type="text" placeholder="resource:read,tool:read">
          <div class="actions"><button type="submit">Create token JSON</button></div>
        </form>
        <form class="mini-form" method="post" action="/mcp/audit-query">
          <label for="mcp_audit_principal">Audit principal</label>
          <input id="mcp_audit_principal" name="principal" type="text" placeholder="reader-agent">
          <label for="mcp_audit_tool">Tool</label>
          <input id="mcp_audit_tool" name="tool" type="text" placeholder="evidence.trace.query">
          <label for="mcp_audit_status">Status</label>
          <select id="mcp_audit_status" name="status">
            <option value="">any</option>
            <option value="success">success</option>
            <option value="failed">failed</option>
          </select>
          <div class="actions"><button type="submit">Query audit</button></div>
        </form>
        <form class="mini-form" method="post" action="/mcp/auth-readiness">
          <label>External auth readiness</label>
          <p class="muted">Checks token policy, project isolation, HTTP session headers, OIDC/Vault env completeness, and MCP audit coverage.</p>
          <div class="actions"><button type="submit">Refresh readiness</button></div>
        </form>
      </div>
    """


def _registry_snapshot_panel(project_dir: Path) -> str:
    snapshot = _read_json(project_dir / "v4" / "registry_snapshots.json", {})
    kb_snapshot = _read_json(project_dir / "v4" / "kb_snapshot.json", {})
    snapshots = snapshot.get("snapshots", {})
    if not snapshots:
        return '<p class="muted">No registry snapshots recorded yet.</p>'
    method = snapshots.get("method_registry", {})
    source = snapshots.get("source_registry", {})
    rubric = snapshots.get("rubric", {})
    rows = [
        ("Project KB Binding", kb_snapshot.get("status", "not_bound"), kb_snapshot.get("registry_snapshot_hash", "")),
        ("Method Registry", method.get("method_count", 0), method.get("hash", "")),
        ("Source Registry", source.get("resource_count", 0), source.get("hash", "")),
        ("Rubric", len(rubric.get("sections", [])), rubric.get("hash", "")),
    ]
    body = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td>{html.escape(str(count))}</td>"
        f"<td><code>{html.escape(str(hash_value)[:16])}</code></td>"
        "</tr>"
        for name, count, hash_value in rows
    )
    return (
        "<details><summary>Registry snapshots</summary>"
        f"<p class=\"muted\">kb snapshot: <code>{html.escape(kb_snapshot.get('kb_snapshot_id', 'not bound'))}</code> · registry snapshot: <code>{html.escape(snapshot.get('snapshot_hash', '')[:16])}</code></p>"
        "<table><thead><tr><th>Registry</th><th>Items</th><th>Hash</th></tr></thead>"
        f"<tbody>{body}</tbody></table></details>"
    )


def _review_form(item_type: str, item_id: str, label: str) -> str:
    escaped_type = html.escape(item_type)
    escaped_id = html.escape(item_id)
    return f"""
      <form class="mini-form review-form" method="post" action="/review">
        <input type="hidden" name="item_type" value="{escaped_type}">
        <input type="hidden" name="item_id" value="{escaped_id}">
        <input type="text" name="reason" placeholder="required review reason">
        <input type="text" name="report_ref" placeholder="optional report ref">
        <button class="small-button" name="action" value="approve" type="submit">{html.escape(label)}</button>
        <button class="small-button ghost" name="action" value="needs_review" type="submit">Needs review</button>
        <button class="small-button ghost" name="action" value="reject" type="submit">Reject</button>
      </form>
    """


def _api_key_panel(project_dir: Path) -> str:
    llm = llm_provider_summary(project_dir)
    return (
        '<div class="method-grid">'
        f'<div><small>LLM provider</small><strong>{html.escape(llm.get("provider", "openai"))}</strong><small>{html.escape(llm.get("base_url", "") or "default endpoint")}</small></div>'
        f'<div><small>LLM API key</small><strong>{html.escape(masked_openai_key(project_dir))}</strong><small>{html.escape(llm.get("model", "") or "default model")}</small></div>'
        "</div>"
    )


def _system_status_panel(project_dir: Path) -> str:
    rows = []
    for item in system_status(project_dir):
        status = item["status"]
        rows.append(
            '<div class="audit-card">'
            f'<span class="pill {html.escape(status.lower())}">{html.escape(status)}</span>'
            f'<strong>{html.escape(item["name"])}</strong>'
            f'<small>{html.escape(item["detail"])}</small>'
            "</div>"
        )
    return "".join(rows)


def _language_options(current_language: str) -> str:
    order = ["zh", "ja", "en"]
    options = []
    for code in order:
        if code not in SUPPORTED_LANGUAGES:
            continue
        selected = " selected" if code == current_language else ""
        options.append(
            f'<option value="{html.escape(code)}"{selected}>{html.escape(LANGUAGE_LABELS.get(code, code))}</option>'
        )
    return "".join(options)


def _safe_redirect_target(headers) -> str:
    referer = headers.get("Referer", "")
    if not referer:
        return "/"
    parsed = urllib.parse.urlparse(referer)
    if parsed.scheme and parsed.hostname not in {"127.0.0.1", "localhost"}:
        return "/"
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return path


def _standalone_page(project_dir: Path, body: str, *, title: str) -> bytes:
    theme = _read_theme(project_dir)
    lang, t = translator(project_dir)
    localized_title = _ui_text(title, lang)
    language_options = _language_options(lang)
    html_body = f"""<!doctype html>
<html lang="{html.escape(lang)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(localized_title)}</title>
  <style>
    :root {{
      --bg:#f6f7f9; --panel:#fff; --panel-soft:#f9fafb; --text:#111827; --muted:#667085; --line:#d9dee7;
      --blue:#2563eb; --green:#16803c; --red:#c0362c; --amber:#a16207; --purple:#6d28d9; --shadow:0 12px 28px rgba(17,24,39,.06);
      font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Arial,sans-serif;
    }}
    body[data-theme="dark"] {{ --bg:#0b1120; --panel:#111827; --panel-soft:#0f172a; --text:#e5e7eb; --muted:#9aa4b2; --line:#263244; --blue:#60a5fa; --green:#4ade80; --red:#f87171; --amber:#fbbf24; --purple:#c4b5fd; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font-size:14px; }}
    .app-topbar {{ position:sticky; top:0; z-index:20; border-bottom:1px solid var(--line); background:color-mix(in srgb, var(--panel) 92%, transparent); backdrop-filter:blur(12px); }}
    .app-topbar-inner {{ max-width:1280px; margin:0 auto; padding:10px 22px; display:flex; gap:18px; align-items:center; justify-content:space-between; }}
    .brand-lockup {{ display:flex; align-items:center; gap:10px; min-width:220px; }}
    .brand-mark {{ width:28px; height:28px; border-radius:8px; background:#111827; color:#fff; display:inline-flex; align-items:center; justify-content:center; font-weight:800; }}
    body[data-theme="dark"] .brand-mark {{ background:#e5e7eb; color:#111827; }}
    .brand-lockup strong {{ display:block; font-size:14px; }}
    .brand-lockup small {{ display:block; font-size:12px; }}
    .topbar-tools {{ display:flex; gap:10px; align-items:center; justify-content:flex-end; flex-wrap:wrap; }}
    .topnav {{ display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }}
    .topnav a {{ color:var(--muted); text-decoration:none; padding:7px 10px; border-radius:8px; border:1px solid transparent; }}
    .topnav a:hover {{ color:var(--text); border-color:var(--line); background:var(--panel-soft); }}
    .language-form {{ margin:0; display:flex; align-items:center; gap:6px; }}
    .language-form label {{ margin:0; font-size:11px; white-space:nowrap; }}
    .language-form select {{ width:auto; min-width:112px; margin:0; padding:7px 28px 7px 9px; font-weight:700; }}
    main.app-shell {{ max-width:1280px; margin:0 auto; padding:24px 22px 72px; }}
    section.app-section {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; box-shadow:var(--shadow); padding:24px; }}
    .page-head {{ display:grid; grid-template-columns:minmax(0,1fr) auto; gap:16px; align-items:start; margin-bottom:6px; }}
    .page-kicker {{ display:inline-flex; width:max-content; align-items:center; gap:6px; border:1px solid var(--line); border-radius:999px; padding:4px 9px; color:var(--muted); background:var(--panel-soft); font-size:12px; font-weight:800; margin-bottom:10px; }}
    .page-meta {{ color:var(--muted); font-size:13px; line-height:1.6; }}
    h1 {{ margin:0 0 8px; font-size:30px; line-height:1.15; letter-spacing:0; }}
    h2,h3 {{ letter-spacing:0; }}
    p,.muted,small {{ color:var(--muted); line-height:1.5; }}
    .actions {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:16px 0; padding:10px; border:1px solid var(--line); border-radius:8px; background:var(--panel-soft); }}
    a.button,button {{ display:inline-flex; align-items:center; justify-content:center; border:0; border-radius:8px; min-height:36px; padding:9px 12px; background:var(--blue); color:#fff; text-decoration:none; font-weight:700; cursor:pointer; white-space:nowrap; }}
    a.button:hover,button:hover {{ filter:brightness(.97); }}
    .ghost {{ background:var(--panel) !important; color:var(--text) !important; border:1px solid var(--line) !important; }}
    input,select {{ width:100%; border:1px solid var(--line); border-radius:8px; padding:9px 10px; background:var(--panel); color:var(--text); margin:4px 0 9px; }}
    label {{ display:block; color:var(--muted); font-size:12px; font-weight:700; margin-top:4px; }}
    .mini-form {{ background:transparent; border:0; padding:0; box-shadow:none; }}
    .audit-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:14px 0; }}
    .audit-card {{ border:1px solid var(--line); border-radius:8px; background:var(--panel-soft); padding:12px; min-height:84px; }}
    .audit-card small {{ display:block; font-size:12px; }}
    .audit-card strong {{ display:block; margin:5px 0 3px; font-size:22px; line-height:1.15; overflow-wrap:anywhere; }}
    details {{ margin-top:12px; border:1px solid var(--line); border-radius:8px; background:var(--panel); overflow:hidden; }}
    summary {{ cursor:pointer; padding:12px 14px; font-weight:800; background:var(--panel-soft); border-bottom:1px solid var(--line); }}
    details:not([open]) summary {{ border-bottom:0; }}
    table {{ width:100%; border-collapse:collapse; }}
    th,td {{ border-bottom:1px solid var(--line); text-align:left; vertical-align:top; padding:10px 9px; font-size:13px; }}
    th {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.02em; background:var(--panel-soft); }}
    tr:last-child td {{ border-bottom:0; }}
    td small {{ display:block; margin-top:4px; }}
    code {{ background:rgba(100,116,139,.12); padding:2px 5px; border-radius:6px; overflow-wrap:anywhere; }}
    pre {{ white-space:pre-wrap; overflow:auto; max-height:360px; border:1px solid var(--line); border-radius:8px; padding:12px; background:var(--panel-soft); }}
    .pill {{ display:inline-flex; align-items:center; border-radius:999px; padding:3px 8px; background:rgba(100,116,139,.12); color:var(--muted); font-size:12px; font-weight:800; min-width:58px; justify-content:center; }}
    .pill.pass,.pill.ready,.pill.completed,.pill.success {{ background:rgba(22,128,60,.12); color:var(--green); }}
    .pill.review,.pill.warn,.pill.warning,.pill.needs_review,.pill.review_required {{ background:rgba(161,98,7,.14); color:var(--amber); }}
    .pill.fail,.pill.failed,.pill.blocked,.pill.error {{ background:rgba(192,54,44,.12); color:var(--red); }}
    .pill.running,.pill.pending {{ background:rgba(37,99,235,.12); color:var(--blue); }}
    .small-button {{ padding:7px 10px; font-size:12px; min-height:30px; }}
    .guide-panel {{ display:grid; grid-template-columns:280px minmax(0,1fr); gap:14px; margin:14px 0 18px; }}
    .guide-summary {{ border:1px solid var(--line); border-radius:10px; padding:16px; background:#101828; color:#fff; }}
    body[data-theme="dark"] .guide-summary {{ background:#020617; border-color:#334155; }}
    .guide-summary small {{ color:#cbd5e1; }}
    .guide-summary strong {{ display:block; font-size:30px; line-height:1; margin:8px 0; color:#fff; }}
    .guide-steps {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }}
    .guide-step {{ border:1px solid var(--line); border-radius:10px; background:var(--panel); padding:13px; display:grid; grid-template-columns:34px minmax(0,1fr); gap:10px; align-items:start; }}
    .guide-index {{ width:34px; height:34px; border-radius:10px; display:inline-flex; align-items:center; justify-content:center; background:var(--panel-soft); border:1px solid var(--line); font-weight:900; color:var(--text); }}
    .guide-step strong {{ display:block; margin-bottom:4px; }}
    .guide-step small {{ display:block; }}
    .status-line {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-top:8px; }}
    @media (max-width:980px) {{ .audit-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .guide-panel {{ grid-template-columns:1fr; }} .app-topbar-inner {{ align-items:flex-start; flex-direction:column; }} .topbar-tools,.topnav {{ justify-content:flex-start; }} }}
    @media (max-width:760px) {{ .audit-grid,.guide-steps {{ grid-template-columns:1fr; }} .page-head {{ grid-template-columns:1fr; }} main.app-shell {{ padding:16px 12px 48px; }} section.app-section {{ padding:16px; }} h1 {{ font-size:24px; }} .actions {{ align-items:stretch; }} a.button,button {{ width:100%; }} th,td {{ font-size:12px; }} }}
  </style>
</head>
<body data-theme="{html.escape(theme)}">
<header class="app-topbar">
  <div class="app-topbar-inner">
    <div class="brand-lockup"><span class="brand-mark">TC</span><div><strong>TargetCompass v5</strong><small>{html.escape(localized_title)}</small></div></div>
    <div class="topbar-tools">
      <nav class="topnav" aria-label="v5 navigation">
        <a href="/">{html.escape(t("home"))}</a>
        <a href="/v5/canonical-flow">{html.escape(t("flow"))}</a>
        <a href="/v5/resource-gate">{html.escape(t("datasets"))}</a>
        <a href="/v5/product-report">{html.escape(t("report"))}</a>
        <a href="/v5/release-acceptance">{html.escape(t("acceptance"))}</a>
        <a href="/v5/production-readiness">{html.escape(t("production"))}</a>
      </nav>
      <form method="post" action="/language" class="language-form">
        <label for="global-language">{html.escape(t("switch_language"))}</label>
        <select id="global-language" name="language" onchange="this.form.submit()">{language_options}</select>
      </form>
    </div>
  </div>
</header>
{body}
{_i18n_ui_script(lang)}
</body>
</html>"""
    return html_body.encode("utf-8")



def _ui_translation_maps() -> dict[str, dict[str, str]]:
    zh = {
        "v5 Dataset Gate": "v5 \u6570\u636e\u96c6\u9501\u5e93",
        "v5 Analysis Main Path": "v5 \u771f\u5b9e\u5206\u6790\u4e3b\u8def\u5f84",
        "v5 Product Report": "v5 \u7814\u7a76\u62a5\u544a",
        "Production Storage": "\u751f\u4ea7\u5b58\u50a8",
        "Service Manager": "\u670d\u52a1\u7ba1\u7406",
        "Access Control": "\u6743\u9650\u63a7\u5236",
        "v5 Canonical Flow": "v5 \u89c4\u8303\u6d41\u7a0b",
        "v5 PilotDeck Console": "v5 \u63a7\u5236\u53f0",
        "P1 Platform Readiness": "P1 \u5e73\u53f0\u5c31\u7eea\u5ea6",
        "P2 Platform Readiness": "P2 \u5e73\u53f0\u5c31\u7eea\u5ea6",
        "v5 Production Readiness": "v5 \u751f\u4ea7\u5c31\u7eea\u5ea6",
        "Release Acceptance": "\u53d1\u5e03\u524d\u9a8c\u6536",
        "Release acceptance": "\u53d1\u5e03\u524d\u9a8c\u6536",
        "Release gate": "\u53d1\u5e03\u95e8\u63a7",
        "v5 Delivery Gate": "v5 \u4ea4\u4ed8\u95e8\u63a7",
        "Status": "\u72b6\u6001", "Manifest": "\u6e05\u5355", "Matrix": "\u77e9\u9635", "Back": "\u8fd4\u56de",
        "Production readiness": "\u751f\u4ea7\u5c31\u7eea\u5ea6", "Refresh acceptance": "\u5237\u65b0\u9a8c\u6536",
        "Question matrix": "\u95ee\u9898\u77e9\u9635", "Suites": "\u6d4b\u8bd5\u5957\u4ef6", "Scenarios": "\u573a\u666f",
        "Pre-release script": "\u53d1\u5e03\u524d\u811a\u672c", "Blocking items": "\u963b\u585e\u9879",
        "Evidence ref": "\u8bc1\u636e\u5f15\u7528", "Required action": "\u5fc5\u8981\u52a8\u4f5c", "Gate": "\u95e8\u63a7",
        "Projects": "\u9879\u76ee", "Access": "\u6743\u9650", "Services": "\u670d\u52a1", "Storage": "\u5b58\u50a8",
        "Evidence Claims": "\u8bc1\u636e\u4e0e\u7ed3\u8bba", "Audit": "\u5ba1\u8ba1", "Permissions": "\u6743\u9650",
        "Capability": "\u80fd\u529b", "Entrypoint": "\u5165\u53e3", "Role permissions": "\u89d2\u8272\u6743\u9650",
        "Access audit": "\u6743\u9650\u5ba1\u8ba1", "Remaining work": "\u5269\u4f59\u5de5\u4f5c", "Production blockers": "\u751f\u4ea7\u963b\u585e\u9879",
        "Question": "\u7814\u7a76\u95ee\u9898", "Dataset": "\u6570\u636e\u96c6", "Task packets": "\u4efb\u52a1\u5305", "Stages": "\u9636\u6bb5", "Stage": "\u9636\u6bb5",
        "Artifacts": "\u4ea7\u7269", "Candidate ranking": "\u5019\u9009\u6392\u540d", "Rank": "\u6392\u540d", "Gene": "\u57fa\u56e0", "Score": "\u8bc4\u5206",
        "Limitations": "\u9650\u5236", "Overall": "\u603b\u4f53", "Backend writes": "\u540e\u7aef\u5199\u5165", "Evidence DB": "\u8bc1\u636e\u5e93",
        "Review": "\u590d\u6838", "Manual review": "\u4eba\u5de5\u590d\u6838", "Candidates": "\u5019\u9009\u6570\u636e\u96c6", "Lockable": "\u53ef\u9501\u5e93",
        "Blocking issues": "\u963b\u585e\u95ee\u9898", "Recovery": "\u6062\u590d\u5efa\u8bae", "Reason": "\u539f\u56e0",
        "Group column": "\u5206\u7ec4\u5217", "Case label": "\u75c5\u4f8b\u6807\u7b7e", "Control label": "\u5bf9\u7167\u6807\u7b7e",
        "Organism": "\u7269\u79cd", "Tissue": "\u7ec4\u7ec7/\u7ec6\u80de\u7c7b\u578b", "Platform": "\u5e73\u53f0", "Sample count": "\u6837\u672c\u6570",
        "PASS": "\u901a\u8fc7", "WARN": "\u8b66\u544a", "FAIL": "\u5931\u8d25", "READY": "\u5c31\u7eea", "RUNNING": "\u8fd0\u884c\u4e2d",
        "completed": "\u5df2\u5b8c\u6210", "blocked": "\u5df2\u963b\u585e", "review_required": "\u9700\u8981\u590d\u6838", "missing": "\u7f3a\u5931", "unknown": "\u672a\u77e5",
        "True": "\u662f", "False": "\u5426",
    }
    ja = {
        "v5 Dataset Gate": "v5 \u30c7\u30fc\u30bf\u30bb\u30c3\u30c8\u30ed\u30c3\u30af",
        "v5 Analysis Main Path": "v5 \u5b9f\u89e3\u6790\u30e1\u30a4\u30f3\u30d1\u30b9",
        "v5 Product Report": "v5 \u7814\u7a76\u30ec\u30dd\u30fc\u30c8",
        "Production Storage": "\u672c\u756a\u30b9\u30c8\u30ec\u30fc\u30b8",
        "Service Manager": "\u30b5\u30fc\u30d3\u30b9\u7ba1\u7406",
        "Access Control": "\u30a2\u30af\u30bb\u30b9\u5236\u5fa1",
        "v5 Canonical Flow": "v5 \u6a19\u6e96\u30d5\u30ed\u30fc",
        "v5 PilotDeck Console": "v5 \u30b3\u30f3\u30bd\u30fc\u30eb",
        "P1 Platform Readiness": "P1 \u30d7\u30e9\u30c3\u30c8\u30d5\u30a9\u30fc\u30e0\u6e96\u5099\u72b6\u6cc1",
        "P2 Platform Readiness": "P2 \u30d7\u30e9\u30c3\u30c8\u30d5\u30a9\u30fc\u30e0\u6e96\u5099\u72b6\u6cc1",
        "v5 Production Readiness": "v5 \u672c\u756a\u6e96\u5099\u72b6\u6cc1",
        "Release Acceptance": "\u30ea\u30ea\u30fc\u30b9\u53d7\u5165", "Release acceptance": "\u30ea\u30ea\u30fc\u30b9\u53d7\u5165",
        "Release gate": "\u30ea\u30ea\u30fc\u30b9\u30b2\u30fc\u30c8", "v5 Delivery Gate": "v5 \u7d0d\u54c1\u30b2\u30fc\u30c8",
        "Status": "\u72b6\u614b", "Manifest": "\u30de\u30cb\u30d5\u30a7\u30b9\u30c8", "Matrix": "\u30de\u30c8\u30ea\u30af\u30b9", "Back": "\u623b\u308b",
        "Production readiness": "\u672c\u756a\u6e96\u5099\u72b6\u6cc1", "Refresh acceptance": "\u53d7\u5165\u72b6\u614b\u3092\u66f4\u65b0",
        "Question matrix": "\u8cea\u554f\u30de\u30c8\u30ea\u30af\u30b9", "Suites": "\u30c6\u30b9\u30c8\u30b9\u30a4\u30fc\u30c8", "Scenarios": "\u30b7\u30ca\u30ea\u30aa",
        "Pre-release script": "\u30ea\u30ea\u30fc\u30b9\u524d\u30b9\u30af\u30ea\u30d7\u30c8", "Blocking items": "\u30d6\u30ed\u30c3\u30af\u9805\u76ee",
        "Evidence ref": "\u8a3c\u62e0\u53c2\u7167", "Required action": "\u5fc5\u8981\u306a\u5bfe\u5fdc", "Gate": "\u30b2\u30fc\u30c8",
        "Projects": "\u30d7\u30ed\u30b8\u30a7\u30af\u30c8", "Access": "\u30a2\u30af\u30bb\u30b9", "Services": "\u30b5\u30fc\u30d3\u30b9", "Storage": "\u30b9\u30c8\u30ec\u30fc\u30b8",
        "Evidence Claims": "\u8a3c\u62e0\u3068\u4e3b\u5f35", "Audit": "\u76e3\u67fb", "Permissions": "\u6a29\u9650",
        "Capability": "\u6a5f\u80fd", "Entrypoint": "\u5165\u53e3", "Role permissions": "\u30ed\u30fc\u30eb\u6a29\u9650",
        "Access audit": "\u30a2\u30af\u30bb\u30b9\u76e3\u67fb", "Remaining work": "\u6b8b\u4f5c\u696d", "Production blockers": "\u672c\u756a\u5316\u30d6\u30ed\u30c3\u30ab\u30fc",
        "Question": "\u7814\u7a76\u8ab2\u984c", "Dataset": "\u30c7\u30fc\u30bf\u30bb\u30c3\u30c8", "Task packets": "\u30bf\u30b9\u30af\u30d1\u30b1\u30c3\u30c8", "Stages": "\u30b9\u30c6\u30fc\u30b8", "Stage": "\u30b9\u30c6\u30fc\u30b8",
        "Artifacts": "\u6210\u679c\u7269", "Candidate ranking": "\u5019\u88dc\u30e9\u30f3\u30ad\u30f3\u30b0", "Rank": "\u9806\u4f4d", "Gene": "\u907a\u4f1d\u5b50", "Score": "\u30b9\u30b3\u30a2",
        "Limitations": "\u9650\u754c", "Overall": "\u5168\u4f53", "Backend writes": "\u30d0\u30c3\u30af\u30a8\u30f3\u30c9\u66f8\u304d\u8fbc\u307f", "Evidence DB": "\u8a3c\u62e0 DB",
        "Review": "\u30ec\u30d3\u30e5\u30fc", "Manual review": "\u4eba\u624b\u30ec\u30d3\u30e5\u30fc", "Candidates": "\u5019\u88dc\u30c7\u30fc\u30bf\u30bb\u30c3\u30c8", "Lockable": "\u30ed\u30c3\u30af\u53ef\u80fd",
        "Blocking issues": "\u30d6\u30ed\u30c3\u30af\u7406\u7531", "Recovery": "\u5fa9\u65e7\u63d0\u6848", "Reason": "\u7406\u7531",
        "Group column": "\u30b0\u30eb\u30fc\u30d7\u5217", "Case label": "\u30b1\u30fc\u30b9\u30e9\u30d9\u30eb", "Control label": "\u5bfe\u7167\u30e9\u30d9\u30eb",
        "Organism": "\u751f\u7269\u7a2e", "Tissue": "\u7d44\u7e54 / \u7d30\u80de\u30bf\u30a4\u30d7", "Platform": "\u30d7\u30e9\u30c3\u30c8\u30d5\u30a9\u30fc\u30e0", "Sample count": "\u30b5\u30f3\u30d7\u30eb\u6570",
        "当前交付状态": "\u73fe\u5728\u306e\u7d0d\u54c1\u72b6\u614b",
        "基础回归": "\u57fa\u672c\u56de\u5e30",
        "真实问题长测": "\u5b9f\u8cea\u554f\u30ed\u30f3\u30b0\u30e9\u30f3",
        "真实数据主路径": "\u5b9f\u30c7\u30fc\u30bf\u4e3b\u7d4c\u8def",
        "安装交付": "\u30a4\u30f3\u30b9\u30c8\u30fc\u30eb\u7d0d\u54c1",
        "阻塞项": "\u30d6\u30ed\u30c3\u30af\u9805\u76ee",
        "最终交付前必须处理": "\u6700\u7d42\u7d0d\u54c1\u524d\u306b\u5bfe\u5fdc\u304c\u5fc5\u8981",
        "交付前阻塞项": "\u7d0d\u54c1\u524d\u306e\u30d6\u30ed\u30c3\u30af\u9805\u76ee",
        "真实数据主路径验证矩阵": "\u5b9f\u30c7\u30fc\u30bf\u4e3b\u7d4c\u8def\u691c\u8a3c\u30de\u30c8\u30ea\u30af\u30b9",
        "发布前固定测试矩阵": "\u30ea\u30ea\u30fc\u30b9\u524d\u56fa\u5b9a\u30c6\u30b9\u30c8\u30de\u30c8\u30ea\u30af\u30b9",
        "验收命令": "\u53d7\u5165\u30b3\u30de\u30f3\u30c9",
        "PASS": "\u5408\u683c", "WARN": "\u8b66\u544a", "FAIL": "\u5931\u6557", "READY": "\u6e96\u5099\u5b8c\u4e86", "RUNNING": "\u5b9f\u884c\u4e2d",
        "completed": "\u5b8c\u4e86", "blocked": "\u30d6\u30ed\u30c3\u30af\u4e2d", "review_required": "\u30ec\u30d3\u30e5\u30fc\u5fc5\u8981", "missing": "\u4e0d\u8db3", "unknown": "\u4e0d\u660e",
        "True": "\u306f\u3044", "False": "\u3044\u3044\u3048",
    }
    return {"zh": zh, "ja": ja, "en": {}}


def _ui_text(value: object, lang: str = "zh") -> str:
    text = str(value or "")
    mapping = _ui_translation_maps().get(lang, {})
    if text in mapping:
        return mapping[text]
    replacements_by_lang = {
        "zh": {
            "lockable: True": "\u53ef\u9501\u5e93\uff1a\u662f",
            "lockable: False": "\u53ef\u9501\u5e93\uff1a\u5426",
            "group_metadata_not_assessed": "\u5206\u7ec4\u5143\u6570\u636e\u672a\u8bc4\u4f30",
            "sample_size_not_assessed": "\u6837\u672c\u91cf\u672a\u8bc4\u4f30",
            "organism_unknown": "\u7269\u79cd\u672a\u77e5",
            "tissue_unknown": "\u7ec4\u7ec7\u672a\u77e5",
            "GEO import": "GEO \u5bfc\u5165",
            "matrix parse": "\u77e9\u9635\u89e3\u6790",
            "registered analysis": "\u5df2\u6ce8\u518c\u5206\u6790",
            "manual metadata review note": "\u4eba\u5de5\u5143\u6570\u636e\u590d\u6838\u5907\u6ce8",
        },
        "ja": {
            "lockable: True": "\u30ed\u30c3\u30af\u53ef\u80fd\uff1a\u306f\u3044",
            "lockable: False": "\u30ed\u30c3\u30af\u53ef\u80fd\uff1a\u3044\u3044\u3048",
            "group_metadata_not_assessed": "\u30b0\u30eb\u30fc\u30d7\u30e1\u30bf\u30c7\u30fc\u30bf\u672a\u8a55\u4fa1",
            "sample_size_not_assessed": "\u30b5\u30f3\u30d7\u30eb\u6570\u672a\u8a55\u4fa1",
            "organism_unknown": "\u751f\u7269\u7a2e\u4e0d\u660e",
            "tissue_unknown": "\u7d44\u7e54\u4e0d\u660e",
            "GEO import": "GEO \u30a4\u30f3\u30dd\u30fc\u30c8",
            "matrix parse": "\u30de\u30c8\u30ea\u30af\u30b9\u89e3\u6790",
            "registered analysis": "\u767b\u9332\u6e08\u307f\u89e3\u6790",
            "manual metadata review note": "\u624b\u52d5\u30e1\u30bf\u30c7\u30fc\u30bf\u30ec\u30d3\u30e5\u30fc\u5099\u8003",
        },
    }
    for src, dst in replacements_by_lang.get(lang, {}).items():
        text = text.replace(src, dst)
    return text


def _zh_ui_text(value: object) -> str:
    return _ui_text(value, "zh")


def _i18n_ui_script(lang: str = "zh") -> str:
    mapping = _ui_translation_maps().get(lang, {})
    if not mapping:
        return "<script>document.documentElement.dataset.lang='en';</script>"
    payload = json.dumps(mapping, ensure_ascii=False, sort_keys=True)
    lang_payload = json.dumps(lang)
    phrases = {
        "zh": {
            "Status:": "状态：",
            "Manifest:": "清单：",
            "Matrix:": "矩阵：",
            "truthful pre-delivery status": "真实交付前状态",
            "real-question validation target": "真实问题验收目标",
            "failure and report gates": "失败与报告门控",
            "run_v5_pre_release_acceptance.ps1": "run_v5_pre_release_acceptance.ps1",
            "Run clean Windows/VM install-start-stop-restart-uninstall smoke and record": "运行干净 Windows/虚拟机安装、启动、停止、重启、卸载 smoke，并记录",
            "GEO passed; SRA/cellxgene need real matrix adapter acceptance.": "GEO 已通过；SRA/cellxgene 仍需真实矩阵 adapter 验收。",
            "quick / full / e2e tests are fixed as pre-release gates.": "quick / full / e2e 测试已固定为发布前门槛。",
        },
        "ja": {
            "Status:": "状態：",
            "Manifest:": "マニフェスト：",
            "Matrix:": "マトリクス：",
            "truthful pre-delivery status": "納品前の実状態",
            "real-question validation target": "実質問検証目標",
            "failure and report gates": "失敗とレポートのゲート",
            "1 个阻塞项需要在最终交付前处理。GEO 主路径已通过，SRA/cellxgene 和干净机安装仍需验收记录。": "1 件のブロック項目は最終納品前に対応が必要です。GEO 主経路は合格済みで、SRA/cellxgene とクリーン環境インストールは受入記録が必要です。",
            "quick / full / e2e 测试已固定为发布前门槛。": "quick / full / e2e テストはリリース前ゲートとして固定されています。",
            "50 个真实研究方向验证资源发现、LLM、报告导出稳定性。": "50 件の実研究方向でリソース探索、LLM、レポート出力の安定性を検証します。",
            "GEO 已通过；SRA/cellxgene 需真实矩阵 adapter 验收。": "GEO は合格済みです。SRA/cellxgene は実マトリクス adapter の受入が必要です。",
            "干净 Windows/VM 安装、启动、停止、卸载仍需记录。": "クリーン Windows/VM でのインストール、起動、停止、アンインストール記録がまだ必要です。",
            "Run clean Windows/VM install-start-stop-restart-uninstall smoke and record": "クリーン Windows/VM でインストール、起動、停止、再起動、アンインストール smoke を実行して記録",
            "GEO passed; SRA/cellxgene need real matrix adapter acceptance.": "GEO は合格済み。SRA/cellxgene は実マトリクス adapter の受入が必要です。",
            "quick / full / e2e tests are fixed as pre-release gates.": "quick / full / e2e テストはリリース前ゲートとして固定されています。",
        },
    }.get(lang, {})
    phrase_payload = json.dumps(phrases, ensure_ascii=False, sort_keys=True)
    return """
<script>
(function() {
  const map = __PAYLOAD__;
  const phrases = __PHRASES__;
  document.documentElement.dataset.lang = __LANG__;
  const skip = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'INPUT', 'SELECT', 'OPTION', 'CODE', 'PRE']);
  function translateTextNode(node) {
    const raw = node.nodeValue;
    if (!raw || !raw.trim()) return;
    const leading = raw.match(/^\\s*/)[0];
    const trailing = raw.match(/\\s*$/)[0];
    const core = raw.trim();
    if (Object.prototype.hasOwnProperty.call(map, core)) {
      node.nodeValue = leading + map[core] + trailing;
      return;
    }
    let updated = raw;
    for (const [source, target] of Object.entries(phrases)) {
      updated = updated.split(source).join(target);
    }
    if (updated !== raw) {
      node.nodeValue = updated;
    }
  }
  function walk(root) {
    for (const child of Array.from(root.childNodes)) {
      if (child.nodeType === Node.TEXT_NODE) translateTextNode(child);
      else if (child.nodeType === Node.ELEMENT_NODE && !skip.has(child.tagName)) walk(child);
    }
  }
  walk(document.body);
  document.querySelectorAll('input[placeholder], textarea[placeholder]').forEach((el) => {
    const value = el.getAttribute('placeholder');
    if (Object.prototype.hasOwnProperty.call(map, value)) el.setAttribute('placeholder', map[value]);
  });
})();
</script>""".replace("__PAYLOAD__", payload).replace("__PHRASES__", phrase_payload).replace("__LANG__", lang_payload)


def _zh_ui_script() -> str:
    return _i18n_ui_script("zh")

def _agent_workflow_panel(project_dir: Path, lang: str) -> str:
    center = build_status_center(project_dir)
    run = center["run"]
    stages = {row["name"]: row for row in center["stage_cards"]}
    ideas = load_ideas(project_dir)
    queue = build_review_queue(project_dir)
    approval = load_approval_state(project_dir)
    report_exists = (project_dir / "reports" / "target_report.html").exists()
    labels = {
        "zh": {
            "title": "六步 Agent 工作流",
            "subtitle": "从 GPT 生成点子，到人工复审和正式报告。每一步都对应当前已有产物，不是额外模拟流程。",
            "generation": ("生成", "GPT/本地方法生成研究点子与 ResearchSpec", f"{len(ideas)} 个候选点子"),
            "initial_review": ("初审", "检查 ResearchSpec、数据适配和可行性门控", f"{queue.get('queue_count', 0)} 项待审"),
            "verification": ("查证", "自动匹配数据集、GEO/GSE 导入、平台和分组检查", f"{len(center.get('geo_statuses', []))} 个 GEO 状态"),
            "execution": ("执行", "运行 DEG、富集、证据库、候选评分等本地分析", run.get("message", "")),
            "final_review": ("复审", "人工通过/复核/驳回，并记录理由、差异和报告引用", approval.get("status", "draft")),
            "report": ("报告", "输出 HTML、Word 兼容报告和结构化 JSON", "已生成" if report_exists else "未生成"),
            "current": "当前",
            "idle": "待运行",
            "pending": "待运行",
            "running": "运行中",
            "success": "完成",
            "failed": "失败",
            "review": "需复核",
        },
        "en": {
            "title": "Six-step Agent workflow",
            "subtitle": "From GPT idea generation to human review and final report. Each step is backed by current project artifacts.",
            "generation": ("Generate", "Generate ideas and ResearchSpec via GPT or local fallback", f"{len(ideas)} candidate ideas"),
            "initial_review": ("Initial review", "Check ResearchSpec readiness, data fit, and feasibility gates", f"{queue.get('queue_count', 0)} queued review item(s)"),
            "verification": ("Verify", "Match datasets, import GEO/GSE, and check platform/grouping", f"{len(center.get('geo_statuses', []))} GEO status file(s)"),
            "execution": ("Execute", "Run DEG, enrichment, evidence import, and scoring locally", run.get("message", "")),
            "final_review": ("Final review", "Approve, review, or reject with reason, diff, and report reference", approval.get("status", "draft")),
            "report": ("Report", "Write HTML, Word-compatible report, and structured JSON", "generated" if report_exists else "not generated"),
            "current": "Current",
            "idle": "Idle",
            "pending": "Pending",
            "running": "Running",
            "success": "Done",
            "failed": "Failed",
            "review": "Review",
        },
    }
    text = labels.get(lang, labels["zh"])
    rows = []
    for idx, stage in enumerate(["generation", "initial_review", "verification", "execution", "final_review", "report"], 1):
        stage_row = stages.get(stage, {})
        status = stage_row.get("status", "pending")
        if status == "pending":
            if stage == "generation" and ideas:
                status = "success"
            elif stage == "initial_review" and (ideas or queue.get("queue_count", 0) or approval.get("review_count", 0)):
                status = "success"
            elif stage == "verification" and ((project_dir / "dataset_match_report.csv").exists() or center.get("geo_statuses")):
                status = "success"
            elif stage == "execution" and run.get("status") == "success":
                status = "success"
        if stage == "final_review" and queue.get("queue_count", 0):
            status = "review"
        elif stage == "final_review" and approval.get("status") in {"ready_for_signoff", "signed_off"}:
            status = "success"
        if stage == "report" and report_exists and status == "pending":
            status = "success"
        title, description, artifact = text[stage]
        active = " active" if stage_row.get("active") else ""
        status_label = text.get(status, status)
        rows.append(
            f'<div class="workflow-step {html.escape(status)}{active}">'
            f'<div class="workflow-head"><span class="workflow-index">{idx}</span><span class="pill {html.escape(status)}">{html.escape(status_label)}</span></div>'
            f'<strong>{html.escape(title)}</strong>'
            f'<p>{html.escape(description)}</p>'
            f'<small>{html.escape(artifact or stage_row.get("message", ""))}</small>'
            "</div>"
        )
    return (
        '<section class="workflow-panel">'
        f'<div class="section-title"><div><h2>{html.escape(text["title"])}</h2><p>{html.escape(text["subtitle"])}</p></div>'
        f'<span class="pill {html.escape(run.get("status", "idle"))}">{html.escape(text["current"])}: {html.escape(text.get(run.get("status", "idle"), run.get("status", "idle")))}</span></div>'
        f'<div class="workflow-grid">{"".join(rows)}</div>'
        "</section>"
    )


def _app_nav(active: str = "home") -> str:
    groups = [
        (
            "工作台",
            [
                ("home", "/", "总览"),
                ("flow", "/v5/flow", "Agent 流程"),
                ("console", "/v5/console", "运行控制台"),
                ("report", "/v5/product-report", "研究报告"),
            ],
        ),
        (
            "数据与证据",
            [
                ("resource", "/v5/resource-gate", "数据集锁库"),
                ("analysis", "/v5/analysis-main-path", "真实分析路径"),
                ("evidence", "/v5/evidence-claims", "证据与 Claim"),
                ("artifacts", "/v5/artifacts", "Artifact 查询"),
            ],
        ),
        (
            "平台管理",
            [
                ("storage", "/v5/storage", "存储后端"),
                ("services", "/v5/services", "服务管理"),
                ("access", "/v5/access", "权限与 Token"),
                ("readiness", "/v5/platform-readiness", "平台验收"),
                ("production", "/v5/production-readiness", "生产化差距"),
            ],
        ),
    ]
    sections = []
    for title, items in groups:
        links = []
        for key, href, label in items:
            selected = " selected" if key == active else ""
            links.append(f'<a class="nav-link{selected}" href="{html.escape(href)}">{html.escape(label)}</a>')
        sections.append(f'<div class="nav-group"><small>{html.escape(title)}</small>{"".join(links)}</div>')
    return (
        '<aside class="app-sidebar">'
        '<div class="brand-mark"><span>TC</span><div><strong>TargetCompass</strong><small>v5 local</small></div></div>'
        + "".join(sections)
        + "</aside>"
    )


def _home_index_cards() -> str:
    cards = [
        ("开始研究", "输入问题并运行 v5 本地流程", "/", "主入口"),
        ("Agent 流程", "查看 7 个 Agent、handoff、task、claim ceiling", "/v5/flow", "流程"),
        ("数据集锁库", "补 metadata、人工纠错、进入真实分析", "/v5/resource-gate", "数据"),
        ("分析主路径", "GEO import、矩阵解析、bulk/scRNA 路由", "/v5/analysis-main-path", "执行"),
        ("报告", "结论摘要、候选排序、证据链和导出", "/v5/product-report", "产物"),
        ("存储", "PostgreSQL / MinIO / ArtifactStore 状态", "/v5/storage", "后台"),
        ("权限", "用户、角色、token 生命周期和审计", "/v5/access", "管理"),
        ("服务", "启动、停止、重启、日志和端口恢复", "/v5/services", "运维"),
        ("生产化验收", "OIDC/Vault、主存储、memory、安装器和大样本验收", "/v5/production-readiness", "交付"),
    ]
    body = "".join(
        '<a class="index-card" href="{href}">'
        '<span>{tag}</span><strong>{title}</strong><small>{desc}</small>'
        "</a>".format(href=html.escape(href), tag=html.escape(tag), title=html.escape(title), desc=html.escape(desc))
        for title, desc, href, tag in cards
    )
    return f'<section class="index-panel"><div class="section-title"><div><h2>功能目录</h2><p>按工作步骤进入，不再把所有功能堆在首页。</p></div></div><div class="index-grid">{body}</div></section>'


def _home_status_summary(project_dir: Path, lang: str) -> str:
    center = build_status_center(project_dir)
    run = center["run"]
    ideas = load_ideas(project_dir)
    queue = build_review_queue(project_dir)
    report_ready = (project_dir / "v5" / "reports" / "product_report.html").exists() or (project_dir / "reports" / "target_report.html").exists()
    cards = [
        ("运行状态", run.get("status", "idle"), run.get("message", "等待运行")),
        ("候选点子", str(len(ideas)), "生成后的 ideas / ResearchSpec"),
        ("待人工处理", str(queue.get("queue_count", 0)), "审批、QC 或 metadata review"),
        ("报告", "READY" if report_ready else "PENDING", "正式展示页与结构化 manifest"),
    ]
    return (
        '<section class="summary-panel"><div class="section-title"><div><h2>当前项目状态</h2><p>只显示会影响下一步操作的关键信息。</p></div></div>'
        '<div class="summary-grid">'
        + "".join(
            f'<div class="summary-card"><small>{html.escape(label)}</small><strong>{html.escape(value)}</strong><p>{html.escape(note)}</p></div>'
            for label, value, note in cards
        )
        + "</div></section>"
    )


def _home_diagnostics_panel(project_dir: Path) -> str:
    return (
        '<section class="summary-panel">'
        '<div class="section-title"><div><h2>系统状态</h2><p>安装后排障入口，显示运行环境、API Key 和重置动作。</p></div></div>'
        '<details><summary>环境自检</summary><div class="summary-grid">'
        + _system_status_panel(project_dir)
        + "</div></details>"
        '<details><summary>API Key</summary>'
        + _api_key_panel(project_dir)
        + "</details>"
        '<form class="mini-form" method="post" action="/reset-demo">'
        '<div class="actions"><button class="ghost" type="submit">清空输出并重建 Demo</button></div>'
        "</form>"
        "</section>"
    )


def _page(project_dir: Path, message: str = "", raw_message: bool = False) -> bytes:
    lang, t = translator(project_dir)
    language_options = _language_options(lang)
    theme = _read_theme(project_dir)
    next_theme = "dark" if theme == "light" else "light"
    theme_label = "夜间模式" if theme == "light" else "日间模式"
    interest = html.escape(_read_text(project_dir / "research_interest.md"))
    report_exists = (project_dir / "reports" / "target_report.html").exists()
    msg = f'<div class="message">{message if raw_message else html.escape(message)}</div>' if message else ""
    report_link = f'<a class="button ghost" href="/report">{html.escape(t("open_report"))}</a>' if report_exists else ""
    body = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(t("app_title"))}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f5f7;
      --panel: rgba(255,255,255,.78);
      --panel-strong: rgba(255,255,255,.94);
      --text: #1d1d1f;
      --muted: #6e6e73;
      --line: rgba(60,60,67,.16);
      --blue: #007aff;
      --green: #34c759;
      --yellow: #ffcc00;
      --red: #ff3b30;
      --shadow: 0 18px 60px rgba(0,0,0,.10);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", Arial, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      background:
        radial-gradient(circle at 18% 0%, rgba(0,122,255,.16), transparent 30%),
        radial-gradient(circle at 82% 10%, rgba(52,199,89,.12), transparent 26%),
        var(--bg);
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 36px 22px 72px; }}
    .topbar {{ display:flex; justify-content:flex-end; margin-bottom: 14px; }}
    header {{ display: grid; grid-template-columns: 1.1fr .9fr; gap: 22px; align-items: end; margin-bottom: 22px; animation: rise .55s ease both; }}
    h1 {{ font-size: clamp(34px, 5vw, 64px); line-height: .96; letter-spacing: 0; margin: 0 0 14px; }}
    h2 {{ margin: 0 0 14px; font-size: 19px; letter-spacing: 0; }}
    h3 {{ margin: 18px 0 8px; }}
    p {{ line-height: 1.55; color: var(--muted); }}
    .eyebrow {{ color: var(--blue); font-weight: 700; margin-bottom: 10px; }}
    .hero-card, form, section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(22px);
    }}
    .hero-card {{ padding: 22px; }}
    .hero-stats {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 18px; }}
    .hero-stats div {{ background: rgba(255,255,255,.62); border: 1px solid var(--line); border-radius: 16px; padding: 12px; }}
    .hero-stats strong {{ display:block; font-size: 22px; }}
    .workspace {{ display: grid; grid-template-columns: minmax(0, 1.15fr) minmax(320px, .85fr); gap: 18px; align-items: start; }}
    form, section {{ padding: 20px; animation: rise .55s ease both; }}
    label {{ display: block; font-weight: 700; margin-bottom: 8px; }}
    textarea {{
      width: 100%; min-height: 178px; resize: vertical; padding: 16px;
      border: 1px solid var(--line); border-radius: 18px;
      background: var(--panel-strong); color: var(--text);
      font: 15px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace;
      outline: none; transition: border-color .2s ease, box-shadow .2s ease, transform .2s ease;
    }}
    textarea:focus {{ border-color: rgba(0,122,255,.7); box-shadow: 0 0 0 5px rgba(0,122,255,.12); transform: translateY(-1px); }}
    select {{
      width: 100%; border: 1px solid var(--line); border-radius: 15px; padding: 12px;
      background: var(--panel-strong); color: var(--text); margin-bottom: 10px;
    }}
    input[type="number"], input[type="text"] {{
      width: 100%; border: 1px solid var(--line); border-radius: 15px; padding: 12px;
      background: var(--panel-strong); color: var(--text); margin-bottom: 10px;
    }}
    .inline-control {{ display: flex; gap: 10px; align-items: center; margin: 10px 0 16px; font-weight: 500; color: var(--muted); }}
    .dataset-list {{ margin-top: 18px; }}
    .dataset-grid {{ display: grid; gap: 10px; }}
    .dataset-card {{ display: grid; grid-template-columns: auto 1fr; gap: 12px; align-items: center; margin: 10px 0; padding: 13px; border: 1px solid var(--line); border-radius: 18px; background: rgba(255,255,255,.68); cursor: pointer; transition: transform .18s ease, border-color .18s ease, background .18s ease; }}
    .dataset-card:hover {{ transform: translateY(-2px); border-color: rgba(0,122,255,.34); background: rgba(255,255,255,.88); }}
    .dataset-card input {{ display: none; }}
    .dataset-toggle {{ width: 44px; height: 27px; border-radius: 999px; background: #d1d1d6; position: relative; transition: background .2s ease; }}
    .dataset-toggle::after {{ content: ""; position: absolute; width: 23px; height: 23px; left: 2px; top: 2px; border-radius: 50%; background: white; box-shadow: 0 2px 8px rgba(0,0,0,.22); transition: transform .2s ease; }}
    .dataset-card input:checked + .dataset-toggle {{ background: var(--green); }}
    .dataset-card input:checked + .dataset-toggle::after {{ transform: translateX(17px); }}
    .dataset-copy strong, .dataset-copy small {{ display: block; }}
    .dataset-copy small, .dataset-index, .muted {{ color: var(--muted); }}
    .dataset-index {{ font-size: 12px; font-weight: 700; }}
    .actions {{ display: flex; gap: 12px; align-items: center; margin-top: 18px; flex-wrap: wrap; }}
    button, .button {{
      border: 0; border-radius: 999px; padding: 13px 18px;
      background: var(--blue); color: #fff; text-decoration: none; font-weight: 800; cursor: pointer;
      box-shadow: 0 8px 24px rgba(0,122,255,.28); transition: transform .18s ease, box-shadow .18s ease;
    }}
    button:hover, .button:hover {{ transform: translateY(-2px); box-shadow: 0 12px 32px rgba(0,122,255,.34); }}
    .ghost {{ background: rgba(255,255,255,.72); color: var(--blue); box-shadow: none; border: 1px solid var(--line); }}
    .message {{ background: rgba(52,199,89,.13); border: 1px solid rgba(52,199,89,.4); padding: 12px 14px; border-radius: 16px; margin-bottom: 14px; }}
    .side-stack {{ display: grid; gap: 18px; }}
    .spec-grid, .audit-grid, .method-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; }}
    .method-grid {{ grid-template-columns: 1fr; }}
    .spec-tile, .audit-card, .method-grid div {{ background: rgba(255,255,255,.68); border: 1px solid var(--line); border-radius: 18px; padding: 13px; }}
    .spec-tile small, .audit-card small {{ display:block; color: var(--muted); }}
    .spec-tile strong, .audit-card strong {{ display:block; margin-top: 4px; }}
    .status {{ display:grid; grid-template-columns:auto 1fr; gap: 10px; align-items:start; border-radius: 18px; padding: 14px; background: rgba(255,255,255,.68); border: 1px solid var(--line); }}
    .status span {{ width: 11px; height: 11px; border-radius: 50%; margin-top: 5px; background: var(--muted); }}
    .status.success span {{ background: var(--green); box-shadow: 0 0 0 6px rgba(52,199,89,.13); }}
    .status.failed span {{ background: var(--red); box-shadow: 0 0 0 6px rgba(255,59,48,.13); }}
    .status.running span {{ background: var(--blue); animation: pulse 1.2s ease infinite; }}
    .status p {{ margin: 3px 0 0; }}
    .pill {{ display: inline-flex; align-items:center; justify-content:center; min-width: 78px; border-radius: 999px; padding: 4px 9px; font-size: 12px; font-weight: 800; background: #e5e5ea; color: var(--muted); }}
    .pill.match, .pill.pass {{ background: rgba(52,199,89,.16); color: #1f7a35; }}
    .pill.review {{ background: rgba(255,204,0,.22); color: #8a6500; }}
    .pill.low_match, .pill.failed {{ background: rgba(255,59,48,.14); color: #b42318; }}
    .pill.candidate {{ background: rgba(52,199,89,.16); color: #1f7a35; }}
    .timeline {{ display: grid; gap: 11px; }}
    .timeline-item {{ display: grid; grid-template-columns: auto 1fr; gap: 10px; padding: 10px; border-radius: 16px; background: rgba(255,255,255,.58); border: 1px solid var(--line); }}
    .timeline-dot {{ width: 12px; height: 12px; border-radius: 50%; margin-top: 4px; background: var(--muted); }}
    .timeline-dot.pass, .timeline-dot.success {{ background: var(--green); }}
    .timeline-dot.review, .timeline-dot.blocked {{ background: var(--yellow); }}
    .timeline-dot.running {{ background: var(--blue); animation: pulse 1.2s ease infinite; }}
    .timeline-dot.failed {{ background: var(--red); }}
    .timeline-item small {{ display: block; color: var(--muted); margin-top: 3px; }}
    .idea-row, .resource-row {{ display: grid; grid-template-columns: auto 1fr; gap: 10px; align-items: start; padding: 10px; border-radius: 16px; background: rgba(255,255,255,.58); border: 1px solid var(--line); margin-bottom: 9px; }}
    .resource-row {{ grid-template-columns: 1fr auto; }}
    .idea-row small, .resource-row small {{ display:block; color: var(--muted); margin-top: 3px; }}
    .mini-form {{ background: transparent; border: 0; box-shadow: none; padding: 0; }}
    .review-form {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }}
    .review-form input[type="text"] {{ max-width: 260px; margin-bottom: 0; }}
    .secret-form {{ display: grid; gap: 8px; }}
    .small-button {{ padding: 8px 11px; box-shadow: none; font-size: 12px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 9px 6px; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ color: var(--muted); }}
    pre {{ white-space: pre-wrap; background: rgba(255,255,255,.72); border: 1px solid var(--line); border-radius: 16px; padding: 12px; overflow-x: auto; }}
    details {{ margin-top: 10px; }}
    code {{ background: rgba(118,118,128,.12); padding: 2px 5px; border-radius: 6px; }}
    /* shadcn-inspired compact product UI overrides */
    :root {{
      --bg: #f8fafc;
      --panel: #ffffff;
      --panel-strong: #ffffff;
      --text: #0f172a;
      --muted: #64748b;
      --line: #e2e8f0;
      --blue: #2563eb;
      --green: #16a34a;
      --yellow: #ca8a04;
      --red: #dc2626;
      --shadow: 0 1px 2px rgba(15,23,42,.05);
    }}
    body[data-theme="dark"] {{
      color-scheme: dark;
      --bg: #0b1120;
      --panel: #111827;
      --panel-strong: #0f172a;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --line: #243044;
      --blue: #60a5fa;
      --green: #4ade80;
      --yellow: #facc15;
      --red: #f87171;
      --shadow: 0 1px 2px rgba(0,0,0,.35);
    }}
    body {{ background: var(--bg); }}
    body[data-theme="dark"] header,
    body[data-theme="dark"] .hero-card,
    body[data-theme="dark"] form,
    body[data-theme="dark"] section,
    body[data-theme="dark"] details.app-section {{ border-color: var(--line); }}
    main {{ max-width: 1320px; padding: 18px 20px 48px; }}
    .topbar {{ align-items: center; margin-bottom: 10px; }}
    header {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: center;
      margin-bottom: 14px;
      padding: 16px 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      animation: none;
    }}
    h1 {{ font-size: 28px; line-height: 1.15; margin: 0 0 6px; }}
    h2 {{ font-size: 16px; margin-bottom: 12px; }}
    h3 {{ font-size: 14px; margin: 16px 0 8px; color: var(--text); }}
    p {{ margin: 6px 0; }}
    .eyebrow {{ margin-bottom: 6px; font-size: 12px; text-transform: uppercase; letter-spacing: .04em; }}
    .hero-card, form, section {{
      background: var(--panel);
      border-radius: 8px;
      box-shadow: var(--shadow);
      backdrop-filter: none;
    }}
    .hero-card {{ min-width: 320px; padding: 14px; }}
    .hero-stats {{ margin-top: 12px; }}
    .hero-stats div {{ padding: 10px; border-radius: 6px; }}
    .hero-stats strong {{ font-size: 16px; }}
    .hero-stats small {{ color: var(--muted); }}
    .hero-stats div, .spec-tile, .audit-card, .method-grid div {{
      border-radius: 6px;
      background: #f8fafc;
      box-shadow: none;
    }}
    .workspace {{ display: block; }}
    .run-form {{ margin-bottom: 14px; }}
    .request-layout {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) 320px;
      gap: 14px;
      align-items: stretch;
    }}
    .prompt-panel, .run-config {{
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-strong);
      padding: 12px;
    }}
    .run-config {{
      display: flex;
      flex-direction: column;
    }}
    .run-config .actions {{ margin-top: auto; }}
    form, section {{ padding: 16px; animation: none; }}
    .workflow-panel {{ margin-bottom: 14px; }}
    .section-title {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 14px;
    }}
    .section-title h2 {{ margin-bottom: 4px; }}
    .section-title p {{ margin: 0; max-width: 820px; }}
    .workflow-grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 10px;
    }}
    .workflow-step {{
      position: relative;
      min-height: 172px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel-strong);
      padding: 12px;
      overflow: hidden;
    }}
    .workflow-step::after {{
      content: "";
      position: absolute;
      top: 28px;
      right: -6px;
      width: 12px;
      height: 12px;
      border-top: 1px solid #cbd5e1;
      border-right: 1px solid #cbd5e1;
      transform: rotate(45deg);
      background: var(--panel-strong);
    }}
    .workflow-step:last-child::after {{ display: none; }}
    .workflow-step.active {{ border-color: #2563eb; box-shadow: inset 0 0 0 1px rgba(37,99,235,.18); }}
    .workflow-step.success {{ background: rgba(22,163,74,.08); border-color: rgba(22,163,74,.35); }}
    .workflow-step.failed {{ background: rgba(220,38,38,.08); border-color: rgba(220,38,38,.35); }}
    .workflow-step.review {{ background: rgba(202,138,4,.10); border-color: rgba(202,138,4,.35); }}
    .workflow-head {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 10px; }}
    .workflow-index {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      border-radius: 999px;
      background: #0f172a;
      color: #fff;
      font-weight: 900;
      font-size: 12px;
    }}
    .workflow-step strong {{ display: block; font-size: 15px; margin-bottom: 7px; }}
    .workflow-step p {{ font-size: 13px; line-height: 1.45; margin: 0 0 10px; }}
    .workflow-step small {{ display: block; color: var(--muted); line-height: 1.4; }}
    .recovery-grid {{ display: grid; gap: 10px; }}
    .recovery-card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: var(--panel-strong);
    }}
    .recovery-card-head {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: flex-start;
      margin-bottom: 8px;
    }}
    .recovery-card-head strong,
    .recovery-card-head small {{ display: block; }}
    .recovery-card ul {{ margin: 8px 0 10px 18px; padding: 0; color: var(--muted); }}
    .recovery-actions {{ display: grid; gap: 8px; margin-top: 10px; }}
    .recovery-form {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr)) auto;
      gap: 8px;
      align-items: end;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }}
    .recovery-form input {{ margin-bottom: 0; }}
    .dag-recovery-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 12px 0 18px; }}
    .dag-recovery-card {{ border: 1px solid var(--border); border-radius: 14px; padding: 14px; background: var(--surface); box-shadow: var(--shadow-soft); }}
    .dag-recovery-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 10px; }}
    .dag-recovery-head strong,
    .dag-recovery-head small {{ display: block; }}
    .dag-recovery-card table {{ margin-top: 8px; }}
    .dag-recovery-card ul {{ margin: 8px 0 10px 18px; padding: 0; color: var(--muted); }}
    .warning-box {{ border: 1px solid rgba(245, 158, 11, 0.35); background: rgba(245, 158, 11, 0.08); border-radius: 12px; padding: 10px; margin: 10px 0; }}
    textarea {{ min-height: 132px; border-radius: 8px; font: 14px/1.5 ui-monospace, SFMono-Regular, Consolas, monospace; }}
    textarea:focus {{ transform: none; box-shadow: 0 0 0 3px rgba(37,99,235,.12); }}
    select, input[type="number"], input[type="text"], input[type="password"] {{ border-radius: 8px; padding: 10px 11px; }}
    label {{ font-size: 13px; margin-bottom: 6px; }}
    .dataset-grid {{ grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }}
    .dataset-card {{ border-radius: 8px; padding: 10px; margin: 0; background: var(--panel); }}
    .dataset-card:hover {{ transform: none; border-color: #93c5fd; }}
    .dataset-toggle {{ width: 34px; height: 20px; }}
    .dataset-toggle::after {{ width: 16px; height: 16px; }}
    .dataset-card input:checked + .dataset-toggle::after {{ transform: translateX(14px); }}
    button, .button {{ border-radius: 8px; padding: 10px 13px; box-shadow: none; }}
    button:hover, .button:hover {{ transform: none; box-shadow: none; filter: brightness(.98); }}
    .ghost {{ background: var(--panel); }}
    .actions {{ gap: 8px; margin-top: 12px; }}
    .side-stack {{
      display: block;
      column-count: 3;
      column-gap: 12px;
    }}
    .side-stack section {{
      display: inline-block;
      width: 100%;
      margin: 0 0 12px;
      break-inside: avoid;
      min-width: 0;
    }}
    .side-stack section:nth-of-type(7),
    .side-stack section:nth-of-type(8),
    .side-stack section:nth-of-type(11) {{ display: none; }}
    .status, .idea-row, .resource-row, .timeline-item {{ border-radius: 8px; background: #fff; }}
    body[data-theme="dark"] .status,
    body[data-theme="dark"] .idea-row,
    body[data-theme="dark"] .resource-row,
    body[data-theme="dark"] .timeline-item,
    body[data-theme="dark"] .hero-stats div,
    body[data-theme="dark"] .spec-tile,
    body[data-theme="dark"] .audit-card,
    body[data-theme="dark"] .method-grid div {{
      background: #0f172a;
      border-color: var(--line);
    }}
    input[type="file"] {{
      width: 100%;
      border: 1px dashed var(--line);
      border-radius: 8px;
      padding: 18px;
      background: var(--panel-strong);
      color: var(--muted);
      margin-bottom: 10px;
    }}
    .pill {{ min-width: auto; border-radius: 999px; padding: 3px 8px; font-size: 11px; }}
    details.app-section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 0;
      margin: 12px 0;
    }}
    details.app-section > summary {{
      list-style: none;
      cursor: pointer;
      padding: 14px 16px;
      font-weight: 800;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    details.app-section > summary::after {{ content: "+"; color: var(--muted); font-weight: 900; }}
    details.app-section[open] > summary::after {{ content: "-"; }}
    details.app-section > .section-body {{ border-top: 1px solid var(--line); padding: 16px; }}
    .advanced-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .advanced-grid section {{ margin: 0; }}
    table {{ font-size: 13px; }}
    th, td {{ padding: 8px; }}
    @keyframes rise {{ from {{ opacity: 0; transform: translateY(14px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    @keyframes pulse {{ 0%,100% {{ opacity: .45; }} 50% {{ opacity: 1; }} }}
    @media (max-width: 860px) {{
      header, .request-layout {{ grid-template-columns: 1fr; }}
      .section-title {{ display: grid; }}
      .workflow-grid {{ grid-template-columns: 1fr; }}
      .workflow-step {{ min-height: auto; }}
      .workflow-step::after {{ display: none; }}
      .recovery-form {{ grid-template-columns: 1fr; }}
      .side-stack {{ column-count: 1; }}
      .side-stack section,
      .side-stack section:nth-of-type(4),
      .side-stack section:nth-of-type(10) {{ grid-column: auto; }}
      .spec-grid, .audit-grid, .hero-stats, .dataset-grid {{ grid-template-columns: 1fr; }}
    }}
    /* v5 product shell */
    main.app-layout {{
      max-width: 1440px;
      display: grid;
      grid-template-columns: 248px minmax(0, 1fr);
      gap: 18px;
      align-items: start;
      padding: 16px;
    }}
    .app-sidebar {{
      position: sticky;
      top: 16px;
      min-height: calc(100vh - 32px);
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
      padding: 14px;
    }}
    .brand-mark {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 4px 4px 18px;
      border-bottom: 1px solid var(--line);
      margin-bottom: 14px;
    }}
    .brand-mark span {{
      width: 38px;
      height: 38px;
      border-radius: 10px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      background: #0f172a;
      color: #fff;
      font-weight: 900;
    }}
    .brand-mark strong,
    .brand-mark small,
    .nav-group small,
    .nav-link {{
      display: block;
    }}
    .brand-mark small,
    .nav-group small {{
      color: var(--muted);
    }}
    .nav-group {{
      display: grid;
      gap: 6px;
      margin-bottom: 16px;
    }}
    .nav-group small {{
      padding: 0 8px 4px;
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .04em;
    }}
    .nav-link {{
      text-decoration: none;
      color: var(--text);
      padding: 9px 10px;
      border-radius: 8px;
      font-weight: 700;
      font-size: 14px;
    }}
    .nav-link:hover,
    .nav-link.selected {{
      background: rgba(37,99,235,.10);
      color: var(--blue);
    }}
    .product-main {{
      min-width: 0;
      display: grid;
      gap: 14px;
    }}
    .product-header {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
      gap: 14px;
      padding: 16px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 12px;
      box-shadow: var(--shadow);
    }}
    .product-header h1 {{
      font-size: 30px;
      margin: 0 0 6px;
    }}
    .product-header p {{
      max-width: 760px;
      margin: 0;
    }}
    .topbar {{
      margin: 0;
      justify-content: flex-end;
      gap: 8px;
    }}
    .command-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.08fr) minmax(340px, .92fr);
      gap: 14px;
      align-items: start;
    }}
    .run-form {{
      margin: 0;
      border-radius: 12px;
    }}
    .workflow-panel,
    .index-panel,
    .summary-panel {{
      border-radius: 12px;
    }}
    .index-grid,
    .summary-grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }}
    .index-card,
    .summary-card {{
      min-height: 118px;
      padding: 13px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: var(--panel-strong);
      text-decoration: none;
      color: var(--text);
    }}
    .index-card:hover {{
      border-color: #93c5fd;
      background: rgba(37,99,235,.06);
    }}
    .index-card span {{
      display: inline-flex;
      margin-bottom: 12px;
      border-radius: 999px;
      padding: 3px 8px;
      background: rgba(37,99,235,.10);
      color: var(--blue);
      font-size: 11px;
      font-weight: 900;
    }}
    .index-card strong,
    .summary-card strong {{
      display: block;
      margin-bottom: 6px;
      font-size: 16px;
    }}
    .index-card small,
    .summary-card p {{
      color: var(--muted);
      line-height: 1.45;
      margin: 0;
    }}
    .summary-card small {{
      display: block;
      color: var(--muted);
      margin-bottom: 8px;
      font-weight: 800;
    }}
    .hero-card,
    .side-stack,
    details.app-section {{
      display: none;
    }}
    @media (max-width: 1100px) {{
      main.app-layout {{ grid-template-columns: 1fr; }}
      .app-sidebar {{
        position: static;
        min-height: auto;
      }}
      .nav-group {{
        grid-template-columns: repeat(4, minmax(0, 1fr));
      }}
      .nav-group small {{
        grid-column: 1 / -1;
      }}
      .command-grid,
      .product-header {{
        grid-template-columns: 1fr;
      }}
      .index-grid,
      .summary-grid {{
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }}
    }}
    @media (max-width: 720px) {{
      main.app-layout {{ padding: 10px; }}
      .nav-group,
      .index-grid,
      .summary-grid {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body data-theme="{html.escape(theme)}">
<main class="app-layout">
  {_app_nav("home")}
  <div class="product-main">
  <header class="product-header">
    <div>
      <div class="eyebrow">{html.escape(t("eyebrow"))}</div>
      <h1>{html.escape(t("hero_title"))}</h1>
      <p>{html.escape(t("hero_copy"))}</p>
    </div>
    <div class="topbar">
      <form class="mini-form" method="post" action="/theme">
        <input type="hidden" name="theme" value="{html.escape(next_theme)}">
        <button class="small-button ghost" type="submit">{html.escape(theme_label)}</button>
      </form>
      <form class="mini-form" method="post" action="/language">
        <label for="home-language">{html.escape(t("switch_language"))}</label>
        <select id="home-language" name="language" onchange="this.form.submit()">{language_options}</select>
      </form>
    </div>
  </header>
  {msg}
  <div class="command-grid">
    <form class="run-form" method="post" action="/run">
      <h2>{html.escape(t("agent_request"))}</h2>
      <div class="request-layout">
        <div class="prompt-panel">
          <label for="interest">{html.escape(t("research_prompt"))}</label>
          <textarea id="interest" name="interest" required>{interest}</textarea>
        </div>
        <div class="run-config">
          <label for="parser">{html.escape(t("generation_engine"))}</label>
          <select id="parser" name="parser">{_parser_options()}</select>
          <label for="ideas">{html.escape(t("idea_volume"))}</label>
          <input id="ideas" name="ideas" type="number" min="1" max="50" value="6">
          <label class="inline-control">
            <input type="checkbox" name="confirm_spec" value="1">
            <span>{html.escape(t("confirm_spec"))}</span>
          </label>
          <div class="actions">
            <button type="submit">{html.escape(t("run_agent"))}</button>
            {report_link}
          </div>
        </div>
      </div>
      <div class="dataset-list">
        <h2>{html.escape(t("datasets_for_run"))}</h2>
        <div class="dataset-grid">{_dataset_controls(project_dir)}</div>
      </div>
    </form>
    {_home_status_summary(project_dir, lang)}
  </div>
    {_agent_workflow_panel(project_dir, lang)}
    {_home_index_cards()}
  {_home_diagnostics_panel(project_dir)}
  <section class="handoff-panel">
    <div class="section-title"><div><h2>下一步怎么走</h2><p>首页只负责引导。具体数据、证据、报告、权限和服务管理都从左侧目录或功能目录进入。</p></div></div>
    <div class="summary-grid">
      <div class="summary-card"><small>1</small><strong>输入研究问题</strong><p>先在上方运行框发起 v5 本地流程。</p></div>
      <div class="summary-card"><small>2</small><strong>检查 Agent 流程</strong><p>进入 Agent 流程页查看 handoff、task、claim ceiling。</p></div>
      <div class="summary-card"><small>3</small><strong>补数据 metadata</strong><p>进入数据集锁库页处理人工纠错和 gate。</p></div>
      <div class="summary-card"><small>4</small><strong>查看报告</strong><p>进入报告页展示候选排序、证据链和限制。</p></div>
    </div>
  </section>
  </div></main>
{_zh_ui_script()}
</body>
</html>
"""
    return body.encode("utf-8")


def _report(project_dir: Path) -> bytes:
    report = project_dir / "reports" / "target_report.html"
    if not report.exists():
        return _page(project_dir, "No report yet. Run the demo first.")
    return report.read_bytes()


def _find_available_port(host: str, preferred_port: int, attempts: int = 20) -> int:
    if preferred_port == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])
    for port in range(preferred_port, preferred_port + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise OSError(f"No available port found from {preferred_port} to {preferred_port + attempts - 1}")


def _run_partial(project_dir: Path, stage: str) -> list[str]:
    outputs = []
    if stage == "annotation":
        outputs.extend(str(path) for path in annotate_project(project_dir))
    elif stage == "enrichment":
        outputs.append(str(run_enrichment(project_dir)))
    elif stage == "evidence":
        outputs.append(str(import_evidence(project_dir)))
    elif stage == "scoring":
        outputs.append(str(score_project(project_dir)))
    elif stage == "report":
        outputs.extend(str(path) for path in build_report(project_dir))
    else:
        raise ValueError(f"unsupported partial stage: {stage}")
    return outputs


def run_server(project: str, host: str = "127.0.0.1", port: int = 8765) -> None:
    project_dir = init_project(project)
    apply_project_secrets(project_dir)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content: bytes, content_type: str = "text/html; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._send(200, b"OK", "text/plain; charset=utf-8")
            elif self.path in {"/", "/index.html"}:
                self._send(200, _page(project_dir))
            elif self.path == "/v5/flow":
                self._send(200, _v5_flow_page(project_dir))
            elif self.path == "/v5/console":
                self._send(200, _v5_console_page(project_dir))
            elif self.path == "/v5/resource-gate":
                self._send(200, _v5_resource_gate_page(project_dir))
            elif self.path == "/v5/analysis-main-path":
                self._send(200, _v5_analysis_main_path_page(project_dir))
            elif self.path == "/v5/product-report":
                self._send(200, _v5_product_report_page(project_dir))
            elif self.path == "/v5/product-report/html":
                report_file = project_dir / "v5" / "reports" / "product_report.html"
                if report_file.exists():
                    self._send(200, report_file.read_bytes())
                else:
                    self._send(404, b"Product report not built", "text/plain; charset=utf-8")
            elif self.path == "/v5/setup":
                self._send(200, _v5_setup_page(project_dir))
            elif self.path == "/v5/services":
                self._send(200, _v5_services_page(project_dir))
            elif self.path == "/v5/update":
                self._send(200, _v5_update_page(project_dir))
            elif self.path == "/v5/projects":
                self._send(200, _v5_projects_page(project_dir))
            elif self.path == "/v5/platform-readiness":
                self._send(200, _v5_platform_readiness_page(project_dir))
            elif self.path in {"/v5/platform-p2-readiness", "/v5/platform-p2"}:
                self._send(200, _v5_platform_p2_readiness_page(project_dir))
            elif self.path in {"/v5/production-readiness", "/v5/platform-production-readiness"}:
                self._send(200, _v5_production_readiness_page(project_dir))
            elif self.path == "/v5/release-acceptance":
                self._send(200, _v5_release_acceptance_page(project_dir))
            elif self.path == "/v5/access":
                self._send(200, _v5_access_page(project_dir))
            elif self.path == "/v5/storage":
                self._send(200, _v5_storage_page(project_dir))
            elif self.path.startswith("/v5/audit"):
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                self._send(200, _v5_audit_page(project_dir, source=query.get("source", ["all"])[0], status=query.get("status", [""])[0], actor=query.get("actor", [""])[0]))
            elif self.path == "/v5/cache":
                self._send(200, _v5_cache_page(project_dir))
            elif self.path == "/v5/backend-writes":
                self._send(200, _v5_backend_writes_page(project_dir))
            elif self.path.startswith("/v5/artifacts"):
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                self._send(200, _v5_artifacts_page(project_dir, selected_path=query.get("path", [""])[0]))
            elif self.path == "/v5/evidence-claims":
                self._send(200, _v5_evidence_claims_page(project_dir))
            elif self.path == "/v5/wetlab":
                self._send(200, _v5_wetlab_page(project_dir))
            elif self.path == "/v5/memory":
                self._send(200, _v5_memory_page(project_dir))
            elif self.path == "/report":
                self._send(200, _report(project_dir))
            elif self.path.startswith("/evidence-trace"):
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                self._send(
                    200,
                    _evidence_trace_detail_page(
                        project_dir,
                        gene=query.get("gene", [""])[0],
                        evidence_id=query.get("evidence_id", [""])[0],
                    ),
                )
            elif self.path.startswith("/qc-review/detail"):
                parsed = urllib.parse.urlparse(self.path)
                query = urllib.parse.parse_qs(parsed.query)
                self._send(200, _qc_review_detail_page(project_dir, query.get("item_id", [""])[0]))
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            if self.path == "/secrets/openai":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    if form.get("clear", [""])[0] == "1":
                        clear_openai_api_key(project_dir)
                        message = "LLM API key cleared."
                    else:
                        save_llm_provider(
                            project_dir,
                            form.get("llm_provider", ["openai"])[0],
                            base_url=form.get("llm_base_url", [""])[0],
                            model=form.get("llm_model", [""])[0],
                        )
                        save_openai_api_key(project_dir, form.get("openai_api_key", [""])[0])
                        message = "LLM provider and API key saved for this local project."
                    self._send(200, _page(project_dir, message))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"OpenAI API key update failed: {exc}"))
                return
            if self.path == "/language":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                set_language(project_dir, form.get("language", ["zh"])[0])
                self.send_response(303)
                self.send_header("Location", _safe_redirect_target(self.headers))
                self.end_headers()
                return
            if self.path == "/theme":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                _write_theme(project_dir, form.get("theme", ["light"])[0])
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
                return
            if self.path == "/knowledge/add":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    add_resource(
                        project_dir,
                        form.get("resource_id", [""])[0],
                        form.get("resource_type", [""])[0],
                        form.get("source_path", [""])[0],
                        form.get("adapter", ["auto"])[0],
                    )
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Knowledge resource add failed: {exc}"))
                return
            if self.path == "/knowledge/delete":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                remove_resource(project_dir, form.get("resource_id", [""])[0])
                self.send_response(303)
                self.send_header("Location", "/")
                self.end_headers()
                return
            if self.path == "/knowledge/adapt":
                try:
                    adapt_resources(project_dir)
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Knowledge adaptation failed: {exc}"))
                return
            if self.path == "/database/validate":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    genes = [item.strip() for item in form.get("genes", [""])[0].replace(";", ",").split(",") if item.strip()]
                    query = form.get("query", [""])[0].strip() or "type 2 diabetes skeletal muscle"
                    result = validate_online_databases(project_dir, genes=genes, query=query, limit=10, timeout=30, adapt=True)
                    self._send(200, _page(project_dir, f"Online database validation completed: {result['success_count']} source(s) succeeded."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Online database validation failed: {exc}"))
                return
            if self.path == "/database/retry":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    genes = [item.strip() for item in form.get("genes", [""])[0].replace(";", ",").split(",") if item.strip()]
                    sources = [item.strip() for item in form.get("sources", [""])[0].replace(";", ",").split(",") if item.strip()]
                    query = form.get("query", [""])[0].strip() or "type 2 diabetes skeletal muscle"
                    result = retry_database_sources(project_dir, sources=sources, genes=genes, query=query, limit=10, timeout=30, adapt=True)
                    self._send(200, _page(project_dir, f"Database retry completed: {result['retry_count']} source(s) recorded."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Database retry failed: {exc}"))
                return
            if self.path == "/recovery/build":
                try:
                    result = build_recovery_manifest(project_dir)
                    self._send(200, _page(project_dir, f"Recovery manifest rebuilt: {result['open_count']} open item(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Recovery manifest failed: {exc}"))
                return
            if self.path == "/cell-type-evidence/build" or self.path == "/cell-type-evidence/build-import":
                try:
                    result = build_cell_type_evidence(project_dir)
                    if self.path.endswith("build-import"):
                        import_evidence(project_dir)
                    self._send(200, _page(project_dir, f"Cell-type evidence rebuilt: {result['row_count']} row(s), {result['gene_count']} gene(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Cell-type evidence build failed: {exc}"))
                return
            if self.path == "/fulltext/upload":
                try:
                    from .fulltext_literature import run_fulltext_literature

                    length = int(self.headers.get("Content-Length", "0"))
                    fields = _parse_multipart_form(self.headers, self.rfile.read(length))
                    file_item = fields.get("pdf_file")
                    if not file_item or not file_item.get("filename"):
                        raise ValueError("choose a PDF or text file first")
                    filename = Path(str(file_item["filename"])).name
                    upload_dir = project_dir / "uploads" / "fulltext"
                    upload_dir.mkdir(parents=True, exist_ok=True)
                    upload_path = upload_dir / filename
                    content = file_item.get("content", b"")
                    upload_path.write_bytes(content if isinstance(content, bytes) else str(content).encode("utf-8"))
                    ocr = fields.get("ocr", {}).get("content", b"") in {b"1", "1"}
                    if upload_path.suffix.lower() == ".txt":
                        result = run_fulltext_literature(project_dir, text=[str(upload_path)], ocr=ocr)
                    else:
                        result = run_fulltext_literature(project_dir, pdf=[str(upload_path)], ocr=ocr)
                    build_recovery_manifest(project_dir)
                    self._send(200, _page(project_dir, f"Full-text upload parsed: {result['document_count']} document(s), {result['evidence_row_count']} evidence row(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Full-text upload failed: {exc}"))
                return
            if self.path == "/fulltext/llm-extract":
                try:
                    from .fulltext_llm_extraction import run_fulltext_llm_extraction

                    result = run_fulltext_llm_extraction(project_dir, max_docs=5)
                    build_recovery_manifest(project_dir)
                    self._send(200, _page(project_dir, f"Full-text LLM extraction completed: {result.get('evidence_row_count', 0)} evidence row(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Full-text LLM extraction failed: {exc}"))
                return
            if self.path == "/geo/discover":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    limit = int(form.get("limit", ["8"])[0] or "8")
                    payload = discover_geo_datasets(
                        project_dir,
                        limit=max(1, min(limit, 20)),
                        query=form.get("query", [""])[0],
                    )
                    self._send(
                        200,
                        _page(
                            project_dir,
                            f"GEO discovery completed: {len(payload.get('recommendations', []))} recommendation(s).",
                        ),
                    )
                except Exception as exc:
                    self._send(400, _page(project_dir, f"GEO discovery failed: {exc}"))
                return
            if self.path == "/geo/import":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    accession = form.get("accession", [""])[0].strip()
                    case_label = form.get("case_label", [""])[0].strip()
                    control_label = form.get("control_label", [""])[0].strip()
                    if not accession or not case_label or not control_label:
                        raise ValueError("accession, case label, and control label are required")
                    result = import_geo_series(
                        project_dir,
                        accession,
                        case_label,
                        control_label,
                        _split_patterns(form.get("case_patterns", [""])[0]),
                        _split_patterns(form.get("control_patterns", [""])[0]),
                        tissue=form.get("tissue", ["unknown"])[0].strip() or "unknown",
                        organism=form.get("organism", ["human"])[0].strip() or "human",
                        platform_annotation=Path(form.get("platform_annotation", [""])[0])
                        if form.get("platform_annotation", [""])[0].strip()
                        else None,
                        force_download=form.get("force_download", [""])[0] == "1",
                    )
                    self._send(
                        200,
                        _page(
                            project_dir,
                            f"Imported {result.accession}: {result.samples} samples, {result.genes} gene rows.",
                        ),
                    )
                except GeoImportError as exc:
                    self._send(400, _page(project_dir, _geo_error_message(project_dir, accession, exc)))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"GEO import failed: {exc}"))
                return
            if self.path == "/geo/import-auto":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                accession = form.get("accession", [""])[0].strip()
                try:
                    if not accession:
                        raise ValueError("accession is required")
                    result = import_geo_series_auto(
                        project_dir,
                        accession,
                        tissue=form.get("tissue", ["unknown"])[0].strip() or "unknown",
                        organism=form.get("organism", ["human"])[0].strip() or "human",
                        platform_annotation=Path(form.get("platform_annotation", [""])[0])
                        if form.get("platform_annotation", [""])[0].strip()
                        else None,
                        force_download=form.get("force_download", [""])[0] == "1",
                        case_hint=form.get("case_hint", [""])[0].strip(),
                        control_hint=form.get("control_hint", [""])[0].strip(),
                        case_label=form.get("case_label", [""])[0].strip(),
                        control_label=form.get("control_label", [""])[0].strip(),
                        min_confidence=int(form.get("min_confidence", ["55"])[0] or "55"),
                    )
                    self._send(
                        200,
                        _page(
                            project_dir,
                            f"Auto imported {result.accession}: {result.case_n} case / {result.control_n} control samples. Review group_inference.json before interpretation.",
                        ),
                    )
                except GeoImportError as exc:
                    self._send(400, _page(project_dir, _geo_error_message(project_dir, accession, exc)))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"GEO auto import failed: {exc}"))
                return
            if self.path == "/v5/run-local":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    from .canonical.local_demo_runner import run_v5_local_demo

                    question = form.get("question", [""])[0].strip() or _read_text(project_dir / "research_interest.md").strip()
                    if not question:
                        raise ValueError("question is required")
                    sources = [item.strip() for item in form.get("sources", ["geo,sra,pubmed,europe_pmc"])[0].split(",") if item.strip()]
                    result = run_v5_local_demo(project_dir, question, sources=tuple(sources))
                    self._send(
                        200,
                        _page(
                            project_dir,
                            f"v5 local demo completed: {result['status']} · TaskRun {result['task_run_ref']} · QC {result['qc_report_ref']}",
                        ),
                    )
                except Exception as exc:
                    self._send(400, _page(project_dir, f"v5 local demo failed: {exc}"))
                return
            if self.path == "/v5/resource-gate":
                try:
                    from .canonical.resource_gate import build_resource_gate_report

                    result = build_resource_gate_report(project_dir)
                    self._send(200, _page(project_dir, f"v5 resource gate rebuilt: {result.get('datasets_lockable_count', 0)} lockable dataset(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"v5 resource gate failed: {exc}"))
                return
            if self.path == "/v5/resource-correction":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    from .canonical.resource_gate import apply_resource_manual_correction, build_resource_gate_report

                    candidate_id = form.get("resource_candidate_id", [""])[0].strip()
                    if not candidate_id:
                        raise ValueError("resource_candidate_id is required")
                    apply_resource_manual_correction(
                        project_dir,
                        candidate_id,
                        group_metadata_status="case_control_selected" if form.get("group_column", [""])[0].strip() else "",
                        sample_size_status="sufficient" if form.get("sample_count", [""])[0].strip() else "",
                        organism=form.get("organism", [""])[0].strip(),
                        tissue=form.get("tissue", [""])[0].strip(),
                        platform=form.get("platform", [""])[0].strip(),
                        group_column=form.get("group_column", [""])[0].strip(),
                        case_label=form.get("case_label", [""])[0].strip(),
                        control_label=form.get("control_label", [""])[0].strip(),
                        sample_count=form.get("sample_count", [""])[0].strip(),
                        notes=form.get("notes", [""])[0].strip(),
                        actor="ui",
                    )
                    build_resource_gate_report(project_dir)
                    self._send(200, _v5_resource_gate_page(project_dir))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"v5 resource correction failed: {exc}"))
                return
            if self.path == "/v5/analysis-main-path/run":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    from .canonical.analysis_main_path import run_v5_analysis_main_path

                    max_packets_raw = form.get("max_analysis_packets", [""])[0].strip()
                    result = run_v5_analysis_main_path(
                        project_dir,
                        question=form.get("question", [""])[0].strip(),
                        accession=form.get("accession", [""])[0].strip(),
                        source=form.get("source", ["geo"])[0].strip() or "geo",
                        case_label=form.get("case_label", [""])[0].strip(),
                        control_label=form.get("control_label", [""])[0].strip(),
                        tissue=form.get("tissue", [""])[0].strip(),
                        organism=form.get("organism", [""])[0].strip(),
                        max_analysis_packets=int(max_packets_raw) if max_packets_raw else None,
                        force_download=form.get("force_download", [""])[0] == "1",
                    )
                    self._send(200, _v5_analysis_main_path_page(project_dir) if result else _page(project_dir, "v5 analysis main path finished"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"v5 analysis main path failed: {exc}"))
                return
            if self.path == "/v5/product-report/build":
                try:
                    from .canonical.product_report import build_productized_project_report

                    build_productized_project_report(project_dir)
                    self._send(200, _v5_product_report_page(project_dir))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"v5 product report failed: {exc}"))
                return
            if self.path == "/v5/backends/activate":
                try:
                    from .local_backends import activate_v5_local_backends

                    result = activate_v5_local_backends(project_dir)
                    self._send(200, _page(project_dir, f"v5 backends: {result['status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"v5 backend activation failed: {exc}"))
                return
            if self.path == "/v5/storage/refresh":
                try:
                    from .storage_migration import build_storage_migration_plan

                    result = build_backend_primary_status(project_dir)
                    migration = build_storage_migration_plan(project_dir)
                    self._send(200, _page(project_dir, f"Storage status refreshed: {result['overall_status']} · missing artifacts {migration.get('artifact_store_missing_count', 0)}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Storage status refresh failed: {exc}"))
                return
            if self.path == "/v5/storage/migrate":
                try:
                    from .storage_migration import migrate_legacy_outputs_to_primary_backends

                    result = migrate_legacy_outputs_to_primary_backends(project_dir, limit=500)
                    self._send(200, _page(project_dir, f"Storage migration: {result['status']} · artifacts {result['migrated_artifact_count']} · failures {result['failed_artifact_count']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Storage migration failed: {exc}"))
                return
            if self.path == "/v5/storage/demo-slim":
                try:
                    from .storage_migration import build_demo_slim_storage_manifest

                    result = build_demo_slim_storage_manifest(project_dir, migrate=True, limit=5000)
                    self._send(200, _page(project_dir, f"Demo slim storage: {result['status']} · effective {result['effective_registered_count']}/{result['effective_artifact_count']} · excluded historical {result['excluded_historical_legacy_count']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Demo slim storage failed: {exc}"))
                return
            if self.path == "/v5/cache/refresh":
                try:
                    result = build_data_cache_manifest(project_dir)
                    self._send(200, _page(project_dir, f"Data cache refreshed: {result['total_size_bytes']} bytes"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Data cache refresh failed: {exc}"))
                return
            if self.path == "/v5/cache/cleanup":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    result = cleanup_data_cache(
                        project_dir,
                        target=form.get("target", [""])[0],
                        dry_run=form.get("dry_run", ["1"])[0] != "0",
                    )
                    self._send(200, _page(project_dir, f"Cache cleanup {'dry-run' if result['dry_run'] else 'completed'}: {len(result['deleted_or_would_delete'])} item(s)"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Cache cleanup failed: {exc}"))
                return
            if self.path == "/v5/pilotdeck-console":
                try:
                    from .canonical.pilotdeck_console import build_pilotdeck_console

                    result = build_pilotdeck_console(project_dir)
                    self._send(200, _page(project_dir, f"PilotDeck console refreshed: {result['schema_version']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"PilotDeck console refresh failed: {exc}"))
                return
            if self.path == "/v5/setup/save":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    save_platform_config(
                        project_dir,
                        provider=form.get("llm_provider", ["deepseek"])[0],
                        base_url=form.get("llm_base_url", [""])[0],
                        model=form.get("llm_model", [""])[0],
                        api_key=form.get("openai_api_key", [""])[0],
                        ui_port=form.get("ui_port", ["8801"])[0],
                        docker_enabled=form.get("docker_enabled", [""])[0] == "1",
                        rscript_path=form.get("rscript_path", [""])[0],
                        nextflow_path=form.get("nextflow_path", [""])[0],
                    )
                    result = platform_readiness(project_dir)
                    self._send(200, _page(project_dir, f"v5 setup saved: {result['status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"v5 setup failed: {exc}"))
                return
            if self.path == "/v5/access/dashboard":
                try:
                    from .canonical.access_admin import build_access_admin_dashboard

                    result = build_access_admin_dashboard(project_dir)
                    self._send(200, _page(project_dir, f"Access dashboard refreshed: {result['readiness_status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Access dashboard failed: {exc}"))
                return
            if self.path == "/v5/services/refresh":
                try:
                    result = service_status(project_dir)
                    platform_readiness(project_dir)
                    self._send(200, _page(project_dir, f"Service status refreshed: {result['health']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Service status refresh failed: {exc}"))
                return
            if self.path == "/v5/update/manifest":
                try:
                    result = write_update_manifest(project_dir)
                    self._send(200, _page(project_dir, f"Update manifest refreshed: {result['current_version']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Update manifest failed: {exc}"))
                return
            if self.path == "/v5/release-acceptance/refresh":
                try:
                    result = build_release_acceptance_manifest(project_dir, question_count=50)
                    self._send(200, _page(project_dir, f"Release acceptance refreshed: {result['status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Release acceptance refresh failed: {exc}"))
                return
            if self.path == "/v5/projects/create":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    new_project = _safe_project_id(form.get("project_id", [""])[0])
                    if not new_project:
                        raise ValueError("project_id is required")
                    template = _safe_project_id(form.get("template_project", [""])[0])
                    created = create_project(new_project, template_project=template)
                    self._send(200, _page(project_dir, f"Project created: {html.escape(str(created))}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Project creation failed: {exc}"))
                return
            if self.path == "/v5/projects/archive":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    archive_project(form.get("project_id", [""])[0], archived=form.get("archived", ["1"])[0] == "1")
                    self._send(200, _v5_projects_page(project_dir))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Project archive failed: {exc}"))
                return
            if self.path == "/v5/projects/export":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    out = export_project(form.get("project_id", [""])[0])
                    self._send(200, _page(project_dir, f"Project exported: {html.escape(str(out))}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Project export failed: {exc}"))
                return
            if self.path == "/v5/projects/import":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    imported = import_project(form.get("zip_path", [""])[0], project_id=form.get("project_id", [""])[0])
                    self._send(200, _page(project_dir, f"Project imported: {html.escape(str(imported))}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Project import failed: {exc}"))
                return
            if self.path == "/v5/projects/delete":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    result = delete_project(form.get("project_id", [""])[0], backup=form.get("backup", ["1"])[0] == "1")
                    self._send(200, _page(project_dir, f"Project deleted: {html.escape(json.dumps(result, ensure_ascii=False))}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Project delete failed: {exc}"))
                return
            if self.path == "/v5/access/user":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    from .canonical.access_control import create_user

                    create_user(project_dir, form.get("user_id", [""])[0].strip(), form.get("display_name", [""])[0].strip())
                    self._send(200, _v5_access_page(project_dir))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Create user failed: {exc}"))
                return
            if self.path == "/v5/access/member":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    from .canonical.access_control import set_project_member

                    set_project_member(
                        project_dir,
                        form.get("user_id", [""])[0].strip(),
                        form.get("role", ["viewer"])[0],
                        status=form.get("status", ["active"])[0],
                    )
                    self._send(200, _v5_access_page(project_dir))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Set member failed: {exc}"))
                return
            if self.path == "/v5/access/token":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    from .canonical.access_control import issue_access_token

                    scopes = [item.strip() for item in form.get("scopes", [""])[0].split(",") if item.strip()] or None
                    token = issue_access_token(
                        project_dir,
                        form.get("user_id", [""])[0].strip(),
                        ttl_minutes=int(form.get("ttl_minutes", ["1440"])[0] or "1440"),
                        scopes=scopes,
                    )
                    self._send(200, _page(project_dir, "Token issued: " + html.escape(json.dumps(token, ensure_ascii=False))))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Issue token failed: {exc}"))
                return
            if self.path == "/v5/access/token/revoke":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    from .canonical.access_control import revoke_access_token

                    revoke_access_token(project_dir, form.get("token_id", [""])[0].strip(), reason=form.get("reason", [""])[0])
                    self._send(200, _v5_access_page(project_dir))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Revoke token failed: {exc}"))
                return
            if self.path == "/v5/wetlab/build":
                try:
                    from .canonical.wet_lab_protocol import build_wet_lab_protocols

                    result = build_wet_lab_protocols(project_dir, max_protocols=5)
                    self._send(200, _page(project_dir, f"Wet-lab protocols rebuilt: {result.get('protocol_count', 0)}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Wet-lab protocol build failed: {exc}"))
                return
            if self.path == "/v5/wetlab/sop":
                try:
                    from .canonical.wet_lab_protocol import build_wet_lab_sop_bundle

                    build_wet_lab_sop_bundle(project_dir, actor="web_ui", max_protocols=5)
                    self._send(200, _v5_wetlab_page(project_dir))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Wet-lab SOP build failed: {exc}"))
                return
            if self.path == "/v5/wetlab/signoff":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    from .canonical.wet_lab_protocol import signoff_wet_lab_protocol

                    signoff = signoff_wet_lab_protocol(
                        project_dir,
                        form.get("protocol_id", [""])[0],
                        signer=form.get("signer", ["human"])[0] or "human",
                        decision=form.get("decision", ["needs_revision"])[0],
                        reason=form.get("reason", [""])[0],
                    )
                    self._send(200, _page(project_dir, f"Wet-lab signoff recorded: {signoff.get('decision', '')}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Wet-lab signoff failed: {exc}"))
                return
            if self.path == "/v5/memory/refresh":
                try:
                    from .canonical.memory_palace import build_memory_audit_dashboard

                    build_memory_audit_dashboard(project_dir, actor="web_ui")
                    self._send(200, _v5_memory_page(project_dir))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Memory refresh failed: {exc}"))
                return
            if self.path == "/v5/memory/rollback-drill":
                try:
                    from .canonical.memory_palace import build_memory_audit_dashboard, run_memory_rollback_drill

                    run_memory_rollback_drill(project_dir, actor="web_ui")
                    build_memory_audit_dashboard(project_dir, actor="web_ui")
                    self._send(200, _v5_memory_page(project_dir))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Memory rollback drill failed: {exc}"))
                return
            if self.path == "/v5/memory/scenarios":
                try:
                    from .canonical.memory_palace import build_memory_audit_dashboard, run_memory_usage_scenarios

                    run_memory_usage_scenarios(project_dir, actor="web_ui")
                    build_memory_audit_dashboard(project_dir, actor="web_ui")
                    self._send(200, _v5_memory_page(project_dir))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Memory usage scenarios failed: {exc}"))
                return
            if self.path == "/review":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    record_review(
                        project_dir,
                        form.get("item_type", [""])[0],
                        form.get("item_id", [""])[0],
                        form.get("action", [""])[0],
                        form.get("note", [""])[0],
                        reason=form.get("reason", [""])[0],
                        report_ref=form.get("report_ref", [""])[0],
                    )
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Review action failed: {exc}"))
                return
            if self.path == "/approval/signoff":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    state = final_signoff(
                        project_dir,
                        signer=form.get("signer", ["human"])[0],
                        reason=form.get("reason", [""])[0],
                        status=form.get("status", ["signed_off"])[0],
                    )
                    self._send(200, _page(project_dir, f"Approval state updated: {state['status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Approval signoff failed: {exc}"))
                return
            if self.path == "/mcp/policy":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    update_policy(
                        project_dir,
                        default_role=form.get("default_role", [""])[0],
                        require_token=form.get("require_token", [""])[0] == "1",
                    )
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"MCP policy update failed: {exc}"))
                return
            if self.path == "/mcp/token":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    scopes = [s.strip() for s in form.get("scopes", [""])[0].split(",") if s.strip()]
                    token = create_token(
                        project_dir,
                        form.get("principal", [""])[0],
                        form.get("role", ["agent_reader"])[0],
                        scopes=scopes or None,
                    )
                    self._send(200, _page(project_dir, "MCP token JSON created: " + html.escape(json.dumps(token, ensure_ascii=False))))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"MCP token creation failed: {exc}"))
                return
            if self.path == "/mcp/audit-query":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    result = query_mcp_audit(
                        project_dir,
                        principal=form.get("principal", [""])[0].strip(),
                        tool_id=form.get("tool", [""])[0].strip(),
                        status=form.get("status", [""])[0].strip(),
                        limit=50,
                    )
                    out = project_dir / "v4" / "mcp_audit_last_query.json"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"MCP audit query failed: {exc}"))
                return
            if self.path == "/mcp/auth-readiness":
                try:
                    check_external_auth_readiness(project_dir)
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"MCP auth readiness failed: {exc}"))
                return
            if self.path == "/evidence-trace/query":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                gene = form.get("gene", [""])[0].strip()
                try:
                    out = project_dir / "v4" / "evidence_trace_last_query.json"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps({"gene": gene}, indent=2, ensure_ascii=False), encoding="utf-8")
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Evidence trace query failed: {exc}"))
                return
            if self.path == "/evidence-db/query":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    result = query_evidence_items(
                        project_dir,
                        gene=form.get("gene", [""])[0].strip(),
                        evidence_type=form.get("evidence_type", [""])[0].strip(),
                        source_dataset=form.get("source_dataset", [""])[0].strip(),
                        review_status=form.get("review_status", [""])[0].strip(),
                        limit=100,
                    )
                    out = project_dir / "v4" / "evidence_db_last_query.json"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
                    self._send(200, _page(project_dir, f"Evidence DB query completed: {result['match_count']} match(es)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Evidence DB query failed: {exc}"))
                return
            if self.path == "/evidence-db/migrate":
                try:
                    result = migrate_evidence_db(project_dir)
                    self._send(200, _page(project_dir, f"Evidence DB migration applied: {result['migration_id']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Evidence DB migration failed: {exc}"))
                return
            if self.path == "/evidence-db/snapshot":
                try:
                    result = build_evidence_db_snapshot(project_dir)
                    self._send(200, _page(project_dir, f"Evidence DB snapshot built: {result['row_count']} row(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Evidence DB snapshot failed: {exc}"))
                return
            if self.path == "/production/storage-readiness":
                try:
                    result = build_production_storage_readiness(project_dir)
                    self._send(200, _page(project_dir, f"Production storage readiness: {result['status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Production storage readiness failed: {exc}"))
                return
            if self.path == "/orchestration-graph":
                try:
                    from .orchestration_graph import build_typed_orchestration_graph

                    build_typed_orchestration_graph(project_dir)
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Typed orchestration graph failed: {exc}"))
                return
            if self.path == "/orchestration-run":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    from .orchestration_graph import run_typed_orchestration

                    result = run_typed_orchestration(
                        project_dir,
                        role_id=form.get("role_id", [""])[0].strip(),
                        force=form.get("force", [""])[0] == "1",
                        actor="ui",
                    )
                    self._send(200, _page(project_dir, f"Typed orchestration run completed: {result['status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Typed orchestration run failed: {exc}"))
                return
            if self.path == "/consistency-check":
                try:
                    result = run_consistency_check(project_dir)
                    self._send(200, _page(project_dir, f"Consistency check completed: {result['status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Consistency check failed: {exc}"))
                return
            if self.path == "/codex/release-gate":
                try:
                    from .engineering_release import build_engineering_release_gate

                    result = build_engineering_release_gate(project_dir)
                    self._send(200, _page(project_dir, f"Codex release gate: {result['status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Codex release gate failed: {exc}"))
                return
            if self.path == "/codex/sbom":
                try:
                    from .engineering_release import build_sbom_manifest

                    result = build_sbom_manifest(project_dir)
                    self._send(200, _page(project_dir, f"SBOM contract built: {result['status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"SBOM contract failed: {exc}"))
                return
            if self.path == "/codex/merge-result":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    from .codex_engineering import apply_approved_codex_result

                    result = apply_approved_codex_result(
                        project_dir,
                        form.get("result_id", [""])[0].strip(),
                        actor="ui",
                        dry_run=form.get("dry_run", [""])[0] in {"1", "true", "yes"},
                    )
                    self._send(200, _page(project_dir, f"Codex merge: {result.get('status', '')}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Codex merge failed: {exc}"))
                return
            if self.path == "/codex-queue/sync":
                try:
                    result = sync_codex_task_queue(project_dir)
                    self._send(200, _page(project_dir, f"Codex task queue synced: {result.get('task_count', 0)} task(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Codex task queue sync failed: {exc}"))
                return
            if self.path == "/codex-queue/claim":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    result = claim_codex_task(
                        project_dir,
                        worker_id=form.get("worker_id", ["ui_codex_worker"])[0] or "ui_codex_worker",
                        task_id=form.get("task_id", [""])[0].strip(),
                    )
                    if result.get("claimed"):
                        message = f"Codex task claimed: {result.get('task', {}).get('task_id', '')}"
                    else:
                        message = f"Codex task claim skipped: {result.get('reason', 'no claimable task')}"
                    self._send(200, _page(project_dir, message))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Codex task claim failed: {exc}"))
                return
            if self.path == "/codex-queue/execute":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    result = execute_codex_queue_task(
                        project_dir,
                        task_id=form.get("task_id", [""])[0].strip(),
                        worker_id=form.get("worker_id", ["ui_codex_worker"])[0] or "ui_codex_worker",
                        force=form.get("force", [""])[0] in {"1", "true", "yes"},
                    )
                    task = result.get("task", {})
                    self._send(200, _page(project_dir, f"Codex task execution finished: {task.get('task_id', '')} {task.get('status', result.get('status', 'unknown'))}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Codex task execution failed: {exc}"))
                return
            if self.path == "/codex-queue/run":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    limit = int(form.get("limit", ["1"])[0] or "1")
                    result = execute_codex_queue(
                        project_dir,
                        worker_id=form.get("worker_id", ["ui_codex_worker"])[0] or "ui_codex_worker",
                        limit=max(1, min(limit, 20)),
                        force=form.get("force", [""])[0] in {"1", "true", "yes"},
                    )
                    self._send(200, _page(project_dir, f"Codex queue run finished: {result.get('executed_count', 0)} task(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Codex queue run failed: {exc}"))
                return
            if self.path == "/qc-review/build":
                try:
                    result = build_qc_review_queue(project_dir)
                    self._send(200, _page(project_dir, f"QC review queue built: {result.get('queue_count', 0)} item(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"QC review queue failed: {exc}"))
                return
            if self.path == "/qc-review/apply":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    result = apply_qc_review(
                        project_dir,
                        form.get("work_order_id", [""])[0].strip(),
                        form.get("action", ["needs_review"])[0],
                        form.get("reason", [""])[0],
                        reviewer="human",
                        report_ref=form.get("report_ref", [""])[0],
                    )
                    self._send(200, _page(project_dir, f"QC review recorded: {result['review']['action']} · evidence rows updated: {result['evidence_update']['updated_rows']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"QC review failed: {exc}"))
                return
            if self.path == "/qc-review/batch":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    result = apply_qc_review_batch(
                        project_dir,
                        [item.strip() for item in form.get("work_order_id", []) if item.strip()],
                        form.get("action", ["needs_review"])[0],
                        form.get("reason", [""])[0],
                        reviewer="human",
                    )
                    self._send(
                        200,
                        _page(
                            project_dir,
                            f"QC batch review recorded: {result['reviewed_count']} item(s), {result['error_count']} error(s). Downstream refresh errors: {len(result.get('downstream_refresh', {}).get('errors', []))}",
                        ),
                    )
                except Exception as exc:
                    self._send(400, _page(project_dir, f"QC batch review failed: {exc}"))
                return
            if self.path == "/observability/build":
                try:
                    result = build_observability_manifest(project_dir)
                    self._send(200, _page(project_dir, f"Observability manifest built: {result['mode']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Observability manifest failed: {exc}"))
                return
            if self.path == "/services/topology":
                try:
                    result = build_service_topology(project_dir)
                    self._send(200, _page(project_dir, f"Service topology built: {result['mode']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Service topology failed: {exc}"))
                return
            if self.path == "/nextflow/tasks":
                try:
                    result = build_nextflow_tasks(project_dir)
                    self._send(200, _page(project_dir, f"Nextflow tasks generated: {result['task_count']} task(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Nextflow task generation failed: {exc}"))
                return
            if self.path == "/nextflow/run":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    result = run_nextflow_local(project_dir, profile="local", resume=form.get("resume", [""])[0] == "1")
                    self._send(200, _page(project_dir, f"Nextflow run finished: {result['status']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Nextflow run failed: {exc}"))
                return
            if self.path == "/adapter-audit":
                try:
                    build_adapter_audit(project_dir)
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Adapter audit failed: {exc}"))
                return
            if self.path == "/export-package":
                try:
                    path = export_run_package(project_dir)
                    self._send(200, _page(project_dir, f"Run package exported: {path}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Run package export failed: {exc}"))
                return
            if self.path == "/reset-demo":
                try:
                    removed = reset_demo_outputs(project_dir)
                    self._send(200, _page(project_dir, f"Demo outputs reset. Removed {len(removed)} item(s)."))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Reset demo failed: {exc}"))
                return
            if self.path == "/methods":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    save_method_config(
                        project_dir,
                        {
                            "query": form.get("query", [""])[0],
                            "audit": form.get("audit", [""])[0],
                            "experiment": form.get("experiment", [""])[0],
                            "disease_normalizer": form.get("disease_normalizer", [""])[0],
                            "dataset_scout": form.get("dataset_scout", [""])[0],
                            "planner": form.get("planner", [""])[0],
                            "method_reviewer": form.get("method_reviewer", [""])[0],
                            "result_reviewer": form.get("result_reviewer", [""])[0],
                            "causal_reviewer": form.get("causal_reviewer", [""])[0],
                            "report_writer": form.get("report_writer", [""])[0],
                        },
                    )
                    _save_role_model_config(
                        project_dir,
                        {
                            "disease_normalizer": form.get("model__disease_normalizer", [""])[0],
                            "dataset_scout": form.get("model__dataset_scout", [""])[0],
                            "planner": form.get("model__planner", [""])[0],
                            "method_reviewer": form.get("model__method_reviewer", [""])[0],
                            "result_reviewer": form.get("model__result_reviewer", [""])[0],
                            "causal_reviewer": form.get("model__causal_reviewer", [""])[0],
                            "report_writer": form.get("model__report_writer", [""])[0],
                        },
                    )
                    _save_role_execution_backend_config(
                        project_dir,
                        {
                            "disease_normalizer": form.get("backend__disease_normalizer", ["auto"])[0],
                            "dataset_scout": form.get("backend__dataset_scout", ["auto"])[0],
                            "planner": form.get("backend__planner", ["auto"])[0],
                            "method_reviewer": form.get("backend__method_reviewer", ["auto"])[0],
                            "result_reviewer": form.get("backend__result_reviewer", ["auto"])[0],
                            "causal_reviewer": form.get("backend__causal_reviewer", ["auto"])[0],
                            "report_writer": form.get("backend__report_writer", ["auto"])[0],
                        },
                    )
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Method configuration failed: {exc}"))
                return
            if self.path == "/methods/upload":
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    fields = _parse_multipart_form(self.headers, self.rfile.read(length))
                    stage = fields.get("stage", {}).get("content", b"query")
                    if isinstance(stage, bytes):
                        stage = stage.decode("utf-8").strip()
                    file_item = fields.get("method_file")
                    if not file_item or not file_item.get("filename"):
                        raise ValueError("choose a markdown file first")
                    content = file_item.get("content", b"")
                    text = content.decode("utf-8") if isinstance(content, bytes) else str(content)
                    installed = install_markdown_method(project_dir, str(stage), str(file_item["filename"]), text)
                    config = load_method_config(project_dir)
                    config[stage] = installed["method_id"]
                    save_method_config(project_dir, config)
                    self._send(200, _page(project_dir, f"Markdown method registered and selected: {installed['method_id']}"))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Markdown method upload failed: {exc}"))
                return
            if self.path == "/methods/delete":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    method_id = form.get("method_id", [""])[0]
                    delete_markdown_method(project_dir, method_id)
                    config = load_method_config(project_dir)
                    changed = False
                    for stage, selected in list(config.items()):
                        if selected == method_id:
                            config.pop(stage, None)
                            changed = True
                    if changed:
                        save_method_config(project_dir, config)
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self.end_headers()
                except Exception as exc:
                    self._send(400, _page(project_dir, f"Markdown method delete failed: {exc}"))
                return
            if self.path == "/run/cancel":
                request_cancel(project_dir)
                self._send(200, _page(project_dir, "Cancel requested. The current run will stop at the next stage boundary."))
                return
            if self.path == "/run/rerun":
                status = read_status(project_dir)
                last = status.get("last_request") or {}
                if not last:
                    self._send(400, _page(project_dir, "No previous request is available to rerun."))
                    return
                form = {
                    "interest": [last.get("interest", "")],
                    "parser": [last.get("parser", "rule_based")],
                    "ideas": [str(last.get("idea_count", 6))],
                    "confirm_spec": ["1" if last.get("confirmed") else ""],
                    "dataset": last.get("selected_datasets", []),
                }
                raw = None
            elif self.path == "/run/partial":
                length = int(self.headers.get("Content-Length", "0"))
                form_partial = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                stage = form_partial.get("partial_stage", [""])[0]
                try:
                    result = partial_rerun_orchestrator(project_dir, stage, actor="ui")
                    self._send(200, _page(project_dir, f"Partial recompute completed through Orchestrator: {result['status']}"))
                except Exception as exc:
                    write_status(project_dir, "failed", f"Partial recompute failed: {stage}", failure_reason=str(exc), active_stage=stage)
                    self._send(400, _page(project_dir, f"Partial recompute failed: {exc}"))
                return
            if self.path not in {"/run", "/run/rerun"}:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            if self.path == "/run":
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                form = urllib.parse.parse_qs(raw)
            interest = form.get("interest", [""])[0]
            parser = form.get("parser", ["rule_based"])[0]
            idea_count = int(form.get("ideas", ["6"])[0] or "6")
            confirmed = form.get("confirm_spec", [""])[0] == "1"
            selected_datasets = form.get("dataset", [])
            run_id = new_run_id()
            last_request = {
                "interest": interest,
                "parser": parser,
                "idea_count": idea_count,
                "confirmed": confirmed,
                "selected_datasets": selected_datasets,
            }
            if not interest.strip():
                write_status(project_dir, "failed", "Research direction is required.", run_id=run_id, last_request=last_request, failure_reason="missing research direction")
                self._send(400, _page(project_dir, "Research direction is required."))
                return
            if not selected_datasets:
                write_status(project_dir, "failed", "Select at least one dataset.", run_id=run_id, last_request=last_request, failure_reason="no dataset selected")
                self._send(400, _page(project_dir, "Select at least one dataset."))
                return
            with lock:
                write_status(project_dir, "running", "Workflow is running.", run_id=run_id, last_request=last_request, active_stage="generation")
                try:
                    result = TargetDiscoveryAgent(project_dir.name).run(
                        interest, parser, selected_datasets, confirmed, idea_count
                    )
                except Exception as exc:
                    message = f"Agent workflow failed: {exc}"
                    write_status(project_dir, "failed", message, run_id=run_id, last_request=last_request, failure_reason=str(exc))
                    self._send(500, _page(project_dir, message))
                    return
                write_status(
                    project_dir,
                    result.status,
                    result.message,
                    result.stdout,
                    result.stderr,
                    [stage.__dict__ for stage in result.stages],
                    run_id=run_id,
                    last_request=last_request,
                    failure_reason="" if result.status == "success" else result.message,
                )
            if result.status != "success":
                self._send(500, _page(project_dir, "Workflow failed. See run status below."))
            else:
                self.send_response(303)
                self.send_header("Location", "/report")
                self.end_headers()

        def log_message(self, format, *args):
            return

    actual_port = _find_available_port(host, port)
    print(f"Serving TargetCompass Lite at http://{host}:{actual_port}/")
    ThreadingHTTPServer((host, actual_port), Handler).serve_forever()
