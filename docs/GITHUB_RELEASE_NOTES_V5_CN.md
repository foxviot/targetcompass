# TargetCompass v5 本地开发版发布说明

## 项目简介

TargetCompass 是一个面向生信靶点发现的本地 Agent 平台原型。当前 v5 版本以 canonical control plane 为核心，把自然语言研究问题拆成结构化对象、Agent handoff、资源发现、TaskPacket、TaskRun、QCReport、Artifact Registry、Evidence/Report 引用链，并提供本地 Web UI、Windows 安装脚本和开发验收命令。

## 本次提交重点

- v5 canonical schema、ProjectState、EventLog、Agent handoff 协议。
- Evidence-driven route：EvidencePlan、DatasetProfile、MethodContract、CompatibilityDecision。
- Resource discovery / resource gate：GEO/SRA/cellxgene 候选、metadata 人工纠错、matrix_parse_ready 门控。
- v5 local run：问题输入到 resource discovery、task packet、local execution、QC、Artifact Registry、canonical report manifest。
- PostgreSQL / MinIO 本地后端：EvidenceRepository、ArtifactStore、backend write audit、storage primary gate。
- SRA/cellxgene matrix path validation：不再把 metadata-only 候选误判为真实矩阵可分析。
- Codex Worker 协议与工程闭环雏形：approve / claim / execute / patch / test / result registry。
- Nextflow / Docker / Apptainer / Windows 安装器脚手架与本地 runtime 检测。
- PilotDeck Web UI：流程、资源门控、存储、发布验收、报告、审计、配置入口。

## 已通过的本地验收

- `python tc_lite.py v5-doctor --project vascular_aging_demo`
- `python tc_lite.py v5-storage-primary-gate --project vascular_aging_demo`
- `python tc_lite.py v5-release-acceptance --project vascular_aging_demo --question-count 50`
- 关键单测：
  - `tests.test_canonical_resource_gate`
  - `tests.test_canonical_matrix_path_validation`
  - `tests.test_canonical_analysis_main_path`
  - `tests.test_canonical_storage_primary_gate`
  - `tests.test_release_acceptance`
  - `tests.test_v5_doctor`

## 当前限制

- SRA/cellxgene 真实大样本矩阵路径仍需接入实际下载/量化或 h5ad export 后再验收。
- Windows 干净机安装、启动、停止、重启、卸载 smoke 需要在目标机器或虚拟机上记录。
- OIDC/Vault/多用户正式 auth 未作为本地开发版交付目标。
- 大型运行产物、数据库、MinIO 对象缓存、安装器输出和密钥文件不应提交到 GitHub。

## 推荐启动

```powershell
python tc_lite.py serve --project vascular_aging_demo --host 127.0.0.1 --port 8831
```

访问：

```text
http://127.0.0.1:8831/
```

## 推荐提交标题

```text
Prepare TargetCompass v5 local platform release
```

## 推荐 PR 摘要

```text
This change packages the TargetCompass v5 local platform control plane, including canonical agent protocols, evidence-driven routing, dataset/resource gates, ArtifactStore/EvidenceRepository primary-path audit, local execution contracts, v5 UI pages, Windows packaging scripts, and release acceptance checks. Runtime outputs, local secrets, downloaded matrices, object-store caches, and installer binaries are intentionally excluded from Git.
```
