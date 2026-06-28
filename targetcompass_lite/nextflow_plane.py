import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analysis_modules import ANALYSIS_MODULES
from .v4 import content_hash


NEXTFLOW_PLANE_SCHEMA = "v4.nextflow_execution_plane/0.1"


MODULE_CONTRACTS = {
    "bulk_deg_v1": {
        "process_name": "BULK_DEG",
        "entry": "workflows/common/modules/bulk_deg/main.nf",
        "command": "python tc_lite.py run-deg --project ${params.project} --dataset ${dataset_id}",
        "inputs": {"dataset_id": "string", "expression_matrix": "file", "metadata": "file"},
        "outputs": ["results/bulk_deg_${dataset_id}/deg_results.tsv", "results/bulk_deg_${dataset_id}/qc_summary.json", "results/bulk_deg_${dataset_id}/executor_manifest.json"],
        "container": "targetcompass-lite:local",
        "container_digest": "",
        "cpus": 2,
        "memory": "4 GB",
        "time": "2h",
    },
    "scrna_pseudobulk_v1": {
        "process_name": "SCRNA_PSEUDOBULK",
        "entry": "workflows/common/modules/scrna_pseudobulk/main.nf",
        "command": "python tc_lite.py scrna-pseudobulk --project ${params.project} --dataset-id ${dataset_id} --count-matrix ${count_matrix} --metadata ${metadata}",
        "inputs": {"dataset_id": "string", "count_matrix": "file", "metadata": "file"},
        "outputs": ["results/scrna_pseudobulk_${dataset_id}/pseudobulk_matrix.tsv", "results/scrna_pseudobulk_${dataset_id}/qc_summary.json"],
        "container": "targetcompass-lite:local",
        "container_digest": "",
        "cpus": 2,
        "memory": "8 GB",
        "time": "4h",
    },
    "enrichment_v2": {
        "process_name": "ENRICHMENT",
        "entry": "workflows/common/modules/enrichment/main.nf",
        "command": "python tc_lite.py enrichment --project ${params.project}",
        "inputs": {"deg_results": "directory"},
        "outputs": ["results/enrichment/enrichment_results.tsv", "results/enrichment/gsea_preranked_results.tsv", "results/enrichment/gene_set_snapshot.json"],
        "container": "targetcompass-lite:local",
        "container_digest": "",
        "cpus": 1,
        "memory": "2 GB",
        "time": "1h",
    },
    "deg_meta_analysis_v1": {
        "process_name": "META_ANALYSIS",
        "entry": "workflows/common/modules/meta_analysis/main.nf",
        "command": "python tc_lite.py meta-analysis --project ${params.project}",
        "inputs": {"deg_results": "directory"},
        "outputs": ["results/meta_analysis/deg_meta_analysis.tsv", "results/meta_analysis/qc_summary.json", "results/meta_analysis/forest_plot_index.tsv"],
        "container": "targetcompass-lite:local",
        "container_digest": "",
        "cpus": 1,
        "memory": "2 GB",
        "time": "1h",
    },
    "genetic_coloc_mr_v1": {
        "process_name": "GENETIC_COLOC_MR",
        "entry": "workflows/common/modules/genetic_coloc_mr/main.nf",
        "command": "python tc_lite.py genetic-coloc-mr --project ${params.project} --gwas-summary ${gwas_summary} --qtl-summary ${qtl_summary} --dataset-id ${dataset_id} --ld-reference ${ld_reference}",
        "inputs": {"gwas_summary": "file", "qtl_summary": "file", "ld_reference": "optional_file"},
        "outputs": ["results/genetic_coloc_mr/coloc_results.tsv", "results/genetic_coloc_mr/mr_results.tsv", "results/genetic_coloc_mr/qc_summary.json"],
        "container": "targetcompass-lite:local",
        "container_digest": "",
        "cpus": 2,
        "memory": "4 GB",
        "time": "2h",
    },
}


def build_nextflow_execution_plane(project_dir: Path, base_image: str = "python:3.11-slim") -> dict[str, Any]:
    root = project_dir / "workflows"
    target = root / "target_discovery"
    common = root / "common" / "modules"
    target.mkdir(parents=True, exist_ok=True)
    common.mkdir(parents=True, exist_ok=True)
    written = []
    for contract in MODULE_CONTRACTS.values():
        module_dir = common / _module_dir_name(contract["process_name"])
        module_dir.mkdir(parents=True, exist_ok=True)
        nf_path = module_dir / "main.nf"
        nf_path.write_text(_module_nf(contract), encoding="utf-8")
        contract_path = module_dir / "module_contract.json"
        contract_path.write_text(json.dumps(_module_contract_payload(contract), indent=2, ensure_ascii=False), encoding="utf-8")
        written.extend([nf_path, contract_path])
    main_nf = target / "main.nf"
    config = target / "nextflow.config"
    params_schema = target / "params.schema.json"
    container_manifest = target / "container_manifest.json"
    dockerfile = target / "Dockerfile.targetcompass-lite"
    resume_manifest = target / "resume_manifest.template.json"
    main_nf.write_text(_target_main_nf(), encoding="utf-8")
    config.write_text(_nextflow_config(), encoding="utf-8")
    params_schema.write_text(json.dumps(_params_schema(), indent=2, ensure_ascii=False), encoding="utf-8")
    container_manifest.write_text(json.dumps(_container_manifest(project_dir, base_image=base_image), indent=2, ensure_ascii=False), encoding="utf-8")
    dockerfile.write_text(_dockerfile(base_image=base_image), encoding="utf-8")
    resume_manifest.write_text(json.dumps(_resume_manifest(project_dir), indent=2, ensure_ascii=False), encoding="utf-8")
    from .nextflow_profiles import build_nextflow_profile_matrix

    profile_matrix = build_nextflow_profile_matrix(project_dir)
    written.extend([main_nf, config, params_schema, container_manifest, dockerfile, resume_manifest])
    payload = {
        "schema_version": NEXTFLOW_PLANE_SCHEMA,
        "project_id": project_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "generated",
        "workflow_root": str(root.relative_to(project_dir)),
        "entrypoint": str(main_nf.relative_to(project_dir)),
        "config": str(config.relative_to(project_dir)),
        "params_schema": str(params_schema.relative_to(project_dir)),
        "profiles": ["local", "docker", "apptainer", "slurm"],
        "profile_matrix": "workflows/target_discovery/execution_profile_matrix.json",
        "profile_matrix_hash": profile_matrix.get("matrix_hash", ""),
        "module_count": len(MODULE_CONTRACTS),
        "modules": [_module_contract_payload(contract) for contract in MODULE_CONTRACTS.values()],
        "container_manifest": str(container_manifest.relative_to(project_dir)),
        "dockerfile": str(dockerfile.relative_to(project_dir)),
        "resume_manifest_template": str(resume_manifest.relative_to(project_dir)),
        "generated_files": [str(path.relative_to(project_dir)) for path in written],
        "plane_hash": content_hash([path.read_text(encoding="utf-8") for path in written]),
        "limitations": [
            "Generated DSL2 scaffold invokes the existing local CLI runners; production containers must replace targetcompass-lite:local with immutable digests.",
            "Nextflow installation is not required for generation tests, but execution requires Nextflow and the selected executor profile.",
        ],
    }
    manifest_path = target / "nextflow_execution_plane.json"
    manifest_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def validate_nextflow_execution_plane(project_dir: Path) -> dict[str, Any]:
    manifest_path = project_dir / "workflows" / "target_discovery" / "nextflow_execution_plane.json"
    if not manifest_path.exists():
        raise ValueError("Nextflow execution plane has not been generated")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing = [rel for rel in manifest.get("generated_files", []) if not (project_dir / rel).exists()]
    module_errors = []
    for module in manifest.get("modules", []):
        if not module.get("container"):
            module_errors.append(f"{module.get('module_id')}: container missing")
        if not module.get("outputs"):
            module_errors.append(f"{module.get('module_id')}: outputs missing")
    from .nextflow_profiles import validate_nextflow_resource_policy

    resource_validation = validate_nextflow_resource_policy(project_dir)
    if resource_validation.get("status") == "failed":
        module_errors.extend(row.get("reason", "") for row in resource_validation.get("issues", []) if row.get("severity") == "failed")
    status = "pass" if not missing and not module_errors else "failed"
    result = {
        "schema_version": "v4.nextflow_execution_plane_validation/0.1",
        "project_id": project_dir.name,
        "status": status,
        "missing_files": missing,
        "module_errors": module_errors,
        "resource_policy_status": resource_validation.get("status", ""),
        "resource_policy": "workflows/target_discovery/resource_policy_validation.json",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    (project_dir / "workflows" / "target_discovery" / "nextflow_validation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def _module_dir_name(process_name: str) -> str:
    return process_name.lower()


def _module_contract_payload(contract: dict[str, Any]) -> dict[str, Any]:
    module_id = _module_id_for_process(contract["process_name"])
    registry = next((row for row in ANALYSIS_MODULES if row["module_id"] == module_id), {})
    return {
        "schema_version": "v4.nextflow_module_contract/0.1",
        "module_id": module_id,
        "process_name": contract["process_name"],
        "status": registry.get("status", "implemented"),
        "runner": registry.get("runner", ""),
        "entry": contract["entry"],
        "inputs": contract["inputs"],
        "outputs": contract["outputs"],
        "resources": {"cpus": contract["cpus"], "memory": contract["memory"], "time": contract["time"]},
        "container": contract["container"],
        "container_digest": contract["container_digest"],
        "network": "disabled",
        "resume": {"strategy": "nextflow_resume_plus_executor_resume_key"},
    }


def _module_id_for_process(process_name: str) -> str:
    mapping = {
        "BULK_DEG": "bulk_deg_v1",
        "SCRNA_PSEUDOBULK": "scrna_pseudobulk_v1",
        "ENRICHMENT": "enrichment_v2",
        "META_ANALYSIS": "deg_meta_analysis_v1",
        "GENETIC_COLOC_MR": "genetic_coloc_mr_v1",
    }
    return mapping[process_name]


def _module_nf(contract: dict[str, Any]) -> str:
    command = _task_command(contract)
    return f"""process {contract['process_name']} {{
  tag "${{job.label ?: job.module_id}}"
  container '{contract['container']}'
  cpus {{ job.resources?.cpus ?: {contract['cpus']} }}
  memory {{ job.resources?.memory ?: '{contract['memory']}' }}
  time {{ job.resources?.time ?: '{contract['time']}' }}
  publishDir "${{params.outdir}}", mode: 'copy', overwrite: true

  input:
  val job

  output:
  path "results", emit: results

  script:
  \"\"\"
  set -euo pipefail
  mkdir -p results
  cd "${{params.repo_root}}"
  {command}
  mkdir -p "${{job.process_name ?: job.module_id}}"
  echo "completed {contract['process_name']} ${{job.dataset_id ?: 'project'}}" > "${{job.process_name ?: job.module_id}}/nextflow_process_marker.txt"
  cp -r "${{job.process_name ?: job.module_id}}" results/
  \"\"\"
}}
"""


def _task_command(contract: dict[str, Any]) -> str:
    process = contract["process_name"]
    if process == "BULK_DEG":
        return '"${params.host_python}" tc_lite.py run-deg --project ${params.project} --dataset ${job.dataset_id}'
    if process == "SCRNA_PSEUDOBULK":
        return '"${params.host_python}" tc_lite.py scrna-pseudobulk --project ${params.project} --dataset-id ${job.dataset_id} --count-matrix "${job.count_matrix}" --metadata "${job.metadata}"'
    if process == "GENETIC_COLOC_MR":
        return '"${params.host_python}" tc_lite.py genetic-coloc-mr --project ${params.project} --gwas-summary "${job.gwas_summary}" --qtl-summary "${job.qtl_summary}" --dataset-id "${job.dataset_id}" --ld-reference "${job.ld_reference ?: \'\'}"'
    if process == "ENRICHMENT":
        return '"${params.host_python}" tc_lite.py enrichment --project ${params.project}'
    if process == "META_ANALYSIS":
        return '"${params.host_python}" tc_lite.py meta-analysis --project ${params.project}'
    return "echo unsupported module"


def _target_main_nf() -> str:
    includes = "\n".join(
        f"include {{ {contract['process_name']} }} from '../common/modules/{_module_dir_name(contract['process_name'])}/main.nf'"
        for contract in MODULE_CONTRACTS.values()
    )
    return f"""nextflow.enable.dsl=2

{includes}

workflow TARGET_DISCOVERY {{
  take:
  tasks

  main:
  task_ch = Channel.fromPath(params.tasks_json)
    .splitJson(path: 'tasks')
    .map {{ task -> task + [label: task.module_id + ':' + (task.dataset_id ?: 'project'), process_name: task.module_id] }}

  bulk_tasks = task_ch.filter {{ it.module_id == 'bulk_deg_v1' }}
  scrna_tasks = task_ch.filter {{ it.module_id == 'scrna_pseudobulk_v1' }}
  genetic_tasks = task_ch.filter {{ it.module_id == 'genetic_coloc_mr_v1' }}

  BULK_DEG(bulk_tasks)
  SCRNA_PSEUDOBULK(scrna_tasks)
  GENETIC_COLOC_MR(genetic_tasks)

  ENRICHMENT(Channel.of([module_id: 'enrichment_v2', dataset_id: 'project']))
  META_ANALYSIS(Channel.of([module_id: 'deg_meta_analysis_v1', dataset_id: 'project']))

  emit:
  bulk = BULK_DEG.out.results
}}

  workflow {{
  TARGET_DISCOVERY(Channel.empty())
}}
"""


def _nextflow_config() -> str:
    return """manifest {
  name = 'targetcompass-discovery'
  description = 'TargetCompass v4 local Nextflow execution plane'
  version = '0.1.0'
}

params {
  project = 'vascular_aging_demo'
  outdir = 'nextflow_results'
  tasks_json = 'workflows/target_discovery/tasks.example.json'
  repo_root = '.'
  host_python = 'python'
}

process {
  errorStrategy = 'terminate'
  maxRetries = 1
  withName: '.*' {
    container = 'targetcompass-lite:local'
  }
}

profiles {
  local {
    process.executor = 'local'
  }
  docker {
    docker.enabled = true
    process.executor = 'local'
  }
  apptainer {
    apptainer.enabled = true
    process.executor = 'local'
  }
  slurm {
    process.executor = 'slurm'
    process.queue = 'default'
  }
}
"""


def _params_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "TargetCompass Nextflow parameters",
        "type": "object",
        "required": ["project", "outdir", "tasks_json"],
        "properties": {
            "project": {"type": "string"},
            "outdir": {"type": "string"},
            "tasks_json": {"type": "string"},
        },
        "additionalProperties": True,
    }


def _container_manifest(project_dir: Path, base_image: str = "python:3.11-slim") -> dict[str, Any]:
    return {
        "schema_version": "v4.container_manifest/0.1",
        "project_id": project_dir.name,
        "policy": {
            "production_requires_digest_pinning": True,
            "latest_tags_forbidden_in_production": True,
            "network_disabled_by_default": True,
        },
        "containers": [
            {
                "image": "targetcompass-lite:local",
                "base_image": base_image,
                "digest": "",
                "status": "local_development_placeholder",
                "dockerfile": "workflows/target_discovery/Dockerfile.targetcompass-lite",
                "build_command": "docker build --build-arg TARGETCOMPASS_BASE_IMAGE=<base> -f workflows/target_discovery/Dockerfile.targetcompass-lite -t targetcompass-lite:local .",
                "production_image_policy": "replace local tag with immutable registry image plus digest before shared execution",
                "modules": sorted(MODULE_CONTRACTS),
            }
        ],
    }


def _dockerfile(base_image: str = "python:3.11-slim") -> str:
    return f"""ARG TARGETCOMPASS_BASE_IMAGE={base_image}
FROM ${{TARGETCOMPASS_BASE_IMAGE}}
ARG TARGETCOMPASS_UPGRADE_PIP=false
WORKDIR /app
COPY . /app
RUN python -V && if [ "$TARGETCOMPASS_UPGRADE_PIP" = "true" ]; then python -m pip install --no-cache-dir -U pip; fi
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "tc_lite.py"]
"""


def _resume_manifest(project_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": "v4.nextflow_resume_manifest/0.1",
        "project_id": project_dir.name,
        "nextflow_resume": True,
        "executor_resume_key_required": True,
        "attempt_id": "",
        "work_order_id": "",
        "nextflow_run_name": "",
        "cache_policy": "reuse only when work_order_hash, input hashes, parameters, module contract, profile, and container digest match",
    }
