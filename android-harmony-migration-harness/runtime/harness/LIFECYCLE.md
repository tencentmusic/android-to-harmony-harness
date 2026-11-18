# 会话、结束检查与恢复

本协议独立于迁移验收门禁，适用于不同项目和业务场景。能力标记为
`agent_sessions/end_check_receipts/read_only_recovery`；不提供后台 Agent 或宿主自动唤醒。

## 开始与续接

首次准备材料前登记当前请求，以覆盖 run 创建前的成本。下面的 `start.json` 使用完整范围；
局部请求改为 `scope={"mode":"tasks","task_ids":[...]}` 或
`scope={"mode":"review","capability_ids":[...]}`，并用 `evidence` 引用保存用户原话的文件。
review 表示准备指定域的交审材料，不能据此跳过实现或人工批准。

```json
{
  "action": "start",
  "session_id": "request-1",
  "request": "执行本次用户授权的完整迁移",
  "scope": {"mode": "full"}
}
```

```bash
python3 migration.py session --journal /path/preparation --operation-id start-1 --input /path/start.json
python3 migration.py init --contract /path/contract.json --run-dir /path/new-run
python3 migration.py session --run-dir /path/new-run --operation-id attach-1 --input /path/attach.json
```

`attach.json` 为 `{"action":"attach","journal":"/path/preparation"}`。源日志随后转为指向 run 的入口，
后续操作使用 run 日志。已有 run 可直接 `session --run-dir ...` 开始，不需要准备日志。
session 与 operation ID 使用不同的稳定标识；重复 operation ID 必须具有相同输入。
附加成功后，使用原 operation ID 重放只返回既有回执；新 operation ID 不得重新附加并覆盖 run 日志。
附加中断时使用原 operation ID、输入及预留目标重试，目标日志一次性提交完整记录与回执。

恢复时先执行 `resume --run-dir ...`。这是只读诊断，不自动执行、登记或重试：

| 观察 | 下一步 |
| --- | --- |
| 存在运行 PID | 观察原进程；PID 仍存在只说明该标识可见，不能证明进程身份 |
| process 已退出且 result 存在 | 使用原 `reconcile --action accept` 校验输入、进程、日志及结果 |
| 进程消失或结果不完整 | 检查业务副作用，按原 reconcile/retry 协议处理；不会重放命令 |
| Windows 无法可靠确认进程已停止 | 保留未决状态，不凭超时或 heartbeat 授权重试 |
| 输入或包变化 | 失效当前关联结果，保留其他有效点与历史证据 |

继续同一请求时登记 `{"action":"resume","session_id":"request-1","summary":"已检查原进程和结果，继续剩余工作"}`。
明确的新请求使用新的 session ID，并保存其范围。摘要不构成人工审批。
每次登记 resume 都推进会话续行轮次，之前的结束回执随之失效；新一轮停止必须重新检查。
`phase` 动作可登记发现、材料准备、实现、诊断及九种适配器阶段；需要 `session_id/summary/phase`。
`interrupt` 动作登记已观察的异常中断。CLI 捕获运行中断时自动登记；进程被强制杀死可能来不及写事件，恢复入口仍按原 running 记录提示未决。

## 外部等待

`session` 的 `hold` 动作需要 `session_id/id/task_ids/reason/resume_condition/expires_at/evidence`。
只允许登记当前前沿的根任务；依赖阻塞、人审、已完成任务和未决进程分别沿用原协议。
`expires_at` 是未来七天内的 Unix 秒数，证据必须真实存在。证据、合同、任务摘要或状态变化后 hold 失效。
每个 hold 仅屏蔽列明任务的 Agent 动作，其他独立工作仍需完成。
解除时使用 `release` 动作，提供相同 hold `id`、当前 `session_id`、`summary` 和新的 `evidence`。
这些文件记录可审计的诊断，不认证本机操作者身份，也不能自动证明业务前提成立。

## 结束检查与宿主

最终答复前调用 `end-check`，输入当前 `session_id` 与下列 `reason`。

| reason | 允许条件 |
| --- | --- |
| `request_complete` | tasks 范围全部当前通过；review 范围已有有效交审 HTML 或已批准；full 范围达到原续行终态 |
| `complete` | 原全量或显式限定交付协议达到终态；demo 仍标记模拟完成 |
| `waiting_human` | 当前前沿只有已准备材料的人审，且无独立 Agent 工作或外部 hold |
| `waiting_external` | 独立 Agent 工作已完成，剩余根任务均有有效 hold |
| `user_paused` | 提供 `actor_type=human`、`user_instruction` 原话及非空 `evidence` |

```json
{"session_id":"request-1","reason":"waiting_human"}
```

```bash
python3 migration.py end-check --run-dir /path/run --operation-id end-1 --input /path/end.json
```

每次检查保存不可变 `session-end-checks/<operation-id>.json`，绑定当前合同、任务摘要、审批、覆盖和请求范围。
退出码 **0** 表示该轮可以按此原因停止，**1** 表示仍需继续，**2** 表示输入或校验错误。
回放旧 operation ID 会重新核对当前快照和证据，失效回执不会继续放行。该回执始终 `quality_gate_granted=false`。

支持 before-end-turn hook 的宿主把上述命令配置为结束前检查，并在任意非零退出时取消最终答复、消费
`next_action` 后再次检查。`host_hook.py` 是同一命令的独立入口；团队包可直接使用
`python <包根>/scripts/harness.py end-check ...`，由包入口选择 Python 环境。
宿主应将命令与 operation ID 绑定到当前请求，不复用其他轮的成功输出。

需要由 Harness 控制续行时，按 [通用运行器](AGENT-RUNNER.md) 使用 `agent-drive`，通过进程协议接入具体 Agent 的 CLI 或 SDK。
运行器消费拒绝回执，继续独立任务，保存未决进程并执行有预算的续接；`agent-check` 和 `doctor --agent-adapter` 区分接入模式。
**当前不自动安装 Codex hook。** `automatic_host_hook=false`；托管控制限于 `agent-drive`，外部应用的 hook 仍由该应用接入。
不支持 hook 且未使用托管入口的宿主，由 Agent 显式检查并呈现回执，具有流程约束和审计能力。
上下文压缩后先消费同一个 `resume`；压缩本身不是连接中断的证据。

## 状态和耗时

`status.lifecycle` 区分 `working/waiting_human/waiting_external/interrupted/paused/request_complete/complete`，
提供当前请求、原 attempt、最近进展、下一动作、恢复条件和失效 hold。`request_complete` 只描述该轮范围。
checkpoint 保存合同、任务收据、批准引用与活动 attempt；源文件和候选包身份仍以原任务收据及动态状态为准。
status、resume 和 metrics 不执行设备动作，不更新会话日志。
日志损坏时核心证据仍可查询、诊断和提交，lifecycle 给出 journal_error；结束检查保持拒绝。
checkpoint_current 区分最新核心事件与过期检查点，旧检查点不会替代现状校验。

任务开始/结束自动切换会话阶段，显式 phase 覆盖 run 创建前准备。成功 end-check 开始等待或结束阶段；
`resume` 动作恢复工作。metrics/report 给出互斥状态区间、异常中断数和被拒绝的结束次数。
工作区间包含工具等待；宿主失联后的真实活跃时间无法从墙钟推断，未观测成本保留 unknown。
会话、timer、适配器和执行器存在包含关系，分别展示，不能相加作为模型耗时。

## 查询成本与入口检查

只读 status 在单次查询内共享 Profile 和审批域计算，查询结束即丢弃。后续查询重新读取哈希，
执行与收据提交仍重新核对输入；没有新增跨调用 mtime 缓存。CLI 的 status/coverage/report 共用当前锁内快照。
离线示例采样中，5 次 status 的 file_record 调用从 1,140 降至 840；这是查询工作量，不能推导业务迁移加速比例。

团队包 `doctor.py --selected-skill /path/SKILL.md` 或 `--launcher /path/scripts/harness.py`
检查显式选择的技能与运行时；`--require-capability end_check_receipts` 可检查所需能力。
声明必需能力时必须同时指定 `--selected-skill` 或 `--launcher`，否则检查返回未完成。
默认列出 `.agents/skills` 和 `.codex/skills` 的候选，无法从文件存在推断宿主实际选择。
`--run-dir` 核对当前任务收据引用的引擎文件与哈希；检查只读，不改写证据。
