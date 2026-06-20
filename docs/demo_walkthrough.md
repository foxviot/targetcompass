# TargetCompass Lite 小白试用流程

## 1. 一键启动

进入项目目录：

```text
D:\targetcompass-lite
```

双击：

```text
START_TARGETCOMPASS.bat
```

脚本会自动检查 Python/R 依赖，启动 Web 服务，并打开：

```text
http://127.0.0.1:8781/
```

如果页面已经在运行，脚本只会重新打开浏览器。

## 2. 手动启动

如果双击启动被系统拦截，可以打开 PowerShell 后运行：

```powershell
cd D:\targetcompass-lite
powershell -ExecutionPolicy Bypass -File scripts\start_app_one_click.ps1
```

## 3. 填写 API Key

在页面右侧的 `API Key` 面板输入 OpenAI API Key，然后点击保存。Key 会保存在本机项目目录的本地密钥文件中，不会进入报告或交付压缩包。

没有 API Key 时也能跑通 demo，系统会自动使用本地 fallback 方法。

## 4. 运行一次 Agent

在 `Agent 研究请求` 输入研究方向，例如：

```text
Find secreted targets for human endothelial senescence in vascular aging
```

设置想生成的点子数量，选择数据集，然后点击运行 Agent。

建议首次运行只选择可直接分析的数据集：

```text
ds_fixture_vascular_aging
GSE312006
GSE43292
```

## 5. 预置 GSE 数据说明

当前项目预置两类 GEO/GSE 卡片：

- `GSE312006`、`GSE43292`：带示例矩阵，可进入 MVP 的 bulk DEG 流程。
- `GSE40279`、`GSE87571`、`GSE113957`：参考数据库卡片，用于老化/衰老背景审查；暂未内置矩阵，默认不会进入 bulk DEG 分析。

后续如果要让参考 GSE 进入分析，需要通过数据库/知识库注册功能补充兼容的表达矩阵和分组信息。

## 6. 人工审核

运行后在页面查看点子可行性，逐条点击通过、复核或驳回。审核记录会保存到：

```text
projects\vascular_aging_demo\results\review_actions.tsv
```

## 7. 导出交付包

页面中点击导出，或在 PowerShell 运行：

```powershell
python tc_lite.py export-package --project vascular_aging_demo
```

运行包会写入：

```text
projects\vascular_aging_demo\exports\
```
