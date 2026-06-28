from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .test_suites import build_platform_test_matrix, test_suite_report_path
from .platform_config import write_pre_release_scripts


RELEASE_ACCEPTANCE_SCHEMA = "v5.release_acceptance/0.1"


def build_release_acceptance_manifest(project_dir: str | Path, *, question_count: int = 50) -> dict[str, Any]:
    project_dir = Path(project_dir)
    matrix = build_platform_test_matrix(project_dir, question_count=question_count)
    quick = _load_test_report("quick")
    full = _load_test_report("full")
    e2e = _load_test_report("e2e")
    validation = _latest_validation(project_dir, minimum_questions=question_count)
    installer = _read_json(project_dir / "v5" / "platform" / "production_readiness.json", {})
    main_path = _read_json(project_dir / "v5" / "analysis_main_path" / "main_path_manifest.json", {})
    resource_gate = _read_json(project_dir / "v5" / "resource_discovery" / "resource_gate_report.json", {})
    matrix_validation = _read_json(project_dir / "v5" / "platform" / "matrix_path_validation.json", {})
    scripts = write_pre_release_scripts(project_dir, question_count=question_count)
    data_matrix = build_real_data_main_path_validation_matrix(project_dir, main_path=main_path, resource_gate=resource_gate, matrix_validation=matrix_validation)
    checks = [
        _check("quick_regression", quick.get("status") == "PASS", _report_ref("quick"), "Run python tc_lite.py test-suite --suite quick."),
        _check("full_regression", full.get("status") == "PASS", _report_ref("full"), "Run python tc_lite.py test-suite --suite full and triage failures."),
        _check("e2e_regression", e2e.get("status") == "PASS", _report_ref("e2e"), "Run python tc_lite.py test-suite --suite e2e after unit tests are stable."),
        _check(
            "real_question_longrun",
            validation.get("status") == "PASS" and validation.get("question_count", 0) >= question_count,
            validation.get("summary_ref", ""),
            f"Run python tc_lite.py v5-real-question-validation --project {project_dir.name} --question-count {question_count} --isolated-projects.",
        ),
        _check(
            "clean_windows_installer_smoke",
            _production_check_status(installer, "windows_gui_installer_release") == "PASS",
            "v5/platform/production_readiness.json",
            "Run clean Windows/VM install-start-stop-restart-uninstall smoke and record v5/packaging/clean_machine_smoke.json.",
        ),
        _check(
            "real_data_main_path",
            _real_data_main_path_ready(main_path),
            "v5/analysis_main_path/main_path_manifest.json",
            "Run additional locked SRA/cellxgene candidates through matrix parse, metadata alignment, analysis, QC, Evidence, and report.",
            details={**_real_data_main_path_details(main_path, resource_gate), "validation_matrix": data_matrix},
        ),
    ]
    payload = {
        "schema_version": RELEASE_ACCEPTANCE_SCHEMA,
        "project_id": project_dir.name,
        "status": "PASS" if all(row["status"] == "PASS" for row in checks) else "REVIEW",
        "checks": checks,
        "test_matrix_ref": "v5/platform/platform_test_matrix.json",
        "test_matrix": matrix,
        "commands": {
            "quick": "python tc_lite.py test-suite --suite quick",
            "full": "python tc_lite.py test-suite --suite full",
            "e2e": "python tc_lite.py test-suite --suite e2e",
            "ten_questions": f"python tc_lite.py v5-real-question-validation --project {project_dir.name} --question-count 10 --isolated-projects",
            "fifty_questions": f"python tc_lite.py v5-real-question-validation --project {project_dir.name} --question-count 50 --isolated-projects",
            "doctor": f"python tc_lite.py v5-doctor --project {project_dir.name}",
            "production_acceptance": f"python tc_lite.py v5-production-acceptance --project {project_dir.name} --target all",
            "pre_release_script": scripts.get("commands", [""])[0],
        },
        "pre_release_scripts_ref": "v5/platform/pre_release_scripts.json",
        "real_data_validation_matrix": data_matrix,
        "generated_at": _now(),
    }
    out = project_dir / "v5" / "platform" / "release_acceptance.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def build_real_data_main_path_validation_matrix(
    project_dir: str | Path,
    *,
    main_path: dict[str, Any] | None = None,
    resource_gate: dict[str, Any] | None = None,
    matrix_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    main_path = main_path if main_path is not None else _read_json(project_dir / "v5" / "analysis_main_path" / "main_path_manifest.json", {})
    resource_gate = resource_gate if resource_gate is not None else _read_json(project_dir / "v5" / "resource_discovery" / "resource_gate_report.json", {})
    matrix_validation = matrix_validation if matrix_validation is not None else _read_json(project_dir / "v5" / "platform" / "matrix_path_validation.json", {})
    source = str(main_path.get("source", "")).lower()
    accession = str(main_path.get("geo_import", {}).get("accession") or main_path.get("selected_dataset", {}).get("accession", "")).upper()
    geo_pass = _real_data_main_path_ready(main_path) and (source == "geo" or accession.startswith("GSE"))
    rows = [
        {
            "source": "GEO",
            "status": "PASS" if geo_pass else "REVIEW",
            "required_path": "GEO/GSE -> series matrix download -> matrix parse -> metadata alignment -> analysis -> report",
            "evidence_ref": "v5/analysis_main_path/main_path_manifest.json" if geo_pass else "",
            "latest_accession": accession if geo_pass else "",
            "next_step": "" if geo_pass else "Run a lockable GSE through v5-analysis-main-path.",
        },
        {
            "source": "SRA",
            "status": _validated_matrix_status(matrix_validation, "sra"),
            "required_path": "SRA/SRR -> download or attach quantification manifest -> matrix parse -> metadata alignment -> analysis -> report",
            "evidence_ref": "v5/platform/matrix_path_validation.json",
            "latest_accession": _latest_matrix_accession(matrix_validation, "sra") or _latest_gate_accession(resource_gate, "sra"),
            "next_step": "" if _validated_matrix_status(matrix_validation, "sra") == "PASS" else "Run v5-matrix-path-validation after a real SRA matrix route; do not mark PASS from metadata only.",
        },
        {
            "source": "cellxgene",
            "status": _validated_matrix_status(matrix_validation, "cellxgene"),
            "required_path": "cellxgene/h5ad -> cell metadata parse -> pseudobulk or cell-type route -> QC -> report",
            "evidence_ref": "v5/platform/matrix_path_validation.json",
            "latest_accession": _latest_matrix_accession(matrix_validation, "cellxgene") or _latest_gate_accession(resource_gate, "cellxgene"),
            "next_step": "" if _validated_matrix_status(matrix_validation, "cellxgene") == "PASS" else "Run v5-matrix-path-validation after a real h5ad/cellxgene route; do not mark PASS from metadata only.",
        },
    ]
    payload = {
        "schema_version": "v5.real_data_main_path_validation_matrix/0.1",
        "project_id": project_dir.name,
        "status": "PASS" if all(row["status"] == "PASS" for row in rows) else "REVIEW",
        "rows": rows,
        "policy": "A source passes only after matrix parse, metadata alignment, analysis/QC, evidence/report refs are recorded.",
        "generated_at": _now(),
    }
    out = project_dir / "v5" / "platform" / "real_data_main_path_validation_matrix.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _load_test_report(suite: str) -> dict[str, Any]:
    return _read_json(test_suite_report_path(suite), {})


def _report_ref(suite: str) -> str:
    path = test_suite_report_path(suite)
    try:
        return str(path.relative_to(Path(__file__).resolve().parents[1])).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _latest_validation(project_dir: Path, *, minimum_questions: int) -> dict[str, Any]:
    root = project_dir / "v5" / "validation"
    candidates = []
    if root.exists():
        for path in root.glob("*/summary.json"):
            data = _read_json(path, {})
            if data.get("question_count", 0) >= minimum_questions:
                data["summary_ref"] = str(path.relative_to(project_dir)).replace("\\", "/")
                candidates.append(data)
    if not candidates:
        return {}
    return sorted(candidates, key=lambda row: row.get("created_at", ""))[-1]


def _production_check_status(manifest: dict[str, Any], check_id: str) -> str:
    for row in manifest.get("checks", []):
        if row.get("check_id") == check_id:
            return row.get("status", "")
    return ""


def _real_data_main_path_ready(main_path: dict[str, Any]) -> bool:
    if not main_path:
        return False
    status = str(main_path.get("status", "")).lower()
    if status not in {"completed", "pass", "ready"}:
        return False
    stages = {row.get("stage"): row.get("status") for row in main_path.get("stages", [])}
    if stages.get("real_data_download_parse_align") != "completed":
        return False
    if stages.get("analysis_qc_evidence_report") != "completed":
        return False
    geo = main_path.get("geo_import", {})
    if not geo.get("expression_matrix") or not geo.get("metadata"):
        return False
    if not main_path.get("task_run_refs") or not main_path.get("qc_report_refs"):
        return False
    return True


def _real_data_main_path_details(main_path: dict[str, Any], resource_gate: dict[str, Any]) -> dict[str, Any]:
    geo = main_path.get("geo_import", {}) if isinstance(main_path, dict) else {}
    return {
        "last_completed_source": main_path.get("source", ""),
        "last_completed_accession": geo.get("accession", main_path.get("selected_dataset", {}).get("accession", "")),
        "last_completed_samples": geo.get("samples", 0),
        "last_completed_genes": geo.get("genes", 0),
        "task_run_count": len(main_path.get("task_run_refs", [])),
        "qc_report_count": len(main_path.get("qc_report_refs", [])),
        "current_resource_gate_lockable_count": resource_gate.get("datasets_lockable_count", 0),
        "current_resource_gate_matrix_parse_ready_count": resource_gate.get("matrix_parse_ready_count", 0),
        "expansion_note": "GEO path has completed if status is PASS; SRA/cellxgene still require additional production validation.",
    }


def _adapter_matrix_status(resource_gate: dict[str, Any], source: str) -> str:
    source = source.lower()
    for row in resource_gate.get("gate_items", []):
        row_source = str(row.get("source_database", "")).lower()
        if source == "cellxgene" and row_source in {"cellxgene", "cz_cellxgene"} and row.get("matrix_parse_ready"):
            return "REVIEW"
        if source == "sra" and row_source == "sra" and row.get("matrix_parse_ready"):
            return "REVIEW"
    return "REVIEW"


def _validated_matrix_status(matrix_validation: dict[str, Any], source: str) -> str:
    for row in matrix_validation.get("rows", []):
        row_source = str(row.get("source_database", "")).lower()
        if source == "cellxgene" and row_source in {"cellxgene", "cz_cellxgene"} and row.get("status") == "PASS":
            return "PASS"
        if source == "sra" and row_source == "sra" and row.get("status") == "PASS":
            return "PASS"
    return "REVIEW"


def _latest_matrix_accession(matrix_validation: dict[str, Any], source: str) -> str:
    for row in reversed(matrix_validation.get("rows", [])):
        row_source = str(row.get("source_database", "")).lower()
        if source == "cellxgene" and row_source in {"cellxgene", "cz_cellxgene"}:
            return str(row.get("accession", ""))
        if source == "sra" and row_source == "sra":
            return str(row.get("accession", ""))
    return ""


def _latest_gate_accession(resource_gate: dict[str, Any], source: str) -> str:
    source = source.lower()
    for row in resource_gate.get("gate_items", []):
        row_source = str(row.get("source_database", "")).lower()
        if source == "cellxgene" and row_source in {"cellxgene", "cz_cellxgene"}:
            return str(row.get("accession", ""))
        if source == "sra" and row_source == "sra":
            return str(row.get("accession", ""))
    return ""


def _check(check_id: str, ok: bool, ref: str, recovery: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "check_id": check_id,
        "status": "PASS" if ok else "REVIEW",
        "ref": ref,
        "recovery": "" if ok else recovery,
    }
    if details:
        row["details"] = details
    return row


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return fallback


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
