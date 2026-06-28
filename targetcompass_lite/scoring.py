import csv
import hashlib
import json
import time
from pathlib import Path

from .paths import KB
from .yamlmini import load_yaml


DEFAULT_RULES = KB / "scoring_rules" / "vaccine_target_v0.yaml"


def _load_annotation(path: Path, key: str) -> dict:
    with path.open(encoding="utf-8") as f:
        return {row[key]: row for row in csv.DictReader(f, delimiter="\t")}


def load_scoring_rules(path: Path = DEFAULT_RULES) -> dict:
    rules = load_yaml(path)
    required = ["expression", "route", "safety", "reproducibility", "specificity", "genetic", "tiers"]
    missing = [key for key in required if key not in rules]
    if missing:
        raise ValueError(f"scoring rules missing sections: {', '.join(missing)}")
    return rules


def _expression_score(best_deg: dict | None, rules: dict) -> float:
    if not best_deg:
        return 0
    expr = rules["expression"]
    effect = _float(best_deg.get("effect_size"))
    p_value = _float(best_deg.get("p_value"), 1.0)
    score = abs(effect) * expr["abs_effect_multiplier"]
    if p_value < expr["significance_p_value"]:
        score += expr["significant_bonus"]
    return min(expr["max_score"], score)


def _float(value: object, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _tier(final: float, hard_gate: str, rules: dict) -> str:
    tiers = rules["tiers"]
    if final >= tiers["A_min_score"] and hard_gate == "PASS":
        return "A"
    if hard_gate == "REJECTED_NO_DISEASE_EVIDENCE":
        return "C"
    if final >= tiers["B_min_score"] and hard_gate != "EXCLUDED_SAFETY":
        return "B"
    return "C"


def _evidence_level_score(evidences: list[dict]) -> float:
    weights = []
    for row in evidences:
        try:
            weights.append(float(row.get("evidence_weight") or 0))
        except (TypeError, ValueError):
            continue
    if not weights:
        return 0
    # Cap at 12 so low-level abstract hits cannot dominate expression/QC evidence.
    return min(12.0, round(sum(sorted(weights, reverse=True)[:8]) * 2.0, 2))


def _evidence_level_counts(evidences: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in evidences:
        level = row.get("evidence_level") or "unclassified"
        counts[level] = counts.get(level, 0) + 1
    return counts


def _load_evidence_plan(project_dir: Path) -> dict:
    path = project_dir / "results" / "evidence_planning" / "evidence_plan.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _evidence_axis_coverage(evidences: list[dict], evidence_plan: dict) -> dict:
    axes = evidence_plan.get("evidence_axes", {}) if evidence_plan else {}
    types = {row.get("evidence_type", "") for row in evidences}
    axis_rules = {
        "disease_relevant_expression": {"types": {"bulk_deg", "scrna_pseudobulk", "deg_meta_analysis"}},
        "cell_type_specificity": {"types": {"cell_type_expression", "cell_type_evidence", "scrna_pseudobulk_cell_type"}},
        "condition_upregulation": {"types": {"bulk_deg", "scrna_pseudobulk", "deg_meta_analysis", "sasp_score"}, "direction": "up"},
        "SASP_annotation": {"types": {"sasp_score"}},
        "secreted_or_surface_annotation": {"types": {"accessibility", "surface_marker_annotation"}},
        "cross_dataset_validation": {"types": {"deg_meta_analysis"}, "cross_dataset": True},
        "literature_support": {"types": {"literature_validation", "fulltext_literature", "fulltext_extracted_result"}},
        "pathway_enrichment": {"types": {"pathway_enrichment"}},
        "causal_or_genetic_support": {"types": {"genetic_evidence", "coloc", "mr", "causal_grade"}},
    }
    aggregation = {axis: _axis_aggregate(evidences, rule) for axis, rule in axis_rules.items()}
    coverage = {axis: row["covered"] for axis, row in aggregation.items()}
    required = [axis for axis, needed in axes.items() if needed]
    missing = [axis for axis in required if not coverage.get(axis, False)]
    covered = [axis for axis in required if coverage.get(axis, False)]
    weighted_required = sum(float(aggregation.get(axis, {}).get("axis_weight", 1.0)) for axis in required) or 1.0
    weighted_covered = sum(float(aggregation.get(axis, {}).get("axis_weight", 1.0)) for axis in covered)
    return {
        "required_axes": required,
        "covered_axes": covered,
        "missing_axes": missing,
        "coverage_fraction": round((len(required) - len(missing)) / len(required), 3) if required else 1.0,
        "weighted_coverage_fraction": round(weighted_covered / weighted_required, 3) if required else 1.0,
        "axis_aggregation": aggregation,
        "all_axes": coverage,
    }


def _evidence_plan_score(axis_coverage: dict) -> float:
    weighted = float(axis_coverage.get("weighted_coverage_fraction", axis_coverage.get("coverage_fraction", 1.0)))
    evidence_strength = 0.0
    for axis in axis_coverage.get("covered_axes", []):
        row = axis_coverage.get("axis_aggregation", {}).get(axis, {})
        evidence_strength += min(1.0, float(row.get("weight_sum", 0.0)) / 2.0) * float(row.get("axis_weight", 1.0))
    normalizer = sum(float(axis_coverage.get("axis_aggregation", {}).get(axis, {}).get("axis_weight", 1.0)) for axis in axis_coverage.get("required_axes", [])) or 1.0
    strength_fraction = evidence_strength / normalizer
    return round(12.0 * ((weighted * 0.7) + (strength_fraction * 0.3)), 2)


def _blocking_missing_axes(axis_coverage: dict) -> list[str]:
    core_axes = {
        "disease_relevant_expression",
        "condition_upregulation",
        "SASP_annotation",
        "secreted_or_surface_annotation",
        "cell_type_specificity",
    }
    missing = set(axis_coverage.get("missing_axes", []))
    return sorted(axis for axis in missing if axis in core_axes)


def _axis_aggregate(evidences: list[dict], rule: dict) -> dict:
    matched = []
    allowed = rule.get("types", set())
    for row in evidences:
        evidence_type = row.get("evidence_type", "")
        if evidence_type not in allowed:
            continue
        if rule.get("direction") and row.get("direction") != rule["direction"]:
            continue
        matched.append(row)
    if rule.get("cross_dataset"):
        datasets = {row.get("source_dataset", "") for row in evidences if row.get("source_dataset")}
        matched = matched or ([{"evidence_weight": 0.5, "evidence_id": "cross_dataset_observed"}] if len(datasets) > 1 else [])
    weight_sum = 0.0
    for row in matched:
        try:
            weight_sum += float(row.get("evidence_weight") or 0)
        except (TypeError, ValueError):
            continue
    return {
        "covered": bool(matched),
        "evidence_count": len(matched),
        "weight_sum": round(weight_sum, 3),
        "evidence_refs": sorted(str(row.get("evidence_id", "")) for row in matched if row.get("evidence_id"))[:20],
        "axis_weight": 1.25 if rule.get("direction") or rule.get("cross_dataset") else 1.0,
    }


def score_project(project_dir: Path, rules_path: Path = DEFAULT_RULES) -> Path:
    rules = load_scoring_rules(rules_path)
    evidence_plan = _load_evidence_plan(project_dir)
    rubric_hash = hashlib.sha256(json.dumps(rules, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    from .evidence_repository import load_evidence_rows

    rows = load_evidence_rows(project_dir, limit=100000)["rows"]
    evidence_snapshot_id = "es_" + hashlib.sha256(
        json.dumps([dict(row) for row in rows], sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]
    by_gene = {}
    for row in rows:
        by_gene.setdefault(row["entity_symbol"], []).append(dict(row))
    access = _load_annotation(project_dir / "results" / "annotation" / "accessibility_annotation.tsv", "gene_symbol")
    safety = _load_annotation(project_dir / "results" / "annotation" / "safety_flags.tsv", "gene_symbol")
    scored = []
    for gene, evidences in by_gene.items():
        degs = [e for e in evidences if e["evidence_type"] == "bulk_deg"]
        evidence_refs = sorted(e["evidence_id"] for e in evidences if e.get("review_status", "PENDING") in {"PENDING", "ACCEPT", "ACCEPT_WITH_FLAGS", "approve", "accepted"})
        route = access.get(gene, {}).get("route", "unknown")
        safety_gate = safety.get(gene, {}).get("safety_gate", "UNKNOWN")
        best_deg = max(degs, key=lambda e: abs(_float(e.get("effect_size")))) if degs else None
        expression_score = _expression_score(best_deg, rules)
        route_score = rules["route"]["supported_score"] if route in set(rules["route"]["supported_routes"]) else rules["route"]["unknown_score"]
        safety_score = rules["safety"]["scores"].get(safety_gate, rules["safety"]["default_score"])
        reproducibility_score = rules["reproducibility"]["single_dataset_score"] if len(degs) == 1 else min(
            rules["reproducibility"]["max_score"],
            len(degs) * rules["reproducibility"]["per_dataset_score"],
        )
        specificity_score = rules["specificity"]["priority_score"] if gene in set(rules["specificity"]["priority_genes"]) else rules["specificity"]["default_score"]
        genetic_score = rules["genetic"]["mvp_score"]
        evidence_level_score = _evidence_level_score(evidences)
        evidence_level_counts = _evidence_level_counts(evidences)
        axis_coverage = _evidence_axis_coverage(evidences, evidence_plan)
        evidence_plan_score = _evidence_plan_score(axis_coverage)
        final = expression_score + route_score + safety_score + reproducibility_score + specificity_score + genetic_score + evidence_level_score + evidence_plan_score
        hard_gate = "PASS"
        if not best_deg:
            hard_gate = "REJECTED_NO_DISEASE_EVIDENCE"
        elif route == "unknown":
            hard_gate = "ROUTE_UNKNOWN"
        if _blocking_missing_axes(axis_coverage) and hard_gate == "PASS":
            hard_gate = "EVIDENCE_PLAN_INCOMPLETE"
        if safety_gate == "EXCLUDED":
            hard_gate = "EXCLUDED_SAFETY"
        tier = _tier(final, hard_gate, rules)
        score_id = "score_" + hashlib.sha256(
            json.dumps(
                {
                    "project": project_dir.name,
                    "gene": gene,
                    "snapshot": evidence_snapshot_id,
                    "rubric": rubric_hash,
                    "final": round(final, 6),
                    "hard_gate": hard_gate,
                },
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()[:16]
        scored.append(
            {
                "score_id": score_id,
                "evidence_snapshot_id": evidence_snapshot_id,
                "entity_symbol": gene,
                "route": route,
                "final_score": f"{final:.2f}",
                "tier": tier,
                "hard_gate_status": hard_gate,
                "safety_gate": safety_gate,
                "evidence_refs": ";".join(evidence_refs),
                "score_json": json.dumps(
                    {
                        "expression": round(expression_score, 2),
                        "reproducibility": reproducibility_score,
                        "specificity": specificity_score,
                        "route": route_score,
                        "safety": safety_score,
                        "genetic": genetic_score,
                        "evidence_level": evidence_level_score,
                        "evidence_level_counts": evidence_level_counts,
                        "evidence_plan": evidence_plan_score,
                        "evidence_axis_coverage": axis_coverage,
                    },
                    sort_keys=True,
                ),
                "next_experiments": rules["next_experiments"],
            }
        )
    scored.sort(key=lambda r: (-float(r["final_score"]), r["hard_gate_status"], r["entity_symbol"]))
    out = project_dir / "candidate_scores.csv"
    tmp = out.with_name(out.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        fields = [
            "score_id",
            "evidence_snapshot_id",
            "entity_symbol",
            "route",
            "final_score",
            "tier",
            "hard_gate_status",
            "safety_gate",
            "evidence_refs",
            "score_json",
            "next_experiments",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(scored)
    _replace_with_retry(tmp, out)
    _write_score_manifest(project_dir, evidence_snapshot_id, rubric_hash, scored, rules_path)
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [out, project_dir / "results" / "scoring" / "target_score_manifest.json"],
            producer="scoring",
            artifact_type="scoring_output",
            task_id="scoring",
        )
    except Exception:
        pass
    return out


def _replace_with_retry(source: Path, target: Path, attempts: int = 8) -> None:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            source.replace(target)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.25)
    if last_error:
        raise last_error


def _write_score_manifest(project_dir: Path, evidence_snapshot_id: str, rubric_hash: str, scored: list[dict], rules_path: Path = DEFAULT_RULES) -> None:
    from .registry_snapshots import build_registry_snapshots

    out_dir = project_dir / "results" / "scoring"
    out_dir.mkdir(parents=True, exist_ok=True)
    registry_snapshot = build_registry_snapshots(project_dir, rules_path)
    rubric_snapshot = registry_snapshot.get("snapshots", {}).get("rubric", {})
    payload = {
        "schema_version": "target_score_manifest_v1",
        "project_id": project_dir.name,
        "evidence_snapshot_id": evidence_snapshot_id,
        "rubric_hash": rubric_hash,
        "rubric_snapshot_hash": rubric_snapshot.get("hash", ""),
        "registry_snapshot": "v4/registry_snapshots.json",
        "score_count": len(scored),
        "score_ids": [row["score_id"] for row in scored],
        "evidence_plan_ref": "results/evidence_planning/evidence_plan.json" if (project_dir / "results" / "evidence_planning" / "evidence_plan.json").exists() else "",
    }
    out = out_dir / "target_score_manifest.json"
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    _replace_with_retry(tmp, out)
