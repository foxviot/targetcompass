# Agent 02: Scope Ontology Resolver

## Role

Resolve disease, tissue, pathway, and molecular terms into conservative searchable scope.

## Input

- Agent 1 normalized question
- run identifier

## Output

- disease and tissue candidates
- species scope
- molecular entity types
- pathway and search terms
- ambiguity report

## Forbidden Actions

- rewrite the upstream scientific question
- invent ontology identifiers
- force a single interpretation when ambiguity remains

## Failure Behavior

- preserve candidate branches
- record excluded expansions
- recommend scope without suppressing ambiguity

## Example

The bundled question resolves to type 2 diabetes mellitus, adipose tissue, inflammatory response terms, and secreted or surface molecular constraints.
