# TargetCompass v4.0 本地版开发交接说明

## 项目定位

TargetCompass v4.0 本地版是一个面向生信研究问题的 Agent 证据链自动化平台。当前目标不是单一分析脚本，而是把用户研究问题拆解为可追溯的小任务，经过数据集选择、方法匹配、执行、QC、证据入库、评分、审批和报告生成，形成可复核的科研产物。

## 代码入口

- CLI 入口：`tc_lite.py`
- 主代码包：`targetcompass_lite/`
- Web UI：`targetcompass_lite/webapp.py`
- 测试目录：`tests/`
- Schema 目录：`schemas/`
- 打包脚本：`scripts/`
- Windows 安装脚本：`packaging/windows/`
- 主要 demo 项目：`projects/vascular_aging_demo/`
- 肌少症真实流程 demo：`projects/sarcopenia_muscle_sasp_demo/`

## 当前已具备的核心能力

- EvidencePlan / DatasetProfile / DatasetFeasibilityReport
- MethodContract 与 CompatibilityDecision
- AnalysisPlan evidence-driven route
- WorkOrder DAG
- Task Registry
- CodexTaskPacket queue
- 四层 QC：Execution / Data / Statistical / Biological
- QC 到 EvidenceItem 的门控逻辑
- Evidence DB、trace 查询、snapshot、报告引用链
- Candidate scoring 与 report/signoff
- DeepSeek/OpenAI-compatible LLM gateway
- typed agent role schema 与 role execution dispatch
- local executor / Nextflow runner contract
- MCP Gateway contract 与审计
- Windows 本地安装包构建流程

## 这个开发包包含什么

本包用于继续开发，不是最终用户安装包。它包含源码、测试、文档、schema、打包脚本、demo 配置和必要的轻量结果索引。

本包不包含：

- `.git/`
- 旧安装包和历史 bundle
- GEO raw/raw_extracted 大数据
- 大量 forest plot 批量图
- Nextflow 临时目录
- Codex 临时 worktree/workspace
- LLM 大批量临时运行记录
- API key 或任何私密密钥

## 接手后建议先执行

```powershell
python -m unittest discover tests -v
python tc_lite.py --help
```

如本机没有 Python/R/Nextflow，本包仍可阅读和开发源码，但真实执行分析需要补齐对应环境。面向小白电脑的一键运行应使用 Windows installer 包，而不是这个开发交接包。

## LLM 配置

不要把 API key 写入代码或提交到仓库。开发时使用环境变量或本地 secrets 文件。DeepSeek/OpenAI-compatible 层由 `targetcompass_lite/llm_gateway.py` 与相关 role execution 模块调用。

## 继续开发优先级

1. 稳定本地 v4.0 全流程：研究问题输入、Agent 拆解、Dataset Scout、WorkOrder DAG、执行、QC、Evidence、评分、审批、报告。
2. 继续把 CodexTaskPacket 做成真实可领取、执行、测试、写回、审批合并的工程闭环。
3. 强化真实数据适配：GEO/GSE、snRNA/scRNA metadata、基因 ID 映射、SASP/cell-type/surface marker 证据。
4. 补强报告质量：证据等级、方法、数据来源、QC、限制、实验建议、可追溯引用。
5. 做正式应用化：安装器、卸载器、启动器、本地服务健康检查、无语言环境运行。
6. 后续再推进 PostgreSQL/MinIO、服务拆分、生产级 MCP、多用户权限、观测系统。

## 注意事项

- 不要把 `dist/`、raw 数据、Nextflow 临时文件、批量图和 Codex 临时 workspace 加回 Git。
- 不要直接重写或删除用户已有的 demo 项目结果，除非明确是清理任务。
- 修改分析流程时优先保持 schema、EvidenceItem、Task Registry 和报告引用链稳定。
- 如果替换分析方法，应该新增 MethodContract 或 Agent method，而不是把旧方法硬删。
- 任何真实 LLM/网络测试都应保存审计记录，但不能泄露密钥。
