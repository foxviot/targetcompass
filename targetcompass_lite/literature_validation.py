import csv
import json
import os
import re
import sqlite3
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .evidence_db import migrate_evidence_db
from .secrets import apply_project_secrets
from .v4 import content_hash, v4_dir


EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
LITERATURE_SCHEMA = "v4.literature_validation_run/0.1"


def run_literature_validation(
    project_dir: Path,
    query: str = "",
    limit: int = 100,
    batch_size: int = 10,
    use_llm: bool = True,
    timeout: int = 20,
) -> dict[str, Any]:
    apply_project_secrets(project_dir)
    limit = max(1, min(int(limit or 100), 200))
    batch_size = max(1, min(int(batch_size or 10), 25))
    spec = _read_json(project_dir / "research_spec.json", {})
    search_query = query.strip() or _build_pubmed_query(spec)
    out_dir = _out_dir(project_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    articles, query_attempts = fetch_pubmed_articles_with_retry(search_query, limit=limit, timeout=timeout)
    raw_path = out_dir / "pubmed_articles.json"
    raw_path.write_text(json.dumps({"query": search_query, "articles": articles}, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_articles_tsv(out_dir / "pubmed_articles.tsv", articles)
    llm_batches = []
    decisions: list[dict[str, Any]] = []
    if use_llm:
        for idx in range(0, len(articles), batch_size):
            batch = articles[idx : idx + batch_size]
            batch_result = _review_batch_with_llm(project_dir, spec, search_query, batch, idx // batch_size + 1)
            llm_batches.append(batch_result)
            decisions.extend(batch_result.get("decisions", []))
    else:
        decisions = [_rule_based_decision(spec, article) for article in articles]
    decision_path = out_dir / "literature_decisions.json"
    decision_path.write_text(json.dumps({"decisions": decisions, "llm_batches": llm_batches}, indent=2, ensure_ascii=False), encoding="utf-8")
    evidence_tsv = out_dir / "literature_evidence.tsv"
    evidence_rows = _decisions_to_evidence_rows(project_dir, spec, articles, decisions, evidence_tsv)
    _write_evidence_tsv(evidence_tsv, evidence_rows)
    inserted = import_literature_evidence(project_dir, evidence_tsv)
    payload = {
        "schema_version": LITERATURE_SCHEMA,
        "project_id": project_dir.name,
        "query": search_query,
        "effective_query": query_attempts[-1]["query"] if query_attempts else search_query,
        "query_attempts": query_attempts,
        "requested_limit": limit,
        "article_count": len(articles),
        "decision_count": len(decisions),
        "evidence_row_count": len(evidence_rows),
        "inserted_evidence_rows": inserted,
        "use_llm": use_llm,
        "provider": os.environ.get("TARGETCOMPASS_LLM_PROVIDER", "openai"),
        "model": os.environ.get("TARGETCOMPASS_OPENAI_MODEL", "deepseek-chat"),
        "artifacts": {
            "articles_json": _rel(raw_path, project_dir),
            "articles_tsv": _rel(out_dir / "pubmed_articles.tsv", project_dir),
            "decisions_json": _rel(decision_path, project_dir),
            "evidence_tsv": _rel(evidence_tsv, project_dir),
        },
        "generated_at": _now(),
    }
    payload["run_id"] = "litval_" + content_hash(payload)[:16]
    payload["run_hash"] = content_hash(payload)
    run_path = out_dir / "literature_validation_run.json"
    run_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    publish_paths = [raw_path, out_dir / "pubmed_articles.tsv", decision_path, evidence_tsv, run_path]
    publish_paths.extend(project_dir / batch.get("request", "") for batch in llm_batches if batch.get("request"))
    publish_paths.extend(project_dir / batch.get("response", "") for batch in llm_batches if batch.get("response"))
    try:
        from .output_backend import publish_output_artifacts

        publish_output_artifacts(
            project_dir,
            publish_paths,
            producer="literature_validation",
            artifact_type="literature_validation_output",
            task_id="literature_validation",
        )
    except Exception:
        pass
    return payload


def fetch_pubmed_articles(query: str, limit: int = 100, timeout: int = 20) -> list[dict[str, Any]]:
    ids = _pubmed_search(query, limit, timeout)
    if not ids:
        return []
    return _pubmed_fetch(ids, timeout)


def fetch_pubmed_articles_with_retry(query: str, limit: int = 100, timeout: int = 20) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    tried: set[str] = set()
    for attempt_index, candidate in enumerate(_literature_query_candidates(query), start=1):
        if candidate in tried:
            continue
        tried.add(candidate)
        try:
            ids = _pubmed_search(candidate, limit, timeout)
        except Exception as exc:
            attempts.append(
                {
                    "attempt_index": attempt_index,
                    "query": candidate,
                    "id_count": 0,
                    "strategy": "original" if attempt_index == 1 else "relaxed_retry",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "recovery": "Retry later, reduce request rate, use a narrower manual query, or upload PDF/TXT when literature APIs are rate-limited.",
                }
            )
            continue
        attempts.append(
            {
                "attempt_index": attempt_index,
                "query": candidate,
                "id_count": len(ids),
                "strategy": "original" if attempt_index == 1 else "relaxed_retry",
                "status": "completed",
            }
        )
        if ids:
            try:
                return _pubmed_fetch(ids, timeout), attempts
            except Exception as exc:
                attempts[-1].update(
                    {
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "recovery": "Search returned IDs but article fetch failed; retry later or upload PMID/PDF evidence manually.",
                    }
                )
    return [], attempts


def import_literature_evidence(project_dir: Path, evidence_tsv: Path) -> int:
    migrate_evidence_db(project_dir)
    con = sqlite3.connect(project_dir / "evidence.sqlite", timeout=30)
    inserted = 0
    try:
        with evidence_tsv.open(encoding="utf-8") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                con.execute(
                    """
                    INSERT OR REPLACE INTO evidence_item
                    (evidence_id, project_id, entity_symbol, entity_type, disease_context, organism, tissue, route,
                     evidence_type, direction, effect_size, p_value, quality_score, review_status, source_dataset,
                     artifact_path, run_id, artifact_id, module_version, limitation, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        row["evidence_type"],
                        row.get("direction", ""),
                        _float_or_none(row.get("effect_size", "")),
                        _float_or_none(row.get("p_value", "")),
                        _float_or_none(row.get("quality_score", "")),
                        row.get("review_status", "PENDING"),
                        row.get("source_dataset", ""),
                        row.get("artifact_path", ""),
                        row.get("run_id", ""),
                        row.get("artifact_id", ""),
                        row.get("module_version", ""),
                        row.get("limitation", ""),
                        row.get("created_at", _now()),
                    ),
                )
                inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


def _build_pubmed_query(spec: dict[str, Any]) -> str:
    disease = spec.get("disease_scope", {}).get("canonical", "") or spec.get("research_theme", "")
    tissues = spec.get("priority_tissues", [])[:3]
    cells = spec.get("priority_cells", [])[:3]
    route_terms = ["SASP", "senescence-associated secretory phenotype", "secreted", "cell surface", "biomarker"]
    parts = []
    if disease:
        parts.append(_pm_term(disease))
    biology = tissues + cells + route_terms
    parts.append("(" + " OR ".join(_pm_term(item) for item in biology if item) + ")")
    parts.append("(human OR mouse OR patient OR tissue OR cell)")
    return " AND ".join(parts)


def _literature_query_candidates(query: str) -> list[str]:
    original = (query or "").strip()
    if not original:
        return [original]
    normalized = re.sub(r"\s+", " ", original)
    candidates = [normalized]
    phrases = re.findall(r'"([^"]+)"', normalized)
    quoted = phrases if phrases else re.split(r"\s+", normalized)
    terms = [term.strip("()[]{}:;,.") for term in quoted if term.strip("()[]{}:;,.")]
    lower_terms = [term.lower() for term in terms]

    disease_terms = [
        term
        for term in terms
        if term.lower()
        in {
            "sarcopenia",
            "diabetes",
            "type 2 diabetes",
            "t2d",
            "aging",
            "cancer",
            "fibrosis",
            "obesity",
        }
    ]
    tissue_terms = [term for term in terms if term.lower() in {"muscle", "skeletal muscle", "myocyte", "myofiber"}]
    biology_terms = [
        term
        for term in terms
        if term.lower()
        in {
            "sasp",
            "senescence",
            "senescence-associated secretory phenotype",
            "surface",
            "cell surface",
            "secreted",
            "marker",
            "biomarker",
        }
    ]
    if disease_terms and tissue_terms:
        candidates.append(f"({' OR '.join(_pm_term(term) for term in disease_terms)}) AND ({' OR '.join(_pm_term(term) for term in tissue_terms)})")
    if disease_terms and biology_terms:
        candidates.append(f"({' OR '.join(_pm_term(term) for term in disease_terms)}) AND ({' OR '.join(_pm_term(term) for term in biology_terms)})")
    if "sarcopenia" in lower_terms:
        candidates.append('sarcopenia AND ("skeletal muscle" OR muscle) AND (senescence OR SASP OR inflammation OR biomarker)')
        candidates.append('sarcopenia AND ("skeletal muscle" OR muscle)')
    if "sasp" in lower_terms or "senescence" in lower_terms:
        candidates.append('("skeletal muscle" OR muscle) AND (senescence OR SASP OR "senescence-associated secretory phenotype")')
    candidates.append(" ".join(term for term in terms if term.lower() not in {"surface", "marker", "cell"}))
    return [candidate for candidate in dict.fromkeys(item.strip() for item in candidates) if candidate]


def _pubmed_search(query: str, limit: int, timeout: int) -> list[str]:
    params = urllib.parse.urlencode({"db": "pubmed", "term": query, "retmax": str(limit), "retmode": "json", "sort": "relevance"})
    data = _get_json(f"{EUTILS}/esearch.fcgi?{params}", timeout)
    return data.get("esearchresult", {}).get("idlist", [])[:limit]


def _pubmed_fetch(ids: list[str], timeout: int) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
    xml = _get_text(f"{EUTILS}/efetch.fcgi?{params}", timeout)
    articles = []
    for block in re.findall(r"<PubmedArticle\b.*?</PubmedArticle>", xml, flags=re.DOTALL):
        pmid = _tag(block, "PMID")
        title = _clean_xml(_tag(block, "ArticleTitle"))
        abstract = " ".join(_clean_xml(item) for item in re.findall(r"<AbstractText[^>]*>(.*?)</AbstractText>", block, flags=re.DOTALL))
        journal = _clean_xml(_tag(block, "Title"))
        year = _tag(block, "Year")
        mesh = [_clean_xml(item) for item in re.findall(r"<DescriptorName[^>]*>(.*?)</DescriptorName>", block, flags=re.DOTALL)]
        if pmid:
            articles.append(
                {
                    "pmid": pmid,
                    "title": title,
                    "abstract": abstract,
                    "journal": journal,
                    "year": year,
                    "mesh_terms": mesh[:30],
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                }
            )
    return articles


def _review_batch_with_llm(project_dir: Path, spec: dict[str, Any], query: str, articles: list[dict[str, Any]], batch_index: int) -> dict[str, Any]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; configure DeepSeek/OpenAI-compatible key first")
    provider = os.environ.get("TARGETCOMPASS_LLM_PROVIDER", "deepseek")
    base_url = os.environ.get("TARGETCOMPASS_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("TARGETCOMPASS_OPENAI_MODEL", "deepseek-chat")
    prompt = {
        "task": "Review PubMed articles for TargetCompass target discovery validation.",
        "research_spec": spec,
        "query": query,
        "instructions": [
            "Return strict JSON object only.",
            "For each article, decide relevance to disease/tissue/cell/SASP/secreted/surface target discovery.",
            "Extract candidate genes or molecules if explicitly mentioned in title/abstract.",
            "Do not invent genes. If none are explicit, use UNKNOWN and low confidence.",
        ],
        "output_schema": {
            "decisions": [
                {
                    "pmid": "string",
                    "relevance": "high|medium|low|exclude",
                    "candidate_symbols": ["string"],
                    "evidence_type": "literature_validation",
                    "confidence": "0-1 number",
                    "rationale": "short string",
                    "limitations": "short string",
                }
            ]
        },
        "articles": articles,
    }
    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a strict biomedical evidence reviewer. Output JSON only."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
    }
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(request_payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    out_dir = _out_dir(project_dir) / "llm_batches"
    out_dir.mkdir(parents=True, exist_ok=True)
    request_path = out_dir / f"batch_{batch_index:03d}_request.json"
    response_path = out_dir / f"batch_{batch_index:03d}_response.json"
    request_path.write_text(json.dumps(_redact_request(request_payload), indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            response_json = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{provider} literature review failed: {exc.code} {detail}") from exc
    response_path.write_text(json.dumps(response_json, indent=2, ensure_ascii=False), encoding="utf-8")
    content = response_json.get("choices", [{}])[0].get("message", {}).get("content", "{}")
    parsed = json.loads(_strip_code_fence(content))
    decisions = parsed.get("decisions", [])
    if not isinstance(decisions, list):
        raise ValueError("LLM literature review response must contain decisions list")
    return {
        "batch_index": batch_index,
        "provider": provider,
        "model": model,
        "article_count": len(articles),
        "decision_count": len(decisions),
        "request": _rel(request_path, project_dir),
        "response": _rel(response_path, project_dir),
        "decisions": decisions,
    }


def _rule_based_decision(spec: dict[str, Any], article: dict[str, Any]) -> dict[str, Any]:
    text = f"{article.get('title', '')} {article.get('abstract', '')}".lower()
    score = 0.2
    disease = spec.get("disease_scope", {}).get("canonical", "").lower()
    if disease and disease in text:
        score += 0.25
    if any(term in text for term in ["sasp", "senescence-associated secretory", "secreted", "cytokine", "chemokine"]):
        score += 0.25
    if any(term.lower() in text for term in spec.get("priority_tissues", [])):
        score += 0.15
    symbols = sorted(set(re.findall(r"\b[A-Z0-9]{2,10}\b", f"{article.get('title', '')} {article.get('abstract', '')}")))[:8] or ["UNKNOWN"]
    return {
        "pmid": article.get("pmid", ""),
        "relevance": "high" if score >= 0.7 else ("medium" if score >= 0.45 else "low"),
        "candidate_symbols": symbols,
        "evidence_type": "literature_validation",
        "confidence": round(min(score, 0.9), 2),
        "rationale": "Rule-based keyword relevance screen.",
        "limitations": "No LLM review; symbols are regex candidates and require review.",
    }


def _decisions_to_evidence_rows(project_dir: Path, spec: dict[str, Any], articles: list[dict[str, Any]], decisions: list[dict[str, Any]], evidence_tsv: Path) -> list[dict[str, Any]]:
    article_by_pmid = {row.get("pmid", ""): row for row in articles}
    disease = spec.get("disease_scope", {}).get("canonical", spec.get("research_theme", ""))
    rows = []
    created = _now()
    for decision in decisions:
        pmid = str(decision.get("pmid", ""))
        article = article_by_pmid.get(pmid, {})
        confidence = _float_or_none(decision.get("confidence", ""))
        for symbol in decision.get("candidate_symbols") or ["UNKNOWN"]:
            symbol = str(symbol or "UNKNOWN").strip().upper()
            if not symbol:
                symbol = "UNKNOWN"
            rows.append(
                {
                    "evidence_id": "lit_" + content_hash({"project": project_dir.name, "pmid": pmid, "symbol": symbol})[:18],
                    "project_id": project_dir.name,
                    "entity_symbol": symbol,
                    "entity_type": "gene" if symbol != "UNKNOWN" else "literature_topic",
                    "disease_context": disease,
                    "organism": "human_or_model",
                    "tissue": ", ".join(spec.get("priority_tissues", [])[:3]),
                    "route": "secreted_or_surface_or_sasp",
                    "evidence_type": "literature_validation",
                    "direction": decision.get("relevance", ""),
                    "effect_size": "",
                    "p_value": "",
                    "quality_score": confidence if confidence is not None else 0.4,
                    "review_status": "PENDING",
                    "source_dataset": f"PubMed:{pmid}",
                    "artifact_path": _rel(evidence_tsv, project_dir),
                    "run_id": "literature_validation",
                    "artifact_id": "artifact_" + content_hash({"pmid": pmid, "path": str(evidence_tsv)})[:16],
                    "module_version": "literature_validation_v1",
                    "limitation": f"{decision.get('rationale', '')} PMID={pmid}; {decision.get('limitations', '')}; title={article.get('title', '')[:160]}",
                    "created_at": created,
                }
            )
    return rows


def _write_articles_tsv(path: Path, articles: list[dict[str, Any]]) -> None:
    fields = ["pmid", "year", "journal", "title", "abstract", "mesh_terms", "url"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in articles:
            out = dict(row)
            out["mesh_terms"] = "; ".join(row.get("mesh_terms", []))
            writer.writerow({field: out.get(field, "") for field in fields})


def _write_evidence_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
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
        writer.writerows(rows)


def _get_json(url: str, timeout: int) -> dict[str, Any]:
    return json.loads(_get_text(url, timeout))


def _get_text(url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "TargetCompassLite/0.4 literature validation"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _tag(xml: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", xml, flags=re.DOTALL)
    return match.group(1) if match else ""


def _clean_xml(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def _pm_term(value: str) -> str:
    return '"' + value.replace('"', "") + '"'


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped


def _redact_request(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload))


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _out_dir(project_dir: Path) -> Path:
    return project_dir / "results" / "literature_validation"


def _rel(path: Path, project_dir: Path) -> str:
    return str(path.relative_to(project_dir)).replace("\\", "/")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
