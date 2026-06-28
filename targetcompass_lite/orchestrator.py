import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .run_state import read_status, request_cancel, write_status
from .v4 import content_hash, finish_work_order_attempt, read_work_order_attempts, start_work_order_attempt, v4_dir


RUN_INDEX_SCHEMA = "v4.orchestrator_run_index/0.1"
RUN_SCHEMA = "v4.orchestrator_run/0.1"


def orchestrator_runs_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "orchestrator_runs.json"


def submit_orchestrator_run(
    project_dir: Path,
    run_type: str = "typed_orchestration",
    idempotency_key: str = "",
    role_id: str = "",
    force: bool = False,
    partial_stage: str = "",
    module_id: str = "",
    work_order_id: str = "",
    actor: str = "orchestrator",
) -> dict[str, Any]:
    request = {
        "run_type": run_type or "typed_orchestration",
        "role_id": role_id,
        "force": bool(force),
        "partial_stage": partial_stage,
        "module_id": module_id,
        "work_order_id": work_order_id,
    }
    key = idempotency_key or "idem_" + content_hash({"project": project_dir.name, "request": request})[:24]
    existing = _find_by_idempotency_key(project_dir, key)
    if existing:
        existing = dict(existing)
        existing["idempotent_replay"] = True
        _write_index(project_dir, _read_index(project_dir))
        return existing

    run_id = "orch_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + content_hash({"key": key, "time": _now()})[:8]
    record = {
        "schema_version": RUN_SCHEMA,
        "orchestrator_run_id": run_id,
        "project_id": project_dir.name,
        "idempotency_key": key,
        "run_type": request["run_type"],
        "status": "running",
        "actor": actor,
        "request": request,
        "submitted_at": _now(),
        "started_at": _now(),
        "finished_at": "",
        "failure_reason": "",
        "resume_of": "",
        "resume_key": key,
        "artifacts": [],
        "state_refs": _state_refs(project_dir),
        "idempotent_replay": False,
    }
    _append_or_replace(project_dir, record)
    write_status(project_dir, "running", f"Orchestrator run started: {run_id}", run_id=run_id, active_stage=request["run_type"])

    try:
        result = _execute_local(project_dir, request, actor)
        record["status"] = _normalize_status(result.get("status", "success"))
        record["result"] = result
        record["artifacts"] = _result_artifacts(project_dir, request, result)
        record["failure_reason"] = result.get("failure_reason", "") if isinstance(result, dict) else ""
    except Exception as exc:
        record["status"] = "failed"
        record["failure_reason"] = str(exc)
        record["result"] = {}
    record["finished_at"] = _now()
    record["state_refs"] = _state_refs(project_dir)
    _append_or_replace(project_dir, record)
    write_status(
        project_dir,
        "success" if record["status"] == "success" else record["status"],
        f"Orchestrator run finished: {record['status']}",
        run_id=run_id,
        failure_reason=record["failure_reason"],
        active_stage=request["run_type"],
    )
    return record


def get_orchestrator_status(project_dir: Path, orchestrator_run_id: str = "") -> dict[str, Any]:
    index = _read_index(project_dir)
    runs = index.get("runs", [])
    selected = next((row for row in runs if row.get("orchestrator_run_id") == orchestrator_run_id), runs[-1] if runs else {})
    attempts = read_work_order_attempts(project_dir).get("attempts", [])
    typed = _read_json(project_dir / "v4" / "typed_orchestration_last_run.json", {})
    status = read_status(project_dir)
    return {
        "schema_version": "v4.orchestrator_status/0.1",
        "project_id": project_dir.name,
        "orchestrator_run_id": selected.get("orchestrator_run_id", ""),
        "status": selected.get("status", status.get("status", "idle")),
        "selected_run": selected,
        "run_status": status,
        "work_order_attempt_count": len(attempts),
        "running_attempt_count": len([row for row in attempts if row.get("status") == "running"]),
        "failed_attempt_count": len([row for row in attempts if row.get("status") == "failed"]),
        "typed_orchestration_status": typed.get("status", ""),
        "state_refs": _state_refs(project_dir),
    }


def cancel_orchestrator_run(project_dir: Path, orchestrator_run_id: str = "", reason: str = "user_requested") -> dict[str, Any]:
    cancel = request_cancel(project_dir, reason=reason)
    index = _read_index(project_dir)
    for row in index.get("runs", []):
        if (not orchestrator_run_id and row.get("status") == "running") or row.get("orchestrator_run_id") == orchestrator_run_id:
            row["status"] = "cancel_requested" if row.get("status") == "running" else row.get("status", "cancel_requested")
            row["cancel_requested_at"] = cancel["requested_at"]
            row["cancel_reason"] = reason
    _write_index(project_dir, index)
    return get_orchestrator_status(project_dir, orchestrator_run_id)


def resume_orchestrator_run(project_dir: Path, orchestrator_run_id: str = "", actor: str = "orchestrator") -> dict[str, Any]:
    status = get_orchestrator_status(project_dir, orchestrator_run_id)
    selected = status.get("selected_run", {})
    if not selected:
        raise ValueError("no orchestrator run is available to resume")
    request = selected.get("request", {})
    resumed = submit_orchestrator_run(
        project_dir,
        run_type=request.get("run_type", "typed_orchestration"),
        idempotency_key="resume_" + content_hash({"run": selected.get("orchestrator_run_id", ""), "time": _now()})[:24],
        role_id=request.get("role_id", ""),
        force=True if request.get("run_type") == "typed_orchestration" else bool(request.get("force", False)),
        partial_stage=request.get("partial_stage", ""),
        module_id=request.get("module_id", ""),
        work_order_id=request.get("work_order_id", ""),
        actor=actor,
    )
    resumed["resume_of"] = selected.get("orchestrator_run_id", "")
    _append_or_replace(project_dir, resumed)
    return resumed


def partial_rerun_orchestrator(project_dir: Path, partial_stage: str, actor: str = "orchestrator") -> dict[str, Any]:
    if not partial_stage:
        raise ValueError("partial_stage is required")
    return submit_orchestrator_run(
        project_dir,
        run_type="partial_rerun",
        idempotency_key="partial_" + content_hash({"stage": partial_stage, "time": _now()})[:24],
        partial_stage=partial_stage,
        actor=actor,
    )


def _execute_local(project_dir: Path, request: dict[str, Any], actor: str) -> dict[str, Any]:
    if request["run_type"] == "typed_orchestration":
        from .orchestration_graph import run_typed_orchestration

        return run_typed_orchestration(project_dir, role_id=request.get("role_id", ""), force=bool(request.get("force", False)), actor=actor)
    if request["run_type"] == "partial_rerun":
        stage = request.get("partial_stage", "")
        artifacts = _run_partial_stage(project_dir, stage)
        return {"schema_version": "v4.partial_rerun_result/0.1", "status": "success", "partial_stage": stage, "artifacts": artifacts}
    if request["run_type"] == "work_order_dag":
        return _run_work_order_dag(project_dir, request, actor)
    if request["run_type"] == "status_only":
        return {"status": "success", "state_refs": _state_refs(project_dir)}
    raise ValueError(f"unsupported orchestrator run_type: {request['run_type']}")


def _run_partial_stage(project_dir: Path, stage: str) -> list[str]:
    if stage == "evidence":
        from .evidence_db import import_evidence

        return [str(import_evidence(project_dir).relative_to(project_dir))]
    if stage == "report":
        from .reporting import build_report

        return [str(path.relative_to(project_dir)) for path in build_report(project_dir)]
    if stage == "manifest":
        from .v4 import build_v4_manifest

        build_v4_manifest(project_dir)
        return ["v4/object_manifest.json"]
    if stage == "traceability":
        from .trace_orchestrator import refresh_traceability

        refresh_traceability(project_dir)
        return ["v4/traceability_refresh.json"]
    raise ValueError(f"unsupported partial_stage: {stage}")


def _run_work_order_dag(project_dir: Path, request: dict[str, Any], actor: str) -> dict[str, Any]:
    from .work_order_dag import build_work_order_dag

    dag_before = build_work_order_dag(project_dir)
    nodes = _select_dag_nodes(dag_before.get("nodes", []), request)
    if not nodes:
        dag_after = build_work_order_dag(project_dir)
        return {
            "schema_version": "v4.work_order_dag_run/0.1",
            "status": "success",
            "selected_count": 0,
            "node_results": [],
            "dag_before": dag_before.get("generated_at", ""),
            "dag_after": dag_after.get("generated_at", ""),
            "dag_status_summary": dag_after.get("status_summary", {}),
            "task_registry": {"path": "v4/task_registry.json", "task_count": 0, "status_summary": {}},
            "artifact": "v4/work_order_dag.json",
        }
    node_results = []
    status = "success"
    completed = {node.get("node_id") for node in dag_before.get("nodes", []) if node.get("status") in {"success", "artifacts_ready"} and not request.get("force")}
    selected_ids = {node.get("node_id") for node in nodes}
    for node in nodes:
        node_id = node.get("node_id", "")
        deps = node.get("dependencies", [])
        missing_deps = [dep for dep in deps if dep not in completed and dep not in selected_ids]
        failed_selected_deps = [dep for dep in deps if dep in selected_ids and dep not in completed and any(row.get("node_id") == dep and row.get("status") in {"failed", "blocked"} for row in node_results)]
        if missing_deps or failed_selected_deps:
            result = _node_result(node, "blocked", reason="dependency not complete: " + ", ".join(missing_deps + failed_selected_deps))
            node_results.append(result)
            status = "blocked" if status == "success" else status
            continue
        if node.get("status") in {"success", "artifacts_ready"} and not request.get("force"):
            result = _node_result(node, "skipped", reason="node already has successful artifacts")
            node_results.append(result)
            completed.add(node_id)
            continue
        result = _execute_work_order_node(project_dir, node, actor)
        node_results.append(result)
        if result["status"] == "success":
            completed.add(node_id)
        elif result["status"] == "failed":
            status = "failed"
        elif result["status"] == "blocked" and status == "success":
            status = "blocked"
        build_work_order_dag(project_dir)
    dag_after = build_work_order_dag(project_dir)
    registry = _refresh_task_registry(project_dir)
    return {
        "schema_version": "v4.work_order_dag_run/0.1",
        "status": status,
        "selected_count": len(nodes),
        "node_results": node_results,
        "dag_before": dag_before.get("generated_at", ""),
        "dag_after": dag_after.get("generated_at", ""),
        "dag_status_summary": dag_after.get("status_summary", {}),
        "task_registry": {"path": "v4/task_registry.json", "task_count": registry.get("task_count", 0), "status_summary": registry.get("status_summary", {})},
        "artifact": "v4/work_order_dag.json",
    }


def _select_dag_nodes(nodes: list[dict[str, Any]], request: dict[str, Any]) -> list[dict[str, Any]]:
    target_work_order = request.get("work_order_id", "")
    target_module = request.get("module_id", "")
    if not target_work_order and not target_module:
        return _topological_nodes(nodes)
    wanted = set()
    by_id = {node.get("node_id"): node for node in nodes}
    for node in nodes:
        if node.get("node_id") == target_work_order or node.get("work_order_id") == target_work_order or node.get("module_id") == target_module:
            _collect_deps(node, by_id, wanted)
            wanted.add(node.get("node_id", ""))
    if not wanted:
        raise ValueError(f"no DAG node matched work_order_id={target_work_order!r} module_id={target_module!r}")
    return [node for node in _topological_nodes(nodes) if node.get("node_id") in wanted]


def _topological_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    remaining = {node.get("node_id", ""): node for node in nodes}
    ordered = []
    while remaining:
        progressed = False
        for node_id, node in list(remaining.items()):
            deps = set(node.get("dependencies", []))
            if deps.isdisjoint(remaining.keys()):
                ordered.append(node)
                remaining.pop(node_id)
                progressed = True
        if not progressed:
            ordered.extend(remaining.values())
            break
    return ordered


def _collect_deps(node: dict[str, Any], by_id: dict[str, dict[str, Any]], wanted: set[str]) -> None:
    for dep in node.get("dependencies", []):
        if dep in wanted:
            continue
        wanted.add(dep)
        if dep in by_id:
            _collect_deps(by_id[dep], by_id, wanted)


def _execute_work_order_node(project_dir: Path, node: dict[str, Any], actor: str) -> dict[str, Any]:
    module_id = node.get("module_id", "")
    attempt = start_work_order_attempt(project_dir, module_id, run_id=actor)
    try:
        dispatch = _dispatch_work_order_node(project_dir, node, actor)
        order = _order_for_node(project_dir, node)
        qc_report = _build_qc_report(project_dir, order, node, dispatch)
        outputs = dispatch.get("artifacts", [])
        updated = finish_work_order_attempt(
            project_dir,
            attempt["attempt_id"],
            dispatch.get("status", "success"),
            outputs,
            failure_reason=dispatch.get("failure_reason", ""),
            metadata={"orchestrator_actor": actor, "node_id": node.get("node_id", ""), "executor_dispatch": dispatch, "task_qc_report": qc_report},
        )
        recovery = _recovery_advice(node, dispatch.get("failure_reason", "")) if dispatch.get("status") == "failed" else {}
        return _node_result(
            node,
            updated.get("status", dispatch.get("status", "success")),
            reason=dispatch.get("failure_reason", ""),
            attempt=updated,
            artifacts=outputs,
            recovery=recovery,
            executor=dispatch,
            qc_report=qc_report,
        )
    except Exception as exc:
        recovery = _recovery_advice(node, str(exc))
        qc_report = {}
        try:
            order = _order_for_node(project_dir, node)
            qc_report = _build_qc_report(
                project_dir,
                order,
                node,
                {"status": "failed", "failure_reason": str(exc), "backend": "orchestrator", "artifacts": []},
            )
        except Exception:
            qc_report = {}
        updated = finish_work_order_attempt(
            project_dir,
            attempt["attempt_id"],
            "failed",
            failure_reason=str(exc),
            metadata={"orchestrator_actor": actor, "node_id": node.get("node_id", ""), "recovery": recovery, "task_qc_report": qc_report},
        )
        return _node_result(node, "failed", reason=str(exc), attempt=updated, recovery=recovery, qc_report=qc_report)


def _dispatch_work_order_node(project_dir: Path, node: dict[str, Any], actor: str) -> dict[str, Any]:
    order = _order_for_node(project_dir, node)
    if node.get("node_type") != "RUN_REGISTERED_MODULE":
        return _dispatch_codex_engineering(project_dir, order, actor)
    backend = order.get("execution_backend", "local_executor")
    if backend == "nextflow":
        return _dispatch_nextflow(project_dir, order)
    return _dispatch_local_module(project_dir, order)


def _dispatch_local_module(project_dir: Path, order: dict[str, Any]) -> dict[str, Any]:
    from .executor import build_executor_contract, run_local_executor
    from .artifact_resolver import resolve_work_order_inputs, write_artifact_resolution

    module = order.get("module", "")
    module_id = order.get("module_id", "")
    expected = list(order.get("expected_artifacts", []))
    out_dir = project_dir / "results" / "executor" / module_id
    resolution = resolve_work_order_inputs(project_dir, order)
    resolution_ref = write_artifact_resolution(project_dir, order, resolution)
    if resolution.get("status") != "pass":
        return {
            "schema_version": "v4.executor_dispatch/0.1",
            "backend": "local_executor",
            "module": module,
            "module_id": module_id,
            "status": "failed",
            "artifacts": [resolution_ref],
            "failure_reason": "missing required input artifact(s): " + ", ".join(row.get("key", "") for row in resolution.get("missing", [])),
            "recovery": {"items": resolution.get("recovery", []), "artifact_resolution": resolution_ref},
            "input_resolution": resolution,
        }
    contract = build_executor_contract(
        project_dir,
        module_id,
        runner=f"targetcompass_lite.orchestrator:{module}",
        inputs=_string_inputs(order.get("inputs", {})),
        parameters=order.get("parameters", {}),
        expected_outputs=expected,
    )

    def operation() -> Any:
        return _run_module_operation(project_dir, order)

    result, manifest = run_local_executor(project_dir, out_dir, contract, operation)
    artifacts = [row["path"] for row in manifest.get("artifacts", [])]
    manifest_rel = str((out_dir / "executor_manifest.json").relative_to(project_dir)).replace("\\", "/")
    if manifest_rel not in artifacts:
        artifacts.append(manifest_rel)
    return {
        "schema_version": "v4.executor_dispatch/0.1",
        "backend": "local_executor",
        "module": module,
        "module_id": module_id,
        "status": manifest.get("status", "success"),
        "artifacts": artifacts,
        "executor_manifest": manifest_rel,
        "result": _jsonable_result(project_dir, result),
    }


def _run_module_operation(project_dir: Path, order: dict[str, Any]) -> Any:
    module = order.get("module", "")
    dataset_id = order.get("dataset_id", "")
    params = order.get("parameters", {})
    inputs = order.get("inputs", {})
    if module == "bulk_deg":
        from .deg import run_deg

        return run_deg(project_dir, dataset_id)
    if module == "enrichment":
        from .enrichment import run_enrichment

        return run_enrichment(project_dir)
    if module == "annotation":
        from .annotation import annotate_project

        return annotate_project(project_dir)
    if module == "sasp_score":
        from .sasp_score import run_sasp_score

        return run_sasp_score(project_dir)
    if module == "cell_type_evidence":
        from .cell_type_evidence import build_cell_type_evidence

        return build_cell_type_evidence(project_dir)
    if module == "evidence_import":
        from .evidence_db import import_evidence

        return import_evidence(project_dir)
    if module == "scoring":
        from .scoring import score_project

        return score_project(project_dir)
    if module == "report":
        from .reporting import build_report

        return build_report(project_dir)
    if module == "meta_analysis":
        from .meta_analysis import run_meta_analysis

        return run_meta_analysis(project_dir)
    if module == "causal_evidence":
        from .causal_evidence import grade_causal_evidence

        return grade_causal_evidence(project_dir)
    if module == "scrna_pseudobulk":
        from .scrna import run_scrna_pseudobulk

        return run_scrna_pseudobulk(
            project_dir,
            dataset_id=dataset_id,
            count_matrix=inputs.get("count_matrix", ""),
            metadata=inputs.get("metadata", ""),
            cell_type=params.get("cell_type", ""),
            donor_column=params.get("donor_column", "donor_id"),
            group_column=params.get("group_column", "group"),
            cell_type_column=params.get("cell_type_column", "cell_type"),
            min_cells_per_donor=int(params.get("min_cells_per_donor", 1)),
            min_donors_per_group=int(params.get("min_donors_per_group", 1)),
            case_group=params.get("case_group", ""),
            control_group=params.get("control_group", ""),
        )
    if module == "genetic_coloc_mr":
        from .genetic import run_genetic_coloc_mr

        return run_genetic_coloc_mr(
            project_dir,
            gwas_summary=inputs.get("gwas_summary", ""),
            qtl_summary=inputs.get("qtl_summary", ""),
            dataset_id=dataset_id or params.get("dataset_id", "genetic"),
            ld_reference=params.get("ld_reference", ""),
        )
    raise RuntimeError(f"no local executor registered for module: {module}")


def _dispatch_nextflow(project_dir: Path, order: dict[str, Any]) -> dict[str, Any]:
    from .nextflow_runner import run_nextflow_local

    manifest = run_nextflow_local(project_dir, module_ids=[order.get("module_id", "")], resume=True)
    return {
        "schema_version": "v4.executor_dispatch/0.1",
        "backend": "nextflow",
        "module": order.get("module", ""),
        "module_id": order.get("module_id", ""),
        "status": manifest.get("status", "failed"),
        "artifacts": manifest.get("artifacts", []),
        "failure_reason": manifest.get("failure_reason", ""),
        "nextflow_manifest": "workflows/target_discovery/nextflow_run_manifest.json",
    }


def _dispatch_codex_engineering(project_dir: Path, order: dict[str, Any], actor: str) -> dict[str, Any]:
    from .codex_engineering import create_isolated_workspace, record_codex_result

    workspace = create_isolated_workspace(project_dir, order.get("work_order_id", ""), actor=actor)
    result = record_codex_result(
        project_dir,
        workspace["codex_job_id"],
        "needs_review",
        artifacts=[workspace.get("workspace_path", ""), f"{workspace.get('workspace_path', '')}/workspace_manifest.json"],
        failure_reason="Engineering task prepared for Codex worker; human approval required before execution.",
        actor=actor,
    )
    return {
        "schema_version": "v4.executor_dispatch/0.1",
        "backend": "codex_engineering",
        "module": order.get("module", ""),
        "module_id": order.get("module_id", ""),
        "status": "failed",
        "failure_reason": "Codex engineering task prepared; awaiting implementation and approval.",
        "artifacts": [workspace.get("workspace_path", ""), f"{workspace.get('workspace_path', '')}/workspace_manifest.json", "v4/codex_engineering/result_registry.json"],
        "workspace": workspace,
        "codex_result": result,
    }


def _node_result(
    node: dict[str, Any],
    status: str,
    reason: str = "",
    attempt: dict[str, Any] | None = None,
    artifacts: list[str] | None = None,
    recovery: dict[str, Any] | None = None,
    executor: dict[str, Any] | None = None,
    qc_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "node_id": node.get("node_id", ""),
        "work_order_id": node.get("work_order_id", ""),
        "module_id": node.get("module_id", ""),
        "status": status,
        "reason": reason,
        "attempt_id": (attempt or {}).get("attempt_id", ""),
        "resume_key": (attempt or {}).get("resume_key", node.get("resume_key", "")),
        "artifacts": artifacts or [],
        "recovery": recovery or {},
        "executor": executor or {},
        "task_qc_report": qc_report or {},
    }


def _build_qc_report(project_dir: Path, order: dict[str, Any], node: dict[str, Any], dispatch: dict[str, Any]) -> dict[str, Any]:
    from .qc import build_task_qc_report

    return build_task_qc_report(project_dir, order, node, dispatch)


def _refresh_task_registry(project_dir: Path) -> dict[str, Any]:
    from .task_registry import build_task_registry

    return build_task_registry(project_dir)


def _recovery_advice(node: dict[str, Any], failure: str) -> dict[str, Any]:
    if node.get("node_type") != "RUN_REGISTERED_MODULE":
        action = "route to Codex engineering loop, approve patch, then rerun this node"
    elif "dependency" in failure.lower():
        action = "rerun dependency nodes first, then resume this node"
    else:
        action = "inspect attempt metadata and rerun with force=true after fixing inputs or artifacts"
    return {
        "failure_reason": failure,
        "resume_key": node.get("resume_key", ""),
        "suggested_action": action,
        "rerun": {"run_type": "work_order_dag", "work_order_id": node.get("work_order_id", ""), "force": True},
    }


def _order_for_node(project_dir: Path, node: dict[str, Any]) -> dict[str, Any]:
    from .v4 import load_v4_work_orders

    for order in load_v4_work_orders(project_dir):
        if order.get("work_order_id") == node.get("work_order_id"):
            return order
    raise ValueError(f"work order not found for node: {node.get('work_order_id', '')}")


def _string_inputs(inputs: dict[str, Any]) -> dict[str, str]:
    out = {}
    for key, value in inputs.items():
        if isinstance(value, str):
            out[key] = value
        elif isinstance(value, (int, float, bool)):
            out[key] = str(value)
        else:
            out[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return out


def _jsonable_result(project_dir: Path, value: Any) -> Any:
    if isinstance(value, Path):
        return str(value.relative_to(project_dir)).replace("\\", "/") if value.exists() and project_dir in value.parents else str(value)
    if isinstance(value, tuple):
        return [_jsonable_result(project_dir, item) for item in value]
    if isinstance(value, list):
        return [_jsonable_result(project_dir, item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable_result(project_dir, item) for key, item in value.items()}
    return value


def _read_index(project_dir: Path) -> dict[str, Any]:
    path = orchestrator_runs_path(project_dir)
    if not path.exists():
        return {"schema_version": RUN_INDEX_SCHEMA, "project_id": project_dir.name, "runs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_index(project_dir: Path, index: dict[str, Any]) -> None:
    index["updated_at"] = _now()
    path = orchestrator_runs_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def _append_or_replace(project_dir: Path, record: dict[str, Any]) -> None:
    index = _read_index(project_dir)
    replaced = False
    for idx, row in enumerate(index.get("runs", [])):
        if row.get("orchestrator_run_id") == record.get("orchestrator_run_id"):
            index["runs"][idx] = record
            replaced = True
            break
    if not replaced:
        index.setdefault("runs", []).append(record)
    _write_index(project_dir, index)


def _find_by_idempotency_key(project_dir: Path, key: str) -> dict[str, Any]:
    return next((row for row in _read_index(project_dir).get("runs", []) if row.get("idempotency_key") == key), {})


def _state_refs(project_dir: Path) -> dict[str, str]:
    refs = {
        "run_status": "results/run_status.json",
        "work_order_attempts": "v4/work_order_attempts.json",
        "typed_orchestration": "v4/typed_orchestration_last_run.json",
        "orchestrator_runs": "v4/orchestrator_runs.json",
    }
    return {key: value for key, value in refs.items() if (project_dir / value).exists() or key == "orchestrator_runs"}


def _result_artifacts(project_dir: Path, request: dict[str, Any], result: dict[str, Any] | None = None) -> list[str]:
    result = result or {}
    if request["run_type"] == "typed_orchestration" and (project_dir / "v4" / "typed_orchestration_last_run.json").exists():
        return ["v4/typed_orchestration_last_run.json", "v4/typed_orchestration_graph.json"]
    if request["run_type"] == "partial_rerun":
        return sorted(set(str(item) for item in result.get("artifacts", []) if item))
    if request["run_type"] == "work_order_dag":
        artifacts = [result.get("artifact", "")]
        for node in result.get("node_results", []):
            artifacts.extend(str(item) for item in node.get("artifacts", []) if item)
            qc = node.get("task_qc_report", {})
            if qc.get("qc_report_id"):
                artifacts.append(f"results/qc/{qc['qc_report_id']}.json")
        return sorted(set(item for item in artifacts if item))
    return []


def _normalize_status(status: str) -> str:
    if status in {"success", "failed", "blocked", "cancelled", "cancel_requested"}:
        return status
    return "success" if status == "ok" else status


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
