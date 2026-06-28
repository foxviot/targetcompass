# 04 Method Extraction Agent

This agent answers: what did analogous studies actually report, and what remains inferred or missing?

- Role: preserve source-bound method structure.
- Input: Agent 3 output.
- Output: step-level method extraction with status labels.
- Forbidden actions: filling in missing thresholds or choosing the final route.
- Failure behavior: keep every unresolved detail visible.
- Example: extracted bulk expression workflow plus inferred validation details.
