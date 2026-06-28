# Agent Communication Contract

## Core Rule

Agents must exchange JSON objects, object refs, artifact refs, evidence refs, assumptions, open questions, blocking issues, audit notes, and claim ceilings. They must not pass free-text scientific conclusions as the primary contract.

## Agent Responsibilities

### question_normalizer

Input:

- raw user question

Output:

- `ResearchSpec`
- `SubQuestion[]`

Forbidden:

- must not recommend specific databases
- must not generate results
- must not claim the question has been proven
- must not raise the claim ceiling

### scope_resolver

Input:

- `ResearchSpec`
- `SubQuestion[]`

Output:

- `ScopeBundle`

Forbidden:

- must not add unrequested disease scope without reason
- must not mix model organism evidence with human evidence
- must not mark tissue/cell-type mismatched data as suitable

### evidence_plan_builder

Input:

- `ResearchSpec`
- `ScopeBundle`

Output:

- `EvidencePlan`

Forbidden:

- must not select a concrete dataset as verified
- must not generate Codex tasks
- must not propose claim levels beyond what data can support

### resource_discovery_agent

Input:

- `EvidencePlan`
- `ScopeBundle`

Output:

- `ResourceCandidate[]`
- `DatasetProfile[]`
- `DatasetSelectionDecision[]`

Forbidden:

- must not set `verified=true` without real metadata
- must not use placeholder accessions
- must not treat paper mention as dataset usability
- must not ignore metadata group, sample size, organism, tissue, or platform

### method_adapter_workorder_compiler

Input:

- `DatasetProfile[]`
- `EvidencePlan`
- method library

Output:

- `WorkflowPlan`
- `AnalysisTaskPacket[]`
- `ReviewTaskPacket[]`

Forbidden:

- must not generate biological results
- must not choose methods unsupported by input data
- must not mix engineering tasks and analysis tasks
- must not omit expected inputs, expected outputs, QC, or failure conditions

### result_auditor

Input:

- `TaskRun[]`
- `ArtifactManifest[]`
- `QCReport[]`

Output:

- `AuditReport`
- `EvidenceItemRef[]`

Forbidden:

- must not modify raw results
- must not fabricate missing outputs
- must not ignore warnings or errors
- must not approve failed-QC artifacts

### evidence_synthesizer_reporter

Input:

- audited `EvidenceItemRef[]`
- `Claim[]`
- `QuestionAlignmentReport`

Output:

- `FinalReportManifest`

Forbidden:

- must not consume unaudited evidence
- must not generate unsupported claims
- must not exceed claim ceiling
- must not omit failed results or limitations

## Handoff Example

```json
{
  "handoff_id": "handoff_123",
  "schema_version": "v5.agent_handoff/0.1",
  "project_id": "demo_project",
  "from_agent": "question_normalizer",
  "to_agent": "scope_resolver",
  "created_at": "2026-06-23T00:00:00+00:00",
  "input_object_refs": [
    {
      "object_type": "UserQuestion",
      "object_id": "user_question"
    }
  ],
  "output_object_refs": [
    {
      "object_type": "ResearchSpec",
      "object_id": "research_spec_abc",
      "path": "v5/objects/research_spec_abc.json"
    }
  ],
  "evidence_refs": [],
  "artifact_refs": [],
  "assumptions": [
    "Human should be searched first unless the user specifies another organism."
  ],
  "open_questions": [
    "Exact tissue context needs scope resolution."
  ],
  "blocking_issues": [],
  "claim_ceiling": {
    "max_allowed_claim": "descriptive",
    "reason": "Natural-language question normalization creates no empirical evidence."
  },
  "audit_notes": [],
  "payload_hash": "abc123"
}
```

If `blocking_issues` is non-empty, the downstream agent must not continue automatically.

## Claim Ceiling Rule

Claim ceilings can be tightened but cannot be automatically loosened.

Example:

```json
{
  "previous_ceiling": "association",
  "next_ceiling": "descriptive",
  "allowed": true
}
```

Invalid example:

```json
{
  "previous_ceiling": "association",
  "next_ceiling": "causal_support",
  "allowed": false
}
```

## AnalysisTaskPacket Example

```json
{
  "schema_version": "v5.canonical/0.1",
  "packet_type": "AnalysisTaskPacket",
  "task_id": "analysis_task_abc",
  "subquestion_id": "subquestion_abc",
  "method_name": "bulk_deg",
  "expected_inputs": [
    "verified_dataset_profile",
    "grouping_metadata",
    "method_contract"
  ],
  "expected_outputs": [
    "deg_result_table",
    "executor_manifest",
    "qc_report"
  ],
  "qc_requirements": [
    "sample_count_check",
    "gene_identifier_check",
    "contrast_design_check"
  ],
  "failure_conditions": [
    "missing_grouping_metadata",
    "insufficient_samples",
    "unmapped_gene_identifiers"
  ],
  "status": "draft"
}
```

Analysis packets must not contain code-change instructions.

## EngineeringTaskPacket Example

```json
{
  "schema_version": "v5.canonical/0.1",
  "packet_type": "EngineeringTaskPacket",
  "task_id": "engineering_task_abc",
  "allowed_paths": [
    "targetcompass_lite/canonical/**",
    "tests/test_canonical_*.py"
  ],
  "forbidden_paths": [
    ".git/",
    "secrets",
    ".env",
    "raw_data/",
    "external_agent_runs/*/mock_run/"
  ],
  "expected_patch_summary": "Add a validator without changing v4 runtime behavior.",
  "test_commands": [
    "python -m unittest tests.test_codex_worker_protocol -v"
  ],
  "status": "draft"
}
```

Engineering packets require approval before any worker can claim them.

## ReviewTaskPacket Example

```json
{
  "schema_version": "v5.canonical/0.1",
  "packet_type": "ReviewTaskPacket",
  "task_id": "review_task_abc",
  "subquestion_id": "subquestion_abc",
  "audit_scope": [
    "artifact_registry",
    "qc_report",
    "claim_ceiling"
  ],
  "claim_ceiling": "association",
  "required_checks": [
    "no_placeholder_artifact",
    "qc_passed",
    "claim_scope_matches_question"
  ],
  "status": "draft"
}
```

Review packets can reject, request rerun, or approve evidence for the next gate. They must not modify raw results.

## Human Review Gate

Human review is required when:

- a dataset is unverified
- an agent reports blocking issues
- a task is about to enter worker execution
- QC fails or is incomplete
- evidence is negative, failed, or omitted
- claim ceiling would need to be raised
- final report needs signoff

## Production Boundary

The v5 mock runner and imported external mock pipeline are not production runners. They are useful for contract testing and architecture validation only.
