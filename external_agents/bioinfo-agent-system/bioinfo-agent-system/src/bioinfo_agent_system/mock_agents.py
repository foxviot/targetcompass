from __future__ import annotations

from copy import deepcopy
from typing import Any

from .io_utils import mock_provenance


EXPECTED_QUESTION = (
    "Find secreted or surface molecules in type 2 diabetes adipose tissue "
    "that are associated with inflammatory gene upregulation."
)
SCHEMA_VERSION = "1.0.0"


def build_mock_output_bundle(
    raw_user_question: str, created_at: str
) -> dict[str, dict[str, Any]]:
    question_output = run_question_normalizer(raw_user_question, created_at)
    scope_output = run_scope_resolver(question_output, created_at)
    evidence_output = run_evidence_dataset_scout(scope_output, created_at)
    method_output = run_method_extraction_agent(evidence_output, created_at)
    motif_output = run_method_motif_feasibility_synthesizer(
        evidence_output, method_output, created_at
    )
    plan_output = run_research_plan_compiler(
        question_output,
        scope_output,
        evidence_output,
        method_output,
        motif_output,
        created_at,
    )
    return {
        "01_scientific_question_normalizer": question_output,
        "02_scope_ontology_resolver": scope_output,
        "03_evidence_dataset_scout": evidence_output,
        "04_method_extraction_agent": method_output,
        "05_method_motif_feasibility_synthesizer": motif_output,
        "06_research_plan_compiler": plan_output,
    }


def run_question_normalizer(raw_user_question: str, created_at: str) -> dict[str, Any]:
    _require_expected_question(raw_user_question)
    output = _base_output(
        "01_scientific_question_normalizer",
        created_at,
        "co_expression",
        "The bundled question asks for association with inflammatory gene upregulation only.",
    )
    output.update(
        {
            "original_question": raw_user_question,
            "expected_answer_type": "candidate_ranked_list",
            "task_mode": "bioinfo_research_planning",
            "question_type": "candidate_discovery",
            "primary_entity_type": "molecule",
            "literal_extraction": [
                "secreted",
                "surface",
                "type 2 diabetes",
                "adipose tissue",
                "inflammatory gene upregulation",
            ],
            "normalized_context": {
                "disease": "type 2 diabetes",
                "tissue": "adipose tissue",
                "molecular_constraints": ["secreted", "surface"],
                "biological_programs": ["inflammatory gene program"],
            },
            "directed_relations": [
                {
                    "subject": "secreted_or_surface_molecule",
                    "predicate": "is_associated_with",
                    "object": "inflammatory_gene_upregulation",
                    "directionality": "molecule_to_program",
                    "user_requested_claim_strength": "association",
                    "allowed_claim_strength_at_normalization": "co_expression",
                    "not_equivalent_to": [
                        "molecule_is_a_causal_driver",
                        "molecule_is_a_validated_therapeutic_target",
                        "molecule_is_experimentally_confirmed_secreted",
                    ],
                }
            ],
            "candidate_interpretations": [
                "association_screen",
                "co_expression_screen",
                "candidate_localization_screen",
            ],
            "primary_interpretation": "association_screen",
            "subquestions": [
                "Which candidate molecules satisfy the secreted or surface filter?",
                "Which candidates track the inflammatory gene program in T2D adipose tissue?",
                "Can fallback single-cell data localize candidate expression without upgrading the claim?",
            ],
            "blocking_uncertainties": [],
            "non_blocking_uncertainties": [
                "Bulk adipose expression does not resolve the source cell type.",
                "Secreted or surface labels are annotation-level only in this mock pipeline.",
            ],
            "default_assumptions": [
                "Human adipose datasets are primary if available.",
                "Expression-only evidence remains non-causal.",
            ],
            "forbidden_inferences": [
                "Do not infer causal drivers from association.",
                "Do not infer validated therapeutic targets from expression and annotation.",
                "Do not infer experimentally confirmed extracellular accessibility from surface or secretome labels.",
            ],
            "method_discovery_needs": [
                "Bulk expression program association workflow",
                "Surface or secretome candidate annotation workflow",
                "Fallback single-cell localization workflow",
            ],
            "planner_hints": [
                "Start with tissue-level association and ranking.",
                "Keep localization as a fallback path.",
                "Preserve the candidate-level claim boundary in the final plan.",
            ],
        }
    )
    return output


def run_scope_resolver(
    question_output: dict[str, Any], created_at: str
) -> dict[str, Any]:
    output = _base_output(
        "02_scope_ontology_resolver",
        created_at,
        question_output["claim_ceiling"]["max_allowed_claim"],
        "Ontology resolution does not increase the claim ceiling.",
    )
    output.update(
        {
            "normalized_question_ref": question_output["agent_id"],
            "disease_candidates": [
                "type 2 diabetes mellitus",
                "diabetes mellitus",
                "insulin resistance",
            ],
            "tissue_candidates": [
                "adipose tissue",
                "subcutaneous adipose tissue",
                "visceral adipose tissue",
            ],
            "species_scope": [
                "human priority",
                "mouse fallback for localization only",
            ],
            "population_scope": [
                "adult metabolic disease cohorts",
                "case-control adipose tissue studies",
            ],
            "cell_type_candidates": [
                "adipocyte",
                "macrophage",
                "stromal vascular fraction cell",
                "endothelial cell",
            ],
            "molecular_entity_types": [
                "secreted protein",
                "cell surface protein",
                "plasma membrane protein",
                "extracellular region annotated protein",
            ],
            "biological_program_terms": [
                "inflammatory response",
                "TNF signaling",
                "IL-6 signaling",
                "NF-kB signaling",
            ],
            "gene_set_candidates": [
                "hallmark_inflammatory_response",
                "tnfa_signaling_via_nfkb",
                "il6_jak_stat3_signaling",
            ],
            "synonyms": [
                "T2D",
                "type II diabetes",
                "fat tissue",
                "adipose depot",
                "secretome",
                "surfaceome",
            ],
            "excluded_expansions": [
                "type 1 diabetes mellitus as the primary disease scope",
                "liver tissue expansion",
                "causal target-discovery framing",
            ],
            "search_query_terms": [
                "type 2 diabetes adipose inflammatory response secreted molecule",
                "type 2 diabetes adipose surface protein inflammatory program",
                "adipose tissue inflammation secretome T2D",
            ],
            "ambiguity_report": [
                "The adipose depot is not specified, so subcutaneous and visceral remain candidates.",
                "Surface and secreted constraints are search filters, not validated accessibility claims.",
            ],
            "recommended_scope": {
                "disease": "type 2 diabetes mellitus",
                "tissue": "adipose tissue",
                "species": "human_priority_with_mouse_fallback",
                "molecular_constraint": "secreted_or_surface",
            },
        }
    )
    return output


def run_evidence_dataset_scout(
    scope_output: dict[str, Any], created_at: str
) -> dict[str, Any]:
    output = _base_output(
        "03_evidence_dataset_scout",
        created_at,
        "co_expression",
        "Mock datasets support tissue-level association and co-expression, not causality.",
    )
    output.update(
        {
            "normalized_question_ref": scope_output["agent_id"],
            "analogous_studies": [
                {
                    "study_id": "MOCK-STUDY-ADIPOSE-ASSOC-001",
                    "source_type": "mock",
                    "source_identifier": "MOCK-STUDY-ADIPOSE-ASSOC-001",
                    "relevance": "Mock adipose bulk expression study aligned to inflammatory association screening.",
                    "supports": [
                        "tissue-level disease-control contrast",
                        "molecule-program association screening",
                    ],
                    "does_not_support": [
                        "causal driver status",
                        "validated extracellular accessibility",
                    ],
                    "provenance": mock_provenance("MOCK-STUDY-ADIPOSE-ASSOC-001"),
                },
                {
                    "study_id": "MOCK-STUDY-ADIPOSE-SCRNA-002",
                    "source_type": "mock",
                    "source_identifier": "MOCK-STUDY-ADIPOSE-SCRNA-002",
                    "relevance": "Mock adipose single-cell study aligned to fallback localization review.",
                    "supports": [
                        "cell-type localization review",
                        "expression context for candidate molecules",
                    ],
                    "does_not_support": [
                        "well-powered T2D case-control ranking",
                        "functional secretion validation",
                    ],
                    "provenance": mock_provenance("MOCK-STUDY-ADIPOSE-SCRNA-002"),
                },
            ],
            "dataset_candidates": [
                {
                    "dataset_id": "MOCK-GSE-T2D-ADIPOSE-001",
                    "repository": "GEO",
                    "accession": "MOCK-GSE-T2D-ADIPOSE-001",
                    "organism": "Homo sapiens",
                    "tissue": "adipose tissue",
                    "assay_type": "bulk RNA-seq",
                    "sample_size": 84,
                    "metadata_fields": [
                        "disease_status",
                        "adipose_depot",
                        "sex",
                        "body_mass_index",
                        "batch",
                    ],
                    "data_availability": ["processed_matrix", "sample_metadata"],
                    "supports": [
                        "tissue-level differential expression context",
                        "molecule-program association screening",
                    ],
                    "does_not_support": [
                        "cell-type source attribution",
                        "causal inference",
                    ],
                    "feasibility_score": 88,
                    "recommendation": "primary",
                    "provenance": mock_provenance("MOCK-GSE-T2D-ADIPOSE-001"),
                },
                {
                    "dataset_id": "MOCK-CELLXGENE-ADIPOSE-002",
                    "repository": "CELLxGENE",
                    "accession": "MOCK-CELLXGENE-ADIPOSE-002",
                    "organism": "Homo sapiens",
                    "tissue": "adipose tissue",
                    "assay_type": "scRNA-seq",
                    "sample_size": 126000,
                    "metadata_fields": [
                        "cell_type",
                        "donor_status",
                        "adipose_depot",
                    ],
                    "data_availability": ["count_matrix", "cell_metadata"],
                    "supports": [
                        "fallback cell-type localization",
                        "candidate expression context review",
                    ],
                    "does_not_support": [
                        "high-confidence case-control ranking by itself",
                        "validated surface accessibility",
                    ],
                    "feasibility_score": 72,
                    "recommendation": "fallback",
                    "provenance": mock_provenance("MOCK-CELLXGENE-ADIPOSE-002"),
                },
                {
                    "dataset_id": "MOCK-GSE-LIVER-003",
                    "repository": "GEO",
                    "accession": "MOCK-GSE-LIVER-003",
                    "organism": "Homo sapiens",
                    "tissue": "liver",
                    "assay_type": "bulk RNA-seq",
                    "sample_size": 40,
                    "metadata_fields": ["disease_status"],
                    "data_availability": ["processed_matrix"],
                    "supports": ["generic T2D expression background"],
                    "does_not_support": [
                        "adipose tissue specificity",
                        "direct support for the user question",
                    ],
                    "feasibility_score": 18,
                    "recommendation": "reject",
                    "provenance": mock_provenance("MOCK-GSE-LIVER-003"),
                },
            ],
            "answerable_parts": [
                "Rank candidate secreted or surface molecules associated with inflammatory activation in T2D adipose tissue.",
                "Use fallback single-cell evidence to localize candidate expression by cell type.",
                "Summarize which claims remain candidate-level only.",
            ],
            "unanswerable_parts": [
                "Causal driver status for insulin resistance or inflammation.",
                "Experimentally confirmed secretion or true extracellular accessibility.",
                "Validated therapeutic target readiness.",
            ],
            "no_suitable_dataset": False,
            "recommended_dataset_ids": [
                "MOCK-GSE-T2D-ADIPOSE-001",
                "MOCK-CELLXGENE-ADIPOSE-002",
            ],
        }
    )
    return output


def run_method_extraction_agent(
    evidence_output: dict[str, Any], created_at: str
) -> dict[str, Any]:
    output = _base_output(
        "04_method_extraction_agent",
        created_at,
        evidence_output["claim_ceiling"]["max_allowed_claim"],
        "Method extraction preserves the claim ceiling from evidence review.",
    )
    output.update(
        {
            "study_method_extractions": [
                {
                    "study_id": "MOCK-STUDY-ADIPOSE-ASSOC-001",
                    "method_steps": [
                        {
                            "step_id": "expression_matrix_qc",
                            "step_name": "expression_matrix_qc",
                            "input": "bulk expression matrix and sample metadata",
                            "output": "QC-filtered matrix",
                            "tool": "standard_bulk_qc",
                            "parameters": [
                                "remove low-quality samples",
                                "retain disease and control labels",
                            ],
                            "evidence_source_location": "mock.study.methods.1",
                            "source_status": "extracted",
                        },
                        {
                            "step_id": "disease_control_contrast",
                            "step_name": "disease_control_contrast",
                            "input": "QC-filtered matrix",
                            "output": "contrast-ready expression matrix",
                            "tool": "linear_model_contrast",
                            "parameters": [
                                "disease versus control contrast",
                                "include available batch covariate",
                            ],
                            "evidence_source_location": "mock.study.methods.2",
                            "source_status": "extracted",
                        },
                        {
                            "step_id": "inflammatory_program_scoring",
                            "step_name": "inflammatory_program_scoring",
                            "input": "contrast-ready expression matrix",
                            "output": "sample-level inflammatory program scores",
                            "tool": "gene_set_scoring",
                            "parameters": ["use inflammatory response gene set"],
                            "evidence_source_location": "mock.study.methods.3",
                            "source_status": "extracted",
                        },
                        {
                            "step_id": "molecule_program_association",
                            "step_name": "molecule_program_association",
                            "input": "program scores plus candidate expression values",
                            "output": "candidate association table",
                            "tool": "association_test",
                            "parameters": [
                                "rank molecules by association magnitude and direction"
                            ],
                            "evidence_source_location": "mock.study.methods.4",
                            "source_status": "extracted",
                        },
                        {
                            "step_id": "surface_secretome_annotation",
                            "step_name": "surface_secretome_annotation",
                            "input": "candidate association table",
                            "output": "candidate annotation table",
                            "tool": "annotation_join",
                            "parameters": [
                                "retain secreted or surface annotated molecules only"
                            ],
                            "evidence_source_location": "mock.study.methods.5",
                            "source_status": "extracted",
                        },
                        {
                            "step_id": "external_validation",
                            "step_name": "external_validation",
                            "input": "candidate annotation table plus fallback dataset",
                            "output": "cross-dataset consistency notes",
                            "tool": "cross_dataset_review",
                            "parameters": ["review localization consistency only"],
                            "evidence_source_location": "mock.study.methods.6",
                            "source_status": "inferred",
                        },
                        {
                            "step_id": "perturbation_validation",
                            "step_name": "perturbation_validation",
                            "input": "top ranked candidates",
                            "output": "causal validation result",
                            "tool": "not_reported",
                            "parameters": [],
                            "evidence_source_location": "mock.study.methods.7",
                            "source_status": "missing",
                        },
                    ],
                }
            ],
            "transferable_components": [
                "bulk expression QC",
                "inflammatory program scoring",
                "molecule-program association ranking",
                "surface or secretome annotation filtering",
            ],
            "non_transferable_components": [
                "wet-lab perturbation validation",
                "claim upgrades beyond co-expression",
            ],
            "quality_concerns": [
                "Bulk tissue averages across cell types.",
                "Annotation filters do not prove secretion or accessibility.",
                "Inferred validation steps remain weaker than extracted steps.",
            ],
            "statistical_controls": [
                "Apply multiple-testing control when ranking candidates.",
                "Keep disease-control contrasts tied to available metadata only.",
            ],
            "validation_strategy": [
                "Use fallback single-cell data for localization only.",
                "Keep final claims at candidate association level.",
            ],
            "extraction_confidence": "medium",
        }
    )
    return output


def run_method_motif_feasibility_synthesizer(
    evidence_output: dict[str, Any],
    method_output: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    del evidence_output, method_output
    output = _base_output(
        "05_method_motif_feasibility_synthesizer",
        created_at,
        "co_expression",
        "Feasible local contracts remain limited to association-level evidence.",
    )
    output.update(
        {
            "method_motifs": [
                {
                    "motif_id": "expression_program_association_screening",
                    "applicable_questions": ["candidate_discovery", "pathway_program_association"],
                    "required_inputs": ["bulk expression matrix", "inflammatory gene set"],
                    "recommended_steps": [
                        "dataset metadata audit",
                        "bulk expression QC",
                        "inflammatory program scoring",
                        "molecule-program association test",
                    ],
                    "optional_steps": ["covariate sensitivity review"],
                    "not_applicable_when": [
                        "no disease-control contrast is available"
                    ],
                    "common_failure_modes": [
                        "over-interpreting correlation as causality"
                    ],
                    "evidence_type_produced": ["association", "co_expression"],
                    "transferability_assessment": "High for mock bulk expression datasets.",
                    "feasibility_assessment": "Ready with primary bulk RNA-seq mock data.",
                },
                {
                    "motif_id": "surface_secretome_candidate_filtering",
                    "applicable_questions": ["candidate_discovery", "target_prioritization"],
                    "required_inputs": ["candidate association table", "annotation table"],
                    "recommended_steps": [
                        "join candidate table to annotation resource",
                        "retain secreted or surface candidates",
                        "rank candidates with annotation flags retained",
                    ],
                    "optional_steps": ["manual review of ambiguous annotations"],
                    "not_applicable_when": [
                        "no candidate association table is available"
                    ],
                    "common_failure_modes": [
                        "treating annotations as proof of accessibility"
                    ],
                    "evidence_type_produced": ["candidate list", "annotation-aware ranking"],
                    "transferability_assessment": "High for conservative candidate filtering.",
                    "feasibility_assessment": "Ready with mock annotation joins only.",
                },
                {
                    "motif_id": "bulk_to_single_cell_localization",
                    "applicable_questions": ["candidate_discovery", "cell_state_discovery"],
                    "required_inputs": ["bulk-ranked candidates", "single-cell expression matrix"],
                    "recommended_steps": [
                        "map ranked candidates into the fallback single-cell dataset",
                        "summarize cell-type localization patterns",
                    ],
                    "optional_steps": ["compare depot-specific localization if metadata permit"],
                    "not_applicable_when": [
                        "single-cell metadata are unavailable"
                    ],
                    "common_failure_modes": [
                        "upgrading localization into cell-type-specific causality"
                    ],
                    "evidence_type_produced": ["localization evidence"],
                    "transferability_assessment": "Moderate because single-cell data are fallback only.",
                    "feasibility_assessment": "Ready as a fallback review path.",
                },
                {
                    "motif_id": "external_dataset_validation",
                    "applicable_questions": ["candidate_discovery", "validation_review"],
                    "required_inputs": ["ranked candidates", "independent fallback dataset"],
                    "recommended_steps": [
                        "check qualitative consistency across datasets",
                        "carry forward only candidate-level language",
                    ],
                    "optional_steps": ["compare consistency by adipose depot"],
                    "not_applicable_when": [
                        "no independent fallback dataset is available"
                    ],
                    "common_failure_modes": [
                        "treating a secondary dataset as experimental validation"
                    ],
                    "evidence_type_produced": ["consistency review"],
                    "transferability_assessment": "Moderate for mock fallback evidence.",
                    "feasibility_assessment": "Ready if used conservatively.",
                },
            ],
            "not_recommended_methods": [
                "unvalidated multistage feature selection classifiers as core evidence",
                "causal Mendelian randomization without appropriate genetic support",
            ],
            "method_quality_flags": [
                "Small-sample multi-step machine learning pipelines are optional only.",
                "Localization evidence remains indirect without orthogonal validation.",
            ],
            "local_method_contracts": [
                _contract(
                    "dataset_metadata_audit",
                    "Confirm phenotype and tissue metadata before ranking candidates.",
                    "ready",
                    ["sample metadata"],
                    "Required first step for deterministic mock intake.",
                ),
                _contract(
                    "bulk_expression_qc",
                    "QC the primary bulk expression matrix.",
                    "ready",
                    ["bulk expression matrix", "sample metadata"],
                    "Supports association-level downstream analyses only.",
                ),
                _contract(
                    "inflammatory_program_scoring",
                    "Score inflammatory activity in primary bulk data.",
                    "ready",
                    ["QC-filtered matrix", "inflammatory gene set"],
                    "Produces sample-level program scores.",
                ),
                _contract(
                    "molecule_program_association",
                    "Rank molecules by association with the inflammatory program.",
                    "ready",
                    ["program scores", "candidate expression matrix"],
                    "Association-only contract.",
                ),
                _contract(
                    "surface_secretome_annotation",
                    "Apply secreted or surface annotations to ranked molecules.",
                    "ready",
                    ["candidate association table", "annotation table"],
                    "Annotation is candidate-level only.",
                ),
                _contract(
                    "candidate_ranking",
                    "Build a conservative ranked candidate list.",
                    "ready",
                    ["annotation-aware candidate table"],
                    "Do not upgrade beyond co-expression claims.",
                ),
                _contract(
                    "bulk_to_single_cell_localization",
                    "Use fallback single-cell data for localization review.",
                    "ready",
                    ["ranked candidates", "single-cell matrix"],
                    "Fallback-only localization contract.",
                ),
                _contract(
                    "external_dataset_validation",
                    "Review qualitative consistency in a fallback dataset.",
                    "ready",
                    ["ranked candidates", "fallback dataset"],
                    "Supports consistency review, not causal validation.",
                ),
                _contract(
                    "evidence_report_compilation",
                    "Draft the final evidence summary and report outline.",
                    "ready",
                    ["task outputs", "claim boundaries"],
                    "Preserves claim ceiling and caveats in the final report.",
                ),
                _contract(
                    "causal_mr_support",
                    "Estimate causal support from genetics.",
                    "blocked",
                    ["genetic instruments", "compatible summary statistics"],
                    "Blocked because no causal data source exists in the mock pipeline.",
                ),
                _contract(
                    "wetlab_validation",
                    "Validate top candidates experimentally.",
                    "blocked",
                    ["wet-lab assays", "biological samples"],
                    "Blocked because the mock pipeline does not execute experiments.",
                ),
                _contract(
                    "vaccine_antigen_validation",
                    "Assess antigen accessibility and readiness.",
                    "blocked",
                    ["surface validation assays", "immune profiling"],
                    "Blocked because the question does not support vaccine framing.",
                ),
                _contract(
                    "target_engagement_assay",
                    "Demonstrate target engagement downstream.",
                    "missing",
                    ["perturbation assay design"],
                    "Missing because no source-bound method contract is available.",
                ),
            ],
            "ready_contracts": [
                "dataset_metadata_audit",
                "bulk_expression_qc",
                "inflammatory_program_scoring",
                "molecule_program_association",
                "surface_secretome_annotation",
                "candidate_ranking",
                "bulk_to_single_cell_localization",
                "external_dataset_validation",
                "evidence_report_compilation",
            ],
            "blocked_contracts": [
                "causal_mr_support",
                "wetlab_validation",
                "vaccine_antigen_validation",
            ],
            "missing_contracts": ["target_engagement_assay"],
        }
    )
    return output


def run_research_plan_compiler(
    question_output: dict[str, Any],
    scope_output: dict[str, Any],
    evidence_output: dict[str, Any],
    method_output: dict[str, Any],
    motif_output: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    del scope_output, method_output, motif_output
    output = _base_output(
        "06_research_plan_compiler",
        created_at,
        "co_expression",
        "The executable plan remains at candidate association and co-expression level.",
    )

    task_packets = [
        _task_packet(
            "T1",
            "dataset intake and metadata audit",
            "Confirm the selected mock datasets match disease, tissue, and metadata expectations.",
            [
                "MOCK-GSE-T2D-ADIPOSE-001 metadata",
                "MOCK-CELLXGENE-ADIPOSE-002 metadata",
            ],
            ["dataset_audit_report.json"],
            [],
            "dataset_metadata_audit",
            [
                "Disease labels and adipose tissue scope are present.",
                "Fallback dataset availability is documented.",
            ],
            "Required phenotype or tissue metadata are missing.",
            "Gate the run before any analysis step.",
        ),
        _task_packet(
            "T2",
            "expression matrix QC",
            "Apply deterministic QC to the primary bulk expression matrix.",
            ["dataset_audit_report.json", "MOCK-GSE-T2D-ADIPOSE-001 matrix"],
            ["qc_expression_matrix.tsv"],
            ["T1"],
            "bulk_expression_qc",
            [
                "Primary matrix passes basic completeness checks.",
                "Case and control samples remain represented after QC.",
            ],
            "QC removes the disease or control branch entirely.",
            "Keep QC deterministic and metadata-aware.",
        ),
        _task_packet(
            "T3",
            "inflammatory gene program scoring",
            "Score inflammatory activation in the QC-filtered bulk dataset.",
            ["qc_expression_matrix.tsv", "inflammatory gene set"],
            ["inflammatory_program_scores.tsv"],
            ["T2"],
            "inflammatory_program_scoring",
            [
                "Every retained sample receives a program score.",
                "Scoring remains tied to the predefined inflammatory gene set.",
            ],
            "Program scores cannot be computed consistently for retained samples.",
            "This produces association-ready covariates only.",
        ),
        _task_packet(
            "T4",
            "molecule-program association test",
            "Rank candidate molecules by association with inflammatory activation.",
            ["qc_expression_matrix.tsv", "inflammatory_program_scores.tsv"],
            ["candidate_association_table.tsv"],
            ["T3"],
            "molecule_program_association",
            [
                "Candidate association table records direction and relative strength.",
                "Results remain framed as association or co-expression only.",
            ],
            "No candidate association table can be produced from the selected data.",
            "Do not reinterpret association as mechanism.",
        ),
        _task_packet(
            "T5",
            "surface or secretome annotation",
            "Apply candidate-level surface or secretome annotation filters.",
            ["candidate_association_table.tsv", "annotation table"],
            ["candidate_annotation_table.tsv"],
            ["T4"],
            "surface_secretome_annotation",
            [
                "Annotated candidates retain their original association evidence.",
                "Annotation status is preserved as candidate-level metadata.",
            ],
            "Required annotation table is unavailable.",
            "Annotation does not prove true accessibility.",
        ),
        _task_packet(
            "T6",
            "candidate ranking",
            "Produce a conservative ranked list of candidate molecules.",
            ["candidate_annotation_table.tsv"],
            ["ranked_candidates.tsv"],
            ["T5"],
            "candidate_ranking",
            [
                "Ranking preserves association-level language.",
                "Candidates outside the secreted or surface filter are excluded.",
            ],
            "No candidates remain after annotation filtering.",
            "Ranking is the primary answerable deliverable.",
        ),
        _task_packet(
            "T7",
            "fallback cell-type localization",
            "Review whether ranked candidates localize to interpretable adipose cell populations.",
            ["ranked_candidates.tsv", "MOCK-CELLXGENE-ADIPOSE-002 matrix"],
            ["candidate_localization_notes.md"],
            ["T5"],
            "bulk_to_single_cell_localization",
            [
                "Localization notes keep the fallback nature explicit.",
                "No cell-type-specific causal claim is introduced.",
            ],
            "Fallback single-cell metadata are insufficient for localization review.",
            "This is optional support, not a required ranking input.",
        ),
        _task_packet(
            "T8",
            "evidence summary and report drafting",
            "Compile the final report with claim boundaries, caveats, and next steps.",
            [
                "ranked_candidates.tsv",
                "candidate_localization_notes.md",
                "claim boundary specification",
            ],
            ["final_report_outline.md"],
            ["T6", "T7"],
            "evidence_report_compilation",
            [
                "Report states answerable and unanswerable scope.",
                "Report repeats allowed and forbidden claims explicitly.",
            ],
            "The report attempts to exceed the claim ceiling.",
            "The final report remains mock-only and non-causal.",
        ),
    ]

    output.update(
        {
            "plan_id": "mock-t2d-adipose-inflammatory-plan",
            "normalized_research_question": question_output["original_question"],
            "answerable_scope": deepcopy(evidence_output["answerable_parts"]),
            "unanswerable_scope": deepcopy(evidence_output["unanswerable_parts"]),
            "selected_datasets": [
                "MOCK-GSE-T2D-ADIPOSE-001",
                "MOCK-CELLXGENE-ADIPOSE-002",
            ],
            "selected_method_contracts": [
                "dataset_metadata_audit",
                "bulk_expression_qc",
                "inflammatory_program_scoring",
                "molecule_program_association",
                "surface_secretome_annotation",
                "candidate_ranking",
                "bulk_to_single_cell_localization",
                "evidence_report_compilation",
            ],
            "evidence_dag": [
                {
                    "edge_id": "E1",
                    "from": "normalized_question",
                    "to": "MOCK-GSE-T2D-ADIPOSE-001",
                    "rationale": "Primary bulk dataset supports tissue-level association screening.",
                },
                {
                    "edge_id": "E2",
                    "from": "normalized_question",
                    "to": "MOCK-CELLXGENE-ADIPOSE-002",
                    "rationale": "Fallback single-cell dataset supports localization review.",
                },
                {
                    "edge_id": "E3",
                    "from": "MOCK-GSE-T2D-ADIPOSE-001",
                    "to": "expression_program_association_screening",
                    "rationale": "Primary dataset feeds association scoring and ranking.",
                },
                {
                    "edge_id": "E4",
                    "from": "expression_program_association_screening",
                    "to": "surface_secretome_candidate_filtering",
                    "rationale": "Candidate ranking is filtered by annotation after association scoring.",
                },
                {
                    "edge_id": "E5",
                    "from": "MOCK-CELLXGENE-ADIPOSE-002",
                    "to": "bulk_to_single_cell_localization",
                    "rationale": "Fallback localization uses the single-cell dataset only.",
                },
                {
                    "edge_id": "E6",
                    "from": "task_dag",
                    "to": "claim_boundaries",
                    "rationale": "Final reporting must honor the claim ceiling from upstream agents.",
                },
            ],
            "task_dag": [_task_without_notes(packet) for packet in task_packets],
            "fallback_paths": [
                "If the fallback single-cell dataset lacks disease labels, use it for localization only and keep ranking anchored to bulk tissue association.",
                "If adipose depot metadata are incomplete, report combined adipose scope and preserve the ambiguity.",
            ],
            "stop_conditions": [
                "Stop if only rejected datasets remain after feasibility review.",
                "Stop if a selected method contract is not in the ready set.",
                "Stop if any downstream conclusion would require a causal claim upgrade.",
            ],
            "claim_boundaries": {
                "allowed_claims": [
                    "Candidate secreted or surface molecules associated with inflammatory activation in type 2 diabetes adipose tissue."
                ],
                "forbidden_claims": [
                    "Causal drivers of insulin resistance.",
                    "Validated therapeutic targets.",
                    "Vaccine-ready antigens.",
                    "Experimentally confirmed secreted or surface proteins.",
                ],
                "caveats": [
                    "Primary evidence is tissue-level association only.",
                    "Fallback localization does not resolve causality.",
                    "Annotation-based accessibility remains candidate-level only.",
                ],
            },
            "report_outline": [
                "Normalized question and scope",
                "Mock datasets and feasibility decisions",
                "Method motifs and selected contracts",
                "Task DAG and acceptance criteria",
                "Candidate ranking with claim boundaries",
                "Limitations and next validation steps",
            ],
            "codex_task_packets": task_packets,
        }
    )
    return output


def _base_output(
    agent_id: str, created_at: str, claim_ceiling: str, claim_reason: str
) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "schema_version": SCHEMA_VERSION,
        "created_at": created_at,
        "provenance": [mock_provenance(agent_id)],
        "warnings": [],
        "blocking_failures": [],
        "claim_ceiling": {
            "max_allowed_claim": claim_ceiling,
            "reason": claim_reason,
        },
    }


def _contract(
    contract_id: str,
    purpose: str,
    status: str,
    required_inputs: list[str],
    notes: str,
) -> dict[str, Any]:
    return {
        "contract_id": contract_id,
        "purpose": purpose,
        "status": status,
        "required_inputs": required_inputs,
        "notes": notes,
    }


def _task_packet(
    task_id: str,
    name: str,
    purpose: str,
    input_artifacts: list[str],
    output_artifacts: list[str],
    dependencies: list[str],
    method_contract_id: str,
    acceptance_criteria: list[str],
    failure_condition: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "name": name,
        "purpose": purpose,
        "input_artifacts": input_artifacts,
        "output_artifacts": output_artifacts,
        "dependencies": dependencies,
        "method_contract_id": method_contract_id,
        "acceptance_criteria": acceptance_criteria,
        "failure_condition": failure_condition,
        "notes": notes,
    }


def _task_without_notes(task_packet: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in task_packet.items() if key != "notes"
    }


def _require_expected_question(raw_user_question: str) -> None:
    if raw_user_question.strip() != EXPECTED_QUESTION:
        raise ValueError(
            "This mock pipeline currently supports only the bundled example question."
        )
