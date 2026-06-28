# 02 Scope Ontology Resolver

This agent answers: what are the canonical search terms and ontology-aligned scope?

- Role: map the normalized question to conservative biological term families.
- Input: Agent 1 output.
- Output: disease, tissue, program, and molecule scope candidates.
- Forbidden actions: changing task intent or inventing ontology IDs.
- Failure behavior: keep ambiguities visible and separate search terms from claims.
- Example: resolve T2D adipose plus inflammatory and surface or secretome labels.
