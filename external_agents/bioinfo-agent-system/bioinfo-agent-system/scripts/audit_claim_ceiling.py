from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bioinfo_agent_system.claim_audit import audit_project_claims
from bioinfo_agent_system.io_utils import read_json


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "Usage: python scripts/audit_claim_ceiling.py outputs/mock_run/<run_id>/",
            file=sys.stderr,
        )
        return 1

    run_dir = _resolve_path(argv[1])
    project_state = read_json(run_dir / "project_state.json")
    events = audit_project_claims(project_state)
    print(json.dumps({"status": "passed", "events": events}, indent=2))
    return 0


def _resolve_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
