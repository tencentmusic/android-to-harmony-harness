# Android to HarmonyOS Migration Harness

通用的 Android → HarmonyOS 迁移编排工具。通过需求、技术、UI 联合评审、真实构建和逐点验收，
让 CLI/SDK Agent 执行可恢复、可核对的迁移任务。

仓库只维护一套实现：`android-harmony-migration-harness/runtime/harness/`。
唯一公开 CLI 为 `scripts/harness.py`，工作流技能为 `android-to-harmony-feature-rollout`。
基础项目规范与 UI 资源分别由 `android-to-harmony`、`hmos-design-visual-mobile` 提供。

提交与部署遵守 [COMMIT-AND-DEPLOY.md](COMMIT-AND-DEPLOY.md)：本地特性分支开发，经 MR 合入 `master`。

## 快速开始

Python 3.9–3.12 和 Git；在团队包目录执行：

```bash
cd android-harmony-migration-harness
python3 scripts/bootstrap.py
python3 scripts/harness.py capabilities
python3 scripts/selftest.py
```

Windows 使用 `python`。安装注册、本机工程配置和完整使用方式见
[团队包说明](android-harmony-migration-harness/README.md)、
[Agent 入口](android-harmony-migration-harness/START_HERE.md) 和
[Windows 说明](android-harmony-migration-harness/WINDOWS.md)。

## 工作流

1. 保存本轮请求与范围，读取项目知识并查证 Android 行为、来源和状态。
2. 核对项目 JSON Profile 的模块、工具、构建和设备能力；按变化编译验收计划。
3. 展示需求、技术、UI 联合评审 HTML，登记用户对当前材料的决定。
4. 实现与真实构建，按合同逐点提交功能、设备和视觉证据。
5. 核对失败与中断，复用有效结果，生成开发交接 HTML 并完成最终人工复核。

`resume` 只读恢复当前任务；`end-check` 根据当前范围和证据判断能否结束。
全量交付要求 `verify=delivery_ready`、当前开发交接和最终人工复核；
合成演示、自检、局部通过和 Agent 自报完成均不授予真实交付通过。

其他 CLI/SDK Agent 通过 `agent-check` / `agent-drive` 的通用进程协议接入。
Harness 持久化每次调用、监督子进程并执行结束检查，中断后先核对原进程与结果。
供应商桥接脚本负责实际 CLI/SDK 和登录，独立宿主对话的结束 hook 需要宿主接入。

## 维护与验证

直接修改团队包内的唯一源码，刷新发布清单后测试：

```bash
python3 android-harmony-migration-harness/scripts/refresh_release.py
python3 -m unittest discover -s android-harmony-migration-harness/runtime/harness/tests
python3 -m unittest discover -s android-harmony-migration-harness/tests
python3 android-harmony-migration-harness/scripts/selftest.py
```

旧引擎、兼容入口及其独立文档已删除。旧格式任务不会自动转换审批或证据；
历史业务档案保持原样，当前实现只接受 `migration-harness/1` 合同和 `harness-state.json`。
当前修改不表示已发布或更新全局安装。

## 使用边界

- 项目源码、DevEco、SDK、签名、设备和账号由使用者准备。
- 模拟平台和离线回归不替代 Windows 原生、真实供应商 Agent 与真机验收。
- 需求完整性、设备行为和图像比较需要可信适配器与人工复核。
- 包内不提供后台唤醒，不自动安装 Codex hook；评分不能替代必验断言。
