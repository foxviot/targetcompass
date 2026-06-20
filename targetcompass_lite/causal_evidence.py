import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .paths import KB
from .v4 import content_hash, file_hash


DEFAULT_CAUSAL_RUBRIC = KB / "rubrics" / "causal_review_v0.json"
PROJECT_CAUSAL_RUBRIC = Path("configs") / "causal_review_rubric.json"


FALLBACK_CAUSAL_RUBRIC = {
    "schema_version": "v4.causal_review_rubric/0.1",
    "rubric_id": "causal_review",
    "version": "0.1.0",
    "recognized_evidence_types": {
        "gwas_association": "association",
        "qtl_colocalization": "coloc",
        "eqtl_colocalization": "coloc",
        "pqtl_colocalization": "coloc",
        "mendelian_randomization": "mr",
        "opentargets_association": "association",
    },
    "grade_rules": [
        {"grade": "A", "requires_methods": ["mr", "coloc"], "rationale": "MR and colocalization evidence are both present."},
        {"grade": "B", "requires_methods": ["coloc"], "rationale": "Colocalization evidence is present; review locus and QTL context."},
        {
            "grade": "B",
            "requires_methods": ["association"],
            "max_best_p_value": 5e-8,
            "rationale": "Genome-wide significant association-level evidence is present.",
        },
        {
            "grade": "C",
            "requires_any_recognized_evidence": True,
            "rationale": "Only association-level or database genetic evidence is available.",
        },
        {"grade": "D", "default": True, "rationale": "No recognized genetic causal evidence."},
    ],
    "support_levels": {"A": "triage_high", "B": "triage_moderate", "C": "triage_low", "D": "insufficient"},
    "review_flags": {
        "base": ["human_review_required"],
        "method_flags": {
            "mr": ["pleiotropy_review_required", "instrument_strength_review_required"],
            "coloc": ["ld_locus_review_required", "qtl_context_review_required"],
            "association": ["locus_to_gene_mapping_review_required"],
        },
        "method_without_method": [{"method": "mr", "without": "coloc", "flag": "mr_without_coloc"}],
        "limitation_keywords": [
            {"keywords": ["single-variant", "single variant"], "flag": "single_variant_mr_proxy"},
            {"keywords": ["ld-aware", "ld"], "flag": "ld_aware_method_required"},
        ],
    },
    "review_policy": "All automated causal grades require human/statistical review before scientific claims.",
    "limitation": "Automated causal grade is a triage label; locus mapping, LD, pleiotropy, and ancestry matching require human/statistical review.",
}


def grade_causal_evidence(project_dir: Path) -> Path:
    rubric, rubric_meta = load_causal_review_rubric(project_dir)
    evidence_type_map = rubric["recognized_evidence_types"]
    genetic_context = _load_genetic_context(project_dir)
    db = project_dir / "evidence.sqlite"
    if not db.exists():
        raise ValueError("evidence.sqlite is required before causal grading")
    con = sqlite3.connect(db, timeout=30)
    con.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in evidence_type_map)
    rows = [
        dict(row)
        for row in con.execute(
            f"SELECT * FROM evidence_item WHERE evidence_type IN ({placeholders}) ORDER BY entity_symbol, evidence_type",
            tuple(evidence_type_map),
        ).fetchall()
    ]
    con.close()
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("evidence_type") in evidence_type_map:
            grouped[row["entity_symbol"]].append(row)
    out_dir = project_dir / "results" / "causal_evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "causal_evidence_grades.tsv"
    grades = []
    for gene, evidences in grouped.items():
        evidence_types = {row["evidence_type"] for row in evidences}
        methods = {evidence_type_map[row["evidence_type"]] for row in evidences}
        best_p = _best_p_value(evidences)
        triage_grade, triage_rationale = _grade(methods, evidence_types, best_p, rubric)
        causal_level, support_level, rationale, bias_flags = _causal_level(gene, evidences, methods, best_p, genetic_context)
        review_flags = _review_flags(evidences, methods, rubric)
        review_flags = sorted(set(review_flags + bias_flags))
        grades.append(
            {
                "gene_symbol": gene,
                "causal_grade": causal_level,
                "causal_support_level": causal_level,
                "triage_grade": triage_grade,
                "support_level": support_level,
                "methods": ";".join(sorted(methods)),
                "evidence_types": ";".join(sorted(evidence_types)),
                "evidence_count": len(evidences),
                "best_p_value": "" if best_p is None else f"{best_p:.6g}",
                "rationale": rationale,
                "triage_rationale": triage_rationale,
                "bias_flags": ";".join(bias_flags) or "none",
                "evidence_ids": ";".join(sorted({row.get("evidence_id", "") for row in evidences if row.get("evidence_id")})),
                "artifact_refs": ";".join(sorted({_artifact_ref(row) for row in evidences if row.get("artifact_path")})),
                "review_flags": ";".join(review_flags) or "none",
                "review_status": "HUMAN_REVIEW_REQUIRED",
                "limitation": rubric.get(
                    "limitation",
                    "Automated causal grade is a triage label; locus mapping, LD, pleiotropy, and ancestry matching require human/statistical review.",
                ),
            }
        )
    grades.sort(key=lambda row: (row["causal_grade"], row["gene_symbol"]))
    fields = [
        "gene_symbol",
        "causal_grade",
        "causal_support_level",
        "triage_grade",
        "support_level",
        "methods",
        "evidence_types",
        "evidence_count",
        "best_p_value",
        "rationale",
        "triage_rationale",
        "bias_flags",
        "evidence_ids",
        "artifact_refs",
        "review_flags",
        "review_status",
        "limitation",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(grades)
    manifest = {
        "schema_version": "v4.causal_evidence_manifest/0.2",
        "module_id": "causal_evidence_grading_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "graded_genes": len(grades),
        "output": str(out.relative_to(project_dir)),
        "causal_level_policy": {
            "C4": "Multi-source genetic support with coloc and MR, acceptable sensitivity, and no major bias flags.",
            "C3": "Coloc or robust cis-MR support with reviewable sensitivity.",
            "C2": "QTL/V2G/GWAS support with partial direction or sensitivity support.",
            "C1": "Association or mapping-only genetic evidence.",
            "C0": "No reliable genetic causal support or blocked genetics route.",
        },
        "rubric_id": rubric.get("rubric_id", ""),
        "rubric_version": rubric.get("version", ""),
        "rubric_path": rubric_meta["path"],
        "rubric_hash": rubric_meta["hash"],
        "rubric_file_hash": rubric_meta["file_hash"],
        "grade_policy": _grade_policy_summary(rubric),
        "review_policy": rubric.get("review_policy", "All automated causal grades require human/statistical review before scientific claims."),
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _load_genetic_context(project_dir: Path) -> dict[str, dict[str, list[dict[str, str]]]]:
    base = project_dir / "results" / "genetic_coloc_mr"
    return {
        "coloc": _group_by_gene(_read_tsv(base / "coloc_results.tsv")),
        "mr": _group_by_gene(_read_tsv(base / "mr_results.tsv")),
        "sensitivity": _group_by_gene(_read_tsv(base / "sensitivity_summary.tsv")),
    }


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _group_by_gene(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get("gene_symbol", "")].append(row)
    return grouped


def _causal_level(gene: str, evidences: list[dict], methods: set[str], best_p: float | None, context: dict) -> tuple[str, str, str, list[str]]:
    coloc_rows = context.get("coloc", {}).get(gene, [])
    mr_rows = context.get("mr", {}).get(gene, [])
    sensitivity_rows = context.get("sensitivity", {}).get(gene, [])
    flags = set()
    for row in [*coloc_rows, *mr_rows, *sensitivity_rows]:
        for key in ["bias_flags", "sensitivity_flags", "flags"]:
            value = row.get(key, "")
            if value and value != "none":
                flags.update(flag for flag in value.split(";") if flag)
    max_pp_h4 = max([_safe_float(row.get("posterior_shared_signal")) or 0 for row in coloc_rows] or [0])
    max_f = max([_safe_float(row.get("instrument_f_stat")) or 0 for row in mr_rows] or [0])
    has_mr = "mr" in methods or bool(mr_rows)
    has_coloc = "coloc" in methods or bool(coloc_rows)
    has_assoc = "association" in methods
    major_bias = bool(flags & {"opposite_direction", "weak_instrument_f_lt_10", "zero_qtl_beta", "invalid_wald_ratio"})
    if has_coloc and has_mr and max_pp_h4 >= 0.7 and max_f >= 10 and not major_bias:
        return "C4", "genetic_strong", "Coloc and MR support are both present with acceptable proxy sensitivity.", sorted(flags)
    if (has_coloc and max_pp_h4 >= 0.5 and not major_bias) or (has_mr and max_f >= 10 and not major_bias):
        return "C3", "genetic_moderate", "Coloc or cis-MR support is present and sensitivity flags are reviewable.", sorted(flags)
    if has_coloc or has_mr or (has_assoc and best_p is not None and best_p < 5e-8):
        return "C2", "genetic_limited", "Genetic support is present but sensitivity, LD, or method completeness limits interpretation.", sorted(flags)
    if has_assoc:
        return "C1", "genetic_mapping_only", "Association or database genetic evidence is present without reliable coloc/MR support.", sorted(flags)
    return "C0", "genetic_insufficient", "No reliable genetic causal support is available.", sorted(flags)


def _safe_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def load_causal_review_rubric(project_dir: Path) -> tuple[dict, dict]:
    project_path = project_dir / PROJECT_CAUSAL_RUBRIC
    if project_path.exists():
        path = project_path
        rubric = json.loads(path.read_text(encoding="utf-8"))
        file_digest = file_hash(path)
    elif DEFAULT_CAUSAL_RUBRIC.exists():
        path = DEFAULT_CAUSAL_RUBRIC
        rubric = json.loads(path.read_text(encoding="utf-8"))
        file_digest = file_hash(path)
    else:
        path = DEFAULT_CAUSAL_RUBRIC
        rubric = dict(FALLBACK_CAUSAL_RUBRIC)
        file_digest = hashlib.sha256(json.dumps(rubric, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    _validate_rubric(rubric)
    meta = {
        "path": str(path),
        "hash": content_hash(rubric),
        "file_hash": file_digest,
    }
    return rubric, meta


def _validate_rubric(rubric: dict) -> None:
    required = ["recognized_evidence_types", "grade_rules", "support_levels", "review_flags"]
    missing = [key for key in required if key not in rubric]
    if missing:
        raise ValueError(f"causal review rubric missing sections: {', '.join(missing)}")
    if not isinstance(rubric["recognized_evidence_types"], dict) or not rubric["recognized_evidence_types"]:
        raise ValueError("causal review rubric requires recognized_evidence_types")
    if not isinstance(rubric["grade_rules"], list) or not rubric["grade_rules"]:
        raise ValueError("causal review rubric requires grade_rules")


def _grade(methods: set[str], evidence_types: set[str], best_p: float | None, rubric: dict) -> tuple[str, str]:
    default_rule = None
    for rule in rubric["grade_rules"]:
        if rule.get("default"):
            default_rule = rule
            continue
        if not _rule_matches(rule, methods, evidence_types, best_p):
            continue
        return rule.get("grade", "D"), rule.get("rationale", "")
    if default_rule:
        return default_rule.get("grade", "D"), default_rule.get("rationale", "")
    return "D", "No recognized genetic causal evidence."


def _rule_matches(rule: dict, methods: set[str], evidence_types: set[str], best_p: float | None) -> bool:
    required_methods = set(rule.get("requires_methods", []))
    if required_methods and not required_methods.issubset(methods):
        return False
    any_methods = set(rule.get("requires_any_method", []))
    if any_methods and not (any_methods & methods):
        return False
    required_evidence_types = set(rule.get("requires_evidence_types", []))
    if required_evidence_types and not required_evidence_types.issubset(evidence_types):
        return False
    if rule.get("requires_any_recognized_evidence") and not evidence_types:
        return False
    if "max_best_p_value" in rule:
        if best_p is None or best_p >= float(rule["max_best_p_value"]):
            return False
    if "min_best_p_value" in rule:
        if best_p is None or best_p <= float(rule["min_best_p_value"]):
            return False
    return True


def _best_p_value(evidences: list[dict]) -> float | None:
    values = []
    for row in evidences:
        value = row.get("p_value")
        if value in (None, ""):
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return min(values) if values else None


def _support_level(grade: str, rubric: dict) -> str:
    return rubric.get("support_levels", {}).get(grade, "insufficient")


def _review_flags(evidences: list[dict], methods: set[str], rubric: dict) -> list[str]:
    flag_rules = rubric.get("review_flags", {})
    flags = set(flag_rules.get("base", []))
    for method in methods:
        flags.update(flag_rules.get("method_flags", {}).get(method, []))
    for rule in flag_rules.get("method_without_method", []):
        if rule.get("method") in methods and rule.get("without") not in methods:
            flags.add(rule.get("flag", ""))
    for row in evidences:
        limitation = (row.get("limitation") or "").lower()
        for rule in flag_rules.get("limitation_keywords", []):
            keywords = [str(keyword).lower() for keyword in rule.get("keywords", [])]
            if any(keyword in limitation for keyword in keywords):
                flags.add(rule.get("flag", ""))
    return sorted(flag for flag in flags if flag)


def _grade_policy_summary(rubric: dict) -> dict:
    summary = {}
    for rule in rubric.get("grade_rules", []):
        grade = rule.get("grade", "")
        rationale = rule.get("rationale", "")
        if grade and rationale:
            summary.setdefault(grade, rationale)
    return summary


def _artifact_ref(row: dict) -> str:
    return str(row.get("artifact_path", "")).replace("\\", "/")
