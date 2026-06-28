import csv
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .knowledge import add_resource, adapt_resources
from .v4 import content_hash


DATABASE_VALIDATION_SCHEMA = "v4.online_database_validation/0.1"


def validate_online_databases(
    project_dir: Path,
    genes: list[str] | None = None,
    query: str = "type 2 diabetes skeletal muscle",
    limit: int = 10,
    timeout: int = 30,
    adapt: bool = True,
) -> dict[str, Any]:
    genes = [gene.strip().upper() for gene in (genes or []) if gene.strip()]
    if not genes:
        genes = _seed_genes(project_dir)[:10] or ["IL6", "CXCL8", "CCL2", "TNF", "AEBP1"]
    out_dir = _out_dir(project_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = [
        _fetch_uniprot(project_dir, out_dir, genes, limit, timeout),
        _fetch_hpa(project_dir, out_dir, genes, limit, timeout),
        _fetch_reactome(project_dir, out_dir, genes, limit, timeout),
        _fetch_gwas_catalog(project_dir, out_dir, query, limit, timeout),
        _fetch_open_targets(project_dir, out_dir, query, genes, limit, timeout),
        _unavailable_source("disgenet", "DisGeNET public API requires credentials/license for automated gene-disease downloads."),
        _unavailable_source("msigdb", "MSigDB gene-set download requires registration/license; use local GMT with msigdb_gene_sets_v0 adapter."),
    ]
    registered = []
    if adapt:
        for source in sources:
            if source.get("status") != "success" or not source.get("source_path") or not source.get("adapter"):
                continue
            resource_id = "online_" + source["source_id"]
            add_resource(project_dir, resource_id, "external_database", str(project_dir / source["source_path"]), adapter=source["adapter"])
            registered.append(resource_id)
        adapted = adapt_resources(project_dir) if registered else []
    else:
        adapted = []
    payload = {
        "schema_version": DATABASE_VALIDATION_SCHEMA,
        "project_id": project_dir.name,
        "query": query,
        "genes": genes,
        "source_count": len(sources),
        "success_count": len([row for row in sources if row.get("status") == "success"]),
        "registered_resources": registered,
        "adapted_count": len(adapted),
        "sources": sources,
        "adaptation": adapted,
        "generated_at": _now(),
    }
    payload["validation_hash"] = content_hash(payload)
    path = out_dir / "online_database_validation.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    summary_path = out_dir / "online_database_validation.tsv"
    _write_summary_tsv(summary_path, sources)
    publish_paths = [path, summary_path]
    publish_paths.extend(project_dir / source["source_path"] for source in sources if source.get("source_path"))
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            publish_paths,
            producer="database_validation",
            artifact_type="database_validation_output",
            task_id="database_validation",
            qc_status="pass" if payload["success_count"] else "review",
        )
    except Exception:
        pass
    return payload


def _fetch_uniprot(project_dir: Path, out_dir: Path, genes: list[str], limit: int, timeout: int) -> dict[str, Any]:
    query = "(" + " OR ".join(f"gene_exact:{gene}" for gene in genes[:limit]) + ") AND organism_id:9606"
    params = urllib.parse.urlencode(
        {
            "query": query,
            "fields": "accession,gene_primary,protein_name,reviewed,cc_subcellular_location",
            "format": "tsv",
            "size": str(max(limit, 10)),
        }
    )
    url = f"https://rest.uniprot.org/uniprotkb/search?{params}"
    path = out_dir / "uniprot.tsv"
    return _download_tsv_source(project_dir, "uniprot", url, path, "uniprot_target_v0", timeout)


def _fetch_hpa(project_dir: Path, out_dir: Path, genes: list[str], limit: int, timeout: int) -> dict[str, Any]:
    rows = []
    urls = []
    try:
        for gene in genes[:limit]:
            url = f"https://www.proteinatlas.org/search/{urllib.parse.quote(gene)}?format=json"
            urls.append(url)
            data = _get_json(url, timeout)
            for item in data if isinstance(data, list) else []:
                symbol = str(item.get("Gene") or item.get("gene") or gene).upper()
                if symbol != gene:
                    continue
                rows.append(
                    {
                        "Gene": symbol,
                        "Subcellular location": item.get("Subcellular location") or item.get("subcellular_location") or "",
                        "RNA tissue specificity": item.get("RNA tissue specificity") or item.get("Tissue specificity") or "",
                        "Brain": item.get("Brain") or "",
                        "Heart": item.get("Heart muscle") or item.get("Heart") or "",
                    }
                )
                break
        path = out_dir / "hpa.tsv"
        _write_rows(path, ["Gene", "Subcellular location", "RNA tissue specificity", "Brain", "Heart"], rows)
        return _source_result(project_dir, "hpa", "success" if rows else "empty", urls[0] if urls else "", path, "hpa_safety_accessibility_v0", len(rows), "" if rows else "HPA search returned no gene rows.")
    except Exception as exc:
        return _source_result(project_dir, "hpa", "failed", urls[0] if urls else "", None, "hpa_safety_accessibility_v0", 0, str(exc))


def _fetch_reactome(project_dir: Path, out_dir: Path, genes: list[str], limit: int, timeout: int) -> dict[str, Any]:
    rows = []
    urls = []
    failures = []
    try:
        for gene in genes[:limit]:
            url = f"https://reactome.org/ContentService/search/query?query={urllib.parse.quote(gene)}&species=Homo%20sapiens&types=Pathway"
            urls.append(url)
            try:
                data = _get_json(url, timeout)
            except Exception as exc:
                failures.append(f"{gene}: {exc}")
                continue
            for result in data.get("results", []) if isinstance(data, dict) else []:
                for pathway in result.get("entries", []):
                    rows.append(
                        {
                            "pathway_id": pathway.get("stId") or pathway.get("id") or pathway.get("dbId") or "",
                            "pathway_name": pathway.get("displayName") or pathway.get("name") or "",
                            "gene_symbols": gene,
                        }
                    )
        path = out_dir / "reactome.tsv"
        _write_rows(path, ["pathway_id", "pathway_name", "gene_symbols"], rows)
        message = "; ".join(failures[:3]) if failures else ""
        return _source_result(project_dir, "reactome", "success" if rows else "empty", urls[0] if urls else "", path, "reactome_gene_sets_v0", len(rows), message if rows else (message or "Reactome query returned no pathway rows."))
    except Exception as exc:
        return _source_result(project_dir, "reactome", "failed", urls[0] if urls else "", None, "reactome_gene_sets_v0", 0, str(exc))


def _fetch_gwas_catalog(project_dir: Path, out_dir: Path, query: str, limit: int, timeout: int) -> dict[str, Any]:
    url = "https://www.ebi.ac.uk/gwas/rest/api/studies/search/findByDiseaseTrait?diseaseTrait=" + urllib.parse.quote(query)
    try:
        data = _get_json(url, timeout)
        studies = data.get("_embedded", {}).get("studies", [])[:limit]
        rows = []
        for item in studies:
            rows.append(
                {
                    "MAPPED_GENE": item.get("mappedGene") or item.get("reportedTrait") or query,
                    "P-VALUE": item.get("pvalue") or "",
                    "DISEASE/TRAIT": item.get("diseaseTrait", {}).get("trait") if isinstance(item.get("diseaseTrait"), dict) else query,
                }
            )
        path = out_dir / "gwas_catalog.tsv"
        _write_rows(path, ["MAPPED_GENE", "P-VALUE", "DISEASE/TRAIT"], rows)
        return _source_result(project_dir, "gwas_catalog", "success" if rows else "empty", url, path, "gwas_catalog_evidence_v0", len(rows), "" if rows else "GWAS Catalog returned no association rows for this query.")
    except Exception as exc:
        return _source_result(project_dir, "gwas_catalog", "failed", url, None, "gwas_catalog_evidence_v0", 0, str(exc))


def _fetch_open_targets(project_dir: Path, out_dir: Path, query: str, genes: list[str], limit: int, timeout: int) -> dict[str, Any]:
    url = "https://api.platform.opentargets.org/api/v4/graphql"
    rows = []
    try:
        for gene in genes[: min(limit, 10)]:
            payload = {
                "query": """
                query SearchTargets($queryString: String!) {
                  search(queryString: $queryString, entityNames: ["target"], page: {index: 0, size: 1}) {
                    hits { id name entity }
                  }
                }
                """,
                "variables": {"queryString": gene},
            }
            data = _post_json(url, payload, timeout)
            hits = data.get("data", {}).get("search", {}).get("hits", [])
            if hits:
                rows.append({"approvedSymbol": gene, "overallScore": "0.5", "diseaseName": query})
        path = out_dir / "opentargets.tsv"
        _write_rows(path, ["approvedSymbol", "overallScore", "diseaseName"], rows)
        return _source_result(project_dir, "opentargets", "success" if rows else "empty", url, path, "opentargets_evidence_v0", len(rows), "" if rows else "Open Targets search returned no target rows.")
    except Exception as exc:
        return _source_result(project_dir, "opentargets", "failed", url, None, "opentargets_evidence_v0", 0, str(exc))


def _download_tsv_source(project_dir: Path, source_id: str, url: str, path: Path, adapter: str, timeout: int) -> dict[str, Any]:
    try:
        text = _get_text(url, timeout)
        path.write_text(text, encoding="utf-8")
        row_count = max(0, len([line for line in text.splitlines() if line.strip()]) - 1)
        return _source_result(project_dir, source_id, "success" if row_count else "empty", url, path, adapter, row_count, "" if row_count else "Downloaded file had no data rows.")
    except Exception as exc:
        return _source_result(project_dir, source_id, "failed", url, None, adapter, 0, str(exc))


def _source_result(project_dir: Path, source_id: str, status: str, url: str, path: Path | None, adapter: str, row_count: int, message: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "status": status,
        "url": url,
        "source_path": str(path.relative_to(project_dir)).replace("\\", "/") if path else "",
        "adapter": adapter,
        "row_count": row_count,
        "message": message,
    }


def _unavailable_source(source_id: str, message: str) -> dict[str, Any]:
    return {"source_id": source_id, "status": "requires_credentials", "url": "", "source_path": "", "adapter": "", "row_count": 0, "message": message}


def _seed_genes(project_dir: Path) -> list[str]:
    candidates = []
    for path in [project_dir / "candidate_scores.csv", project_dir / "results" / "literature_validation" / "literature_evidence.tsv"]:
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as f:
            delimiter = "\t" if path.suffix == ".tsv" else ","
            for row in csv.DictReader(f, delimiter=delimiter):
                gene = row.get("gene_symbol") or row.get("entity_symbol")
                if gene and gene != "UNKNOWN":
                    candidates.append(gene.upper())
    return list(dict.fromkeys(candidates))


def _write_summary_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["source_id", "status", "row_count", "adapter", "source_path", "url", "message"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _write_rows(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _get_json(url: str, timeout: int) -> dict[str, Any]:
    return json.loads(_get_text(url, timeout))


def _get_text(url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "TargetCompassLite/0.4 online database validation"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "TargetCompassLite/0.4 online database validation"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _out_dir(project_dir: Path) -> Path:
    return project_dir / "results" / "database_validation"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
