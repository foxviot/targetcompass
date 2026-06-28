import hashlib
import json
import re
from typing import Any


def hash_payload(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def make_stable_id(prefix: str, payload: Any) -> str:
    safe_prefix = normalize_id_text(prefix) or "id"
    return f"{safe_prefix}_{hash_payload(payload)[:16]}"


def normalize_id_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")
