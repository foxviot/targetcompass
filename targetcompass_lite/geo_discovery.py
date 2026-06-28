import csv
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .validators import load_dataset_card


EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


@dataclass
class GeoRecommendation:
    accession: str
    title: str
    summary: str
    organism: str
    sample_count: int | str
    platform: str
    score: int
    reasons: list[str]
    warnings: list[str]
    source: str = "ncbi_geo"
    import_status: str = "candidate"

    def to_dict(self) -> dict[str, Any]:
        return {
            "accession": self.accession,
            "title": self.title,
            "summary": self.summary,
            "organism": self.organism,
            "sample_count": self.sample_count,
            "platform": self.platform,
            "score": self.score,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "source": self.source,
            "import_status": self.import_status,
            "suggested_next_step": (
                "Review GEO metadata, define case/control patterns, then run geo-import."
                if self.import_status == "candidate"
                else "Already registered as a project dataset card."
            ),
        }


def recommendation_paths(project_dir: Path) -> tuple[Path, Path]:
    out_dir = project_dir / "results" / "geo_discovery"
    return out_dir / "geo_recommendations.json", out_dir / "geo_recommendations.tsv"


def load_recommendations(project_dir: Path) -> list[dict[str, Any]]:
    json_path, _ = recommendation_paths(project_dir)
    if not json_path.exists():
        return []
    data = json.loads(json_path.read_text(encoding="utf-8"))
    return data.get("recommendations", [])


def build_geo_query(spec: dict[str, Any], query_override: str = "") -> str:
    if query_override.strip():
        return query_override.strip()
    terms = []
    disease = spec.get("disease_scope", {}).get("canonical", "")
    theme = spec.get("research_theme", "")
    tissues = spec.get("priority_tissues", [])[:3]
    cells = spec.get("priority_cells", [])[:3]
    organisms = spec.get("organisms", [])
    if disease and disease != "unknown":
        terms.append(disease)
    for item in [*tissues, *cells]:
        if item and item not in terms:
            terms.append(item)
    if not terms and theme:
        terms.append(theme)
    organism_query = " OR ".join(_quote(item) for item in organisms[:2]) or "human"
    biology_query = " OR ".join(_quote(item) for item in terms[:6]) or _quote(theme or "aging")
    modality_query = '"expression profiling by array" OR "high throughput sequencing" OR "RNA-seq" OR transcriptome'
    return f"({biology_query}) AND ({organism_query}) AND ({modality_query})"


def discover_geo_datasets(
    project_dir: Path,
    limit: int = 8,
    query: str = "",
    online: bool = True,
    timeout: int = 10,
    write: bool = True,
) -> dict[str, Any]:
    spec = _read_json(project_dir / "research_spec.json", {})
    search_query = build_geo_query(spec, query)
    warnings = []
    query_attempts = []
    recommendations: list[GeoRecommendation]
    mode = "online"
    try:
        if not online:
            raise RuntimeError("online GEO discovery disabled")
        recommendations = _discover_online(search_query, spec, limit, timeout)
        query_attempts.append(_query_attempt(search_query, "online", len(recommendations), "initial_query"))
        if not recommendations:
            recovered = _retry_relaxed_geo_queries(search_query, spec, limit, timeout)
            query_attempts.extend(recovered["attempts"])
            recommendations = recovered["recommendations"]
            if recommendations:
                warnings.append("Initial NCBI GEO query returned no usable GSE recommendations; Dataset Scout recovered by relaxing overly specific terms.")
            else:
                warnings.append("NCBI GEO search returned no usable GSE recommendations after relaxed retry; using local registered GEO fallback.")
                recommendations = _discover_local(project_dir, spec, limit)
                mode = "local_fallback"
    except Exception as exc:
        warnings.append(f"NCBI GEO discovery unavailable: {exc}")
        query_attempts.append(_query_attempt(search_query, "failed", 0, str(exc)))
        recommendations = _discover_local(project_dir, spec, limit)
        mode = "local_fallback"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "query": search_query,
        "query_attempts": query_attempts,
        "query_recovery": _query_recovery_summary(query_attempts, mode),
        "recommendations": [item.to_dict() for item in recommendations[:limit]],
        "warnings": warnings,
    }
    if write:
        _write_outputs(project_dir, payload)
    return payload


def _retry_relaxed_geo_queries(query: str, spec: dict[str, Any], limit: int, timeout: int) -> dict[str, Any]:
    attempts = []
    for relaxed_query in build_relaxed_geo_queries(query, spec):
        try:
            recommendations = _discover_online(relaxed_query, spec, limit, timeout)
            attempts.append(_query_attempt(relaxed_query, "online", len(recommendations), "relaxed_query"))
            if recommendations:
                return {"recommendations": recommendations, "attempts": attempts}
        except Exception as exc:
            attempts.append(_query_attempt(relaxed_query, "failed", 0, str(exc)))
    return {"recommendations": [], "attempts": attempts}


def build_relaxed_geo_queries(query: str, spec: dict[str, Any]) -> list[str]:
    relaxed_terms = _relaxed_biology_terms(query, spec)
    organism_query = _organism_query(spec)
    modality_query = '"expression profiling by array" OR "high throughput sequencing" OR "RNA-seq" OR transcriptome'
    queries = []
    if relaxed_terms:
        queries.append(f"({_or_query(relaxed_terms[:6])}) AND ({organism_query}) AND ({modality_query})")
    broad_terms = _broad_context_terms(relaxed_terms, spec)
    if broad_terms:
        queries.append(f"({_or_query(broad_terms[:6])}) AND ({organism_query}) AND ({modality_query})")
    seen = set()
    return [item for item in queries if item not in seen and not seen.add(item)]


def _relaxed_biology_terms(query: str, spec: dict[str, Any]) -> list[str]:
    stop_terms = {
        "sasp",
        "surface",
        "surfaceome",
        "secreted",
        "secretome",
        "marker",
        "markers",
        "molecule",
        "molecules",
        "target",
        "targets",
        "cell",
        "cells",
        "accessible",
        "accessibility",
        "factor",
        "factors",
        "cytokine",
        "cytokines",
    }
    candidates = []
    candidates.extend(_extract_plain_terms(query))
    candidates.extend([spec.get("disease_scope", {}).get("canonical", ""), spec.get("research_theme", "")])
    candidates.extend(spec.get("priority_tissues", [])[:3])
    candidates.extend(spec.get("priority_cells", [])[:3])
    out = []
    for term in candidates:
        cleaned = _clean_relaxed_term(term, stop_terms)
        if cleaned and cleaned.lower() not in {item.lower() for item in out}:
            out.append(cleaned)
    return out


def _broad_context_terms(relaxed_terms: list[str], spec: dict[str, Any]) -> list[str]:
    broad = list(relaxed_terms)
    broad.extend(["senescence", "aging", "inflammation"])
    disease = spec.get("disease_scope", {}).get("canonical", "")
    if disease and disease != "unknown":
        broad.insert(0, disease)
    out = []
    for term in broad:
        if term and term.lower() not in {item.lower() for item in out}:
            out.append(term)
    return out


def _extract_plain_terms(query: str) -> list[str]:
    text = re.sub(r"\bAND\b|\bOR\b|\bNOT\b", " ", query, flags=re.IGNORECASE)
    text = re.sub(r"RNA-seq|transcriptome|expression profiling|high throughput sequencing|Homo sapiens|human", " ", text, flags=re.IGNORECASE)
    parts = re.findall(r'"([^"]+)"|([A-Za-z][A-Za-z0-9 -]{2,})', text)
    return [" ".join(item for item in group if item).strip(" ()") for group in parts]


def _clean_relaxed_term(term: str, stop_terms: set[str]) -> str:
    words = [word for word in re.sub(r"[^A-Za-z0-9 ]", " ", str(term)).split() if word.lower() not in stop_terms]
    return " ".join(words[:6]).strip()


def _organism_query(spec: dict[str, Any]) -> str:
    organisms = spec.get("organisms", [])
    return " OR ".join(_quote(item) for item in organisms[:2]) or "human"


def _or_query(terms: list[str]) -> str:
    return " OR ".join(_quote(item) for item in terms if item)


def _query_attempt(query: str, status: str, recommendation_count: int, reason: str) -> dict[str, Any]:
    return {
        "query": query,
        "status": status,
        "recommendation_count": recommendation_count,
        "reason": reason,
    }


def _query_recovery_summary(query_attempts: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    recovered = len(query_attempts) > 1 and any(item.get("recommendation_count", 0) for item in query_attempts[1:])
    return {
        "attempt_count": len(query_attempts),
        "recovered": recovered,
        "final_mode": mode,
        "final_query": next((item.get("query", "") for item in reversed(query_attempts) if item.get("recommendation_count", 0)), query_attempts[-1].get("query", "") if query_attempts else ""),
        "suggested_action": "" if recovered or mode != "local_fallback" else "Broaden disease/tissue terms or manually provide GEO accession.",
    }


def _discover_online(query: str, spec: dict[str, Any], limit: int, timeout: int) -> list[GeoRecommendation]:
    ids = _esearch(query, min(max(limit * 3, 10), 30), timeout)
    summaries = _esummary(ids, timeout)
    items = []
    seen = set()
    for doc in summaries:
        text = " ".join(str(doc.get(key, "")) for key in ["title", "summary", "gds", "entrytype"])
        accession = _extract_gse(text) or str(doc.get("accession") or doc.get("gse") or doc.get("uid") or "")
        if not accession or accession in seen:
            continue
        seen.add(accession)
        item = _score_record(
            accession=accession,
            title=str(doc.get("title", "")),
            summary=str(doc.get("summary", "")),
            organism=str(doc.get("taxon", "") or doc.get("organism", "")),
            sample_count=_sample_count(doc),
            platform=str(doc.get("gpl", "") or doc.get("platform", "")),
            spec=spec,
            source="ncbi_geo",
            import_status="candidate",
        )
        items.append(item)
    return sorted(items, key=lambda item: item.score, reverse=True)[:limit]


def _discover_local(project_dir: Path, spec: dict[str, Any], limit: int) -> list[GeoRecommendation]:
    items = []
    for path in sorted((project_dir / "dataset_cards").glob("*.yaml")):
        card = load_dataset_card(path)
        accession = str(card.get("accession") or card.get("dataset_id") or path.stem)
        if not accession.upper().startswith("GSE") and str(card.get("source", "")).lower() != "geo":
            continue
        title = f"{card.get('dataset_id', path.stem)} {card.get('tissue', '')} {card.get('modality', '')}".strip()
        summary = "; ".join(str(item) for item in card.get("known_limitations", []))
        item = _score_record(
            accession=accession,
            title=title,
            summary=summary,
            organism=str(card.get("organism", "")),
            sample_count=(card.get("sample_summary", {}) or {}).get("donor_n", ""),
            platform=str(card.get("modality", "")),
            spec=spec,
            source="registered_dataset_card",
            import_status="registered",
        )
        items.append(item)
    return sorted(items, key=lambda item: item.score, reverse=True)[:limit]


def _score_record(
    accession: str,
    title: str,
    summary: str,
    organism: str,
    sample_count: int | str,
    platform: str,
    spec: dict[str, Any],
    source: str,
    import_status: str,
) -> GeoRecommendation:
    text = f"{title} {summary} {organism} {platform}".lower()
    reasons = []
    warnings = []
    score = 20
    disease = spec.get("disease_scope", {}).get("canonical", "")
    if disease and disease != "unknown" and disease.lower() in text:
        score += 25
        reasons.append(f"disease match: {disease}")
    for tissue in spec.get("priority_tissues", []):
        if tissue.lower() in text:
            score += 12
            reasons.append(f"tissue match: {tissue}")
            break
    for cell in spec.get("priority_cells", []):
        if cell.lower() in text:
            score += 10
            reasons.append(f"cell match: {cell}")
            break
    organism_text = organism.lower()
    expected_orgs = [org.lower() for org in spec.get("organisms", [])]
    if _organism_matches(organism_text, expected_orgs):
        score += 10
        reasons.append("organism match")
    elif expected_orgs:
        score -= 15
        warnings.append(f"organism mismatch or unclear: {organism}")
    if any(marker in text for marker in ["expression", "transcript", "rna-seq", "microarray", "array"]):
        score += 15
        reasons.append("bulk expression compatible")
    if any(marker in text for marker in ["senescence", "senescent", "aging", "ageing", "aged"]):
        score += 12
        reasons.append("aging/senescence context")
    if any(marker in text for marker in ["single-cell", "single cell", "scrna", "atac", "multi-modal", "multimodal"]):
        score -= 8
        warnings.append("non-bulk or multi-omic signal detected; route to reserved scRNA/accessibility interface before DEG")
    n = _coerce_int(sample_count)
    if n >= 6:
        score += 8
        reasons.append(f"sample count appears usable: {n}")
    elif n:
        warnings.append(f"small sample count: {n}")
    if not accession.upper().startswith("GSE"):
        warnings.append("accession was not parsed as a GSE id; manual review required")
    if not reasons:
        warnings.append("low semantic match; review manually")
    return GeoRecommendation(
        accession=accession,
        title=title[:300],
        summary=summary[:800],
        organism=organism,
        sample_count=sample_count,
        platform=platform,
        score=min(score, 100),
        reasons=reasons,
        warnings=warnings,
        source=source,
        import_status=import_status,
    )


def _organism_matches(organism_text: str, expected_orgs: list[str]) -> bool:
    if not expected_orgs:
        return True
    aliases = {
        "human": ["human", "homo sapiens"],
        "mouse": ["mouse", "mus musculus", "mice", "murine"],
    }
    for expected in expected_orgs:
        for alias in aliases.get(expected, [expected]):
            if alias in organism_text:
                return True
    return False


def _esearch(query: str, limit: int, timeout: int) -> list[str]:
    params = urllib.parse.urlencode({"db": "gds", "term": query, "retmax": str(limit), "retmode": "json"})
    data = _get_json(f"{EUTILS}/esearch.fcgi?{params}", timeout)
    return data.get("esearchresult", {}).get("idlist", [])


def _esummary(ids: list[str], timeout: int) -> list[dict[str, Any]]:
    if not ids:
        return []
    params = urllib.parse.urlencode({"db": "gds", "id": ",".join(ids), "retmode": "json"})
    data = _get_json(f"{EUTILS}/esummary.fcgi?{params}", timeout)
    result = data.get("result", {})
    return [result[uid] for uid in result.get("uids", []) if isinstance(result.get(uid), dict)]


def _get_json(url: str, timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "TargetCompassLite/0.4 GEO discovery"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _extract_gse(text: str) -> str:
    match = re.search(r"\bGSE\d+\b", text, flags=re.IGNORECASE)
    return match.group(0).upper() if match else ""


def _sample_count(doc: dict[str, Any]) -> int | str:
    for key in ["n_samples", "nsamples", "samples", "sample_count"]:
        value = doc.get(key)
        if value not in {None, ""}:
            return value
    summary = str(doc.get("summary", ""))
    match = re.search(r"(\d+)\s+samples?", summary, flags=re.IGNORECASE)
    return int(match.group(1)) if match else ""


def _coerce_int(value: int | str) -> int:
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else 0


def _quote(value: str) -> str:
    value = value.strip()
    return f'"{value}"' if " " in value else value


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _write_outputs(project_dir: Path, payload: dict[str, Any]) -> None:
    json_path, tsv_path = recommendation_paths(project_dir)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    fields = [
        "accession",
        "score",
        "title",
        "organism",
        "sample_count",
        "platform",
        "source",
        "import_status",
        "reasons",
        "warnings",
        "suggested_next_step",
    ]
    with tsv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in payload["recommendations"]:
            flat = dict(row)
            flat["reasons"] = "; ".join(row.get("reasons", []))
            flat["warnings"] = "; ".join(row.get("warnings", []))
            writer.writerow({field: flat.get(field, "") for field in fields})
