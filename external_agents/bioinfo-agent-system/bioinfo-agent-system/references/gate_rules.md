# Gate Rules

Validation gates are applied after every agent step.

Core gates:

- output must be valid JSON
- output must match the declared schema
- provenance must be present
- warnings and blocking failures must be explicit arrays
- claim ceiling must be present
- mock sources must be labeled as mock

Cross-agent gates:

- rejected datasets cannot be selected downstream
- blocked contracts cannot be selected downstream
- missing method steps cannot become ready contracts
- final claims cannot exceed upstream claim ceilings
