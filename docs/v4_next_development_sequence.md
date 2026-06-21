# TargetCompass v4.0 Remaining Development Sequence

This file records the execution order after the current Nextflow work.

## P5/P6 Completed In This Pass

- Nextflow tasks contract:
  - `workflows/target_discovery/tasks.json`
  - built from v4 WorkOrders
  - supports module filtering through CLI `--module-id`
- Real Nextflow local runner contract:
  - CLI: `python tc_lite.py nextflow-run --project <project> --profile local`
  - command shape: `nextflow run ... -profile local`
  - optional resume: `--resume`
  - collects `.nextflow.log`, report, timeline, trace, DAG
  - writes `workflows/target_discovery/nextflow_run_manifest.json`
  - writes run metadata back to `v4/work_order_attempts.json`
- Failure recovery contract:
  - missing Nextflow is reported as structured failure
  - trace failures are parsed into `recovery.failed_tasks`
  - resume command is preserved in the run manifest
- Container execution contract:
  - Dockerfile scaffold: `workflows/target_discovery/Dockerfile.targetcompass-lite`
  - container manifest contains build command and production digest policy
- UI execution panel:
  - generate `tasks.json`
  - run local profile
  - resume run
  - inspect run manifest, artifacts, failed tasks, and recovery advice

## P6 Remaining

- Build and test a real Docker image locally.
- Replace `targetcompass-lite:local` with immutable image tags and digests.
- Add Apptainer build recipe for HPC environments.
- Add mounted input/output path policy for container execution.
- Add per-process resource tuning from WorkOrder parameters.

## P7 Service Split Remaining

- Extract Project API service as a standalone process.
- Extract Evidence service around `evidence.sqlite` and trace queries.
- Extract Registry service for methods, sources, rubrics, adapters, and snapshots.
- Extract Report service for report build, validation, package export, and signoff.
- Keep MCP Gateway as the only external tool entrypoint.
- Add service-to-service identity and request audit.
- Add migration tests that compare monolith results with service-mode results.

## P7 Multi-Agent Remaining

- Promote current role runs into a typed orchestration graph.
- Add strict JSON schemas for:
  - Disease Normalizer
  - Dataset Scout
  - Planner
  - Method Reviewer
  - Result Reviewer
  - Causal Reviewer
  - Report Writer
- Enforce no-self-approval:
  - generator role cannot approve its own outputs
  - reviewer role must write ReviewItem records
- Add agent retry and fallback policy.
- Add per-role model/method selection in UI.

## P8 Production Delivery Remaining

- One-click installer for clean Windows machines.
- Dependency checker for Python, R, Java, Nextflow, Docker/Apptainer.
- Log rotation and project backup/restore.
- Multi-project permission isolation.
- Human-readable deployment guide for professors/non-engineers.
- End-to-end acceptance suite using a small fixture project and one real GEO project.
