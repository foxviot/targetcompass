import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "projects" / "vascular_aging_demo"


def run(command: list[str]) -> None:
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    if result.returncode:
        raise SystemExit(
            "command failed: "
            + " ".join(command)
            + f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    print(result.stdout.strip())


def assert_file(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise SystemExit(f"missing or empty file: {path}")


def main() -> None:
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    run([sys.executable, "tc_lite.py", "demo", "--project", "vascular_aging_demo"])
    for path in [
        PROJECT / "candidate_scores.csv",
        PROJECT / "evidence.sqlite",
        PROJECT / "results" / "enrichment" / "enrichment_results.tsv",
        PROJECT / "results" / "annotation" / "unknown_review.tsv",
        PROJECT / "reports" / "target_report.html",
        PROJECT / "reports" / "target_report.docx",
    ]:
        assert_file(path)
    con = sqlite3.connect(PROJECT / "evidence.sqlite")
    try:
        evidence_count = con.execute("SELECT COUNT(*) FROM evidence_item").fetchone()[0]
    finally:
        con.close()
    summary = json.loads((PROJECT / "results" / "evidence_import" / "import_summary.json").read_text(encoding="utf-8"))
    print(f"SMOKE OK evidence_count={evidence_count} rejected_rows={summary['rejected_rows']}")


if __name__ == "__main__":
    main()
