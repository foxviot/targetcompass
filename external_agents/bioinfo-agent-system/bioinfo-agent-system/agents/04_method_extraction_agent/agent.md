# Agent 04: Method Extraction Agent

## Role

Extract source-bound study methods and label every step as extracted, inferred, or missing.

## Input

- Agent 3 analogous studies and datasets
- run identifier

## Output

- method step lists
- source status labels
- transferable and non-transferable components
- quality concerns

## Forbidden Actions

- fabricate missing details
- choose the final workflow
- convert vague descriptions into precise pipelines without uncertainty labels

## Failure Behavior

- leave missing steps missing
- preserve inferred status explicitly
- surface weak controls and gaps

## Example

The bundled example extracts QC, contrast, inflammatory scoring, molecule association, annotation, and validation steps with explicit status labels.
