import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


V4_STATE_MACHINE = [
    {"state": "PROJECT_CREATED", "phase": "input", "terminal": False},
    {"state": "SPEC_DRAFTED", "phase": "generation", "terminal": False},
    {"state": "SPEC_FROZEN", "phase": "generation", "terminal": False},
    {"state": "DATASET_DISCOVERED", "phase": "verification", "terminal": False},
    {"state": "PLAN_DRAFTED", "phase": "planning", "terminal": False},
    {"state": "PLAN_APPROVED", "phase": "review", "terminal": False},
    {"state": "WORK_ORDERS_COMPILED", "phase": "compile", "terminal": False},
    {"state": "RUNNING", "phase": "execution", "terminal": False},
    {"state": "ARTIFACTS_READY", "phase": "execution", "terminal": False},
    {"state": "EVIDENCE_ACCEPTED", "phase": "review", "terminal": False},
    {"state": "SCORED", "phase": "scoring", "terminal": False},
    {"state": "REPORTED", "phase": "report", "terminal": False},
    {"state": "SIGNED_OUT", "phase": "signoff", "terminal": True},
    {"state": "CODEX_REQUIRED", "phase": "engineering", "terminal": False},
    {"state": "FAILED", "phase": "failure", "terminal": True},
    {"state": "CANCELLED", "phase": "failure", "terminal": True},
]

REGISTERED_MODULE_TYPES = {
    "bulk_deg",
    "descriptive_evidence",
    "enrichment",
    "annotation",
    "safety",
    "unknown_review",
    "evidence_import",
    "scoring",
    "report",
}


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def v4_dir(project_dir: Path) -> Path:
    path = project_dir / "v4"
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_v4_state_machine(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "state_machine.json"
    payload = {
        "schema_version": "v4.state_machine/0.1",
        "description": "Authoritative v4-compatible state contract for local MVP runs.",
        "states": V4_STATE_MACHINE,
        "transitions": [
            ["PROJECT_CREATED", "SPEC_DRAFTED"],
            ["SPEC_DRAFTED", "SPEC_FROZEN"],
            ["SPEC_FROZEN", "DATASET_DISCOVERED"],
            ["DATASET_DISCOVERED", "PLAN_DRAFTED"],
            ["PLAN_DRAFTED", "PLAN_APPROVED"],
            ["PLAN_APPROVED", "WORK_ORDERS_COMPILED"],
            ["WORK_ORDERS_COMPILED", "RUNNING"],
            ["RUNNING", "ARTIFACTS_READY"],
            ["ARTIFACTS_READY", "EVIDENCE_ACCEPTED"],
            ["EVIDENCE_ACCEPTED", "SCORED"],
            ["SCORED", "REPORTED"],
            ["REPORTED", "SIGNED_OUT"],
            ["PLAN_DRAFTED", "CODEX_REQUIRED"],
            ["RUNNING", "FAILED"],
            ["RUNNING", "CANCELLED"],
        ],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def derive_disease_spec(research_spec: dict[str, Any]) -> dict[str, Any]:
    disease_scope = research_spec.get("disease_scope", {})
    canonical = disease_scope.get("canonical") or research_spec.get("research_theme") or "unknown"
    return {
        "schema_version": "v4.disease_spec/0.1",
        "project_id": research_spec.get("project_id", ""),
        "disease_id": _stable_id("disease", canonical),
        "canonical_name": canonical,
        "related_phenotypes": disease_scope.get("related_phenotypes", []),
        "organisms": research_spec.get("organisms", []),
        "priority_tissues": research_spec.get("priority_tissues", []),
        "priority_cells": research_spec.get("priority_cells", []),
        "target_routes": research_spec.get("target_routes", []),
        "constraints": research_spec.get("constraints", {}),
    }


def compile_v4_work_orders(project_dir: Path, plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    plan = plan or read_json(project_dir / "analysis_plan.json", {})
    plan_hash = content_hash(plan)
    orders: list[dict[str, Any]] = []
    work_order_dir = v4_dir(project_dir) / "work_orders"
    work_order_dir.mkdir(parents=True, exist_ok=True)
    for index, module in enumerate(plan.get("modules", []), start=1):
        module_type = module.get("module", "unknown")
        order_type = "RUN_REGISTERED_MODULE" if module_type in REGISTERED_MODULE_TYPES else "BUILD_ADAPTER"
        seed = {"project": project_dir.name, "index": index, "plan_hash": plan_hash, "module": module}
        work_order_id = "wo_" + content_hash(seed)[:16]
        command = module.get("command") or ""
        payload = {
            "schema_version": "v4.work_order/0.1",
            "work_order_id": work_order_id,
            "project_id": project_dir.name,
            "plan_hash": plan_hash,
            "work_order_type": order_type,
            "module_id": module.get("module_id", ""),
            "module": module_type,
            "dataset_id": module.get("dataset_id", ""),
            "status": "compiled",
            "idempotency_key": "idem_" + content_hash(seed)[:24],
            "execution_backend": "local_executor",
            "target_backend": "temporal_nextflow_compatible",
            "command": command,
            "inputs": module.get("inputs", {}),
            "parameters": module.get("parameters", {}),
            "expected_artifacts": module.get("expected_outputs", []),
            "qc_checks": module.get("qc_checks", []),
            "allowed_paths": module.get("allowed_files", []),
            "lineage": {
                "analysis_plan": "analysis_plan.json",
                "module_registry": plan.get("module_registry", "analysis_module_registry.json"),
            },
            "requires_codex": order_type != "RUN_REGISTERED_MODULE",
        }
        out = work_order_dir / f"{work_order_id}.json"
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        if payload["requires_codex"]:
            packet = build_codex_task_packet(project_dir, payload)
            packet_path = work_order_dir / f"{work_order_id}_codex_task_packet.json"
            packet_path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
            payload["codex_task_packet"] = str(packet_path.relative_to(project_dir))
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        orders.append(payload)
    index_path = v4_dir(project_dir) / "work_orders.json"
    index_path.write_text(json.dumps({"schema_version": "v4.work_order_index/0.1", "work_orders": orders}, indent=2, ensure_ascii=False), encoding="utf-8")
    return orders


def build_codex_task_packet(project_dir: Path, work_order: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "v4.codex_task_packet/0.1",
        "codex_job_id": "cj_" + content_hash(work_order)[:16],
        "project_id": project_dir.name,
        "work_order_id": work_order["work_order_id"],
        "baseline_commit": _git_commit(project_dir),
        "task_type": work_order["work_order_type"],
        "problem_statement": f"Implement or repair capability for module {work_order.get('module', 'unknown')}.",
        "allowed_paths": work_order.get("allowed_paths", []),
        "forbidden_inputs": [
            "production secrets",
            "private raw research data outside the task fixture",
            "manual edits to accepted scientific results",
        ],
        "fixture": {
            "project": project_dir.name,
            "inputs": work_order.get("inputs", {}),
            "parameters": work_order.get("parameters", {}),
        },
        "tests": ["python -m unittest discover -s tests -p \"test*.py\" -v"],
        "expected_outputs": work_order.get("expected_artifacts", []),
        "release_gate": "tests_pass_and_human_review_required",
    }


def build_mcp_resource_manifest(project_dir: Path, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    from .mcp_gateway import build_mcp_gateway

    return build_mcp_gateway(project_dir, plan)["resources"]


def build_evidence_snapshot(project_dir: Path) -> dict[str, Any]:
    db = project_dir / "evidence.sqlite"
    rows = 0
    accepted_or_pending = 0
    missing_lineage = 0
    datasets: list[str] = []
    if db.exists():
        con = sqlite3.connect(db, timeout=30)
        try:
            rows = con.execute("SELECT COUNT(*) FROM evidence_item").fetchone()[0]
            accepted_or_pending = con.execute(
                """
                SELECT COUNT(*)
                FROM evidence_item
                WHERE COALESCE(review_status, 'PENDING') IN ('PENDING', 'ACCEPT', 'ACCEPT_WITH_FLAGS', 'approve', 'accepted')
                """
            ).fetchone()[0]
            columns = {row[1] for row in con.execute("PRAGMA table_info(evidence_item)").fetchall()}
            if {"run_id", "artifact_id", "module_version"}.issubset(columns):
                missing_lineage = con.execute(
                    """
                    SELECT COUNT(*)
                    FROM evidence_item
                    WHERE COALESCE(run_id, '') = ''
                       OR COALESCE(artifact_id, '') = ''
                       OR COALESCE(module_version, '') = ''
                    """
                ).fetchone()[0]
            datasets = [
                row[0]
                for row in con.execute(
                    "SELECT DISTINCT COALESCE(source_dataset, '') FROM evidence_item WHERE COALESCE(source_dataset, '') != '' ORDER BY 1"
                ).fetchall()
            ]
        finally:
            con.close()
    snapshot = {
        "schema_version": "v4.evidence_snapshot/0.1",
        "snapshot_id": "es_" + content_hash({"project": project_dir.name, "rows": rows, "datasets": datasets, "lineage_missing": missing_lineage})[:16],
        "project_id": project_dir.name,
        "evidence_db": "evidence.sqlite" if db.exists() else "",
        "evidence_rows": rows,
        "accepted_or_pending_rows": accepted_or_pending,
        "missing_lineage_rows": missing_lineage,
        "source_datasets": datasets,
        "scoring_manifest": "results/scoring/target_score_manifest.json"
        if (project_dir / "results" / "scoring" / "target_score_manifest.json").exists()
        else "",
        "review_status_policy": "report_writer_may_reference_accepted_or_flagged_evidence_only",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = v4_dir(project_dir) / "evidence_snapshot.json"
    path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return snapshot


def build_v4_manifest(project_dir: Path, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = plan or read_json(project_dir / "analysis_plan.json", {})
    research_spec = read_json(project_dir / "research_spec.json", {})
    disease_spec = derive_disease_spec(research_spec)
    disease_spec_path = v4_dir(project_dir) / "disease_spec.json"
    disease_spec_path.write_text(json.dumps(disease_spec, indent=2, ensure_ascii=False), encoding="utf-8")
    state_machine_path = write_v4_state_machine(project_dir)
    work_orders = compile_v4_work_orders(project_dir, plan)
    evidence_snapshot = build_evidence_snapshot(project_dir)
    mcp_manifest = build_mcp_resource_manifest(project_dir, plan)
    manifest = {
        "schema_version": "v4.object_manifest/0.1",
        "project_id": project_dir.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "objects": {
            "research_spec": {
                "path": "research_spec.json",
                "hash": content_hash(research_spec),
            },
            "disease_spec": {
                "path": str(disease_spec_path.relative_to(project_dir)),
                "hash": content_hash(disease_spec),
            },
            "analysis_plan": {
                "path": "analysis_plan.json",
                "hash": content_hash(plan),
            },
            "state_machine": {
                "path": str(state_machine_path.relative_to(project_dir)),
                "hash": file_hash(state_machine_path),
            },
            "work_orders": {
                "count": len(work_orders),
                "path": "v4/work_orders.json",
                "hash": file_hash(v4_dir(project_dir) / "work_orders.json"),
            },
            "evidence_snapshot": evidence_snapshot,
            "mcp_resources": {
                "path": "v4/mcp_resources.json",
                "count": len(mcp_manifest["resources"]),
            },
            "mcp_tools": {
                "path": "v4/mcp_tools.json",
                "exists": (project_dir / "v4" / "mcp_tools.json").exists(),
            },
            "mcp_call_audit": {
                "path": "v4/mcp_call_audit_summary.json",
                "exists": (project_dir / "v4" / "mcp_call_audit_summary.json").exists(),
            },
            "agent_roles": {
                "path": "v4/agent_roles.json",
                "exists": (project_dir / "v4" / "agent_roles.json").exists(),
            },
            "role_runs": {
                "path": "v4/role_runs.json",
                "exists": (project_dir / "v4" / "role_runs.json").exists(),
            },
        },
    }
    path = v4_dir(project_dir) / "object_manifest.json"
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def load_v4_work_orders(project_dir: Path) -> list[dict[str, Any]]:
    index = v4_dir(project_dir) / "work_orders.json"
    if not index.exists():
        return []
    return json.loads(index.read_text(encoding="utf-8")).get("work_orders", [])


def save_v4_work_order(project_dir: Path, work_order: dict[str, Any]) -> None:
    work_order_id = work_order["work_order_id"]
    order_dir = v4_dir(project_dir) / "work_orders"
    order_dir.mkdir(parents=True, exist_ok=True)
    (order_dir / f"{work_order_id}.json").write_text(json.dumps(work_order, indent=2, ensure_ascii=False), encoding="utf-8")
    index = v4_dir(project_dir) / "work_orders.json"
    orders = load_v4_work_orders(project_dir)
    replaced = False
    for idx, row in enumerate(orders):
        if row.get("work_order_id") == work_order_id:
            orders[idx] = work_order
            replaced = True
            break
    if not replaced:
        orders.append(work_order)
    index.write_text(json.dumps({"schema_version": "v4.work_order_index/0.1", "work_orders": orders}, indent=2, ensure_ascii=False), encoding="utf-8")


def load_codex_task_packet(project_dir: Path, work_order: dict[str, Any]) -> dict[str, Any]:
    rel = work_order.get("codex_task_packet", "")
    if not rel:
        return {}
    path = project_dir / rel
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_codex_task_packet(project_dir: Path, work_order: dict[str, Any], packet: dict[str, Any]) -> None:
    rel = work_order.get("codex_task_packet", "")
    if not rel:
        return
    path = project_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")


def attempt_manifest_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "work_order_attempts.json"


def read_work_order_attempts(project_dir: Path) -> dict[str, Any]:
    path = attempt_manifest_path(project_dir)
    if not path.exists():
        return {"schema_version": "v4.work_order_attempts/0.1", "project_id": project_dir.name, "attempts": []}
    return json.loads(path.read_text(encoding="utf-8"))


def start_work_order_attempt(project_dir: Path, module_id: str, run_id: str = "") -> dict[str, Any]:
    orders = load_v4_work_orders(project_dir)
    order = next((row for row in orders if row.get("module_id") == module_id), {})
    seed = {"module_id": module_id, "run_id": run_id, "time": datetime.now(timezone.utc).isoformat()}
    attempt = {
        "attempt_id": "attempt_" + content_hash(seed)[:16],
        "work_order_id": order.get("work_order_id", ""),
        "module_id": module_id,
        "dataset_id": order.get("dataset_id", ""),
        "work_order_type": order.get("work_order_type", ""),
        "run_id": run_id,
        "status": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": "",
        "failure_reason": "",
        "artifacts": [],
        "resume_key": order.get("idempotency_key", ""),
    }
    manifest = read_work_order_attempts(project_dir)
    manifest["attempts"].append(attempt)
    _write_attempt_manifest(project_dir, manifest)
    return attempt


def finish_work_order_attempt(
    project_dir: Path,
    attempt_id: str,
    status: str,
    artifacts: list[str] | None = None,
    failure_reason: str = "",
) -> dict[str, Any]:
    manifest = read_work_order_attempts(project_dir)
    updated = {}
    for row in manifest["attempts"]:
        if row.get("attempt_id") == attempt_id:
            row["status"] = status
            row["finished_at"] = datetime.now(timezone.utc).isoformat()
            row["failure_reason"] = failure_reason
            row["artifacts"] = artifacts or row.get("artifacts", [])
            updated = row
            break
    _write_attempt_manifest(project_dir, manifest)
    return updated


def _write_attempt_manifest(project_dir: Path, manifest: dict[str, Any]) -> None:
    path = attempt_manifest_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _stable_id(prefix: str, value: str) -> str:
    return prefix + "_" + hashlib.sha256(value.lower().strip().encode("utf-8")).hexdigest()[:12]


def _git_commit(project_dir: Path) -> str:
    head = project_dir.parents[1] / ".git" / "HEAD"
    if not head.exists():
        return "unknown"
    text = head.read_text(encoding="utf-8").strip()
    if text.startswith("ref:"):
        ref = project_dir.parents[1] / ".git" / text.split(" ", 1)[1]
        return ref.read_text(encoding="utf-8").strip()[:12] if ref.exists() else "unknown"
    return text[:12]
