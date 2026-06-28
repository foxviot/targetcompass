# Question Alignment Auditor Stage 7 Summary

## Scope

This stage added a v5 Question Alignment Auditor. It checks whether structured claims answer the original research question, remain within scope, stay under the claim ceiling, and are supported by valid evidence and artifacts.

It does not call an LLM, does not generate a final report, and does not modify claims.

## Files Added

- `targetcompass_lite/canonical/alignment_auditor.py`
- `tests/test_question_alignment_auditor.py`

## Implemented API

```python
audit_question_alignment(
    research_spec,
    subquestions,
    scope_bundle,
    evidence_item_refs,
    claims,
    artifact_manifests,
    qc_reports,
    max_claim_level=None,
)
```

## Output

The auditor returns a `QuestionAlignmentReport`-style dictionary containing:

- `report_id`
- `project_id`
- `research_spec_id`
- `coverage_by_subquestion`
- `scope_fidelity`
- `unsupported_claims`
- `claim_ceiling_violations`
- `omitted_negative_or_failed_evidence`
- `method_relevance_findings`
- `unresolved_questions`
- `final_decision`
- `required_reruns`
- `human_review_notes`

## Checks Implemented

The auditor checks:

- each claim has `supports_subquestion_ids`
- each claim has `evidence_item_refs`
- each claim has `claim_level`
- each claim has structured `scope`
- each claim has `limitations`
- subquestions are covered by claims or have an explicit unresolved reason
- claim `species`, `tissue`, and `condition` align with `ScopeBundle`
- claim level does not exceed the project or supplied ceiling
- claim evidence refs are present
- placeholder artifacts are not used to approve claims
- missing artifacts are not used to approve claims
- QC-failed evidence is not used to approve claims
- negative or failed evidence is not silently omitted

Scope checks use structured fields rather than free-text keyword matching.

## Decision Rules

- `reject`: hard evidence, scope, QC, missing coverage, or claim-ceiling violation.
- `needs_review`: unresolved subquestions or omitted negative/failed evidence without hard claim rejection.
- `approve`: all structured checks pass.

## Tests Run

```powershell
python -m unittest tests.test_question_alignment_auditor -v
```

Result: passed, 8 tests.

```powershell
python -m unittest tests.test_canonical_memory_palace tests.test_canonical_schemas tests.test_canonical_state tests.test_agent_protocol tests.test_canonical_mock_runner tests.test_external_agent_import tests.test_canonical_artifacts tests.test_question_alignment_auditor -v
```

Result: passed, 51 tests.

```powershell
python -m compileall -q targetcompass_lite\canonical tests\test_question_alignment_auditor.py
```

Result: passed.

```powershell
python -m unittest discover tests -v
```

Result: timed out after approximately 240 seconds. This is consistent with prior stages and is not a failure of the new Stage 7 tests.

## Compatibility Notes

- Existing v4 report generation was not changed.
- Existing claims are not modified by the auditor.
- The auditor only produces an alignment report and rerun/review recommendations.
