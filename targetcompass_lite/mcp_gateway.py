import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .v4 import content_hash, file_hash, read_json, v4_dir


RESOURCE_SCHEMA = "v4.mcp_resource_manifest/0.2"
TOOL_SCHEMA = "v4.mcp_tool_manifest/0.1"
AUDIT_SCHEMA = "v4.mcp_call_audit/0.1"


TOOL_CONTRACTS = [
    {
        "tool_id": "resource.read",
        "purpose": "Read a registered project resource through the local gateway.",
        "risk": "read_only",
        "requires_review": False,
        "input_schema": {"uri": "string"},
        "output_schema": "McpResourceReadResult",
        "handler": "targetcompass_lite.mcp_gateway.read_resource",
    },
    {
        "tool_id": "v4.build_manifest",
        "purpose": "Rebuild v4 object, resource, tool, and state manifests.",
        "risk": "project_metadata_write",
        "requires_review": False,
        "input_schema": {},
        "output_schema": "V4ObjectManifest",
        "handler": "targetcompass_lite.v4.build_v4_manifest",
    },
    {
        "tool_id": "review.queue.build",
        "purpose": "Build the human review queue for work orders, Codex packets, and final signoff.",
        "risk": "review_metadata_write",
        "requires_review": False,
        "input_schema": {},
        "output_schema": "ReviewQueue",
        "handler": "targetcompass_lite.review.build_review_queue",
    },
    {
        "tool_id": "evidence.index.build",
        "purpose": "Build the EvidenceItem -> ReviewItem -> ReportRef traceability index.",
        "risk": "project_metadata_write",
        "requires_review": False,
        "input_schema": {},
        "output_schema": "EvidenceReviewReportIndex",
        "handler": "targetcompass_lite.evidence_index.build_evidence_review_report_index",
    },
    {
        "tool_id": "evidence.trace.query",
        "purpose": "Query EvidenceItem -> ReviewItem -> ReportRef links by gene, evidence_id, or review_status.",
        "risk": "read_only",
        "requires_review": False,
        "input_schema": {"gene": "string", "evidence_id": "string", "review_status": "string"},
        "output_schema": "EvidenceTraceQueryResult",
        "handler": "targetcompass_lite.evidence_index.query_evidence_trace",
    },
    {
        "tool_id": "knowledge.adapt_resources",
        "purpose": "Normalize registered knowledge/database resources through configured adapters.",
        "risk": "project_data_write",
        "requires_review": True,
        "input_schema": {},
        "output_schema": "KnowledgeAdaptationResult",
        "handler": "targetcompass_lite.knowledge.adapt_resources",
    },
    {
        "tool_id": "codex.task_packet.inspect",
        "purpose": "Inspect generated Codex task packets without executing code.",
        "risk": "read_only",
        "requires_review": False,
        "input_schema": {"work_order_id": "string"},
        "output_schema": "CodexTaskPacket",
        "handler": "targetcompass_lite.mcp_gateway.inspect_codex_task_packet",
    },
    {
        "tool_id": "method.registry.list",
        "purpose": "List replaceable method/agent contracts registered for this project.",
        "risk": "read_only",
        "requires_review": False,
        "input_schema": {},
        "output_schema": "MethodRegistry",
        "handler": "targetcompass_lite.methods.registry.available_project_methods",
    },
    {
        "tool_id": "method.config.read",
        "purpose": "Read the selected replaceable method config for this project.",
        "risk": "read_only",
        "requires_review": False,
        "input_schema": {},
        "output_schema": "MethodConfig",
        "handler": "targetcompass_lite.methods.registry.load_method_config",
    },
    {
        "tool_id": "method.config.update",
        "purpose": "Update selected replaceable methods. This is a project policy change and must be reviewed.",
        "risk": "project_policy_write",
        "requires_review": True,
        "input_schema": {"config": "object"},
        "output_schema": "MethodConfig",
        "handler": "targetcompass_lite.methods.registry.save_method_config",
    },
    {
        "tool_id": "role.runs.list",
        "purpose": "List audited v4 role/agent runs including method, model, parameters hash, and packets.",
        "risk": "read_only",
        "requires_review": False,
        "input_schema": {},
        "output_schema": "RoleRunIndex",
        "handler": "targetcompass_lite.role_runner.load_role_runs",
    },
    {
        "tool_id": "role.run.inspect",
        "purpose": "Inspect one audited role run input packet, output packet, and log.",
        "risk": "read_only",
        "requires_review": False,
        "input_schema": {"role_run_id": "string"},
        "output_schema": "RoleRunDetail",
        "handler": "targetcompass_lite.mcp_gateway.inspect_role_run",
    },
]


def build_mcp_gateway(project_dir: Path, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    tools = build_tool_manifest(project_dir)
    audit = summarize_call_audit(project_dir)
    resources = build_resource_manifest(project_dir, plan)
    return {"resources": resources, "tools": tools, "audit": audit}


def build_resource_manifest(project_dir: Path, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    resources = _discover_core_resources(project_dir)
    payload = {
        "schema_version": RESOURCE_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "policy": {
            "mcp_is_gateway_not_state_store": True,
            "write_tools_must_call_orchestrator": True,
            "large_objects_are_referenced_by_artifact_path": True,
            "resource_reads_are_audited": True,
        },
        "resources": resources,
    }
    path = v4_dir(project_dir) / "mcp_resources.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def build_tool_manifest(project_dir: Path) -> dict[str, Any]:
    tools = []
    for contract in TOOL_CONTRACTS:
        public = {key: value for key, value in contract.items() if key != "handler"}
        public["contract_hash"] = content_hash(public)
        tools.append(public)
    payload = {
        "schema_version": TOOL_SCHEMA,
        "project_id": project_dir.name,
        "generated_at": _now(),
        "policy": {
            "tool_calls_are_audited": True,
            "write_tools_require_contract": True,
            "review_required_tools_cannot_self_approve": True,
        },
        "tools": tools,
    }
    path = v4_dir(project_dir) / "mcp_tools.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def call_tool(project_dir: Path, tool_id: str, arguments: dict[str, Any] | None = None, actor: str = "local_gateway") -> Any:
    arguments = arguments or {}
    contract = _tool_contract(tool_id)
    started_at = _now()
    status = "success"
    failure_reason = ""
    result: Any = None
    try:
        result = _dispatch_tool(project_dir, tool_id, arguments)
        return result
    except Exception as exc:
        status = "failed"
        failure_reason = str(exc)
        raise
    finally:
        record_call(
            project_dir,
            {
                "schema_version": AUDIT_SCHEMA,
                "call_id": "mcp_call_" + content_hash({"tool": tool_id, "args": arguments, "started_at": started_at})[:16],
                "tool_id": tool_id,
                "actor": actor,
                "risk": contract.get("risk", ""),
                "requires_review": contract.get("requires_review", False),
                "arguments_hash": content_hash(arguments),
                "status": status,
                "failure_reason": failure_reason,
                "started_at": started_at,
                "finished_at": _now(),
                "result_summary": _summarize(result),
            },
        )


def read_resource(project_dir: Path, uri: str, actor: str = "local_gateway") -> dict[str, Any]:
    result = _read_resource(project_dir, uri)
    record_call(
        project_dir,
        {
            "schema_version": AUDIT_SCHEMA,
            "call_id": "mcp_call_" + content_hash({"resource": uri, "hash": result["content_hash"], "time": _now()})[:16],
            "tool_id": "resource.read",
            "actor": actor,
            "risk": "read_only",
            "requires_review": False,
            "arguments_hash": content_hash({"uri": uri}),
            "status": "success",
            "failure_reason": "",
            "started_at": _now(),
            "finished_at": _now(),
            "result_summary": {"path": result["path"], "bytes": len(result["text"].encode("utf-8"))},
        },
    )
    return result


def _read_resource(project_dir: Path, uri: str) -> dict[str, Any]:
    manifest = build_resource_manifest(project_dir)
    resource = next((row for row in manifest["resources"] if row.get("uri") == uri), None)
    if not resource:
        raise ValueError(f"resource not registered: {uri}")
    path = project_dir / resource["path"]
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    result = {
        "schema_version": "v4.mcp_resource_read/0.1",
        "uri": uri,
        "path": resource["path"],
        "content_hash": file_hash(path),
        "text": text,
    }
    return result


def inspect_codex_task_packet(project_dir: Path, work_order_id: str) -> dict[str, Any]:
    orders = read_json(v4_dir(project_dir) / "work_orders.json", {}).get("work_orders", [])
    order = next((row for row in orders if row.get("work_order_id") == work_order_id), None)
    if not order:
        raise ValueError(f"work order not found: {work_order_id}")
    rel = order.get("codex_task_packet", "")
    if not rel:
        return {}
    return read_json(project_dir / rel, {})


def inspect_role_run(project_dir: Path, role_run_id: str) -> dict[str, Any]:
    from .role_runner import load_role_runs

    runs = load_role_runs(project_dir).get("runs", [])
    record = next((row for row in runs if row.get("role_run_id") == role_run_id), None)
    if not record:
        raise ValueError(f"role run not found: {role_run_id}")
    input_packet = read_json(project_dir / record.get("input_packet", ""), {})
    output_packet = read_json(project_dir / record.get("output_packet", ""), {})
    log_path = project_dir / record.get("log", "")
    return {
        "schema_version": "v4.role_run_detail/0.1",
        "project_id": project_dir.name,
        "record": record,
        "input_packet": input_packet,
        "output_packet": output_packet,
        "log": log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else "",
    }


def audit_log_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "mcp_call_audit.jsonl"


def audit_summary_path(project_dir: Path) -> Path:
    return v4_dir(project_dir) / "mcp_call_audit_summary.json"


def record_call(project_dir: Path, record: dict[str, Any]) -> dict[str, Any]:
    path = audit_log_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    summarize_call_audit(project_dir)
    return record


def load_call_audit(project_dir: Path) -> list[dict[str, Any]]:
    path = audit_log_path(project_dir)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def summarize_call_audit(project_dir: Path) -> dict[str, Any]:
    rows = load_call_audit(project_dir)
    by_tool: dict[str, int] = {}
    failures = 0
    for row in rows:
        by_tool[row.get("tool_id", "")] = by_tool.get(row.get("tool_id", ""), 0) + 1
        failures += 1 if row.get("status") == "failed" else 0
    payload = {
        "schema_version": "v4.mcp_call_audit_summary/0.1",
        "project_id": project_dir.name,
        "call_count": len(rows),
        "failure_count": failures,
        "by_tool": by_tool,
        "latest_calls": rows[-20:],
        "updated_at": _now(),
    }
    audit_summary_path(project_dir).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _discover_core_resources(project_dir: Path) -> list[dict[str, Any]]:
    entries = [
        (f"project://{project_dir.name}", project_dir / "research_interest.md", "read"),
        (f"spec://{project_dir.name}/research/latest", project_dir / "research_spec.json", "read"),
        (f"spec://{project_dir.name}/disease/latest", v4_dir(project_dir) / "disease_spec.json", "read"),
        (f"plan://{project_dir.name}/latest", project_dir / "analysis_plan.json", "read"),
        (f"work-order://{project_dir.name}/index", v4_dir(project_dir) / "work_orders.json", "read"),
        (f"work-order-dag://{project_dir.name}/latest", v4_dir(project_dir) / "work_order_dag.json", "read"),
        (f"role-run://{project_dir.name}/index", v4_dir(project_dir) / "role_runs.json", "read"),
        (f"method-registry://{project_dir.name}/config", project_dir / "configs" / "agent_methods.json", "read"),
        (f"evidence://{project_dir.name}/snapshot/latest", v4_dir(project_dir) / "evidence_snapshot.json", "read"),
        (f"evidence://{project_dir.name}/review-report-index/latest", v4_dir(project_dir) / "evidence_review_report_index.json", "read"),
        (f"mcp-tool://{project_dir.name}/index", v4_dir(project_dir) / "mcp_tools.json", "read"),
        (f"mcp-audit://{project_dir.name}/summary", audit_summary_path(project_dir), "read"),
        (f"registry-snapshot://{project_dir.name}/latest", v4_dir(project_dir) / "registry_snapshots.json", "read"),
    ]
    resources = []
    for uri, path, access in entries:
        if path.exists():
            resources.append(
                {
                    "uri": uri,
                    "path": str(path.relative_to(project_dir)),
                    "access": access,
                    "content_hash": file_hash(path),
                    "version": "0.1",
                    "resource_type": _resource_type(uri),
                }
            )
    return resources


def _dispatch_tool(project_dir: Path, tool_id: str, arguments: dict[str, Any]) -> Any:
    if tool_id == "resource.read":
        return _read_resource(project_dir, arguments["uri"])
    if tool_id == "v4.build_manifest":
        from .v4 import build_v4_manifest

        return build_v4_manifest(project_dir)
    if tool_id == "review.queue.build":
        from .review import build_review_queue

        return build_review_queue(project_dir)
    if tool_id == "evidence.index.build":
        from .evidence_index import build_evidence_review_report_index

        return build_evidence_review_report_index(project_dir)
    if tool_id == "evidence.trace.query":
        from .evidence_index import query_evidence_trace

        return query_evidence_trace(
            project_dir,
            gene=arguments.get("gene", ""),
            evidence_id=arguments.get("evidence_id", ""),
            review_status=arguments.get("review_status", ""),
        )
    if tool_id == "knowledge.adapt_resources":
        from .knowledge import adapt_resources

        return adapt_resources(project_dir)
    if tool_id == "codex.task_packet.inspect":
        return inspect_codex_task_packet(project_dir, arguments["work_order_id"])
    if tool_id == "method.registry.list":
        from .methods.registry import available_project_methods

        return {
            "schema_version": "v4.method_registry/0.1",
            "project_id": project_dir.name,
            "methods": available_project_methods(project_dir),
        }
    if tool_id == "method.config.read":
        from .methods.registry import load_method_config

        return {
            "schema_version": "v4.method_config/0.1",
            "project_id": project_dir.name,
            "config": load_method_config(project_dir),
        }
    if tool_id == "method.config.update":
        from .methods.registry import save_method_config

        return {
            "schema_version": "v4.method_config/0.1",
            "project_id": project_dir.name,
            "config": save_method_config(project_dir, arguments.get("config", {})),
        }
    if tool_id == "role.runs.list":
        from .role_runner import load_role_runs

        return load_role_runs(project_dir)
    if tool_id == "role.run.inspect":
        return inspect_role_run(project_dir, arguments["role_run_id"])
    raise ValueError(f"unsupported tool: {tool_id}")


def _tool_contract(tool_id: str) -> dict[str, Any]:
    for contract in TOOL_CONTRACTS:
        if contract["tool_id"] == tool_id:
            return contract
    raise ValueError(f"unknown tool contract: {tool_id}")


def _resource_type(uri: str) -> str:
    return uri.split("://", 1)[0]


def _summarize(result: Any) -> Any:
    if isinstance(result, dict):
        return {"keys": sorted(str(key) for key in result.keys())[:20]}
    if isinstance(result, list):
        return {"count": len(result)}
    if isinstance(result, (str, int, float, bool)) or result is None:
        return result
    return str(result)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
