# TargetCompass v4 本地测试分层

不要再把 `python -m unittest discover tests -v` 当成默认验收入口。全量 discover 会把快速单元测试、真实 demo 工作流、Nextflow/服务/本地后端相关测试混在一起，失败时很难判断是代码失败、环境未启动，还是长任务超时。

## Quick

用途：日常开发后快速验收，要求不依赖真实网络、DeepSeek、Docker、Nextflow 运行时。

```powershell
python tc_lite.py test-suite --suite quick
```

输出：

```text
results/test_suites/quick_test_suite_report.json
```

## Full

用途：本地完整集成验收，包含服务 contract、MCP gateway、Nextflow contract、报告、Evidence、Orchestrator、Codex 工程闭环等测试，但仍避免真实外部 API 和真实长任务。

```powershell
python tc_lite.py test-suite --suite full
```

输出：

```text
results/test_suites/full_test_suite_report.json
```

## E2E

用途：交付前端到端验收。当前包含真实 demo workflow 和真实项目数据检查；后续可加入 DeepSeek 真调用、Nextflow WSL 真跑、本地 PostgreSQL/MinIO Docker 真后端。

```powershell
python tc_lite.py test-suite --suite e2e
```

输出：

```text
results/test_suites/e2e_test_suite_report.json
```

## 查看清单

```powershell
python tc_lite.py test-suite --list
```

## 超时策略

每个测试模块独立进程执行，并有单模块超时。这样某个模块卡住时，报告会明确显示具体模块和原因，而不是整个 `discover` 无声超时。

默认超时：

- quick：总 45 秒，单模块 20 秒
- full：总 240 秒，单模块 45 秒
- e2e：总 900 秒，单模块 420 秒

可以临时覆盖：

```powershell
python tc_lite.py test-suite --suite full --timeout-seconds 600
```
