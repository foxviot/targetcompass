import csv
import json
from pathlib import Path

from .validators import load_dataset_card


TISSUE_SYNONYMS = {
    "vascular endothelium": {"vascular endothelium", "endothelium", "endothelial", "huvec"},
    "artery": {"artery", "arterial", "aorta"},
    "blood": {"blood", "pbmc", "plasma", "serum"},
    "lung": {"lung", "pulmonary"},
    "heart": {"heart", "cardiac", "myocardial"},
    "brain": {"brain", "cerebrovascular"},
}

DISEASE_CONTRAST_TERMS = {
    "vascular aging": {"aged", "aging", "old", "senescence", "replicative_senescence"},
    "endothelial senescence": {"senescence", "replicative_senescence", "premature_senescence", "trf2dn"},
    "atherosclerosis": {"atherosclerosis", "atherogenic", "hgps", "progeria"},
    "pulmonary fibrosis": {"fibrosis", "ipf", "mir-205-5p"},
    "immune aging": {"aged", "aging", "immunosenescence"},
}


def _norm(value) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _contains_any(value: str, terms: set[str]) -> bool:
    normalized = _norm(value)
    return any(term in normalized for term in terms)


def _tissue_match(card_tissue: str, spec_tissues: list[str]) -> tuple[bool, str]:
    raw = str(card_tissue or "").lower()
    for tissue in spec_tissues:
        terms = TISSUE_SYNONYMS.get(tissue, {tissue.lower()})
        if any(term in raw for term in terms):
            return True, tissue
    return False, ""


def match_card_to_spec(card: dict, spec: dict) -> dict:
    reasons = []
    warnings = []
    score = 0

    spec_organisms = {o.lower() for o in spec.get("organisms", [])}
    organism = str(card.get("organism", "")).lower()
    if organism in spec_organisms:
        score += 30
        reasons.append(f"organism matches: {card.get('organism')}")
    else:
        warnings.append(f"organism mismatch: dataset={card.get('organism')} spec={','.join(sorted(spec_organisms))}")

    tissue_ok, tissue = _tissue_match(card.get("tissue", ""), spec.get("priority_tissues", []))
    if tissue_ok:
        score += 25
        reasons.append(f"tissue matches: {card.get('tissue')} -> {tissue}")
    else:
        warnings.append(f"tissue not matched: {card.get('tissue')}")

    disease = spec.get("disease_scope", {}).get("canonical", "unknown")
    contrast = card.get("contrast", {})
    contrast_text = " ".join([str(contrast.get("case", "")), str(contrast.get("control", "")), " ".join(card.get("known_limitations", []))])
    terms = DISEASE_CONTRAST_TERMS.get(disease, set())
    if terms and _contains_any(contrast_text, terms):
        score += 25
        reasons.append(f"contrast is compatible with {disease}")
    elif disease == "unknown":
        warnings.append("research disease is unknown")
    else:
        warnings.append(f"contrast may not represent {disease}: {contrast.get('case')} vs {contrast.get('control')}")

    if card.get("modality") in spec.get("modalities_mvp", {}).get("required", []):
        score += 20
        reasons.append(f"required modality available: {card.get('modality')}")
    elif card.get("modality") == "bulk_expression" and "bulk_expression" in spec.get("modalities_mvp", {}).get("required", []):
        score += 20
        reasons.append("required modality available: bulk_expression")
    else:
        warnings.append(f"modality not required by spec: {card.get('modality')}")

    if score >= 75 and not warnings:
        status = "MATCH"
    elif score >= 50:
        status = "REVIEW"
    else:
        status = "LOW_MATCH"

    return {
        "dataset_id": card.get("dataset_id", "unknown"),
        "match_status": status,
        "match_score": score,
        "reasons": "; ".join(reasons),
        "warnings": "; ".join(warnings),
    }


def match_project(project_dir: Path, selected_ids: set[str] | None = None) -> list[dict]:
    spec = json.loads((project_dir / "research_spec.json").read_text(encoding="utf-8"))
    rows = []
    for card_path in sorted((project_dir / "dataset_cards").glob("*.yaml")):
        if selected_ids is not None and card_path.stem not in selected_ids:
            continue
        rows.append(match_card_to_spec(load_dataset_card(card_path), spec))

    csv_path = project_dir / "dataset_match_report.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["dataset_id", "match_status", "match_score", "reasons", "warnings"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    md_path = project_dir / "dataset_match_report.md"
    with md_path.open("w", encoding="utf-8") as f:
        f.write("# Dataset / ResearchSpec Match Report\n\n")
        for row in rows:
            f.write(f"## {row['dataset_id']}\n")
            f.write(f"- Status: {row['match_status']}\n")
            f.write(f"- Score: {row['match_score']}\n")
            f.write(f"- Reasons: {row['reasons'] or 'none'}\n")
            f.write(f"- Warnings: {row['warnings'] or 'none'}\n\n")
    return rows
