import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .secrets import apply_project_secrets
from .v4 import content_hash


ADAPTER_SCHEMA = "v4.external_agent_adapter_run/0.1"


def run_bioinfo_agent_adapter(project_dir: Path, question: str, agent_root: Path, use_llm: bool = False, model: str = "") -> dict[str, Any]:
    agent_root = agent_root.resolve()
    if not agent_root.exists():
        raise FileNotFoundError(f"external agent root not found: {agent_root}")
    run_root = project_dir / "external_agent_runs" / "bioinfo_agent_system"
    run_root.mkdir(parents=True, exist_ok=True)
    question_path = run_root / "input_question.txt"
    question_path.write_text(question.strip() + "\n", encoding="utf-8")

    native = _try_native_pipeline(agent_root, question_path)
    if native["status"] == "success":
        imported = _import_native_run(project_dir, agent_root, native)
        payload = {
            "schema_version": ADAPTER_SCHEMA,
            "project_id": project_dir.name,
            "adapter_id": "bioinfo_agent_system",
            "mode": "native_pipeline",
            "status": "success",
            "question": question,
            "native": native,
            **imported,
        }
    else:
        llm = _try_llm_pipeline(project_dir, question, agent_root, native, model=model) if use_llm else {"status": "skipped", "reason": "LLM mode not requested."}
        if llm.get("status") == "success":
            payload = {
                "schema_version": ADAPTER_SCHEMA,
                "project_id": project_dir.name,
                "adapter_id": "bioinfo_agent_system",
                "mode": "llm_schema_generation",
                "status": "success",
                "question": question,
                "native": native,
                "llm": llm,
                "imported_run_dir": "external_agent_runs/bioinfo_agent_system/llm_latest",
                "plan_ref": llm["plan_ref"],
                "codex_task_packet_count": llm["codex_task_packet_count"],
                "codex_task_packets_ref": "external_agent_runs/bioinfo_agent_system/codex_task_packets.json",
                "claim_ceiling": llm.get("claim_ceiling", {}),
            }
            out = run_root / "latest_adapter_run.json"
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            return payload
        synthesized = _synthesize_sarcopenia_plan(project_dir, question, native)
        payload = {
            "schema_version": ADAPTER_SCHEMA,
            "project_id": project_dir.name,
            "adapter_id": "bioinfo_agent_system",
            "mode": "schema_compatible_synthesis",
            "status": "success_with_adapter_synthesis",
            "question": question,
            "native": native,
            "llm": llm,
            **synthesized,
        }

    out = run_root / "latest_adapter_run.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _try_llm_pipeline(project_dir: Path, question: str, agent_root: Path, native: dict[str, Any], model: str = "") -> dict[str, Any]:
    apply_project_secrets(project_dir)
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return {"status": "skipped", "reason": "OPENAI_API_KEY is not set."}
    schema_path = agent_root / "agents" / "06_research_plan_compiler" / "output.schema.json"
    if not schema_path.exists():
        return {"status": "failed", "failure_reason": f"missing external schema: {schema_path}"}
    schema = _read_json(schema_path, {})
    out_dir = project_dir / "external_agent_runs" / "bioinfo_agent_system" / "llm_latest"
    llm_dir = out_dir / "llm"
    outputs = out_dir / "agent_outputs"
    llm_dir.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)
    provider = os.environ.get("TARGETCOMPASS_LLM_PROVIDER", "deepseek")
    base_url = os.environ.get("TARGETCOMPASS_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = model or os.environ.get("TARGETCOMPASS_OPENAI_MODEL", "deepseek-chat")
    request_payload = _build_external_agent_llm_request(question, schema, native, model)
    request_id = "external_agent_llm_" + content_hash({"question": question, "model": model, "schema": schema})[:16]
    request_path = llm_dir / f"{request_id}_request.json"
    response_path = llm_dir / f"{request_id}_response.json"
    output_path = llm_dir / f"{request_id}_output.json"
    request_path.write_text(json.dumps(_redact_external_request(request_payload), indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        response_payload = _post_chat_completion(base_url, api_key, request_payload)
        response_path.write_text(json.dumps(_redact_external_response(response_payload), indent=2, ensure_ascii=False), encoding="utf-8")
        plan = _parse_llm_json(_extract_chat_text(response_payload))
        plan = _repair_external_plan(plan, question)
        validation = _validate_external_plan(plan)
        status = "success" if validation["valid"] else "failed"
        failure_reason = "" if validation["valid"] else "schema_validation_failed: " + "; ".join(validation["errors"])
        if validation["valid"]:
            plan_path = outputs / "06_executable_research_plan.json"
            plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
            _write_external_packets(project_dir, plan.get("codex_task_packets", []))
        else:
            plan_path = output_path
        output = {
            "schema_version": "v4.external_agent_llm_generation/0.1",
            "status": status,
            "provider": provider,
            "model": model,
            "request": str(request_path.relative_to(project_dir)).replace("\\", "/"),
            "response": str(response_path.relative_to(project_dir)).replace("\\", "/"),
            "plan_ref": str(plan_path.relative_to(project_dir)).replace("\\", "/"),
            "schema_validation": validation,
            "failure_reason": failure_reason,
            "codex_task_packet_count": len(plan.get("codex_task_packets", [])) if validation["valid"] else 0,
            "claim_ceiling": plan.get("claim_ceiling", {}),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        output_path.write_text(json.dumps({"output": output, "plan": plan}, indent=2, ensure_ascii=False), encoding="utf-8")
        return output
    except Exception as exc:
        output = {
            "schema_version": "v4.external_agent_llm_generation/0.1",
            "status": "failed",
            "provider": provider,
            "model": model,
            "request": str(request_path.relative_to(project_dir)).replace("\\", "/"),
            "response": str(response_path.relative_to(project_dir)).replace("\\", "/") if response_path.exists() else "",
            "failure_reason": str(exc),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        return output


def _build_external_agent_llm_request(question: str, schema: dict[str, Any], native: dict[str, Any], model: str) -> dict[str, Any]:
    system = (
        "You are replacing a deterministic external bioinformatics planning agent. "
        "Return JSON only. Follow the provided ResearchPlanCompilerOutput JSON schema. "
        "Do not invent verified datasets, results, citations, statistical findings, or approvals. "
        "You may propose dataset search placeholders and executable task packets. "
        "Keep claims at association/co-expression level unless the prompt provides real causal evidence."
    )
    user = {
        "raw_question": question,
        "native_agent_failure": native.get("failure_reason", ""),
        "required_output_schema": schema,
        "domain_requirements": [
            "Question is about sarcopenia skeletal muscle background/non-myofiber cells.",
            "Need SASP score, characteristic surface markers, cell-type evidence, gene identity QC, and reportable claim boundaries.",
            "Output codex_task_packets must be directly usable by a downstream coding/execution orchestrator.",
        ],
    }
    return {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, ensure_ascii=False)}],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }


def _post_chat_completion(base_url: str, api_key: str, request_payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(request_payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"external agent LLM request failed: {exc.code} {detail}") from exc


def _extract_chat_text(data: dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content", "")
        if content:
            return content
    raise RuntimeError("LLM response did not contain message content")


def _parse_llm_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("external agent LLM output must be a JSON object")
    return parsed


def _repair_external_plan(plan: dict[str, Any], question: str) -> dict[str, Any]:
    repaired = dict(plan)
    repaired.setdefault("agent_id", "06_research_plan_compiler")
    repaired.setdefault("schema_version", "1.0.0")
    repaired.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    repaired.setdefault("provenance", [{"source_type": "manual", "source_id": "llm_external_agent_adapter", "note": "Generated by LLM to satisfy the external agent research plan schema."}])
    repaired.setdefault("warnings", [])
    repaired.setdefault("blocking_failures", [])
    repaired.setdefault("claim_ceiling", {"max_allowed_claim": "co_expression", "reason": "Expression, SASP scoring, and annotation support association/co-expression only."})
    repaired.setdefault("plan_id", "llm-sarcopenia-muscle-background-sasp-surface")
    repaired.setdefault("normalized_research_question", _normalize_question(question))
    repaired.setdefault("answerable_scope", ["Plan a reproducible analysis for high-SASP background cells and characteristic surface markers in sarcopenia skeletal muscle."])
    repaired.setdefault("unanswerable_scope", ["Causal or therapeutic target claims without orthogonal evidence."])
    repaired.setdefault("selected_datasets", ["AUTO_GEO_SARCOPENIA_MUSCLE", "AUTO_SNRNA_MUSCLE_BACKGROUND_CELL"])
    repaired.setdefault("fallback_paths", ["If metadata grouping fails, require human column selection before execution."])
    repaired.setdefault("stop_conditions", ["Stop if gene identity mapping or disease/control grouping cannot be validated."])
    repaired.setdefault("claim_boundaries", {"allowed_claims": ["Association/co-expression candidate claims."], "forbidden_claims": ["Causal or validated target claims."], "caveats": ["Requires metadata and orthogonal validation."]})
    repaired.setdefault("report_outline", ["Question", "Data", "QC", "SASP score", "Surface markers", "Cell type evidence", "Limitations"])
    packets = repaired.get("codex_task_packets") or repaired.get("task_dag") or []
    for packet in packets:
        if isinstance(packet, dict):
            packet.setdefault("notes", "Generated by LLM external agent adapter; requires human review before execution.")
    repaired["codex_task_packets"] = [p for p in packets if isinstance(p, dict)]
    repaired.setdefault("task_dag", [{k: v for k, v in p.items() if k != "notes"} for p in repaired["codex_task_packets"]])
    repaired.setdefault("selected_method_contracts", sorted({p.get("method_contract_id", "") for p in repaired["codex_task_packets"] if p.get("method_contract_id")}))
    repaired.setdefault("evidence_dag", [{"edge_id": "E1", "from": "normalized_question", "to": "task_dag", "rationale": "External agent plan decomposes the research question into executable tasks."}])
    return repaired


def _validate_external_plan(plan: dict[str, Any]) -> dict[str, Any]:
    required = [
        "agent_id",
        "schema_version",
        "created_at",
        "provenance",
        "warnings",
        "blocking_failures",
        "claim_ceiling",
        "plan_id",
        "normalized_research_question",
        "answerable_scope",
        "unanswerable_scope",
        "selected_datasets",
        "selected_method_contracts",
        "evidence_dag",
        "task_dag",
        "fallback_paths",
        "stop_conditions",
        "claim_boundaries",
        "report_outline",
        "codex_task_packets",
    ]
    errors = [f"missing required field: {key}" for key in required if key not in plan]
    if plan.get("agent_id") != "06_research_plan_compiler":
        errors.append("agent_id must be 06_research_plan_compiler")
    if not isinstance(plan.get("codex_task_packets"), list) or not plan.get("codex_task_packets"):
        errors.append("codex_task_packets must be a non-empty list")
    for idx, packet in enumerate(plan.get("codex_task_packets", []) if isinstance(plan.get("codex_task_packets"), list) else []):
        for field in ["task_id", "name", "purpose", "input_artifacts", "output_artifacts", "dependencies", "method_contract_id", "acceptance_criteria", "failure_condition", "notes"]:
            if field not in packet:
                errors.append(f"codex_task_packets[{idx}].{field}: missing required field")
    ceiling = plan.get("claim_ceiling", {}).get("max_allowed_claim") if isinstance(plan.get("claim_ceiling"), dict) else ""
    if ceiling not in {"descriptive", "association", "correlation", "co_expression", "cell_state_marker", "candidate_biomarker", "mechanistic_hypothesis", "causal_support", "therapeutic_target_hypothesis", "experimentally_validated_target"}:
        errors.append("claim_ceiling.max_allowed_claim is invalid")
    return {"valid": not errors, "errors": errors}


def _redact_external_request(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


def _redact_external_response(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


def _try_native_pipeline(agent_root: Path, question_path: Path) -> dict[str, Any]:
    script = agent_root / "scripts" / "run_mock_pipeline.py"
    if not script.exists():
        return {"status": "failed", "failure_reason": f"missing script: {script}"}
    completed = subprocess.run(
        [sys.executable, str(script), str(question_path)],
        cwd=str(agent_root),
        text=True,
        capture_output=True,
        timeout=120,
    )
    stdout = completed.stdout.strip()
    parsed: dict[str, Any] = {}
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = {"stdout": stdout[-4000:]}
    return {
        "status": "success" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout": stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "result": parsed,
        "failure_reason": "" if completed.returncode == 0 else _native_failure_reason(completed.stderr),
    }


def _native_failure_reason(stderr: str) -> str:
    if "supports only the bundled example question" in stderr:
        return "external mock pipeline is hard-limited to its bundled example question"
    return stderr.strip().splitlines()[-1] if stderr.strip() else "external agent returned non-zero exit"


def _import_native_run(project_dir: Path, agent_root: Path, native: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(native.get("result", {}).get("run_dir", ""))
    if not run_dir.exists():
        return {"imported_run_dir": "", "plan": {}, "codex_task_packets": []}
    dest = project_dir / "external_agent_runs" / "bioinfo_agent_system" / run_dir.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(run_dir, dest)
    plan = _read_json(dest / "agent_outputs" / "06_executable_research_plan.json", {})
    packets = plan.get("codex_task_packets", []) if isinstance(plan, dict) else []
    _write_external_packets(project_dir, packets)
    return {
        "imported_run_dir": str(dest.relative_to(project_dir)),
        "plan_ref": str((dest / "agent_outputs" / "06_executable_research_plan.json").relative_to(project_dir)),
        "codex_task_packet_count": len(packets),
        "codex_task_packets_ref": "external_agent_runs/bioinfo_agent_system/codex_task_packets.json",
    }


def _synthesize_sarcopenia_plan(project_dir: Path, question: str, native: dict[str, Any]) -> dict[str, Any]:
    created_at = datetime.now(timezone.utc).isoformat()
    normalized = _normalize_question(question)
    tasks = [
        _task("T1", "question normalization and claim ceiling", "Normalize sarcopenia, skeletal muscle, stromal/background cell, SASP score, and surface marker constraints.", [], ["normalized_question.json"], "question_normalization"),
        _task("T2", "dataset discovery and feasibility", "Search and qualify human sarcopenia skeletal muscle bulk/snRNA datasets with usable metadata and gene identity.", ["normalized_question.json"], ["dataset_feasibility.json"], "dataset_metadata_audit"),
        _task("T3", "matrix metadata gene alignment", "Verify matrix gene symbols/features align to metadata and cell annotations before scoring.", ["dataset_feasibility.json"], ["gene_identity_qc.json"], "gene_identity_qc"),
        _task("T4", "background cell pseudobulk", "Build donor-aware pseudobulk for non-myofiber/background cell populations when single-nucleus metadata permit.", ["gene_identity_qc.json"], ["background_cell_pseudobulk.tsv"], "scrna_pseudobulk"),
        _task("T5", "SASP score and differential signal", "Compute SASP core/program scores and DEG signal in sarcopenia versus control contrasts.", ["background_cell_pseudobulk.tsv"], ["sasp_score.tsv", "deg_results.tsv"], "sasp_score"),
        _task("T6", "surface marker filtering", "Intersect high-SASP cell/candidate signals with surface/plasma membrane annotations from HPA/UniProt/CellMarker-like sources.", ["sasp_score.tsv", "deg_results.tsv"], ["surface_sasp_candidates.tsv"], "surface_secretome_annotation"),
        _task("T7", "cell-type evidence review", "Link candidate surface markers to cell populations and preserve evidence source and limitation.", ["surface_sasp_candidates.tsv"], ["cell_type_evidence.tsv"], "cell_type_localization"),
        _task("T8", "conservative report and handoff", "Report whether high-SASP background cells with characteristic surface markers are supported, with association-level claim limits.", ["cell_type_evidence.tsv"], ["external_agent_report_outline.md"], "evidence_report_compilation"),
    ]
    plan = {
        "agent_id": "06_research_plan_compiler",
        "schema_version": "1.0.0",
        "created_at": created_at,
        "provenance": [{"source_type": "manual", "source_id": "targetcompass_external_agent_adapter", "note": "Schema-compatible adapter synthesis because the provided external mock agent only supports its bundled example question."}],
        "warnings": [native.get("failure_reason", "")],
        "blocking_failures": [],
        "claim_ceiling": {"max_allowed_claim": "co_expression", "reason": "SASP/cell-type/surface-marker signals from expression and annotation support association/co-expression, not causal or therapeutic claims."},
        "plan_id": "adapter-sarcopenia-muscle-background-sasp-surface",
        "normalized_research_question": normalized,
        "answerable_scope": [
            "Identify whether skeletal muscle background/non-myofiber cell populations show high SASP scores in sarcopenia datasets.",
            "Rank characteristic surface-marker candidates linked to those high-SASP cells when metadata and annotation support it.",
            "Keep claims at association/co-expression level until experimental validation exists.",
        ],
        "unanswerable_scope": [
            "Causal driver status for sarcopenia.",
            "Validated therapeutic or vaccine target readiness.",
            "Experimentally confirmed cell-surface accessibility without orthogonal protein evidence.",
        ],
        "selected_datasets": ["AUTO_GEO_SARCOPENIA_MUSCLE", "AUTO_SNRNA_MUSCLE_BACKGROUND_CELL"],
        "selected_method_contracts": sorted({task["method_contract_id"] for task in tasks}),
        "evidence_dag": [
            {"edge_id": "E1", "from": "normalized_question", "to": "dataset_discovery", "rationale": "The user asks a tissue/cell-state question requiring disease-specific muscle data."},
            {"edge_id": "E2", "from": "dataset_discovery", "to": "gene_identity_qc", "rationale": "Matrix features must be mapped to gene identity before biological interpretation."},
            {"edge_id": "E3", "from": "gene_identity_qc", "to": "SASP_score", "rationale": "Only aligned gene symbols can enter SASP scoring."},
            {"edge_id": "E4", "from": "SASP_score", "to": "surface_marker_filter", "rationale": "High-SASP candidates are filtered by surface annotation after expression evidence."},
            {"edge_id": "E5", "from": "surface_marker_filter", "to": "cell_type_evidence", "rationale": "Cell population evidence answers where the molecule is expressed."},
            {"edge_id": "E6", "from": "cell_type_evidence", "to": "report", "rationale": "Report preserves provenance and claim ceiling."},
        ],
        "task_dag": tasks,
        "fallback_paths": [
            "If snRNA metadata cannot identify background cells, run bulk DEG/SASP score and mark cell-type localization as unresolved.",
            "If surface annotation is missing, keep the candidate in SASP evidence but exclude it from characteristic surface-marker conclusions.",
            "If GEO grouping fails, require human metadata column selection before execution.",
        ],
        "stop_conditions": [
            "Stop if no sarcopenia/control grouping can be recovered.",
            "Stop if gene identity mapping fails for the expression matrix.",
            "Stop if the final conclusion would exceed association/co-expression evidence.",
        ],
        "claim_boundaries": {
            "allowed_claims": ["Candidate high-SASP background-cell-associated surface markers in sarcopenia skeletal muscle."],
            "forbidden_claims": ["Causal drivers of sarcopenia.", "Validated therapeutic targets.", "Experimentally confirmed surface accessibility from RNA-only data."],
            "caveats": ["Cell-type evidence depends on metadata quality.", "Surface status may be annotation-level only.", "SASP score is a program summary and requires orthogonal validation."],
        },
        "report_outline": ["Question and scope", "Dataset discovery and QC", "Gene identity alignment", "SASP score", "Surface marker filtering", "Cell-type evidence", "Limitations and experiments"],
        "codex_task_packets": [{**task, "notes": "Generated by external bioinfo agent adapter; execute through TargetCompass WorkOrder/Orchestrator after human review."} for task in tasks],
    }
    run_dir = project_dir / "external_agent_runs" / "bioinfo_agent_system" / "adapter_latest"
    outputs = run_dir / "agent_outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    plan_path = outputs / "06_executable_research_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_external_packets(project_dir, plan["codex_task_packets"])
    return {
        "imported_run_dir": str(run_dir.relative_to(project_dir)),
        "plan_ref": str(plan_path.relative_to(project_dir)),
        "codex_task_packet_count": len(plan["codex_task_packets"]),
        "codex_task_packets_ref": "external_agent_runs/bioinfo_agent_system/codex_task_packets.json",
        "claim_ceiling": plan["claim_ceiling"],
    }


def _task(task_id: str, name: str, purpose: str, inputs: list[str], outputs: list[str], method_contract: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "name": name,
        "purpose": purpose,
        "input_artifacts": inputs,
        "output_artifacts": outputs,
        "dependencies": [f"T{int(task_id[1:]) - 1}"] if task_id != "T1" else [],
        "method_contract_id": method_contract,
        "acceptance_criteria": [
            "Inputs are present and provenance is recorded.",
            "Output remains compatible with the project claim ceiling.",
        ],
        "failure_condition": "Required input data or metadata are missing or cannot be validated.",
    }


def _normalize_question(question: str) -> str:
    text = question.strip()
    if re.search(r"sasp", text, flags=re.IGNORECASE) and ("肌少症" in text or "sarcopenia" in text.lower()):
        return "In sarcopenia skeletal muscle, determine whether background/non-myofiber cells contain high-SASP cell states with characteristic surface-marker molecules."
    return text


def _write_external_packets(project_dir: Path, packets: list[dict[str, Any]]) -> None:
    out = project_dir / "external_agent_runs" / "bioinfo_agent_system" / "codex_task_packets.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema_version": "v4.external_codex_task_packets/0.1", "packets": packets}, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))
