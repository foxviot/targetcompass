import csv
import gzip
from collections import defaultdict
from pathlib import Path


PROJECT = Path("projects/vascular_aging_demo")
DATA = PROJECT / "data" / "GSE43292"
SERIES = DATA / "GSE43292_series_matrix.txt.gz"
ANNOT = DATA / "GPL6244.annot.gz"


def _clean(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def load_probe_symbols() -> dict[str, str]:
    mapping = {}
    with gzip.open(ANNOT, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#"):
                continue
            if line.startswith("ID\t"):
                reader = csv.DictReader([line] + list(f), delimiter="\t")
                for row in reader:
                    symbol = (row.get("Gene symbol") or "").split(" /// ")[0].strip()
                    if symbol and symbol != "---":
                        mapping[row["ID"]] = symbol
                break
    return mapping


def load_series() -> tuple[list[dict], dict[str, dict[str, float]]]:
    sample_ids = []
    titles = []
    patients = []
    tissues = []
    matrix = {}
    in_table = False
    header = []
    with gzip.open(SERIES, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = [_clean(part) for part in line.rstrip("\n").split("\t")]
            key = parts[0]
            values = parts[1:]
            if key == "!Sample_geo_accession":
                sample_ids = values
            elif key == "!Sample_title":
                titles = values
            elif key == "!Sample_characteristics_ch1" and values and values[0].startswith("patient:"):
                patients = [value.replace("patient:", "").strip() for value in values]
            elif key == "!Sample_characteristics_ch1" and values and values[0].startswith("tissue:"):
                tissues = [value.replace("tissue:", "").strip() for value in values]
            elif key == "!series_matrix_table_begin":
                in_table = True
            elif in_table and key == "ID_REF":
                header = values
            elif in_table and key == "!series_matrix_table_end":
                break
            elif in_table and header:
                probe = key
                try:
                    matrix[probe] = {sample: float(value) for sample, value in zip(header, values)}
                except ValueError:
                    continue
    metadata = []
    for sample_id, title, patient, tissue in zip(sample_ids, titles, patients, tissues):
        group = "atheroma_plaque" if "atheroma" in tissue.lower() else "intact_carotid"
        metadata.append(
            {
                "sample_id": sample_id,
                "group": group,
                "patient_id": patient,
                "batch": patient,
                "tissue": tissue,
                "title": title,
            }
        )
    return metadata, matrix


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    probe_symbols = load_probe_symbols()
    metadata, probe_matrix = load_series()
    by_gene: dict[str, list[dict[str, float]]] = defaultdict(list)
    for probe, values in probe_matrix.items():
        symbol = probe_symbols.get(probe)
        if symbol:
            by_gene[symbol].append(values)
    samples = [row["sample_id"] for row in metadata]
    with (DATA / "metadata.tsv").open("w", newline="", encoding="utf-8") as f:
        fields = ["sample_id", "group", "patient_id", "batch", "tissue", "title"]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(metadata)
    with (DATA / "expression_matrix.tsv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["gene_symbol", *samples])
        for gene in sorted(by_gene):
            rows = by_gene[gene]
            averaged = []
            for sample in samples:
                averaged.append(sum(row[sample] for row in rows) / len(rows))
            writer.writerow([gene, *[f"{value:.6g}" for value in averaged]])
    card = PROJECT / "dataset_cards" / "GSE43292.yaml"
    card.write_text(
        "\n".join(
            [
                "dataset_id: GSE43292",
                "source: GEO",
                "accession: GSE43292",
                "modality: bulk_expression",
                "organism: human",
                "tissue: carotid artery",
                "contrast:",
                "  case: atheroma_plaque",
                "  control: intact_carotid",
                "sample_summary:",
                "  case_n: 32",
                "  control_n: 32",
                "  donor_n: 32",
                "metadata_fields: [sample_id, group, patient_id, batch, tissue, title]",
                "matrix_available: true",
                "license_status: public",
                "file_paths:",
                "  expression_matrix: data/GSE43292/expression_matrix.tsv",
                "  metadata: data/GSE43292/metadata.tsv",
                "known_limitations: [microarray series matrix, paired patient design represented as batch covariate, disease is carotid atheroma rather than normal aging]",
                "recommended_use: [bulk_deg]",
                "blocked_use: []",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (DATA / "README.md").write_text(
        "GSE43292: human carotid endarterectomy paired atheroma plaque vs macroscopically intact carotid tissue. Prepared from GEO series matrix and GPL6244 annotation.\n",
        encoding="utf-8",
    )
    print(f"metadata rows: {len(metadata)}")
    print(f"gene rows: {len(by_gene)}")


if __name__ == "__main__":
    main()
