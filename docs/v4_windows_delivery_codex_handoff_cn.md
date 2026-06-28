# TargetCompass V4 Windows 交付接手说明

本文给后续 Codex/开发者使用，用于理解当前 v4.0 本地 Windows 应用封装状态、交付文件、验证方式和剩余风险。

## 当前交付定位

这是 TargetCompass V4 本地原型的 Windows 应用封装版，目标是让教授电脑不需要预装 Python 即可安装、启动、打开 UI，并运行核心 v4 本地服务与 DeepSeek Agent 验证。

它不是云端生产部署包，也不是完整离线包。Docker、WSL、Nextflow、Java 17+ 仍属于高级分析运行时，不随安装器强制内置。

## 最新交付文件

优先交付这个文件：

```text
C:\Users\ASUS\Documents\target\dist\TargetCompassV4_Windows_Installer_20260622T084047Z.zip
```

该安装器内含：

- `Install-TargetCompassV4.ps1`
- `Launch-TargetCompassV4.ps1`
- `Uninstall-TargetCompassV4.ps1`
- `README_CN.md`
- `payload/targetcompass_v4_local_bundle.zip`
- `installer_manifest.json`

对应的 v4 payload 来源：

```text
C:\Users\ASUS\Documents\target\dist\targetcompass_v4_local_bundle_20260622T083241Z.zip
```

可一起保留但不一定直接交付：

```text
C:\Users\ASUS\Documents\target\dist\TargetCompassV4_Windows_Installer_20260622T084047Z_manifest.json
C:\Users\ASUS\Documents\target\dist\targetcompass_v4_local_bundle_20260622T083241Z_manifest.json
```

单项目运行包：

```text
C:\Users\ASUS\Documents\target\projects\vascular_aging_demo\exports\
C:\Users\ASUS\Documents\target\projects\sarcopenia_muscle_sasp_demo\exports\
C:\Users\ASUS\Documents\target\projects\engineering_packet_validation_50\exports\
```

## 安装方式

在目标 Windows 电脑上：

1. 解压 `TargetCompassV4_Windows_Installer_20260622T084047Z.zip`
2. 右键 `Install-TargetCompassV4.ps1`
3. 选择“使用 PowerShell 运行”
4. 安装完成后，从桌面或开始菜单打开 `TargetCompass V4`

默认安装位置：

```text
%LOCALAPPDATA%\TargetCompassV4
```

安装器会自动：

- 解包 v4 应用 payload
- 下载 Python embeddable runtime
- 安装 Python 依赖
- 将 app 路径写入 embedded Python `._pth`
- 运行 `local-v4-prepare` 生成本机路径的服务脚本
- 创建桌面快捷方式
- 创建开始菜单启动和卸载入口

## 卸载方式

开始菜单运行：

```text
Uninstall TargetCompass V4
```

或执行：

```powershell
%LOCALAPPDATA%\TargetCompassV4\Uninstall-TargetCompassV4.ps1
```

## 已验证结果

安装后真实运行验证已经通过，注意这是在安装后的临时目录中跑的，不是源码目录。

验证日志：

```text
C:\Users\ASUS\Documents\target\results\installed_real_run_install_v2.log
C:\Users\ASUS\Documents\target\results\installed_real_run_verify_v2.log
C:\Users\ASUS\Documents\target\results\installed_real_run_uninstall_v2.log
```

验证结果：

- 安装：PASS
- 使用安装包内置 `runtime\python\python.exe`：PASS
- v4 local services：7/7 PASS
- DeepSeek Agent 真调用：PASS
- LLM schema validation：PASS
- 卸载：PASS
- 安装包 secret scan：0 hit

安装包结构/脱敏检查日志在最近一次命令输出中确认：

- `Install-TargetCompassV4.ps1` 存在
- `Uninstall-TargetCompassV4.ps1` 存在
- `Launch-TargetCompassV4.ps1` 存在
- `payload/targetcompass_v4_local_bundle.zip` 存在
- `secret_hit_count = 0`

## 重新构建命令

从源码目录执行：

```powershell
python scripts\export_v4_local_bundle.py
python scripts\build_windows_installer.py
```

如果修改了 v4 服务、报告或项目数据，建议先执行：

```powershell
python tc_lite.py local-v4-prepare --project vascular_aging_demo
python tc_lite.py test-suite --suite quick --timeout-seconds 180
```

完整安装后验证示例，注意不要把真实 key 写入文件：

```powershell
$SmokeDir = Join-Path $env:TEMP ('TargetCompassV4RealRun_' + [guid]::NewGuid().ToString('N'))
$env:OPENAI_API_KEY = '<DeepSeek/OpenAI-compatible key>'
$env:TARGETCOMPASS_LLM_PROVIDER = 'deepseek'
$env:TARGETCOMPASS_LLM_BASE_URL = 'https://api.deepseek.com'
$env:TARGETCOMPASS_OPENAI_MODEL = 'deepseek-chat'

powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\Install-TargetCompassV4.ps1 -InstallDir $SmokeDir
$Python = Join-Path $SmokeDir 'runtime\python\python.exe'
$AppDir = Join-Path $SmokeDir 'app'
Push-Location $AppDir
& $Python tc_lite.py local-v4-verify --project vascular_aging_demo --start-services --wait-seconds 5 --deepseek-test
Pop-Location
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $SmokeDir 'Uninstall-TargetCompassV4.ps1') -Quiet
```

## 关键代码位置

Windows 安装封装：

```text
packaging/windows/Install-TargetCompassV4.ps1
packaging/windows/Launch-TargetCompassV4.ps1
packaging/windows/Uninstall-TargetCompassV4.ps1
packaging/windows/README_CN.md
scripts/build_windows_installer.py
scripts/export_v4_local_bundle.py
```

v4 服务运行时修复：

```text
targetcompass_lite/service_deployment.py
targetcompass_lite/local_v4_delivery.py
```

GEO Dataset Scout 自动放宽/重试：

```text
targetcompass_lite/geo_discovery.py
tests/test_geo_discovery.py
```

LLM artifact 脱敏测试：

```text
tests/test_llm_parser.py
```

## 仍建议修改或注意的地方

1. 当前安装器不是完全离线。
   - 它会在线下载 Python embeddable、`get-pip.py` 和依赖。
   - 如果要完全离线，需要把 Python runtime、pip、wheelhouse 全部放进安装包。

2. Nextflow 仍是可选高级运行时。
   - 安装后真实验证中核心服务和 DeepSeek 通过。
   - Nextflow 只验证 execution plane，实际 Nextflow run 仍要求 Java 17+/WSL/Nextflow。

3. 安装器还不是 `.exe` 或 MSI。
   - 当前是 PowerShell 安装器 zip。
   - 如果要更像商业软件，下一步建议用 Inno Setup / NSIS / WiX 包成 `.exe`，把当前 PowerShell 作为内部 install action。

4. 旧的 `START_TARGETCOMPASS.bat` 和部分项目脚本仍偏开发模式。
   - 正式安装后请使用安装器创建的桌面快捷方式或 `Launch-TargetCompassV4.ps1`。
   - 不要要求用户直接运行源码根目录的旧脚本。

5. DeepSeek key 不应写入交付包。
   - 交付包已做 secret scan。
   - 教授电脑应通过 UI 或运行时环境变量输入 key。

## 给后续 Codex 的判断

当前可以交付“可安装、可卸载、无需预装 Python、安装后可真实启动核心 v4 本地服务并调用 DeepSeek”的 Windows 本地原型。

若用户要求“完全零网络/完全离线/单 exe 安装”，下一步不是继续改业务流程，而是做安装工程：

1. 内置 Python embeddable runtime
2. 内置 wheelhouse
3. Inno Setup/NSIS 生成 `.exe`
4. 安装后自动 health check
5. UI 中加入首次启动配置向导
