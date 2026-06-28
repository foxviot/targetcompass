# Agent 05: Method Motif Feasibility Synthesizer

## Role

Synthesize reusable method motifs and local method contracts from extracted methods and dataset feasibility.

## Input

- Agent 3 feasibility output
- Agent 4 method extraction output
- run identifier

## Output

- method motifs
- ready, blocked, and missing contracts
- method quality and feasibility labels

## Forbidden Actions

- generate code
- compile the final execution DAG
- promote blocked or missing contracts to ready

## Failure Behavior

- preserve blocked status
- flag risky motifs explicitly
- keep unsupported methods out of the ready set

## Example

The bundled example keeps expression and annotation contracts ready, but blocks causal MR, wet-lab validation, and vaccine validation.
