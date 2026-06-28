# Method Motif Rules

A method motif is a reusable analysis pattern abstracted from one or more source-bound study methods.

Example motifs:

- `expression_program_association_screening`
- `surface_secretome_candidate_filtering`
- `cell_state_marker_discovery`
- `bulk_to_single_cell_localization`
- `external_dataset_validation`
- `genetic_support_prioritization`

Each motif must define:

- applicable conditions
- non-applicable conditions
- required inputs
- recommended steps
- optional steps
- common failure modes
- evidence type produced

Popularity is not enough. A motif can still be blocked or not recommended if the available data cannot support it.
