from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bioinfo_agent_system.claim_audit import audit_project_claims
from bioinfo_agent_system.cross_agent_rules import run_cross_agent_validation
from bioinfo_agent_system.mock_agents import EXPECTED_QUESTION, build_mock_output_bundle
from bioinfo_agent_system.state import ResearchProjectState
from bioinfo_agent_system.validators import ValidationError


FIXED_CREATED_AT = "2026-06-21T00:00:00Z"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("Usage: python scripts/run_evals.py", file=sys.stderr)
        return 1

    bundle = build_mock_output_bundle(EXPECTED_QUESTION, FIXED_CREATED_AT)
    project_state = _build_project_state(bundle)

    results = {
        "agent_cases": _run_agent_cases(bundle),
        "root_cases": _run_root_cases(bundle, project_state),
        "expected_failures": _run_failure_cases(project_state),
    }
    print(json.dumps({"status": "passed", "results": results}, indent=2))
    return 0


def _build_project_state(bundle: dict[str, dict[str, object]]) -> dict[str, object]:
    state = ResearchProjectState(run_id="eval-run", raw_user_question=EXPECTED_QUESTION)
    field_map = {
        "01_scientific_question_normalizer": "question_normalization",
        "02_scope_ontology_resolver": "ontology_scope",
        "03_evidence_dataset_scout": "evidence_dataset_scout",
        "04_method_extraction_agent": "method_extraction",
        "05_method_motif_feasibility_synthesizer": "method_motif_feasibility",
        "06_research_plan_compiler": "executable_research_plan",
    }
    for agent_id, field_name in field_map.items():
        state.agent_outputs[field_name] = bundle[agent_id]
        state.claim_ceiling_history.append(
            {
                "agent_id": agent_id,
                "max_allowed_claim": bundle[agent_id]["claim_ceiling"]["max_allowed_claim"],
                "reason": bundle[agent_id]["claim_ceiling"]["reason"],
            }
        )
    state.ready_contracts = list(bundle["05_method_motif_feasibility_synthesizer"]["ready_contracts"])
    state.blocked_contracts = list(
        bundle["05_method_motif_feasibility_synthesizer"]["blocked_contracts"]
    )
    state.locked_datasets = list(bundle["06_research_plan_compiler"]["selected_datasets"])
    state.current_step = "completed"
    return state.to_dict()


def _run_agent_cases(bundle: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for eval_path in sorted((PROJECT_ROOT / "agents").glob("*/eval_cases.jsonl")):
        agent_folder = eval_path.parent.name
        for case in _load_jsonl(eval_path):
            _evaluate_agent_case(agent_folder, case, bundle)
            results.append({"case_id": case["case_id"], "status": "passed"})
    return results


def _run_root_cases(
    bundle: dict[str, dict[str, object]], project_state: dict[str, object]
) -> list[dict[str, object]]:
    del bundle
    results = []
    for case in _load_jsonl(PROJECT_ROOT / "evals" / "question_normalization_cases.jsonl"):
        agent1 = project_state["agent_outputs"]["question_normalization"]
        _assert(
            agent1["question_type"] == case["expected_question_type"],
            f"{case['case_id']}: question_type mismatch",
        )
        _assert(
            agent1["claim_ceiling"]["max_allowed_claim"] == case["expected_claim_ceiling"],
            f"{case['case_id']}: claim ceiling mismatch",
        )
        results.append({"case_id": case["case_id"], "status": "passed"})

    for case in _load_jsonl(PROJECT_ROOT / "evals" / "cross_agent_cases.jsonl"):
        cross_events = run_cross_agent_validation(project_state)
        selected = project_state["agent_outputs"]["executable_research_plan"]["selected_datasets"]
        _assert(
            selected == case["expected_selected_datasets"],
            f"{case['case_id']}: selected dataset mismatch",
        )
        blocked = project_state["agent_outputs"]["method_motif_feasibility"]["blocked_contracts"]
        _assert(
            blocked == case["expected_blocked_contracts"],
            f"{case['case_id']}: blocked contract mismatch",
        )
        results.append(
            {"case_id": case["case_id"], "status": "passed", "events": cross_events}
        )

    claim_events = audit_project_claims(project_state)
    results.append({"name": "claim_audit", "status": "passed", "events": claim_events})
    return results


def _run_failure_cases(project_state: dict[str, object]) -> list[dict[str, object]]:
    results = []
    for case in _load_jsonl(PROJECT_ROOT / "evals" / "expected_gate_failures.jsonl"):
        mutated_state = deepcopy(project_state)
        if case["mutation"] == "select_rejected_dataset":
            mutated_state["agent_outputs"]["executable_research_plan"]["selected_datasets"] = [
                "MOCK-GSE-LIVER-003"
            ]
        elif case["mutation"] == "select_blocked_contract":
            mutated_state["agent_outputs"]["executable_research_plan"][
                "selected_method_contracts"
            ] = ["wetlab_validation"]
        else:
            raise AssertionError(f"Unknown failure mutation: {case['mutation']}")
        results.append({"case_id": case["case_id"], "status": _expect_failure(mutated_state)})
    return results


def _expect_failure(project_state: dict[str, object]) -> str:
    try:
        run_cross_agent_validation(project_state)
    except ValidationError:
        return "passed"
    raise AssertionError("Expected cross-agent validation to fail but it passed")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        rows.append(json.loads(stripped))
    return rows


def _evaluate_agent_case(
    agent_folder: str, case: dict[str, object], bundle: dict[str, dict[str, object]]
) -> None:
    assertions = case["assertions"]
    if agent_folder == "01_scientific_question_normalizer":
        output = bundle["01_scientific_question_normalizer"]
        _assert(output["question_type"] == assertions["question_type"], f"{case['case_id']}: question_type mismatch")
        _assert(
            output["primary_interpretation"] == assertions["primary_interpretation"],
            f"{case['case_id']}: primary_interpretation mismatch",
        )
        _assert(
            output["claim_ceiling"]["max_allowed_claim"] == assertions["claim_ceiling"],
            f"{case['case_id']}: claim ceiling mismatch",
        )
        return
    if agent_folder == "02_scope_ontology_resolver":
        output = bundle["02_scope_ontology_resolver"]
        _assert(
            output["recommended_scope"]["disease"] == assertions["recommended_scope_disease"],
            f"{case['case_id']}: disease mismatch",
        )
        _assert(
            output["recommended_scope"]["molecular_constraint"] == assertions["molecular_constraint"],
            f"{case['case_id']}: molecular constraint mismatch",
        )
        _assert(
            output["recommended_scope"]["species"] == assertions["species"],
            f"{case['case_id']}: species mismatch",
        )
        return
    if agent_folder == "03_evidence_dataset_scout":
        output = bundle["03_evidence_dataset_scout"]
        recommendations = [item["recommendation"] for item in output["dataset_candidates"]]
        _assert(("primary" in recommendations) == assertions["has_primary"], f"{case['case_id']}: primary dataset mismatch")
        _assert(("fallback" in recommendations) == assertions["has_fallback"], f"{case['case_id']}: fallback dataset mismatch")
        _assert(("reject" in recommendations) == assertions["has_reject"], f"{case['case_id']}: reject dataset mismatch")
        _assert(
            output["claim_ceiling"]["max_allowed_claim"] == assertions["claim_ceiling"],
            f"{case['case_id']}: claim ceiling mismatch",
        )
        return
    if agent_folder == "04_method_extraction_agent":
        output = bundle["04_method_extraction_agent"]
        statuses = [
            step["source_status"]
            for extraction in output["study_method_extractions"]
            for step in extraction["method_steps"]
        ]
        _assert(
            all(status in set(assertions["allowed_statuses"]) for status in statuses),
            f"{case['case_id']}: invalid method status",
        )
        return
    if agent_folder == "05_method_motif_feasibility_synthesizer":
        output = bundle["05_method_motif_feasibility_synthesizer"]
        _assert(
            len(output["ready_contracts"]) == assertions["ready_contract_count"],
            f"{case['case_id']}: ready contract count mismatch",
        )
        _assert(
            len(output["blocked_contracts"]) == assertions["blocked_contract_count"],
            f"{case['case_id']}: blocked contract count mismatch",
        )
        _assert(
            output["claim_ceiling"]["max_allowed_claim"] == assertions["claim_ceiling"],
            f"{case['case_id']}: claim ceiling mismatch",
        )
        return
    if agent_folder == "06_research_plan_compiler":
        output = bundle["06_research_plan_compiler"]
        blocked = bundle["05_method_motif_feasibility_synthesizer"]["blocked_contracts"]
        selected = output["selected_method_contracts"]
        _assert(
            (not any(contract in blocked for contract in selected))
            == (not assertions["uses_blocked_contracts"]),
            f"{case['case_id']}: blocked contract selection mismatch",
        )
        recommendations = {
            item["dataset_id"]: item["recommendation"]
            for item in bundle["03_evidence_dataset_scout"]["dataset_candidates"]
        }
        uses_reject = any(recommendations[item] == "reject" for item in output["selected_datasets"])
        _assert(
            uses_reject == assertions["uses_rejected_datasets"],
            f"{case['case_id']}: rejected dataset selection mismatch",
        )
        _assert(
            output["claim_ceiling"]["max_allowed_claim"] == assertions["claim_ceiling"],
            f"{case['case_id']}: claim ceiling mismatch",
        )
        return
    raise AssertionError(f"Unhandled agent eval folder: {agent_folder}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
