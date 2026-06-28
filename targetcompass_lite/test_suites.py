import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import ROOT
from .v4 import content_hash


TEST_SUITE_SCHEMA = "v4.test_suite_run/0.1"
PLATFORM_TEST_MATRIX_SCHEMA = "v5.platform_test_matrix/0.1"

QUICK_TESTS = [
    "tests.test_agent",
    "tests.test_annotation",
    "tests.test_consistency",
    "tests.test_database_validation",
    "tests.test_enrichment",
    "tests.test_evidence_db",
    "tests.test_evidence_index",
    "tests.test_fulltext_evidence_levels",
    "tests.test_fulltext_llm_extraction",
    "tests.test_geo_discovery",
    "tests.test_geo_importer",
    "tests.test_i18n",
    "tests.test_ideas_knowledge",
    "tests.test_knowledge_adapters",
    "tests.test_literature_validation",
    "tests.test_llm_parser",
    "tests.test_local_backends",
    "tests.test_matching",
    "tests.test_packaging",
    "tests.test_recovery_cell_type",
    "tests.test_schemas",
    "tests.test_scoring_rules",
    "tests.test_screening",
    "tests.test_secrets",
    "tests.test_spec_builder",
    "tests.test_standard_database_adapters",
    "tests.test_status_ui",
    "tests.test_system_reset",
]

FULL_TESTS = QUICK_TESTS + [
    "tests.test_analysis_extensions",
    "tests.test_codex_engineering",
    "tests.test_delivery",
    "tests.test_deg",
    "tests.test_executor_and_roles",
    "tests.test_gse43292_integration",
    "tests.test_mcp_gateway",
    "tests.test_mcp_server",
    "tests.test_methods",
    "tests.test_nextflow_plane",
    "tests.test_orchestration_graph",
    "tests.test_orchestrator",
    "tests.test_planning",
    "tests.test_production_platform",
    "tests.test_registry_snapshots",
    "tests.test_reporting_evidence_index",
    "tests.test_review_and_run_state",
    "tests.test_services",
    "tests.test_trace_orchestrator",
    "tests.test_v4_manifest",
    "tests.test_webapp",
    "tests.test_work_order_dag",
]

E2E_TESTS = [
    "tests.test_demo_workflow",
    "tests.test_gse43292_integration",
    "tests.test_report_structure",
]

PLATFORM_E2E_QUESTIONS = [
    "Are there SASP-high skeletal muscle background cells with characteristic surface markers in sarcopenia?",
    "Do type 2 diabetes skeletal muscle samples contain secreted or surface-accessible SASP-associated molecules?",
    "Which endothelial markers are associated with vascular aging and SASP activity?",
    "Are senescent stromal cells in fibrotic tissue enriched for targetable surface molecules?",
    "Which immune cell populations express inflammatory SASP ligands in atherosclerosis?",
    "Do adipose tissue cells in obesity show a high SASP score with secreted candidate factors?",
    "Which pancreatic islet cell types show stress-associated cytokine expression in diabetes?",
    "Are osteoarthritis synovial fibroblasts enriched for surface-accessible senescence markers?",
    "Which lung aging epithelial or stromal cells have SASP-high signatures?",
    "Do Alzheimer's disease vascular cells show inflammatory surface marker candidates?",
]

SUITES = {
    "quick": {"modules": QUICK_TESTS, "timeout_seconds": 45, "per_test_timeout_seconds": 20},
    "full": {"modules": FULL_TESTS, "timeout_seconds": 240, "per_test_timeout_seconds": 45},
    "e2e": {"modules": E2E_TESTS, "timeout_seconds": 900, "per_test_timeout_seconds": 420},
}


def run_test_suite(suite: str = "quick", fail_fast: bool = False, timeout_seconds: int | None = None) -> dict[str, Any]:
    if suite not in SUITES:
        raise ValueError(f"unknown test suite: {suite}")
    config = SUITES[suite]
    deadline = time.monotonic() + int(timeout_seconds or config["timeout_seconds"])
    results = []
    status = "PASS"
    for module in config["modules"]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            results.append(_timeout_row(module, "suite timeout reached before module started"))
            status = "TIMEOUT"
            break
        per_timeout = max(1, min(int(config["per_test_timeout_seconds"]), int(remaining)))
        row = _run_module(module, per_timeout)
        results.append(row)
        if row["status"] != "PASS":
            status = "TIMEOUT" if row["status"] == "TIMEOUT" else "FAIL"
            if fail_fast:
                break
    passed = sum(1 for row in results if row["status"] == "PASS")
    failed = sum(1 for row in results if row["status"] == "FAIL")
    timed_out = sum(1 for row in results if row["status"] == "TIMEOUT")
    skipped = len(config["modules"]) - len(results)
    payload = {
        "schema_version": TEST_SUITE_SCHEMA,
        "suite": suite,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "module_count": len(config["modules"]),
        "executed_count": len(results),
        "passed_count": passed,
        "failed_count": failed,
        "timeout_count": timed_out,
        "skipped_due_to_suite_timeout": skipped,
        "timeouts": {
            "suite_seconds": int(timeout_seconds or config["timeout_seconds"]),
            "per_module_seconds": int(config["per_test_timeout_seconds"]),
        },
        "results": results,
        "commands": {
            "quick": "python tc_lite.py test-suite --suite quick",
            "full": "python tc_lite.py test-suite --suite full",
            "e2e": "python tc_lite.py test-suite --suite e2e",
        },
    }
    payload["suite_hash"] = content_hash(payload)
    out = test_suite_report_path(suite)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def list_test_suites() -> dict[str, Any]:
    return {
        "schema_version": "v4.test_suite_manifest/0.1",
        "suites": {
            name: {
                "module_count": len(config["modules"]),
                "timeout_seconds": config["timeout_seconds"],
                "per_test_timeout_seconds": config["per_test_timeout_seconds"],
                "modules": config["modules"],
                "report": str(test_suite_report_path(name).relative_to(ROOT)).replace("\\", "/"),
            }
            for name, config in SUITES.items()
        },
    }


def build_platform_test_matrix(project_dir: str | Path | None = None, *, question_count: int = 10) -> dict[str, Any]:
    count = max(1, min(int(question_count), 50))
    questions = PLATFORM_E2E_QUESTIONS[: min(count, len(PLATFORM_E2E_QUESTIONS))]
    while len(questions) < count:
        questions.append(f"Validation question {len(questions) + 1}: disease tissue SASP surface marker discovery.")
    scenarios = [
        {
            "scenario_id": "real_question_e2e",
            "category": "10-50 research questions",
            "count": len(questions),
            "questions": questions,
            "expected_artifacts": ["project_state.json", "resource_discovery_bundle.json", "task_packets", "QCReport", "Artifact Registry", "product report"],
        },
        {
            "scenario_id": "network_failure",
            "category": "failure recovery",
            "fault": "GEO/PubMed/Europe PMC request timeout or empty result",
            "expected_behavior": "query is relaxed or failure is recorded with retry guidance; no fake verified dataset is produced",
        },
        {
            "scenario_id": "llm_failure",
            "category": "failure recovery",
            "fault": "DeepSeek/OpenAI-compatible API key missing, timeout, malformed JSON, or schema mismatch",
            "expected_behavior": "role run records retry/fallback/audit; downstream stage does not silently approve free text",
        },
        {
            "scenario_id": "metadata_missing",
            "category": "dataset gate",
            "fault": "metadata lacks group column, sample size, tissue, organism, or platform mapping",
            "expected_behavior": "candidate stays analysis_ready_after_review; UI allows manual correction before DATASETS_LOCKED",
        },
        {
            "scenario_id": "docker_nextflow_missing",
            "category": "runtime dependency",
            "fault": "Docker daemon or Nextflow is not available",
            "expected_behavior": "v5-doctor/service manager reports WARN/BLOCKED with repair commands; local control-plane still starts",
        },
        {
            "scenario_id": "report_acceptance",
            "category": "report QA",
            "checks": ["candidate ranking", "evidence chain", "failed items", "limitations", "experiment suggestions", "claim ceiling alignment"],
            "expected_behavior": "report manifest references EvidencePlan, ArtifactManifest, QCReport, and QuestionAlignmentReport",
        },
    ]
    payload = {
        "schema_version": PLATFORM_TEST_MATRIX_SCHEMA,
        "question_count": len(questions),
        "suites": list_test_suites()["suites"],
        "scenarios": scenarios,
        "recommended_order": ["quick", "full", "real_question_e2e", "fault_injection", "e2e", "package_acceptance"],
        "commands": {
            "quick": "python tc_lite.py test-suite --suite quick",
            "full": "python tc_lite.py test-suite --suite full",
            "e2e": "python tc_lite.py test-suite --suite e2e",
            "matrix": "python tc_lite.py v5-test-matrix --project <project> --question-count 10",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if project_dir is not None:
        out = Path(project_dir) / "v5" / "platform" / "platform_test_matrix.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def test_suite_report_path(suite: str) -> Path:
    return ROOT / "results" / "test_suites" / f"{suite}_test_suite_report.json"


def _run_module(module: str, timeout_seconds: int) -> dict[str, Any]:
    start = time.monotonic()
    command = [sys.executable, "-m", "unittest", module, "-v"]
    try:
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds)
        status = "PASS" if completed.returncode == 0 else "FAIL"
        stdout = completed.stdout
        stderr = completed.stderr
        returncode = completed.returncode
        failure_reason = "" if status == "PASS" else _tail(stderr or stdout)
    except subprocess.TimeoutExpired as exc:
        status = "TIMEOUT"
        stdout = _decode(exc.stdout)
        stderr = _decode(exc.stderr)
        returncode = None
        failure_reason = f"module exceeded {timeout_seconds}s timeout"
    duration = round(time.monotonic() - start, 3)
    return {
        "module": module,
        "status": status,
        "duration_seconds": duration,
        "timeout_seconds": timeout_seconds,
        "returncode": returncode,
        "failure_reason": failure_reason,
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "command": " ".join(command),
    }


def _timeout_row(module: str, reason: str) -> dict[str, Any]:
    return {
        "module": module,
        "status": "TIMEOUT",
        "duration_seconds": 0,
        "timeout_seconds": 0,
        "returncode": None,
        "failure_reason": reason,
        "stdout_tail": "",
        "stderr_tail": "",
        "command": "",
    }


def _tail(text: str, limit: int = 4000) -> str:
    text = text or ""
    return text[-limit:]


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)
