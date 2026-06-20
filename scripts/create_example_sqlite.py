import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "examples" / "databases" / "sample_target_evidence.sqlite"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    con = sqlite3.connect(OUT)
    try:
        con.execute(
            """
            CREATE TABLE target_evidence (
              target_symbol TEXT,
              type TEXT,
              score REAL,
              pvalue REAL,
              confidence REAL,
              association TEXT,
              location TEXT,
              description TEXT
            )
            """
        )
        con.executemany(
            "INSERT INTO target_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                ("VCAM1", "database_prior", 1.2, 0.01, 0.8, "positive", "cell surface", "example external target evidence"),
                ("IL6", "database_prior", 0.9, 0.04, 0.7, "positive", "secreted", "example cytokine evidence"),
                ("CXCL8", "database_prior", 0.7, 0.05, 0.6, "positive", "secreted", "example chemokine evidence"),
            ],
        )
        con.commit()
    finally:
        con.close()
    print(OUT)


if __name__ == "__main__":
    main()
