# TargetCompass Discovery v4.0 开发流程与优先级建议报告

## 当前工程主线

v4.0 不再按 MVP 的单次 demo 思路推进，而是按可审计科研自动化平台推进：

1. 用户输入研究问题
2. LLM 结构化理解为 ResearchSpec
3. Planner 拆解研究任务
4. WorkOrder DAG 固化任务、输入、输出、状态
5. Local Executor/Codex Executor 执行任务并写出 artifact
6. Evidence DB 导入证据并保留 artifact/run/module lineage
7. Evidence index 连接 EvidenceItem、ReviewItem、ReportRef
8. 人工/Agent 复核并记录理由、版本、diff、签出状态
9. 报告生成并引用固定 evidence index
10. MCP Gateway 向外部 agent 暴露资源和查询工具
11. 导出完整交付包

## 已完成的关键闭环

- WorkOrder attempt 级状态记录
- WorkOrder DAG
- Codex task packet 与工程结果复核
- Evidence DB 基础导入
- EvidenceItem -> ReviewItem -> ReportRef 统一索引
- Causal evidence grade 与人工复核
- MCP resource/tool contract
- MCP stdio server
- 报告生成时刷新 evidence trace index
- signoff 绑定 traceability snapshot hash
- 导出包包含 v4 trace 文件
- UI 展示 Evidence -> Review -> Report index，并支持 gene 查询

## P0：平台可信闭环

目标：任何报告结论都能追溯到证据、执行、复核和版本。

已完成：

- 清理 demo 运行产物污染
- 统一 traceability refresh orchestrator
- Evidence index 接入 report、MCP、UI、export package
- final signoff 冻结 evidence index、WorkOrder DAG、traceability refresh hash

仍建议补强：

- 把 run/package/export 操作也写入审计日志
- 给 signoff 后的关键 artifact 增加只读/冻结策略
- 增加一键检查：报告、Evidence index、DAG、review queue 是否一致

## P1：可操作证据链与复核体验

目标：教授或外部 agent 能快速理解每个候选为什么出现、卡在哪里、谁审核过。

已完成：

- Evidence trace query MCP tool
- UI gene 查询 Evidence trace
- DAG evidence_writes 携带 review/report refs
- causal grade review queue

建议开发：

- Evidence trace 独立详情页
- 按 gene、dataset、evidence_type、review_status、artifact 过滤
- 报告内每个候选增加 Evidence trace 摘要块
- 复核界面展示 before/after diff，而不是只存 JSON

## P2：方法与 Agent 可替换

目标：不同教授、课题、数据库、审查标准可以替换方法，而不是改核心代码。

已完成：

- 方法 registry 初步支持 query/audit/experiment
- Markdown skill/agent method 可上传
- Causal review flags 已结构化输出

建议开发：

- Causal review rubric 配置化
- Evidence scoring rubric 版本化与签名
- Query/Planner/Reviewer/Report Writer 分角色 agent contract
- MCP tool contract 与 method registry 对齐
- 自动记录每个 Agent 输入、输出、模型、参数、人工覆盖

## P3：分析能力扩展

目标：从 bulk DEG 平台扩展到更完整靶点发现平台。

已完成：

- bulk RNA/microarray DEG
- enrichment v2
- meta-analysis lightweight
- scRNA pseudobulk interface
- genetic coloc/MR 最小 runner
- 标准数据库 adapter 框架

建议开发顺序：

1. scRNA pseudobulk 稳定化：Seurat/Scanpy 输入、donor-aware QC、cell type contrasts
2. enrichment 升级：MSigDB/Reactome 版本快照、背景基因集、ORA/GSEA 区分
3. meta-analysis 升级：fixed/random effects、异质性、方向一致性
4. GWAS/QTL/coloc/MR 正式化：接入标准 summary schema、LD reference、coloc/MR package runner
5. Causal Evidence 分级升级：根据 GWAS/QTL/coloc/MR 输出结构做正式等级
6. Nextflow 预留：先保持 executor contract，不急着大迁移

## 建议的下一阶段开发顺序

1. P1：Evidence trace 详情页与报告候选级 trace 摘要
2. P1：signoff 后冻结检查和一致性校验
3. P2：Causal review rubric 配置化
4. P2：多 Agent contract 标准化
5. P3：scRNA pseudobulk 稳定化
6. P3：正式 GWAS/QTL/coloc/MR runner

## 对教授的介绍建议

可以这样概括：

TargetCompass Discovery v4.0 的核心不是简单生成报告，而是建立一个可审计的疾病相关分子发现工作流。系统把研究问题结构化成 ResearchSpec，再拆成 WorkOrder DAG；每个执行结果进入 Evidence DB，证据与人工复核、报告引用通过 Evidence trace index 绑定。最终报告不是孤立文本，而是可以追溯到数据、脚本、artifact、审查理由和版本 hash 的科研产物。

当前版本已经具备本地 v4 骨架：真实数据接入、任务状态、证据库、复核队列、报告、MCP Gateway 和导出包。下一阶段重点是提升分析深度和方法可替换性，包括 scRNA pseudobulk、正式遗传因果分析、多 Agent contract 和配置化审查标准。
