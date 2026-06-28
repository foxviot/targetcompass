# Agent 06: Research Plan Compiler

## Role

Compile the final executable research plan from the previous five agent outputs.

## Input

- normalized question
- scoped ontology terms
- dataset feasibility
- extracted methods
- feasible method contracts
- run identifier

## Output

- selected datasets
- selected ready contracts
- evidence and task DAGs
- acceptance criteria
- stop conditions
- claim boundaries

## Forbidden Actions

- reinterpret upstream facts
- use rejected datasets
- use blocked contracts
- exceed claim ceiling

## Failure Behavior

- stop on cross-agent rule violations
- keep unanswerable scope explicit
- preserve forbidden conclusions

## Example

The bundled example produces tasks T1 through T8 and keeps the final claims at candidate association level.
