from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    display_name: str
    responsibility: str
    forbidden_actions: list[str]
    input_schema_name: str
    output_schema_name: str
    allowed_tools: list[str]
    required_input_refs: list[str]
    required_output_refs: list[str]
    max_claim_level: str
    handoff_contract: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_agent_specs() -> dict[str, dict[str, Any]]:
    specs = [
        AgentSpec(
            agent_id="question_normalizer",
            display_name="Question Normalizer",
            responsibility="Convert the raw user question into ResearchSpec and SubQuestion refs without selecting resources or making scientific claims.",
            forbidden_actions=["recommend_specific_database", "generate_result", "claim_question_is_proven", "raise_claim_ceiling", "run_analysis", "lock_dataset", "call_external_database"],
            input_schema_name="UserQuestion",
            output_schema_name="ResearchSpecBundle",
            allowed_tools=[],
            required_input_refs=["user_question"],
            required_output_refs=["research_spec", "subquestions"],
            max_claim_level="descriptive",
            handoff_contract={"to_agent": "scope_resolver", "message_type": "handoff"},
        ),
        AgentSpec(
            agent_id="scope_resolver",
            display_name="Scope Resolver",
            responsibility="Resolve species, tissue, disease, cell-type, organism-model, and analysis scope boundaries from ResearchSpec and SubQuestions.",
            forbidden_actions=["add_unrequested_disease_scope_without_reason", "mix_model_organism_with_human_evidence", "mark_mismatched_tissue_or_cell_type_as_suitable", "run_analysis", "lock_dataset", "make_biological_claim"],
            input_schema_name="ResearchSpecBundle",
            output_schema_name="ScopeBundle",
            allowed_tools=[],
            required_input_refs=["research_spec", "subquestions"],
            required_output_refs=["scope_bundle"],
            max_claim_level="descriptive",
            handoff_contract={"to_agent": "evidence_plan_builder", "message_type": "handoff"},
        ),
        AgentSpec(
            agent_id="evidence_plan_builder",
            display_name="Evidence Plan Builder",
            responsibility="Define evidence axes, required evidence types, and acceptable claim ceiling from ResearchSpec and ScopeBundle.",
            forbidden_actions=["mark_specific_dataset_verified", "generate_codex_task", "propose_claim_level_beyond_data_support", "run_analysis", "lock_dataset", "invent_dataset", "make_biological_claim"],
            input_schema_name="ScopeBundle",
            output_schema_name="EvidencePlan",
            allowed_tools=[],
            required_input_refs=["research_spec", "scope_bundle"],
            required_output_refs=["evidence_plan"],
            max_claim_level="association",
            handoff_contract={"to_agent": "resource_discovery_agent", "message_type": "handoff"},
        ),
        AgentSpec(
            agent_id="resource_discovery_agent",
            display_name="Resource Discovery Agent",
            responsibility="Discover candidate resources and profile datasets using real metadata, while preserving verification, usability, grouping, sample size, organism, tissue, and platform constraints.",
            forbidden_actions=["set_verified_true_without_real_metadata", "use_placeholder_accession", "treat_paper_mention_as_dataset_usability", "ignore_metadata_group_sample_size_organism_tissue_or_platform", "run_analysis", "make_biological_claim", "lock_unverified_dataset"],
            input_schema_name="EvidencePlanAndScopeBundle",
            output_schema_name="ResourceCandidateDatasetProfileBundle",
            allowed_tools=["metadata_search"],
            required_input_refs=["evidence_plan", "scope_bundle"],
            required_output_refs=["resource_candidates", "dataset_profiles", "dataset_selection_decisions"],
            max_claim_level="association",
            handoff_contract={"to_agent": "method_adapter_workorder_compiler", "message_type": "handoff"},
        ),
        AgentSpec(
            agent_id="method_adapter_workorder_compiler",
            display_name="Method Adapter WorkOrder Compiler",
            responsibility="Compile WorkflowPlan, AnalysisTaskPacket, and ReviewTaskPacket from DatasetProfiles, EvidencePlan, and method-library contracts.",
            forbidden_actions=["generate_biological_result", "select_method_unsupported_by_input_data", "mix_engineering_task_with_analysis_task", "omit_expected_inputs_outputs_qc_or_failure_conditions", "modify_raw_result", "approve_own_output"],
            input_schema_name="DatasetProfileEvidencePlanMethodLibraryBundle",
            output_schema_name="WorkflowPlan",
            allowed_tools=["method_registry", "task_packet_builder"],
            required_input_refs=["dataset_profiles", "evidence_plan", "method_library"],
            required_output_refs=["workflow_plan", "task_packets"],
            max_claim_level="association",
            handoff_contract={"to_agent": "result_auditor", "message_type": "handoff"},
        ),
        AgentSpec(
            agent_id="result_auditor",
            display_name="Result Auditor",
            responsibility="Audit TaskRun, ArtifactManifest, and QCReport records, then produce AuditReport and audited EvidenceItemRef records without changing raw outputs.",
            forbidden_actions=["modify_raw_result", "fabricate_missing_output", "ignore_warning_or_error", "approve_failed_qc_artifact", "rerun_analysis_without_task", "make_final_report"],
            input_schema_name="TaskRunBundle",
            output_schema_name="AuditReportBundle",
            allowed_tools=["qc_validator", "artifact_reader"],
            required_input_refs=["task_runs", "artifact_manifests", "qc_reports"],
            required_output_refs=["audit_reports", "audited_evidence_refs"],
            max_claim_level="candidate_biomarker",
            handoff_contract={"to_agent": "evidence_synthesizer_reporter", "message_type": "handoff"},
        ),
        AgentSpec(
            agent_id="evidence_synthesizer_reporter",
            display_name="Evidence Synthesizer Reporter",
            responsibility="Consume audited EvidenceItemRefs, Claims, and QuestionAlignmentReport to produce a FinalReportManifest with limitations and failed evidence represented.",
            forbidden_actions=["consume_unaudited_evidence", "generate_unsupported_claim", "exceed_claim_ceiling", "omit_failed_results_or_limitations", "raise_claim_ceiling", "invent_evidence"],
            input_schema_name="AuditedEvidenceClaimAlignmentBundle",
            output_schema_name="FinalReportManifest",
            allowed_tools=["evidence_db_query", "report_builder"],
            required_input_refs=["audited_evidence_refs", "claims", "question_alignment_report"],
            required_output_refs=["final_report_manifest"],
            max_claim_level="candidate_biomarker",
            handoff_contract={"to_agent": None, "message_type": "handoff"},
        ),
    ]
    return {spec.agent_id: spec.to_dict() for spec in specs}


def next_agent_for_stage(stage: str) -> str | None:
    return {
        "INTAKE": "question_normalizer",
        "QUESTION_RESOLVED": "scope_resolver",
        "SCOPE_RESOLVED": "evidence_plan_builder",
        "EVIDENCE_PLANNED": "resource_discovery_agent",
        "RESOURCES_DISCOVERED": "method_adapter_workorder_compiler",
        "DATASETS_LOCKED": "method_adapter_workorder_compiler",
        "WORKFLOW_COMPILED": "result_auditor",
        "TASKS_READY": "result_auditor",
        "TASKS_RUNNING": "result_auditor",
        "QC_COMPLETED": "evidence_synthesizer_reporter",
        "EVIDENCE_SYNTHESIZED": "evidence_synthesizer_reporter",
        "ALIGNMENT_AUDITED": "evidence_synthesizer_reporter",
        "REPORT_READY": None,
        "HUMAN_REVIEW_REQUIRED": None,
        "FAILED": None,
        "CANCELLED": None,
    }.get(stage)
