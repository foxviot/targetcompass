import json
import re
from pathlib import Path

from .llm_parser import parse_with_openai


RULES = {
    "disease_scope": {
        "vascular aging": ["vascular aging", "arterial aging", "artery aging", "arterial ageing", "vascular ageing", "aorta aging", "vascular inflammation", "vascular inflammatory", "\u8840\u7ba1\u8870\u8001", "\u52a8\u8109\u8870\u8001"],
        "endothelial senescence": ["endothelial senescence", "senescent endothelial", "\u5185\u76ae\u8870\u8001", "\u5185\u76ae\u7ec6\u80de\u8870\u8001"],
        "atherosclerosis": ["atherosclerosis", "atherogenesis", "atheroma", "atherosclerotic plaque", "plaque progression", "\u52a8\u8109\u7ca5\u6837\u786c\u5316"],
        "pulmonary fibrosis": ["pulmonary fibrosis", "\u80ba\u7ea4\u7ef4\u5316"],
        "immune aging": ["immune aging", "immunosenescence", "\u514d\u75ab\u8870\u8001"],
    },
    "organisms": {
        "human": ["human", "homo sapiens", "patient", "patients", "\u4eba", "\u4eba\u4f53", "\u60a3\u8005", "\u4eba\u7c7b"],
        "mouse": ["mouse", "mice", "murine", "\u5c0f\u9f20"],
    },
    "priority_tissues": {
        "artery": ["artery", "arterial", "aorta", "\u52a8\u8109", "\u4e3b\u52a8\u8109"],
        "vascular endothelium": ["endothelium", "endothelial", "huvec", "\u5185\u76ae"],
        "blood": ["blood", "pbmc", "plasma", "serum", "\u8840\u6db2", "\u5916\u5468\u8840"],
        "lung": ["lung", "pulmonary", "\u80ba"],
        "heart": ["heart", "cardiac", "myocardial", "\u5fc3\u810f", "\u5fc3\u808c"],
        "brain": ["brain", "cerebrovascular", "\u8111", "\u8111\u8840\u7ba1"],
    },
    "priority_cells": {
        "endothelial cell": ["endothelial cell", "endothelium", "huvec", "\u5185\u76ae\u7ec6\u80de"],
        "vascular smooth muscle cell": ["smooth muscle", "vsmc", "\u5e73\u6ed1\u808c"],
        "monocyte": ["monocyte", "\u5355\u6838"],
        "macrophage": ["macrophage", "\u5de8\u566c"],
        "T cell": ["t cell", "t-cell", "t\u7ec6\u80de", "T\u7ec6\u80de"],
        "fibroblast": ["fibroblast", "\u6210\u7ea4\u7ef4"],
    },
    "target_routes": {
        "surface": ["surface", "membrane", "cell surface", "\u819c\u86cb\u767d", "\u8868\u9762"],
        "secreted": ["secreted", "cytokine", "chemokine", "secretome", "\u5206\u6ccc", "\u7ec6\u80de\u56e0\u5b50", "\u8d8b\u5316\u56e0\u5b50"],
        "ECD": ["extracellular domain", "ecd", "\u80de\u5916\u7ed3\u6784\u57df"],
        "T_cell_peptide": ["peptide", "epitope", "t cell peptide", "\u8868\u4f4d", "\u80bd"],
    },
}


DEFAULTS = {
    "goal": "vaccine_candidate_target_prioritization",
    "organisms": ["human", "mouse"],
    "priority_tissues": ["artery", "vascular endothelium", "blood"],
    "priority_cells": ["endothelial cell", "monocyte", "macrophage", "T cell"],
    "target_routes": ["surface", "secreted", "ECD", "T_cell_peptide"],
}


def _contains(text: str, pattern: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", pattern):
        return pattern in text
    return re.search(rf"(?<![a-z0-9]){re.escape(pattern)}(?![a-z0-9])", text) is not None


def _matches(text: str, category: str) -> list[str]:
    found = []
    for value, patterns in RULES[category].items():
        if any(_contains(text, pattern.lower()) for pattern in patterns):
            found.append(value)
    return found


def parse_interest(interest: str, project_id: str, parser: str = "rule_based") -> dict:
    if parser == "gpt":
        return parse_with_openai(interest, project_id)
    text = interest.strip()
    lowered = text.lower()
    disease_matches = _matches(lowered, "disease_scope")
    organisms = _matches(lowered, "organisms") or DEFAULTS["organisms"]
    tissues = _matches(lowered, "priority_tissues") or DEFAULTS["priority_tissues"]
    cells = _matches(lowered, "priority_cells") or DEFAULTS["priority_cells"]
    routes = _matches(lowered, "target_routes") or DEFAULTS["target_routes"]
    canonical = disease_matches[0] if disease_matches else "unknown"
    confidence = "medium" if disease_matches else "low"
    if disease_matches and (tissues or cells):
        confidence = "high"
    unmatched_terms = []
    if canonical == "unknown":
        unmatched_terms.append("disease_scope")
    return {
        "project_id": project_id,
        "goal": DEFAULTS["goal"],
        "research_theme": text.splitlines()[0][:200] if text else "unknown",
        "disease_scope": {
            "canonical": canonical,
            "related_phenotypes": disease_matches[1:],
        },
        "organisms": organisms,
        "priority_tissues": tissues,
        "priority_cells": cells,
        "target_routes": routes,
        "modalities_mvp": {
            "required": ["bulk_expression", "accessibility_annotation", "safety_annotation"],
            "optional": ["enrichment", "manual_genetic_evidence"],
        },
        "constraints": {
            "causal_requirement": "preferred_not_mandatory",
            "critical_normal_tissues": ["brain", "heart", "liver", "kidney", "hematopoietic_stem_cell"],
            "claim_policy": "association_only_without_genetic_or_experimental_validation",
        },
        "parser_metadata": {
            "parser_version": "rule_based_v0",
            "confidence": confidence,
            "unmatched_terms": unmatched_terms,
            "confirmation_required": False,
            "confirmed": True,
        },
    }


def readiness_errors(spec: dict) -> list[str]:
    errors = []
    disease = spec.get("disease_scope", {}).get("canonical", "unknown")
    confidence = spec.get("parser_metadata", {}).get("confidence", "unknown")
    metadata = spec.get("parser_metadata", {})
    if metadata.get("confirmation_required") and not metadata.get("confirmed"):
        errors.append("ResearchSpec requires user confirmation before running.")
    if disease == "unknown":
        errors.append("Research direction did not identify a supported disease or phenotype.")
    if confidence in {"low", "requires_user_review"}:
        errors.append("Research direction parsing confidence is low.")
    if not spec.get("research_theme") or spec.get("research_theme") == "unknown":
        errors.append("Research theme is empty.")
    return errors


def update_project_spec(project_dir: Path, interest: str, parser: str = "rule_based", confirmed: bool = False) -> dict:
    spec = parse_interest(interest, project_dir.name, parser)
    if parser == "gpt" and confirmed:
        spec.setdefault("parser_metadata", {})["confirmed"] = True
        spec["parser_metadata"]["confidence"] = "user_confirmed"
    (project_dir / "research_interest.md").write_text(interest.strip() + "\n", encoding="utf-8")
    (project_dir / "research_spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    return spec


def confirm_project_spec(project_dir: Path) -> dict:
    spec_path = project_dir / "research_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    spec.setdefault("parser_metadata", {})["confirmed"] = True
    if spec["parser_metadata"].get("confidence") == "requires_user_review":
        spec["parser_metadata"]["confidence"] = "user_confirmed"
    spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    return spec
