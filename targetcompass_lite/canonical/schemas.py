from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from .ids import make_stable_id


CANONICAL_SCHEMA_VERSION = "v5.canonical/0.1"

CLAIM_LEVELS = [
    "descriptive",
    "association",
    "co_expression",
    "candidate_biomarker",
    "mechanistic_hypothesis",
    "causal_support",
    "experimentally_validated_target",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_provenance() -> list[dict[str, str]]:
    return [{"source_type": "system", "source_id": "targetcompass_v5", "note": "canonical control-plane object"}]


@dataclass
class ResearchSpec:
    research_question: str
    project_id: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    research_spec_id: str = ""
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"
    max_claim_level: str = "association"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data["research_spec_id"]:
            data["research_spec_id"] = make_stable_id("research_spec", {"project_id": self.project_id, "research_question": self.research_question})
        return data


@dataclass
class SubQuestion:
    research_spec_id: str
    question: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    subquestion_id: str = ""
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if not data["subquestion_id"]:
            data["subquestion_id"] = make_stable_id("subquestion", {"research_spec_id": self.research_spec_id, "question": self.question})
        return data


@dataclass
class ScopeBundle:
    research_spec_id: str
    species: list[str]
    tissues: list[str]
    conditions: list[str]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    scope_bundle_id: str = ""
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"


@dataclass
class EvidencePlan:
    research_spec_id: str
    evidence_axes: list[str]
    max_claim_level: str = "association"
    schema_version: str = CANONICAL_SCHEMA_VERSION
    evidence_plan_id: str = ""
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"


@dataclass
class ResourceCandidate:
    resource_name: str
    resource_type: str
    verified: bool
    source_status: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    resource_candidate_id: str = ""
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "candidate"
    accession: str = ""


@dataclass
class DatasetProfile:
    dataset_id: str
    modality: str
    organism: str
    tissue: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    dataset_profile_id: str = ""
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "profiled"


@dataclass
class DatasetSelectionDecision:
    dataset_id: str
    decision: str
    reason: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    decision_id: str = ""
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"


@dataclass
class MethodContractRef:
    method_contract_id: str
    method_name: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    method_ref_id: str = ""
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "active"


@dataclass
class CompatibilityDecision:
    dataset_id: str
    method_contract_id: str
    compatible: bool
    reason: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    compatibility_decision_id: str = ""
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"


@dataclass
class WorkflowPlan:
    workflow_name: str
    task_ids: list[str]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    workflow_plan_id: str = ""
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"


@dataclass
class AnalysisTaskPacket:
    task_id: str
    subquestion_id: str
    expected_inputs: list[str]
    expected_outputs: list[str]
    qc_requirements: list[str]
    failure_conditions: list[str]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"


@dataclass
class EngineeringTaskPacket:
    task_id: str
    allowed_paths: list[str]
    forbidden_paths: list[str]
    expected_patch_summary: str
    test_commands: list[str]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"


@dataclass
class ReviewTaskPacket:
    task_id: str
    audit_scope: list[str]
    claim_ceiling: str
    required_checks: list[str]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"


@dataclass
class TaskRun:
    task_run_id: str
    task_id: str
    result_status: str
    artifact_refs: list[str]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "recorded"


@dataclass
class ArtifactManifest:
    artifact_id: str
    project_id: str
    path: str
    exists: bool
    checksum_sha256: str
    is_placeholder: bool
    qc_status: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "registered"


@dataclass
class QCReport:
    qc_report_id: str
    overall_status: str
    checks: list[dict[str, Any]]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "recorded"


@dataclass
class EvidenceItemRef:
    evidence_item_id: str
    artifact_id: str
    review_status: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "referenced"


@dataclass
class Claim:
    claim_id: str
    text: str
    claim_level: str
    evidence_item_refs: list[str]
    supports_subquestion_ids: list[str]
    scope: dict[str, Any]
    limitations: list[str]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"


@dataclass
class QuestionAlignmentReport:
    report_id: str
    final_decision: str
    unsupported_claims: list[str]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    generated_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "recorded"


@dataclass
class FinalReportManifest:
    final_report_id: str
    report_path: str
    claim_ids: list[str]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    generated_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "draft"


@dataclass
class ProjectEvent:
    event_id: str
    project_id: str
    event_type: str
    actor: str
    schema_version: str = CANONICAL_SCHEMA_VERSION
    created_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "recorded"


@dataclass
class ProjectState:
    project_id: str
    current_stage: str
    allowed_next_stages: list[str]
    schema_version: str = CANONICAL_SCHEMA_VERSION
    project_state_id: str = ""
    updated_at: str = field(default_factory=now_iso)
    provenance: list[dict[str, Any]] = field(default_factory=_default_provenance)
    status: str = "active"
