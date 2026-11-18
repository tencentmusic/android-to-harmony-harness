# Migration Harness 完整团队包 1.8.0

团队包包含统一迁移工作流、基础项目规范、UI 模板资源、项目配置和运行依赖。
公开入口为 `python scripts/harness.py`，工作流技能为 `android-to-harmony-feature-rollout`。
采用需求、技术、UI 联合评审与逐点验收。源码只有 `runtime/harness/` 一份，发布包直接使用这份实现。

各环节的执行实现、回归入口和已知缺口见 [能力覆盖表](CAPABILITY-COVERAGE.md)。
正式视觉任务必须绑定内置比较器；系统录屏与知识库身份桥接尚未迁入当前内核。

## 安装与配置

准备 Python 3.9–3.12 和 Git。在解压目录运行：

```bash
python3 scripts/bootstrap.py
python3 scripts/selftest.py --output selftest-result.json
```

Windows 使用 `python`，详细步骤见 [WINDOWS.md](WINDOWS.md)。bootstrap 创建当前包的 `.venv`；
自检只操作临时合成工程，不访问业务设备，不授予业务交付通过。
可直接告诉 Agent 读取本包 [START_HERE.md](START_HERE.md)，或注册当前目录：

```bash
python3 scripts/install.py --register-only
```

需要复制到固定安装目录时执行 `python3 scripts/install.py`。版本目录来自发布清单，默认位于
`~/.local/share/migration-harness-team/1.8.0/`。安装注册三项技能：基础规范 `android-to-harmony`、
统一工作流 `android-to-harmony-feature-rollout`、UI 资料 `hmos-design-visual-mobile`。
Windows 创建轻量转发文件，macOS 默认使用符号链接；引擎和资源保持在完整包内。

发现旧入口时先保留原文件并列出冲突；`--replace` 会备份后替换，并将退役的
`migration-harness-v2` 从技能发现目录移到备份。支持处理 `.agents/skills` 与 `.codex/skills`；
不会改插件缓存和宿主设置。插件安装与直接技能注册选择一种，防止重复加载。
安装后在新任务中加载技能，已打开任务可能保留旧上下文。
安装器同时更新 `migration-harness` 命令入口并备份旧启动器，避免 PATH 与技能指向不同运行时。
可用 `--bin-dir` 指定命令目录；doctor 会静态报告入口不一致，不执行旧脚本。

安装依赖需要可用 Python 包源；离线环境可传 `--wheelhouse /实际/wheels目录`。
已配置匹配依赖的环境可传 `--skip-deps`，再用 doctor 检查。不要复制旧 `.venv`。

在包根绑定自己的工程路径，示例项目仅提供本机路由与规范：

```bash
python3 scripts/configure.py --project interactive-demo \
  --harmony-root /实际路径/harmony --android-root /实际路径/android \
  --deveco-home /实际路径/DevEco-Studio.app
python3 scripts/doctor.py --project interactive-demo
python3 scripts/harness.py capabilities
```

`configure.py` 仅提供示例 Profile 路由；其他工程通过 `project-discover` 生成待核对的
JSON Project Profile，按 [工程接入](runtime/harness/PROJECT-EXECUTION.md) 验证实际能力。
路由用的 Markdown Profile 提供项目规范，执行用的 JSON Profile 描述模块、工具、recipe 与能力证据。
路径配置保存在 `.local/`，不进入发布文件。doctor 的环境通过不代表构建、设备或业务验收通过。

## 发起迁移

在 Harmony 工程的任务中发送，并提供本机路径及已有截图、录屏或设计：

```text
读取 <团队包路径>/START_HERE.md，使用 Migration Harness 完成这次迁移。
Android 来源：【路径与入口】；Harmony 目标：【路径与模块】。
功能与范围：【本次目标及明确排除项】。
先查证并按变化准备测试计划，展示需求、技术、UI 联合评审 HTML。
批准后实现与逐点验收；沿用有效批准，失败时继续独立项，最终生成开发交接 HTML。
```

完整步骤见 [Agent 工作流](runtime/harness/AGENT.md) 和
[合同](runtime/harness/CONTRACT.md)。新任务先保存 session 请求，
核对项目能力并编译 [变化驱动测试计划](runtime/harness/TEST-PLAN.md)，再创建 run：

```bash
python3 scripts/harness.py start --contract /实际路径/contract.json --run-dir /实际路径/new-run
python3 scripts/harness.py review-packet --run-dir /实际路径/new-run
```

`init` 是同一创建操作的兼容命令。人工决定必须对应实际展示的当前材料；创建合同和工具升级不代替批准。
源码、材料、候选包与验收证据均按摘要检查新鲜度，外部执行器的“完成”只是候选结果。
全量交付需 `verify=delivery_ready`、当前开发交接和最终人工复核。
`demo_only`、局部通过及自检均不代表真实迁移完成。

## 继续与 Agent 接入

所有任务均从原目录恢复：

```bash
python3 scripts/harness.py resume --run-dir /实际路径/run
python3 scripts/doctor.py --launcher /实际包路径/scripts/harness.py --require-capability end_check_receipts
```

`resume/status` 只读核对当前任务的合同、批准、收据和证据。
唯一合同格式为 `migration-harness/1`，状态文件为 `harness-state.json`。
旧引擎、版本转发入口和按格式分发逻辑已删除；旧格式明确报错，历史业务档案保留原样。
已存在的 run 使用 `resume`，新建不会覆盖旧目录。

CLI/SDK Agent 通过 [通用运行器](runtime/harness/AGENT-RUNNER.md) 的进程协议接入：

```bash
python3 scripts/harness.py agent-check --adapter /实际路径/agent.json --require-enforcement
python3 scripts/harness.py agent-drive --run-dir /实际路径/run --adapter /实际路径/agent.json --operation-id drive-1
python3 scripts/harness.py agent-status --run-dir /实际路径/run
```

Harness 持久化调用并监督子进程；Agent 返回后重新核对真实证据和 `end-check`，仍有独立待办时继续派发。
中断后先核对原进程及结果，必要时 `agent-reconcile`；无法证明原调用结束时保留待核对，不重复执行。
预算耗尽或缺少结果不会标记完成。桥接脚本负责供应商 CLI/SDK、登录和会话标识转换。
`doctor.py --agent-adapter /实际路径/agent.json --require-agent-enforcement` 可静态检查接入配置。

会话、范围与外部等待见 [会话协议](runtime/harness/LIFECYCLE.md)，连续设备动作见
[声明式套件](runtime/harness/DEVICE-SUITES.md)。托管控制作用于 `agent-drive` 的子进程；
其他应用独立对话的结束 hook 仍需宿主接入。当前不会自动安装 Codex hook，也不提供后台唤醒。

## 知识与 UI

工程含结构化知识库时先读 catalog、page-schema 与 code-map-schema，复用 canonical 功能包，
按 wiki → CodeGraph → 定点源码顺序查证。通用工程使用自己的知识来源与 Profile，无须绑定业务特例。
知识回写按项目规范完成，不以写入知识库代替任务验收。参考：

- [知识库查证顺序](skills/android-to-harmony-feature-rollout/references/evidence-first-discovery.md)
- [身份映射与查重](skills/android-to-harmony-feature-rollout/references/evidence-first-discovery.md)
- [新功能落档](skills/android-to-harmony-feature-rollout/references/fxwiki-greenfield-package.md)

UI 规范、HTML/CSS、图标、字体和状态栏图片包含在 `hmos-design-visual-mobile` 中。
使用本机 Chrome/Edge 渲染原型，视口取真实任务尺寸：

```bash
python3 scripts/render_prototype.py --html /实际路径/原型.html \
  --output /实际路径/新的原型渲染.png --width 360 --height 792
```

原型不代表 Harmony 真机证据。实现后按所选视觉验收点比较当前包截图、Android 基线与批准原型。
独立真机采集、SSIM/MAE、动态 mask、必验 ROI 与正式任务接入见 [视觉比较](runtime/harness/VISUAL.md)。
项目源码、SDK、签名、adb/hdc、设备与账号由使用者本机准备；CodeGraph 与 HomeTrans 属于可选增强。

## 维护与验证

本包是可独立运行的发布单元。`release-capabilities.json` 为唯一公开能力清单，
`release-files.json` 保存发布哈希。直接修改 `runtime/harness/`，刷新清单后验证：

```bash
python3 scripts/refresh_release.py
python3 -m unittest discover -s tests
python3 -m unittest discover -s runtime/harness/tests
python3 scripts/selftest.py
```

遵守仓库 COMMIT-AND-DEPLOY.md，经 MR 合入 `master` 后导出正式 ZIP：

```bash
python3 scripts/export_release.py --output /实际输出目录/migration-harness-team-1.8.0.zip
```

发布包不含 `.local/`、`.venv/`、业务源码、账号与迁移档案。刷新和导出不表示已更新本机技能。
macOS 回归和模拟平台测试不替代 Windows 原生、真实供应商 Agent、DevEco 与设备验收。
