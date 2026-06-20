import csv
import json
from pathlib import Path

from .knowledge import load_registry


def build_adapter_audit(project_dir: Path) -> tuple[Path, Path]:
    rows = []
    for item in load_registry(project_dir):
        rows.append(
            {
                "resource_id": item.get("resource_id", ""),
                "resource_type": item.get("resource_type", ""),
                "adapter": item.get("adapter", ""),
                "database_adapter": item.get("database_adapter", ""),
                "status": item.get("status", ""),
                "normalized_rows": item.get("normalized_rows", 0),
                "input_rows": item.get("input_rows", 0),
                "dropped_rows": item.get("dropped_rows", 0),
                "field_mapping": json.dumps(item.get("field_mapping", {}), ensure_ascii=False),
                "adapter_message": item.get("adapter_message", ""),
                "normalized_evidence": item.get("normalized_evidence", ""),
                "normalized_gene_sets": item.get("normalized_gene_sets", ""),
                "normalized_accessibility": item.get("normalized_accessibility", ""),
                "normalized_safety": item.get("normalized_safety", ""),
            }
        )
    out_dir = project_dir / "results" / "adapter_audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "adapter_audit.json"
    tsv_path = out_dir / "adapter_audit.tsv"
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    with tsv_path.open("w", newline="", encoding="utf-8") as f:
        fields = [
            "resource_id",
            "resource_type",
            "adapter",
            "database_adapter",
            "status",
            "normalized_rows",
            "input_rows",
            "dropped_rows",
            "field_mapping",
            "adapter_message",
            "normalized_evidence",
            "normalized_gene_sets",
            "normalized_accessibility",
            "normalized_safety",
        ]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    return json_path, tsv_path
