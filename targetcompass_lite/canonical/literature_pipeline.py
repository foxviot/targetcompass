from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifacts import register_artifact
from .schemas import now_iso


LITERATURE_PIPELINE_SCHEMA_VERSION = "v5.literature_pipeline/0.1"


def run_v5_literature_pipeline(
    project_dir: str | Path,
    *,
    query: str = "",
    limit: int = 20,
    batch_size: int = 10,
    timeout: int = 20,
    use_llm: bool = False,
    fulltext_limit: int = 5,
    run_fulltext_llm: bool = False,
    model: str = "",
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    from targetcompass_lite.fulltext_literature import run_fulltext_literature
    from targetcompass_lite.literature_validation import run_literature_validation

    lit = run_literature_validation(project_dir, query=query, limit=limit, batch_size=batch_size, use_llm=use_llm, timeout=timeout)
    fulltext = run_fulltext_literature(project_dir, limit=fulltext_limit, timeout=timeout)
    fulltext_llm: dict[str, Any] = {"status": "skipped", "reason": "run_fulltext_llm=false"}
    if run_fulltext_llm:
        from targetcompass_lite.fulltext_llm_extraction import run_fulltext_llm_extraction

        fulltext_llm = run_fulltext_llm_extraction(project_dir, max_docs=fulltext_limit, model=model)
    artifacts = _register_literature_artifacts(project_dir, lit, fulltext, fulltext_llm)
    payload = {
        "schema_version": LITERATURE_PIPELINE_SCHEMA_VERSION,
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "query": lit.get("query", query),
        "abstract_layer": {
            "run_ref": "results/literature_validation/literature_validation_run.json",
            "article_count": lit.get("article_count", 0),
            "evidence_row_count": lit.get("evidence_row_count", 0),
            "evidence_level": "L0_abstract",
        },
        "fulltext_layer": {
            "run_ref": "results/fulltext_literature/fulltext_literature_run.json",
            "document_count": fulltext.get("document_count", 0),
            "failure_count": fulltext.get("failure_count", 0),
            "evidence_row_count": fulltext.get("evidence_row_count", 0),
            "evidence_level": "L1_fulltext",
            "failure_recovery": "Upload PDF or TXT when PMC Open Access full text is unavailable.",
        },
        "fulltext_llm_layer": {
            "run_ref": "results/fulltext_literature/llm_extraction/fulltext_llm_extraction_run.json" if run_fulltext_llm else "",
            "status": "completed" if run_fulltext_llm else "skipped",
            "evidence_row_count": fulltext_llm.get("evidence_row_count", 0),
            "evidence_levels": ["L1_fulltext", "L5_experimental"] if run_fulltext_llm else [],
        },
        "artifact_refs": [artifact["artifact_id"] for artifact in artifacts],
        "default_evidence_policy": {
            "abstract_only": "low weight L0_abstract",
            "fulltext_parsed": "medium weight L1_fulltext",
            "fulltext_llm_result_with_assay": "high weight L5_experimental pending human review",
        },
        "status": "completed" if lit.get("article_count", 0) or fulltext.get("document_count", 0) else "review_required",
        "limitations": [
            "Abstract-only evidence cannot support strong biological claims.",
            "PMC full text is only available for open-access articles; upload PDF/TXT for missing full text.",
            "LLM-extracted evidence remains PENDING until audited.",
        ],
    }
    out = project_dir / "v5" / "literature" / "literature_pipeline_run.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return payload


def _register_literature_artifacts(project_dir: Path, *runs: dict[str, Any]) -> list[dict[str, Any]]:
    refs = []
    for run in runs:
        for artifact in (run.get("artifacts") or {}).values():
            if artifact and (project_dir / artifact).exists():
                refs.append(artifact)
    artifacts = []
    for ref in sorted(set(refs)):
        artifacts.append(
            register_artifact(
                project_dir,
                ref,
                producer="v5_literature_pipeline",
                artifact_type="v5_literature_evidence_artifact",
                expected_by_task_ids=["v5_literature_pipeline"],
                supports_subquestion_ids=[],
                producer_run_id="v5_literature_pipeline",
                qc_status="pending",
                limitations=["Literature evidence artifact requires result_auditor and question alignment before claim synthesis."],
            )
        )
    return artifacts
