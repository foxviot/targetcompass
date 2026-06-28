import json
import os
from pathlib import Path


SECRET_FILE = "secrets.local.json"


def secrets_path(project_dir: Path) -> Path:
    return project_dir / "configs" / SECRET_FILE


def load_secrets(project_dir: Path) -> dict:
    path = secrets_path(project_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_openai_api_key(project_dir: Path, api_key: str) -> None:
    key = api_key.strip()
    if not key:
        raise ValueError("API key is empty")
    path = secrets_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    secrets = load_secrets(project_dir)
    secrets["OPENAI_API_KEY"] = key
    path.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    os.environ["OPENAI_API_KEY"] = key


def save_llm_provider(project_dir: Path, provider: str, base_url: str = "", model: str = "") -> None:
    selected = (provider or "openai").strip().lower()
    if selected not in {"openai", "deepseek"}:
        raise ValueError(f"unsupported LLM provider: {provider}")
    path = secrets_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    secrets = load_secrets(project_dir)
    secrets["TARGETCOMPASS_LLM_PROVIDER"] = selected
    if base_url.strip():
        secrets["TARGETCOMPASS_LLM_BASE_URL"] = base_url.strip().rstrip("/")
    if model.strip():
        secrets["TARGETCOMPASS_OPENAI_MODEL"] = model.strip()
    path.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    os.environ["TARGETCOMPASS_LLM_PROVIDER"] = selected
    if base_url.strip():
        os.environ["TARGETCOMPASS_LLM_BASE_URL"] = base_url.strip().rstrip("/")
    if model.strip():
        os.environ["TARGETCOMPASS_OPENAI_MODEL"] = model.strip()


def clear_openai_api_key(project_dir: Path) -> None:
    secrets = load_secrets(project_dir)
    secrets.pop("OPENAI_API_KEY", None)
    path = secrets_path(project_dir)
    if secrets:
        path.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    elif path.exists():
        path.unlink()
    os.environ.pop("OPENAI_API_KEY", None)


def apply_project_secrets(project_dir: Path) -> None:
    secrets = load_secrets(project_dir)
    key = secrets.get("OPENAI_API_KEY")
    if key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = key
    for env_name in ["TARGETCOMPASS_LLM_PROVIDER", "TARGETCOMPASS_LLM_BASE_URL", "TARGETCOMPASS_OPENAI_MODEL"]:
        value = secrets.get(env_name)
        if value and not os.environ.get(env_name):
            os.environ[env_name] = value


def masked_openai_key(project_dir: Path) -> str:
    key = os.environ.get("OPENAI_API_KEY") or load_secrets(project_dir).get("OPENAI_API_KEY", "")
    if not key:
        return "not set"
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"


def llm_provider_summary(project_dir: Path) -> dict:
    secrets = load_secrets(project_dir)
    provider = os.environ.get("TARGETCOMPASS_LLM_PROVIDER") or secrets.get("TARGETCOMPASS_LLM_PROVIDER", "openai")
    base_url = os.environ.get("TARGETCOMPASS_LLM_BASE_URL") or secrets.get("TARGETCOMPASS_LLM_BASE_URL", "")
    model = os.environ.get("TARGETCOMPASS_OPENAI_MODEL") or secrets.get("TARGETCOMPASS_OPENAI_MODEL", "")
    return {"provider": provider, "base_url": base_url, "model": model}
