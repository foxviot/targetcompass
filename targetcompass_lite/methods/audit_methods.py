import json

from .contracts import MethodContext, MethodResult, MethodSpec
from ..ideas import load_ideas


def _write_audit(project_dir, rows: list[dict]) -> None:
    out_dir = project_dir / "results" / "ideas"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "feasibility_audit.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def local_feasibility_audit(context: MethodContext) -> MethodResult:
    ideas = load_ideas(context.project_dir)
    candidates = [idea for idea in ideas if idea.get("execution_status") == "candidate"]
    rows = [
        {
            "idea_id": idea.get("idea_id"),
            "title": idea.get("title"),
            "status": idea.get("execution_status"),
            "feasibility_score": idea.get("feasibility_score"),
            "blockers": idea.get("blockers", []),
        }
        for idea in ideas
    ]
    _write_audit(context.project_dir, rows)
    return MethodResult(
        status="review" if len(candidates) < len(ideas) else "pass",
        message=f"{len(ideas)} idea(s) audited; {len(candidates)} passed feasibility thresholds.",
        details={"idea_count": len(ideas), "candidate_count": len(candidates)},
    )


def strict_feasibility_audit(context: MethodContext) -> MethodResult:
    ideas = load_ideas(context.project_dir)
    candidates = [
        idea
        for idea in ideas
        if idea.get("execution_status") == "candidate" and int(idea.get("feasibility_score", 0)) >= 85
    ]
    rows = [
        {
            "idea_id": idea.get("idea_id"),
            "title": idea.get("title"),
            "status": "candidate" if idea in candidates else "review",
            "feasibility_score": idea.get("feasibility_score"),
            "blockers": idea.get("blockers", []) + ([] if idea in candidates else ["strict audit threshold not met"]),
        }
        for idea in ideas
    ]
    _write_audit(context.project_dir, rows)
    return MethodResult(
        status="review" if len(candidates) < len(ideas) else "pass",
        message=f"Strict audit accepted {len(candidates)} of {len(ideas)} idea(s).",
        details={"idea_count": len(ideas), "candidate_count": len(candidates), "threshold": 85},
    )


METHODS = [
    MethodSpec(
        method_id="local_feasibility_audit_v0",
        stage="audit",
        label="Local feasibility audit v0",
        description="Default quantitative feasibility audit using data fit, route, novelty, and risk scores.",
        runner=local_feasibility_audit,
    ),
    MethodSpec(
        method_id="strict_feasibility_audit_v0",
        stage="audit",
        label="Strict feasibility audit v0",
        description="More conservative audit requiring feasibility score >= 85 before a point is treated as candidate.",
        runner=strict_feasibility_audit,
    ),
]
