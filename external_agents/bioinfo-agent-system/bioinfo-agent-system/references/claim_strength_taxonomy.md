# Claim Strength Taxonomy

Ordered claim levels:

0. `descriptive`
1. `association`
2. `correlation`
3. `co_expression`
4. `cell_state_marker`
5. `candidate_biomarker`
6. `mechanistic_hypothesis`
7. `causal_support`
8. `therapeutic_target_hypothesis`
9. `experimentally_validated_target`

Rules:

- Expression data alone cannot support `causal_support`.
- Co-expression cannot prove functional regulation.
- Surface or secretome annotation cannot prove true extracellular accessibility.
- Mouse-only evidence cannot be generalized to human without caveat.
- Literature association cannot replace perturbation evidence.
- `candidate_biomarker` is not `therapeutic_target_hypothesis`.
- `therapeutic_target_hypothesis` is not `experimentally_validated_target`.
