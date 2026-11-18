# 单一 Migration Harness

仓库直接维护团队包的 `runtime/harness/`；公开入口为 `scripts/harness.py`，
工作流技能为 `android-to-harmony-feature-rollout`。基础规范和 UI 资料是支持技能，不是独立引擎。
每个功能使用独立 run 目录、合同、批准和证据；任务之间不共享批准，也不复制引擎到业务仓库。

## 维护

遵守仓库 COMMIT-AND-DEPLOY.md：本地特性分支修改并自测，经 MR 合入 master 后由使用方 pull/安装。
直接修改唯一源码，运行包根 `scripts/refresh_release.py`、运行时与发布包测试、`scripts/selftest.py`。
`release-capabilities.json` 为能力清单，`release-files.json` 校验实际发布字节。
本机技能目录属于运行入口；刷新清单、导出 ZIP 和修改仓库均不表示更新了本机安装。

## 接入与恢复

通过 `capabilities` 检查实际运行能力；doctor 的 `--selected-skill` 或 `--launcher` 可静态核对宿主入口。
当前格式 run 用 `status/resume` 只读核对，按输入与证据摘要重新判断有效性。
旧格式引擎与转发入口已删除；历史业务档案保留，不改原批准、收据、哈希或成功状态。
未知格式明确报错，新任务使用当前合同和全新目录。

安装器将退役技能从扫描目录移入备份，保留安装收据和失败回滚能力。
备份只用于恢复用户文件，不注册为另一套运行入口。全局安装需要实际执行安装流程。
团队使用包根 START_HERE.md；项目工具链、源码、设备和账号按使用者本机配置。
