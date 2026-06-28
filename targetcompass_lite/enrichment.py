import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .paths import KB
from .v4 import content_hash, file_hash


GENE_SETS = KB / "enrichment_gene_sets.tsv"


def _read_gene_sets(path: Path = GENE_SETS, collection_id: str = "") -> list[dict]:
    collection_id = collection_id or path.stem
    with path.open(encoding="utf-8") as f:
        rows = []
        for idx, row in enumerate(csv.DictReader(f, delimiter="\t"), 1):
            genes = {gene.strip() for gene in row["genes"].split(",") if gene.strip()}
            source = row.get("source", collection_id)
            version = row.get("version", row.get("collection_version", "unversioned"))
            rows.append(
                {
                    **row,
                    "gene_set": genes,
                    "source": source,
                    "collection_id": row.get("collection_id", collection_id),
                    "collection_version": version,
                    "gene_set_hash": content_hash({"term_id": row.get("term_id", f"term_{idx}"), "genes": sorted(genes), "source": source, "version": version}),
                    "source_path": str(path),
                }
            )
        return rows


def _project_gene_sets(project_dir: Path) -> tuple[list[dict], list[dict]]:
    rows = _read_gene_sets()
    sources = [_source_snapshot(GENE_SETS, rows)]
    for path in sorted((project_dir / "knowledge_imports" / "normalized").glob("*_gene_sets.tsv")):
        imported = _read_gene_sets(path, collection_id=path.stem)
        rows.extend(imported)
        sources.append(_source_snapshot(path, imported))
    return rows, sources


def _read_deg(path: Path) -> tuple[set[str], set[str], dict[str, float]]:
    background = set()
    selected = set()
    ranks = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            gene = row["gene_symbol"]
            background.add(gene)
            try:
                adj_p = float(row["adj_p_value"])
                log_fc = float(row["logFC"])
            except (TypeError, ValueError):
                continue
            ranks[gene] = log_fc
            if adj_p < 0.05 and log_fc > 0:
                selected.add(gene)
    return background, selected, ranks


def _hypergeom_sf(overlap: int, selected_n: int, term_n: int, universe_n: int) -> float:
    if overlap <= 0 or selected_n <= 0 or term_n <= 0 or universe_n <= 0:
        return 1.0
    denominator = math.comb(universe_n, selected_n)
    max_k = min(selected_n, term_n)
    total = 0
    for k in range(overlap, max_k + 1):
        if selected_n - k <= universe_n - term_n:
            total += math.comb(term_n, k) * math.comb(universe_n - term_n, selected_n - k)
    return min(1.0, total / denominator)


def run_enrichment(project_dir: Path) -> Path:
    out_dir = project_dir / "results" / "enrichment"
    out_dir.mkdir(parents=True, exist_ok=True)
    gene_sets, source_snapshots = _project_gene_sets(project_dir)
    ora_rows = []
    gsea_rows = []
    for deg_path in sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv")):
        dataset_id = deg_path.parent.name.replace("bulk_deg_", "")
        background, selected, ranks = _read_deg(deg_path)
        universe_n = len(background)
        selected_n = len(selected)
        for term in gene_sets:
            term_genes = term["gene_set"] & background
            overlap_genes = sorted(term_genes & selected)
            p_value = _hypergeom_sf(len(overlap_genes), selected_n, len(term_genes), universe_n)
            ora_rows.append(
                {
                    "method": "ORA",
                    "dataset_id": dataset_id,
                    "term_id": term["term_id"],
                    "term_name": term["term_name"],
                    "collection_id": term["collection_id"],
                    "collection_version": term["collection_version"],
                    "overlap_n": len(overlap_genes),
                    "selected_n": selected_n,
                    "term_n": len(term_genes),
                    "universe_n": universe_n,
                    "p_value": f"{p_value:.6g}",
                    "overlap_genes": ",".join(overlap_genes),
                    "source": term["source"],
                    "gene_set_hash": term["gene_set_hash"],
                }
            )
            if ranks and term_genes:
                gsea_rows.append(_gsea_like_row(dataset_id, term, ranks, background))
    ora_rows.sort(key=lambda row: (float(row["p_value"]), row["dataset_id"], row["term_id"]))
    for rank, row in enumerate(ora_rows, 1):
        row["adj_p_value"] = f"{min(1.0, float(row['p_value']) * len(ora_rows) / rank):.6g}"
    gsea_rows.sort(key=lambda row: (-abs(float(row["enrichment_score"])), row["dataset_id"], row["term_id"]))
    out = out_dir / "enrichment_results.tsv"
    fields = [
        "method",
        "dataset_id",
        "term_id",
        "term_name",
        "collection_id",
        "collection_version",
        "overlap_n",
        "selected_n",
        "term_n",
        "universe_n",
        "p_value",
        "adj_p_value",
        "overlap_genes",
        "source",
        "gene_set_hash",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(ora_rows)
    gsea_out = out_dir / "gsea_preranked_results.tsv"
    gsea_fields = [
        "method",
        "dataset_id",
        "term_id",
        "term_name",
        "collection_id",
        "collection_version",
        "set_size",
        "ranked_gene_count",
        "enrichment_score",
        "normalized_enrichment_score",
        "leading_edge_genes",
        "source",
        "gene_set_hash",
    ]
    with gsea_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=gsea_fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(gsea_rows)
    snapshot = {
        "schema_version": "v4.enrichment_gene_set_snapshot/0.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sources": source_snapshots,
        "gene_set_count": len(gene_sets),
        "snapshot_hash": content_hash(
            [
                {
                    "term_id": row.get("term_id", ""),
                    "source": row.get("source", ""),
                    "collection_version": row.get("collection_version", ""),
                    "gene_set_hash": row.get("gene_set_hash", ""),
                }
                for row in gene_sets
            ]
        ),
    }
    snapshot_path = out_dir / "gene_set_snapshot.json"
    snapshot_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    manifest = {
        "schema_version": "v4.enrichment_manifest/0.3",
        "module_id": "enrichment_v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_deg_files": [str(path.relative_to(project_dir)) for path in sorted((project_dir / "results").glob("bulk_deg_*/deg_results.tsv"))],
        "gene_set_count": len(gene_sets),
        "gene_set_snapshot": str(snapshot_path.relative_to(project_dir)),
        "gene_set_snapshot_hash": snapshot["snapshot_hash"],
        "methods": {
            "ora": "Hypergeometric over-representation analysis on significant up-regulated genes.",
            "gsea_preranked": "Lightweight preranked enrichment score using logFC-ranked genes; no permutation p-value.",
        },
        "background_policy": "Per-dataset background is all genes present in the DEG result.",
        "tested_rows": len(ora_rows),
        "gsea_rows": len(gsea_rows),
        "significant_rows": sum(1 for row in ora_rows if float(row["adj_p_value"]) < 0.05),
        "output": str(out.relative_to(project_dir)),
        "gsea_output": str(gsea_out.relative_to(project_dir)),
        "output_hash": file_hash(out),
        "gsea_output_hash": file_hash(gsea_out),
        "qc": {
            "status": "pass" if ora_rows else "warning",
            "message": "enrichment completed" if ora_rows else "no DEG inputs or gene sets produced testable rows",
            "ora_rows": len(ora_rows),
            "gsea_rows": len(gsea_rows),
            "gene_set_sources": len(source_snapshots),
        },
    }
    run_manifest = out_dir / "run_manifest.json"
    qc_summary = out_dir / "qc_summary.json"
    run_manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    qc_summary.write_text(json.dumps(manifest["qc"], indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            [out, gsea_out, snapshot_path, run_manifest, qc_summary],
            producer="enrichment",
            artifact_type="enrichment_output",
            task_id="enrichment",
            qc_status="pass" if manifest["qc"]["status"] == "pass" else "pending",
        )
    except Exception:
        pass
    return out


def _source_snapshot(path: Path, rows: list[dict]) -> dict:
    return {
        "path": str(path),
        "file_hash": file_hash(path) if path.exists() else "",
        "source_count": len({row.get("source", "") for row in rows}),
        "gene_set_count": len(rows),
        "collection_versions": sorted({row.get("collection_version", "unversioned") for row in rows}),
    }


def _gsea_like_row(dataset_id: str, term: dict, ranks: dict[str, float], background: set[str]) -> dict[str, str]:
    ranked = sorted(((gene, score) for gene, score in ranks.items() if gene in background), key=lambda item: item[1], reverse=True)
    genes = term["gene_set"] & {gene for gene, _score in ranked}
    if not ranked or not genes:
        score = 0.0
        leading_edge = []
    else:
        hits = {gene for gene in genes}
        hit_weight = sum(abs(score) for gene, score in ranked if gene in hits) or 1.0
        miss_weight = max(1, len(ranked) - len(hits))
        running = 0.0
        best = 0.0
        best_index = 0
        for index, (gene, value) in enumerate(ranked):
            if gene in hits:
                running += abs(value) / hit_weight
            else:
                running -= 1.0 / miss_weight
            if abs(running) > abs(best):
                best = running
                best_index = index
        score = best
        leading_edge = [gene for gene, _value in ranked[: best_index + 1] if gene in hits]
    normalized = score / math.sqrt(max(1, len(genes)))
    return {
        "method": "GSEA_PRERANKED_LIGHTWEIGHT",
        "dataset_id": dataset_id,
        "term_id": term["term_id"],
        "term_name": term["term_name"],
        "collection_id": term["collection_id"],
        "collection_version": term["collection_version"],
        "set_size": str(len(genes)),
        "ranked_gene_count": str(len(ranked)),
        "enrichment_score": f"{score:.6g}",
        "normalized_enrichment_score": f"{normalized:.6g}",
        "leading_edge_genes": ",".join(leading_edge),
        "source": term["source"],
        "gene_set_hash": term["gene_set_hash"],
    }
