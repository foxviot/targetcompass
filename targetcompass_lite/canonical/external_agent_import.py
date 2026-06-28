from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agent_specs import build_agent_specs
from .ids import hash_payload, make_stable_id
from .schemas import now_iso


EXTERNAL_TO_V5_AGENT = {
    "01_scientific_question_normalizer": "question_normalizer",
    "02_scope_ontology_resolver": "scope_resolver",
    "03_evidence_dataset_scout": "resource_discovery_agent",
    "04_method_extraction_agent": "evidence_plan_builder",
    "05_method_motif_feasibility_synthesizer": "method_adapter_workorder_compiler",
    "06_research_plan_compiler": "method_adapter_workorder_compiler",
}

REFERENCE_ONLY_STATUS = "imported_reference"


def discover_external_agent_specs(agent_root: str | Path) -> list[dict[str, Any]]:
    root = _resolve_agent_root(agent_root)
    agents_dir = root / "agents"
    if not agents_dir.exists():
        raise FileNotFoundError(f"external agents directory not found: {agents_dir}")

    discovered = []
    for agent_dir in sorted(item for item in agents_dir.iterdir() if item.is_dir()):
        agent_id = agent_dir.name
        agent_md = agent_dir / "agent.md"
        input_schema = agent_dir / "input.schema.json"
        output_schema = agent_dir / "output.schema.json"
        eval_cases = agent_dir / "eval_cases.jsonl"
        if not agent_md.exists() or not input_schema.exists() or not output_schema.exists():
            continue
        discovered.append(
            {
                "external_agent_id": agent_id,
                "external_agent_dir": str(agent_dir),
                "agent_md_path": str(agent_md),
                "input_schema_path": str(input_schema),
                "output_schema_path": str(output_schema),
                "eval_cases_path": str(eval_cases) if eval_cases.exists() else "",
                "agent_md": agent_md.read_text(encoding="utf-8"),
                "input_schema": _read_json(input_schema),
                "output_schema": _read_json(output_schema),
                "eval_case_count": _count_jsonl(eval_cases) if eval_cases.exists() else 0,
                "mapped_v5_agent": map_external_agent_to_v5_agent(agent_id),
                "import_status": REFERENCE_ONLY_STATUS,
                "reference_only": True,
                "imported_as_evidence": False,
            }
        )
    return discovered


def import_external_agent_contracts(project_dir: str | Path, agent_root: str | Path) -> dict[str, Any]:
    project_dir = Path(project_dir)
    discovered = discover_external_agent_specs(agent_root)
    import_result = {
        "schema_version": "v5.external_agent_import/0.1",
        "import_id": make_stable_id(
            "external_agent_import",
            {
                "project_id": project_dir.name,
                "external_agent_ids": [item["external_agent_id"] for item in discovered],
            },
        ),
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "source_root": str(_resolve_agent_root(agent_root)),
        "import_mode": "contract_reference_only",
        "reference_only": True,
        "imported_as_evidence": False,
        "canonical_specs_overwritten": False,
        "external_mock_runtime_called": False,
        "mock_outputs_imported": False,
        "forbidden_paths": _forbidden_external_paths(agent_root),
        "agents": [_strip_large_agent_text(item) for item in discovered],
        "content_hash": hash_payload(
            [
                {
                    "external_agent_id": item["external_agent_id"],
                    "mapped_v5_agent": item["mapped_v5_agent"],
                    "input_schema": item["input_schema"],
                    "output_schema": item["output_schema"],
                }
                for item in discovered
            ]
        ),
    }
    errors = validate_external_agent_contract_import(import_result)
    if errors:
        raise ValueError("; ".join(errors))
    path = project_dir / "v5" / "imported_external_agents.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(import_result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return import_result


def map_external_agent_to_v5_agent(external_agent_id: str) -> str:
    return EXTERNAL_TO_V5_AGENT.get(external_agent_id, "")


def validate_external_agent_contract_import(import_result: dict[str, Any]) -> list[str]:
    errors = []
    canonical_specs = build_agent_specs()
    agents = import_result.get("agents") or []
    if len(agents) != 6:
        errors.append(f"expected 6 external agents, found {len(agents)}")
    if import_result.get("reference_only") is not True:
        errors.append("import_result must be reference_only=true")
    if import_result.get("imported_as_evidence") is not False:
        errors.append("external contracts must not be imported as evidence")
    if import_result.get("external_mock_runtime_called") is not False:
        errors.append("external mock runtime must not be called")
    if import_result.get("mock_outputs_imported") is not False:
        errors.append("external mock outputs must not be imported")
    if import_result.get("canonical_specs_overwritten") is not False:
        errors.append("canonical v5 agent specs must not be overwritten")

    for agent in agents:
        external_agent_id = agent.get("external_agent_id", "")
        mapped = agent.get("mapped_v5_agent", "")
        if not mapped:
            errors.append(f"{external_agent_id}: missing v5 mapping")
        elif mapped not in canonical_specs:
            errors.append(f"{external_agent_id}: mapped v5 agent does not exist: {mapped}")
        if agent.get("reference_only") is not True:
            errors.append(f"{external_agent_id}: reference_only must be true")
        if agent.get("import_status") != REFERENCE_ONLY_STATUS:
            errors.append(f"{external_agent_id}: import_status must be {REFERENCE_ONLY_STATUS}")
        if agent.get("imported_as_evidence") is not False:
            errors.append(f"{external_agent_id}: must not be imported as evidence")
        for field in ["agent_md_path", "input_schema_path", "output_schema_path"]:
            if not agent.get(field):
                errors.append(f"{external_agent_id}: missing {field}")
        if not isinstance(agent.get("input_schema"), dict):
            errors.append(f"{external_agent_id}: input_schema must be JSON object")
        if not isinstance(agent.get("output_schema"), dict):
            errors.append(f"{external_agent_id}: output_schema must be JSON object")
        if _contains_auto_verified_dataset(agent):
            errors.append(f"{external_agent_id}: AUTO_* dataset cannot be imported as verified")
    return errors


def _resolve_agent_root(agent_root: str | Path) -> Path:
    root = Path(agent_root)
    if (root / "agents").exists():
        return root
    nested = root / "bioinfo-agent-system"
    if (nested / "agents").exists():
        return nested
    raise FileNotFoundError(f"cannot resolve external agent root: {root}")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_jsonl(path: Path) -> int:
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def _strip_large_agent_text(item: dict[str, Any]) -> dict[str, Any]:
    copied = dict(item)
    agent_md = copied.pop("agent_md", "")
    copied["agent_md_excerpt"] = agent_md[:1200]
    copied["agent_md_sha256"] = hash_payload(agent_md)
    return copied


def _forbidden_external_paths(agent_root: str | Path) -> list[str]:
    root = _resolve_agent_root(agent_root)
    return [
        str(root / "scripts" / "run_mock_pipeline.py"),
        str(root / "outputs" / "mock_run"),
    ]


def _contains_auto_verified_dataset(agent: dict[str, Any]) -> bool:
    text = json.dumps(agent, ensure_ascii=False).upper()
    if "AUTO_" not in text:
        return False
    return '"VERIFIED": TRUE' in text or '"VERIFIED":TRUE' in text
