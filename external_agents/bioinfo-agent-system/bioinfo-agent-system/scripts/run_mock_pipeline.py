from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bioinfo_agent_system.io_utils import read_text
from bioinfo_agent_system.orchestrator import run_pipeline


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(
            "Usage: python scripts/run_mock_pipeline.py "
            "examples/input_question_t2d_adipose_secretome.txt",
            file=sys.stderr,
        )
        return 1

    question_path = _resolve_path(argv[1])
    raw_question = read_text(question_path)
    state = run_pipeline(raw_question, str(PROJECT_ROOT / "outputs"))
    run_dir = PROJECT_ROOT / "outputs" / "mock_run" / state.run_id
    print(
        json.dumps(
            {
                "status": "passed",
                "run_id": state.run_id,
                "run_dir": str(run_dir),
            },
            indent=2,
        )
    )
    return 0


def _resolve_path(raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
