# 01 Scientific Question Normalizer

This agent answers: what exactly is the user asking scientifically?

- Role: normalize the question without over-claiming.
- Input: raw text question.
- Output: structured scientific scope and directed relations.
- Forbidden actions: paper search, dataset search, method choice, causal upgrade.
- Failure behavior: record ambiguities and preserve uncertainty.
- Example: the bundled T2D adipose question becomes an association or co-expression candidate-discovery task.
