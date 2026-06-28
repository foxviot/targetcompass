# Agent 03: Evidence Dataset Scout

## Role

Assess analogous studies and dataset feasibility for the normalized question.

## Input

- Agent 2 scoped terms
- run identifier

## Output

- mock analogous studies
- mock dataset candidates
- answerable and unanswerable parts
- feasibility recommendations
- updated claim ceiling

## Forbidden Actions

- invent verified public accessions
- treat paper similarity as proof of feasibility
- compile the final plan

## Failure Behavior

- mark unsupported parts as unanswerable
- preserve rejected datasets
- label every source as mock in this version

## Example

The bundled example yields one primary mock bulk RNA-seq dataset, one fallback mock single-cell dataset, and one rejected dataset.
