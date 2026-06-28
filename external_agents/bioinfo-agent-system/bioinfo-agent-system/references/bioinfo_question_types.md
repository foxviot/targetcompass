# Bioinformatics Question Types

## `candidate_discovery`

- Typical wording: find candidate molecules or genes linked to a phenotype.
- Primary entity type: molecule or gene.
- Evidence needed: expression, annotation, validation context.
- Common pitfalls: treating candidates as validated drivers.
- Allowed pre-analysis ceiling: `co_expression`.

## `biomarker_discovery`

- Typical wording: identify biomarkers that separate cases from controls.
- Primary entity type: biomarker panel.
- Evidence needed: reproducible signal and independent validation.
- Common pitfalls: overfitting and single-dataset claims.
- Allowed pre-analysis ceiling: `candidate_biomarker`.

## `target_prioritization`

- Typical wording: rank potential targets for intervention.
- Primary entity type: molecular target.
- Evidence needed: disease relevance, feasibility, and mechanism support.
- Common pitfalls: upgrading expression signal into target readiness.
- Allowed pre-analysis ceiling: `therapeutic_target_hypothesis`.

## `mechanism_explanation`

- Typical wording: explain why or how a pathway drives a phenotype.
- Primary entity type: mechanism or pathway.
- Evidence needed: perturbation or convergent evidence.
- Common pitfalls: mistaking correlation for mechanism.
- Allowed pre-analysis ceiling: `mechanistic_hypothesis`.

## `dataset_discovery`

- Typical wording: locate datasets that can answer a question.
- Primary entity type: dataset.
- Evidence needed: metadata, matrix availability, phenotype coverage.
- Common pitfalls: assuming paper availability equals public accessibility.
- Allowed pre-analysis ceiling: `descriptive`.

## `method_planning`

- Typical wording: plan an analysis route for available data.
- Primary entity type: method contract.
- Evidence needed: dataset feasibility and source-bound methods.
- Common pitfalls: copying literature workflows without fit checks.
- Allowed pre-analysis ceiling: `descriptive`.

## `validation_review`

- Typical wording: review whether a claim has enough support.
- Primary entity type: claim package.
- Evidence needed: independent validation and claim audit.
- Common pitfalls: ignoring weak controls or caveats.
- Allowed pre-analysis ceiling: `candidate_biomarker`.

## `cell_state_discovery`

- Typical wording: identify cell states linked to a condition.
- Primary entity type: cell state.
- Evidence needed: single-cell or deconvolution support.
- Common pitfalls: assigning tissue signal to a specific cell type.
- Allowed pre-analysis ceiling: `cell_state_marker`.

## `pathway_program_association`

- Typical wording: associate pathways or programs with molecules or phenotypes.
- Primary entity type: pathway or program.
- Evidence needed: scoring and association tests.
- Common pitfalls: claiming direct regulation from co-activation.
- Allowed pre-analysis ceiling: `co_expression`.

## `causal_inference`

- Typical wording: infer causal direction or driver status.
- Primary entity type: causal relationship.
- Evidence needed: perturbation, genetics, or other causal support.
- Common pitfalls: using observational expression data alone.
- Allowed pre-analysis ceiling: `mechanistic_hypothesis`.
