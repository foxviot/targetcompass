# TargetCompass v5 开发验收版使用说明

本文档用于把 TargetCompass v5 当前版本交给教授、甲方 GPT、或另一位 Codex 做开发验收。它说明如何解包、启动、运行自检、查看真实验证结果，以及当前版本的边界。

## 1. 版本定位

当前交付物是 **v5 本地开发验收版 / 教授演示版**。

它已经具备：

- v5 canonical agent 控制面；
- 7 个规范 Agent 的 handoff / task packet / claim ceiling / human gate；
- 真实 DeepSeek/OpenAI-compatible LLM 角色执行能力；
- GEO / PubMed / Europe PMC 等真实资源发现试行流程；
- TaskPacket、TaskRun、QCReport、Artifact Registry、Report Manifest；
- PilotDeck 本地 Web UI；
- Windows zip 安装脚本、自检、启动、停止、重启、修复、卸载脚本；
- quick/full/e2e 测试套件；
- 10 个真实研究问题的在线 LLM + resource discovery 验收记录。

它还不是最终商业平台版，因为：

- `.exe` GUI 安装器已在本机编译并完成隔离目录安装/卸载 smoke；
- 还没有在干净 Windows 电脑或虚拟机上完成安装、启动、停止、卸载全流程验收；
- 多用户权限、PostgreSQL/MinIO 主路径、真实 Nextflow 大规模分析、长期 memory、wet-lab protocol 仍是继续开发项；
- 数据集 metadata 不足时仍会严格停在人工 review gate，不会伪造锁库和结果。

## 2. 主要交付文件

工程根目录：

```text
C:\Users\ASUS\Documents\target
```

开发验收推荐交付包：

```text
C:\Users\ASUS\Documents\target\dist\targetcompass_v5_professor_demo_bundle_20260624T051344Z.zip
```

Windows 安装脚本包：

```text
C:\Users\ASUS\Documents\target\dist\TargetCompassV5_Windows_Installer_20260624T062644Z.zip
```

正式 Windows GUI 安装器：

```text
C:\Users\ASUS\Documents\target\dist\TargetCompassV5_Setup.exe
```

交付版本固化清单：

```text
C:\Users\ASUS\Documents\target\projects\vascular_aging_demo\v5\delivery\v5_development_delivery_freeze.json
```

P1 平台化 readiness 清单：

```text
C:\Users\ASUS\Documents\target\projects\vascular_aging_demo\v5\platform\p1_readiness.json
```

10 个真实问题验收结果：

```text
C:\Users\ASUS\Documents\target\projects\vascular_aging_demo\v5\validation\real_question_e2e_10\e2e10_summary.html
```

默认 demo 项目：

```text
C:\Users\ASUS\Documents\target\projects\vascular_aging_demo
```

## 3. 推荐验收环境

最低要求：

- Windows 10 / Windows 11；
- PowerShell；
- Python 3.10+，如果使用安装脚本包，可优先走包内 embedded/runtime 策略；
- 可联网，用于 DeepSeek、PubMed、Europe PMC、GEO 等真实检索；
- 可选：Rscript、Nextflow、Docker Desktop。缺失时 v5-doctor 会给出 WARN 或修复建议。

注意：当前已有 Inno Setup `.exe` 和 zip + PowerShell 脚本两种交付形式；如果要给完全小白电脑一键安装，仍建议补一次真正干净 Windows 电脑或虚拟机验收。

## 4. 解包方式

### 方式 A：开发者直接用当前工程目录

进入工程目录：

```powershell
cd C:\Users\ASUS\Documents\target
```

### 方式 B：从 zip 包解压

把下面的 zip 解压到目标目录，例如 `D:\TargetCompassV5`：

```text
C:\Users\ASUS\Documents\target\dist\targetcompass_v5_professor_demo_bundle_20260624T051344Z.zip
```

然后进入解压后的工程根目录。

### 方式 C：使用 Windows 安装脚本包

解压：

```text
C:\Users\ASUS\Documents\target\dist\TargetCompassV5_Windows_Installer_20260624T062644Z.zip
```

进入解压目录后，优先阅读：

```text
README_CN.md
```

常用脚本：

```text
Install-TargetCompassV5.ps1
Launch-TargetCompassV5.ps1
Stop-TargetCompassV5.ps1
Restart-TargetCompassV5.ps1
Repair-TargetCompassV5.ps1
Uninstall-TargetCompassV5.ps1
```

如果 PowerShell 阻止执行脚本，可在当前 PowerShell 会话中临时允许：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 方式 D：使用正式 GUI 安装器

双击或运行：

```text
C:\Users\ASUS\Documents\target\dist\TargetCompassV5_Setup.exe
```

当前已完成本机隔离目录 silent 安装/卸载烟测。它还不是签名安装器，Windows 可能提示未知发布者。

## 5. 启动 UI

在工程根目录执行：

```powershell
python tc_lite.py serve --project vascular_aging_demo --host 127.0.0.1 --port 8801
```

浏览器打开：

```text
http://127.0.0.1:8801/
```

推荐检查页面：

```text
http://127.0.0.1:8801/v5/flow
http://127.0.0.1:8801/v5/storage
http://127.0.0.1:8801/v5/services
http://127.0.0.1:8801/v5/platform-readiness
http://127.0.0.1:8801/v5/evidence
http://127.0.0.1:8801/v5/reports
```

如果端口被占用，可以换端口：

```powershell
python tc_lite.py serve --project vascular_aging_demo --host 127.0.0.1 --port 8810
```

## 6. 安装后自检

执行：

```powershell
python tc_lite.py v5-doctor --project vascular_aging_demo
```

自检报告输出位置：

```text
projects\vascular_aging_demo\v5\doctor\v5_doctor_report.json
```

验收标准：

- Python / 项目结构 / v5 目录应为 PASS；
- Rscript、Docker、Nextflow 缺失时可以是 WARN，但报告必须给出原因；
- 不应出现关键配置文件缺失导致的不可恢复 FAIL。

## 7. 一键本地 v5 流程验收

示例问题：

```text
肌少症患者肌肉背景细胞中是否存在有特征性表面分子的 SASP 评分高细胞？
```

运行：

```powershell
python tc_lite.py v5-run-local --project vascular_aging_demo --question "肌少症患者肌肉背景细胞中是否存在有特征性表面分子的 SASP 评分高细胞？" --limit 2 --max-analysis-packets 2
```

预期会生成或刷新：

```text
projects\vascular_aging_demo\v5\project_state.json
projects\vascular_aging_demo\v5\events.jsonl
projects\vascular_aging_demo\v5\handoffs\
projects\vascular_aging_demo\v5\objects\
projects\vascular_aging_demo\v5\artifact_registry.jsonl
projects\vascular_aging_demo\v5\reports\canonical_report_manifest.json
```

验收重点：

- Agent handoff 链完整；
- resource discovery 有候选资源；
- metadata 不足时进入人工 review，而不是伪造 verified；
- TaskPacket 有 expected inputs / expected outputs / QC requirements；
- Artifact Registry 记录 checksum、producer、QC status；
- Report Manifest 引用 EvidencePlan、ArtifactManifest、QCReport、QuestionAlignmentReport。

## 8. 真实 LLM 与资源检索验收

当前项目已完成 10 个真实研究问题的在线验收，结果位置：

```text
projects\vascular_aging_demo\v5\validation\real_question_e2e_10\e2e10_summary.html
projects\vascular_aging_demo\v5\validation\real_question_e2e_10\e2e10_summary.json
projects\vascular_aging_demo\v5\validation\real_question_e2e_10\e2e10_summary.md
```

这次验收覆盖：

- 10 个不同真实研究方向；
- 每个方向 7 个 canonical agent；
- 真实 DeepSeek LLM 调用；
- GEO / PubMed / Europe PMC resource discovery；
- resource gate report；
- LLM fallback 统计；
- 候选资源数量统计。

当前记录显示：

- 10/10 问题完成；
- LLM 异常 0；
- resource discovery 异常 0；
- 总候选资源 36；
- 严格锁库结果为 0，这是预期行为，因为缺少足够 metadata 时不能自动进入 DATASETS_LOCKED。

## 9. 测试命令

快速回归：

```powershell
python tc_lite.py test-suite --suite quick
```

完整单测：

```powershell
python tc_lite.py test-suite --suite full
```

端到端验收：

```powershell
python tc_lite.py test-suite --suite e2e
```

打包验收：

```powershell
python scripts\v5_package_acceptance.py --suite quick --timeout 180
```

历史结果：

- quick：已 PASS；
- full：已 PASS，50/50；
- e2e：已 PASS，3/3；
- package acceptance：已 PASS。

## 10. GPT 或另一位 Codex 的推荐验收步骤

1. 解压教授演示包。
2. 运行 `python tc_lite.py v5-doctor --project vascular_aging_demo`。
3. 启动 UI：`python tc_lite.py serve --project vascular_aging_demo --port 8801`。
4. 打开 `/v5/flow`，确认 agent、handoff、task、artifact、QC、claim ceiling、human gate 可见。
5. 打开 `/v5/storage`，确认页面不再全量扫描 2.3 万文件导致长时间卡顿。
6. 打开 `/v5/services`，确认服务启动、停止、重启、端口恢复说明可见。
7. 运行一次 `v5-run-local` 示例问题。
8. 查看 `real_question_e2e_10\e2e10_summary.html`。
9. 运行 quick/full/e2e 测试。
10. 检查报告是否没有把 placeholder、未审核 evidence、failed QC 伪装成最终科学结论。

## 11. 当前不要误解的地方

不要把当前版本说成：

- 已完成商业 SaaS；
- 已完成多用户云平台；
- 已完成零环境 `.exe` 安装；
- 已完成所有真实数据库的生产级稳定检索；
- 已能对任意问题自动给出可直接发表的靶点结论；
- 已能绕过人工 metadata 纠错直接锁定所有数据集；
- 已能替代 wet-lab 专家审批。

可以准确介绍为：

> TargetCompass v5 当前是一个本地可运行的生信 Agent 证据链平台原型。它已经把用户问题、LLM Agent、资源发现、任务包、QC、Artifact、Evidence、Report 和人工 gate 串成了可追溯流程，并通过了本地测试和 50 个真实问题的在线 LLM/resource discovery 验收。当前已有正式 `.exe` 安装器和本机隔离目录安装/卸载 smoke；下一阶段重点是真正干净 Windows 机器或虚拟机验收、真实 Nextflow 大规模分析验收、wet-lab protocol 签出和后续产品化。

## 12. 常见问题

### UI 打不开

检查服务是否启动：

```powershell
python tc_lite.py serve --project vascular_aging_demo --host 127.0.0.1 --port 8801
```

如果端口冲突，换端口：

```powershell
python tc_lite.py serve --project vascular_aging_demo --host 127.0.0.1 --port 8810
```

### DeepSeek / LLM 跑不通

运行：

```powershell
python tc_lite.py v5-doctor --project vascular_aging_demo
```

检查 secrets/config 是否存在。不要把 API key 写进文档或公开提交。

### Docker / MinIO / PostgreSQL 显示 WARN

这是允许的。当前开发验收版可回退到 SQLite/local filesystem。若要验收 PostgreSQL/MinIO 主路径，需要先启动 Docker daemon 和对应服务。

### 资源发现找到了候选，但没有进入分析

这是严格 gate 的结果。只有 metadata 足够、organism/tissue/group/sample size/platform 等信息满足要求，才应进入 DATASETS_LOCKED 和后续分析。否则应进入人工 review / metadata 补正。

### 报告没有直接给最终靶点

如果证据不足，报告应显示候选、证据链、失败项、限制和下一步建议，而不是强行给确定结论。这是 v5 相比普通 prompt chaining 的核心差异。

## 13. 下一步开发清单

交付前 P0：

- 已完成：编译正式 `TargetCompassV5_Setup.exe`；
- 已完成：本机隔离目录 silent 安装/卸载烟测；
- 已完成：强化 metadata 人工纠错 UI，显示缺失字段并支持 lockable 后进入 analysis route；
- 已完成：固化开发版交付包和验收报告版本号；
- 已完成：50 问题真实在线验收，命令为 `python tc_lite.py v5-real-question-validation --project vascular_aging_demo --question-count 50 --output-name real_question_e2e_50 --timeout-seconds-per-question 120 --max-retries 1`；
- 未完成：真正干净 Windows 机器或虚拟机安装、启动、停止、重启、卸载验收。

平台化 P1：

- 已完成：项目创建、复制 demo、导入导出、归档删除；
- 已完成：用户、成员、角色、token 生命周期 UI；
- 已完成：服务管理后台，包括端口冲突恢复、日志入口和 backend 激活入口；
- 已完成：Artifact / Evidence / Claim drill-down 页面产品化；
- 已完成：P1 readiness manifest 和 `/v5/platform-readiness` 页面；
- 仍需继续：PostgreSQL / MinIO 主路径可用，但历史 `results/` 仍有大量 legacy local writer 产物未全部迁移，当前状态是 `LEGACY_WRITER_REMAINING`。

生产 P2：

- Windows GUI 安装器签名；
- 离线依赖缓存；
- Docker / Nextflow / R 自动诊断修复；
- 已完成：全量 50 问题真实 E2E；
- wet-lab protocol 审批签出流程。
## 14. P2 平台化 readiness

本次新增 P2 readiness，用于集中验收以下五块：

- 多用户权限：本地用户、成员、角色、token 生命周期和审计已经可见；生产级 OIDC/Vault 仍是后续项。
- PostgreSQL / MinIO 主路径：系统会优先读取 active backend 状态，并展示 legacy writer 剩余量；只有 `PRIMARY_READY` 才能称为完全主路径。
- 真实 Nextflow 大规模分析：Nextflow profile、TaskRun、QCReport、Artifact Registry 记录链已具备；大规模真实矩阵验收未记录时保持 REVIEW。
- 长期 memory：memory palace 已版本化、可审计、可回滚；它只作为 agent 上下文，不替代 Evidence DB。
- wet-lab protocol：能生成验证草案并记录签出；没有 PI/reviewer 签出时不能当作可执行实验 SOP。

生成命令：

```powershell
python tc_lite.py v5-platform-p2-readiness --project vascular_aging_demo
```

输出文件：

```text
projects\vascular_aging_demo\v5\platform\p2_readiness.json
```

UI 页面：

```text
http://127.0.0.1:8801/v5/platform-p2-readiness
```

验收时请重点看 `production_blockers`。如果里面仍提示 legacy writer、Nextflow large-scale validation 或 wet-lab signoff，说明这块是“控制面具备、生产验收未完成”，不能对外说成完全平台化完成。

### 50 问题真实在线验收

输出目录：

```text
projects\vascular_aging_demo\v5\validation\real_question_e2e_50
```

核心文件：

```text
summary.html
summary.json
summary.md
validation_progress.json
```

当前结果：

```text
status: PASS
question_count: 50
llm_failures: 0
resource_failures: 0
resource_candidates: 156
verified_candidates: 156
lockable_datasets: 0
manual_review_items: 150
llm_fallbacks: 0
duration_seconds: 189.192
```

说明：`lockable_datasets = 0` 不是失败。当前 resource gate 策略是严格模式，metadata / 分组 / 样本量不足时必须进入人工 review，不允许自动伪造 DATASETS_LOCKED。

### P2 demo slim storage

教授演示版不再要求迁移整个开发工作区的历史 `results/`。当前策略是只迁移本次展示和报告链条实际需要的有效产物，并把历史调试、批量验证、全基因图像等文件记录为 excluded historical outputs。

生成命令：

```powershell
python tc_lite.py v5-storage-migration --project vascular_aging_demo --action demo-slim --limit 5000
```

输出文件：

```text
projects\vascular_aging_demo\v5\platform\demo_slim_storage_manifest.json
```

当前 demo 结果：

```text
effective_artifact_count: 208
effective_registered_count: 208
effective_missing_count: 0
excluded_historical_legacy_count: 23504
status: PASS
```

这表示教授演示版的有效产物已经完成 ArtifactStore 注册；完整开发工作区仍可用全量命令继续迁移：

```powershell
python tc_lite.py v5-storage-migration --project vascular_aging_demo --action migrate --limit 5000
```

### P2 full legacy storage migration

当前开发工作区的 legacy analysis/report writer 产物也已批量迁移到 ArtifactStore / MinIO 主路径，EvidenceRepository 已同步到 PostgreSQL 主路径。

验收命令：

```powershell
python tc_lite.py v5-storage-migration --project vascular_aging_demo --action plan
python tc_lite.py v5-platform-p2-readiness --project vascular_aging_demo
```

当前结果：

```text
candidate_file_count: 23608
artifact_store_registered_count: 23608
artifact_store_missing_count: 0
migration_progress.percent_complete: 100.0
storage status: PRIMARY_READY
sqlite fallback: retained as P3 local backup
```

这表示 `LEGACY_WRITER_REMAINING` 已从 P2 storage blocker 中移除。P2 当前剩余 blocker 主要是：

- Nextflow large-scale matrix validation 尚未记录；
- wet-lab protocol 草案尚未 PI/reviewer 签出。
