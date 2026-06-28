# TargetCompass V5 离线运行时缓存说明

本目录用于放置安装包可识别的离线运行时缓存。当前安装器已经包含：

- embedded Python zip；
- get-pip.py；
- wheelhouse 中的 Python wheel。

R、Nextflow、Docker 由于体积、许可证和机器环境差异，不默认随包完整分发。可以把已有离线文件放到以下目录：

```text
runtime_cache/r_packages/
runtime_cache/nextflow/
runtime_cache/docker_images/
```

## R / Rscript

可放入：

```text
runtime_cache/r_packages/R-4.x.x-win.exe
runtime_cache/r_packages/R-portable.zip
```

安装器不会静默安装系统级 R。安装后需要在 Setup Wizard 或系统 PATH 中配置 Rscript。

## Nextflow / Java

可放入：

```text
runtime_cache/nextflow/nextflow
runtime_cache/nextflow/nextflow.bat
runtime_cache/nextflow/OpenJDK17U-jre_x64_windows_hotspot_*.zip
```

Nextflow 需要 Java 17+。没有 Java 时，`v5-doctor` 会给出 WARN。

## Docker

可放入：

```text
runtime_cache/docker_images/postgres_16_alpine.tar
runtime_cache/docker_images/minio_latest.tar
runtime_cache/docker_images/targetcompass_lite.tar
```

Docker Desktop 本身不随包分发。安装 Docker Desktop 后，可用：

```powershell
docker load -i runtime_cache\docker_images\<image>.tar
```

## 生成缓存 manifest

在 `packaging/windows_v5` 目录运行：

```powershell
.\Prepare-OfflineRuntimeCache.ps1 -WriteManifestOnly
```

也可以一次性复制已有文件：

```powershell
.\Prepare-OfflineRuntimeCache.ps1 `
  -RInstaller D:\cache\R-4.4.3-win.exe `
  -JreArchive D:\cache\OpenJDK17U-jre_x64_windows_hotspot.zip `
  -NextflowBinary D:\cache\nextflow `
  -DockerImageTar D:\cache\postgres.tar,D:\cache\minio.tar
```
