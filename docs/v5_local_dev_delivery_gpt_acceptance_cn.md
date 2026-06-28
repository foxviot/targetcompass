# TargetCompass v5 本地开发版交付与 GPT 验收说明

## 交付定位

这是 v5 本地开发版，不是零环境生产安装包。它用于让教授电脑或另一个 GPT/Codex 验收 v5 canonical 控制面：问题输入、Agent handoff、资源发现、task packet、本地执行、QC、Artifact Registry、Report Manifest、后端偏好读取和安装后自检。

## 主要交付文件

- 工程根目录：`C:/Users/ASUS/Documents/target`
- v5 默认项目：`C:/Users/ASUS/Documents/target/projects/vascular_aging_demo`
- v5 自检报告：`C:/Users/ASUS/Documents/target/projects/vascular_aging_demo/v5/doctor/v5_doctor_report.json`
- v5 后端激活：`C:/Users/ASUS/Documents/target/projects/vascular_aging_demo/v5/active_backends.json`
- v5 报告清单：`C:/Users/ASUS/Documents/target/projects/vascular_aging_demo/v5/reports/canonical_report_manifest.json`
- v5 流程页：`http://127.0.0.1:8801/v5/flow`
- v5 本地执行包：`C:/Users/ASUS/Documents/target/projects/vascular_aging_demo/v5/local_execution/local_execution_bundle.json`
- v5 Artifact Registry：`C:/Users/ASUS/Documents/target/projects/vascular_aging_demo/v5/artifact_registry.jsonl`
- 开发 bundle：`C:/Users/ASUS/Documents/target/dist/targetcompass_v5_local_bundle_20260623T113401Z.zip`
- Windows 安装脚本包：`C:/Users/ASUS/Documents/target/dist/TargetCompassV5_Windows_Installer_20260623T113501Z.zip`

## GPT/Codex 验收命令

在 `C:/Users/ASUS/Documents/target` 下执行：

```powershell
python tc_lite.py v5-doctor --project vascular_aging_demo
python tc_lite.py v5-report-manifest --project vascular_aging_demo
python tc_lite.py v5-run-local --project vascular_aging_demo --question "肌少症患者肌肉背景细胞中是否存在特征性表面分子的 SASP 评分高细胞？" --limit 2 --max-analysis-packets 2
python -m unittest tests.test_v5_doctor tests.test_canonical_report_manifest tests.test_local_backends tests.test_webapp -v
```

## 预期结果

- `v5-doctor` 返回 `PASS` 或 `WARN`，并写入 `v5/doctor/v5_doctor_report.json`。
- `v5-report-manifest` 生成 canonical report manifest，并包含 `backend_preference`。
- `v5-run-local` 可以生成或刷新 `project_state.json`、handoff、resource discovery、task packets、TaskRun、QCReport、Artifact Registry 和 Report Manifest。
- UI 首页和 `/v5/flow` 能显示 Agent、handoff、task、artifact、QC、claim ceiling、human gate。

## 后端说明

v5 查询优先读取 `v5/active_backends.json`：

- 如果状态是 `ACTIVE`，Evidence DB 偏好为 `postgres_local`，Artifact 偏好为 `minio_local`。
- 如果状态是 `FALLBACK` 或文件缺失，系统回退到 SQLite/local filesystem。
- 当前开发版仍保留本地文件作为可追溯副本，不删除 local artifacts。

## 已知边界

- 这是开发交付版，仍需要 Python 环境运行。
- PostgreSQL/MinIO 是本地 Docker 后端优先策略，不等同云端多用户生产部署。
- v5 report manifest 是 canonical 引用清单，不代表所有科学 claim 已人工签出。
- 真实科学结论必须经过 QC、Question Alignment Auditor 和人工审核。
