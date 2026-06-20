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
    key = load_secrets(project_dir).get("OPENAI_API_KEY")
    if key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = key


def masked_openai_key(project_dir: Path) -> str:
    key = os.environ.get("OPENAI_API_KEY") or load_secrets(project_dir).get("OPENAI_API_KEY", "")
    if not key:
        return "not set"
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}"
