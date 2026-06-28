import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .v4 import content_hash, file_hash, load_codex_task_packet, load_v4_work_orders, save_codex_task_packet, save_v4_work_order, v4_dir


def engineering_dir(project_dir: Path) -> Path:
    path = v4_dir(project_dir) / "codex_engineering"
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_root(project_dir: Path) -> Path:
    path = engineering_dir(project_dir) / "workspaces"
    path.mkdir(parents=True, exist_ok=True)
    return path


def patch_registry_path(project_dir: Path) -> Path:
    return engineering_dir(project_dir) / "patch_registry.json"


def test_registry_path(project_dir: Path) -> Path:
    return engineering_dir(project_dir) / "test_registry.json"


def result_registry_path(project_dir: Path) -> Path:
    return engineering_dir(project_dir) / "result_registry.json"


def merge_registry_path(project_dir: Path) -> Path:
    return engineering_dir(project_dir) / "merge_registry.json"


def workspace_registry_path(project_dir: Path) -> Path:
    return engineering_dir(project_dir) / "workspace_registry.json"


def git_worktree_root(project_dir: Path) -> Path:
    path = engineering_dir(project_dir) / "git_worktrees"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_isolated_workspace(project_dir: Path, work_order_id: str, actor: str = "codex") -> dict[str, Any]:
    order = _find_work_order(project_dir, work_order_id)
    packet = load_codex_task_packet(project_dir, order)
    if not packet:
        raise ValueError(f"work order has no Codex task packet: {work_order_id}")
    job_id = packet["codex_job_id"]
    path = workspace_root(project_dir) / job_id
    path.mkdir(parents=True, exist_ok=True)
    (path / "task_packet.json").write_text(json.dumps(packet, indent=2, ensure_ascii=False), encoding="utf-8")
    copied = _copy_allowed_inputs(project_dir, path, packet.get("allowed_paths", []))
    manifest = {
        "schema_version": "v4.codex_workspace/0.1",
        "workspace_id": "cws_" + content_hash({"job": job_id, "work_order": work_order_id})[:16],
        "codex_job_id": job_id,
        "work_order_id": work_order_id,
        "project_id": project_dir.name,
        "workspace_path": str(path.relative_to(project_dir)),
        "baseline_commit": packet.get("baseline_commit", ""),
        "allowed_paths": packet.get("allowed_paths", []),
        "copied_inputs": copied,
        "status": "prepared",
        "created_by": actor,
        "created_at": _now(),
    }
    (path / "workspace_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    registry = _read_registry(workspace_registry_path(project_dir), "v4.codex_workspace_registry/0.1", "workspaces")
    _upsert(registry["workspaces"], manifest, "codex_job_id")
    _write_registry(workspace_registry_path(project_dir), registry)
    packet["workspace_ref"] = str((path / "workspace_manifest.json").relative_to(project_dir))
    packet["execution_status"] = "workspace_prepared"
    save_codex_task_packet(project_dir, order, packet)
    order["engineering_status"] = "workspace_prepared"
    order["codex_workspace_ref"] = packet["workspace_ref"]
    save_v4_work_order(project_dir, order)
    return manifest


def prepare_git_worktree(project_dir: Path, codex_job_id: str, actor: str = "codex", allow_unapproved_dispatch: bool = False) -> dict[str, Any]:
    found = _find_order_and_packet(project_dir, codex_job_id)
    packet = found["packet"]
    if packet.get("release_gate") != "approved_for_codex_worker" and not allow_unapproved_dispatch:
        raise ValueError("Codex task must be approved before preparing a git worktree")
    repo_root = _repo_root()
    target = git_worktree_root(project_dir) / codex_job_id
    branch = f"codex/task-{codex_job_id}-{content_hash({'project': str(project_dir.resolve())})[:8]}"
    if not target.exists():
        _run_git(repo_root, ["worktree", "add", "-B", branch, str(target)])
    manifest = create_isolated_workspace(project_dir, found["order"]["work_order_id"], actor=actor)
    manifest["schema_version"] = "v4.codex_git_worktree/0.1"
    manifest["git_worktree_path"] = str(target)
    manifest["git_branch"] = branch
    manifest["repo_root"] = str(repo_root)
    manifest["status"] = "git_worktree_prepared"
    worktree_manifest = workspace_root(project_dir) / codex_job_id / "git_worktree_manifest.json"
    worktree_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    _update_workspace(project_dir, codex_job_id, {"status": "git_worktree_prepared", "git_worktree_path": str(target), "git_branch": branch})
    found["order"]["engineering_status"] = "git_worktree_prepared"
    found["order"]["codex_git_worktree_ref"] = str(worktree_manifest.relative_to(project_dir))
    packet["execution_status"] = "git_worktree_prepared"
    packet["git_worktree_ref"] = found["order"]["codex_git_worktree_ref"]
    save_codex_task_packet(project_dir, found["order"], packet)
    save_v4_work_order(project_dir, found["order"])
    return manifest


def run_codex_task_tests(project_dir: Path, codex_job_id: str, actor: str = "codex", allow_unapproved_dispatch: bool = False) -> dict[str, Any]:
    found = _find_order_and_packet(project_dir, codex_job_id)
    packet = found["packet"]
    workspace = _workspace_for_job(project_dir, codex_job_id)
    worktree_path = Path(workspace.get("git_worktree_path", ""))
    if not worktree_path.exists():
        worktree = prepare_git_worktree(project_dir, codex_job_id, actor=actor, allow_unapproved_dispatch=allow_unapproved_dispatch)
        worktree_path = Path(worktree["git_worktree_path"])
    test_results = []
    artifacts = []
    failure_reason = ""
    for command in packet.get("tests", []):
        if not _allowed_test_command(command):
            test_results.append(
                register_codex_test_result(project_dir, codex_job_id, command, "skipped", stderr_ref="command rejected by allowlist", actor=actor)
            )
            failure_reason = f"test command rejected by allowlist: {command}"
            continue
        started = time.time()
        completed = subprocess.run(command, cwd=worktree_path, shell=True, text=True, capture_output=True, timeout=300)
        out_dir = workspace_root(project_dir) / codex_job_id / "test_logs"
        out_dir.mkdir(parents=True, exist_ok=True)
        log_seed = content_hash({"job": codex_job_id, "command": command, "time": _now()})[:12]
        stdout_path = out_dir / f"{log_seed}_stdout.txt"
        stderr_path = out_dir / f"{log_seed}_stderr.txt"
        stdout_path.write_text(completed.stdout, encoding="utf-8", errors="replace")
        stderr_path.write_text(completed.stderr, encoding="utf-8", errors="replace")
        status = "passed" if completed.returncode == 0 else "failed"
        if status == "failed":
            failure_reason = f"test failed: {command}"
        test_results.append(
            register_codex_test_result(
                project_dir,
                codex_job_id,
                command,
                status,
                stdout_ref=str(stdout_path.relative_to(project_dir)),
                stderr_ref=str(stderr_path.relative_to(project_dir)),
                duration_seconds=round(time.time() - started, 3),
                actor=actor,
            )
        )
        artifacts.extend([str(stdout_path.relative_to(project_dir)), str(stderr_path.relative_to(project_dir))])
    patch_refs = []
    if test_results and all(row["status"] == "passed" for row in test_results):
        patch = collect_codex_worktree_patch(project_dir, codex_job_id, actor=actor)
        if patch:
            patch_refs.append(patch["patch_id"])
            artifacts.append(patch["patch_path"])
    final_status = "success" if test_results and all(row["status"] == "passed" for row in test_results) else "failed"
    result = record_codex_result(project_dir, codex_job_id, final_status, artifacts=artifacts, failure_reason=failure_reason, actor=actor)
    if patch_refs:
        result["patch_refs"] = sorted(set(result.get("patch_refs", []) + patch_refs))
        registry = _read_registry(result_registry_path(project_dir), "v4.codex_result_registry/0.1", "results")
        _upsert(registry["results"], result, "result_id")
        _write_registry(result_registry_path(project_dir), registry)
    result["test_results"] = test_results
    return result


def collect_codex_worktree_patch(project_dir: Path, codex_job_id: str, actor: str = "codex") -> dict[str, Any]:
    workspace = _workspace_for_job(project_dir, codex_job_id)
    worktree_path = Path(workspace.get("git_worktree_path", ""))
    if not worktree_path.exists():
        return {}
    completed = subprocess.run(["git", "diff", "--binary"], cwd=worktree_path, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    diff_text = completed.stdout
    if not diff_text.strip():
        return {}
    out_dir = workspace_root(project_dir) / codex_job_id
    out_dir.mkdir(parents=True, exist_ok=True)
    patch_path = out_dir / "generated_changes.patch"
    patch_path.write_text(diff_text, encoding="utf-8")
    rel_patch = str(patch_path.relative_to(project_dir)).replace("\\", "/")
    return register_codex_patch(project_dir, codex_job_id, rel_patch, summary="Generated from isolated Codex git worktree diff.", actor=actor)


def register_codex_patch(project_dir: Path, codex_job_id: str, patch_path: str, summary: str = "", actor: str = "codex") -> dict[str, Any]:
    path = _resolve_project_file(project_dir, patch_path)
    result = _find_order_and_packet(project_dir, codex_job_id)
    patch = {
        "schema_version": "v4.codex_patch/0.1",
        "patch_id": "cpatch_" + content_hash({"job": codex_job_id, "path": patch_path, "hash": file_hash(path)})[:16],
        "codex_job_id": codex_job_id,
        "work_order_id": result["order"]["work_order_id"],
        "patch_path": patch_path,
        "patch_hash": file_hash(path),
        "summary": summary.strip(),
        "created_by": actor,
        "created_at": _now(),
    }
    registry = _read_registry(patch_registry_path(project_dir), "v4.codex_patch_registry/0.1", "patches")
    _upsert(registry["patches"], patch, "patch_id")
    _write_registry(patch_registry_path(project_dir), registry)
    return patch


def register_codex_test_result(
    project_dir: Path,
    codex_job_id: str,
    command: str,
    status: str,
    stdout_ref: str = "",
    stderr_ref: str = "",
    duration_seconds: float | None = None,
    actor: str = "codex",
) -> dict[str, Any]:
    if status not in {"passed", "failed", "skipped"}:
        raise ValueError(f"unsupported test status: {status}")
    result = _find_order_and_packet(project_dir, codex_job_id)
    test = {
        "schema_version": "v4.codex_test_result/0.1",
        "test_id": "ctest_" + content_hash({"job": codex_job_id, "command": command, "time": _now()})[:16],
        "codex_job_id": codex_job_id,
        "work_order_id": result["order"]["work_order_id"],
        "command": command,
        "status": status,
        "stdout_ref": stdout_ref,
        "stderr_ref": stderr_ref,
        "duration_seconds": duration_seconds,
        "recorded_by": actor,
        "recorded_at": _now(),
    }
    registry = _read_registry(test_registry_path(project_dir), "v4.codex_test_registry/0.1", "tests")
    registry["tests"].append(test)
    _write_registry(test_registry_path(project_dir), registry)
    return test


def record_codex_result(
    project_dir: Path,
    codex_job_id: str,
    status: str,
    artifacts: list[str] | None = None,
    failure_reason: str = "",
    actor: str = "codex",
) -> dict[str, Any]:
    if status not in {"success", "failed", "cancelled", "needs_review"}:
        raise ValueError(f"unsupported Codex result status: {status}")
    found = _find_order_and_packet(project_dir, codex_job_id)
    engineering = load_codex_engineering(project_dir)
    patches = [row for row in engineering.get("patches", []) if row.get("codex_job_id") == codex_job_id]
    tests = [row for row in engineering.get("tests", []) if row.get("codex_job_id") == codex_job_id]
    artifact_set = set(artifacts or [])
    if artifact_set:
        scoped_tests = [
            row
            for row in tests
            if row.get("stdout_ref") in artifact_set or row.get("stderr_ref") in artifact_set
        ]
        if scoped_tests:
            tests = scoped_tests
    merge_status = "pending_human_approval" if status == "success" else "blocked"
    result = {
        "schema_version": "v4.codex_execution_result/0.1",
        "result_id": "cresult_" + content_hash({"job": codex_job_id, "status": status, "time": _now()})[:16],
        "codex_job_id": codex_job_id,
        "work_order_id": found["order"]["work_order_id"],
        "status": status,
        "merge_status": merge_status,
        "patch_refs": [row["patch_id"] for row in patches],
        "test_refs": [row["test_id"] for row in tests],
        "artifacts": artifacts or [],
        "failure_reason": failure_reason,
        "review_status": "pending" if status == "success" else "needs_review",
        "evidence_ref": "v4/evidence_snapshot.json",
        "work_order_ref": found["order"].get("work_order_id", ""),
        "recorded_by": actor,
        "recorded_at": _now(),
    }
    registry = _read_registry(result_registry_path(project_dir), "v4.codex_result_registry/0.1", "results")
    _upsert(registry["results"], result, "result_id")
    _write_registry(result_registry_path(project_dir), registry)

    order = found["order"]
    packet = found["packet"]
    order["engineering_status"] = "result_recorded"
    order["codex_result_status"] = status
    order["codex_result_ref"] = f"v4/codex_engineering/result_registry.json#{result['result_id']}"
    order["status"] = "engineering_review_required" if status == "success" else "engineering_failed"
    packet["execution_status"] = status
    packet["result_ref"] = order["codex_result_ref"]
    packet["merge_status"] = merge_status
    save_codex_task_packet(project_dir, order, packet)
    save_v4_work_order(project_dir, order)
    try:
        from .engineering_closure import record_engineering_attempt_update, refresh_engineering_closure

        record_engineering_attempt_update(project_dir, result)
        closure = refresh_engineering_closure(project_dir)
        result["engineering_closure_ref"] = "v4/codex_engineering/engineering_closure.json"
        result["evidence_snapshot_hash"] = closure.get("evidence_snapshot_hash", "")
        _write_registry(result_registry_path(project_dir), registry)
    except Exception as exc:
        result["engineering_closure_error"] = str(exc)
        _write_registry(result_registry_path(project_dir), registry)
    return result


def apply_approved_codex_result(project_dir: Path, result_id: str, actor: str = "human", dry_run: bool = False) -> dict[str, Any]:
    from .engineering_release import build_engineering_release_gate

    engineering = load_codex_engineering(project_dir)
    result = next((row for row in engineering["results"] if row.get("result_id") == result_id), None)
    if not result:
        raise ValueError(f"Codex result not found: {result_id}")
    if result.get("status") != "success":
        raise ValueError("only successful Codex results can be merged")
    if result.get("merge_status") != "approved_for_merge":
        raise ValueError("Codex result must be approved_for_merge before merge")
    gate = build_engineering_release_gate(project_dir)
    gate_item = next((row for row in gate.get("items", []) if row.get("result_id") == result_id), {})
    if gate_item.get("gate_status") != "READY_TO_MERGE":
        raise ValueError("release gate is not READY_TO_MERGE")
    patch_ids = set(result.get("patch_refs", []))
    patches = [
        row
        for row in engineering.get("patches", [])
        if row.get("patch_id") in patch_ids or (not patch_ids and row.get("codex_job_id") == result.get("codex_job_id"))
    ]
    if not patches:
        raise ValueError("no registered patch is available for merge")
    repo_root = _repo_root()
    resolved_patches = []
    for patch in patches:
        patch_path = _resolve_project_file(project_dir, patch.get("patch_path", ""))
        _run_git(repo_root, ["apply", "--check", str(patch_path)])
        resolved_patches.append((patch, patch_path))
    applied = []
    for patch, patch_path in resolved_patches:
        if not dry_run:
            _run_git(repo_root, ["apply", str(patch_path)])
        applied.append(
            {
                "patch_id": patch.get("patch_id", ""),
                "patch_path": patch.get("patch_path", ""),
                "patch_hash": patch.get("patch_hash", ""),
            }
        )
    merge = {
        "schema_version": "v4.codex_merge_record/0.1",
        "merge_id": "cmerge_" + content_hash({"result": result_id, "time": _now(), "dry_run": dry_run})[:16],
        "result_id": result_id,
        "codex_job_id": result.get("codex_job_id", ""),
        "work_order_id": result.get("work_order_id", ""),
        "status": "dry_run_passed" if dry_run else "merged_to_working_tree",
        "applied_patches": applied,
        "release_gate_hash": gate.get("release_gate_hash", ""),
        "actor": actor,
        "merged_at": _now(),
    }
    registry = _read_registry(merge_registry_path(project_dir), "v4.codex_merge_registry/0.1", "merges")
    _upsert(registry["merges"], merge, "merge_id")
    _write_registry(merge_registry_path(project_dir), registry)
    if not dry_run:
        result["merge_status"] = "merged"
        result["merged_at"] = merge["merged_at"]
        result["merge_ref"] = f"v4/codex_engineering/merge_registry.json#{merge['merge_id']}"
        result_registry = _read_registry(result_registry_path(project_dir), "v4.codex_result_registry/0.1", "results")
        _upsert(result_registry["results"], result, "result_id")
        _write_registry(result_registry_path(project_dir), result_registry)
        found = _find_order_and_packet(project_dir, result.get("codex_job_id", ""))
        order = found["order"]
        packet = found["packet"]
        order["engineering_merge_status"] = "merged"
        order["engineering_merge_ref"] = result["merge_ref"]
        order["status"] = "engineering_merged"
        packet["merge_status"] = "merged"
        packet["merge_ref"] = result["merge_ref"]
        save_codex_task_packet(project_dir, order, packet)
        save_v4_work_order(project_dir, order)
        try:
            from .engineering_closure import refresh_engineering_closure
            from .task_registry import build_task_registry

            refresh_engineering_closure(project_dir)
            build_task_registry(project_dir)
        except Exception:
            pass
    return merge


def mark_codex_result_reviewed(project_dir: Path, result_id: str, action: str, reason: str, reviewer: str = "human") -> dict[str, Any]:
    registry = _read_registry(result_registry_path(project_dir), "v4.codex_result_registry/0.1", "results")
    result = next((row for row in registry["results"] if row.get("result_id") == result_id), None)
    if not result:
        raise ValueError(f"Codex result not found: {result_id}")
    if action not in {"approve", "reject", "needs_review"}:
        raise ValueError(f"unsupported review action: {action}")
    result["review_status"] = action
    result["review_reason"] = reason
    result["reviewer"] = reviewer
    result["reviewed_at"] = _now()
    result["merge_status"] = "approved_for_merge" if action == "approve" else "merge_blocked"
    _write_registry(result_registry_path(project_dir), registry)
    found = _find_order_and_packet(project_dir, result["codex_job_id"])
    order = found["order"]
    packet = found["packet"]
    order["engineering_review_status"] = action
    order["engineering_review_reason"] = reason
    order["engineering_merge_status"] = result["merge_status"]
    packet["engineering_review_status"] = action
    packet["merge_status"] = result["merge_status"]
    save_codex_task_packet(project_dir, order, packet)
    save_v4_work_order(project_dir, order)
    try:
        from .engineering_closure import refresh_engineering_closure

        closure = refresh_engineering_closure(project_dir)
        result["engineering_closure_ref"] = "v4/codex_engineering/engineering_closure.json"
        result["evidence_snapshot_hash"] = closure.get("evidence_snapshot_hash", "")
        _write_registry(result_registry_path(project_dir), registry)
    except Exception as exc:
        result["engineering_closure_error"] = str(exc)
        _write_registry(result_registry_path(project_dir), registry)
    return result


def load_codex_engineering(project_dir: Path) -> dict[str, Any]:
    return {
        "workspaces": _read_registry(workspace_registry_path(project_dir), "v4.codex_workspace_registry/0.1", "workspaces")["workspaces"],
        "patches": _read_registry(patch_registry_path(project_dir), "v4.codex_patch_registry/0.1", "patches")["patches"],
        "tests": _read_registry(test_registry_path(project_dir), "v4.codex_test_registry/0.1", "tests")["tests"],
        "results": _read_registry(result_registry_path(project_dir), "v4.codex_result_registry/0.1", "results")["results"],
        "merges": _read_registry(merge_registry_path(project_dir), "v4.codex_merge_registry/0.1", "merges")["merges"],
    }


def _copy_allowed_inputs(project_dir: Path, workspace: Path, allowed_paths: list[str]) -> list[dict[str, str]]:
    copied: list[dict[str, str]] = []
    input_dir = workspace / "allowed_inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    for raw in allowed_paths:
        rel = str(raw).replace("\\", "/")
        if "*" in rel:
            continue
        source = (project_dir / rel).resolve()
        if not source.exists() or not source.is_file():
            continue
        try:
            source.relative_to(project_dir.resolve())
        except ValueError:
            continue
        target = input_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append({"source": rel, "workspace_path": str(target.relative_to(workspace)), "hash": file_hash(target)})
    return copied


def _find_work_order(project_dir: Path, work_order_id: str) -> dict[str, Any]:
    for order in load_v4_work_orders(project_dir):
        if order.get("work_order_id") == work_order_id:
            return order
    raise ValueError(f"work order not found: {work_order_id}")


def _find_order_and_packet(project_dir: Path, codex_job_id: str) -> dict[str, Any]:
    for order in load_v4_work_orders(project_dir):
        packet = load_codex_task_packet(project_dir, order)
        if packet.get("codex_job_id") == codex_job_id:
            return {"order": order, "packet": packet}
    raise ValueError(f"Codex task not found: {codex_job_id}")


def _workspace_for_job(project_dir: Path, codex_job_id: str) -> dict[str, Any]:
    registry = _read_registry(workspace_registry_path(project_dir), "v4.codex_workspace_registry/0.1", "workspaces")
    return next((row for row in registry["workspaces"] if row.get("codex_job_id") == codex_job_id), {})


def _update_workspace(project_dir: Path, codex_job_id: str, updates: dict[str, Any]) -> None:
    registry = _read_registry(workspace_registry_path(project_dir), "v4.codex_workspace_registry/0.1", "workspaces")
    for row in registry["workspaces"]:
        if row.get("codex_job_id") == codex_job_id:
            row.update(updates)
            row["updated_at"] = _now()
            break
    _write_registry(workspace_registry_path(project_dir), registry)


def _allowed_test_command(command: str) -> bool:
    normalized = " ".join(command.strip().split()).lower()
    if any(token in command for token in ["&", "|", ";", ">", "<", "\n", "\r"]):
        return False
    return normalized.startswith("python -m unittest") or normalized.startswith("py -m unittest")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_git(repo_root: Path, args: list[str]) -> None:
    completed = subprocess.run(["git", *args], cwd=repo_root, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())


def _resolve_project_file(project_dir: Path, rel_path: str) -> Path:
    path = (project_dir / rel_path).resolve()
    try:
        path.relative_to(project_dir.resolve())
    except ValueError:
        raise ValueError(f"path is outside project: {rel_path}") from None
    if not path.exists() or not path.is_file():
        raise ValueError(f"file not found: {rel_path}")
    return path


def _read_registry(path: Path, schema_version: str, key: str) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": schema_version, "updated_at": "", key: []}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_registry(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _now()
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _upsert(rows: list[dict[str, Any]], item: dict[str, Any], key: str) -> None:
    for idx, row in enumerate(rows):
        if row.get(key) == item.get(key):
            rows[idx] = item
            return
    rows.append(item)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
