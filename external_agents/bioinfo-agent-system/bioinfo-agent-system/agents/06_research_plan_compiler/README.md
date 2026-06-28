# 06 Research Plan Compiler

This agent answers: given the earlier evidence and method constraints, what exact task DAG should be executed?

- Role: compile an executable mock plan without changing upstream interpretation.
- Input: outputs from Agents 1 through 5.
- Output: selected datasets, task DAG, task packets, and claim boundaries.
- Forbidden actions: use rejected datasets, blocked contracts, or stronger claims.
- Failure behavior: fail validation on cross-agent rule breaches.
- Example: dataset intake, QC, scoring, association, annotation, ranking, fallback localization, and report drafting.
