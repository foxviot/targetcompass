import csv
import gzip
import json
import re
import urllib.request
from pathlib import Path
from urllib.error import URLError, HTTPError

from .paths import KB


HGNC_URL = "https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt"
HGNC_PATH = KB / "annotation_tables" / "hgnc_complete_set.txt"


def load_ensembl_to_symbol(project_dir: Path | None = None) -> dict[str, str]:
    paths = []
    if project_dir is not None:
        paths.extend(sorted((project_dir / "knowledge_imports").glob("*hgnc*.txt")))
        paths.extend(sorted((project_dir / "knowledge_imports").glob("*hgnc*.tsv")))
    paths.append(HGNC_PATH)
    for path in paths:
        if path.exists() and path.stat().st_size > 0:
            mapping = _read_hgnc_mapping(path)
            if mapping:
                return mapping
    return {}


def load_hgnc_symbols(project_dir: Path | None = None) -> set[str]:
    paths = []
    if project_dir is not None:
        paths.extend(sorted((project_dir / "knowledge_imports").glob("*hgnc*.txt")))
        paths.extend(sorted((project_dir / "knowledge_imports").glob("*hgnc*.tsv")))
    paths.append(HGNC_PATH)
    symbols: set[str] = set()
    for path in paths:
        if path.exists() and path.stat().st_size > 0:
            symbols.update(_read_hgnc_symbols(path))
    return symbols


def ensure_hgnc_mapping(project_dir: Path | None = None, timeout: int = 60) -> dict[str, str]:
    mapping = load_ensembl_to_symbol(project_dir)
    if mapping:
        return mapping
    HGNC_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(HGNC_URL, timeout=timeout) as response:
            HGNC_PATH.write_bytes(response.read())
    except (URLError, HTTPError, TimeoutError):
        return {}
    return load_ensembl_to_symbol(project_dir)


def ensure_hgnc_symbols(project_dir: Path | None = None, timeout: int = 60) -> set[str]:
    symbols = load_hgnc_symbols(project_dir)
    if symbols:
        return symbols
    ensure_hgnc_mapping(project_dir, timeout=timeout)
    return load_hgnc_symbols(project_dir)


def map_gene_symbol(value: str, ensembl_to_symbol: dict[str, str]) -> str:
    gene = _normalize_gene_id(value)
    if gene in ensembl_to_symbol:
        return ensembl_to_symbol[gene]
    return gene.upper()


def write_mapping_manifest(project_dir: Path, mapping: dict[str, str], mapped: int, unmapped: int, output: Path) -> Path:
    manifest = {
        "schema_version": "v4.gene_id_mapping/0.1",
        "project_id": project_dir.name,
        "source": str(HGNC_PATH),
        "mapping_count": len(mapping),
        "mapped_rows": mapped,
        "unmapped_rows": unmapped,
        "output": str(output.relative_to(project_dir)).replace("\\", "/"),
        "note": "Ensembl gene IDs are mapped to HGNC symbols when a current HGNC complete-set table is available.",
    }
    out = output.with_name("gene_id_mapping_manifest.json")
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _read_hgnc_mapping(path: Path) -> dict[str, str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not reader.fieldnames:
            return {}
        fields = {_norm(field): field for field in reader.fieldnames}
        symbol_field = fields.get("symbol")
        ensembl_field = fields.get("ensemblgeneid")
        if not symbol_field or not ensembl_field:
            return {}
        mapping = {}
        for row in reader:
            symbol = row.get(symbol_field, "").strip().upper()
            ensembl_ids = _split_ids(row.get(ensembl_field, ""))
            if not symbol or not ensembl_ids:
                continue
            for ensembl_id in ensembl_ids:
                mapping[_normalize_gene_id(ensembl_id)] = symbol
    return mapping


def _read_hgnc_symbols(path: Path) -> set[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        if not reader.fieldnames:
            return set()
        fields = {_norm(field): field for field in reader.fieldnames}
        symbol_field = fields.get("symbol")
        alias_field = fields.get("aliassymbol")
        prev_field = fields.get("prevsymbol")
        if not symbol_field:
            return set()
        symbols: set[str] = set()
        for row in reader:
            for field in [symbol_field, alias_field, prev_field]:
                if not field:
                    continue
                for symbol in _split_ids(row.get(field, "")):
                    if symbol:
                        symbols.add(symbol.upper())
        return symbols


def _split_ids(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,|; ]+", value or "") if item.strip()]


def _normalize_gene_id(value: str) -> str:
    value = str(value or "").strip().strip('"')
    if "|" in value:
        value = value.split("|")[-1]
    value = re.sub(r"\.\d+$", "", value)
    return value.upper()


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())
