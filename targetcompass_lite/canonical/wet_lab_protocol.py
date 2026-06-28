from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .backend_writer import write_json_artifact
from .ids import make_stable_id
from .schemas import now_iso


PROTOCOL_SCHEMA = "v5.wet_lab_protocol/0.1"
SIGNOFF_SCHEMA = "v5.wet_lab_protocol_signoff/0.1"
PROTOCOL_BUNDLE_SCHEMA = "v5.wet_lab_protocol_bundle/0.1"
SOP_BUNDLE_SCHEMA = "v5.wet_lab_sop_bundle/0.1"


def build_wet_lab_protocols(project_dir: str | Path, *, actor: str = "protocol_builder", max_protocols: int = 5) -> dict[str, Any]:
    project_dir = Path(project_dir)
    candidates = _load_candidates(project_dir)[: max(1, max_protocols)]
    experiment_designs = _read_json(project_dir / "results" / "experiments" / "experiment_designs.json", [])
    protocols = []
    for idx, candidate in enumerate(candidates, 1):
        gene = candidate.get("gene") or candidate.get("symbol") or f"candidate_{idx}"
        design = _matching_design(experiment_designs, gene)
        risk = _risk_grade(candidate, design)
        protocol = {
            "schema_version": PROTOCOL_SCHEMA,
            "protocol_id": make_stable_id("wet_lab_protocol", {"project": project_dir.name, "gene": gene, "candidate": candidate, "design": design}),
            "protocol_version": 1,
            "project_id": project_dir.name,
            "candidate_gene": gene,
            "title": f"Controlled validation draft for {gene}",
            "objective": design.get("objective") or f"Validate whether {gene} is associated with the scoped phenotype and accessible route.",
            "protocol_type": "review_required_validation_draft",
            "evidence_refs": _evidence_refs(candidate),
            "artifact_refs": _artifact_refs(project_dir),
            "risk_grade": risk["grade"],
            "risk_reasons": risk["reasons"],
            "high_level_workflow": [
                "Confirm sample/model suitability and inclusion/exclusion criteria.",
                "Validate expression or protein accessibility with an orthogonal assay.",
                "Compare disease/control or high/low SASP groups using a pre-specified statistical plan.",
                "Record negative, failed, and inconclusive outcomes in Evidence DB before reporting.",
            ],
            "required_controls": [
                "biological replicate control",
                "technical replicate control",
                "negative/isotype or assay-specific negative control",
                "batch and donor metadata control",
            ],
            "required_readouts": design.get("readouts") or _default_readouts(candidate),
            "decision_points": [
                "Reject or revise if input evidence lacks reviewed EvidenceItem references.",
                "Reject or revise if cell/tissue context does not match the project scope.",
                "Reject or revise if risk controls are incomplete for the proposed assay class.",
                "Approve only as a validation plan; do not convert into therapeutic or clinical claims.",
            ],
            "approval_requirements": {
                "minimum_approvals": 1,
                "allowed_approver_roles": ["pi", "admin", "reviewer"],
                "reason_required": True,
                "reject_on_high_risk_without_revision": risk["grade"] == "high",
            },
            "exclusions": [
                "No therapeutic claim is allowed from this protocol draft.",
                "No wet-lab execution should start before human signoff.",
                "No unsupported claim may exceed the canonical report claim ceiling.",
            ],
            "human_review_gate": {"required": True, "status": "pending_signoff", "reason": "Wet-lab protocol drafts require PI or reviewer signoff."},
            "created_by": actor,
            "created_at": now_iso(),
        }
        protocols.append(protocol)
    manifest = {
        "schema_version": "v5.wet_lab_protocol_manifest/0.1",
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "created_by": actor,
        "protocol_count": len(protocols),
        "protocols": protocols,
        "signoff_summary": summarize_wet_lab_protocol_signoffs(project_dir, protocols),
        "status": "review_required" if protocols else "no_candidates",
    }
    out = _protocol_dir(project_dir) / "wet_lab_protocol_manifest.json"
    _write_json(out, manifest)
    return manifest


def signoff_wet_lab_protocol(project_dir: str | Path, protocol_id: str, *, signer: str, decision: str, reason: str) -> dict[str, Any]:
    if decision not in {"approved", "rejected", "needs_revision"}:
        raise ValueError("decision must be approved, rejected, or needs_revision")
    if not reason.strip():
        raise ValueError("wet-lab protocol signoff reason is required")
    project_dir = Path(project_dir)
    manifest = _read_json(_protocol_dir(project_dir) / "wet_lab_protocol_manifest.json", {})
    protocols = manifest.get("protocols", [])
    if not any(row.get("protocol_id") == protocol_id for row in protocols):
        raise ValueError(f"unknown protocol_id: {protocol_id}")
    protocol = next((row for row in protocols if row.get("protocol_id") == protocol_id), {})
    signoff = {
        "schema_version": SIGNOFF_SCHEMA,
        "signoff_id": make_stable_id("wet_lab_protocol_signoff", {"project": project_dir.name, "protocol_id": protocol_id, "signer": signer, "decision": decision, "reason": reason, "time": now_iso()}),
        "project_id": project_dir.name,
        "protocol_id": protocol_id,
        "protocol_version": protocol.get("protocol_version", 1),
        "candidate_gene": protocol.get("candidate_gene", ""),
        "signer": signer,
        "decision": decision,
        "reason": reason,
        "created_at": now_iso(),
    }
    path = _protocol_dir(project_dir) / "signoffs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(signoff, ensure_ascii=False) + "\n")
    _write_signoff_bundle(project_dir, protocols)
    return signoff


def load_wet_lab_signoffs(project_dir: str | Path) -> list[dict[str, Any]]:
    path = _protocol_dir(Path(project_dir)) / "signoffs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_signoff_bundle(project_dir: Path, protocols: list[dict[str, Any]]) -> dict[str, Any]:
    summary = summarize_wet_lab_protocol_signoffs(project_dir, protocols)
    payload = {
        "schema_version": "v5.wet_lab_protocol_signoff_bundle/0.1",
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "signoffs": load_wet_lab_signoffs(project_dir),
        "summary": summary,
    }
    _write_json(_protocol_dir(project_dir) / "wet_lab_protocol_signoff_bundle.json", payload)
    return payload


def summarize_wet_lab_protocol_signoffs(project_dir: str | Path, protocols: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    project_dir = Path(project_dir)
    protocols = protocols if protocols is not None else _read_json(_protocol_dir(project_dir) / "wet_lab_protocol_manifest.json", {}).get("protocols", [])
    signoffs = load_wet_lab_signoffs(project_dir)
    by_protocol: dict[str, list[dict[str, Any]]] = {}
    for row in signoffs:
        by_protocol.setdefault(row.get("protocol_id", ""), []).append(row)
    rows = []
    for protocol in protocols:
        history = by_protocol.get(protocol.get("protocol_id", ""), [])
        latest = history[-1] if history else {}
        decision = latest.get("decision", "pending")
        rows.append(
            {
                "protocol_id": protocol.get("protocol_id", ""),
                "candidate_gene": protocol.get("candidate_gene", ""),
                "risk_grade": protocol.get("risk_grade", ""),
                "approval_state": "signed_out" if decision == "approved" else "rejected" if decision == "rejected" else "needs_revision" if decision == "needs_revision" else "pending",
                "latest_decision": decision,
                "signoff_count": len(history),
                "latest_signoff_id": latest.get("signoff_id", ""),
            }
        )
    return {
        "schema_version": "v5.wet_lab_protocol_signoff_summary/0.1",
        "project_id": project_dir.name,
        "protocol_count": len(protocols),
        "signed_out_count": sum(1 for row in rows if row["approval_state"] == "signed_out"),
        "pending_count": sum(1 for row in rows if row["approval_state"] == "pending"),
        "needs_revision_count": sum(1 for row in rows if row["approval_state"] == "needs_revision"),
        "rejected_count": sum(1 for row in rows if row["approval_state"] == "rejected"),
        "rows": rows,
    }


def build_wet_lab_protocol_bundle(project_dir: str | Path, *, actor: str = "protocol_builder", max_protocols: int = 5) -> dict[str, Any]:
    project_dir = Path(project_dir)
    manifest_path = _protocol_dir(project_dir) / "wet_lab_protocol_manifest.json"
    manifest = _read_json(manifest_path, {})
    if not manifest.get("protocols") or _protocols_need_upgrade(manifest.get("protocols", [])):
        manifest = build_wet_lab_protocols(project_dir, actor=actor, max_protocols=max_protocols)
    protocols = manifest.get("protocols", [])
    summary = summarize_wet_lab_protocol_signoffs(project_dir, protocols)
    bundle = {
        "schema_version": PROTOCOL_BUNDLE_SCHEMA,
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "created_by": actor,
        "manifest_ref": "v5/wet_lab_protocols/wet_lab_protocol_manifest.json",
        "signoffs_ref": "v5/wet_lab_protocols/signoffs.jsonl",
        "protocol_count": len(protocols),
        "protocols": protocols,
        "signoff_summary": summary,
        "status": "signed_out" if protocols and summary.get("signed_out_count") == len(protocols) else "review_required" if protocols else "no_candidates",
        "safety_notice": "This bundle is a controlled validation-plan artifact. It is not a step-by-step wet-lab SOP and requires qualified human approval before any experiment.",
    }
    _write_json(_protocol_dir(project_dir) / "wet_lab_protocol_bundle.json", bundle)
    return bundle


def build_wet_lab_sop_bundle(project_dir: str | Path, *, actor: str = "sop_builder", max_protocols: int = 5) -> dict[str, Any]:
    """Build auditable SOP governance records without authorizing experiment execution."""
    project_dir = Path(project_dir)
    bundle = build_wet_lab_protocol_bundle(project_dir, actor=actor, max_protocols=max_protocols)
    protocols = bundle.get("protocols", [])
    signoff_summary = bundle.get("signoff_summary", {})
    sops = []
    for protocol in protocols:
        signoff_row = next((row for row in signoff_summary.get("rows", []) if row.get("protocol_id") == protocol.get("protocol_id")), {})
        approved = signoff_row.get("approval_state") == "signed_out"
        sop = {
            "schema_version": "v5.wet_lab_sop/0.1",
            "sop_id": make_stable_id("wet_lab_sop", {"project": project_dir.name, "protocol_id": protocol.get("protocol_id"), "version": protocol.get("protocol_version", 1)}),
            "project_id": project_dir.name,
            "protocol_id": protocol.get("protocol_id", ""),
            "protocol_version": protocol.get("protocol_version", 1),
            "candidate_gene": protocol.get("candidate_gene", ""),
            "sop_status": "approved_for_planning" if approved else "review_required",
            "claim_boundary": "Validation planning only; no therapeutic, diagnostic, or clinical claim is authorized.",
            "purpose": protocol.get("objective", ""),
            "scope": {
                "allowed": [
                    "review evidence prerequisites",
                    "confirm model/sample suitability",
                    "define assay class, controls, readout categories, and records required for review",
                    "record deviations, failed runs, inconclusive outcomes, and limitations",
                ],
                "excluded": protocol.get("exclusions", []),
            },
            "roles_and_responsibilities": [
                {"role": "pi", "responsibility": "final scientific and safety signoff"},
                {"role": "reviewer", "responsibility": "evidence, scope, risk, and controls review"},
                {"role": "operator", "responsibility": "record planned assay class and attach outputs only after approval"},
                {"role": "system", "responsibility": "preserve EvidenceItem, ArtifactManifest, QCReport, and signoff refs"},
            ],
            "pre_execution_gate": {
                "required": True,
                "approval_state": signoff_row.get("approval_state", "pending"),
                "minimum_approvals": protocol.get("approval_requirements", {}).get("minimum_approvals", 1),
                "reason_required": True,
                "blocking_conditions": [
                    "missing audited EvidenceItem refs",
                    "scope mismatch",
                    "high risk without explicit revision",
                    "claim ceiling violation",
                    "missing required controls or readout categories",
                ],
            },
            "recordkeeping_requirements": [
                "protocol_id and SOP id",
                "evidence_refs and artifact_refs",
                "reviewer signoff id and reason",
                "QCReport for any generated result",
                "negative, failed, and inconclusive outcomes",
                "deviations and recovery actions",
            ],
            "deviation_policy": [
                "Any deviation creates a new review item.",
                "A failed or inconclusive result cannot be omitted from the report.",
                "A revised protocol must increment protocol_version or create a new protocol_id.",
            ],
            "audit_refs": {
                "protocol_manifest": "v5/wet_lab_protocols/wet_lab_protocol_manifest.json",
                "protocol_bundle": "v5/wet_lab_protocols/wet_lab_protocol_bundle.json",
                "signoff_bundle": "v5/wet_lab_protocols/wet_lab_protocol_signoff_bundle.json",
            },
            "created_by": actor,
            "created_at": now_iso(),
        }
        sops.append(sop)
    sop_bundle = {
        "schema_version": SOP_BUNDLE_SCHEMA,
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "created_by": actor,
        "sop_count": len(sops),
        "approved_for_planning_count": sum(1 for row in sops if row.get("sop_status") == "approved_for_planning"),
        "review_required_count": sum(1 for row in sops if row.get("sop_status") != "approved_for_planning"),
        "sops": sops,
        "status": "signed_out" if sops and all(row.get("sop_status") == "approved_for_planning" for row in sops) else "review_required" if sops else "no_candidates",
        "safety_notice": "This is an auditable SOP governance bundle, not an operational wet-lab recipe. Execution still requires qualified local approval and institution-specific SOPs.",
    }
    _write_json(_protocol_dir(project_dir) / "wet_lab_sop_bundle.json", sop_bundle)
    return sop_bundle


def _protocols_need_upgrade(protocols: list[dict[str, Any]]) -> bool:
    required = {"protocol_version", "decision_points", "approval_requirements"}
    return any(not required.issubset(set(row)) for row in protocols)


def _load_candidates(project_dir: Path) -> list[dict[str, Any]]:
    path = project_dir / "candidate_scores.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _matching_design(designs: list[dict[str, Any]], gene: str) -> dict[str, Any]:
    lower = gene.lower()
    for row in designs:
        if lower and lower in str(row.get("candidate", "") or row.get("gene", "")).lower():
            return row
    return designs[0] if designs else {}


def _risk_grade(candidate: dict[str, Any], design: dict[str, Any]) -> dict[str, Any]:
    reasons = []
    score = 0
    safety = str(candidate.get("safety") or candidate.get("safety_gate") or "").lower()
    route = str(candidate.get("route") or "").lower()
    risks = " ".join(str(item) for item in design.get("risks", [])).lower() if isinstance(design.get("risks"), list) else str(design.get("risks", "")).lower()
    if "fail" in safety or "high" in safety:
        score += 2
        reasons.append("candidate has safety warning")
    if "t_cell" in route or "peptide" in route:
        score += 1
        reasons.append("route may require stronger immunology review")
    if "human" not in risks and "review" not in risks:
        reasons.append("risk statement requires human review before execution")
    else:
        score += 1
        reasons.append("existing design already flags review/risk")
    grade = "high" if score >= 3 else "medium" if score >= 1 else "low"
    return {"grade": grade, "reasons": reasons}


def _evidence_refs(candidate: dict[str, Any]) -> list[str]:
    refs = []
    for key in ["evidence_item_id", "evidence_refs", "report_ref"]:
        value = candidate.get(key)
        if value:
            refs.append(str(value))
    return refs


def _artifact_refs(project_dir: Path) -> list[str]:
    refs = []
    for path in ["candidate_scores.csv", "results/experiments/experiment_designs.json", "v5/reports/canonical_report_manifest.json"]:
        if (project_dir / path).exists():
            refs.append(path)
    return refs


def _default_readouts(candidate: dict[str, Any]) -> list[str]:
    route = str(candidate.get("route") or "").lower()
    if "secret" in route:
        return ["secreted protein abundance", "cell-state marker expression", "SASP score consistency"]
    if "surface" in route or "membrane" in route:
        return ["surface protein accessibility", "cell-type specificity", "SASP score consistency"]
    return ["gene/protein abundance", "cell-type specificity", "SASP score consistency"]


def _protocol_dir(project_dir: Path) -> Path:
    path = project_dir / "v5" / "wet_lab_protocols"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    project_dir = _project_dir_from_v5_path(path)
    write_json_artifact(project_dir, path.relative_to(project_dir), payload, producer="wet_lab_protocol", artifact_type="wet_lab_protocol_json")


def _project_dir_from_v5_path(path: Path) -> Path:
    parts = path.parts
    if "v5" in parts:
        return Path(*parts[: parts.index("v5")])
    return path.parent
