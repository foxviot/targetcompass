import csv

from .common import detect_field_mapping, normalized_evidence_row, write_evidence
from .contracts import DatabaseAdapterContext, DatabaseAdapterResult


class TabularEvidenceAdapter:
    adapter_id = "tabular_evidence_v0"
    label = "CSV/TSV evidence table"
    description = "Adapts CSV/TSV gene-target tables with common gene, score, p-value, route, and evidence-type columns."

    def can_handle(self, context: DatabaseAdapterContext) -> bool:
        return context.source_path.suffix.lower() in {".csv", ".tsv", ".txt"}

    def adapt(self, context: DatabaseAdapterContext) -> DatabaseAdapterResult:
        rows = _read_rows(context.source_path)
        normalized = [normalized_evidence_row(row, context.resource_id) for row in rows]
        out = context.project_dir / "knowledge_imports" / "normalized" / f"{context.resource_id}_evidence.tsv"
        count = write_evidence(out, normalized)
        columns = list(rows[0].keys()) if rows else []
        return DatabaseAdapterResult(
            self.adapter_id,
            out,
            count,
            f"Adapted {count} tabular evidence row(s).",
            input_rows=len(rows),
            dropped_rows=max(0, len(rows) - count),
            field_mapping=detect_field_mapping(columns),
        )


def _read_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        sample = f.read(2048)
        f.seek(0)
        delimiter = "," if sample.count(",") > sample.count("\t") else "\t"
        return list(csv.DictReader(f, delimiter=delimiter))
