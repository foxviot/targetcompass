# TargetCompass v5 项目完整说明

## 项目定位

TargetCompass 是一个本地优先的生物医学靶点发现平台。它把一个自然语言研究问题，转换成可追溯的证据工作流：规范 Agent 交接、资源检索、数据集门控、任务包、分析执行合同、QC、Artifact 注册、Evidence 入库、Claim 对齐和报告交付。

当前仓库是 **v5 本地开发验收 / 演示交付版**。它适合本地验收、教授演示、GPT/Codex 复核和继续开发，但还不是完整的云端多租户生产服务。

## 它解决的问题

传统生信靶点发现很容易出现几个问题：问题拆解不清楚、数据集为什么选不清楚、脚本结果和报告结论断开、失败步骤被忽略、证据等级被夸大。TargetCompass 的核心目标是把这些环节结构化：

- 用户到底问了什么问题。
- 这个问题需要哪些证据轴。
- 哪些数据集被发现、拒绝、需要人工补正或锁定。
- 数据本身能支持哪些方法，不能支持哪些方法。
- 每个输出由哪个任务包产生。
- 每个输出通过了哪些 QC，哪些需要人工复核。
- 每个报告结论引用了哪些 EvidenceItem 和 Artifact。
- 哪些结论超过了当前证据等级，必须降级或复核。

这样平台就可以审计、重跑、纠错、交付和交给另一个 Codex/GPT 继续开发。

## v5 规范流程

v5 控制面使用 7 个规范 Agent。Agent 之间不传大段自然语言结论，只传结构化对象引用、Artifact 引用、Evidence 引用、假设、阻塞问题和 claim ceiling。

1. `question_normalizer`
   - 把用户原始问题转换成 `ResearchSpec` 和 `SubQuestion`。
   - 不推荐数据库，不生成结果，不声称问题已经被证明。

2. `scope_resolver`
   - 解析疾病、物种、组织、细胞类型、条件和结论边界。
   - 不把 human evidence、model organism evidence、组织证据和细胞类型证据混在一起。

3. `evidence_plan_builder`
   - 生成 EvidencePlan，说明回答问题需要哪些证据轴。
   - 定义表达、细胞类型、表面分子、分泌分子、SASP、富集、遗传和因果证据的最低要求。

4. `resource_discovery_agent`
   - 检索 GEO/SRA/ArrayExpress/cellxgene 类数据候选和 PubMed/Europe PMC 文献候选。
   - 生成 `ResourceCandidate`、`DatasetProfile`、`DatasetSelectionDecision`。
   - 只有 metadata、物种、组织、分组、样本量、平台和矩阵可解析性都满足要求时，才能进入锁库。

5. `method_adapter_workorder_compiler`
   - 根据 EvidencePlan 和已验证 DatasetProfile 生成 WorkflowPlan 和 TaskPacket。
   - 使用 CompatibilityDecision 做红线判断：数据反向约束方法，数据不支持的方法不能被派发。

6. `result_auditor`
   - 审核 TaskRun、ArtifactManifest 和 QCReport。
   - 写入 audit record 和 EvidenceItemRef。
   - 不能修改原始结果，不能补造缺失输出。

7. `evidence_synthesizer_reporter`
   - 只消费已审核 Evidence。
   - 在当前 claim ceiling 内生成结论和报告。
   - 必须展示限制、失败项和未解决问题。

## 核心对象

- `ResearchSpec`：规范化后的研究问题。
- `SubQuestion`：从主问题拆出的可回答小问题。
- `ScopeBundle`：疾病、物种、组织、细胞类型、条件和 claim 边界。
- `EvidencePlan`：证据轴和最低证据要求。
- `DatasetProfile`：数据集 metadata、可用性、限制和锁库状态。
- `MethodContract`：方法需要什么输入、产出什么输出、需要什么 QC。
- `CompatibilityDecision`：数据是否支持某个方法的结构化判断。
- `AnalysisTaskPacket`：分析任务包，包含输入、输出、QC 和失败条件。
- `EngineeringTaskPacket`：工程任务包，包含允许路径、禁止路径、patch 预期和测试命令。
- `ReviewTaskPacket`：审核任务包，包含审核范围和 claim ceiling。
- `TaskRun`：任务执行记录。
- `QCReport`：执行、数据、统计、生物学四层 QC。
- `ArtifactManifest`：文件或对象的 checksum、生产者、schema、状态、限制和证据引用。
- `EvidenceItem`：用于评分和报告的标准证据单元。
- `QuestionAlignmentReport`：检查结论是否回答原问题、是否跑题、是否超过证据等级。
- `CanonicalReportManifest`：报告包索引，引用 Evidence、Artifact、QC 和 Alignment 输出。

## 当前已具备能力

v5 本地版当前已经具备：

- canonical agent protocol、handoff 格式和 claim ceiling 校验。
- v5 mock runner，用于安全验证控制面闭环。
- GEO/SRA/ArrayExpress/cellxgene 风格资源发现 adapter 和 resource gate。
- PubMed / Europe PMC 文献检索方向。
- 数据集锁库和人工 metadata 补正 UI。
- matrix_parse_ready 前置判断，避免只看 metadata 就推荐不可解析 GEO。
- v5 TaskPacket、Worker 协议、审批、领取、完成和失败状态。
- local executor、Nextflow、Codex worker 的执行合同。
- Artifact Registry 和 ArtifactStore 抽象。
- EvidenceRepository 抽象，支持 SQLite fallback 和 PostgreSQL primary path。
- MinIO/S3 对象存储支持。
- 四层 QC：Execution、Data、Statistical、Biological。
- Question Alignment Auditor，可发现 unsupported claim、scope drift、claim ceiling violation、placeholder artifact、failed QC evidence。
- PilotDeck 本地 Web UI，支持中文、日文、英文切换。
- v5-doctor、自检页面、发布验收页面、存储后端页面、resource gate 页面和产品报告页面。
- Windows 打包脚本和 Inno Setup 安装器资源。

## 当前限制

这些内容不能被夸大为已经完全生产化：

- OIDC/Vault 和正式多用户登录会话不在当前本地交付范围内。
- PostgreSQL/MinIO 可以作为 active backend，但仍有少量 legacy analysis/report writer 会先写本地文件，再注册或同步。
- SRA/cellxgene 的真实大样本矩阵下载、量化、解析和分析路径还需要更多真实数据验证。
- Nextflow 和 Codex Worker 合同已存在，但生产级大样本验证还需要继续跑。
- Windows 安装器脚本已存在，但正式签名和干净机器安装验收记录还需要交付前补齐。
- wet-lab protocol 当前是可审计建议和签出界面，不是完整生产 SOP 生成系统。
- 文献可以用于验证和背景，但当问题需要组学证据时，不能只靠论文摘要或综述生成靶点结论。

## 快速启动

在仓库根目录执行：

```powershell
python tc_lite.py serve --project vascular_aging_demo --host 127.0.0.1 --port 8831
```

打开：

```text
http://127.0.0.1:8831/
```

运行 v5 自检：

```powershell
python tc_lite.py v5-doctor --project vascular_aging_demo
```

运行发布验收：

```powershell
python tc_lite.py v5-release-acceptance --project vascular_aging_demo --question-count 10
python tc_lite.py v5-release-acceptance --project vascular_aging_demo --question-count 50
```

运行矩阵路径验证：

```powershell
python tc_lite.py v5-matrix-path-validation --project vascular_aging_demo
```

推荐单测：

```powershell
python -m unittest tests.test_canonical_matrix_path_validation tests.test_canonical_storage_primary_gate tests.test_v5_doctor tests.test_release_acceptance -v
```

## 推荐验收流程

1. 启动本地 Web UI。
2. 运行 `v5-doctor`，记录 WARN/FAIL。
3. 跑 10 个真实问题在线验收。
4. 对多个独立研究问题运行 resource discovery。
5. 至少选一个数据集，通过人工 metadata 补正进入锁库。
6. 在进入分析前确认 matrix_parse_ready。
7. 在依赖可用时跑 local analysis 或 Nextflow。
8. 确认 TaskRun、QCReport、Artifact Registry、EvidenceRepository 和 report 输出能互相引用。
9. 打开 PilotDeck 页面，检查 Agent handoff、resource gate、storage backend、report、release acceptance 页面是否能看懂。
10. 对外交付前，在干净 Windows 机器或虚拟机上做安装、启动、停止、重启、卸载验收。

## 交付说明

- 开源协议：Apache License 2.0。
- 不应提交 runtime outputs、secrets、下载原始数据和本地缓存。
- 本仓库适合连同英文说明一起交给 GPT/Codex 验收或继续开发。

