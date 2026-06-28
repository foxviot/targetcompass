from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .ids import make_stable_id
from .schemas import DatasetProfile, DatasetSelectionDecision, ResourceCandidate, now_iso
from .validation import validate_no_unknown_verified_dataset


FetchJson = Callable[[str, int], dict[str, Any]]

NCBI_EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
EUROPE_PMC_SEARCH = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
BIOSTUDIES_SEARCH = "https://www.ebi.ac.uk/biostudies/api/v1/search"
CELLXGENE_COLLECTIONS = "https://api.cellxgene.cziscience.com/curation/v1/collections"

SUPPORTED_SOURCES = ("geo", "sra", "pubmed", "europe_pmc", "arrayexpress", "cellxgene")


def discover_real_resources(
    project_dir: str | Path,
    evidence_plan: dict[str, Any],
    scope_bundle: dict[str, Any],
    *,
    sources: list[str] | tuple[str, ...] = ("geo", "sra", "pubmed", "europe_pmc"),
    limit: int = 5,
    timeout: int = 15,
    fetch_json: FetchJson | None = None,
    write: bool = True,
) -> dict[str, Any]:
    project_dir = Path(project_dir)
    fetch = fetch_json or _fetch_json
    query = build_resource_query(evidence_plan, scope_bundle)
    resource_candidates: list[dict[str, Any]] = []
    dataset_profiles: list[dict[str, Any]] = []
    dataset_selection_decisions: list[dict[str, Any]] = []
    query_attempts = []

    for source in sources:
        if source not in SUPPORTED_SOURCES:
            query_attempts.append({"source": source, "status": "unsupported", "query": query, "result_count": 0})
            continue
        try:
            result = _discover_source(source, query, limit, timeout, fetch)
            query_attempts.append({"source": source, "status": "success", "query": query, "result_count": len(result["resource_candidates"])})
            if not result["resource_candidates"]:
                for relaxed_query in relaxed_resource_queries(query):
                    result = _discover_source(source, relaxed_query, limit, timeout, fetch)
                    query_attempts.append({"source": source, "status": "relaxed_success", "query": relaxed_query, "result_count": len(result["resource_candidates"])})
                    if result["resource_candidates"]:
                        break
            resource_candidates.extend(result["resource_candidates"])
            dataset_profiles.extend(result["dataset_profiles"])
            dataset_selection_decisions.extend(result["dataset_selection_decisions"])
        except Exception as exc:
            query_attempts.append({"source": source, "status": "failed", "query": query, "result_count": 0, "reason": str(exc)})

    _add_scope_suggestions_to_profiles(dataset_profiles, scope_bundle)
    resource_candidates = _screen_and_prioritize_literature_candidates(
        resource_candidates,
        allow_review_literature=_allow_review_literature(evidence_plan, scope_bundle),
    )
    validation_candidates = [item for item in resource_candidates if item.get("literature_screening_decision") == "eligible_for_validation_extraction"]
    filtered_literature = [item for item in resource_candidates if item.get("literature_screening_decision") == "not_default_validation_target"]

    bundle = {
        "schema_version": "v5.resource_discovery/0.1",
        "project_id": project_dir.name,
        "created_at": now_iso(),
        "agent_id": "resource_discovery_agent",
        "query": query,
        "sources": list(sources),
        "query_attempts": query_attempts,
        "resource_candidates": resource_candidates,
        "dataset_profiles": dataset_profiles,
        "dataset_selection_decisions": dataset_selection_decisions,
        "verified_candidate_count": len([item for item in resource_candidates if item.get("verified") is True]),
        "locked_dataset_count": len([item for item in dataset_selection_decisions if item.get("decision") == "locked"]),
        "validation_extraction_candidate_count": len(validation_candidates),
        "filtered_literature_count": len(filtered_literature),
        "literature_screening_policy": {
            "default": "Literature is validation-only by default. Mechanism/experimental papers are prioritized for paper-based validation; review/method/guideline/diagnostic literature is retained for audit but not used to drive the core target-discovery workflow.",
            "allow_review_literature": _allow_review_literature(evidence_plan, scope_bundle),
        },
    }
    errors = validate_resource_discovery_bundle(bundle)
    if errors:
        raise ValueError("; ".join(errors))
    if write:
        _write_resource_discovery_bundle(project_dir, bundle)
        from .resource_gate import build_resource_gate_report

        gate = build_resource_gate_report(project_dir, bundle, write=True)
        bundle["resource_gate_ref"] = "v5/resource_discovery/resource_gate_report.json"
        bundle["manual_review_count"] = gate.get("manual_review_count", 0)
    return bundle


def build_resource_query(evidence_plan: dict[str, Any], scope_bundle: dict[str, Any]) -> str:
    terms: list[str] = []
    terms.extend(_as_list(scope_bundle.get("conditions")))
    terms.extend(_as_list(scope_bundle.get("tissues")))
    terms.extend(_as_list(scope_bundle.get("cell_types")))
    terms.extend(_query_terms_from_evidence_axes(evidence_plan.get("evidence_axes")))
    terms = [item for item in terms if item and "unspecified" not in item.lower()]
    if not terms:
        terms = ["transcriptome"]
    species = _as_list(scope_bundle.get("species")) or ["human"]
    return " ".join(_dedupe(terms[:6] + species[:2]))


def validate_resource_discovery_bundle(bundle: dict[str, Any]) -> list[str]:
    errors = []
    candidate_ids = {item.get("resource_candidate_id") for item in bundle.get("resource_candidates", [])}
    for candidate in bundle.get("resource_candidates", []):
        errors.extend(validate_no_unknown_verified_dataset(candidate))
        if candidate.get("verified") is True and candidate.get("source_status") != "metadata_verified":
            errors.append(f"{candidate.get('resource_candidate_id')}: verified=true requires source_status=metadata_verified")
        if candidate.get("verified") is True and not candidate.get("title"):
            errors.append(f"{candidate.get('resource_candidate_id')}: verified=true requires title metadata")
    for profile in bundle.get("dataset_profiles", []):
        if profile.get("resource_candidate_id") not in candidate_ids:
            errors.append(f"{profile.get('dataset_profile_id')}: profile references unknown resource_candidate_id")
    for decision in bundle.get("dataset_selection_decisions", []):
        if decision.get("resource_candidate_id") not in candidate_ids:
            errors.append(f"{decision.get('decision_id')}: decision references unknown resource_candidate_id")
        if decision.get("decision") == "locked" and decision.get("verified") is not True:
            errors.append(f"{decision.get('decision_id')}: dataset cannot be locked when verified is not true")
    return errors


def relaxed_resource_queries(query: str) -> list[str]:
    tokens = [item for item in query.split() if item]
    noisy = {"sasp", "senescence", "single", "cell", "type", "transcriptome", "expression", "surface", "secreted"}
    broad = [item for item in tokens if item.lower() not in noisy]
    queries = []
    if broad and " ".join(broad) != query:
        queries.append(" ".join(broad))
    if len(broad) > 3:
        queries.append(" ".join(broad[:3]))
    seen = set()
    return [item for item in queries if item and item not in seen and not seen.add(item)]


def _discover_source(source: str, query: str, limit: int, timeout: int, fetch: FetchJson) -> dict[str, list[dict[str, Any]]]:
    if source == "geo":
        return _discover_ncbi_dataset_source(source, "gds", query, limit, timeout, fetch)
    if source == "sra":
        return _discover_ncbi_dataset_source(source, "sra", query, limit, timeout, fetch)
    if source == "pubmed":
        return _discover_pubmed(query, limit, timeout, fetch)
    if source == "europe_pmc":
        return _discover_europe_pmc(query, limit, timeout, fetch)
    if source == "arrayexpress":
        return _discover_biostudies_arrayexpress(query, limit, timeout, fetch)
    if source == "cellxgene":
        return _discover_cellxgene(query, limit, timeout, fetch)
    return {"resource_candidates": [], "dataset_profiles": [], "dataset_selection_decisions": []}


def _discover_ncbi_dataset_source(source: str, db: str, query: str, limit: int, timeout: int, fetch: FetchJson) -> dict[str, list[dict[str, Any]]]:
    ids = _ncbi_esearch(db, query, limit, timeout, fetch)
    if not ids:
        return {"resource_candidates": [], "dataset_profiles": [], "dataset_selection_decisions": []}
    summary_url = f"{NCBI_EUTILS}/esummary.fcgi?{urllib.parse.urlencode({'db': db, 'id': ','.join(ids), 'retmode': 'json'})}"
    payload = fetch(summary_url, timeout)
    result = payload.get("result", {})
    candidates = []
    profiles = []
    decisions = []
    for uid in result.get("uids", ids):
        item = result.get(str(uid), {})
        accession = _dataset_accession_from_summary(source, item, uid)
        title = _dataset_title_from_summary(source, item)
        summary = _first_text(item, ["summary", "title", "studytitle"])
        organism = _first_text(item, ["taxon", "organism", "organism_name"])
        platform = _first_text(item, ["gpl", "platform", "instrument"])
        candidate = _resource_candidate(
            source=source,
            accession=accession,
            title=title,
            summary=summary,
            resource_type="dataset",
            organism=organism,
            platform=platform,
            raw=item,
        )
        candidates.append(candidate)
        profiles.append(_dataset_profile(candidate, modality=_infer_modality(title, summary, platform), organism=organism, platform=platform))
        decisions.append(_dataset_decision(candidate, decision="candidate_review_required", reason="Official metadata found; grouping/sample usability still requires review."))
    return {"resource_candidates": candidates, "dataset_profiles": profiles, "dataset_selection_decisions": decisions}


def _discover_pubmed(query: str, limit: int, timeout: int, fetch: FetchJson) -> dict[str, list[dict[str, Any]]]:
    ids = _ncbi_esearch("pubmed", query, limit, timeout, fetch)
    if not ids:
        return {"resource_candidates": [], "dataset_profiles": [], "dataset_selection_decisions": []}
    url = f"{NCBI_EUTILS}/esummary.fcgi?{urllib.parse.urlencode({'db': 'pubmed', 'id': ','.join(ids), 'retmode': 'json'})}"
    payload = fetch(url, timeout)
    result = payload.get("result", {})
    candidates = []
    for uid in result.get("uids", ids):
        item = result.get(str(uid), {})
        candidates.append(
            _resource_candidate(
                source="pubmed",
                accession=str(item.get("uid") or uid),
                title=_first_text(item, ["title"]),
                summary=_first_text(item, ["source", "fulljournalname"]),
                resource_type="literature",
                organism="",
                platform="",
                raw=item,
            )
        )
    return {"resource_candidates": candidates, "dataset_profiles": [], "dataset_selection_decisions": []}


def _discover_europe_pmc(query: str, limit: int, timeout: int, fetch: FetchJson) -> dict[str, list[dict[str, Any]]]:
    url = f"{EUROPE_PMC_SEARCH}?{urllib.parse.urlencode({'query': query, 'format': 'json', 'pageSize': str(limit)})}"
    payload = fetch(url, timeout)
    results = payload.get("resultList", {}).get("result", [])
    candidates = [
        _resource_candidate(
            source="europe_pmc",
            accession=str(item.get("id") or item.get("pmid") or item.get("pmcid") or ""),
            title=str(item.get("title") or ""),
            summary=str(item.get("abstractText") or item.get("journalTitle") or ""),
            resource_type="literature",
            organism="",
            platform="",
            raw=item,
        )
        for item in results[:limit]
    ]
    return {"resource_candidates": [item for item in candidates if item.get("accession")], "dataset_profiles": [], "dataset_selection_decisions": []}


def _discover_biostudies_arrayexpress(query: str, limit: int, timeout: int, fetch: FetchJson) -> dict[str, list[dict[str, Any]]]:
    url = f"{BIOSTUDIES_SEARCH}?{urllib.parse.urlencode({'query': query, 'pageSize': str(limit)})}"
    payload = fetch(url, timeout)
    hits = payload.get("hits") or payload.get("entries") or []
    candidates = []
    profiles = []
    decisions = []
    for item in hits[:limit]:
        accession = str(item.get("accession") or item.get("id") or "")
        candidate = _resource_candidate(
            source="arrayexpress",
            accession=accession,
            title=str(item.get("title") or item.get("name") or ""),
            summary=str(item.get("description") or item.get("title") or ""),
            resource_type="dataset",
            organism=str(item.get("organism") or ""),
            platform=str(item.get("technology") or ""),
            raw=item,
        )
        if candidate.get("accession"):
            candidates.append(candidate)
            profiles.append(_dataset_profile(candidate, modality="expression_or_multiomics", organism=candidate.get("organism", ""), platform=candidate.get("platform", "")))
            decisions.append(_dataset_decision(candidate, decision="candidate_review_required", reason="BioStudies/ArrayExpress metadata found; usability requires review."))
    return {"resource_candidates": candidates, "dataset_profiles": profiles, "dataset_selection_decisions": decisions}


def _discover_cellxgene(query: str, limit: int, timeout: int, fetch: FetchJson) -> dict[str, list[dict[str, Any]]]:
    payload = fetch(CELLXGENE_COLLECTIONS, timeout)
    collections = payload if isinstance(payload, list) else payload.get("collections") or []
    query_terms = {term.lower() for term in query.split() if len(term) > 2}
    candidates = []
    profiles = []
    decisions = []
    for item in collections:
        title = str(item.get("name") or item.get("title") or "")
        description = str(item.get("description") or "")
        haystack = f"{title} {description}".lower()
        if query_terms and not any(term in haystack for term in query_terms):
            continue
        accession = str(item.get("collection_id") or item.get("id") or "")
        candidate = _resource_candidate(
            source="cellxgene",
            accession=accession,
            title=title,
            summary=description,
            resource_type="dataset",
            organism=str(item.get("organism") or ""),
            platform="single_cell",
            raw=item,
        )
        if candidate.get("accession"):
            candidates.append(candidate)
            profiles.append(_dataset_profile(candidate, modality="single_cell_expression", organism=candidate.get("organism", ""), platform="cellxgene"))
            decisions.append(_dataset_decision(candidate, decision="candidate_review_required", reason="cellxgene collection metadata found; disease/tissue/grouping usability requires review."))
        if len(candidates) >= limit:
            break
    return {"resource_candidates": candidates, "dataset_profiles": profiles, "dataset_selection_decisions": decisions}


def _ncbi_esearch(db: str, query: str, limit: int, timeout: int, fetch: FetchJson) -> list[str]:
    url = f"{NCBI_EUTILS}/esearch.fcgi?{urllib.parse.urlencode({'db': db, 'term': query, 'retmax': str(limit), 'retmode': 'json'})}"
    payload = fetch(url, timeout)
    return [str(item) for item in payload.get("esearchresult", {}).get("idlist", [])]


def _resource_candidate(
    *,
    source: str,
    accession: str,
    title: str,
    summary: str,
    resource_type: str,
    organism: str,
    platform: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    has_real_metadata = bool(accession and title and not accession.upper().startswith(("AUTO_", "MOCK_")))
    candidate = ResourceCandidate(
        resource_name=title or accession,
        resource_type=resource_type,
        verified=has_real_metadata,
        source_status="metadata_verified" if has_real_metadata else "metadata_incomplete",
        accession=accession,
        status="candidate",
    )
    data = asdict(candidate)
    data["resource_candidate_id"] = make_stable_id("resource_candidate", {"source": source, "accession": accession, "title": title})
    data["source_database"] = source
    data["title"] = title
    data["summary"] = summary
    data["organism"] = organism
    data["platform"] = platform
    data["raw_metadata"] = raw
    data["metadata_fields_present"] = sorted([key for key, value in {"accession": accession, "title": title, "summary": summary, "organism": organism, "platform": platform}.items() if value])
    if resource_type == "literature":
        data.update(_classify_literature_candidate(title, summary, raw))
    return data


def _add_scope_suggestions_to_profiles(dataset_profiles: list[dict[str, Any]], scope_bundle: dict[str, Any]) -> None:
    tissue_suggestion = _first_scope_value(scope_bundle, ["tissues", "cell_types"])
    organism_suggestion = _first_scope_value(scope_bundle, ["species"])
    for profile in dataset_profiles:
        suggestions: dict[str, str] = {}
        if tissue_suggestion and profile.get("tissue") in {"", "unknown", None}:
            suggestions["tissue"] = tissue_suggestion
        if organism_suggestion and profile.get("organism") in {"", "unknown", None}:
            suggestions["organism"] = organism_suggestion
        if suggestions:
            profile["scope_suggestions"] = {
                **suggestions,
                "method": "scope_bundle_suggestion",
                "limitation": "from research scope, not verified dataset metadata; human review required",
            }


def _first_scope_value(scope_bundle: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        for value in _as_list(scope_bundle.get(key)):
            cleaned = str(value).strip()
            if cleaned and cleaned.lower() not in {"unknown", "unspecified", "any"}:
                return cleaned
    return ""


def _classify_literature_candidate(title: str, summary: str, raw: dict[str, Any]) -> dict[str, Any]:
    text = f"{title} {summary} {_raw_publication_type_text(raw)}".lower()
    reasons: list[str] = []
    review_terms = ["review", "systematic review", "meta-analysis", "scoping review", "narrative review", "overview"]
    method_terms = ["method", "methods", "protocol", "workflow", "pipeline", "benchmark", "database", "software", "feasibility", "guideline", "criteria", "consensus"]
    mechanism_terms = [
        "mechanism",
        "regulates",
        "regulated",
        "knockout",
        "knockdown",
        "overexpression",
        "single-cell",
        "single cell",
        "snrna",
        "scrna",
        "rna-seq",
        "transcriptome",
        "proteome",
        "immunohistochemistry",
        "biopsy",
        "mouse",
        "mice",
        "cellular senescence",
        "sasp",
        "expression",
    ]
    if any(term in text for term in review_terms):
        reasons.append("review/meta-analysis terminology detected")
        paper_type = "review"
        confidence = 0.86
    elif any(term in text for term in method_terms):
        reasons.append("method/protocol/feasibility terminology detected")
        paper_type = "method"
        confidence = 0.78
    elif any(term in text for term in mechanism_terms):
        reasons.append("experimental/mechanistic assay terminology detected")
        paper_type = "mechanism_experiment"
        confidence = 0.74
    else:
        reasons.append("insufficient metadata for paper type classification")
        paper_type = "unknown"
        confidence = 0.35
    priority = {
        "mechanism_experiment": "high",
        "method": "review",
        "review": "low",
        "unknown": "review",
    }[paper_type]
    return {
        "paper_type": paper_type,
        "paper_type_confidence": confidence,
        "paper_type_reason": "; ".join(reasons),
        "fulltext_extraction_priority": priority,
    }


def _screen_and_prioritize_literature_candidates(candidates: list[dict[str, Any]], *, allow_review_literature: bool) -> list[dict[str, Any]]:
    screened = []
    for candidate in candidates:
        if candidate.get("resource_type") != "literature":
            candidate["resource_screening_rank"] = 0
            screened.append(candidate)
            continue
        paper_type = candidate.get("paper_type", "unknown")
        if paper_type == "mechanism_experiment":
            decision = "eligible_for_validation_extraction"
            suitability = "high"
            reason = "mechanism/experimental paper type is suitable for validation-set full-text extraction"
            action = "use for validation runs only; do not treat literature as a required core workflow resource"
        elif allow_review_literature:
            decision = "eligible_for_review_evidence"
            suitability = "review_only"
            reason = "review literature was explicitly requested; use as low-weight validation/background evidence, not direct molecule-level proof"
            action = "run literature validation; keep claims at background/association level"
        else:
            decision = "not_default_validation_target"
            suitability = "low" if paper_type == "review" else "review"
            reason = f"{paper_type} paper is not a default paper-validation target"
            action = "skip in default paper validation or send to human review if the user explicitly wants review/background evidence"
        candidate["literature_screening_decision"] = decision
        candidate["validation_extraction_suitability"] = suitability
        candidate["validation_extraction_reason"] = reason
        candidate["core_workflow_role"] = "validation_only_not_required_for_target_discovery"
        candidate["recommended_next_step"] = action
        candidate["resource_screening_rank"] = _literature_rank(candidate)
        screened.append(candidate)
    return sorted(screened, key=lambda item: (item.get("resource_type") == "literature", item.get("resource_screening_rank", 0), item.get("title", "")))


def _literature_rank(candidate: dict[str, Any]) -> int:
    paper_type = candidate.get("paper_type", "unknown")
    decision = candidate.get("literature_screening_decision", "")
    if decision == "eligible_for_validation_extraction":
        return 10
    if paper_type == "unknown":
        return 40
    if paper_type == "method":
        return 50
    if paper_type == "review":
        return 60
    return 70


def _allow_review_literature(evidence_plan: dict[str, Any], scope_bundle: dict[str, Any]) -> bool:
    text = json.dumps({"evidence_plan": evidence_plan, "scope_bundle": scope_bundle}, ensure_ascii=False).lower()
    return any(term in text for term in ["include_review_literature", "review evidence", "systematic review", "综述", "meta-analysis", "background_literature"])


def _raw_publication_type_text(raw: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ["pubtype", "pubType", "publicationType", "pubTypeList", "journalInfo"]:
        value = raw.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif isinstance(value, dict):
            parts.extend(str(item) for item in value.values())
        elif value:
            parts.append(str(value))
    return " ".join(parts)


def _dataset_profile(candidate: dict[str, Any], *, modality: str, organism: str, platform: str) -> dict[str, Any]:
    inference = _infer_dataset_metadata(candidate)
    profile = DatasetProfile(
        dataset_id=candidate["accession"],
        modality=modality or "unknown",
        organism=organism or "unknown",
        tissue=inference.get("tissue") or "unknown",
        status="profiled_from_metadata",
    )
    data = asdict(profile)
    data["dataset_profile_id"] = make_stable_id("dataset_profile", {"accession": candidate["accession"], "source": candidate["source_database"]})
    data["resource_candidate_id"] = candidate["resource_candidate_id"]
    data["source_database"] = candidate["source_database"]
    data["platform"] = platform or "unknown"
    data["group_metadata_status"] = inference.get("group_metadata_status") or "not_assessed"
    data["sample_size_status"] = inference.get("sample_size_status") or "not_assessed"
    data["group_column"] = inference.get("group_column", "")
    data["case_label"] = inference.get("case_label", "")
    data["control_label"] = inference.get("control_label", "")
    data["sample_count"] = inference.get("sample_count", "")
    data["metadata_inference"] = inference
    data["analysis_readiness"] = "metadata_only_review_required"
    return data


def _infer_dataset_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    raw = candidate.get("raw_metadata") or {}
    text = _metadata_text(candidate, raw)
    tissue = _infer_tissue_from_text(text)
    sample_count = _infer_sample_count(raw, text)
    labels = _infer_group_labels(text)
    inference: dict[str, Any] = {
        "schema_version": "v5.dataset_metadata_inference/0.1",
        "method": "conservative_title_summary_raw_metadata_parser",
        "confidence": "low",
        "limitations": [
            "inferred from public metadata text only",
            "human review is still required before DATASETS_LOCKED",
        ],
    }
    if tissue:
        inference["tissue"] = tissue
    if sample_count:
        inference["sample_count"] = str(sample_count)
        inference["sample_size_status"] = "inferred_from_metadata"
    if labels:
        inference.update(labels)
        inference["group_metadata_status"] = "inferred_from_metadata"
    if tissue or sample_count or labels:
        inference["confidence"] = "medium" if sample_count and tissue else "low"
    return inference


def _metadata_text(candidate: dict[str, Any], raw: dict[str, Any]) -> str:
    parts = [
        candidate.get("title", ""),
        candidate.get("summary", ""),
        candidate.get("organism", ""),
        candidate.get("platform", ""),
    ]
    for key in [
        "title",
        "summary",
        "studytitle",
        "description",
        "organism",
        "taxon",
        "platform",
        "instrument",
        "sample",
        "samples",
        "sample_attributes",
        "characteristics",
    ]:
        value = raw.get(key)
        if isinstance(value, (dict, list)):
            parts.append(json.dumps(value, ensure_ascii=False))
        elif value:
            parts.append(str(value))
    return " ".join(parts).lower()


def _infer_tissue_from_text(text: str) -> str:
    patterns = [
        (r"\bskeletal muscle\b|\bmuscle biopsy\b|\bvastus lateralis\b|\bmyotube\b|\bmyofiber\b", "skeletal muscle"),
        (r"\bendothelial\b|\bendothelium\b|\bhuvec\b", "endothelium"),
        (r"\bcarotid artery\b|\baorta\b|\bvascular\b", "vascular tissue"),
        (r"\bblood\b|\bpbmc\b|\bperipheral blood\b", "blood"),
        (r"\badipose\b|\bfat tissue\b", "adipose tissue"),
        (r"\bliver\b|\bhepatic\b", "liver"),
        (r"\bpancreatic islet\b|\bislet\b|\bpancreas\b", "pancreatic islet"),
        (r"\bheart\b|\bcardiac\b|\bmyocard", "heart"),
    ]
    for pattern, tissue in patterns:
        if re.search(pattern, text):
            return tissue
    return ""


def _infer_sample_count(raw: dict[str, Any], text: str) -> int | None:
    for key in ["n_samples", "sample_count", "samples", "samplecount", "sample_count_total", "gsm_count", "gse_samples"]:
        value = raw.get(key)
        parsed = _parse_positive_int(value)
        if parsed:
            return parsed
    candidates = []
    for match in re.finditer(r"\b(\d{1,6})\s+(?:human\s+)?(?:samples|specimens|subjects|patients|controls|donors|biopsies|cells)\b", text):
        value = _parse_positive_int(match.group(1))
        if value:
            candidates.append(value)
    return max(candidates) if candidates else None


def _parse_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) and value > 0:
        return value
    match = re.search(r"\b(\d{1,6})\b", str(value))
    if not match:
        return None
    parsed = int(match.group(1))
    return parsed if parsed > 0 else None


def _infer_group_labels(text: str) -> dict[str, str]:
    disease_terms = [
        "sarcopenia",
        "diabetes",
        "type 2 diabetes",
        "aging",
        "aged",
        "senescence",
        "disease",
        "case",
        "patient",
    ]
    control_terms = ["control", "healthy", "young", "normal", "non diabetic", "non-diabetic"]
    case_label = next((term for term in disease_terms if term in text), "")
    control_label = next((term for term in control_terms if term in text), "")
    if not case_label or not control_label:
        return {}
    return {
        "group_column": "condition",
        "case_label": case_label,
        "control_label": control_label,
    }


def _dataset_decision(candidate: dict[str, Any], *, decision: str, reason: str) -> dict[str, Any]:
    payload = DatasetSelectionDecision(dataset_id=candidate["accession"], decision=decision, reason=reason, status="requires_review")
    data = asdict(payload)
    data["decision_id"] = make_stable_id("dataset_selection_decision", {"accession": candidate["accession"], "decision": decision, "source": candidate["source_database"]})
    data["resource_candidate_id"] = candidate["resource_candidate_id"]
    data["verified"] = candidate["verified"]
    data["source_database"] = candidate["source_database"]
    data["blocking_issues"] = ["group_metadata_not_assessed", "sample_size_not_assessed", "raw_data_not_imported"]
    return data


def _fetch_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "TargetCompassLite/0.1 (resource-discovery-agent)"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _write_resource_discovery_bundle(project_dir: Path, bundle: dict[str, Any]) -> None:
    path = project_dir / "v5" / "resource_discovery" / "resource_discovery_bundle.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _first_text(item: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, list):
            value = "; ".join(str(part) for part in value if part)
        if value:
            return str(value)
    return ""


def _dataset_accession_from_summary(source: str, item: dict[str, Any], uid: str) -> str:
    if source == "sra":
        text = " ".join(str(item.get(key, "")) for key in ["accession", "studyacc", "runs", "expxml", "extrelations"])
        match = re.search(r"\b(SR[APRXS]\d+)\b", text)
        if match:
            return match.group(1)
    accession = _first_text(item, ["accession", "gse", "extrelations"])
    return accession or f"{source.upper()}:{uid}"


def _dataset_title_from_summary(source: str, item: dict[str, Any]) -> str:
    title = _first_text(item, ["title", "studytitle", "exp_title", "exptitle"])
    if title:
        return title
    if source == "sra":
        expxml = str(item.get("expxml", ""))
        match = re.search(r"<Title>(.*?)</Title>", expxml, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _infer_modality(title: str, summary: str, platform: str) -> str:
    text = f"{title} {summary} {platform}".lower()
    if "single cell" in text or "scrna" in text or "snrna" in text:
        return "single_cell_expression"
    if "rna-seq" in text or "transcript" in text or "expression" in text or "array" in text:
        return "bulk_expression"
    return "unknown"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _query_terms_from_evidence_axes(value: Any) -> list[str]:
    mapped = []
    for axis in _as_list(value):
        key = axis.strip().lower()
        if key in {"sasp_annotation", "senescence", "senescence_sasp"}:
            mapped.extend(["senescence", "SASP"])
        elif key in {"cell_type_specificity", "cell_type_expression"}:
            mapped.extend(["single cell", "cell type"])
        elif key in {"disease_relevant_expression", "condition_upregulation"}:
            mapped.extend(["transcriptome", "expression"])
        elif key in {"secreted_or_surface_annotation", "surface_marker_annotation"}:
            mapped.extend(["surface", "secreted"])
        elif "_" not in key:
            mapped.append(axis)
    return mapped


def _dedupe(values: list[str]) -> list[str]:
    out = []
    seen = set()
    for value in values:
        cleaned = " ".join(str(value).strip().split())
        key = cleaned.lower()
        if cleaned and key not in seen:
            out.append(cleaned)
            seen.add(key)
    return out
