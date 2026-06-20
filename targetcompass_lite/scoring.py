import csv
import json
import sqlite3
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
    score = abs(best_deg["effect_size"]) * expr["abs_effect_multiplier"]
    if best_deg["p_value"] < expr["significance_p_value"]:
        score += expr["significant_bonus"]
    return min(expr["max_score"], score)


def _tier(final: float, hard_gate: str, rules: dict) -> str:
    tiers = rules["tiers"]
    if final >= tiers["A_min_score"] and hard_gate == "PASS":
        return "A"
    if final >= tiers["B_min_score"] and hard_gate != "EXCLUDED_SAFETY":
        return "B"
    return "C"


def score_project(project_dir: Path, rules_path: Path = DEFAULT_RULES) -> Path:
    rules = load_scoring_rules(rules_path)
    con = sqlite3.connect(project_dir / "evidence.sqlite")
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM evidence_item ORDER BY entity_symbol, evidence_type, evidence_id").fetchall()
    con.close()
    by_gene = {}
    for row in rows:
        by_gene.setdefault(row["entity_symbol"], []).append(dict(row))
    access = _load_annotation(project_dir / "results" / "annotation" / "accessibility_annotation.tsv", "gene_symbol")
    safety = _load_annotation(project_dir / "results" / "annotation" / "safety_flags.tsv", "gene_symbol")
    scored = []
    for gene, evidences in by_gene.items():
        degs = [e for e in evidences if e["evidence_type"] == "bulk_deg"]
        route = access.get(gene, {}).get("route", "unknown")
        safety_gate = safety.get(gene, {}).get("safety_gate", "UNKNOWN")
        best_deg = max(degs, key=lambda e: abs(e["effect_size"] or 0)) if degs else None
        expression_score = _expression_score(best_deg, rules)
        route_score = rules["route"]["supported_score"] if route in set(rules["route"]["supported_routes"]) else rules["route"]["unknown_score"]
        safety_score = rules["safety"]["scores"].get(safety_gate, rules["safety"]["default_score"])
        reproducibility_score = rules["reproducibility"]["single_dataset_score"] if len(degs) == 1 else min(
            rules["reproducibility"]["max_score"],
            len(degs) * rules["reproducibility"]["per_dataset_score"],
        )
        specificity_score = rules["specificity"]["priority_score"] if gene in set(rules["specificity"]["priority_genes"]) else rules["specificity"]["default_score"]
        genetic_score = rules["genetic"]["mvp_score"]
        final = expression_score + route_score + safety_score + reproducibility_score + specificity_score + genetic_score
        hard_gate = "PASS"
        if not best_deg:
            hard_gate = "REJECTED_NO_DISEASE_EVIDENCE"
        elif route == "unknown":
            hard_gate = "ROUTE_UNKNOWN"
        if safety_gate == "EXCLUDED":
            hard_gate = "EXCLUDED_SAFETY"
        tier = _tier(final, hard_gate, rules)
        scored.append(
            {
                "entity_symbol": gene,
                "route": route,
                "final_score": f"{final:.2f}",
                "tier": tier,
                "hard_gate_status": hard_gate,
                "safety_gate": safety_gate,
                "score_json": json.dumps(
                    {
                        "expression": round(expression_score, 2),
                        "reproducibility": reproducibility_score,
                        "specificity": specificity_score,
                        "route": route_score,
                        "safety": safety_score,
                        "genetic": genetic_score,
                    },
                    sort_keys=True,
                ),
                "next_experiments": rules["next_experiments"],
            }
        )
    scored.sort(key=lambda r: (-float(r["final_score"]), r["hard_gate_status"], r["entity_symbol"]))
    out = project_dir / "candidate_scores.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        fields = ["entity_symbol", "route", "final_score", "tier", "hard_gate_status", "safety_gate", "score_json", "next_experiments"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(scored)
    return out
