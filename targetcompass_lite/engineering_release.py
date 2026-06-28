import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .codex_engineering import load_codex_engineering
from .engineering_closure import refresh_engineering_closure
from .review import load_reviews
from .v4 import content_hash, file_hash, v4_dir


ENGINEERING_RELEASE_SCHEMA = "v4.codex_engineering_release_gate/0.1"


def build_engineering_release_gate(project_dir: Path) -> dict[str, Any]:
    engineering = load_codex_engineering(project_dir)
    closure = refresh_engineering_closure(project_dir)
    reviews = load_reviews(project_dir)
    items = [_release_item(project_dir, result, engineering, reviews) for result in engineering.get("results", [])]
    blocked = [row for row in items if row["gate_status"] == "BLOCKED"]
    ready = [row for row in items if row["gate_status"] == "READY_TO_MERGE"]
    payload = {
        "schema_version": ENGINEERING_RELEASE_SCHEMA,
        "project_id": project_dir.name,
        "status": "BLOCKED" if blocked else ("READY" if ready else "NO_RESULTS"),
        "policy": {
            "patch_required": True,
            "tests_must_pass": True,
            "human_review_required": True,
            "merge_requires_approved_for_merge": True,
            "ci_contract_required": True,
            "sbom_contract_required": True,
        },
        "summary": {
            "result_count": len(items),
            "ready_to_merge_count": len(ready),
            "blocked_count": len(blocked),
            "closure_ref": "v4/codex_engineering/engineering_closure.json",
            "evidence_snapshot_hash": closure.get("evidence_snapshot_hash", ""),
        },
        "ci_contract": {
            "required_commands": ["python -m unittest"],
            "allowed_test_prefixes": ["python -m unittest", "py -m unittest"],
            "status": "contract_only",
            "production_gap": "No external CI runner is attached in this local environment.",
        },
        "sbom_contract": {
            "required_artifact": "v4/codex_engineering/sbom_manifest.json",
            "status": "contract_only",
            "production_gap": "No SBOM generator has been executed in this local environment.",
        },
        "items": items,
        "generated_at": _now(),
    }
    payload["release_gate_hash"] = content_hash(payload)
    path = engineering_release_gate_path(project_dir)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def engineering_release_gate_path(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "codex_engineering" / "release_gate.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _release_item(project_dir: Path, result: dict[str, Any], engineering: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    job_id = result.get("codex_job_id", "")
    patch_refs = set(result.get("patch_refs", []))
    test_refs = set(result.get("test_refs", []))
    patches = [
        row
        for row in engineering.get("patches", [])
        if row.get("patch_id") in patch_refs or (not patch_refs and row.get("codex_job_id") == job_id)
    ]
    tests = [
        row
        for row in engineering.get("tests", [])
        if row.get("test_id") in test_refs or (not test_refs and row.get("codex_job_id") == job_id)
    ]
    review = next((row for row in reversed(reviews) if row.get("item_type") == "codex_result" and row.get("item_id") == result.get("result_id")), {})
    checks = [
        _check("result_success", result.get("status") == "success", "Codex result succeeded.", "Result must succeed before merge."),
        _check("patch_registered", bool(patches), "Patch registry has at least one patch.", "Register a patch before merge.", severity="warning"),
        _check("tests_passed", bool(tests) and all(row.get("status") == "passed" for row in tests), "All registered tests passed.", "Run allowed tests and fix failures."),
        _check("human_approved", result.get("merge_status") == "approved_for_merge" and review.get("action") == "approve", "Human review approved the result.", "Approve the Codex result with a reason."),
        _check("artifacts_exist", _artifacts_exist(project_dir, result.get("artifacts", [])), "Result artifacts are present or external refs.", "Produce or register missing artifacts.", severity="warning"),
    ]
    blocked = [row for row in checks if row["status"] == "FAIL"]
    return {
        "result_id": result.get("result_id", ""),
        "codex_job_id": job_id,
        "work_order_id": result.get("work_order_id", ""),
        "gate_status": "BLOCKED" if blocked else "READY_TO_MERGE",
        "merge_status": result.get("merge_status", ""),
        "review_status": result.get("review_status", ""),
        "review_reason": review.get("reason", result.get("review_reason", "")),
        "patch_count": len(patches),
        "test_count": len(tests),
        "artifact_count": len(result.get("artifacts", [])),
        "patch_hashes": [row.get("patch_hash", "") for row in patches],
        "test_statuses": [row.get("status", "") for row in tests],
        "checks": checks,
    }


def _check(check_id: str, passed: bool, ok: str, remediation: str, severity: str = "error") -> dict[str, str]:
    return {
        "check_id": check_id,
        "status": "PASS" if passed else ("WARN" if severity == "warning" else "FAIL"),
        "severity": severity,
        "message": ok if passed else remediation,
        "remediation": "" if passed else remediation,
    }


def _artifacts_exist(project_dir: Path, artifacts: list[str]) -> bool:
    if not artifacts:
        return False
    for rel in artifacts:
        if "://" in rel:
            continue
        if not (project_dir / rel).exists():
            return False
    return True


def build_sbom_manifest(project_dir: Path) -> dict[str, Any]:
    files = []
    for rel in ["pyproject.toml", "requirements.txt"]:
        path = Path.cwd() / rel
        if path.exists():
            files.append({"path": rel, "hash": file_hash(path), "bytes": path.stat().st_size})
    payload = {
        "schema_version": "v4.sbom_manifest/0.1",
        "project_id": project_dir.name,
        "status": "contract_only",
        "files": files,
        "production_gap": "Replace this manifest with a CycloneDX/SPDX SBOM generated by CI.",
        "generated_at": _now(),
    }
    payload["sbom_hash"] = content_hash(payload)
    path = v4_dir(project_dir) / "codex_engineering" / "sbom_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
