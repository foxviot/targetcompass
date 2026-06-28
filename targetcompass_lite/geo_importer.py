import csv
import gzip
import json
import re
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

from .validators import validate_dataset_card


GEO_FTP = "https://ftp.ncbi.nlm.nih.gov/geo"


class GeoImportError(RuntimeError):
    def __init__(
        self,
        code: str,
        stage: str,
        message: str,
        recovery: list[str],
        retryable: bool = False,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.message = message
        self.recovery = recovery
        self.retryable = retryable
        self.details = details or {}

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "stage": self.stage,
            "message": self.message,
            "retryable": self.retryable,
            "recovery": self.recovery,
            "details": self.details,
        }


@dataclass
class GeoImportResult:
    accession: str
    data_dir: Path
    series_matrix: Path
    expression_matrix: Path
    metadata: Path
    dataset_card: Path
    readme: Path
    samples: int
    genes: int
    case_n: int
    control_n: int
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "accession": self.accession,
            "data_dir": str(self.data_dir),
            "series_matrix": str(self.series_matrix),
            "expression_matrix": str(self.expression_matrix),
            "metadata": str(self.metadata),
            "dataset_card": str(self.dataset_card),
            "readme": str(self.readme),
            "samples": self.samples,
            "genes": self.genes,
            "case_n": self.case_n,
            "control_n": self.control_n,
            "warnings": self.warnings,
        }


@dataclass
class GroupInference:
    group_column: str
    case_label: str
    control_label: str
    confidence: int
    reasons: list[str]
    warnings: list[str]
    value_counts: dict[str, int]
    sample_groups: dict[str, str]

    def to_dict(self) -> dict:
        return {
            "group_column": self.group_column,
            "case_label": self.case_label,
            "control_label": self.control_label,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "value_counts": self.value_counts,
            "sample_groups": self.sample_groups,
        }


def _clean(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def _family_bucket(accession: str) -> str:
    accession = accession.upper()
    match = re.match(r"^([A-Z]+)(\d+)$", accession)
    if not match:
        raise ValueError(f"Unsupported GEO accession: {accession}")
    prefix, number = match.groups()
    if len(number) <= 3:
        return f"{prefix}nnn"
    return f"{prefix}{number[:-3]}nnn"


def geo_series_matrix_url(accession: str) -> str:
    accession = accession.upper()
    bucket = _family_bucket(accession)
    return f"{GEO_FTP}/series/{bucket}/{accession}/matrix/{accession}_series_matrix.txt.gz"


def geo_platform_annotation_url(platform_id: str) -> str:
    platform_id = platform_id.upper()
    bucket = _family_bucket(platform_id)
    return f"{GEO_FTP}/platforms/{bucket}/{platform_id}/annot/{platform_id}.annot.gz"


def download_file(url: str, out: Path, force: bool = False) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 0 and not force:
        return out
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            out.write_bytes(response.read())
    except HTTPError as exc:
        raise GeoImportError(
            "GEO_DOWNLOAD_HTTP_ERROR",
            "download",
            f"GEO series matrix download failed with HTTP {exc.code}.",
            [
                "Confirm the accession is a GEO Series id such as GSE312006.",
                "Open the GEO page manually and confirm that a series matrix file exists.",
                "Retry with --force-download after network access is stable.",
            ],
            retryable=exc.code in {408, 429, 500, 502, 503, 504},
            details={"url": url, "http_status": exc.code},
        ) from exc
    except URLError as exc:
        raise GeoImportError(
            "GEO_DOWNLOAD_NETWORK_ERROR",
            "download",
            "GEO series matrix download failed because the network request did not complete.",
            [
                "Check internet access and proxy/firewall settings.",
                "Retry the same command; cached files will be reused when available.",
                "If the file was downloaded manually, place it under data/<GSE>/<GSE>_series_matrix.txt.gz and retry.",
            ],
            retryable=True,
            details={"url": url, "reason": str(exc.reason)},
        ) from exc
    return out


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def parse_series_matrix(path: Path) -> tuple[dict[str, list[str]], list[str], dict[str, dict[str, float]]]:
    sample_meta: dict[str, list[str]] = {}
    sample_ids: list[str] = []
    matrix: dict[str, dict[str, float]] = {}
    in_table = False
    header: list[str] = []
    with _open_text(path) as f:
        for raw in f:
            parts = [_clean(part) for part in raw.rstrip("\n").split("\t")]
            if not parts:
                continue
            key = parts[0]
            values = parts[1:]
            if key.startswith("!Sample_"):
                meta_key = key
                if meta_key in sample_meta:
                    suffix = 2
                    while f"{key}#{suffix}" in sample_meta:
                        suffix += 1
                    meta_key = f"{key}#{suffix}"
                sample_meta[meta_key] = values
                if key == "!Sample_geo_accession":
                    sample_ids = values
            elif key == "!series_matrix_table_begin":
                in_table = True
            elif in_table and key == "ID_REF":
                header = values
            elif in_table and key == "!series_matrix_table_end":
                break
            elif in_table and header:
                probe = key
                row = {}
                for sample, value in zip(header, values):
                    try:
                        row[sample] = float(value)
                    except ValueError:
                        row = {}
                        break
                if row:
                    matrix[probe] = row
    if not sample_ids and header:
        sample_ids = header
    return sample_meta, sample_ids, matrix


def _sample_blob(sample_meta: dict[str, list[str]], idx: int) -> str:
    values = []
    for key, items in sample_meta.items():
        if idx < len(items):
            values.append(items[idx])
    return " ".join(values)


def _matches(blob: str, patterns: list[str]) -> bool:
    text = blob.lower()
    return any(re.search(pattern.lower(), text) for pattern in patterns if pattern)


def _normalize_field(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower()).strip("_")
    aliases = {
        "disease_state": "condition",
        "phenotype": "condition",
        "treatment": "condition",
        "cell_type": "cell_type",
        "celltype": "cell_type",
    }
    return aliases.get(value, value)


def _clean_group_value(value: str) -> str:
    value = value.strip().strip('"').strip("'")
    value = re.sub(r"\s+", " ", value)
    return value or "unknown"


def _safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower()).strip("_")
    return label[:40] or "group"


def _score_group_field(field: str, values: list[str], case_hint: str, control_hint: str) -> tuple[int, list[str], list[str]]:
    reasons = []
    warnings = []
    field_l = field.lower()
    joined = " ".join(values).lower()
    score = 0
    if any(token in field_l for token in ["condition", "group", "disease", "phenotype", "treatment", "status"]):
        score += 40
        reasons.append(f"group-like metadata column: {field}")
    if any(token in joined for token in ["senescent", "senescence", "aged", "aging", "old", "young", "control", "normal"]):
        score += 25
        reasons.append("values contain aging/senescence/control terms")
    if case_hint and case_hint.lower() in joined:
        score += 20
        reasons.append(f"case hint matched: {case_hint}")
    if control_hint and control_hint.lower() in joined:
        score += 20
        reasons.append(f"control hint matched: {control_hint}")
    if case_hint and control_hint and case_hint.lower() in joined and control_hint.lower() in joined:
        score += 15
        reasons.append("case/control hints both matched the same metadata column")
    if field_l in {"tissue", "source_name_ch1"} and case_hint and control_hint:
        score += 10
        reasons.append(f"tissue/source column accepted because both hints were provided")
    if any(token in field_l for token in ["sex", "gender", "batch", "patient", "subject", "replicate"]):
        score -= 35
        warnings.append(f"column may be confounder rather than biological group: {field}")
    return max(score, 0), reasons, warnings


def _choose_case_control(values: list[str], case_hint: str, control_hint: str) -> tuple[str, str, list[str], list[str]]:
    reasons = []
    warnings = []
    lower = {value.lower(): value for value in values}
    case_terms = [case_hint, "senescent", "senescence", "aged", "old", "disease", "treated", "case", "premature senescent"]
    control_terms = [control_hint, "young", "control", "normal", "untreated", "vehicle", "baseline"]
    case = _first_matching_value(lower, case_terms)
    control = _first_matching_value(lower, control_terms)
    if case:
        reasons.append(f"case value inferred: {case}")
    if control:
        reasons.append(f"control value inferred: {control}")
    if case and control and case != control:
        return case, control, reasons, warnings
    if len(values) == 2:
        warnings.append("case/control orientation inferred from two groups; manual review recommended")
        return values[1], values[0], reasons, warnings
    raise GeoImportError(
        "GEO_AUTO_GROUPING_ORIENTATION_FAILED",
        "metadata_inference",
        "A grouping column was found, but case/control orientation could not be determined.",
        [
            "Provide --case-hint and --control-hint, or use manual geo-import patterns.",
            "Inspect group_inference.json candidate values.",
        ],
        retryable=False,
        details={"values": values},
    )


def _first_matching_value(lower_values: dict[str, str], terms: list[str]) -> str:
    for term in terms:
        term = (term or "").strip().lower()
        if not term:
            continue
        for value_l, original in lower_values.items():
            if term in value_l:
                return original
    return ""


def _extract_characteristic(blob: str, label: str) -> str:
    labels = "patient|disease state|tissue|condition|age|sex|cell type|cell_type|source"
    match = re.search(rf"{re.escape(label)}:\s*(.*?)(?=\s+(?:{labels}):\s|$)", blob, flags=re.IGNORECASE)
    if not match:
        return "unknown"
    value = match.group(1).strip()
    for marker in [" total RNA", " RNA ", " Trizol ", " biotin ", " ftp://", " http://"]:
        if marker in value:
            value = value.split(marker, 1)[0].strip()
    return value or "unknown"


def extract_sample_metadata_table(sample_meta: dict[str, list[str]], sample_ids: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    titles = sample_meta.get("!Sample_title", [])
    for idx, sample_id in enumerate(sample_ids):
        row = {
            "sample_id": sample_id,
            "title": titles[idx] if idx < len(titles) else sample_id,
        }
        for key, values in sample_meta.items():
            if idx >= len(values):
                continue
            clean_key = key.replace("!Sample_", "").replace("#", "_").lower()
            value = values[idx].strip()
            if not value:
                continue
            row.setdefault(clean_key, value)
            if ":" in value and key.lower().startswith("!sample_characteristics"):
                label, content = value.split(":", 1)
                field = _normalize_field(label)
                if field:
                    final = field
                    suffix = 2
                    while final in row and row[final] != content.strip():
                        final = f"{field}_{suffix}"
                        suffix += 1
                    row[final] = content.strip()
        row["geo_blob"] = _sample_blob(sample_meta, idx)[:500]
        rows.append(row)
    return rows


def infer_grouping(
    metadata_rows: list[dict[str, str]],
    case_hint: str = "",
    control_hint: str = "",
    min_confidence: int = 55,
) -> GroupInference:
    if not metadata_rows:
        raise GeoImportError(
            "GEO_METADATA_EMPTY",
            "metadata_inference",
            "No sample metadata rows were available for automatic grouping.",
            ["Confirm the series matrix contains !Sample metadata rows.", "Choose another GSE or provide manual case/control patterns."],
            retryable=False,
        )
    candidates = []
    fields = sorted({field for row in metadata_rows for field in row})
    for field in fields:
        values = [_clean_group_value(row.get(field, "")) for row in metadata_rows]
        non_empty = [value for value in values if value and value != "unknown"]
        unique = sorted(set(non_empty))
        if field in {"sample_id", "geo_accession", "geo_blob", "supplementary_file"}:
            continue
        if len(unique) < 2 or len(unique) > 8:
            continue
        counts = {value: non_empty.count(value) for value in unique}
        if min(counts.values()) < 2:
            continue
        score, reasons, warnings = _score_group_field(field, unique, case_hint, control_hint)
        if score:
            candidates.append((score, field, unique, counts, reasons, warnings))
    if not candidates:
        raise GeoImportError(
            "GEO_AUTO_GROUPING_NO_CANDIDATE",
            "metadata_inference",
            "Automatic grouping could not find a usable metadata column with at least two samples per group.",
            [
                "Inspect metadata_profile.json for available columns and values.",
                "Retry with manual --case-pattern and --control-pattern values.",
                "Use another recommended GSE if sample metadata does not describe the biological contrast.",
            ],
            retryable=False,
            details={"columns": fields[:80]},
        )
    candidates.sort(key=lambda item: item[0], reverse=True)
    score, field, unique, counts, reasons, warnings = candidates[0]
    case_value, control_value, pair_reasons, pair_warnings = _choose_case_control(unique, case_hint, control_hint)
    score += 15 if pair_reasons else 0
    warnings.extend(pair_warnings)
    confidence = min(score, 100)
    if confidence < min_confidence:
        raise GeoImportError(
            "GEO_AUTO_GROUPING_LOW_CONFIDENCE",
            "metadata_inference",
            f"Automatic grouping confidence is too low ({confidence}).",
            [
                "Inspect group_inference.json and metadata_profile.json.",
                "Retry with manual case/control labels or patterns.",
                "Lower --min-confidence only after manually reviewing metadata.",
            ],
            retryable=False,
            details={"best_column": field, "confidence": confidence, "value_counts": counts, "reasons": reasons, "warnings": warnings},
        )
    sample_groups = {}
    for row in metadata_rows:
        value = _clean_group_value(row.get(field, ""))
        if value == case_value:
            sample_groups[row["sample_id"]] = "case"
        elif value == control_value:
            sample_groups[row["sample_id"]] = "control"
    return GroupInference(
        group_column=field,
        case_label=_safe_label(case_value),
        control_label=_safe_label(control_value),
        confidence=confidence,
        reasons=[*reasons, *pair_reasons],
        warnings=warnings,
        value_counts=counts,
        sample_groups=sample_groups,
    )


def infer_grouping_from_column(
    metadata_rows: list[dict[str, str]],
    group_column: str,
    case_label: str = "",
    control_label: str = "",
) -> GroupInference:
    if not metadata_rows:
        raise GeoImportError(
            "GEO_METADATA_EMPTY",
            "manual_metadata_grouping",
            "No sample metadata rows were available for manual grouping.",
            ["Confirm the series matrix contains !Sample metadata rows.", "Choose another GSE or provide expression_matrix.tsv and metadata.tsv manually."],
            retryable=False,
        )
    fields = sorted({field for row in metadata_rows for field in row})
    if group_column not in fields:
        raise GeoImportError(
            "GEO_MANUAL_GROUP_COLUMN_MISSING",
            "manual_metadata_grouping",
            f"Manual group column was not found in GEO metadata: {group_column}",
            [
                "Open data/<GSE>/metadata_profile.json and copy an exact column name.",
                "Use v5 Dataset Gate to correct group_column.",
                "Retry analysis main path after saving the correction.",
            ],
            retryable=False,
            details={"requested_group_column": group_column, "available_columns": fields[:120]},
        )
    values = [_clean_group_value(row.get(group_column, "")) for row in metadata_rows]
    non_empty = [value for value in values if value and value != "unknown"]
    unique = sorted(set(non_empty))
    if len(unique) < 2:
        raise GeoImportError(
            "GEO_MANUAL_GROUP_COLUMN_NOT_USABLE",
            "manual_metadata_grouping",
            f"Manual group column has fewer than two usable values: {group_column}",
            ["Choose a metadata column with both case and control groups.", "Inspect metadata_profile.json value_counts before locking the dataset."],
            retryable=False,
            details={"requested_group_column": group_column, "value_counts": {value: non_empty.count(value) for value in unique}},
        )
    if case_label.strip() and control_label.strip():
        case_value = _clean_group_value(case_label)
        control_value = _clean_group_value(control_label)
        if case_value not in unique or control_value not in unique:
            raise GeoImportError(
                "GEO_MANUAL_GROUP_LABEL_MISSING",
                "manual_metadata_grouping",
                "Manual case/control labels were not found in the selected metadata column.",
                [
                    "Use exact values from data/<GSE>/metadata_profile.json.",
                    "If labels are long free-text values, copy the exact normalized value shown in value_counts.",
                ],
                retryable=False,
                details={"group_column": group_column, "case_label": case_value, "control_label": control_value, "value_counts": {value: non_empty.count(value) for value in unique}},
            )
    else:
        case_value, control_value, _, _ = _choose_case_control(unique, case_label, control_label)
    sample_groups = {}
    for row in metadata_rows:
        value = _clean_group_value(row.get(group_column, ""))
        if value == case_value:
            sample_groups[row["sample_id"]] = "case"
        elif value == control_value:
            sample_groups[row["sample_id"]] = "control"
    counts = {value: non_empty.count(value) for value in unique}
    return GroupInference(
        group_column=group_column,
        case_label=_safe_label(case_value),
        control_label=_safe_label(control_value),
        confidence=100,
        reasons=[f"manual DATASETS_LOCKED grouping column: {group_column}"],
        warnings=[],
        value_counts=counts,
        sample_groups=sample_groups,
    )


def build_metadata_from_inference(
    metadata_table: list[dict[str, str]],
    inference: GroupInference,
) -> tuple[list[dict], list[str]]:
    by_sample = {row["sample_id"]: row for row in metadata_table}
    rows = []
    warnings = []
    for sample_id, role in inference.sample_groups.items():
        source = by_sample.get(sample_id)
        if not source:
            warnings.append(f"inferred sample missing from metadata table: {sample_id}")
            continue
        group = inference.case_label if role == "case" else inference.control_label
        rows.append(
            {
                "sample_id": sample_id,
                "group": group,
                "patient_id": source.get("patient") or source.get("subject") or source.get("individual") or "unknown",
                "batch": source.get("batch") or source.get("patient") or source.get("subject") or "unknown",
                "tissue": source.get("tissue") or source.get("source_name_ch1") or "unknown",
                "title": source.get("title", sample_id),
                "geo_blob": source.get("geo_blob", "")[:500],
            }
        )
    return rows, warnings


def build_metadata(
    sample_meta: dict[str, list[str]],
    sample_ids: list[str],
    case_label: str,
    control_label: str,
    case_patterns: list[str],
    control_patterns: list[str],
) -> tuple[list[dict], list[str]]:
    rows = []
    warnings = []
    titles = sample_meta.get("!Sample_title", [])
    for idx, sample_id in enumerate(sample_ids):
        blob = _sample_blob(sample_meta, idx)
        case_match = _matches(blob, case_patterns)
        control_match = _matches(blob, control_patterns)
        if case_match and control_match:
            warnings.append(f"sample matched both case and control patterns and was skipped: {sample_id}")
            continue
        if case_match:
            group = case_label
        elif control_match:
            group = control_label
        else:
            warnings.append(f"sample not assigned to case/control and was skipped: {sample_id}")
            continue
        rows.append(
            {
                "sample_id": sample_id,
                "group": group,
                "patient_id": _extract_characteristic(blob, "patient"),
                "batch": _extract_characteristic(blob, "patient"),
                "tissue": _extract_characteristic(blob, "tissue"),
                "title": titles[idx] if idx < len(titles) else sample_id,
                "geo_blob": blob[:500],
            }
        )
    return rows, warnings


def _looks_like_symbol(value: str) -> bool:
    if not value or len(value) > 25:
        return False
    if value.startswith(("ENSG", "ENSMUS", "ILMN_", "AFFX", "cg")):
        return False
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9.-]{1,24}$", value))


def parse_platform_annotation(path: Path, symbol_column: str | None = None) -> dict[str, str]:
    candidates = [
        symbol_column,
        "Gene symbol",
        "Gene Symbol",
        "GENE_SYMBOL",
        "Symbol",
        "gene_symbol",
        "Gene",
    ]
    candidates = [c for c in candidates if c]
    with _open_text(path) as f:
        buffered = []
        for raw in f:
            if raw.startswith("#") or not raw.strip():
                continue
            if raw.startswith("ID\t") or raw.startswith("ID,"):
                delimiter = "\t" if "\t" in raw else ","
                reader = csv.DictReader([raw, *list(f)], delimiter=delimiter)
                fields = reader.fieldnames or []
                column = next((name for name in candidates if name in fields), None)
                if column is None:
                    return {}
                mapping = {}
                for row in reader:
                    probe = row.get("ID", "").strip()
                    symbol = (row.get(column) or "").split(" /// ")[0].split(";")[0].strip()
                    if probe and _looks_like_symbol(symbol):
                        mapping[probe] = symbol
                return mapping
            buffered.append(raw)
            if len(buffered) > 2000:
                break
    return {}


def infer_platform_id(sample_meta: dict[str, list[str]]) -> str:
    values: list[str] = []
    for key, items in sample_meta.items():
        if "platform_id" not in key.lower():
            continue
        values.extend(str(item).strip().upper() for item in items if str(item).strip())
    unique = sorted({item for item in values if re.match(r"^GPL\d+$", item)})
    return unique[0] if len(unique) == 1 else ""


def download_platform_annotation_for_series(
    data_dir: Path,
    sample_meta: dict[str, list[str]],
    *,
    force: bool = False,
) -> Path | None:
    platform_id = infer_platform_id(sample_meta)
    if not platform_id:
        return None
    out = data_dir / f"{platform_id}.annot.gz"
    try:
        return download_file(geo_platform_annotation_url(platform_id), out, force)
    except GeoImportError:
        return None


def collapse_to_gene_matrix(
    probe_matrix: dict[str, dict[str, float]],
    samples: list[str],
    metadata_rows: list[dict],
    probe_to_symbol: dict[str, str] | None = None,
) -> dict[str, list[float]]:
    selected_samples = [row["sample_id"] for row in metadata_rows]
    sample_set = set(samples)
    if any(sample not in sample_set for sample in selected_samples):
        missing = [sample for sample in selected_samples if sample not in sample_set]
        raise ValueError(f"metadata sample ids missing from matrix: {missing}")
    by_gene: dict[str, list[list[float]]] = defaultdict(list)
    mapping = probe_to_symbol or {}
    for probe, values in probe_matrix.items():
        symbol = mapping.get(probe)
        if not symbol and _looks_like_symbol(probe):
            symbol = probe
        if not symbol:
            continue
        by_gene[symbol].append([values[sample] for sample in selected_samples])
    collapsed = {}
    for symbol, rows in by_gene.items():
        collapsed[symbol] = [sum(row[idx] for row in rows) / len(rows) for idx in range(len(selected_samples))]
    return collapsed


def _write_metadata(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["sample_id", "group", "patient_id", "batch", "tissue", "title", "geo_blob"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def _write_expression(path: Path, metadata_rows: list[dict], matrix: dict[str, list[float]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    samples = [row["sample_id"] for row in metadata_rows]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["gene_symbol", *samples])
        for gene in sorted(matrix):
            writer.writerow([gene, *[f"{value:.6g}" for value in matrix[gene]]])


def _write_card(
    path: Path,
    accession: str,
    tissue: str,
    organism: str,
    case_label: str,
    control_label: str,
    case_n: int,
    control_n: int,
    limitations: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"dataset_id: {accession}",
        "source: GEO",
        f"accession: {accession}",
        "modality: bulk_expression",
        f"organism: {organism}",
        f"tissue: {tissue}",
        "contrast:",
        f"  case: {case_label}",
        f"  control: {control_label}",
        "sample_summary:",
        f"  case_n: {case_n}",
        f"  control_n: {control_n}",
        f"  donor_n: {case_n + control_n}",
        "metadata_fields: [sample_id, group, patient_id, batch, tissue, title, geo_blob]",
        "matrix_available: true",
        "license_status: public",
        "file_paths:",
        f"  expression_matrix: data/{accession}/expression_matrix.tsv",
        f"  metadata: data/{accession}/metadata.tsv",
        f"known_limitations: [{', '.join(limitations)}]",
        "recommended_use: [bulk_deg]",
        "blocked_use: []",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def geo_status_path(project_dir: Path, accession: str) -> Path:
    return project_dir / "data" / accession.upper() / "geo_import_status.json"


def _write_status(project_dir: Path, accession: str, status: str, payload: dict) -> None:
    path = geo_status_path(project_dir, accession)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "accession": accession.upper(),
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_failure(project_dir: Path, accession: str, error: GeoImportError) -> None:
    _write_status(project_dir, accession, "failed", {"error": error.to_dict()})


def _write_metadata_profile(data_dir: Path, metadata_table: list[dict[str, str]]) -> Path:
    path = data_dir / "metadata_profile.json"
    fields = sorted({field for row in metadata_table for field in row})
    profile = {
        "sample_count": len(metadata_table),
        "columns": [],
    }
    for field in fields:
        values = [_clean_group_value(row.get(field, "")) for row in metadata_table]
        non_empty = [value for value in values if value and value != "unknown"]
        counts = {value: non_empty.count(value) for value in sorted(set(non_empty))[:30]}
        profile["columns"].append(
            {
                "name": field,
                "non_empty": len(non_empty),
                "unique_count": len(set(non_empty)),
                "value_counts": counts,
            }
        )
    path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _write_handoff_manifest(
    data_dir: Path,
    accession: str,
    result: GeoImportResult,
    inference: GroupInference | None = None,
) -> Path:
    path = data_dir / "handoff_manifest.json"
    payload = {
        "accession": accession,
        "route": "geo_series_matrix",
        "analysis_ready": True,
        "artifacts": {
            "series_matrix": str(result.series_matrix),
            "expression_matrix": str(result.expression_matrix),
            "metadata": str(result.metadata),
            "dataset_card": str(result.dataset_card),
        },
        "sample_summary": {
            "samples": result.samples,
            "case_n": result.case_n,
            "control_n": result.control_n,
        },
        "group_inference": inference.to_dict() if inference else None,
        "warnings": result.warnings,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _sample_preview(sample_meta: dict[str, list[str]], sample_ids: list[str], limit: int = 8) -> list[dict[str, str]]:
    rows = []
    for idx, sample_id in enumerate(sample_ids[:limit]):
        rows.append({"sample_id": sample_id, "text": _sample_blob(sample_meta, idx)[:500]})
    return rows


def _ensure_min_samples(
    accession: str,
    case_n: int,
    control_n: int,
    metadata_rows: list[dict],
    warnings: list[str],
    sample_preview: list[dict[str, str]] | None = None,
) -> None:
    if case_n == 0 or control_n == 0:
        raise GeoImportError(
            "GEO_GROUP_ASSIGNMENT_FAILED",
            "metadata_grouping",
            "Case/control assignment failed; at least one group has zero samples.",
            [
                "Inspect data/<GSE>/geo_import_status.json for sample text previews.",
                "Use broader or more specific --case-pattern and --control-pattern values.",
                "Avoid overlapping keywords that match both groups.",
            ],
            retryable=False,
            details={
                "case_n": case_n,
                "control_n": control_n,
                "assigned_samples": len(metadata_rows),
                "warnings": warnings[:20],
                "sample_preview": sample_preview or [],
                "example_command": (
                    f"python tc_lite.py geo-import --project vascular_aging_demo --accession {accession} "
                    '--case-label case --control-label control --case-pattern "case keyword" --control-pattern "control keyword"'
                ),
            },
        )
    if case_n < 2 or control_n < 2:
        raise GeoImportError(
            "GEO_SAMPLE_SIZE_TOO_SMALL",
            "metadata_grouping",
            "GEO import produced fewer than two samples in case or control; DEG would be unstable.",
            [
                "Review whether more samples can be assigned with better case/control patterns.",
                "Use this dataset as reference evidence only, not as bulk DEG input.",
                "Choose another recommended GSE with larger groups.",
            ],
            retryable=False,
            details={"case_n": case_n, "control_n": control_n, "assigned_samples": len(metadata_rows), "warnings": warnings[:20]},
        )


def _platform_annotation_error(accession: str, platform_annotation: Path | None, symbol_column: str | None) -> GeoImportError:
    recovery = [
        "Download the GEO platform annotation file, for example GPLxxxx.annot.gz, and pass --platform-annotation.",
        "If the gene symbol column is not auto-detected, pass --symbol-column with the exact column name.",
        "For RNA-seq gene-level matrices, confirm ID_REF already contains gene symbols.",
    ]
    details = {
        "platform_annotation": str(platform_annotation) if platform_annotation else "",
        "symbol_column": symbol_column or "",
        "example_command": (
            f"python tc_lite.py geo-import --project vascular_aging_demo --accession {accession} "
            '--case-label case --control-label control --case-pattern "case keyword" --control-pattern "control keyword" '
            '--platform-annotation D:\\path\\GPLxxxx.annot.gz --symbol-column "Gene symbol"'
        ),
    }
    return GeoImportError(
        "GEO_PLATFORM_ANNOTATION_MISSING",
        "probe_to_gene_mapping",
        "No gene-symbol expression rows were produced; probe IDs could not be mapped to gene symbols.",
        recovery,
        retryable=False,
        details=details,
    )


def import_geo_series(
    project_dir: Path,
    accession: str,
    case_label: str,
    control_label: str,
    case_patterns: list[str],
    control_patterns: list[str],
    tissue: str = "unknown",
    organism: str = "human",
    platform_annotation: Path | None = None,
    symbol_column: str | None = None,
    force_download: bool = False,
) -> GeoImportResult:
    accession = accession.upper()
    data_dir = project_dir / "data" / accession
    series_path = data_dir / f"{accession}_series_matrix.txt.gz"
    try:
        _write_status(
            project_dir,
            accession,
            "running",
            {
                "stage": "download",
                "inputs": {
                    "case_label": case_label,
                    "control_label": control_label,
                    "case_patterns": case_patterns,
                    "control_patterns": control_patterns,
                    "tissue": tissue,
                    "organism": organism,
                    "platform_annotation": str(platform_annotation) if platform_annotation else "",
                    "symbol_column": symbol_column or "",
                },
            },
        )
        download_file(geo_series_matrix_url(accession), series_path, force_download)
        _write_status(project_dir, accession, "running", {"stage": "parse_series_matrix", "series_matrix": str(series_path)})
        sample_meta, samples, probe_matrix = parse_series_matrix(series_path)
        if not samples or not probe_matrix:
            raise GeoImportError(
                "GEO_SERIES_MATRIX_PARSE_FAILED",
                "parse_series_matrix",
                "The GEO series matrix did not contain usable sample ids and expression values.",
                [
                    "Confirm the GEO series has a downloadable expression series matrix.",
                    "Choose another recommended GSE if this record is not expression data.",
                    "If the matrix is custom formatted, prepare expression_matrix.tsv and metadata.tsv manually.",
                ],
                retryable=False,
                details={"sample_count": len(samples), "matrix_rows": len(probe_matrix), "series_matrix": str(series_path)},
            )
        metadata_rows, warnings = build_metadata(
            sample_meta,
            samples,
            case_label,
            control_label,
            case_patterns,
            control_patterns,
        )
        _write_status(
            project_dir,
            accession,
            "running",
            {
                "stage": "metadata_grouping",
                "sample_preview": _sample_preview(sample_meta, samples),
                "assigned_samples": len(metadata_rows),
                "warnings": warnings[:50],
            },
        )
        case_n = sum(1 for row in metadata_rows if row["group"] == case_label)
        control_n = sum(1 for row in metadata_rows if row["group"] == control_label)
        preview = _sample_preview(sample_meta, samples)
        _ensure_min_samples(accession, case_n, control_n, metadata_rows, warnings, preview)
        platform_annotation = platform_annotation or download_platform_annotation_for_series(data_dir, sample_meta, force=force_download)
        probe_to_symbol = parse_platform_annotation(platform_annotation, symbol_column) if platform_annotation else {}
        expression = collapse_to_gene_matrix(probe_matrix, samples, metadata_rows, probe_to_symbol)
        if not expression:
            raise _platform_annotation_error(accession, platform_annotation, symbol_column)
    except GeoImportError as exc:
        _write_failure(project_dir, accession, exc)
        raise
    except Exception as exc:
        wrapped = GeoImportError(
            "GEO_IMPORT_UNEXPECTED_ERROR",
            "unknown",
            str(exc),
            [
                "Check data/<GSE>/geo_import_status.json for the last completed stage.",
                "Retry with --force-download if the cached file may be incomplete.",
                "Use GEO auto-discovery to choose another candidate dataset if this one is not compatible.",
            ],
            retryable=True,
            details={"exception_type": type(exc).__name__},
        )
        _write_failure(project_dir, accession, wrapped)
        raise wrapped from exc
    expression_path = data_dir / "expression_matrix.tsv"
    metadata_path = data_dir / "metadata.tsv"
    card_path = project_dir / "dataset_cards" / f"{accession}.yaml"
    readme_path = data_dir / "README.md"
    _write_metadata(metadata_path, metadata_rows)
    _write_expression(expression_path, metadata_rows, expression)
    limitations = [
        "auto-imported from GEO series matrix",
        "case/control assignment based on user-supplied text patterns",
        "MVP lightweight DEG; inspect metadata before interpreting",
    ]
    if not probe_to_symbol:
        limitations.append("probe IDs used only when they look like gene symbols")
    _write_card(card_path, accession, tissue, organism, case_label, control_label, case_n, control_n, limitations)
    validation_errors = validate_dataset_card(card_path)
    if validation_errors:
        error = GeoImportError(
            "GEO_DATASET_CARD_INVALID",
            "dataset_card_validation",
            "Generated DatasetCard did not pass schema validation.",
            [
                "Inspect the generated dataset card under dataset_cards/.",
                "Fix missing metadata fields or regenerate with more complete import parameters.",
            ],
            retryable=False,
            details={"validation_errors": validation_errors},
        )
        _write_failure(project_dir, accession, error)
        raise error
    readme_path.write_text(
        "\n".join(
            [
                f"# {accession} GEO Import",
                "",
                f"Series matrix: {geo_series_matrix_url(accession)}",
                f"Samples retained: {len(metadata_rows)}",
                f"Gene rows: {len(expression)}",
                f"Case: {case_label} ({case_n}) patterns={case_patterns}",
                f"Control: {control_label} ({control_n}) patterns={control_patterns}",
                "",
                "Review the generated metadata.tsv before using the result for scientific claims.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = GeoImportResult(
        accession=accession,
        data_dir=data_dir,
        series_matrix=series_path,
        expression_matrix=expression_path,
        metadata=metadata_path,
        dataset_card=card_path,
        readme=readme_path,
        samples=len(metadata_rows),
        genes=len(expression),
        case_n=case_n,
        control_n=control_n,
        warnings=warnings,
    )
    _write_status(project_dir, accession, "success", {"stage": "complete", "result": result.to_dict()})
    _write_handoff_manifest(data_dir, accession, result)
    return result


def import_geo_series_auto(
    project_dir: Path,
    accession: str,
    tissue: str = "unknown",
    organism: str = "human",
    platform_annotation: Path | None = None,
    symbol_column: str | None = None,
    force_download: bool = False,
    case_hint: str = "",
    control_hint: str = "",
    case_label: str = "",
    control_label: str = "",
    group_column: str = "",
    min_confidence: int = 55,
) -> GeoImportResult:
    accession = accession.upper()
    data_dir = project_dir / "data" / accession
    series_path = data_dir / f"{accession}_series_matrix.txt.gz"
    try:
        _write_status(
            project_dir,
            accession,
            "running",
            {
                "stage": "auto_download",
                "mode": "auto_grouping",
                "inputs": {
                    "case_hint": case_hint,
                    "control_hint": control_hint,
                    "group_column": group_column,
                    "tissue": tissue,
                    "organism": organism,
                    "platform_annotation": str(platform_annotation) if platform_annotation else "",
                    "symbol_column": symbol_column or "",
                    "min_confidence": min_confidence,
                },
            },
        )
        download_file(geo_series_matrix_url(accession), series_path, force_download)
        sample_meta, samples, probe_matrix = parse_series_matrix(series_path)
        if not samples or not probe_matrix:
            raise GeoImportError(
                "GEO_SERIES_MATRIX_PARSE_FAILED",
                "parse_series_matrix",
                "The GEO series matrix did not contain usable sample ids and expression values.",
                [
                    "Confirm the GEO series has a downloadable expression series matrix.",
                    "Choose another recommended GSE if this record is not expression data.",
                    "If the matrix is custom formatted, prepare expression_matrix.tsv and metadata.tsv manually.",
                ],
                retryable=False,
                details={"sample_count": len(samples), "matrix_rows": len(probe_matrix), "series_matrix": str(series_path)},
            )
        metadata_table = extract_sample_metadata_table(sample_meta, samples)
        profile_path = _write_metadata_profile(data_dir, metadata_table)
        if group_column.strip():
            inference = infer_grouping_from_column(metadata_table, group_column.strip(), case_label=case_label, control_label=control_label)
        else:
            inference = infer_grouping(metadata_table, case_hint=case_hint, control_hint=control_hint, min_confidence=min_confidence)
        if case_label.strip() and not group_column.strip():
            inference.case_label = _safe_label(case_label)
        if control_label.strip() and not group_column.strip():
            inference.control_label = _safe_label(control_label)
        inference_path = data_dir / "group_inference.json"
        inference_path.write_text(json.dumps(inference.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        metadata_rows, warnings = build_metadata_from_inference(metadata_table, inference)
        warnings.extend(inference.warnings)
        case_n = sum(1 for row in metadata_rows if row["group"] == inference.case_label)
        control_n = sum(1 for row in metadata_rows if row["group"] == inference.control_label)
        _ensure_min_samples(accession, case_n, control_n, metadata_rows, warnings, _sample_preview(sample_meta, samples))
        platform_annotation = platform_annotation or download_platform_annotation_for_series(data_dir, sample_meta, force=force_download)
        probe_to_symbol = parse_platform_annotation(platform_annotation, symbol_column) if platform_annotation else {}
        expression = collapse_to_gene_matrix(probe_matrix, samples, metadata_rows, probe_to_symbol)
        if not expression:
            raise _platform_annotation_error(accession, platform_annotation, symbol_column)
    except GeoImportError as exc:
        _write_failure(project_dir, accession, exc)
        raise
    except Exception as exc:
        wrapped = GeoImportError(
            "GEO_AUTO_IMPORT_UNEXPECTED_ERROR",
            "auto_grouping",
            str(exc),
            [
                "Check metadata_profile.json and group_inference.json if they were created.",
                "Retry with --case-hint and --control-hint.",
                "Fall back to manual geo-import with explicit case/control patterns.",
            ],
            retryable=True,
            details={"exception_type": type(exc).__name__},
        )
        _write_failure(project_dir, accession, wrapped)
        raise wrapped from exc
    expression_path = data_dir / "expression_matrix.tsv"
    metadata_path = data_dir / "metadata.tsv"
    card_path = project_dir / "dataset_cards" / f"{accession}.yaml"
    readme_path = data_dir / "README.md"
    _write_metadata(metadata_path, metadata_rows)
    _write_expression(expression_path, metadata_rows, expression)
    limitations = [
        "auto-imported from GEO series matrix",
        f"case/control assignment inferred from metadata column {inference.group_column} with confidence {inference.confidence}",
        "MVP lightweight DEG; inspect metadata_profile.json and group_inference.json before interpreting",
    ]
    if not probe_to_symbol:
        limitations.append("probe IDs used only when they look like gene symbols")
    _write_card(card_path, accession, tissue, organism, inference.case_label, inference.control_label, case_n, control_n, limitations)
    validation_errors = validate_dataset_card(card_path)
    if validation_errors:
        error = GeoImportError(
            "GEO_DATASET_CARD_INVALID",
            "dataset_card_validation",
            "Generated DatasetCard did not pass schema validation.",
            [
                "Inspect the generated dataset card under dataset_cards/.",
                "Fix missing metadata fields or regenerate with more complete import parameters.",
            ],
            retryable=False,
            details={"validation_errors": validation_errors},
        )
        _write_failure(project_dir, accession, error)
        raise error
    readme_path.write_text(
        "\n".join(
            [
                f"# {accession} GEO Auto Import",
                "",
                f"Series matrix: {geo_series_matrix_url(accession)}",
                f"Samples retained: {len(metadata_rows)}",
                f"Gene rows: {len(expression)}",
                f"Group column: {inference.group_column}",
                f"Case: {inference.case_label} ({case_n})",
                f"Control: {inference.control_label} ({control_n})",
                f"Inference confidence: {inference.confidence}",
                f"Metadata profile: {profile_path.name}",
                f"Group inference: {inference_path.name}",
                "",
                "Review the generated metadata.tsv and group_inference.json before using the result for scientific claims.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = GeoImportResult(
        accession=accession,
        data_dir=data_dir,
        series_matrix=series_path,
        expression_matrix=expression_path,
        metadata=metadata_path,
        dataset_card=card_path,
        readme=readme_path,
        samples=len(metadata_rows),
        genes=len(expression),
        case_n=case_n,
        control_n=control_n,
        warnings=warnings,
    )
    manifest_path = _write_handoff_manifest(data_dir, accession, result, inference)
    _write_status(
        project_dir,
        accession,
        "success",
        {
            "stage": "complete",
            "mode": "auto_grouping",
            "result": result.to_dict(),
            "metadata_profile": str(profile_path),
            "group_inference": str(inference_path),
            "handoff_manifest": str(manifest_path),
        },
    )
    return result
