from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .registry import AgentRecord


def _empty_agent_outputs() -> dict[str, dict[str, Any]]:
    return {
        "question_normalization": {},
        "ontology_scope": {},
        "evidence_dataset_scout": {},
        "method_extraction": {},
        "method_motif_feasibility": {},
        "executable_research_plan": {},
    }


@dataclass
class ResearchProjectState:
    run_id: str
    raw_user_question: str
    current_step: str = "initialized"
    agent_outputs: dict[str, dict[str, Any]] = field(default_factory=_empty_agent_outputs)
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    claim_ceiling_history: list[dict[str, Any]] = field(default_factory=list)
    locked_datasets: list[str] = field(default_factory=list)
    ready_contracts: list[str] = field(default_factory=list)
    blocked_contracts: list[str] = field(default_factory=list)
    audit_log: list[dict[str, str]] = field(default_factory=list)

    def set_agent_output(self, record: AgentRecord, output: dict[str, Any]) -> None:
        self.agent_outputs[record.owned_state_field] = output
        self.current_step = record.agent_id
        self.claim_ceiling_history.append(
            {
                "agent_id": record.agent_id,
                "max_allowed_claim": output["claim_ceiling"]["max_allowed_claim"],
                "reason": output["claim_ceiling"]["reason"],
            }
        )
        if record.agent_id == "05_method_motif_feasibility_synthesizer":
            self.ready_contracts = list(output["ready_contracts"])
            self.blocked_contracts = list(output["blocked_contracts"])
        if record.agent_id == "06_research_plan_compiler":
            self.locked_datasets = list(output["selected_datasets"])

    def add_handoff(self, handoff: dict[str, Any]) -> None:
        self.handoffs.append(handoff)

    def add_audit_entries(self, entries: list[dict[str, str]]) -> None:
        self.audit_log.extend(entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "raw_user_question": self.raw_user_question,
            "current_step": self.current_step,
            "agent_outputs": self.agent_outputs,
            "handoffs": self.handoffs,
            "claim_ceiling_history": self.claim_ceiling_history,
            "locked_datasets": self.locked_datasets,
            "ready_contracts": self.ready_contracts,
            "blocked_contracts": self.blocked_contracts,
            "audit_log": self.audit_log,
        }
