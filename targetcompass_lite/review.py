import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .v4 import load_codex_task_packet, load_v4_work_orders, save_codex_task_packet, save_v4_work_order


VALID_ACTIONS = {"approve", "reject", "needs_review"}
SIGNOFF_STATUSES = {"draft", "review_required", "ready_for_signoff", "signed_off", "rejected"}


def review_log_path(project_dir: Path) -> Path:
    return project_dir / "results" / "review_actions.tsv"


def review_event_path(project_dir: Path) -> Path:
    return project_dir / "results" / "review_actions.jsonl"


def review_versions_dir(project_dir: Path) -> Path:
    return project_dir / "results" / "review_versions"


def review_queue_path(project_dir: Path) -> Path:
    return project_dir / "results" / "review_queue.json"


def approval_state_path(project_dir: Path) -> Path:
    return project_dir / "results" / "approval_state.json"


def record_review(
    project_dir: Path,
    item_type: str,
    item_id: str,
    action: str,
    note: str = "",
    reviewer: str = "human",
    reason: str = "",
    report_ref: str = "",
) -> dict:
    action = action.strip()
    if action not in VALID_ACTIONS:
        raise ValueError(f"unsupported review action: {action}")
    if not (reason or note).strip():
        raise ValueError("review reason is required")
    before = _artifact_snapshot(project_dir, item_type, item_id)
    row = {
        "review_id": f"rvw_{uuid4().hex[:12]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reviewer": reviewer,
        "item_type": item_type.strip(),
        "item_id": item_id.strip(),
        "action": action,
        "reason": (reason or note).strip(),
        "note": note.strip(),
        "report_ref": report_ref.strip() or _default_report_ref(item_type, item_id),
        "before_hash": _snapshot_hash(before),
        "before": before,
    }
    _apply_review_to_artifacts(project_dir, row)
    after = _artifact_snapshot(project_dir, item_type, item_id)
    row["after_hash"] = _snapshot_hash(after)
    row["after"] = after
    row["diff"] = _snapshot_diff(before, after)
    row["version_file"] = str(_write_review_version(project_dir, row).relative_to(project_dir))
    path = review_log_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        fields = [
            "review_id",
            "timestamp",
            "reviewer",
            "item_type",
            "item_id",
            "action",
            "reason",
            "note",
            "report_ref",
            "before_hash",
            "after_hash",
            "diff",
            "version_file",
        ]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        if not exists:
            writer.writeheader()
        writer.writerow({field: json.dumps(row[field], ensure_ascii=False) if field == "diff" else row.get(field, "") for field in fields})
    with review_event_path(project_dir).open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    build_review_queue(project_dir)
    update_approval_state(project_dir)
    return row


def build_review_queue(project_dir: Path) -> dict:
    ideas = _load_ideas(project_dir)
    queue = []
    for idea in ideas:
        review_status = idea.get("review_status", "")
        execution_status = idea.get("execution_status", "")
        if review_status == "approve":
            continue
        if review_status in {"", "needs_review"} or execution_status in {"review", "candidate"}:
            queue.append(
                {
                    "item_type": "idea",
                    "item_id": idea.get("idea_id", ""),
                    "title": idea.get("title", ""),
                    "execution_status": execution_status,
                    "review_status": review_status or "pending",
                    "reason": idea.get("review_reason", ""),
                    "report_ref": _default_report_ref("idea", idea.get("idea_id", "")),
                }
            )
    for order in load_v4_work_orders(project_dir):
        review_status = order.get("review_status", "")
        needs_review = order.get("requires_codex") or order.get("work_order_type") in {"BUILD_ADAPTER", "FIX_CODE"}
        if review_status == "approve" or not needs_review:
            continue
        queue.append(
            {
                "item_type": "work_order",
                "item_id": order.get("work_order_id", ""),
                "title": f"{order.get('work_order_type', '')}: {order.get('module_id', '')}",
                "execution_status": order.get("status", "compiled"),
                "review_status": review_status or "pending",
                "reason": order.get("review_reason", ""),
                "report_ref": _default_report_ref("work_order", order.get("work_order_id", "")),
            }
        )
        if order.get("codex_task_packet"):
            packet = load_codex_task_packet(project_dir, order)
            packet_status = packet.get("review_status", "")
            if packet_status != "approve":
                queue.append(
                    {
                        "item_type": "codex_task",
                        "item_id": packet.get("codex_job_id", order.get("work_order_id", "")),
                        "title": f"Codex task packet: {order.get('module_id', '')}",
                        "execution_status": packet.get("release_gate", "review_required"),
                        "review_status": packet_status or "pending",
                        "reason": packet.get("review_reason", ""),
                        "report_ref": _default_report_ref("codex_task", packet.get("codex_job_id", order.get("work_order_id", ""))),
                    }
                )
    reviews = load_reviews(project_dir)
    approved = sum(1 for row in reviews if row.get("action") == "approve")
    rejected = sum(1 for row in reviews if row.get("action") == "reject")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queue_count": len(queue),
        "approved_count": approved,
        "rejected_count": rejected,
        "items": queue,
    }
    path = review_queue_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def update_approval_state(project_dir: Path, status: str | None = None, signer: str = "", reason: str = "") -> dict:
    queue = build_review_queue(project_dir) if not review_queue_path(project_dir).exists() else json.loads(review_queue_path(project_dir).read_text(encoding="utf-8"))
    reviews = load_reviews(project_dir)
    if status is None:
        status = "ready_for_signoff" if queue.get("queue_count", 0) == 0 and reviews else "review_required"
    if status not in SIGNOFF_STATUSES:
        raise ValueError(f"unsupported approval status: {status}")
    if status in {"signed_off", "rejected"} and not reason.strip():
        raise ValueError("final signoff reason is required")
    if status == "signed_off" and queue.get("queue_count", 0):
        raise ValueError("cannot sign off while review queue is not empty")
    previous = load_approval_state(project_dir)
    payload = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "signer": signer,
        "reason": reason.strip(),
        "queue_count": queue.get("queue_count", 0),
        "review_count": len(reviews),
        "approved_count": sum(1 for row in reviews if row.get("action") == "approve"),
        "rejected_count": sum(1 for row in reviews if row.get("action") == "reject"),
        "previous_status": previous.get("status", "draft"),
        "report_ref": "reports/target_report.html#approval-audit",
    }
    path = approval_state_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def load_approval_state(project_dir: Path) -> dict:
    path = approval_state_path(project_dir)
    if not path.exists():
        return {"status": "draft", "queue_count": 0, "review_count": 0}
    return json.loads(path.read_text(encoding="utf-8"))


def final_signoff(project_dir: Path, signer: str, reason: str, status: str = "signed_off") -> dict:
    return update_approval_state(project_dir, status=status, signer=signer, reason=reason)


def load_reviews(project_dir: Path) -> list[dict]:
    path = review_log_path(project_dir)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def load_review_events(project_dir: Path) -> list[dict]:
    path = review_event_path(project_dir)
    if not path.exists():
        return []
    events = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


def _apply_review_to_artifacts(project_dir: Path, review: dict) -> None:
    if review["item_type"] == "work_order":
        for order in load_v4_work_orders(project_dir):
            if order.get("work_order_id") == review["item_id"]:
                order["review_status"] = review["action"]
                order["review_reason"] = review.get("reason", review.get("note", ""))
                order["review_note"] = review["note"]
                order["reviewer"] = review["reviewer"]
                order["review_id"] = review.get("review_id", "")
                order["reviewed_at"] = review.get("timestamp", "")
                if review["action"] == "approve":
                    order["status"] = "approved"
                elif review["action"] == "reject":
                    order["status"] = "rejected"
                else:
                    order["status"] = "review_required"
                save_v4_work_order(project_dir, order)
                return
        return
    if review["item_type"] == "codex_task":
        for order in load_v4_work_orders(project_dir):
            packet = load_codex_task_packet(project_dir, order)
            if packet.get("codex_job_id") == review["item_id"]:
                packet["review_status"] = review["action"]
                packet["review_reason"] = review.get("reason", review.get("note", ""))
                packet["review_note"] = review["note"]
                packet["reviewer"] = review["reviewer"]
                packet["review_id"] = review.get("review_id", "")
                packet["reviewed_at"] = review.get("timestamp", "")
                packet["release_gate"] = "approved_for_codex_worker" if review["action"] == "approve" else "human_review_required"
                save_codex_task_packet(project_dir, order, packet)
                return
        return
    if review["item_type"] != "idea":
        return
    path = project_dir / "results" / "ideas" / "idea_batch.json"
    if not path.exists():
        return
    ideas = json.loads(path.read_text(encoding="utf-8"))
    for idea in ideas:
        if idea.get("idea_id") == review["item_id"]:
            idea["review_status"] = review["action"]
            idea["review_reason"] = review.get("reason", review.get("note", ""))
            idea["review_note"] = review["note"]
            idea["reviewer"] = review["reviewer"]
            idea["review_id"] = review.get("review_id", "")
            idea["reviewed_at"] = review.get("timestamp", "")
    path.write_text(json.dumps(ideas, indent=2, ensure_ascii=False), encoding="utf-8")


def _artifact_snapshot(project_dir: Path, item_type: str, item_id: str) -> dict:
    if item_type == "work_order":
        for order in load_v4_work_orders(project_dir):
            if order.get("work_order_id") == item_id:
                keys = [
                    "work_order_id",
                    "work_order_type",
                    "module_id",
                    "module",
                    "dataset_id",
                    "status",
                    "requires_codex",
                    "review_status",
                    "review_reason",
                    "review_note",
                    "reviewer",
                    "review_id",
                    "codex_task_packet",
                ]
                return {key: order.get(key, "") for key in keys}
        return {}
    if item_type == "codex_task":
        for order in load_v4_work_orders(project_dir):
            packet = load_codex_task_packet(project_dir, order)
            if packet.get("codex_job_id") == item_id:
                keys = [
                    "codex_job_id",
                    "work_order_id",
                    "task_type",
                    "baseline_commit",
                    "release_gate",
                    "allowed_paths",
                    "tests",
                    "review_status",
                    "review_reason",
                    "review_note",
                    "reviewer",
                    "review_id",
                ]
                return {key: packet.get(key, "") for key in keys}
        return {}
    if item_type != "idea":
        return {}
    ideas = _load_ideas(project_dir)
    for idea in ideas:
        if idea.get("idea_id") == item_id:
            keys = [
                "idea_id",
                "title",
                "execution_status",
                "feasibility_score",
                "review_status",
                "review_reason",
                "review_note",
                "reviewer",
                "review_id",
            ]
            return {key: idea.get(key, "") for key in keys}
    return {}


def _load_ideas(project_dir: Path) -> list[dict]:
    path = project_dir / "results" / "ideas" / "idea_batch.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _write_review_version(project_dir: Path, row: dict) -> Path:
    out_dir = review_versions_dir(project_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{row['review_id']}.json"
    payload = {
        "review_id": row["review_id"],
        "timestamp": row["timestamp"],
        "reviewer": row["reviewer"],
        "item_type": row["item_type"],
        "item_id": row["item_id"],
        "action": row["action"],
        "reason": row["reason"],
        "note": row["note"],
        "report_ref": row["report_ref"],
        "before_hash": row["before_hash"],
        "after_hash": row["after_hash"],
        "before": row["before"],
        "after": row["after"],
        "diff": row["diff"],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _snapshot_hash(snapshot: dict) -> str:
    payload = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _snapshot_diff(before: dict, after: dict) -> dict:
    keys = sorted(set(before) | set(after))
    return {
        key: {"before": before.get(key, ""), "after": after.get(key, "")}
        for key in keys
        if before.get(key, "") != after.get(key, "")
    }


def _default_report_ref(item_type: str, item_id: str) -> str:
    anchor = f"{item_type}-{item_id}".lower().replace(" ", "-").replace("_", "-")
    return f"reports/target_report.html#{anchor}"
