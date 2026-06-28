import csv
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_db import migrate_evidence_db
from .evidence_levels import classify_evidence_level
from .secrets import apply_project_secrets
from .v4 import content_hash


FULLTEXT_LLM_SCHEMA = "v4.fulltext_llm_extraction/0.1"

GENE_ALIAS_MAP = {
    "P16": "CDKN2A",
    "P16INK4A": "CDKN2A",
    "P16 INK4A": "CDKN2A",
    "P21": "CDKN1A",
    "P21CIP1": "CDKN1A",
    "P21 CIP1/WAF1": "CDKN1A",
    "LAMIN B1": "LMNB1",
    "LAMINB1": "LMNB1",
}
NON_GENE_MARKERS = {"TAF", "TAFS", "SAHF", "SA-BETA-GAL", "SA Β GAL", "SA Β-GAL", "SA BETA GAL"}


def run_fulltext_llm_extraction(
    project_dir: Path,
    max_docs: int = 5,
    max_chars: int = 14000,
    chunk_chars: int = 4500,
    model: str = "",
) -> dict[str, Any]:
    apply_project_secrets(project_dir)
    out_dir = project_dir / "results" / "fulltext_literature" / "llm_extraction"
    out_dir.mkdir(parents=True, exist_ok=True)
    docs = _load_documents(project_dir)[: max(1, int(max_docs or 5))]
    extractions = []
    failures = []
    for index, doc in enumerate(docs, 1):
        chunks = _select_chunks(doc.get("text", ""), max_chars=max_chars, chunk_chars=chunk_chars)
        try:
            extraction = _extract_doc_with_llm(project_dir, doc, chunks, index, out_dir, model=model)
            extractions.append(extraction)
        except Exception as exc:
            failures.append({"document": doc.get("artifact_path", "") or doc.get("pmcid", "") or doc.get("title", ""), "reason": str(exc)})
    extraction_path = out_dir / "fulltext_llm_extractions.json"
    extraction_path.write_text(json.dumps({"extractions": extractions, "failures": failures}, indent=2, ensure_ascii=False), encoding="utf-8")
    evidence_rows = _extractions_to_evidence(project_dir, extractions)
    evidence_path = out_dir / "fulltext_llm_evidence.tsv"
    _write_evidence(evidence_path, evidence_rows)
    inserted = import_fulltext_llm_evidence(project_dir, evidence_path)
    manifest = {
        "schema_version": FULLTEXT_LLM_SCHEMA,
        "project_id": project_dir.name,
        "document_count": len(docs),
        "extracted_document_count": len(extractions),
        "failure_count": len(failures),
        "failures": failures,
        "evidence_row_count": len(evidence_rows),
        "inserted_evidence_rows": inserted,
        "model": model or os.environ.get("TARGETCOMPASS_OPENAI_MODEL", "deepseek-chat"),
        "artifacts": {
            "extractions_json": _rel(extraction_path, project_dir),
            "evidence_tsv": _rel(evidence_path, project_dir),
        },
        "generated_at": _now(),
    }
    manifest["run_id"] = "fulltext_llm_" + content_hash(manifest)[:16]
    run_path = out_dir / "fulltext_llm_extraction_run.json"
    run_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    publish_paths = [extraction_path, evidence_path, run_path]
    for extraction in extractions:
        artifacts = extraction.get("artifacts", {})
        publish_paths.extend(project_dir / artifacts[key] for key in ["request", "response"] if artifacts.get(key))
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            publish_paths,
            producer="fulltext_llm_extraction",
            artifact_type="fulltext_llm_extraction_output",
            task_id="fulltext_llm_extraction",
            qc_status="pass" if extractions else "review",
        )
    except Exception:
        pass
    return manifest


def import_fulltext_llm_evidence(project_dir: Path, evidence_tsv: Path) -> int:
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
                        row.get("evidence_type", "fulltext_extracted_result"),
                        row.get("direction", ""),
                        _float_or_none(row.get("effect_size")),
                        _float_or_none(row.get("p_value")),
                        _float_or_none(row.get("quality_score")) or 0.78,
                        level,
                        weight,
                        basis,
                        row.get("review_status", "PENDING"),
                        row.get("source_dataset", ""),
                        row.get("artifact_path", ""),
                        row.get("run_id", "fulltext_llm_extraction"),
                        row.get("artifact_id", ""),
                        row.get("module_version", "fulltext_llm_extraction_v1"),
                        row.get("limitation", ""),
                        row.get("created_at", _now()),
                    ),
                )
                inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


def _extract_doc_with_llm(project_dir: Path, doc: dict[str, Any], chunks: list[dict[str, str]], doc_index: int, out_dir: Path, model: str = "") -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    provider = os.environ.get("TARGETCOMPASS_LLM_PROVIDER", "deepseek")
    base_url = os.environ.get("TARGETCOMPASS_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = model or os.environ.get("TARGETCOMPASS_OPENAI_MODEL", "deepseek-chat")
    spec = _read_json(project_dir / "research_spec.json", {})
    prompt = {
        "task": "Extract structured biomedical evidence from full-text article segments.",
        "research_spec": spec,
        "document": {
            "title": doc.get("title", ""),
            "pmid": doc.get("pmid", ""),
            "pmcid": doc.get("pmcid", ""),
            "source_type": doc.get("source_type", ""),
            "artifact_path": doc.get("artifact_path", ""),
        },
        "instructions": [
            "Return strict JSON only.",
            "Use only the supplied article segments.",
            "Extract methods, sample information, cell types, and result sentences.",
            "For result sentences, include explicit molecules/genes only if present in the text.",
            "Do not infer causality. Do not invent sample size, cell type, or gene symbols.",
            "Keep quoted sentences short and preserve enough wording for traceability.",
        ],
        "output_schema": {
            "document_id": "string",
            "methods": [{"method": "string", "evidence_sentence": "string"}],
            "samples": [{"organism": "string", "tissue": "string", "cell_type": "string", "sample_size": "string", "condition": "string", "evidence_sentence": "string"}],
            "cell_types": [{"cell_type": "string", "marker_or_context": "string", "evidence_sentence": "string"}],
            "results": [{"gene_symbol": "string", "molecule": "string", "direction": "up|down|changed|associated|not_applicable", "cell_type": "string", "tissue": "string", "assay": "string", "evidence_sentence": "string", "confidence": 0.0}],
            "limitations": ["string"],
        },
        "segments": chunks,
    }
    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a strict biomedical full-text evidence extractor. Output JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }
    request_path = out_dir / f"doc_{doc_index:03d}_request.json"
    response_path = out_dir / f"doc_{doc_index:03d}_response.json"
    request_path.write_text(json.dumps(_redact_request(request_payload), indent=2, ensure_ascii=False), encoding="utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(request_payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            response_json = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{provider} fulltext extraction failed: {exc.code} {detail}") from exc
    response_path.write_text(json.dumps(response_json, indent=2, ensure_ascii=False), encoding="utf-8")
    raw_text = response_json.get("choices", [{}])[0].get("message", {}).get("content", "{}")
    parsed = _parse_json(raw_text)
    normalized = _normalize_extraction(doc, parsed)
    normalized["artifacts"] = {"request": _rel(request_path, project_dir), "response": _rel(response_path, project_dir)}
    return normalized


def _extractions_to_evidence(project_dir: Path, extractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    spec = _read_json(project_dir / "research_spec.json", {})
    disease = spec.get("disease_scope", {}).get("canonical", spec.get("research_theme", ""))
    rows = []
    created = _now()
    for extraction in extractions:
        doc = extraction.get("document", {})
        artifact_path = doc.get("artifact_path", "")
        source = doc.get("pmcid") or doc.get("pmid") or artifact_path
        for result in extraction.get("results", []):
            raw_gene = str(result.get("gene_symbol") or result.get("molecule") or "").strip()
            gene = _normalize_gene_symbol(raw_gene)
            if not gene or gene in {"UNKNOWN", "NA", "N/A"}:
                continue
            confidence = _float_or_none(result.get("confidence")) or 0.75
            evidence_sentence = _short(result.get("evidence_sentence", ""), 360)
            rows.append(
                {
                    "evidence_id": "ftllm_" + content_hash({"project": project_dir.name, "source": source, "gene": gene, "sentence": evidence_sentence})[:18],
                    "project_id": project_dir.name,
                    "entity_symbol": gene,
                    "entity_type": "gene",
                    "disease_context": disease,
                    "organism": _first_nonempty([sample.get("organism", "") for sample in extraction.get("samples", [])]),
                    "tissue": result.get("tissue", "") or _first_nonempty([sample.get("tissue", "") for sample in extraction.get("samples", [])]),
                    "route": _infer_route(result, evidence_sentence),
                    "evidence_type": "fulltext_extracted_result",
                    "direction": result.get("direction", "associated"),
                    "effect_size": "",
                    "p_value": "",
                    "quality_score": f"{min(0.9, max(0.55, confidence)):.2f}",
                    "evidence_level": "L5_experimental" if result.get("assay") else "L1_fulltext",
                    "evidence_weight": "1.0" if result.get("assay") else "0.55",
                    "evidence_basis": "LLM-extracted result sentence from full text with method/sample/cell-type context.",
                    "review_status": "PENDING",
                    "source_dataset": source,
                    "artifact_path": artifact_path,
                    "run_id": "fulltext_llm_extraction",
                    "artifact_id": "artifact_" + content_hash({"source": source, "artifact": artifact_path})[:16],
                    "module_version": "fulltext_llm_extraction_v1",
                    "limitation": _short(
                        f"raw_symbol={raw_gene}; assay={result.get('assay','')}; cell_type={result.get('cell_type','')}; sentence={evidence_sentence}",
                        600,
                    ),
                    "created_at": created,
                }
            )
    return rows


def _load_documents(project_dir: Path) -> list[dict[str, Any]]:
    path = project_dir / "results" / "fulltext_literature" / "fulltext_documents.json"
    if not path.exists():
        raise FileNotFoundError("fulltext_documents.json not found; run fulltext-literature first")
    return _read_json(path, {"documents": []}).get("documents", [])


def _select_chunks(text: str, max_chars: int, chunk_chars: int) -> list[dict[str, str]]:
    text = re.sub(r"\s+", " ", text or "").strip()[: max(1000, int(max_chars or 14000))]
    if not text:
        return []
    windows = []
    keywords = ["method", "sample", "participant", "skeletal muscle", "myocyte", "cell", "result", "increased", "decreased", "IL", "CXCL", "TNF", "SASP", "ELISA", "qPCR"]
    lower = text.lower()
    spans = []
    for key in keywords:
        start = lower.find(key.lower())
        if start >= 0:
            spans.append(max(0, start - chunk_chars // 3))
    spans = sorted(set(spans))[:4] or [0]
    for idx, start in enumerate(spans, 1):
        chunk = text[start : start + chunk_chars]
        if chunk:
            windows.append({"segment_id": f"seg_{idx}", "text": chunk})
    return windows


def _normalize_extraction(doc: dict[str, Any], parsed: dict[str, Any]) -> dict[str, Any]:
    return {
        "document": {
            "title": doc.get("title", ""),
            "pmid": doc.get("pmid", ""),
            "pmcid": doc.get("pmcid", ""),
            "source_type": doc.get("source_type", ""),
            "artifact_path": doc.get("artifact_path", ""),
            "ocr_artifact": doc.get("ocr_artifact", ""),
        },
        "methods": _as_list(parsed.get("methods")),
        "samples": _as_list(parsed.get("samples")),
        "cell_types": _as_list(parsed.get("cell_types")),
        "results": _as_list(parsed.get("results")),
        "limitations": _as_list(parsed.get("limitations")),
    }


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


def _parse_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("LLM fulltext extraction must return a JSON object")
    return parsed


def _infer_route(result: dict[str, Any], sentence: str) -> str:
    text = f"{result.get('molecule','')} {sentence}".lower()
    if any(term in text for term in ["secreted", "secretion", "cytokine", "chemokine", "plasma"]):
        return "secreted"
    if any(term in text for term in ["surface", "membrane", "receptor"]):
        return "surface"
    return "unknown"


def _normalize_gene_symbol(value: str) -> str:
    symbol = re.sub(r"\s+", " ", str(value or "").strip().upper())
    symbol = symbol.replace("-", " ")
    compact = symbol.replace(" ", "")
    if symbol in GENE_ALIAS_MAP:
        return GENE_ALIAS_MAP[symbol]
    if compact in GENE_ALIAS_MAP:
        return GENE_ALIAS_MAP[compact]
    if symbol in NON_GENE_MARKERS or compact in {item.replace(" ", "").replace("-", "") for item in NON_GENE_MARKERS}:
        return ""
    return symbol


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _redact_request(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_nonempty(values: list[Any]) -> str:
    for value in values:
        if value:
            return str(value)
    return ""


def _short(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _rel(path: Path, project_dir: Path) -> str:
    return str(path.relative_to(project_dir)).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
