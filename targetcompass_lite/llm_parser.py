import json
import os
import urllib.error
import urllib.request


DEFAULT_MODEL = os.environ.get("TARGETCOMPASS_OPENAI_MODEL", "gpt-4.1-mini")


RESEARCH_SPEC_SCHEMA = {
    "name": "research_spec",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "goal",
            "research_theme",
            "disease_scope",
            "organisms",
            "priority_tissues",
            "priority_cells",
            "target_routes",
        ],
        "properties": {
            "goal": {"type": "string"},
            "research_theme": {"type": "string"},
            "disease_scope": {
                "type": "object",
                "additionalProperties": False,
                "required": ["canonical", "related_phenotypes"],
                "properties": {
                    "canonical": {"type": "string"},
                    "related_phenotypes": {"type": "array", "items": {"type": "string"}},
                },
            },
            "organisms": {"type": "array", "items": {"type": "string"}},
            "priority_tissues": {"type": "array", "items": {"type": "string"}},
            "priority_cells": {"type": "array", "items": {"type": "string"}},
            "target_routes": {"type": "array", "items": {"type": "string"}},
        },
    },
}


IDEA_BATCH_SCHEMA = {
    "name": "target_idea_batch",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ideas"],
        "properties": {
            "ideas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "route", "rationale"],
                    "properties": {
                        "title": {"type": "string"},
                        "route": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                },
            }
        },
    },
}


def parse_with_openai(interest: str, project_id: str, model: str = DEFAULT_MODEL) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "You convert biomedical research interests into a conservative TargetCompass ResearchSpec. "
                    "Do not invent datasets, results, scores, or claims. Use unknown when the disease scope is unclear."
                ),
            },
            {"role": "user", "content": interest},
        ],
        "text": {"format": {"type": "json_schema", "json_schema": RESEARCH_SPEC_SCHEMA}},
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI parser request failed: {exc.code} {detail}") from exc
    text = _extract_response_text(data)
    parsed = json.loads(text)
    parsed["project_id"] = project_id
    parsed["modalities_mvp"] = {
        "required": ["bulk_expression", "accessibility_annotation", "safety_annotation"],
        "optional": ["enrichment", "manual_genetic_evidence"],
    }
    parsed["constraints"] = {
        "causal_requirement": "preferred_not_mandatory",
        "critical_normal_tissues": ["brain", "heart", "liver", "kidney", "hematopoietic_stem_cell"],
        "claim_policy": "association_only_without_genetic_or_experimental_validation",
    }
    parsed["parser_metadata"] = {
        "parser_version": "openai_responses_v1",
        "model": model,
        "confidence": "requires_user_review",
        "confirmation_required": True,
        "confirmed": False,
    }
    return parsed


def generate_ideas_with_openai(interest: str, count: int, model: str = DEFAULT_MODEL) -> list[dict]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "Generate conservative biomedical target discovery ideas. "
                    "Do not invent datasets, results, scores, or validated claims. "
                    "Each idea must be executable with downstream evidence review."
                ),
            },
            {
                "role": "user",
                "content": f"Research request: {interest}\nGenerate exactly {max(1, min(int(count), 50))} target discovery ideas.",
            },
        ],
        "text": {"format": {"type": "json_schema", "json_schema": IDEA_BATCH_SCHEMA}},
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI idea request failed: {exc.code} {detail}") from exc
    parsed = json.loads(_extract_response_text(data))
    return parsed.get("ideas", [])


def _extract_response_text(data: dict) -> str:
    if data.get("output_text"):
        return data["output_text"]
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                return content["text"]
    raise RuntimeError("OpenAI parser response did not contain text output")
