# TargetCompass v5 本地开发验收版最终交付说明

## 交付定位

本次交付的是 **TargetCompass v5 本地单机开发验收版**。它面向教授演示、甲方 GPT 验收、后续 Codex 接手开发，不再包含生产级登录/OIDC/Vault。默认运行方式是本机启动服务，再自动或手动打开浏览器访问本地 Web UI。

本版保留的边界：

- 不做真实多用户登录系统；
- 不要求外网生产部署；
- 不承诺离线完成 GEO/PubMed/LLM 检索；
- 不把 metadata 不足的数据集伪装成可分析数据；
- 不把 SRA/cellxgene 的候选 metadata 等同于真实矩阵分析完成。

## 主要交付物

交付目录：

```text
C:\Users\ASUS\Documents\target\dist
```

核心文件：

```text
TargetCompassV5_Setup.exe
TargetCompassV5_Windows_Installer_*.zip
targetcompass_v5_professor_demo_bundle_*.zip
targetcompass_v5_developer_bundle_*.zip
```

推荐交付组合：

- 给教授或非开发人员：`TargetCompassV5_Setup.exe`
- 给验收人员：`TargetCompassV5_Windows_Installer_*.zip`
- 给另一个 Codex 或开发人员：`targetcompass_v5_developer_bundle_*.zip`
- 给轻量演示：`targetcompass_v5_professor_demo_bundle_*.zip`

## 当前已完成能力

- v5 canonical agent 控制面；
- 7 个规范 Agent 的 handoff、claim ceiling、human gate；
- 真实 DeepSeek/OpenAI-compatible LLM 接入路径；
- GEO / PubMed / Europe PMC resource discovery 试行路径；
- v5 TaskPacket、TaskRun、QCReport、Artifact Registry、canonical report manifest；
- PilotDeck 本地 Web UI；
- 中文、日文、英文下拉切换；
- v5-doctor 自检；
- PostgreSQL/MinIO 本地后端激活与同步路径；
- Windows 安装器、启动、停止、重启、修复、卸载脚本；
- quick/full/e2e 测试入口；
- 10 问题真实在线验收记录；
- 发布前验收页面 `/v5/release-acceptance`。

## 已知限制

- 登录/成员/OIDC/Vault 不作为本次交付范围；
- 仍有少量 legacy writer 会先写本地文件，再进入 ArtifactStore/EvidenceRepository；
- SRA/cellxgene 仍需更多真实矩阵路径大样本验收；
- 干净 Windows/VM 安装验收需要在目标机器另做记录；
- Inno Setup 生成的 exe 未签名，Windows 可能提示未知发布者；
- Rscript、Nextflow、Docker 缺失时不会阻止 UI 启动，但相关分析能力会降级或 WARN。

## 最小验收流程

1. 安装或解压交付包。
2. 启动 TargetCompass V5。
3. 打开本地 UI。
4. 运行自检：

```powershell
python tc_lite.py v5-doctor --project vascular_aging_demo
```

5. 打开验收页：

```text
http://127.0.0.1:<实际端口>/v5/release-acceptance
```

6. 检查：

- 页面能打开；
- 中文/日本語/English 下拉能切换；
- `v5-doctor` 无不可恢复 FAIL；
- `release acceptance` 页面能展示 quick/full/e2e、真实问题、真实数据主路径和安装交付状态；
- 报告页、数据集锁库页、生产就绪页能打开。

## 开发接手入口

开发人员从工程根目录进入：

```powershell
cd C:\Users\ASUS\Documents\target
python -m unittest tests.test_webapp -v
python tc_lite.py v5-doctor --project vascular_aging_demo
python tc_lite.py serve --project vascular_aging_demo --host 127.0.0.1 --port 8831
```

关键源码：

```text
targetcompass_lite/webapp.py
targetcompass_lite/i18n.py
targetcompass_lite/canonical/
targetcompass_lite/artifact_store.py
targetcompass_lite/evidence_repository.py
targetcompass_lite/release_acceptance.py
scripts/export_v5_local_bundle.py
scripts/build_windows_installer_v5.py
packaging/windows_v5/
```

## 本次封装后的建议验收结论

建议作为 **本地开发验收版 / 教授演示版** 交付。若要进入商业生产版，下一阶段再做：

- 干净机安装验收记录；
- 签名安装器；
- 离线 R/Nextflow/Docker 缓存；
- SRA/cellxgene 真实矩阵路径大样本；
- PostgreSQL/MinIO 完全替代所有 legacy writer；
- 生产级用户登录与权限后台。
