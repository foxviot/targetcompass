import csv
import hashlib
import json
from pathlib import Path

from .matching import match_project


ROUTE_WEIGHTS = {
    "secreted": 8,
    "surface": 7,
    "ECD": 6,
    "T_cell_peptide": 5,
}


def _stable_id(text: str, index: int) -> str:
    digest = hashlib.sha1(f"{index}:{text}".encode("utf-8")).hexdigest()[:8]
    return f"idea_{index:03d}_{digest}"


def _safe_count(count: int) -> int:
    return max(1, min(int(count or 1), 50))


def _base_ideas(interest: str, count: int) -> list[dict]:
    templates = [
        ("Secreted senescence signal target", "secreted", "Prioritize soluble mediators elevated in senescent vascular cells."),
        ("Surface accessibility target", "surface", "Prioritize membrane-accessible molecules with analyzable expression support."),
        ("ECD shedding target", "ECD", "Prioritize extracellular-domain candidates that can be bound or depleted."),
        ("T cell epitope candidate", "T_cell_peptide", "Prioritize antigens only when presentation evidence can be added later."),
        ("Inflammaging mediator target", "secreted", "Focus on inflammatory molecules that connect vascular aging and immune activation."),
        ("Endothelial dysfunction marker", "surface", "Focus on endothelial candidates with replicated disease contrast support."),
    ]
    ideas = []
    for idx in range(1, _safe_count(count) + 1):
        title, route, rationale = templates[(idx - 1) % len(templates)]
        variant = 1 + ((idx - 1) // len(templates))
        display_title = title if variant == 1 else f"{title} v{variant}"
        ideas.append(
            {
                "idea_id": _stable_id(f"{interest}:{display_title}:{route}", idx),
                "title": display_title,
                "route": route,
                "research_prompt": interest.strip(),
                "rationale": rationale,
                "review_status": "pending",
            }
        )
    return ideas


def _dataset_fit(project_dir: Path) -> tuple[int, int]:
    try:
        rows = match_project(project_dir)
    except Exception:
        return 0, 0
    matches = sum(1 for row in rows if row.get("match_status") == "MATCH")
    reviews = sum(1 for row in rows if row.get("match_status") != "MATCH")
    return matches, reviews


def evaluate_idea(project_dir: Path, idea: dict) -> dict:
    matches, reviews = _dataset_fit(project_dir)
    route_score = ROUTE_WEIGHTS.get(idea.get("route"), 4) * 5
    data_fit_score = min(35, matches * 12 + max(0, 6 - reviews * 3))
    novelty_score = 12 + (int(idea["idea_id"][-2:], 16) % 14)
    risk_penalty = 8 if idea.get("route") == "T_cell_peptide" else 3
    feasibility_score = max(0, min(100, route_score + data_fit_score + novelty_score - risk_penalty))
    blockers = []
    if matches == 0:
        blockers.append("no matched dataset")
    if idea.get("route") == "T_cell_peptide":
        blockers.append("MVP lacks antigen-presentation validation")
    status = "review" if blockers or feasibility_score < 70 else "candidate"
    return {
        **idea,
        "feasibility_score": feasibility_score,
        "data_fit_score": data_fit_score,
        "novelty_score": novelty_score,
        "risk_score": risk_penalty,
        "matched_dataset_count": matches,
        "review_dataset_count": reviews,
        "execution_status": status,
        "blockers": blockers,
    }


def generate_idea_batch(project_dir: Path, interest: str, count: int, seed_ideas: list[dict] | None = None) -> list[dict]:
    raw_ideas = seed_ideas if seed_ideas is not None else _base_ideas(interest, count)
    ideas = [evaluate_idea(project_dir, _normalize_seed_idea(idea, interest, idx)) for idx, idea in enumerate(raw_ideas, 1)]
    ideas.sort(key=lambda row: (-row["feasibility_score"], row["idea_id"]))
    out_dir = project_dir / "results" / "ideas"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "idea_batch.json"
    csv_path = out_dir / "idea_batch.csv"
    json_path.write_text(json.dumps(ideas, indent=2, ensure_ascii=False), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        fields = [
            "idea_id",
            "title",
            "route",
            "feasibility_score",
            "data_fit_score",
            "novelty_score",
            "risk_score",
            "execution_status",
            "blockers",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for idea in ideas:
            row = {field: idea.get(field, "") for field in fields}
            row["blockers"] = "; ".join(idea.get("blockers", []))
            writer.writerow(row)
    return ideas


def _normalize_seed_idea(idea: dict, interest: str, index: int) -> dict:
    title = str(idea.get("title") or f"Generated idea {index}").strip()
    route = str(idea.get("route") or "unknown").strip()
    rationale = str(idea.get("rationale") or "Generated for review.").strip()
    return {
        "idea_id": idea.get("idea_id") or _stable_id(f"{interest}:{title}:{route}", index),
        "title": title,
        "route": route,
        "research_prompt": interest.strip(),
        "rationale": rationale,
        "review_status": idea.get("review_status", "pending"),
    }


def load_ideas(project_dir: Path) -> list[dict]:
    path = project_dir / "results" / "ideas" / "idea_batch.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))
