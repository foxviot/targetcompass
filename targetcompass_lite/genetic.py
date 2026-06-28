import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path


def run_genetic_coloc_mr(project_dir: Path, gwas_summary: str, qtl_summary: str, dataset_id: str = "genetic", ld_reference: str = "") -> Path:
    gwas_path = project_dir / gwas_summary
    qtl_path = project_dir / qtl_summary
    gwas_rows = _read_rows(gwas_path, "gwas")
    qtl_rows = _read_rows(qtl_path, "qtl")
    qtl_index = {(row.get("variant_id", ""), row.get("gene_symbol", "")): row for row in qtl_rows}
    out_dir = project_dir / "results" / "genetic_coloc_mr"
    out_dir.mkdir(parents=True, exist_ok=True)
    gwas_standard = []
    qtl_standard = []
    harmonized = []
    coloc_results = []
    mr_results = []
    evidence = []
    sensitivity = []
    dropped = []
    for row in gwas_rows:
        gwas_standard.append(_standard_summary_row(row, "gwas", dataset_id))
    for row in qtl_rows:
        qtl_standard.append(_standard_summary_row(row, "qtl", dataset_id))
    for gwas in gwas_rows:
        key = (gwas.get("variant_id", ""), gwas.get("gene_symbol", ""))
        qtl = qtl_index.get(key)
        if not qtl:
            dropped.append({"variant_id": key[0], "gene_symbol": key[1], "reason": "no_matching_qtl"})
            continue
        gene = gwas.get("gene_symbol", "")
        beta_gwas = _float(gwas.get("beta"))
        beta_qtl = _float(qtl.get("beta"))
        p_gwas = _float(gwas.get("p_value"))
        p_qtl = _float(qtl.get("p_value"))
        if beta_gwas is None or beta_qtl is None or p_gwas is None or p_qtl is None:
            dropped.append({"variant_id": key[0], "gene_symbol": key[1], "reason": "missing_numeric_beta_or_p"})
            continue
        same_direction = (beta_gwas >= 0 and beta_qtl >= 0) or (beta_gwas < 0 and beta_qtl < 0)
        coloc_score = _coloc_score(p_gwas, p_qtl, same_direction)
        pp_h4 = coloc_score
        pp_h3 = min(1.0, (1.0 - pp_h4) * 0.35)
        wald_ratio = beta_gwas / beta_qtl if beta_qtl else None
        mr_score = _mr_score(p_gwas, p_qtl, same_direction, wald_ratio)
        flags = _flags(p_gwas, p_qtl, same_direction, beta_qtl)
        harmonized.append(
            {
                "variant_id": key[0],
                "gene_symbol": gene,
                "chromosome": gwas.get("chromosome", qtl.get("chromosome", "")),
                "position": gwas.get("position", qtl.get("position", "")),
                "effect_allele": gwas.get("effect_allele", ""),
                "other_allele": gwas.get("other_allele", ""),
                "gwas_beta": f"{beta_gwas:.6g}",
                "gwas_se": _fmt(_float(gwas.get("se"))),
                "qtl_beta": f"{beta_qtl:.6g}",
                "qtl_se": _fmt(_float(qtl.get("se"))),
                "gwas_p_value": f"{p_gwas:.6g}",
                "qtl_p_value": f"{p_qtl:.6g}",
                "same_direction": str(same_direction),
                "trait": gwas.get("trait", ""),
                "qtl_context": qtl.get("tissue", qtl.get("cell_type", "")),
                "harmonization_status": "pass",
            }
        )
        coloc_results.append(
            {
                "gene_symbol": gene,
                "variant_id": key[0],
                "method": "coloc_abf_lightweight",
                "posterior_shared_signal": f"{pp_h4:.6g}",
                "posterior_distinct_signal": f"{pp_h3:.6g}",
                "same_direction": str(same_direction),
                "ld_reference_id": _ld_reference_id(ld_reference),
                "qtl_context": qtl.get("tissue", qtl.get("cell_type", "")),
                "bias_flags": flags,
                "qc_status": "review" if flags != "none" else "pass",
                "limitation": "Proxy coloc score; formal coloc requires LD-aware regional summary statistics.",
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
                "limitation": f"posterior_shared_signal={pp_h4:.3f}; {flags}; formal LD-aware coloc is required for publication-grade inference.",
            }
        )
        if wald_ratio is not None:
            f_stat = (beta_qtl / (_float(qtl.get("se")) or max(abs(beta_qtl) / 3.0, 1e-6))) ** 2
            mr_flags = _mr_flags(flags, f_stat, wald_ratio)
            mr_results.append(
                {
                    "gene_symbol": gene,
                    "variant_id": key[0],
                    "method": "single_variant_wald_ratio",
                    "estimate": f"{wald_ratio:.6g}",
                    "standard_error": _fmt(_wald_se(beta_gwas, beta_qtl, _float(gwas.get("se")), _float(qtl.get("se")))),
                    "instrument_f_stat": f"{f_stat:.6g}",
                    "direction": "risk_increasing" if wald_ratio > 0 else "risk_decreasing",
                    "sensitivity_flags": mr_flags,
                    "qc_status": "review" if mr_flags != "none" else "pass",
                    "limitation": "Single-variant Wald-ratio MR proxy; multi-instrument sensitivity unavailable.",
                }
            )
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
                    "limitation": f"instrument_f_stat={f_stat:.3f}; {mr_flags}; single-variant Wald-ratio MR proxy requires review.",
                }
            )
        sensitivity.append(
            {
                "gene_symbol": gene,
                "variant_id": key[0],
                "same_direction": str(same_direction),
                "coloc_score": f"{coloc_score:.6g}",
                "posterior_shared_signal": f"{pp_h4:.6g}",
                "wald_ratio": "" if wald_ratio is None else f"{wald_ratio:.6g}",
                "mr_score": f"{mr_score:.6g}",
                "flags": flags,
                "leave_one_variant_out": "not_applicable_single_variant",
                "pleiotropy_test": "not_available_single_variant",
                "directionality_check": "pass" if same_direction else "review",
            }
        )
    gwas_standard_path = out_dir / "standard_gwas_summary.tsv"
    qtl_standard_path = out_dir / "standard_qtl_summary.tsv"
    harmonized_path = out_dir / "harmonized_gwas_qtl.tsv"
    coloc_path = out_dir / "coloc_results.tsv"
    mr_path = out_dir / "mr_results.tsv"
    evidence_path = out_dir / "genetic_evidence.tsv"
    sensitivity_path = out_dir / "sensitivity_summary.tsv"
    dropped_path = out_dir / "rejected_rows.tsv"
    ld_manifest_path = out_dir / "ld_reference_manifest.json"
    _write_tsv(gwas_standard_path, gwas_standard, _standard_fields())
    _write_tsv(qtl_standard_path, qtl_standard, _standard_fields())
    _write_tsv(
        harmonized_path,
        harmonized,
        ["variant_id", "gene_symbol", "chromosome", "position", "effect_allele", "other_allele", "gwas_beta", "gwas_se", "qtl_beta", "qtl_se", "gwas_p_value", "qtl_p_value", "same_direction", "trait", "qtl_context", "harmonization_status"],
    )
    _write_tsv(coloc_path, coloc_results, ["gene_symbol", "variant_id", "method", "posterior_shared_signal", "posterior_distinct_signal", "same_direction", "ld_reference_id", "qtl_context", "bias_flags", "qc_status", "limitation"])
    _write_tsv(mr_path, mr_results, ["gene_symbol", "variant_id", "method", "estimate", "standard_error", "instrument_f_stat", "direction", "sensitivity_flags", "qc_status", "limitation"])
    _write_tsv(evidence_path, evidence, ["entity_symbol", "evidence_type", "direction", "effect_size", "p_value", "quality_score", "source_dataset", "module_version", "limitation"])
    _write_tsv(sensitivity_path, sensitivity, ["gene_symbol", "variant_id", "same_direction", "coloc_score", "posterior_shared_signal", "wald_ratio", "mr_score", "flags", "leave_one_variant_out", "pleiotropy_test", "directionality_check"])
    _write_tsv(dropped_path, dropped, ["variant_id", "gene_symbol", "reason"])
    ld_manifest = _ld_reference_manifest(project_dir, ld_reference)
    ld_manifest_path.write_text(json.dumps(ld_manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    qc = {
        "schema_version": "v4.genetic_coloc_mr_qc/0.1",
        "status": "pass" if harmonized else "warning",
        "gwas_rows": len(gwas_rows),
        "qtl_rows": len(qtl_rows),
        "harmonized_rows": len(harmonized),
        "dropped_rows": len(dropped),
        "coloc_review_rows": sum(1 for row in coloc_results if row["qc_status"] == "review"),
        "mr_review_rows": sum(1 for row in mr_results if row["qc_status"] == "review"),
        "ld_reference_status": ld_manifest["status"],
    }
    qc_path = out_dir / "qc_summary.json"
    qc_path.write_text(json.dumps(qc, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "schema_version": "v4.genetic_coloc_mr_manifest/0.2",
        "module_id": "genetic_coloc_mr_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "gwas_summary": gwas_summary,
            "qtl_summary": qtl_summary,
            "gwas_summary_hash": _file_hash(gwas_path),
            "qtl_summary_hash": _file_hash(qtl_path),
        },
        "dataset_id": dataset_id,
        "schemas": {
            "standard_summary": "v4.genetic_summary_schema/0.1",
            "harmonized": "v4.harmonized_gwas_qtl/0.2",
            "coloc_result": "v4.coloc_result/0.1",
            "mr_result": "v4.mr_result/0.1",
            "sensitivity": "v4.genetic_sensitivity/0.2",
        },
        "ld_reference": ld_manifest,
        "gwas_rows": len(gwas_rows),
        "qtl_rows": len(qtl_rows),
        "harmonized_rows": len(harmonized),
        "coloc_rows": len(coloc_results),
        "mr_rows": len(mr_results),
        "evidence_rows": len(evidence),
        "qc": qc,
        "outputs": {
            "standard_gwas": str(gwas_standard_path.relative_to(project_dir)),
            "standard_qtl": str(qtl_standard_path.relative_to(project_dir)),
            "harmonized": str(harmonized_path.relative_to(project_dir)),
            "coloc": str(coloc_path.relative_to(project_dir)),
            "mr": str(mr_path.relative_to(project_dir)),
            "evidence": str(evidence_path.relative_to(project_dir)),
            "sensitivity": str(sensitivity_path.relative_to(project_dir)),
            "rejected_rows": str(dropped_path.relative_to(project_dir)),
            "ld_reference_manifest": str(ld_manifest_path.relative_to(project_dir)),
            "qc_summary": str((out_dir / "qc_summary.json").relative_to(project_dir)),
        },
        "limitations": [
            "This runner implements a local standard contract and lightweight proxies, not publication-grade LD-aware coloc/MR.",
            "LD reference is recorded as a contract placeholder unless a project-approved reference path is supplied.",
            "Use as an engineering contract and triage signal before formal statistical genetics review.",
        ],
    }
    run_manifest_path = out_dir / "run_manifest.json"
    run_manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [
                gwas_standard_path,
                qtl_standard_path,
                harmonized_path,
                coloc_path,
                mr_path,
                evidence_path,
                sensitivity_path,
                dropped_path,
                ld_manifest_path,
                qc_path,
                run_manifest_path,
            ],
            producer="genetic_coloc_mr",
            artifact_type="genetic_coloc_mr_output",
            task_id="genetic_coloc_mr",
            qc_status=qc.get("status", "pass"),
        )
    except Exception:
        pass
    return evidence_path


def _read_rows(path: Path, kind: str) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    required = {"variant_id", "gene_symbol", "beta", "p_value"}
    if not rows:
        raise ValueError(f"{path} has no data rows")
    missing = required - set(rows[0].keys())
    if missing:
        raise ValueError(f"{path} missing required columns: {', '.join(sorted(missing))}")
    for idx, row in enumerate(rows, 2):
        if _float(row.get("p_value")) is None:
            raise ValueError(f"{path} row {idx} has invalid p_value for {kind}")
        if _float(row.get("beta")) is None:
            raise ValueError(f"{path} row {idx} has invalid beta for {kind}")
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


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:.6g}"


def _standard_fields() -> list[str]:
    return [
        "schema_version",
        "dataset_id",
        "summary_type",
        "variant_id",
        "gene_symbol",
        "chromosome",
        "position",
        "effect_allele",
        "other_allele",
        "beta",
        "standard_error",
        "p_value",
        "sample_size",
        "trait_or_molecular_context",
        "ancestry",
        "build",
    ]


def _standard_summary_row(row: dict[str, str], summary_type: str, dataset_id: str) -> dict[str, str]:
    return {
        "schema_version": "v4.genetic_summary_schema/0.1",
        "dataset_id": dataset_id,
        "summary_type": summary_type,
        "variant_id": row.get("variant_id", ""),
        "gene_symbol": row.get("gene_symbol", ""),
        "chromosome": row.get("chromosome", ""),
        "position": row.get("position", ""),
        "effect_allele": row.get("effect_allele", ""),
        "other_allele": row.get("other_allele", ""),
        "beta": row.get("beta", ""),
        "standard_error": row.get("se", row.get("standard_error", "")),
        "p_value": row.get("p_value", ""),
        "sample_size": row.get("sample_size", row.get("n", "")),
        "trait_or_molecular_context": row.get("trait", row.get("tissue", row.get("cell_type", ""))),
        "ancestry": row.get("ancestry", ""),
        "build": row.get("build", ""),
    }


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


def _mr_flags(base_flags: str, f_stat: float, wald_ratio: float) -> str:
    flags = [] if base_flags == "none" else base_flags.split(";")
    if f_stat < 10:
        flags.append("weak_instrument_f_lt_10")
    if not math.isfinite(wald_ratio):
        flags.append("invalid_wald_ratio")
    return ";".join(sorted(set(flags))) or "none"


def _wald_se(beta_gwas: float, beta_qtl: float, se_gwas: float | None, se_qtl: float | None) -> float | None:
    if beta_qtl == 0:
        return None
    if se_gwas is None and se_qtl is None:
        return None
    se_gwas = se_gwas or 0.0
    se_qtl = se_qtl or 0.0
    variance = (se_gwas / beta_qtl) ** 2 + ((beta_gwas * se_qtl) / (beta_qtl**2)) ** 2
    return math.sqrt(max(variance, 0.0))


def _ld_reference_id(ld_reference: str) -> str:
    if not ld_reference:
        return "ld_reference_placeholder"
    return "ldref_" + hashlib.sha256(ld_reference.encode("utf-8")).hexdigest()[:12]


def _ld_reference_manifest(project_dir: Path, ld_reference: str) -> dict:
    if not ld_reference:
        return {
            "schema_version": "v4.ld_reference_manifest/0.1",
            "ld_reference_id": "ld_reference_placeholder",
            "status": "placeholder",
            "path": "",
            "file_hash": "",
            "limitation": "No LD reference supplied; coloc/MR outputs are lightweight proxy results.",
        }
    path = project_dir / ld_reference
    return {
        "schema_version": "v4.ld_reference_manifest/0.1",
        "ld_reference_id": _ld_reference_id(ld_reference),
        "status": "available" if path.exists() else "missing",
        "path": ld_reference,
        "file_hash": _file_hash(path) if path.exists() else "",
        "limitation": "LD reference recorded for contract compatibility; local lightweight runner does not consume LD matrix.",
    }


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
