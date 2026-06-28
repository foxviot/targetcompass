# TargetCompass v5 用户使用手册

## 1. 这是什么

TargetCompass v5 是一个本地运行的生信 Agent 平台原型。用户输入研究问题后，系统会按规范流程生成研究对象、发现候选资源、生成任务包、执行本地分析或进入人工复核，并输出可追溯报告。

当前交付版重点是本地演示和开发验收，不要求登录。

## 2. 安装

### 推荐方式：运行正式安装器

双击：

```text
TargetCompassV5_Setup.exe
```

默认安装位置：

```text
%LOCALAPPDATA%\TargetCompassV5
```

安装完成后，桌面或开始菜单会出现：

```text
TargetCompass V5
```

### 备用方式：使用 zip 安装包

解压 `TargetCompassV5_Windows_Installer_*.zip`，在 PowerShell 中运行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Install-TargetCompassV5.ps1 -LaunchAfterInstall
```

## 3. 启动

双击桌面图标 `TargetCompass V5`。

启动器会：

1. 启动本地 Python 服务；
2. 检查端口是否可用；
3. 等待服务 ready；
4. 自动拉起系统默认浏览器。

如果浏览器没有自动打开，可以手动访问启动日志里显示的地址，常见为：

```text
http://127.0.0.1:8801/
http://127.0.0.1:8831/
```

## 4. 首次检查

进入页面后，建议先看：

```text
/v5/release-acceptance
/v5/production-readiness
/v5/resource-gate
/v5/product-report
```

也可以在安装目录运行：

```powershell
python tc_lite.py v5-doctor --project vascular_aging_demo
```

如果 `v5-doctor` 显示 PASS 或 READY_WITH_WARNINGS，说明本地平台可用于演示。WARN 通常表示 Rscript、Docker、Nextflow 或外部数据库后端未完全准备，不一定影响 UI。

## 5. 语言切换

页面右上角有语言下拉：

```text
中文
日本語
English
```

切换后会停留在当前页面，不会跳回首页。

## 6. 典型演示流程

1. 打开首页。
2. 查看 v5 流程图和发布验收状态。
3. 打开数据集锁库页，查看候选数据集、metadata 缺失项和人工纠错入口。
4. 打开报告页，查看候选靶点、证据链、QC、限制和导出入口。
5. 打开生产就绪页，查看哪些能力已经 ready，哪些仍是 review。

## 7. 运行一个本地问题

在工程目录或安装目录的 app 子目录运行：

```powershell
python tc_lite.py v5-run-local --project vascular_aging_demo --question "肌少症患者肌肉背景细胞中是否存在有特征性表面分子的 SASP 评分高细胞？" --limit 2 --max-analysis-packets 2
```

运行后重点查看：

```text
projects\vascular_aging_demo\v5\project_state.json
projects\vascular_aging_demo\v5\events.jsonl
projects\vascular_aging_demo\v5\handoffs\
projects\vascular_aging_demo\v5\artifact_registry.jsonl
projects\vascular_aging_demo\v5\reports\canonical_report_manifest.json
```

## 8. 配置 LLM Key

当前版本支持 DeepSeek / OpenAI-compatible API。Key 只保存在本机项目配置中，不应写入交付包。

在 UI 中进入配置向导或首页 API Key 区域填写。也可以由开发人员通过项目配置文件设置。

注意：没有 Key 时，本地 fallback 可用于结构演示，但真实 LLM 角色运行会降级。

## 9. 停止、重启、卸载

安装版常用脚本：

```text
Stop-TargetCompassV5.ps1
Restart-TargetCompassV5.ps1
Repair-TargetCompassV5.ps1
Uninstall-TargetCompassV5.ps1
```

保留项目数据卸载：

```powershell
.\Uninstall-TargetCompassV5.ps1 -KeepProjects
```

## 10. 常见问题

### 浏览器显示无法连接

先等 10-30 秒。如果仍失败，检查日志：

```text
%LOCALAPPDATA%\TargetCompassV5\logs
```

重点文件：

```text
targetcompass-v5-launch.log
targetcompass-v5.out.log
targetcompass-v5.err.log
```

### Docker / Nextflow / Rscript 显示 WARN

这不一定影响 UI。它表示对应高级分析运行时未准备好。演示报告、流程视图、自检和部分本地分析仍可运行。

### 数据集不能进入真实分析

这是正常门控。metadata 不足、样本量不足、分组不明确或矩阵不可解析时，系统会停在 review，不会伪造结果。

### Windows 提示未知发布者

当前 exe 未签名。正式商用发布前需要代码签名证书。

## 11. 交付验收标准

最小合格：

- 安装完成；
- UI 能打开；
- 语言能切换；
- `v5-doctor` 可运行；
- 发布验收页可打开；
- 报告页和数据集页可打开；
- 卸载脚本可运行。

增强合格：

- Docker 可启动；
- PostgreSQL/MinIO 后端 ACTIVE；
- DeepSeek/OpenAI-compatible Key 可用；
- 能跑一个真实研究问题；
- 生成 canonical report manifest 和 Artifact Registry。
