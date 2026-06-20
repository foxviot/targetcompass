import json
import shutil
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any

from .annotation import annotate_project
from .agent_roles import write_agent_role_manifest
from .cli import init_project
from .deg import run_deg
from .enrichment import run_enrichment
from .evidence_db import import_evidence
from .causal_evidence import grade_causal_evidence
from .geo_discovery import discover_geo_datasets
from .matching import match_project
from .meta_analysis import run_meta_analysis
from .methods.contracts import MethodContext
from .methods.registry import load_method_config, run_method
from .planning import build_plan
from .reporting import build_report
from .role_runner import load_role_runs, run_role
from .run_state import check_cancelled, clear_cancel, write_status
from .scoring import score_project
from .screening import screen_project
from .spec_builder import readiness_errors, update_project_spec
from .validators import validate_dataset_card, validate_research_spec
from .v4 import build_v4_manifest, finish_work_order_attempt, start_work_order_attempt


AGENT_STATE_MACHINE = [
    {
        "state": "generation",
        "label": "生成",
        "purpose": "Convert the user request into ResearchSpec and generate candidate ideas.",
        "replaceable_method_stage": "query",
    },
    {
        "state": "initial_review",
        "label": "初审",
        "purpose": "Run ResearchSpec readiness gates and feasibility audit before any computation.",
        "replaceable_method_stage": "audit",
    },
    {
        "state": "verification",
        "label": "查证",
        "purpose": "Validate selected datasets, screen eligibility, match ResearchSpec, and compile an AnalysisPlan.",
        "replaceable_method_stage": "dataset_verification",
    },
    {
        "state": "execution",
        "label": "执行",
        "purpose": "Run deterministic local analysis modules and evidence integration.",
        "replaceable_method_stage": "execution",
    },
    {
        "state": "final_review",
        "label": "复审",
        "purpose": "Draft experiments and summarize remaining review gates after execution.",
        "replaceable_method_stage": "experiment",
    },
    {
        "state": "report",
        "label": "报告",
        "purpose": "Generate report and delivery-ready artifacts after final review.",
        "replaceable_method_stage": "reporting",
    },
]


@dataclass
class AgentStage:
    name: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentRunResult:
    status: str
    message: str
    stages: list[AgentStage]
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "stages": [
                {
                    "name": stage.name,
                    "status": stage.status,
                    "message": stage.message,
                    "details": stage.details,
                }
                for stage in self.stages
            ],
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


class _DemoArgs:
    def __init__(self, project: str, dataset: list[str]):
        self.project = project
        self.dataset = dataset


class TargetDiscoveryAgent:
    """Application-owned agent harness for GPT-generated target discovery runs."""

    def __init__(self, project: str):
        self.project = project
        self.project_dir = init_project(project)
        self.stages: list[AgentStage] = []
        self.last_request: dict[str, Any] = {}

    def _stage(self, name: str, status: str, message: str, **details: Any) -> None:
        order = len(self.stages) + 1
        machine = next((item for item in AGENT_STATE_MACHINE if item["state"] == name), {})
        self.stages.append(
            AgentStage(
                name=name,
                status=status,
                message=message,
                details={
                    "order": order,
                    "label": machine.get("label", name),
                    "purpose": machine.get("purpose", ""),
                    **details,
                },
            )
        )

    def run(
        self,
        interest: str,
        parser: str,
        selected_datasets: list[str],
        confirmed: bool,
        idea_count: int = 6,
    ) -> AgentRunResult:
        self.last_request = {
            "interest": interest,
            "parser": parser,
            "selected_datasets": selected_datasets,
            "confirmed": confirmed,
            "ideas": idea_count,
        }
        if not interest.strip():
            return self._finish("failed", "Research request is required.")
        if not selected_datasets:
            return self._finish("failed", "Select at least one dataset.")

        clear_cancel(self.project_dir)
        stdout = StringIO()
        stderr = StringIO()
        method_context: MethodContext | None = None
        analysis_plan: dict[str, Any] | None = None

        check_cancelled(self.project_dir)
        self._stage(
            "generation",
            "running",
            "Generate ResearchSpec and candidate ideas from the user research request.",
            parser=parser,
            confirmed=confirmed,
            selected_datasets=selected_datasets,
            idea_count=idea_count,
        )
        try:
            spec, _ = run_role(
                self.project_dir,
                "disease_normalizer",
                {"interest": interest, "parser": parser, "confirmed": confirmed},
                lambda: update_project_spec(self.project_dir, interest, parser=parser, confirmed=confirmed),
                runner="targetcompass_lite.spec_builder.update_project_spec",
                method_id=load_method_config(self.project_dir).get("disease_normalizer"),
                parameters={"parser": parser, "confirmed": confirmed},
            )
            rc = _errors_to_exception(validate_research_spec(self.project_dir / "research_spec.json"))
            if rc:
                raise ValueError(rc)
            method_context = MethodContext(
                project_dir=self.project_dir,
                interest=interest,
                parser=parser,
                selected_datasets=selected_datasets,
                confirmed=confirmed,
                idea_count=idea_count,
            )
            query_result = run_method("query", method_context)
        except Exception as exc:
            self._stage("generation", "failed", str(exc))
            return self._finish("failed", f"Generation failed: {exc}", stdout.getvalue(), stderr.getvalue())

        metadata = spec.get("parser_metadata", {})
        self._stage(
            "generation",
            query_result.status,
            "ResearchSpec and candidate idea batch generated.",
            disease=spec.get("disease_scope", {}).get("canonical", "unknown"),
            confidence=metadata.get("confidence", "unknown"),
            parser_version=metadata.get("parser_version", "unknown"),
            query_method=load_method_config(self.project_dir).get("query"),
            query_message=query_result.message,
            **query_result.details,
        )

        check_cancelled(self.project_dir)
        self._stage("initial_review", "running", "Run ResearchSpec readiness gates and feasibility audit.")
        try:
            ready_errors = readiness_errors(spec)
            if ready_errors:
                self._stage("initial_review", "blocked", "ResearchSpec did not pass readiness gates.", errors=ready_errors)
                return self._finish("blocked", "Cannot run analysis: " + " ".join(ready_errors), stdout.getvalue(), stderr.getvalue())
            audit_result, _ = run_role(
                self.project_dir,
                "method_reviewer",
                {"research_spec": "research_spec.json", "analysis_plan": "pending", "method_stage": "audit"},
                lambda: run_method("audit", method_context),
                runner="targetcompass_lite.methods.run_method:audit",
                method_id=load_method_config(self.project_dir).get("method_reviewer") or load_method_config(self.project_dir).get("audit"),
                parameters={"method_stage": "audit"},
            )
        except Exception as exc:
            self._stage("initial_review", "failed", str(exc))
            return self._finish("failed", f"Initial review failed: {exc}", stdout.getvalue(), stderr.getvalue())
        self._stage(
            "initial_review",
            audit_result.status,
            audit_result.message,
            audit_method=load_method_config(self.project_dir).get("audit"),
            **audit_result.details,
        )

        check_cancelled(self.project_dir)
        self._stage("verification", "running", "Validate datasets, screen eligibility, match ResearchSpec, and compile plan.")
        try:
            geo_discovery, _ = run_role(
                self.project_dir,
                "dataset_scout",
                {"research_spec": "research_spec.json", "selected_datasets": selected_datasets},
                lambda: discover_geo_datasets(self.project_dir, limit=6, timeout=6),
                runner="targetcompass_lite.geo_discovery.discover_geo_datasets",
                method_id=load_method_config(self.project_dir).get("dataset_scout"),
                parameters={"limit": 6, "timeout": 6},
            )
            self._validate_selected_dataset_cards(selected_datasets)
            rows = screen_project(self.project_dir, set(selected_datasets))
            matches = match_project(self.project_dir, set(selected_datasets))
            analysis_plan, _ = run_role(
                self.project_dir,
                "planner",
                {"eligible_datasets": "eligible_datasets.csv", "selected_datasets": selected_datasets},
                lambda: build_plan(self.project_dir),
                runner="targetcompass_lite.planning.build_plan",
                method_id=load_method_config(self.project_dir).get("planner"),
            )
        except Exception as exc:
            self._stage("verification", "failed", str(exc))
            return self._finish("failed", f"Verification failed: {exc}", stdout.getvalue(), stderr.getvalue())
        review_count = sum(1 for row in matches if row.get("match_status") != "MATCH")
        eligible_count = sum(1 for row in rows if row.get("grade") in {"A", "B", "C"})
        module_count = len(analysis_plan.get("modules", []))
        self._stage(
            "verification",
            "review" if review_count else "pass",
            f"{len(matches)} dataset(s) checked; {eligible_count} eligible; {module_count} module(s) planned.",
            selected_datasets=selected_datasets,
            review_count=review_count,
            eligible_count=eligible_count,
            module_count=module_count,
            datasets=[row.get("dataset_id") for row in rows],
            geo_discovery_mode=geo_discovery.get("mode", ""),
            geo_recommendations=[
                {
                    "accession": row.get("accession", ""),
                    "score": row.get("score", ""),
                    "source": row.get("source", ""),
                    "import_status": row.get("import_status", ""),
                }
                for row in geo_discovery.get("recommendations", [])[:5]
            ],
            geo_discovery_warnings=geo_discovery.get("warnings", []),
        )

        check_cancelled(self.project_dir)
        self._stage("execution", "running", "Run deterministic analysis modules and evidence integration.")
        try:
            self._clear_unselected_bulk_outputs(selected_datasets)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                executed = self._execute_plan(analysis_plan)
        except Exception as exc:
            self._stage("execution", "failed", str(exc))
            return self._finish("failed", "Execution failed.", stdout.getvalue(), stderr.getvalue())
        self._stage(
            "execution",
            "pass",
            "Analysis modules, enrichment, annotation, evidence import, and scoring completed.",
            executed_modules=executed,
        )

        check_cancelled(self.project_dir)
        self._stage("final_review", "running", "Draft experiments and summarize remaining review gates.")
        try:
            experiment_result, _ = run_role(
                self.project_dir,
                "result_reviewer",
                {"candidate_scores": "candidate_scores.csv", "qc": "results/*/qc_summary.json", "method_stage": "experiment"},
                lambda: run_method("experiment", method_context),
                runner="targetcompass_lite.methods.run_method:experiment",
                method_id=load_method_config(self.project_dir).get("result_reviewer") or load_method_config(self.project_dir).get("experiment"),
                parameters={"method_stage": "experiment"},
            )
            review_summary = self._review_summary()
        except Exception as exc:
            self._stage("final_review", "failed", str(exc))
            return self._finish("failed", f"Final review failed: {exc}", stdout.getvalue(), stderr.getvalue())
        self._stage(
            "final_review",
            experiment_result.status,
            experiment_result.message,
            experiment_method=load_method_config(self.project_dir).get("experiment"),
            **review_summary,
            **experiment_result.details,
        )

        check_cancelled(self.project_dir)
        self._stage("report", "running", "Generate final report artifacts.")
        try:
            report_output, _ = run_role(
                self.project_dir,
                "report_writer",
                {"evidence_db": "evidence.sqlite", "scores": "candidate_scores.csv"},
                lambda: build_report(self.project_dir),
                runner="targetcompass_lite.reporting.build_report",
                method_id=load_method_config(self.project_dir).get("report_writer"),
            )
            html_path, docx_path = report_output
        except Exception as exc:
            self._stage("report", "failed", str(exc))
            return self._finish("failed", f"Report generation failed: {exc}", stdout.getvalue(), stderr.getvalue())
        self._stage(
            "report",
            "pass",
            "Report and audit artifacts are ready for user review.",
            html_report=str(html_path),
            docx_report=str(docx_path),
        )
        build_v4_manifest(self.project_dir, analysis_plan)
        write_agent_role_manifest(self.project_dir, self._role_observations())
        return self._finish("success", "Agent workflow completed.", stdout.getvalue(), stderr.getvalue())

    def _validate_selected_dataset_cards(self, selected_datasets: list[str]) -> None:
        card_paths = sorted((self.project_dir / "dataset_cards").glob("*.yaml"))
        available = {path.stem: path for path in card_paths}
        missing = sorted(set(selected_datasets) - set(available))
        if missing:
            raise ValueError("selected dataset not found: " + ", ".join(missing))
        for dataset_id in selected_datasets:
            errors = validate_dataset_card(available[dataset_id])
            if errors:
                raise ValueError(f"{dataset_id}: " + "; ".join(errors))

    def _clear_unselected_bulk_outputs(self, selected_datasets: list[str]) -> None:
        selected = set(selected_datasets)
        for out_dir in (self.project_dir / "results").glob("bulk_deg_*"):
            dataset_id = out_dir.name.replace("bulk_deg_", "")
            if dataset_id not in selected:
                shutil.rmtree(out_dir)

    def _execute_plan(self, analysis_plan: dict[str, Any]) -> list[dict[str, str]]:
        executed: list[dict[str, str]] = []
        for module in analysis_plan.get("modules", []):
            check_cancelled(self.project_dir)
            if module.get("module") == "bulk_deg":
                attempt = start_work_order_attempt(self.project_dir, module["module_id"], "")
                try:
                    result_path = run_deg(self.project_dir, module["dataset_id"])
                    artifacts = [
                        str(result_path.relative_to(self.project_dir)),
                        f"results/bulk_deg_{module['dataset_id']}/qc_summary.json",
                        f"results/bulk_deg_{module['dataset_id']}/run_manifest.json",
                    ]
                    finish_work_order_attempt(self.project_dir, attempt["attempt_id"], "success", artifacts)
                    executed.append(
                        {
                            "module": "bulk_deg",
                            "dataset_id": module["dataset_id"],
                            "status": "executed",
                            "attempt_id": attempt["attempt_id"],
                        }
                    )
                except Exception as exc:
                    finish_work_order_attempt(self.project_dir, attempt["attempt_id"], "failed", failure_reason=str(exc))
                    raise
            else:
                executed.append({"module": module.get("module", "unknown"), "dataset_id": module.get("dataset_id", ""), "status": "planned"})
        check_cancelled(self.project_dir)
        enrichment_path = run_enrichment(self.project_dir)
        meta_path = run_meta_analysis(self.project_dir)
        access_path, safety_path, review_path = annotate_project(self.project_dir)
        evidence_path = import_evidence(self.project_dir)
        causal_path = grade_causal_evidence(self.project_dir)
        if _has_data_rows(causal_path):
            evidence_path = import_evidence(self.project_dir)
        scores_path = score_project(self.project_dir)
        executed.extend(
            [
                {"module": "enrichment", "dataset_id": "*", "status": str(enrichment_path)},
                {"module": "meta_analysis", "dataset_id": "*", "status": str(meta_path)},
                {"module": "annotation", "dataset_id": "*", "status": str(access_path)},
                {"module": "safety", "dataset_id": "*", "status": str(safety_path)},
                {"module": "unknown_review", "dataset_id": "*", "status": str(review_path)},
                {"module": "causal_evidence", "dataset_id": "*", "status": str(causal_path)},
                {"module": "evidence_import", "dataset_id": "*", "status": str(evidence_path)},
                {"module": "scoring", "dataset_id": "*", "status": str(scores_path)},
            ]
        )
        return executed

    def _review_summary(self) -> dict[str, Any]:
        dataset_rows = _read_csv(self.project_dir / "dataset_match_report.csv")
        score_rows = _read_csv(self.project_dir / "candidate_scores.csv")
        unknown_rows = _read_csv(self.project_dir / "results" / "annotation" / "unknown_review.tsv", delimiter="\t")
        return {
            "dataset_review_count": sum(1 for row in dataset_rows if row.get("match_status") != "MATCH"),
            "top_candidate_count": min(20, len(score_rows)),
            "top_candidate_hard_gate_issues": sum(1 for row in score_rows[:20] if row.get("hard_gate_status") != "PASS"),
            "unknown_annotation_rows": len(unknown_rows),
        }

    def _finish(self, status: str, message: str, stdout: str = "", stderr: str = "") -> AgentRunResult:
        result = AgentRunResult(status=status, message=message, stages=list(self.stages), stdout=stdout, stderr=stderr)
        self._write_trace(result)
        failure_reason = message if status in {"failed", "blocked"} else ""
        write_status(
            self.project_dir,
            status,
            message,
            stdout,
            stderr,
            result.to_dict()["stages"],
            last_request=self.last_request,
            failure_reason=failure_reason,
        )
        return result

    def _write_trace(self, result: AgentRunResult) -> None:
        trace = result.to_dict()
        trace["project"] = self.project
        trace["timestamp"] = datetime.now(timezone.utc).isoformat()
        trace["architecture"] = "local_state_machine_agent_v1"
        trace["v4_compatibility"] = {
            "object_manifest": "v4/object_manifest.json",
            "state_machine": "v4/state_machine.json",
            "work_orders": "v4/work_orders.json",
            "mcp_resources": "v4/mcp_resources.json",
            "evidence_snapshot": "v4/evidence_snapshot.json",
            "agent_roles": "v4/agent_roles.json",
        }
        trace["state_machine"] = AGENT_STATE_MACHINE
        trace["method_config"] = load_method_config(self.project_dir)
        path = self.project_dir / "results" / "agent_trace.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")

    def _role_observations(self) -> dict[str, Any]:
        observations = {
            "disease_normalizer": {"request": self.last_request.get("interest", ""), "parser": self.last_request.get("parser", "")},
            "dataset_scout": {"selected_datasets": self.last_request.get("selected_datasets", [])},
            "planner": {"analysis_plan": "analysis_plan.json"},
            "method_reviewer": {"review_queue": "results/review_queue.json"},
            "result_reviewer": {"scores": "candidate_scores.csv"},
            "report_writer": {"report": "reports/target_report.html"},
        }
        for record in load_role_runs(self.project_dir).get("runs", []):
            role_id = record.get("role_id", "")
            if not role_id:
                continue
            observations.setdefault(role_id, {})
            observations[role_id].update(
                {
                    "latest_role_run_id": record.get("role_run_id", ""),
                    "latest_status": record.get("status", ""),
                    "latest_input_packet": record.get("input_packet", ""),
                    "latest_output_packet": record.get("output_packet", ""),
                    "latest_log": record.get("log", ""),
                }
            )
        return observations


def _errors_to_exception(errors: list[str]) -> str:
    return "; ".join(errors)


def _read_csv(path: Path, delimiter: str = ",") -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        import csv

        return list(csv.DictReader(f, delimiter=delimiter))


def _has_data_rows(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8") as f:
        next(f, None)
        return any(line.strip() for line in f)

