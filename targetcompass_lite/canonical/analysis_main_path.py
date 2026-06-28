from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .artifacts import register_artifact
from .backend_writer import write_json_artifact
from .failure_recovery import build_v5_failure_recovery_report
from .local_execution import compile_registered_analysis_task_packets, execute_registered_analysis_task_packets
from .report_manifest import build_canonical_report_manifest
from .resource_gate import build_resource_gate_report
from .schemas import now_iso


ANALYSIS_MAIN_PATH_SCHEMA = "v5.analysis_main_path/0.1"


def run_v5_analysis_main_path(
    project_dir: str | Path,
    *,
    question: str = "",
    accession: str = "",
    source: str = "geo",
    case_label: str = "",
    control_label: str = "",
    case_pattern: str = "",
    control_pattern: str = "",
    tissue: str = "",
    organism: str = "",
    platform_annotation: str = "",
    symbol_column: str = "",
    max_analysis_packets: int | None = None,
    force_download: bool = False,
    import_geo_func: Callable[..., Any] | None = None,
    compile_func: Callable[..., dict[str, Any]] | None = None,
    execute_func: Callable[..., dict[str, Any]] | None = None,
    report_func: Callable[..., dict[str, Any]] | None = None,
    export_func: Callable[[Path], Path] | None = None,
) -> dict[str, Any]:
    """Run the v5 real-data main path with explicit lock/recovery gates.

    The path is intentionally conservative: if no dataset has passed the v5
    dataset lock gate, it writes a structured blocked manifest instead of
    pretending analysis has completed.
    """

    project_dir = Path(project_dir)
    selected = _select_dataset(project_dir, accession)
    manifest: dict[str, Any] = {
        "schema_version": ANALYSIS_MAIN_PATH_SCHEMA,
        "project_id": project_dir.name,
        "question": question,
        "created_at": now_iso(),
        "source": source,
        "selected_dataset": selected,
        "stages": [],
        "status": "running",
        "recovery": [],
    }

    if not selected.get("accession"):
        manifest["status"] = "blocked"
        manifest["stages"].append(_stage("dataset_lock", "blocked", selected.get("reason", "no dataset selected")))
        manifest["recovery"].append(
            {
                "category": "dataset_lock",
                "severity": "high",
                "reason": selected.get("reason", "No lockable dataset is available."),
                "recovery_actions": [
                    "Open v5 Dataset Gate and fill group, case/control labels, organism, tissue, platform, and sample count.",
                    "Re-run resource discovery if no real GEO/SRA/ArrayExpress/cellxgene candidate exists.",
                ],
                "rerun_commands": [f"python tc_lite.py v5-resource-gate --project {project_dir.name}"],
            }
        )
        return _write_manifest(project_dir, manifest)

    import_status: dict[str, Any] = {}
    selected_source = _selected_source(selected, source)
    selected_modality = (selected.get("modality") or "").lower()
    manifest["selected_route"] = _route_for_dataset(selected_source, selected_modality, selected.get("accession", ""))
    if selected_source == "geo" or str(selected.get("accession", "")).upper().startswith("GSE"):
        try:
            importer = import_geo_func or _default_geo_importer
            result = importer(
                project_dir,
                selected["accession"],
                tissue=tissue or selected.get("tissue", "") or "unknown",
                organism=organism or selected.get("organism", "") or "human",
                platform_annotation=Path(platform_annotation) if platform_annotation else None,
                symbol_column=symbol_column or None,
                force_download=force_download,
                case_hint=case_pattern or selected.get("case_label", ""),
                control_hint=control_pattern or selected.get("control_label", ""),
                case_label=case_label or selected.get("case_label", ""),
                control_label=control_label or selected.get("control_label", ""),
                group_column=selected.get("group_column", ""),
            )
            import_status = _geo_result_to_dict(result)
            manifest["stages"].append(_stage("real_data_download_parse_align", "completed", f"Imported {selected['accession']}"))
            manifest["geo_import"] = import_status
        except Exception as exc:
            error = _geo_error_to_dict(exc)
            manifest["status"] = "blocked"
            manifest["geo_import_error"] = error
            manifest["stages"].append(_stage("real_data_download_parse_align", "failed", error.get("message", str(exc))))
            manifest["recovery"].append(
                {
                    "category": "geo_import",
                    "severity": "high",
                    "reason": error.get("message", str(exc)),
                    "recovery_actions": error.get("recovery", []) or ["Retry with manual case/control hints or upload expression_matrix.tsv and metadata.tsv."],
                    "rerun_commands": [
                        f"python tc_lite.py v5-analysis-main-path --project {project_dir.name} --accession {selected['accession']} --case-pattern <case> --control-pattern <control>"
                    ],
                }
            )
            build_v5_failure_recovery_report(project_dir)
            return _write_manifest(project_dir, manifest)
    elif selected_source in {"sra", "cellxgene", "cz_cellxgene", "arrayexpress"}:
        local_matrix = _local_matrix_status(project_dir, selected)
        manifest["adapter_input_status"] = local_matrix
        if not local_matrix.get("ready"):
            manifest["status"] = "blocked"
            manifest["stages"].append(
                _stage(
                    "real_data_download_parse_align",
                    "blocked",
                    f"source {selected_source} requires a dedicated importer or quantification manifest before matrix analysis",
                )
            )
            manifest["recovery"].append(
                {
                    "category": f"{selected_source}_adapter",
                    "severity": "high",
                    "reason": f"{selected_source} candidate is metadata-verified but not matrix-parse-ready.",
                    "recovery_actions": [
                        "Provide an expression_matrix.tsv and metadata.tsv under data/<ACCESSION>/.",
                        "For SRA, run or attach a quantification manifest before DEG/pseudobulk analysis.",
                        "For cellxgene, attach an h5ad/cellxgene export plus donor/group/cell-type metadata mapping.",
                        "Re-run v5 Dataset Gate after the matrix artifacts are present.",
                    ],
                    "rerun_commands": [
                        f"python tc_lite.py v5-resource-gate --project {project_dir.name}",
                        f"python tc_lite.py v5-analysis-main-path --project {project_dir.name} --accession {selected['accession']} --source {selected_source}",
                    ],
                }
            )
            build_v5_failure_recovery_report(project_dir)
            return _write_manifest(project_dir, manifest)
        manifest["stages"].append(_stage("real_data_download_parse_align", "completed", f"Using local parsed matrix for {selected_source}:{selected['accession']}"))
        manifest["parsed_matrix"] = local_matrix
        _register_parsed_matrix_inputs(project_dir, selected, local_matrix)
    else:
        manifest["stages"].append(_stage("real_data_download_parse_align", "skipped", f"source {selected_source or source} requires an adapter-specific importer"))

    compiler = compile_func or compile_registered_analysis_task_packets
    compiled = compiler(project_dir, subquestion_id="sq_v5_real_analysis_main_path")
    manifest["task_packet_ref"] = "v5/task_packets/registered_analysis_task_packets.json"
    manifest["task_packet_count"] = int(compiled.get("packet_count", len(compiled.get("packets", []))))
    manifest["stages"].append(_stage("task_packet_compile", compiled.get("status", "compiled"), f"{manifest['task_packet_count']} packet(s)"))
    if not compiled.get("packets"):
        manifest["status"] = "blocked"
        manifest["recovery"].append(
            {
                "category": "task_packet_compile",
                "severity": "medium",
                "reason": compiled.get("reason", "No analysis packets were compiled."),
                "recovery_actions": ["Check dataset_cards and MethodContract compatibility.", "Confirm matrix and metadata paths exist."],
                "rerun_commands": [f"python tc_lite.py plan --project {project_dir.name}"],
            }
        )
        return _write_manifest(project_dir, manifest)

    executor = execute_func or execute_registered_analysis_task_packets
    execution = executor(project_dir, compiled["packets"], max_packets=max_analysis_packets)
    manifest["execution_bundle_ref"] = "v5/local_execution/local_execution_bundle.json"
    manifest["execution_status"] = execution.get("status", "")
    manifest["task_run_refs"] = _collect_task_run_refs(execution)
    manifest["qc_report_refs"] = _collect_qc_refs(execution)
    manifest["stages"].append(_stage("analysis_qc_evidence_report", execution.get("status", "unknown"), f"{execution.get('completed_count', 0)} completed"))

    report_builder = report_func or build_canonical_report_manifest
    report = report_builder(project_dir)
    manifest["canonical_report_manifest_ref"] = "v5/reports/canonical_report_manifest.json"
    manifest["canonical_report_status"] = report.get("status", "")

    recovery = build_v5_failure_recovery_report(project_dir)
    manifest["failure_recovery_ref"] = "v5/recovery/failure_recovery_report.json"
    manifest["open_recovery_count"] = recovery.get("open_count", 0)
    manifest["recovery"].extend(recovery.get("items", [])[:10])

    try:
        exporter = export_func or _default_exporter
        package_path = exporter(project_dir)
        manifest["export_package"] = _rel(package_path, project_dir)
        manifest["stages"].append(_stage("export_package", "completed", manifest["export_package"]))
    except Exception as exc:
        manifest["stages"].append(_stage("export_package", "failed", str(exc)))
        manifest["recovery"].append(
            {
                "category": "export_package",
                "severity": "low",
                "reason": str(exc),
                "recovery_actions": ["Re-run export after report artifacts exist."],
                "rerun_commands": [f"python tc_lite.py export-package --project {project_dir.name}"],
            }
        )

    manifest["status"] = "completed" if execution.get("status") == "completed" else "review_required"
    return _write_manifest(project_dir, manifest)


def _select_dataset(project_dir: Path, accession: str) -> dict[str, Any]:
    gate_path = project_dir / "v5" / "resource_discovery" / "resource_gate_report.json"
    try:
        gate = build_resource_gate_report(project_dir)
    except Exception:
        gate = _read_json(gate_path, {})
    if accession:
        requested = accession.strip().upper()
        for row in gate.get("gate_items", []):
            if str(row.get("accession", "")).upper() == requested:
                correction = row.get("manual_correction", {}) or {}
                return {
                    "accession": requested,
                    "resource_candidate_id": row.get("resource_candidate_id", ""),
                    "source_database": row.get("source_database", ""),
                    "modality": row.get("modality", ""),
                    "selection_mode": "explicit_accession_with_gate_context",
                    "organism": correction.get("organism", "") or row.get("organism", ""),
                    "tissue": correction.get("tissue", "") or row.get("tissue", ""),
                    "platform": correction.get("platform", "") or row.get("platform", ""),
                    "group_column": correction.get("group_column", ""),
                    "case_label": correction.get("case_label", ""),
                    "control_label": correction.get("control_label", ""),
                    "sample_count": correction.get("sample_count", ""),
                    "can_enter_datasets_locked": row.get("can_enter_datasets_locked", False),
                    "reason": row.get("reason", "User supplied accession."),
                }
        return {"accession": requested, "selection_mode": "explicit_cli", "reason": "User supplied accession."}
    lockable = [row for row in gate.get("gate_items", []) if row.get("can_enter_datasets_locked") and row.get("accession")]
    if lockable:
        row = lockable[0]
        correction = row.get("manual_correction", {}) or {}
        return {
            "accession": str(row.get("accession", "")).upper(),
            "resource_candidate_id": row.get("resource_candidate_id", ""),
            "source_database": row.get("source_database", ""),
            "modality": row.get("modality", ""),
            "selection_mode": "first_dataset_lockable",
            "organism": correction.get("organism", ""),
            "tissue": correction.get("tissue", ""),
            "platform": correction.get("platform", ""),
            "group_column": correction.get("group_column", ""),
            "case_label": correction.get("case_label", ""),
            "control_label": correction.get("control_label", ""),
            "sample_count": correction.get("sample_count", ""),
            "reason": row.get("reason", ""),
        }
    return {
        "accession": "",
        "selection_mode": "none",
        "reason": "No dataset can enter DATASETS_LOCKED. Manual metadata correction is required before real analysis.",
        "gate_ref": "v5/resource_discovery/resource_gate_report.json" if gate else "",
    }


def _route_for_dataset(source_database: str, modality: str, accession: str) -> dict[str, str]:
    accession = str(accession).upper()
    if source_database == "geo" or accession.startswith("GSE"):
        return {
            "download_adapter": "geo_series_matrix",
            "matrix_parser": "geo_series_matrix_to_gene_matrix",
            "analysis_module": "scrna_pseudobulk" if modality in {"single_cell_expression", "scrna", "snrna"} else "bulk_deg",
        }
    if source_database in {"cellxgene", "cz_cellxgene"}:
        return {"download_adapter": "cellxgene_adapter", "matrix_parser": "h5ad_or_cxg_to_pseudobulk", "analysis_module": "scrna_pseudobulk"}
    if source_database == "sra" or accession.startswith("SRR"):
        return {"download_adapter": "sra_adapter_pending", "matrix_parser": "requires_quantification_manifest", "analysis_module": "bulk_deg_or_scrna_after_quantification"}
    return {"download_adapter": source_database or "unknown", "matrix_parser": "unknown", "analysis_module": "registered_method_router"}


def _selected_source(selected: dict[str, Any], requested_source: str) -> str:
    source = str(selected.get("source_database") or "").lower().strip()
    accession = str(selected.get("accession") or "").upper().strip()
    if source:
        return source
    requested = str(requested_source or "").lower().strip()
    if accession.startswith("GSE"):
        return "geo"
    if requested in {"local", "manual", "uploaded", "fixture"}:
        return requested
    return "manual"


def _local_matrix_status(project_dir: Path, selected: dict[str, Any]) -> dict[str, Any]:
    accession = str(selected.get("accession", "")).upper()
    data_dir = project_dir / "data" / accession
    expression = data_dir / "expression_matrix.tsv"
    metadata = data_dir / "metadata.tsv"
    quant_manifest = data_dir / "quantification_manifest.json"
    h5ad_manifest = data_dir / "cellxgene_manifest.json"
    source = str(selected.get("source_database", "")).lower()
    source_requirements = []
    if source == "sra" or accession.startswith("SRR"):
        source_requirements.append("quantification_manifest")
    if source in {"cellxgene", "cz_cellxgene"}:
        source_requirements.append("cellxgene_manifest_or_h5ad_export")
    return {
        "ready": expression.exists() and metadata.exists(),
        "accession": accession,
        "expression_matrix": _rel(expression, project_dir) if expression.exists() else "",
        "metadata": _rel(metadata, project_dir) if metadata.exists() else "",
        "quantification_manifest": _rel(quant_manifest, project_dir) if quant_manifest.exists() else "",
        "cellxgene_manifest": _rel(h5ad_manifest, project_dir) if h5ad_manifest.exists() else "",
        "source_requirements": source_requirements,
        "limitations": [
            "SRA/cellxgene local matrix path assumes upstream quantification/export has already been performed and must be reviewed.",
            "Metadata alignment and QC still gate scientific evidence import.",
        ],
    }


def _register_parsed_matrix_inputs(project_dir: Path, selected: dict[str, Any], local_matrix: dict[str, Any]) -> None:
    for rel_path, artifact_type in [
        (local_matrix.get("expression_matrix", ""), "parsed_expression_matrix"),
        (local_matrix.get("metadata", ""), "parsed_sample_metadata"),
        (local_matrix.get("quantification_manifest", ""), "sra_quantification_manifest"),
        (local_matrix.get("cellxgene_manifest", ""), "cellxgene_export_manifest"),
    ]:
        if not rel_path:
            continue
        register_artifact(
            project_dir,
            rel_path,
            producer="analysis_main_path_input_adapter",
            artifact_type=artifact_type,
            expected_by_task_ids=["v5_analysis_main_path"],
            supports_subquestion_ids=["sq_v5_real_analysis_main_path"],
            producer_run_id=selected.get("accession", ""),
            qc_status="review_required",
            limitations=local_matrix.get("limitations", []),
        )


def _default_geo_importer(*args: Any, **kwargs: Any) -> Any:
    from targetcompass_lite.geo_importer import import_geo_series_auto

    return import_geo_series_auto(*args, **kwargs)


def _default_exporter(project_dir: Path) -> Path:
    from targetcompass_lite.package import export_run_package

    return export_run_package(project_dir)


def _geo_result_to_dict(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, dict):
        return result
    return {"result": str(result)}


def _geo_error_to_dict(exc: Exception) -> dict[str, Any]:
    if hasattr(exc, "to_dict"):
        return exc.to_dict()
    return {"code": type(exc).__name__, "stage": "unknown", "message": str(exc), "retryable": True, "recovery": []}


def _collect_task_run_refs(execution: dict[str, Any]) -> list[str]:
    return [row.get("task_run_ref", "") for row in execution.get("task_results", []) if row.get("task_run_ref")]


def _collect_qc_refs(execution: dict[str, Any]) -> list[str]:
    return [row.get("qc_report_ref", "") for row in execution.get("task_results", []) if row.get("qc_report_ref")]


def _stage(stage: str, status: str, message: str) -> dict[str, str]:
    return {"stage": stage, "status": status, "message": message, "created_at": now_iso()}


def _write_manifest(project_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    rel = "v5/analysis_main_path/main_path_manifest.json"
    write_json_artifact(project_dir, rel, manifest, producer="analysis_main_path", artifact_type="analysis_main_path_manifest")
    try:
        artifact = register_artifact(
            project_dir,
            rel,
            producer="analysis_main_path",
            artifact_type="analysis_main_path_manifest",
            expected_by_task_ids=["v5_analysis_main_path"],
            supports_subquestion_ids=["sq_v5_real_analysis_main_path"],
            producer_run_id=manifest.get("created_at", ""),
            qc_status="pass" if manifest.get("status") in {"completed", "review_required"} else "failed",
            limitations=["Main path manifest is an execution trace; scientific claims still require QC and human review."],
        )
        manifest["artifact_id"] = artifact.get("artifact_id", "")
        write_json_artifact(project_dir, rel, manifest, producer="analysis_main_path", artifact_type="analysis_main_path_manifest")
    except Exception:
        pass
    return manifest


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _rel(path: str | Path, project_dir: Path) -> str:
    p = Path(path)
    try:
        return str(p.relative_to(project_dir)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")
