# TargetCompass V4 Windows 安装包

这个安装包用于没有 Python 环境的 Windows 电脑。

## 安装

1. 解压 `TargetCompassV4_Windows_Installer_*.zip`
2. 右键 `Install-TargetCompassV4.ps1`
3. 选择“使用 PowerShell 运行”
4. 安装完成后，从桌面或开始菜单打开 `TargetCompass V4`

默认安装位置：

```text
%LOCALAPPDATA%\TargetCompassV4
```

安装器会自动：

- 解包 TargetCompass V4 本地应用
- 下载并安装便携 Python runtime
- 安装 Python 依赖
- 创建桌面快捷方式
- 创建开始菜单入口
- 放置卸载脚本

## 启动

双击桌面快捷方式 `TargetCompass V4`。

默认地址：

```text
http://127.0.0.1:8781/
```

## 卸载

从开始菜单运行：

```text
Uninstall TargetCompass V4
```

或执行：

```powershell
%LOCALAPPDATA%\TargetCompassV4\Uninstall-TargetCompassV4.ps1
```

## 说明

这个包不要求预装 Python。DeepSeek、GEO、PubMed 等真实外网功能仍需要网络和 API key。Nextflow、Docker、WSL 属于高级分析运行时，未内置为零环境组件。
