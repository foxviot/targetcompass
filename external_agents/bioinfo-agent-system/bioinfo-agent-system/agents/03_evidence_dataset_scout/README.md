# 03 Evidence Dataset Scout

This agent answers: what parts of the question are answerable with analogous evidence and datasets?

- Role: score feasibility rather than prove biology.
- Input: Agent 2 scope output.
- Output: mock studies, datasets, answerable scope, and claim ceiling.
- Forbidden actions: final planning or unsupported evidence claims.
- Failure behavior: keep rejected datasets visible and mark unsupported scope explicitly.
- Example: use mock T2D adipose bulk and single-cell sources only.
