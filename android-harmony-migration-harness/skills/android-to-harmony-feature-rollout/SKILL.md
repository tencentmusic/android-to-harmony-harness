---
name: android-to-harmony-feature-rollout
description: "使用 Migration Harness 将 Android 功能或同期需求迁到 HarmonyOS、继续已有迁移任务，或接入 CLI/SDK Agent 时使用。采用需求、技术、UI 联合评审与逐点验收。Harmony-only 修复和普通代码审查不触发。"
---

# Migration Harness

包根是本文件上两级目录。所有命令使用 `python <包根>/scripts/harness.py`，源码在
`runtime/harness`，工作流和合同只有一套。

1. 运行 `capabilities`，确认 `format=migration-harness/1`，且 `joint_review`、`change_driven_test_plan`、
   `end_check_receipts` 与 `managed_agent_runner` 为 `true`。有视觉验收时还须确认
   `native_screen_capture`、`deterministic_visual_comparison`、`real_visual_binding_required` 和
   `plan_visual_bindings` 为 `true`，读取包根 `runtime/harness/VISUAL.md`，将比较配置写入测试计划。
   缺能力先修复当前选中的安装入口，并重新握手。首次使用按包根 `START_HERE.md` 准备环境。
2. 读取同级 `android-to-harmony/SKILL.md` 的项目路由、架构与基础规范；需要 UI 时读取
   `hmos-design-visual-mobile/SKILL.md`。编排、批准与交付以本入口指向的合同为准。
3. 已有当前格式 run 执行 `resume --run-dir`，复用原目录和仍有效的批准与证据。
   旧格式报错时保留原始档案，不改格式或摘要来冒充当前验收。
4. 读取包根 `runtime/harness/AGENT.md`。新任务先保存 session 请求，再按
   `PROJECT-EXECUTION.md` 和 `TEST-PLAN.md` 准备项目 JSON Profile、变化与测试合同，
   `start --contract <contract.json> --run-dir <新目录>`。路径路由的 Markdown Profile 提供规范，
   执行的 JSON Profile 提供可验证能力；按实际工程核对后再执行。

资料查证按 `references/evidence-first-discovery.md` 执行；结构化知识库按
`references/fxwiki-android-discovery.md` 和 `references/fxwiki-id-mapping.md`（通用知识库流程）复用 canonical 包。
展示关联需求、技术、UI 和验收点的联合评审 HTML，登记用户对当前材料的实际决定后实现。
逐点提交功能和视觉证据，失败时继续前置条件满足的独立项，最终生成当前开发交接 HTML 并复核。

Harness 托管 CLI/SDK Agent 时读包根 `runtime/harness/AGENT-RUNNER.md`，
先用 `agent-check` 校验桥接配置，再通过 `agent-drive` 派发。有未决调用时先
`agent-status/agent-reconcile` 核对原进程与结果，再续接。

当前 run 的 `continuation.can_end_turn=false` 时继续 `next_action`，最终答复前执行 `end-check`。
全量交付须 `verify=delivery_ready`、开发交接为 current 且无 Agent 待办。
`demo_only`、局部通过和离线自检均不代表业务迁移完成。

讨论工具共建、维护或安装时读取 `references/single-source.md` 和包根
`runtime/harness/EXTENSIONS.md`，完成能力握手与接入检查。只有具体业务任务才创建业务 run。
