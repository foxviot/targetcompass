# TargetCompass v4.0 开发清单

本文根据 `TargetCompass-Discovery ... v4.0` 技术书拆分开发任务。MVP 已经具备本地 Agent、GEO/GSE 自动导入、bulk DEG、审批记录、报告和一键启动；v4.0 的重点是把这些能力升级为后台研究引擎。

## P0 交付基线

- 保留 MVP 六步流程：生成 -> 初审 -> 查证 -> 执行 -> 复审 -> 报告。
- 保留本地可运行 demo、中文 UI、报告、真实 GEO 示例和一键启动。
- 所有 v4 改造先以兼容层落地，不破坏现有 `agent-run`、`demo`、`serve`。

## P1 核心对象与权威状态机

- 增加 v4 对象模型：Project、DiseaseSpec、ResearchSpec、AnalysisPlan、ReviewDecision、WorkOrder、WorkflowRun、Artifact、EvidenceSnapshot、CodexJob。
- 生成 `v4/state_machine.json`，明确可恢复状态、终态、失败态和人工门控。
- 为每个项目生成 `v4/object_manifest.json`，保存对象 ID、hash、路径、版本和 lineage。
- ResearchSpec/AnalysisPlan 审批后计算 canonical JSON SHA-256，防止静默修改。

## P2 WorkOrder / Codex Task Packet

- 将 `analysis_plan.json` 编译成结构化 WorkOrder。
- WorkOrder 分三类：`RUN_REGISTERED_MODULE`、`BUILD_ADAPTER`、`FIX_CODE`。
- 标准模块直接进入本地执行兼容层；缺失 adapter 或可复现代码故障才生成 Codex task packet。
- Codex task packet 必须包含 baseline commit、allowed paths、fixture、tests、expected outputs，不包含正式私密研究数据。

## P3 MCP Gateway 兼容清单

- 生成 MCP Resource manifest：`project://`、`spec://`、`plan://`、`work-order://`、`evidence://`、`engineering://`。
- 所有 Resource 只引用对象 ID 和 artifact 路径，不把大矩阵直接塞进模型上下文。
- 记录 resource version 和 content hash，供 GPT/Codex 复现上下文。

## P4 Evidence DB 与报告可追溯性

- EvidenceItem 增加 run_id、artifact_id、review_status、quality、module_version 的强约束。
- 生成 EvidenceSnapshot，并让报告段落引用 evidence_id / score_id。
- 报告 Writer 不改写证据，只聚合 accepted/flagged evidence。

## P5 长任务与恢复

- 当前本地 `run_status.json` 升级为 Orchestrator 状态模型。
- 增加 run attempt、局部重算、取消、失败恢复建议和 artifact manifest。
- 后续接 Temporal 时保持相同对象契约。

## P6 Nextflow / 容器执行平面

- 为 bulk DEG、pseudobulk、enrichment、annotation 生成 Nextflow DSL2 module contract。
- 记录 pipeline tag、git commit、container digest、profile、resume manifest。
- MVP 本地 Python/R runner 作为 `local_executor` 兼容后端。

## P7 多 GPT 审批角色

- 拆分 Disease Normalizer、Dataset Scout、Planner、Method Reviewer、Result Reviewer、Causal Reviewer、Report Writer。
- 每个角色输出严格 JSON，经 schema、registry、policy 校验。
- 生成者不能审批自己；Scoring Engine 保持确定性。

## P8 v4.0 验收

- 同一 spec + data snapshot + rubric 评分完全可重复。
- 100% WorkOrder 有 hash、run_id、artifact、日志和审批记录。
- 100% accepted EvidenceItem 可追溯到数据、模块、参数、审批和报告引用。
- Codex 工程任务必须通过测试后才能发布为新 adapter/module。
