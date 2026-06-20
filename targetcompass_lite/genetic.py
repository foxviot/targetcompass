import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def run_genetic_coloc_mr(project_dir: Path, gwas_summary: str, qtl_summary: str, dataset_id: str = "genetic") -> Path:
    gwas_rows = _read_rows(project_dir / gwas_summary)
    qtl_rows = _read_rows(project_dir / qtl_summary)
    qtl_index = {(row.get("variant_id", ""), row.get("gene_symbol", "")): row for row in qtl_rows}
    out_dir = project_dir / "results" / "genetic_coloc_mr"
    out_dir.mkdir(parents=True, exist_ok=True)
    harmonized = []
    evidence = []
    sensitivity = []
    for gwas in gwas_rows:
        key = (gwas.get("variant_id", ""), gwas.get("gene_symbol", ""))
        qtl = qtl_index.get(key)
        if not qtl:
            continue
        gene = gwas.get("gene_symbol", "")
        beta_gwas = _float(gwas.get("beta"))
        beta_qtl = _float(qtl.get("beta"))
        p_gwas = _float(gwas.get("p_value"))
        p_qtl = _float(qtl.get("p_value"))
        if beta_gwas is None or beta_qtl is None or p_gwas is None or p_qtl is None:
            continue
        same_direction = (beta_gwas >= 0 and beta_qtl >= 0) or (beta_gwas < 0 and beta_qtl < 0)
        coloc_score = _coloc_score(p_gwas, p_qtl, same_direction)
        wald_ratio = beta_gwas / beta_qtl if beta_qtl else None
        mr_score = _mr_score(p_gwas, p_qtl, same_direction, wald_ratio)
        harmonized.append(
            {
                "variant_id": key[0],
                "gene_symbol": gene,
                "effect_allele": gwas.get("effect_allele", ""),
                "other_allele": gwas.get("other_allele", ""),
                "gwas_beta": f"{beta_gwas:.6g}",
                "qtl_beta": f"{beta_qtl:.6g}",
                "gwas_p_value": f"{p_gwas:.6g}",
                "qtl_p_value": f"{p_qtl:.6g}",
                "same_direction": str(same_direction),
                "trait": gwas.get("trait", ""),
                "qtl_context": qtl.get("tissue", qtl.get("cell_type", "")),
            }
        )
        evidence.append(
            {
                "entity_symbol": gene,
                "evidence_type": "qtl_colocalization",
                "direction": "same_direction" if same_direction else "opposite_direction",
                "effect_size": f"{coloc_score:.6g}",
                "p_value": f"{max(p_gwas, p_qtl):.6g}",
                "quality_score": f"{coloc_score:.3f}",
                "source_dataset": dataset_id,
                "module_version": "genetic_coloc_mr_v1",
                "limitation": "Lightweight coloc proxy from harmonized GWAS/QTL summary rows; formal LD-aware coloc is required for publication-grade inference.",
            }
        )
        if wald_ratio is not None:
            evidence.append(
                {
                    "entity_symbol": gene,
                    "evidence_type": "mendelian_randomization",
                    "direction": "risk_increasing" if wald_ratio > 0 else "risk_decreasing",
                    "effect_size": f"{wald_ratio:.6g}",
                    "p_value": f"{max(p_gwas, p_qtl):.6g}",
                    "quality_score": f"{mr_score:.3f}",
                    "source_dataset": dataset_id,
                    "module_version": "genetic_coloc_mr_v1",
                    "limitation": "Single-variant Wald-ratio MR proxy; requires instrument strength, pleiotropy, LD, and sensitivity review.",
                }
            )
        sensitivity.append(
            {
                "gene_symbol": gene,
                "variant_id": key[0],
                "same_direction": str(same_direction),
                "coloc_score": f"{coloc_score:.6g}",
                "wald_ratio": "" if wald_ratio is None else f"{wald_ratio:.6g}",
                "mr_score": f"{mr_score:.6g}",
                "flags": _flags(p_gwas, p_qtl, same_direction, beta_qtl),
            }
        )
    harmonized_path = out_dir / "harmonized_gwas_qtl.tsv"
    evidence_path = out_dir / "genetic_evidence.tsv"
    sensitivity_path = out_dir / "sensitivity_summary.tsv"
    _write_tsv(harmonized_path, harmonized, ["variant_id", "gene_symbol", "effect_allele", "other_allele", "gwas_beta", "qtl_beta", "gwas_p_value", "qtl_p_value", "same_direction", "trait", "qtl_context"])
    _write_tsv(evidence_path, evidence, ["entity_symbol", "evidence_type", "direction", "effect_size", "p_value", "quality_score", "source_dataset", "module_version", "limitation"])
    _write_tsv(sensitivity_path, sensitivity, ["gene_symbol", "variant_id", "same_direction", "coloc_score", "wald_ratio", "mr_score", "flags"])
    manifest = {
        "schema_version": "v4.genetic_coloc_mr_manifest/0.1",
        "module_id": "genetic_coloc_mr_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {"gwas_summary": gwas_summary, "qtl_summary": qtl_summary},
        "dataset_id": dataset_id,
        "gwas_rows": len(gwas_rows),
        "qtl_rows": len(qtl_rows),
        "harmonized_rows": len(harmonized),
        "evidence_rows": len(evidence),
        "outputs": {
            "harmonized": str(harmonized_path.relative_to(project_dir)),
            "evidence": str(evidence_path.relative_to(project_dir)),
            "sensitivity": str(sensitivity_path.relative_to(project_dir)),
        },
        "limitations": [
            "This runner is a minimal local proxy and not a replacement for LD-aware coloc/MR packages.",
            "Use as an engineering contract and triage signal before formal statistical genetics review.",
        ],
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return evidence_path


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    required = {"variant_id", "gene_symbol", "beta", "p_value"}
    if not rows:
        raise ValueError(f"{path} has no data rows")
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(f"{path} missing required columns: {', '.join(sorted(missing))}")
    return rows


def _write_tsv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def _coloc_score(p_gwas: float, p_qtl: float, same_direction: bool) -> float:
    evidence_strength = min(1.0, (-math.log10(max(p_gwas, 1e-300)) + -math.log10(max(p_qtl, 1e-300))) / 20)
    return max(0.0, evidence_strength if same_direction else evidence_strength * 0.25)


def _mr_score(p_gwas: float, p_qtl: float, same_direction: bool, wald_ratio: float | None) -> float:
    if wald_ratio is None:
        return 0.0
    base = _coloc_score(p_gwas, p_qtl, same_direction)
    return min(1.0, base * 0.85)


def _flags(p_gwas: float, p_qtl: float, same_direction: bool, beta_qtl: float) -> str:
    flags = []
    if p_gwas >= 5e-8:
        flags.append("gwas_not_genome_wide_significant")
    if p_qtl >= 1e-5:
        flags.append("qtl_weak")
    if not same_direction:
        flags.append("opposite_direction")
    if beta_qtl == 0:
        flags.append("zero_qtl_beta")
    return ";".join(flags) or "none"
