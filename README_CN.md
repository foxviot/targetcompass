# TargetCompass Lite 中文说明

TargetCompass Lite 是一个本地优先的疾病相关分子发现与候选靶点筛选 MVP。它把用户输入的研究问题转成结构化 `ResearchSpec`，生成候选研究点子，自动查证数据集，执行 bulk RNA / microarray 分析，整合证据，生成候选排序和科研报告，并保留人工审核与审批记录。

当前默认项目是 `vascular_aging_demo`，主题为血管衰老、内皮衰老和动脉粥样硬化相关靶点探索。

## 一键启动

在交付目录双击：

```text
START_TARGETCOMPASS.bat
```

启动器会检查环境、启动 Web 应用，并打开：

```text
http://127.0.0.1:8781/
```

如果端口已占用或服务已运行，启动器会尽量复用现有服务并打开浏览器。

手动启动：

```powershell
python scripts\check_install.py
python tc_lite.py serve --project vascular_aging_demo --port 8781
```

## Web 界面主要入口

- 研究请求：输入研究方向，选择 GPT 或本地规则生成。
- 六步 Agent 工作流：生成、初审、查证、执行、复审、报告。
- 数据集选择：选择本次运行的数据集。
- 运行状态：查看阶段状态、失败原因、日志、局部重算和重跑。
- GEO / GSE recovery center：查看真实数据导入失败原因，并直接重试、手动分组或补平台注释。
- 方法配置：选择或上传 Markdown skill / agent 方法。
- 审批与人工审核：通过、复核、驳回候选并记录理由。
- 报告入口：打开 HTML 报告。
- 夜间模式：右上角切换日间/夜间模式。

## GPT / API Key

可以在 Web 页面中保存 OpenAI API Key，也可以在启动前设置环境变量：

```powershell
$env:OPENAI_API_KEY="你的_API_Key"
```

没有 API Key 时，系统会使用本地确定性规则继续运行，并在 `results/agent_trace.json` 中记录 fallback 信息。

## 六步 Agent 流程

```text
生成 -> 初审 -> 查证 -> 执行 -> 复审 -> 报告
```

- 生成：把研究问题转成 `ResearchSpec`，并生成候选点子。
- 初审：检查研究方向、可行性和审查门控。
- 查证：匹配数据集、GEO/GSE 状态、知识库和分析计划。
- 执行：运行 DEG、富集、注释、证据导入和候选评分。
- 复审：生成实验建议，汇总人工审核和剩余风险。
- 报告：生成 HTML、Word 兼容报告和结构化 JSON。

每次运行都会写入：

```text
projects/<项目名>/results/agent_trace.json
projects/<项目名>/results/run_status.json
```

## Markdown Skill / Agent 方法

Web 页面支持拖入 `.md` 方法文件，注册为可替换方法。当前支持三个阶段：

- 生成 / Query
- 初审复核 / Audit
- 实验设计 / Experiment

上传后的文件保存在：

```text
projects/<项目名>/agent_methods/
```

当前实现采用安全包装策略：Markdown 方法会作为方法说明和审查 prompt 附加到稳定的内置方法上，不会因为一个错误的 Markdown 文件直接破坏分析流程。

## GEO / GSE 真实数据接入

系统支持自动下载 GEO series matrix，自动解析 metadata，推断 case/control 分组，生成表达矩阵和 DatasetCard。

自动导入示例：

```powershell
python tc_lite.py geo-import-auto --project vascular_aging_demo `
  --accession GSE43292 `
  --tissue artery `
  --organism human `
  --platform-annotation projects\vascular_aging_demo\data\GSE43292\GPL6244.annot.gz `
  --case-hint atheroma `
  --control-hint macroscopically `
  --case-label atheroma `
  --control-label control `
  --min-confidence 35
```

导入后会生成：

```text
projects/<项目名>/data/<GSE>/expression_matrix.tsv
projects/<项目名>/data/<GSE>/metadata.tsv
projects/<项目名>/dataset_cards/<GSE>.yaml
projects/<项目名>/data/<GSE>/geo_import_status.json
```

失败时，Web 的 `GEO / GSE recovery center` 会显示：

- 下载失败原因和强制重新下载入口
- 分组识别失败原因和手动分组入口
- 平台注释缺失原因和补充注释入口
- 样本量不足提示和替代建议

## 分析模块

已实现：

- `bulk_deg_v1`：bulk RNA / microarray 差异表达分析。
- `enrichment_v1`：基于 DEG 和 gene set 的富集分析。
- `accessibility_annotation_v1`：靶点可及性注释。
- `safety_annotation_v1`：安全性标记和 UNKNOWN 人工复核。
- `evidence_import_v1`：把 DEG、富集、注释和外部 adapter 证据写入 SQLite。
- `candidate_scoring_v1`：候选排序和硬门控。

保留接口：

- `scrna_pseudobulk_v0`
- `genetic_coloc_mr_v0`

DEG 现在会处理真实数据中的常见问题：

- batch 与 group 混杂时，不会直接崩溃；会降级为无 batch 的关联筛选，并在 QC/manifest 中记录警告。
- R limma 失败时，会自动降级到 Python fallback，并记录原因。

## 数据库 Adapter

支持注册外部数据库和知识库：

- `tabular_evidence_v0`
- `sqlite_evidence_v0`
- `uniprot_target_v0`
- `hpa_safety_accessibility_v0`
- `opentargets_evidence_v0`
- `disgenet_evidence_v0`
- `gwas_catalog_evidence_v0`
- `msigdb_gene_sets_v0`
- `reactome_gene_sets_v0`

示例：

```powershell
python tc_lite.py knowledge-add --project vascular_aging_demo `
  --id uniprot_demo `
  --type external_database `
  --path examples\databases\sample_uniprot_targets.tsv `
  --adapter uniprot_target_v0

python tc_lite.py knowledge-adapt --project vascular_aging_demo
python tc_lite.py adapter-audit --project vascular_aging_demo
```

## 审批与人工审核

支持：

- 审批理由强制填写
- 通过 / 复核 / 驳回
- 审批历史
- 审批版本记录
- 审批前后差异
- 报告引用
- 最终签出 / 驳回

相关文件：

```text
projects/<项目名>/results/review_actions.tsv
projects/<项目名>/results/review_actions.jsonl
projects/<项目名>/results/review_versions/
projects/<项目名>/results/approval_state.json
```

## 报告输出

报告包括：

- 执行摘要
- 研究问题与边界
- 方法与模块
- 数据来源与 QC
- 候选排序
- 证据链
- 限制与风险
- 实验建议
- 审批与审计

输出路径：

```text
projects/<项目名>/reports/target_report.html
projects/<项目名>/reports/target_report.docx
projects/<项目名>/reports/target_report_structured.json
```

## 交付包

导出运行包：

```powershell
python tc_lite.py export-package --project vascular_aging_demo
python scripts\export_project_package.py
```

输出位置：

```text
projects/vascular_aging_demo/exports/
dist/
```

## 验证结果

当前 MVP 已完成：

- 单元测试：`105 tests OK`
- D 盘 smoke：通过
- 真实联网 GEO 数据测试：从 `GSE43292` 下载真实数据
- 真实数据 100 轮压力测试：`100/100` 通过

最终 100 轮摘要：

```text
projects/real100final_summary_1781942796.json
```

## 常用验证命令

```powershell
python -m unittest discover -s tests -p "test*.py" -v
python scripts\smoke_test.py
```

注意：完整测试和 smoke 不建议并行运行，避免 SQLite 或临时输出互相影响。

## 使用边界

TargetCompass Lite 是科研探索和教学演示工具。报告中的候选靶点和证据链属于关联级证据，不构成医学诊断、临床决策、用药建议或因果结论。任何候选靶点都必须经过人工审查、实验验证和合规审批后，才能进入真实研究决策。

## v4.0 后续方向

MVP 当前是固定六步 Agent。v4.0 建议扩展为：

- LLM 研究任务拆解
- Evidence DAG
- Codex task packet
- Codex / 沙盒执行器
- LLM 结果审核
- 数据库和文献自动搜索
- DAG 级证据聚合和人工审批
