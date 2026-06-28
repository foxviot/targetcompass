import csv
import html
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .database_validation import validate_online_databases
from .evidence_db import build_evidence_db_snapshot, import_evidence
from .literature_validation import run_literature_validation
from .paths import project_path
from .reporting import build_report
from .scoring import score_project


DELIVERY_SCHEMA = "targetcompass.validation_delivery/0.1"


def run_validation_delivery(
    project: str,
    output_root: str = "D:/TargetCompass_validation_delivery",
    query: str = "",
    limit: int = 100,
    batch_size: int = 10,
    use_llm: bool = True,
    timeout: int = 45,
    genes: list[str] | None = None,
    db_query: str = "type 2 diabetes skeletal muscle SASP",
) -> dict[str, Any]:
    project_dir = project_path(project)
    delivery_dir = _new_delivery_dir(Path(output_root), project_dir.name)
    (delivery_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (delivery_dir / "visualizations").mkdir(parents=True, exist_ok=True)
    (delivery_dir / "tables").mkdir(parents=True, exist_ok=True)

    steps: list[dict[str, Any]] = []
    literature = _step(
        steps,
        "1_literature_validation",
        lambda: run_literature_validation(
            project_dir,
            query=query,
            limit=limit,
            batch_size=batch_size,
            use_llm=use_llm,
            timeout=timeout,
        ),
    )
    selected_genes = genes or _top_genes_from_literature(project_dir)[:8] or _top_genes_from_scores(project_dir)[:8]
    database = _step(
        steps,
        "2_online_database_validation",
        lambda: validate_online_databases(project_dir, genes=selected_genes, query=db_query, limit=10, timeout=timeout, adapt=True),
    )
    _step(steps, "3_import_evidence", lambda: {"evidence_db": str(import_evidence(project_dir))})
    snapshot = _step(steps, "4_evidence_snapshot", lambda: build_evidence_db_snapshot(project_dir))
    _step(steps, "5_candidate_scoring", lambda: {"candidate_scores": str(score_project(project_dir))})
    report_paths = _step(steps, "6_report_build", lambda: {"reports": [str(path) for path in build_report(project_dir)]})

    summary = _build_summary(project_dir, literature, database, snapshot, steps)
    visuals = _build_visualizations(project_dir, delivery_dir, summary)
    copied = _copy_core_artifacts(project_dir, delivery_dir, report_paths)
    table_refs = _write_delivery_tables(project_dir, delivery_dir)
    report_path = _write_validation_report(delivery_dir, project_dir, summary, steps, visuals, copied, table_refs)
    manifest = {
        "schema_version": DELIVERY_SCHEMA,
        "project_id": project_dir.name,
        "delivery_dir": str(delivery_dir),
        "query": literature.get("query", query),
        "steps": steps,
        "summary": summary,
        "visualizations": visuals,
        "copied_artifacts": copied,
        "tables": table_refs,
        "entry_report": str(report_path),
        "generated_at": _now(),
    }
    (delivery_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def _step(steps: list[dict[str, Any]], name: str, fn) -> dict[str, Any]:
    started = _now()
    try:
        result = fn()
        status = "success"
        failure = ""
    except Exception as exc:
        result = {}
        status = "failed"
        failure = str(exc)
    steps.append({"step": name, "status": status, "started_at": started, "finished_at": _now(), "failure_reason": failure})
    if status != "success":
        raise RuntimeError(f"{name} failed: {failure}")
    return result


def _new_delivery_dir(root: Path, project_id: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = root / f"{project_id}_{stamp}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _build_summary(project_dir: Path, literature: dict[str, Any], database: dict[str, Any], snapshot: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    articles = _read_json(project_dir / "results" / "literature_validation" / "pubmed_articles.json", {}).get("articles", [])
    decisions = _read_json(project_dir / "results" / "literature_validation" / "literature_decisions.json", {}).get("decisions", [])
    lit_rows = _read_tsv(project_dir / "results" / "literature_validation" / "literature_evidence.tsv")
    scores = _read_csv(project_dir / "candidate_scores.csv")
    by_relevance = Counter(str(row.get("relevance", "unknown")) for row in decisions)
    by_gene = Counter(row.get("entity_symbol", "") for row in lit_rows if row.get("entity_symbol") and row.get("entity_symbol") != "UNKNOWN")
    by_source_status = Counter(row.get("status", "") for row in database.get("sources", []))
    return {
        "article_count": len(articles),
        "decision_count": len(decisions),
        "literature_evidence_rows": len(lit_rows),
        "literature_relevance": dict(by_relevance),
        "top_literature_genes": by_gene.most_common(15),
        "database_source_count": database.get("source_count", 0),
        "database_success_count": database.get("success_count", 0),
        "database_status": dict(by_source_status),
        "database_sources": database.get("sources", []),
        "evidence_db_rows": snapshot.get("row_count", 0),
        "evidence_type_counts": snapshot.get("by_evidence_type", {}),
        "evidence_level_counts": snapshot.get("by_evidence_level", {}),
        "top_candidates": scores[:10],
        "step_success_count": len([row for row in steps if row.get("status") == "success"]),
    }


def _build_visualizations(project_dir: Path, delivery_dir: Path, summary: dict[str, Any]) -> list[dict[str, str]]:
    visuals = []
    visuals.append(_bar_svg(delivery_dir / "visualizations" / "literature_relevance.svg", "PubMed 文献相关性分布", summary.get("literature_relevance", {})))
    visuals.append(_bar_svg(delivery_dir / "visualizations" / "database_status.svg", "在线数据库验证状态", summary.get("database_status", {})))
    visuals.append(_bar_svg(delivery_dir / "visualizations" / "evidence_type_counts.svg", "Evidence DB 证据类型分布", summary.get("evidence_type_counts", {}), limit=12))
    visuals.append(_bar_svg(delivery_dir / "visualizations" / "evidence_level_counts.svg", "证据等级与权重层级分布", summary.get("evidence_level_counts", {}), limit=12))
    visuals.append(_score_svg(delivery_dir / "visualizations" / "top_candidate_scores.svg", "候选分子评分 Top 10", summary.get("top_candidates", [])))
    return [{"title": item[0], "path": _rel(item[1], delivery_dir)} for item in visuals]


def _bar_svg(path: Path, title: str, values: dict[str, Any], limit: int = 20) -> tuple[str, Path]:
    items = [(str(k), float(v or 0)) for k, v in values.items()]
    items = sorted(items, key=lambda row: (-row[1], row[0]))[:limit]
    width = 980
    row_h = 34
    height = max(160, 90 + len(items) * row_h)
    max_v = max([v for _, v in items] or [1])
    rows = []
    for idx, (label, value) in enumerate(items):
        y = 70 + idx * row_h
        bar_w = 720 * value / max_v if max_v else 0
        rows.append(
            f'<text x="24" y="{y + 20}" font-size="14" fill="#1f2937">{html.escape(label)}</text>'
            f'<rect x="230" y="{y}" width="{bar_w:.1f}" height="22" rx="6" fill="#2563eb"/>'
            f'<text x="{240 + bar_w:.1f}" y="{y + 17}" font-size="13" fill="#374151">{value:g}</text>'
        )
    _write_svg(path, width, height, title, rows)
    return title, path


def _score_svg(path: Path, title: str, rows_in: list[dict[str, str]]) -> tuple[str, Path]:
    values = {}
    for row in rows_in[:10]:
        try:
            values[row.get("entity_symbol", "")] = float(row.get("final_score", 0))
        except ValueError:
            values[row.get("entity_symbol", "")] = 0
    return _bar_svg(path, title, values, limit=10)


def _write_svg(path: Path, width: int, height: int, title: str, rows: list[str]) -> None:
    text = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#f8fafc"/>
  <text x="24" y="38" font-family="Arial, Microsoft YaHei, sans-serif" font-size="24" font-weight="700" fill="#111827">{html.escape(title)}</text>
  <g font-family="Arial, Microsoft YaHei, sans-serif">
    {"".join(rows)}
  </g>
</svg>
'''
    path.write_text(text, encoding="utf-8")


def _copy_core_artifacts(project_dir: Path, delivery_dir: Path, report_paths: dict[str, Any]) -> list[dict[str, str]]:
    refs = [
        project_dir / "results" / "literature_validation" / "pubmed_articles.tsv",
        project_dir / "results" / "literature_validation" / "literature_decisions.json",
        project_dir / "results" / "literature_validation" / "literature_evidence.tsv",
        project_dir / "results" / "database_validation" / "online_database_validation.json",
        project_dir / "results" / "database_validation" / "online_database_validation.tsv",
        project_dir / "candidate_scores.csv",
        project_dir / "v4" / "evidence_db_snapshot.json",
        project_dir / "reports" / "target_report.html",
        project_dir / "reports" / "target_report.docx",
        project_dir / "reports" / "target_report_structured.json",
    ]
    out = []
    for src in refs:
        if not src.exists():
            continue
        dst = delivery_dir / "artifacts" / src.name
        shutil.copy2(src, dst)
        out.append({"source": _rel(src, project_dir), "path": _rel(dst, delivery_dir)})
    return out


def _write_delivery_tables(project_dir: Path, delivery_dir: Path) -> list[dict[str, str]]:
    refs = []
    rows = _read_tsv(project_dir / "results" / "literature_validation" / "literature_evidence.tsv")
    by_gene = defaultdict(lambda: {"gene": "", "count": 0, "pmids": set(), "max_quality": 0.0})
    for row in rows:
        gene = row.get("entity_symbol", "")
        if not gene or gene == "UNKNOWN":
            continue
        item = by_gene[gene]
        item["gene"] = gene
        item["count"] += 1
        item["pmids"].add(str(row.get("source_dataset", "")).replace("PubMed:", ""))
        try:
            item["max_quality"] = max(float(item["max_quality"]), float(row.get("quality_score") or 0))
        except ValueError:
            pass
    table_path = delivery_dir / "tables" / "literature_gene_summary.tsv"
    with table_path.open("w", newline="", encoding="utf-8") as f:
        fields = ["gene", "count", "max_quality", "pmids"]
        writer = csv.DictWriter(f, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for item in sorted(by_gene.values(), key=lambda r: (-int(r["count"]), r["gene"])):
            writer.writerow({"gene": item["gene"], "count": item["count"], "max_quality": item["max_quality"], "pmids": ";".join(sorted(item["pmids"]))})
    refs.append({"title": "文献基因汇总", "path": _rel(table_path, delivery_dir)})
    return refs


def _write_validation_report(
    delivery_dir: Path,
    project_dir: Path,
    summary: dict[str, Any],
    steps: list[dict[str, Any]],
    visuals: list[dict[str, str]],
    artifacts: list[dict[str, str]],
    tables: list[dict[str, str]],
) -> Path:
    visual_html = "\n".join(f'<section><h2>{html.escape(v["title"])}</h2><img src="{html.escape(v["path"])}" alt="{html.escape(v["title"])}"></section>' for v in visuals)
    step_rows = "".join(f"<tr><td>{html.escape(s['step'])}</td><td>{html.escape(s['status'])}</td><td>{html.escape(s.get('failure_reason', ''))}</td></tr>" for s in steps)
    source_rows = "".join(
        f"<tr><td>{html.escape(row.get('source_id', ''))}</td><td>{html.escape(row.get('status', ''))}</td><td>{html.escape(str(row.get('row_count', '')))}</td><td>{html.escape(row.get('message', ''))}</td></tr>"
        for row in summary.get("database_sources", [])
    )
    candidate_rows = "".join(
        f"<tr><td>{html.escape(row.get('entity_symbol', ''))}</td><td>{html.escape(row.get('final_score', ''))}</td><td>{html.escape(row.get('tier', ''))}</td><td>{html.escape(row.get('hard_gate_status', ''))}</td><td>{html.escape(row.get('safety_gate', ''))}</td></tr>"
        for row in summary.get("top_candidates", [])
    )
    link_rows = "".join(f'<li><a href="{html.escape(row["path"])}">{html.escape(row.get("title") or row.get("path", ""))}</a></li>' for row in artifacts + tables)
    path = delivery_dir / "validation_report.html"
    path.write_text(
        f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>TargetCompass 真实验证交付报告</title>
  <style>
    body {{ margin: 0; background: #f5f7fb; color: #111827; font-family: Arial, "Microsoft YaHei", sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 34px 24px 70px; }}
    header {{ padding: 24px 0 18px; border-bottom: 1px solid #d8dee9; }}
    h1 {{ margin: 0 0 10px; font-size: 32px; }}
    section {{ background: #fff; border: 1px solid #d8dee9; border-radius: 14px; padding: 18px 20px; margin: 18px 0; box-shadow: 0 10px 24px rgba(15, 23, 42, .04); }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }}
    .metric {{ background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 12px; padding: 14px; }}
    .metric strong {{ display: block; font-size: 26px; margin-top: 8px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 9px; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ color: #475569; background: #f8fafc; }}
    img {{ max-width: 100%; border-radius: 10px; border: 1px solid #e5e7eb; background: #f8fafc; }}
    .note {{ color: #64748b; }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>TargetCompass 真实验证交付报告</h1>
    <p class="note">项目：{html.escape(project_dir.name)}。本报告展示真实 PubMed 文献拉取、LLM 结构化审核、在线数据库验证、Evidence DB 入库、评分和科研报告重建的完整链路。</p>
  </header>
  <section>
    <h2>核心完成成果</h2>
    <div class="grid">
      <div class="metric">PubMed 文献<strong>{summary.get("article_count", 0)}</strong></div>
      <div class="metric">LLM 判定<strong>{summary.get("decision_count", 0)}</strong></div>
      <div class="metric">文献证据行<strong>{summary.get("literature_evidence_rows", 0)}</strong></div>
      <div class="metric">Evidence DB 总行<strong>{summary.get("evidence_db_rows", 0)}</strong></div>
    </div>
  </section>
  <section>
    <h2>执行链路</h2>
    <table><thead><tr><th>环节</th><th>状态</th><th>失败原因</th></tr></thead><tbody>{step_rows}</tbody></table>
  </section>
  {visual_html}
  <section>
    <h2>在线数据库验证</h2>
    <p class="note">成功源：{summary.get("database_success_count", 0)} / {summary.get("database_source_count", 0)}</p>
    <table><thead><tr><th>数据库</th><th>状态</th><th>行数</th><th>说明</th></tr></thead><tbody>{source_rows}</tbody></table>
  </section>
  <section>
    <h2>候选分子 Top 10</h2>
    <table><thead><tr><th>分子</th><th>评分</th><th>层级</th><th>硬门控</th><th>安全性</th></tr></thead><tbody>{candidate_rows}</tbody></table>
  </section>
  <section>
    <h2>交付文件</h2>
    <ul>{link_rows}</ul>
  </section>
</main>
</body>
</html>
""",
        encoding="utf-8",
    )
    return path


def _top_genes_from_literature(project_dir: Path) -> list[str]:
    rows = _read_tsv(project_dir / "results" / "literature_validation" / "literature_evidence.tsv")
    counts = Counter(row.get("entity_symbol", "") for row in rows if row.get("entity_symbol") and row.get("entity_symbol") != "UNKNOWN")
    return [gene for gene, _ in counts.most_common(20)]


def _top_genes_from_scores(project_dir: Path) -> list[str]:
    return [row.get("entity_symbol", "") for row in _read_csv(project_dir / "candidate_scores.csv") if row.get("entity_symbol")]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _read_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"
