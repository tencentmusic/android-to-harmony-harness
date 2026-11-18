# 从解压目录使用 Migration Harness

完整团队包通过 `python scripts/harness.py` 提供统一入口。新任务默认采用需求、技术、UI 联合评审与逐点验收；
继续任务时使用当前格式的原 run 目录。源码只有 `runtime/harness/` 一份；旧格式明确报错并保留原始档案。

开始业务任务前依次读取：

1. `skills/android-to-harmony/SKILL.md`：项目路由和基础规范。
2. `skills/android-to-harmony-feature-rollout/SKILL.md`：工作流、任务恢复和 Agent 接入。
3. 涉及 UI 原型时读取 `skills/hmos-design-visual-mobile/SKILL.md`。

所有相对路径以本文件所在解压目录为根。首次使用按 README 配置本机项目；Windows 先读 WINDOWS.md。
新工程可用 `project-discover` 生成待核对的 JSON Profile；路径路由与执行能力验证按工作流技能区分。
已注册技能的转发入口最终也读取这份包，不复制引擎到业务目录。

用户尚未给出具体功能时只检查环境与入口，不创建迁移任务。输入具体功能后遵循对应工作流；自检通过只说明合成环境检查通过，不提升业务验收状态。
