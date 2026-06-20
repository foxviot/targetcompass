import csv
from pathlib import Path

from .common import field, norm_route, normalized_evidence_row, write_evidence
from .contracts import DatabaseAdapterContext, DatabaseAdapterResult


def _read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = "," if sample.count(",") > sample.count("\t") else "\t"
        return list(csv.DictReader(f, delimiter=delimiter))


def _out_dir(context: DatabaseAdapterContext) -> Path:
    path = context.project_dir / "knowledge_imports" / "normalized"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _norm_symbol(value: str) -> str:
    return str(value or "").strip().split(";")[0].split(",")[0]


def _write_accessibility(path: Path, rows: list[dict]) -> int:
    fields = ["gene_symbol", "route", "accessibility_status", "source"]
    count = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            gene = _norm_symbol(row.get("gene_symbol", ""))
            if not gene:
                continue
            writer.writerow(
                {
                    "gene_symbol": gene,
                    "route": row.get("route", "unknown"),
                    "accessibility_status": row.get("accessibility_status", "UNKNOWN"),
                    "source": row.get("source", ""),
                }
            )
            count += 1
    return count


def _write_safety(path: Path, rows: list[dict]) -> int:
    fields = ["gene_symbol", "safety_gate", "critical_tissue_flag", "note"]
    count = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            gene = _norm_symbol(row.get("gene_symbol", ""))
            if not gene:
                continue
            writer.writerow(
                {
                    "gene_symbol": gene,
                    "safety_gate": row.get("safety_gate", "REVIEW_REQUIRED"),
                    "critical_tissue_flag": row.get("critical_tissue_flag", "UNKNOWN"),
                    "note": row.get("note", ""),
                }
            )
            count += 1
    return count


def _write_gene_sets(path: Path, rows: list[dict]) -> int:
    fields = ["term_id", "term_name", "genes", "source"]
    count = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for idx, row in enumerate(rows, 1):
            genes = _join_genes(row.get("genes", ""))
            if not genes:
                continue
            term_id = str(row.get("term_id") or f"TERM_{idx}").replace(" ", "_")
            writer.writerow(
                {
                    "term_id": term_id,
                    "term_name": row.get("term_name") or term_id,
                    "genes": genes,
                    "source": row.get("source", ""),
                }
            )
            count += 1
    return count


def _join_genes(value: str) -> str:
    parts = [
        item.strip()
        for item in str(value or "").replace("|", ";").replace(",", ";").split(";")
        if item.strip()
    ]
    return ",".join(dict.fromkeys(parts))


def _quality(value: str, default: str = "0.5") -> str:
    text = str(value or "").strip()
    if text == "":
        return default
    try:
        return f"{float(text):.4g}"
    except ValueError:
        return default


class UniProtAdapter:
    adapter_id = "uniprot_target_v0"
    label = "UniProt target accessibility"
    description = "Adapts UniProt TSV/CSV exports into accessibility and reviewed protein evidence."

    def can_handle(self, context: DatabaseAdapterContext) -> bool:
        return context.adapter == self.adapter_id or "uniprot" in context.source_path.name.lower()

    def adapt(self, context: DatabaseAdapterContext) -> DatabaseAdapterResult:
        rows = _read_rows(context.source_path)
        access_rows = []
        evidence_rows = []
        for row in rows:
            gene = field(row, ["Gene Names (primary)", "Gene Names", "gene_primary", "gene_symbol", "Entry Name", "Entry"])
            location = field(row, ["Subcellular location [CC]", "Subcellular location", "location", "cc_subcellular_location"])
            protein = field(row, ["Protein names", "protein_name", "recommended_name"])
            reviewed = field(row, ["Reviewed", "reviewed", "Status"])
            route = norm_route(location)
            access_rows.append(
                {
                    "gene_symbol": gene,
                    "route": route,
                    "accessibility_status": "SUPPORTED" if route in {"surface", "secreted", "ECD"} else "UNKNOWN",
                    "source": context.resource_id,
                }
            )
            evidence_rows.append(
                {
                    "entity_symbol": gene,
                    "route": route,
                    "evidence_type": "uniprot_annotation",
                    "direction": "",
                    "effect_size": "",
                    "p_value": "",
                    "quality_score": "0.8" if "reviewed" in reviewed.lower() else "0.5",
                    "source_dataset": context.resource_id,
                    "limitation": protein or "UniProt annotation; requires review",
                }
            )
        out = _out_dir(context)
        access_path = out / f"{context.resource_id}_accessibility.tsv"
        evidence_path = out / f"{context.resource_id}_evidence.tsv"
        access_n = _write_accessibility(access_path, access_rows)
        evidence_n = write_evidence(evidence_path, evidence_rows)
        return DatabaseAdapterResult(
            self.adapter_id,
            evidence_path,
            access_n + evidence_n,
            f"Adapted UniProt rows into {access_n} accessibility and {evidence_n} evidence row(s).",
            input_rows=len(rows),
            dropped_rows=max(0, len(rows) - evidence_n),
            normalized_outputs={"accessibility": str(access_path), "evidence": str(evidence_path)},
        )


class HPAAdapter:
    adapter_id = "hpa_safety_accessibility_v0"
    label = "Human Protein Atlas safety/accessibility"
    description = "Adapts HPA-like TSV/CSV exports into tissue safety and accessibility annotations."

    def can_handle(self, context: DatabaseAdapterContext) -> bool:
        name = context.source_path.name.lower()
        return context.adapter == self.adapter_id or "hpa" in name or "proteinatlas" in name

    def adapt(self, context: DatabaseAdapterContext) -> DatabaseAdapterResult:
        rows = _read_rows(context.source_path)
        access_rows = []
        safety_rows = []
        for row in rows:
            gene = field(row, ["Gene", "Gene name", "gene_symbol", "approved_symbol"])
            location = field(row, ["Subcellular location", "Main location", "location"])
            tissue = field(row, ["RNA tissue specificity", "Tissue specificity", "tissue_specificity"])
            brain = field(row, ["Brain", "brain_expression", "brain"])
            heart = field(row, ["Heart muscle", "Heart", "heart_expression", "heart"])
            route = norm_route(location)
            critical = ",".join([name for name, value in {"brain": brain, "heart": heart}.items() if str(value).lower() in {"high", "medium", "detected"}])
            safety_gate = "REVIEW_REQUIRED" if critical else "PASS"
            access_rows.append(
                {
                    "gene_symbol": gene,
                    "route": route,
                    "accessibility_status": "SUPPORTED" if route in {"surface", "secreted", "ECD"} else "UNKNOWN",
                    "source": context.resource_id,
                }
            )
            safety_rows.append(
                {
                    "gene_symbol": gene,
                    "safety_gate": safety_gate,
                    "critical_tissue_flag": critical or "none",
                    "note": tissue or "HPA tissue annotation",
                }
            )
        out = _out_dir(context)
        access_path = out / f"{context.resource_id}_accessibility.tsv"
        safety_path = out / f"{context.resource_id}_safety.tsv"
        access_n = _write_accessibility(access_path, access_rows)
        safety_n = _write_safety(safety_path, safety_rows)
        return DatabaseAdapterResult(
            self.adapter_id,
            None,
            access_n + safety_n,
            f"Adapted HPA rows into {access_n} accessibility and {safety_n} safety row(s).",
            input_rows=len(rows),
            dropped_rows=max(0, len(rows) - max(access_n, safety_n)),
            normalized_outputs={"accessibility": str(access_path), "safety": str(safety_path)},
        )


class OpenTargetsAdapter:
    adapter_id = "opentargets_evidence_v0"
    label = "Open Targets evidence"
    description = "Adapts Open Targets association exports into normalized target evidence."

    def can_handle(self, context: DatabaseAdapterContext) -> bool:
        name = context.source_path.name.lower()
        return context.adapter == self.adapter_id or "opentarget" in name or "open_targets" in name

    def adapt(self, context: DatabaseAdapterContext) -> DatabaseAdapterResult:
        rows = _read_rows(context.source_path)
        normalized = []
        for row in rows:
            gene = field(row, ["approvedSymbol", "approved_symbol", "targetSymbol", "target_symbol", "symbol"])
            score = field(row, ["overallScore", "association_score", "score"])
            disease = field(row, ["diseaseName", "disease", "disease_label"])
            normalized.append(
                {
                    **normalized_evidence_row({**row, "target_symbol": gene, "score": score}, context.resource_id),
                    "evidence_type": "opentargets_association",
                    "quality_score": _quality(score, "0.5"),
                    "limitation": f"Open Targets association; disease={disease or 'unknown'}; requires review",
                }
            )
        out = _out_dir(context) / f"{context.resource_id}_evidence.tsv"
        count = write_evidence(out, normalized)
        return DatabaseAdapterResult(self.adapter_id, out, count, f"Adapted {count} Open Targets evidence row(s).", len(rows), len(rows) - count, normalized_outputs={"evidence": str(out)})


class DisGeNETAdapter:
    adapter_id = "disgenet_evidence_v0"
    label = "DisGeNET evidence"
    description = "Adapts DisGeNET gene-disease association exports into normalized evidence."

    def can_handle(self, context: DatabaseAdapterContext) -> bool:
        return context.adapter == self.adapter_id or "disgenet" in context.source_path.name.lower()

    def adapt(self, context: DatabaseAdapterContext) -> DatabaseAdapterResult:
        rows = _read_rows(context.source_path)
        normalized = []
        for row in rows:
            gene = field(row, ["geneSymbol", "gene_symbol", "symbol"])
            score = field(row, ["score", "DSI", "DPI"])
            disease = field(row, ["diseaseName", "disease_name", "disease"])
            normalized.append(
                {
                    **normalized_evidence_row({**row, "gene_symbol": gene, "score": score}, context.resource_id),
                    "evidence_type": "disgenet_association",
                    "quality_score": _quality(score, "0.5"),
                    "limitation": f"DisGeNET association; disease={disease or 'unknown'}; requires review",
                }
            )
        out = _out_dir(context) / f"{context.resource_id}_evidence.tsv"
        count = write_evidence(out, normalized)
        return DatabaseAdapterResult(self.adapter_id, out, count, f"Adapted {count} DisGeNET evidence row(s).", len(rows), len(rows) - count, normalized_outputs={"evidence": str(out)})


class GWASCatalogAdapter:
    adapter_id = "gwas_catalog_evidence_v0"
    label = "GWAS Catalog evidence"
    description = "Adapts GWAS Catalog association exports into normalized statistical genetics evidence."

    def can_handle(self, context: DatabaseAdapterContext) -> bool:
        name = context.source_path.name.lower()
        return context.adapter == self.adapter_id or "gwas" in name

    def adapt(self, context: DatabaseAdapterContext) -> DatabaseAdapterResult:
        rows = _read_rows(context.source_path)
        normalized = []
        for row in rows:
            gene = field(row, ["MAPPED_GENE", "REPORTED GENE(S)", "gene_symbol", "mapped_gene"])
            p_value = field(row, ["P-VALUE", "p_value", "pvalue"])
            trait = field(row, ["DISEASE/TRAIT", "trait", "disease_trait"])
            normalized.append(
                {
                    **normalized_evidence_row({**row, "gene_symbol": gene, "p_value": p_value}, context.resource_id),
                    "evidence_type": "gwas_association",
                    "quality_score": "0.8" if p_value and _is_significant(p_value) else "0.5",
                    "limitation": f"GWAS association; trait={trait or 'unknown'}; locus-to-gene mapping requires review",
                }
            )
        out = _out_dir(context) / f"{context.resource_id}_evidence.tsv"
        count = write_evidence(out, normalized)
        return DatabaseAdapterResult(self.adapter_id, out, count, f"Adapted {count} GWAS Catalog evidence row(s).", len(rows), len(rows) - count, normalized_outputs={"evidence": str(out)})


def _is_significant(value: str) -> bool:
    try:
        return float(str(value).replace("E", "e")) <= 5e-8
    except ValueError:
        return False


class MSigDBAdapter:
    adapter_id = "msigdb_gene_sets_v0"
    label = "MSigDB gene sets"
    description = "Adapts MSigDB GMT or TSV exports into normalized gene sets."

    def can_handle(self, context: DatabaseAdapterContext) -> bool:
        name = context.source_path.name.lower()
        return context.adapter == self.adapter_id or "msigdb" in name or context.source_path.suffix.lower() == ".gmt"

    def adapt(self, context: DatabaseAdapterContext) -> DatabaseAdapterResult:
        rows = _read_gene_set_rows(context, source_label="MSigDB")
        out = _out_dir(context) / f"{context.resource_id}_gene_sets.tsv"
        count = _write_gene_sets(out, rows)
        return DatabaseAdapterResult(self.adapter_id, None, count, f"Adapted {count} MSigDB gene set row(s).", len(rows), len(rows) - count, normalized_outputs={"gene_sets": str(out)})


class ReactomeAdapter:
    adapter_id = "reactome_gene_sets_v0"
    label = "Reactome gene sets"
    description = "Adapts Reactome pathway exports into normalized gene sets."

    def can_handle(self, context: DatabaseAdapterContext) -> bool:
        return context.adapter == self.adapter_id or "reactome" in context.source_path.name.lower()

    def adapt(self, context: DatabaseAdapterContext) -> DatabaseAdapterResult:
        rows = _read_gene_set_rows(context, source_label="Reactome")
        out = _out_dir(context) / f"{context.resource_id}_gene_sets.tsv"
        count = _write_gene_sets(out, rows)
        return DatabaseAdapterResult(self.adapter_id, None, count, f"Adapted {count} Reactome gene set row(s).", len(rows), len(rows) - count, normalized_outputs={"gene_sets": str(out)})


def _read_gene_set_rows(context: DatabaseAdapterContext, source_label: str) -> list[dict]:
    if context.source_path.suffix.lower() == ".gmt":
        rows = []
        with context.source_path.open(encoding="utf-8-sig") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 3:
                    rows.append({"term_id": parts[0], "term_name": parts[1] or parts[0], "genes": ";".join(parts[2:]), "source": context.resource_id})
        return rows
    rows = []
    for row in _read_rows(context.source_path):
        term_id = field(row, ["term_id", "pathway_id", "geneset_id", "set_id", "id", "ST_ID"], context.resource_id)
        term_name = field(row, ["term_name", "pathway_name", "geneset_name", "set_name", "name", "description"], term_id)
        genes = field(row, ["genes", "gene_symbols", "members", "member_symbols", "gene_list", "symbol", "gene_symbol"])
        rows.append({"term_id": term_id, "term_name": term_name, "genes": genes, "source": context.resource_id or source_label})
    return rows
