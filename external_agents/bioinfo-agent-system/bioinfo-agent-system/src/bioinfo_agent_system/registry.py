from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AgentRecord:
    agent_id: str
    agent_name: str
    step_index: int
    folder_path: Path
    input_schema_path: Path
    output_schema_path: Path
    owned_state_field: str
    output_filename: str


def _agent_record(
    agent_id: str,
    agent_name: str,
    step_index: int,
    folder_name: str,
    owned_state_field: str,
    output_filename: str,
) -> AgentRecord:
    folder_path = PROJECT_ROOT / "agents" / folder_name
    return AgentRecord(
        agent_id=agent_id,
        agent_name=agent_name,
        step_index=step_index,
        folder_path=folder_path,
        input_schema_path=folder_path / "input.schema.json",
        output_schema_path=folder_path / "output.schema.json",
        owned_state_field=owned_state_field,
        output_filename=output_filename,
    )


AGENT_REGISTRY = [
    _agent_record(
        "01_scientific_question_normalizer",
        "Scientific Question Normalizer",
        1,
        "01_scientific_question_normalizer",
        "question_normalization",
        "01_question_normalization.json",
    ),
    _agent_record(
        "02_scope_ontology_resolver",
        "Scope Ontology Resolver",
        2,
        "02_scope_ontology_resolver",
        "ontology_scope",
        "02_ontology_scope.json",
    ),
    _agent_record(
        "03_evidence_dataset_scout",
        "Evidence Dataset Scout",
        3,
        "03_evidence_dataset_scout",
        "evidence_dataset_scout",
        "03_evidence_dataset_scout.json",
    ),
    _agent_record(
        "04_method_extraction_agent",
        "Method Extraction Agent",
        4,
        "04_method_extraction_agent",
        "method_extraction",
        "04_method_extraction.json",
    ),
    _agent_record(
        "05_method_motif_feasibility_synthesizer",
        "Method Motif Feasibility Synthesizer",
        5,
        "05_method_motif_feasibility_synthesizer",
        "method_motif_feasibility",
        "05_method_motif_feasibility.json",
    ),
    _agent_record(
        "06_research_plan_compiler",
        "Research Plan Compiler",
        6,
        "06_research_plan_compiler",
        "executable_research_plan",
        "06_executable_research_plan.json",
    ),
]


def get_agent_records() -> list[AgentRecord]:
    return list(AGENT_REGISTRY)


def get_agent_record(agent_id: str) -> AgentRecord:
    for record in AGENT_REGISTRY:
        if record.agent_id == agent_id:
            return record
    raise KeyError(f"Unknown agent id: {agent_id}")
