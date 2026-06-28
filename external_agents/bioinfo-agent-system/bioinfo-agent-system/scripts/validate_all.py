from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bioinfo_agent_system.claim_audit import audit_project_claims
from bioinfo_agent_system.cross_agent_rules import run_cross_agent_validation
from bioinfo_agent_system.io_utils import read_json
from bioinfo_agent_system.registry import PROJECT_ROOT as PACKAGE_ROOT, get_agent_records
from bioinfo_agent_system.validators import (
    validate_data_against_schema,
    validate_data_file,
    validate_schema_catalog,
    validate_schema_file,
)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "Usage: python scripts/validate_all.py outputs/mock_run/<run_id>/",
            file=sys.stderr,
        )
        return 1

    run_dir = _resolve_path(argv[1])
    schema_paths = sorted(PACKAGE_ROOT.rglob("*.schema.json"))
    validate_schema_catalog(schema_paths)

    handoff_schema = validate_schema_file(
        PACKAGE_ROOT / "schemas" / "shared" / "agent_handoff.schema.json"
    )
    project_state_schema = validate_schema_file(
        PACKAGE_ROOT / "schemas" / "shared" / "project_state.schema.json"
    )

    for record in get_agent_records():
        validate_data_file(
            PACKAGE_ROOT / "examples" / "expected_outputs" / record.output_filename,
            record.output_schema_path,
        )
        validate_data_file(run_dir / "agent_outputs" / record.output_filename, record.output_schema_path)
        handoff_path = run_dir / "handoffs" / f"{record.agent_id}.handoff.json"
        handoff_data = read_json(handoff_path)
        validate_data_against_schema(handoff_data, handoff_schema, handoff_path.as_posix())

    project_state_path = run_dir / "project_state.json"
    project_state = read_json(project_state_path)
    validate_data_against_schema(project_state, project_state_schema, project_state_path.as_posix())

    cross_agent_events = run_cross_agent_validation(project_state)
    claim_events = audit_project_claims(project_state)

    report = {
        "status": "passed",
        "schemas_validated": len(schema_paths),
        "example_outputs_validated": len(get_agent_records()),
        "run_outputs_validated": len(get_agent_records()),
        "handoffs_validated": len(get_agent_records()),
        "cross_agent_events": cross_agent_events,
        "claim_audit_events": claim_events,
    }
    print(json.dumps(report, indent=2))
    return 0


def _resolve_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
