# TargetCompass v5 软件 Figure 说明

本文件用于给教授、甲方或另一个 Codex 解释当前 v5 本地版的软件框架和端到端流程。两张图都是 SVG 矢量图，可以直接放进 PPT、Word 或项目书。

## Figure 1：总体架构图

文件：

- `docs/figures/targetcompass_v5_architecture.svg`

讲解重点：

- 左侧是用户与 PilotDeck 操作台：输入研究问题、查看项目运行、做人工纠错和审批、导出报告。
- 中间是 v5 canonical 控制面：ProjectState、EventLog、ResearchSpec、EvidencePlan、TaskPacket、Artifact、QC、Claim ceiling 和 Human gate。
- 右侧是七个 Agent 的协作层：每个 Agent 只传对象引用、证据引用、产物引用和 handoff，不靠自由文本串联。
- 最右侧是执行与存储平面：本地 Python/R、Nextflow、Codex Worker、EvidenceRepository、ArtifactStore、审计和权限。
- 这张图适合回答“这个系统的架构是什么，为什么不是简单聊天机器人”。

推荐口径：

> TargetCompass v5 的核心不是让 LLM 直接给结论，而是把 LLM 生成、数据发现、方法选择、执行、QC、证据入库、人工审批和报告签出全部放进一个可追溯控制面。每个结论都必须能追溯到数据、任务、产物和审计记录。

## Figure 2：端到端流程图

文件：

- `docs/figures/targetcompass_v5_workflow.svg`

讲解重点：

- 从自然语言研究问题开始，先变成 ResearchSpec、SubQuestion 和 ScopeBundle。
- EvidencePlan 决定需要什么证据、能支持什么等级的 claim。
- Resource discovery 自动寻找 GEO/SRA/ArrayExpress/cellxgene/PubMed 等候选资源。
- 数据集必须经过 metadata、分组、样本量、物种、组织和平台的锁库门控；不满足就进入人工纠错，不继续伪分析。
- TaskPacket 再驱动 local / Nextflow / Codex Worker 执行。
- 执行结果进入四层 QC：Execution、Data、Statistical、Biological。
- 只有通过 QC 或进入人工复核的结果，才能写入 EvidenceRepository 和 ArtifactStore。
- Question Alignment Auditor 检查最终 claim 是否回答原始问题、是否跑题、是否超过证据等级。
- 人工审批后才形成正式报告。

推荐口径：

> v5 流程强调“数据反向约束方法”。系统不会先固定分析模块，而是根据研究问题和可用数据决定能做什么；如果 metadata 不够、QC 失败或 claim 超证据等级，流程会停下来要求人工纠错、复核或局部重跑。

## 当前图中哪些已经实现

- v5 canonical schemas、ProjectState、EventLog、Handoff、TaskPacket 已实现。
- Resource discovery 已有 GEO/SRA/ArrayExpress/cellxgene/PubMed 等 adapter 方向，并接入 verified gate。
- 数据集锁库和 metadata 人工纠错 UI 已有。
- AnalysisTaskPacket 到本地分析/部分 Nextflow/Codex Worker 的执行链已打通。
- Artifact Registry、QCReport、EvidenceRepository、ArtifactStore、Question Alignment、Report manifest 已有。
- PilotDeck 已能展示流程、任务、产物、QC、证据、权限、存储和平台状态。

## 当前图中仍需继续产品化的部分

- 真实数据库检索还需要更大样本稳定性测试和更强 metadata 自动解析。
- Nextflow 需要更多真实 bulk/scRNA/enrichment profile 验收。
- Codex Worker 需要更多真实工程任务大样本验证。
- PostgreSQL/MinIO 已可作为 active backend，但仍要继续压缩 legacy local writer。
- Windows GUI 安装器和干净机器安装/卸载验收还需要完成。
- 多用户权限、长期 memory、wet-lab protocol 签出还属于后续产品化模块。

## 推荐放入 PPT 的顺序

1. 先放 `targetcompass_v5_architecture.svg`：说明系统分层。
2. 再放 `targetcompass_v5_workflow.svg`：说明一次研究如何跑完。
3. 最后用当前 demo 页面截图展示：PilotDeck、数据集锁库、研究报告、证据详情。
