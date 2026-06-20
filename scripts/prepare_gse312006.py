import csv
import gzip
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "projects" / "vascular_aging_demo" / "data" / "GSE312006"
GENE_INFO = ROOT / "knowledge_base" / "annotation_tables" / "Homo_sapiens.gene_info.gz"
RAW_COUNTS = DATA_DIR / "GSE312006_featureCounts_raw_s3.txt.gz"
OUT_EXPR = DATA_DIR / "expression_matrix.tsv"
OUT_META = DATA_DIR / "metadata.tsv"
OUT_README = DATA_DIR / "README.md"


SAMPLES = [
    ("GSM9336110", "young", "HUVEC young replicate 1"),
    ("GSM9336111", "young", "HUVEC young replicate 2"),
    ("GSM9336112", "young", "HUVEC young replicate 3"),
    ("GSM9336113", "replicative_senescence", "HUVEC replicative senescence replicate 1"),
    ("GSM9336114", "replicative_senescence", "HUVEC replicative senescence replicate 2"),
    ("GSM9336115", "replicative_senescence", "HUVEC replicative senescence replicate 3"),
]


def ensembl_to_symbol() -> dict[str, str]:
    mapping = {}
    with gzip.open(GENE_INFO, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            symbol = row["Symbol"]
            for xref in row.get("dbXrefs", "").split("|"):
                if xref.startswith("Ensembl:"):
                    mapping[xref.split(":", 1)[1]] = symbol
    return mapping


def raw_sample_name(column: str) -> str:
    match = re.search(r"(TR_2423_\d+)_Aligned", column)
    if not match:
        raise ValueError(f"Cannot parse sample column: {column}")
    return match.group(1)


def main() -> None:
    mapping = ensembl_to_symbol()
    collapsed: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(SAMPLES))
    with gzip.open(RAW_COUNTS, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("#"):
                continue
            header = line.rstrip("\n").split("\t")
            break
        sample_columns = header[6:12]
        parsed = [raw_sample_name(col) for col in sample_columns]
        expected = [f"TR_2423_{idx:03d}" for idx in range(7, 13)]
        if parsed != expected:
            raise ValueError(f"Unexpected first six sample columns: {parsed}")
        for line in f:
            parts = line.rstrip("\n").split("\t")
            ensembl = parts[0].split(".", 1)[0]
            symbol = mapping.get(ensembl)
            if not symbol or symbol.startswith("LOC"):
                continue
            values = [float(v) for v in parts[6:12]]
            for idx, value in enumerate(values):
                collapsed[symbol][idx] += value

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with OUT_EXPR.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["gene_symbol", *[sample[0] for sample in SAMPLES]])
        for symbol in sorted(collapsed):
            values = collapsed[symbol]
            if sum(values) > 0:
                writer.writerow([symbol, *[f"{v:.0f}" for v in values]])

    with OUT_META.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["sample_id", "group", "batch", "sex", "age", "title"])
        for idx, (sample_id, group, title) in enumerate(SAMPLES, 1):
            writer.writerow([sample_id, group, "unknown", "unknown", "unknown", title])

    OUT_README.write_text(
        "\n".join(
            [
                "# GSE312006 Prepared Data",
                "",
                "Source: NCBI GEO GSE312006.",
                "Title: Transcriptomic profiling of young, replicative senescent, and premature senescent HUVECs.",
                "Used contrast: replicative_senescence vs young.",
                "Input raw counts: GSE312006_featureCounts_raw_s3.txt.gz.",
                "Sample metadata source: GSE312006_series_matrix.txt.gz.",
                "Gene ID mapping source: NCBI Homo_sapiens.gene_info.gz, Ensembl dbXrefs.",
                "",
                "This prepared matrix keeps the first six samples only: three young controls and three replicative senescence samples.",
                "Premature senescence samples are not used in the first real-data demo contrast.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT_EXPR}")
    print(f"wrote {OUT_META}")
    print(f"genes: {len(collapsed)}")


if __name__ == "__main__":
    main()
