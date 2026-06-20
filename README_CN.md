# TargetCompass Lite 中文说明

TargetCompass Lite 是一个本地运行的疾病相关分子发现与疫苗候选靶点筛选 MVP。当前版本围绕 GPT/本地 Agent 六步流程工作：

```text
生成 -> 初审 -> 查证 -> 执行 -> 复审 -> 报告
```

系统可以把用户研究方向转成结构化 `ResearchSpec`，生成候选点子，自动查找或接入 GEO/GSE 数据，运行 bulk RNA/microarray 差异分析、富集、可及性和安全性注释，最后输出证据链、候选排序、实验建议和可审查报告。

## 一键启动

推荐把项目放在：

```text
D:\targetcompass-lite
```

双击：

```text
START_TARGETCOMPASS.bat
```

或在 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_app_one_click.ps1
```

启动后访问：

```text
http://127.0.0.1:8781/
```

## GPT API Key

页面中有本地 API Key 设置区。Key 会保存在本机项目配置中，不会进入导出的运行包。未填写 Key 时，系统仍可使用本地规则 fallback 跑通 demo，但 GPT 生成和结构化理解能力会降级。

## MVP 已实现能力

- GPT/本地规则生成 `ResearchSpec` 和候选点子。
- 六步 Agent 状态视图。
- 人工可替换 Markdown skill / agent method。
- 真实 GEO/GSE 自动下载、metadata 解析、自动分组和失败恢复提示。
- bulk RNA/microarray DEG，支持本地 Python fallback 和 R/limma。
- UniProt、HPA、Open Targets、DisGeNET、GWAS Catalog、MSigDB/Reactome 等 adapter 兼容层。
- 审批理由、审批记录、版本记录、差异记录、复核队列和最终签出。
- 运行状态、失败原因、取消、重跑和局部重算入口。
- 结构化科研报告：方法、数据来源、QC、候选排序、证据链、限制和实验建议。
- 中英切换、夜间模式和 iOS 风格界面。

## 常用命令

初始化项目：

```powershell
python tc_lite.py init --project vascular_aging_demo
```

运行 demo：

```powershell
python tc_lite.py demo --project vascular_aging_demo
```

运行 Agent：

```powershell
python tc_lite.py agent-run --project vascular_aging_demo --text "Find secreted targets for human endothelial senescence in vascular aging" --parser rule_based --dataset GSE43292
```

导出运行包：

```powershell
python tc_lite.py export-package --project vascular_aging_demo
```

## v4.0 当前开发入口

当前分支已开始按 v4.0 技术书补后台研究引擎骨架，且不破坏 MVP。新增核心文件：

```text
docs/v4_architecture_source.txt
docs/v4_development_backlog.md
targetcompass_lite/v4.py
tests/test_v4_manifest.py
```

新增命令：

```powershell
python tc_lite.py v4-manifest --project vascular_aging_demo
```

该命令会生成：

```text
projects/<项目名>/v4/state_machine.json
projects/<项目名>/v4/object_manifest.json
projects/<项目名>/v4/disease_spec.json
projects/<项目名>/v4/work_orders.json
projects/<项目名>/v4/work_orders/*.json
projects/<项目名>/v4/mcp_resources.json
projects/<项目名>/v4/evidence_snapshot.json
```

这些文件对应 v4.0 的关键方向：

- 权威状态机。
- DiseaseSpec / ResearchSpec / AnalysisPlan 对象 hash。
- WorkOrder 编译。
- `RUN_REGISTERED_MODULE` / `BUILD_ADAPTER` / `FIX_CODE` 三类任务。
- Codex task packet。
- MCP Resource manifest。
- Evidence snapshot。

后续 v4.0 要继续开发的重点见：

```text
docs/v4_development_backlog.md
```

## 主要输出

```text
research_spec.json
analysis_plan.json
work_orders/*.md
v4/*.json
results/agent_trace.json
results/run_status.json
results/bulk_deg_*/deg_results.tsv
results/bulk_deg_*/qc_summary.json
results/enrichment/enrichment_results.tsv
results/annotation/accessibility_annotation.tsv
results/annotation/safety_flags.tsv
evidence.sqlite
candidate_scores.csv
reports/target_report.html
reports/target_report.docx
reports/target_report_structured.json
exports/*.zip
```

## 验证

```powershell
python -m unittest discover -s tests -p "test*.py" -v
python scripts\smoke_test.py
```

当前交付验证记录：

- 完整测试：`105 tests OK`。
- smoke test：通过。
- 真实 GEO 示例：`GSE43292`，64 个样本，19033 个基因。
- 真实数据 100 轮压力测试：`100/100` 通过。

## 使用边界

TargetCompass Lite 是科研探索和教学演示工具。报告中的候选靶点和证据链属于关联级证据，不构成医学诊断、临床决策、用药建议或因果结论。任何候选靶点都必须经过人工审查、实验验证和合规审批后，才能进入真实研究决策。
