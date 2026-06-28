# TargetCompass V5 Windows 本地应用安装包

这是 TargetCompass v5 本地单机开发验收版安装包。它面向教授演示、甲方验收和后续开发交接。当前版本不要求登录，启动后会拉起本地 Web UI。

## 安装方式

### 方式 A：正式安装器

双击：

```text
TargetCompassV5_Setup.exe
```

默认安装目录：

```text
%LOCALAPPDATA%\TargetCompassV5
```

### 方式 B：PowerShell 安装

解压安装器 zip 后运行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\Install-TargetCompassV5.ps1 -LaunchAfterInstall
```

## 启动

桌面或开始菜单中点击：

```text
TargetCompass V5
```

也可以手动运行：

```powershell
& "$env:LOCALAPPDATA\TargetCompassV5\TargetCompassV5-Launcher.cmd"
```

启动器会启动本地服务，等待 ready 后打开系统默认浏览器。如果 8801 被占用，会自动选择后续可用端口。

## 自检

安装后可运行：

```powershell
cd "$env:LOCALAPPDATA\TargetCompassV5\app"
..\runtime\python\python.exe tc_lite.py v5-doctor --project vascular_aging_demo
```

`PASS` 或 `READY_WITH_WARNINGS` 表示可用于本地演示。Rscript、Docker、Nextflow 缺失时可能出现 WARN，不一定阻止 UI 启动。

## 主要页面

```text
/v5/release-acceptance
/v5/production-readiness
/v5/resource-gate
/v5/product-report
```

页面右上角支持语言切换：

```text
中文 / 日本語 / English
```

## 后端与高级运行时

可选运行时：

- Docker Desktop；
- PostgreSQL / MinIO 本地后端；
- Rscript；
- Nextflow；
- DeepSeek/OpenAI-compatible API key。

缺失这些组件时，平台会降级为本地文件和演示路径，不会伪造真实分析结果。

## 停止、重启、修复、卸载

```powershell
.\Stop-TargetCompassV5.ps1
.\Restart-TargetCompassV5.ps1
.\Repair-TargetCompassV5.ps1
.\Uninstall-TargetCompassV5.ps1
```

保留项目数据卸载：

```powershell
.\Uninstall-TargetCompassV5.ps1 -KeepProjects
```

## 日志

如果浏览器打不开或服务启动失败，请查看：

```text
%LOCALAPPDATA%\TargetCompassV5\logs
```

重点日志：

```text
targetcompass-v5-launch.log
targetcompass-v5.out.log
targetcompass-v5.err.log
```

## 当前边界

- 当前不是生产级多用户系统；
- exe 未签名，Windows 可能提示未知发布者；
- 干净 Windows/VM 安装验收需要在目标机器另行记录；
- SRA/cellxgene 真实矩阵主路径仍需要继续做大样本验收；
- metadata 不足的数据集会停在人工复核，不会自动锁库。
