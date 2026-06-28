import csv
import json
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_db import migrate_evidence_db
from .evidence_levels import classify_evidence_level
from .v4 import content_hash


EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
FULLTEXT_SCHEMA = "v4.fulltext_literature_run/0.1"


def run_fulltext_literature(
    project_dir: Path,
    pmid: list[str] | None = None,
    pdf: list[str] | None = None,
    text: list[str] | None = None,
    limit: int = 20,
    timeout: int = 30,
    ocr: bool = False,
    ocr_pages: int = 3,
    ocr_lang: str = "en",
) -> dict[str, Any]:
    out_dir = project_dir / "results" / "fulltext_literature"
    out_dir.mkdir(parents=True, exist_ok=True)
    pmids = [item.strip() for item in (pmid or []) if item.strip()]
    if not pmids:
        pmids = _pmids_from_literature(project_dir)[:limit]
    docs: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for item in pmids[:limit]:
        try:
            docs.append(_fetch_pmc_fulltext(project_dir, out_dir, item, timeout))
        except Exception as exc:
            failures.append({"source": item, "stage": "pmc_fetch", "reason": str(exc)})
    for item in pdf or []:
        try:
            docs.append(_parse_pdf_upload(project_dir, out_dir, Path(item), use_ocr=ocr, ocr_pages=ocr_pages, ocr_lang=ocr_lang))
        except Exception as exc:
            failures.append({"source": item, "stage": "pdf_parse", "reason": str(exc)})
    for item in text or []:
        try:
            docs.append(_parse_text_upload(project_dir, out_dir, Path(item)))
        except Exception as exc:
            failures.append({"source": item, "stage": "text_parse", "reason": str(exc)})
    evidence_rows = _extract_fulltext_evidence(project_dir, out_dir, docs)
    evidence_path = out_dir / "fulltext_evidence.tsv"
    _write_evidence(evidence_path, evidence_rows)
    inserted = import_fulltext_evidence(project_dir, evidence_path)
    manifest = {
        "schema_version": FULLTEXT_SCHEMA,
        "project_id": project_dir.name,
        "requested_pmids": pmids[:limit],
        "document_count": len(docs),
        "failure_count": len(failures),
        "failures": failures,
        "ocr": {"requested": ocr, "pages": ocr_pages, "lang": ocr_lang},
        "evidence_row_count": len(evidence_rows),
        "inserted_evidence_rows": inserted,
        "artifacts": {
            "documents_json": _rel(out_dir / "fulltext_documents.json", project_dir),
            "evidence_tsv": _rel(evidence_path, project_dir),
        },
        "generated_at": _now(),
    }
    manifest["run_id"] = "fulltext_" + content_hash(manifest)[:16]
    documents_path = out_dir / "fulltext_documents.json"
    run_path = out_dir / "fulltext_literature_run.json"
    documents_path.write_text(json.dumps({"documents": docs}, indent=2, ensure_ascii=False), encoding="utf-8")
    run_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    publish_paths = [documents_path, evidence_path, run_path]
    publish_paths.extend(project_dir / doc.get("artifact_path", "") for doc in docs if doc.get("artifact_path"))
    publish_paths.extend(project_dir / doc.get("ocr_artifact", "") for doc in docs if doc.get("ocr_artifact"))
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            publish_paths,
            producer="fulltext_literature",
            artifact_type="fulltext_literature_output",
            task_id="fulltext_literature",
            qc_status="pass" if docs else "review",
        )
    except Exception:
        pass
    return manifest


def import_fulltext_evidence(project_dir: Path, evidence_tsv: Path) -> int:
    migrate_evidence_db(project_dir)
    con = sqlite3.connect(project_dir / "evidence.sqlite", timeout=30)
    inserted = 0
    try:
        with evidence_tsv.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                level, weight, basis = classify_evidence_level(row)
                con.execute(
                    """
                    INSERT OR REPLACE INTO evidence_item
                    (evidence_id, project_id, entity_symbol, entity_type, disease_context, organism, tissue, route,
                     evidence_type, direction, effect_size, p_value, quality_score, evidence_level, evidence_weight,
                     evidence_basis, review_status, source_dataset, artifact_path, run_id, artifact_id,
                     module_version, limitation, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["evidence_id"],
                        row["project_id"],
                        row["entity_symbol"],
                        row.get("entity_type", "gene"),
                        row.get("disease_context", ""),
                        row.get("organism", ""),
                        row.get("tissue", ""),
                        row.get("route", ""),
                        row.get("evidence_type", "fulltext_literature"),
                        row.get("direction", ""),
                        _float_or_none(row.get("effect_size")),
                        _float_or_none(row.get("p_value")),
                        _float_or_none(row.get("quality_score")) or 0.65,
                        level,
                        weight,
                        basis,
                        row.get("review_status", "PENDING"),
                        row.get("source_dataset", ""),
                        row.get("artifact_path", ""),
                        row.get("run_id", "fulltext_literature"),
                        row.get("artifact_id", ""),
                        row.get("module_version", "fulltext_literature_v1"),
                        row.get("limitation", ""),
                        row.get("created_at", _now()),
                    ),
                )
                inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


def _fetch_pmc_fulltext(project_dir: Path, out_dir: Path, pmid: str, timeout: int) -> dict[str, Any]:
    pmcid = _pmid_to_pmcid(pmid, timeout)
    if not pmcid:
        raise RuntimeError("no PMC Open Access full text found for PMID")
    params = urllib.parse.urlencode({"db": "pmc", "id": pmcid.replace("PMC", ""), "retmode": "xml"})
    xml = _get_text(f"{EUTILS}/efetch.fcgi?{params}", timeout)
    path = out_dir / f"{pmcid}.xml"
    path.write_text(xml, encoding="utf-8")
    title = _clean_xml(_tag(xml, "article-title"))
    body = _clean_xml(" ".join(re.findall(r"<body[^>]*>(.*?)</body>", xml, flags=re.DOTALL)))
    abstract = _clean_xml(" ".join(re.findall(r"<abstract[^>]*>(.*?)</abstract>", xml, flags=re.DOTALL)))
    return {
        "source_type": "pmc_open_access",
        "pmid": pmid,
        "pmcid": pmcid,
        "title": title,
        "text": (abstract + " " + body).strip(),
        "artifact_path": _rel(path, project_dir),
        "url": f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/",
    }


def _parse_pdf_upload(project_dir: Path, out_dir: Path, path: Path, use_ocr: bool = False, ocr_pages: int = 3, ocr_lang: str = "en") -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    text = ""
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception as exc:
            if not use_ocr:
                raise RuntimeError("PDF parsing requires pypdf or PyPDF2 installed, or rerun with --ocr") from exc
            PdfReader = None
    if PdfReader is not None:
        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    ocr_artifact = ""
    if use_ocr or len(text.strip()) < 500:
        try:
            from .ocr import ocr_pdf_with_paddle

            ocr_result = ocr_pdf_with_paddle(path, out_dir, max_pages=ocr_pages, lang=ocr_lang)
            ocr_artifact = _rel(Path(ocr_result.get("artifact_path", "")), project_dir) if ocr_result.get("artifact_path") else ""
            if len(ocr_result.get("text", "")) > len(text):
                text = ocr_result.get("text", "")
        except Exception as exc:
            if use_ocr:
                raise RuntimeError(f"PaddleOCR PDF parsing failed: {exc}") from exc
    copied = out_dir / path.name
    if path.resolve() != copied.resolve():
        copied.write_bytes(path.read_bytes())
    return {"source_type": "uploaded_pdf_ocr" if ocr_artifact else "uploaded_pdf", "pmid": "", "pmcid": "", "title": path.stem, "text": text, "artifact_path": _rel(copied, project_dir), "ocr_artifact": ocr_artifact, "url": ""}


def _parse_text_upload(project_dir: Path, out_dir: Path, path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    copied = out_dir / path.name
    if path.resolve() != copied.resolve():
        copied.write_text(text, encoding="utf-8")
    return {"source_type": "uploaded_text", "pmid": "", "pmcid": "", "title": path.stem, "text": _clean_xml(text), "artifact_path": _rel(copied, project_dir), "url": ""}


def _extract_fulltext_evidence(project_dir: Path, out_dir: Path, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spec = _read_json(project_dir / "research_spec.json", {})
    disease = spec.get("disease_scope", {}).get("canonical", spec.get("research_theme", ""))
    rows = []
    created = _now()
    for doc in docs:
        text = doc.get("text", "")
        symbols = _candidate_symbols(text)
        sections = _section_signals(text)
        for symbol in symbols:
            confidence = _confidence(text, symbol, sections)
            limitation = f"Full-text evidence from {doc.get('source_type')}; sections={','.join(k for k, v in sections.items() if v)}; title={doc.get('title', '')[:120]}"
            rows.append(
                {
                    "evidence_id": "fulltext_" + content_hash({"project": project_dir.name, "symbol": symbol, "source": doc.get("artifact_path", "")})[:18],
                    "project_id": project_dir.name,
                    "entity_symbol": symbol,
                    "entity_type": "gene",
                    "disease_context": disease,
                    "organism": "human_or_model",
                    "tissue": _infer_tissue(text, spec),
                    "route": _infer_route(text),
                    "evidence_type": "fulltext_literature",
                    "direction": "support",
                    "effect_size": "",
                    "p_value": "",
                    "quality_score": f"{confidence:.2f}",
                    "evidence_level": "L1_fulltext",
                    "evidence_weight": "0.55",
                    "evidence_basis": "Full text parsed from PMC Open Access or uploaded document.",
                    "review_status": "PENDING",
                    "source_dataset": doc.get("pmcid") or doc.get("pmid") or doc.get("source_type", ""),
                    "artifact_path": doc.get("artifact_path", ""),
                    "run_id": "fulltext_literature",
                    "artifact_id": "artifact_" + content_hash({"path": doc.get("artifact_path", "")})[:16],
                    "module_version": "fulltext_literature_v1",
                    "limitation": limitation,
                    "created_at": created,
                }
            )
    return rows


def _candidate_symbols(text: str) -> list[str]:
    blacklist = {"DNA", "RNA", "PCR", "ELISA", "FIG", "TABLE", "COVID", "SASP", "PMC", "PMID", "BMI", "ATP"}
    tokens = re.findall(r"\b[A-Z][A-Z0-9]{1,9}\b", text or "")
    counts = {}
    for token in tokens:
        if token in blacklist:
            continue
        counts[token] = counts.get(token, 0) + 1
    return [token for token, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:20]]


def _section_signals(text: str) -> dict[str, bool]:
    lower = (text or "").lower()
    return {
        "methods": any(term in lower for term in ["methods", "materials and methods", "participants", "samples"]),
        "results": any(term in lower for term in ["results", "we found", "increased", "decreased", "significant"]),
        "cell_type": any(term in lower for term in ["myocyte", "macrophage", "endothelial", "fibroblast", "satellite cell", "cell type"]),
        "experiment": any(term in lower for term in ["western blot", "qpcr", "elisa", "immunofluorescence", "flow cytometry", "knockdown", "neutralization"]),
    }


def _confidence(text: str, symbol: str, sections: dict[str, bool]) -> float:
    score = 0.45
    count = len(re.findall(rf"\b{re.escape(symbol)}\b", text or ""))
    if count >= 2:
        score += 0.1
    if sections.get("results"):
        score += 0.1
    if sections.get("methods"):
        score += 0.05
    if sections.get("cell_type"):
        score += 0.08
    if sections.get("experiment"):
        score += 0.08
    return min(score, 0.85)


def _infer_route(text: str) -> str:
    lower = (text or "").lower()
    if any(term in lower for term in ["secreted", "secretion", "cytokine", "chemokine", "plasma"]):
        return "secreted"
    if any(term in lower for term in ["cell surface", "membrane", "receptor"]):
        return "surface"
    return "unknown"


def _infer_tissue(text: str, spec: dict[str, Any]) -> str:
    lower = (text or "").lower()
    for tissue in spec.get("priority_tissues", []):
        if str(tissue).lower() in lower:
            return str(tissue)
    if "skeletal muscle" in lower:
        return "skeletal muscle"
    return ", ".join(spec.get("priority_tissues", [])[:2])


def _pmid_to_pmcid(pmid: str, timeout: int) -> str:
    params = urllib.parse.urlencode({"ids": pmid, "format": "json"})
    data = json.loads(_get_text(f"https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?{params}", timeout))
    records = data.get("records", [])
    if not records:
        return ""
    return records[0].get("pmcid", "")


def _pmids_from_literature(project_dir: Path) -> list[str]:
    path = project_dir / "results" / "literature_validation" / "pubmed_articles.tsv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [row.get("pmid", "") for row in csv.DictReader(f, delimiter="\t") if row.get("pmid")]


def _write_evidence(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "evidence_id",
        "project_id",
        "entity_symbol",
        "entity_type",
        "disease_context",
        "organism",
        "tissue",
        "route",
        "evidence_type",
        "direction",
        "effect_size",
        "p_value",
        "quality_score",
        "evidence_level",
        "evidence_weight",
        "evidence_basis",
        "review_status",
        "source_dataset",
        "artifact_path",
        "run_id",
        "artifact_id",
        "module_version",
        "limitation",
        "created_at",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _get_text(url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "TargetCompassLite/0.4 fulltext literature"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _tag(xml: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", xml, flags=re.DOTALL)
    return match.group(1) if match else ""


def _clean_xml(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _rel(path: Path, project_dir: Path) -> str:
    return str(path.relative_to(project_dir)).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
