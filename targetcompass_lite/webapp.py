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
from .db_adapters import available_database_adapters
from .enrichment import run_enrichment
from .evidence_db import import_evidence
from .experiment_design import design_experiments
from .geo_discovery import discover_geo_datasets, load_recommendations
from .geo_importer import GeoImportError, geo_status_path, import_geo_series, import_geo_series_auto
from .ideas import load_ideas
from .i18n import set_language, translator
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
from .package import export_run_package
from .reporting import build_report
from .review import build_review_queue, final_signoff, load_approval_state, load_reviews, record_review
from .reset_demo import reset_demo_outputs
from .run_state import new_run_id, read_status, request_cancel, write_status
from .scoring import score_project
from .secrets import apply_project_secrets, clear_openai_api_key, masked_openai_key, save_openai_api_key
from .status_ui import build_status_center
from .system_status import system_status
from .validators import load_dataset_card
from .v4 import build_v4_manifest, load_codex_task_packet, load_v4_work_orders, read_work_order_attempts


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
        f'</div><h3>Stage cards</h3><div class="audit-grid">{stage_cards}</div><h3>Recovery center</h3>{recovery_cards}{geo_table}{logs}{controls}'
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
    if not resources:
        return '<p class="muted">No custom knowledge or database resources registered.</p>'
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
    return "".join(rows)


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
        "</div>"
    )


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
        return '<p class="muted">No v4 WorkOrders yet. Run planning or Agent workflow first.</p>'
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
    return "".join(cards) + attempt_table + resource_table


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
    return (
        '<div class="method-grid">'
        f'<div><small>OpenAI API key</small><strong>{html.escape(masked_openai_key(project_dir))}</strong></div>'
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


def _page(project_dir: Path, message: str = "") -> bytes:
    lang, t = translator(project_dir)
    next_lang = "en" if lang == "zh" else "zh"
    theme = _read_theme(project_dir)
    next_theme = "dark" if theme == "light" else "light"
    theme_label = "夜间模式" if theme == "light" else "日间模式"
    interest = html.escape(_read_text(project_dir / "research_interest.md"))
    report_exists = (project_dir / "reports" / "target_report.html").exists()
    msg = f'<div class="message">{html.escape(message)}</div>' if message else ""
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
  </style>
</head>
<body data-theme="{html.escape(theme)}">
<main>
  <div class="topbar">
    <form class="mini-form" method="post" action="/theme">
      <input type="hidden" name="theme" value="{html.escape(next_theme)}">
      <button class="small-button ghost" type="submit">{html.escape(theme_label)}</button>
    </form>
    <form class="mini-form" method="post" action="/language">
      <input type="hidden" name="language" value="{next_lang}">
      <button class="small-button ghost" type="submit">{html.escape(t("switch_language"))}</button>
    </form>
  </div>
  <header>
    <div>
      <div class="eyebrow">{html.escape(t("eyebrow"))}</div>
      <h1>{html.escape(t("hero_title"))}</h1>
      <p>{html.escape(t("hero_copy"))}</p>
    </div>
    <div class="hero-card">
      <strong>{html.escape(t("demo_title"))}</strong>
      <p>{html.escape(t("demo_copy"))}</p>
      <div class="hero-stats">
        <div><strong>3</strong><small>datasets</small></div>
        <div><strong>SQLite</strong><small>evidence</small></div>
        <div><strong>Review</strong><small>gates</small></div>
      </div>
    </div>
  </header>
  {msg}
  {_agent_workflow_panel(project_dir, lang)}
  <div class="workspace">
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
    <div class="side-stack">
      <section>
        <h2>{html.escape(t("api_key"))}</h2>
        {_api_key_panel(project_dir)}
        <form class="mini-form secret-form" method="post" action="/secrets/openai">
          <label for="openai_api_key">{html.escape(t("openai_api_key"))}</label>
          <input id="openai_api_key" name="openai_api_key" type="password" placeholder="sk-...">
          <div class="actions">
            <button type="submit">{html.escape(t("save_key"))}</button>
            <button class="ghost" name="clear" value="1" type="submit">{html.escape(t("clear_key"))}</button>
          </div>
        </form>
      </section>
      <section>
        <h2>{html.escape(t("system_status"))}</h2>
        <div class="audit-grid">{_system_status_panel(project_dir)}</div>
      </section>
      <section>
        <h2>{html.escape(t("replaceable_methods"))}</h2>
        {_method_panel(project_dir)}
      </section>
      <section>
        <h2>{html.escape(t("run_status"))}</h2>
        {_run_status(project_dir)}
      </section>
      <section>
        <h2>{html.escape(t("structured_spec"))}</h2>
        <div class="spec-grid">{_spec_summary(project_dir)}</div>
      </section>
      <section>
        <h2>{html.escape(t("audit_gates"))}</h2>
        <div class="audit-grid">{_audit_panel(project_dir)}</div>
      </section>
      <section>
        <h2>{html.escape(t("agent_trace"))}</h2>
        <div class="timeline">{_agent_trace(project_dir)}</div>
      </section>
      <section>
        <h2>{html.escape(t("idea_feasibility"))}</h2>
        {_idea_review_panel(project_dir)}
      </section>
      <section>
        <h2>{html.escape(t("manual_review"))}</h2>
        {_review_panel(project_dir)}
      </section>
      <section>
        <h2>Approval workflow</h2>
        {_approval_panel(project_dir)}
      </section>
      <section>
        <h2>v4 WorkOrders / Codex tasks</h2>
        {_v4_work_order_panel(project_dir)}
      </section>
      <section>
        <h2>{html.escape(t("experiment_designs"))}</h2>
        {_experiment_panel(project_dir)}
      </section>
    </div>
  </div>
  <details class="app-section">
    <summary>Advanced workspace</summary>
    <div class="section-body advanced-grid">
      <section>
        <h2>{html.escape(t("method_config"))}</h2>
        <form class="mini-form" method="post" action="/methods">
          <label for="query_method">Intermediate query method</label>
          <select id="query_method" name="query">{_method_select(project_dir, "query")}</select>
          <label for="audit_method">Feasibility / review method</label>
          <select id="audit_method" name="audit">{_method_select(project_dir, "audit")}</select>
          <label for="experiment_method">Experiment design method</label>
          <select id="experiment_method" name="experiment">{_method_select(project_dir, "experiment")}</select>
          <div class="actions">
            <button type="submit">{html.escape(t("save_methods"))}</button>
          </div>
        </form>
        <h3>Markdown skill / agent methods</h3>
        {_markdown_method_panel(project_dir)}
      </section>
      <section>
        <h2>{html.escape(t("delivery_package"))}</h2>
        <form class="mini-form" method="post" action="/export-package">
          <div class="actions">
            <button type="submit">{html.escape(t("export_package"))}</button>
          </div>
        </form>
        <form class="mini-form" method="post" action="/reset-demo">
          <div class="actions">
            <button class="ghost" type="submit">{html.escape(t("reset_demo_button"))}</button>
          </div>
        </form>
      </section>
      <section>
        <h2>{html.escape(t("knowledge_registry"))}</h2>
        {_knowledge_panel(project_dir)}
        <form class="mini-form" method="post" action="/knowledge/add">
          <label for="resource_id">Resource id</label>
          <input id="resource_id" name="resource_id" type="text" placeholder="custom_gene_set_v1">
          <label for="resource_type">Resource type</label>
          <select id="resource_type" name="resource_type">
            <option value="dataset_card">dataset_card</option>
            <option value="annotation_table">annotation_table</option>
            <option value="gene_set">gene_set</option>
            <option value="literature_card">literature_card</option>
            <option value="external_database">external_database</option>
          </select>
          <label for="adapter">Database adapter</label>
          <select id="adapter" name="adapter">
            {_database_adapter_options()}
          </select>
          <label for="source_path">Local source path</label>
          <input id="source_path" name="source_path" type="text" placeholder="D:/path/to/resource.tsv">
          <div class="actions">
            <button type="submit">{html.escape(t("add_resource"))}</button>
            <button type="submit" formaction="/knowledge/adapt">{html.escape(t("adapt_resources"))}</button>
            <button type="submit" formaction="/adapter-audit">{html.escape(t("build_adapter_audit"))}</button>
          </div>
        </form>
      </section>
      <section>
        <h2>GEO / GSE recovery center</h2>
        <p class="muted">集中查看真实数据导入失败原因、恢复建议，并直接重试或改为手动分组。</p>
        {_geo_recovery_center(project_dir)}
        <h3>GEO / GSE import tools</h3>
        {_geo_import_panel()}
        {_geo_recommendation_panel(project_dir)}
        {_geo_import_form()}
      </section>
    </div>
  </details>
  <details class="app-section">
    <summary>{html.escape(t("dataset_match_review"))}</summary>
    <div class="section-body">{_match_summary(project_dir)}</div>
  </details>
</main>
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
            if self.path in {"/", "/index.html"}:
                self._send(200, _page(project_dir))
            elif self.path == "/report":
                self._send(200, _report(project_dir))
            else:
                self._send(404, b"Not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            if self.path == "/secrets/openai":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                try:
                    if form.get("clear", [""])[0] == "1":
                        clear_openai_api_key(project_dir)
                        message = "OpenAI API key cleared."
                    else:
                        save_openai_api_key(project_dir, form.get("openai_api_key", [""])[0])
                        message = "OpenAI API key saved for this local project."
                    self._send(200, _page(project_dir, message))
                except Exception as exc:
                    self._send(400, _page(project_dir, f"OpenAI API key update failed: {exc}"))
                return
            if self.path == "/language":
                length = int(self.headers.get("Content-Length", "0"))
                form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
                set_language(project_dir, form.get("language", ["zh"])[0])
                self.send_response(303)
                self.send_header("Location", "/")
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
                    outputs = _run_partial(project_dir, stage)
                    write_status(
                        project_dir,
                        "success",
                        f"Partial recompute completed: {stage}",
                        stdout="\n".join(outputs),
                        active_stage=stage,
                    )
                    self._send(200, _page(project_dir, f"Partial recompute completed: {stage}"))
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
