# TargetCompass 文件结构说明

## 当前应优先看的目录

- `targetcompass_lite/`：平台主代码，v5 canonical、UI、执行器、后端抽象都在这里。
- `scripts/`：打包、验收、导出、维护脚本。
- `tests/`：单测和验收测试。
- `packaging/windows_v5/`：Windows 安装器脚本、启动器、Inno Setup 配置。
- `projects/vascular_aging_demo/`：当前 v5 主 demo 项目。
- `projects/sarcopenia_muscle_sasp_demo/`：MVP/旧流程展示 demo，已移走大体积 raw data 和 results。
- `dist/`：只保留最新版交付文件。

## 当前保留的交付文件

- `dist/TargetCompassV5_Setup.exe`
- `dist/TargetCompassV5_Windows_Installer_20260624T072954Z.zip`
- `dist/TargetCompassV5_Windows_Installer_20260624T072954Z_manifest.json`
- `dist/targetcompass_v5_professor_demo_bundle_20260624T072838Z.zip`
- `dist/targetcompass_v5_professor_demo_bundle_20260624T072838Z_manifest.json`

## 已归档到 D 盘的内容

归档根目录：

```text
D:\TargetCompass_archive_20260624
```

包含：

- `tmp_smoke/`：历史 Windows 安装 smoke 目录。
- `dist_old/`：旧 v4/v5 安装包、bundle、manifest。
- `projects_old/`：旧大样本工程验证项目。
- `project_heavy_outputs/sarcopenia_muscle_sasp_demo/`：MVP demo 的 raw data 和大体积 results。

## 文档整理

`docs/` 根目录只保留当前接手和验收需要的文档。

阶段性开发总结、审计报告、长 extracted 文档已移动到：

```text
docs/archive/
```

## 注意

- 不要直接删除 `projects/vascular_aging_demo/`，它是当前 v5 主 demo。
- 不要直接删除 `packaging/windows_v5/`，它是当前 Windows 安装器来源。
- 如果要重新封装 exe，需要先确认 `dist/` 中保留的是最新 bundle，再运行打包脚本。
