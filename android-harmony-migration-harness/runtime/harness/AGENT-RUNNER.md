# 通用 Agent 运行与适配

## 选择接入方式

`agent-drive` 在前台启动配置的 Agent 桥接进程，每轮返回后由 Harness 根据当前证据执行 `end-check`。
Agent 的 `completed` 仅表示该次调用返回；独立待办仍存在时继续派发，只有有效结束回执才退出 0。
业务任务、验收、人工批准和执行预算沿用原协议，Agent 结果不能直接写成任务 passed。

| mode | 接入方式 | 结束控制 |
| --- | --- | --- |
| managed | CLI 或 SDK 通过桥接进程接入 `agent-drive` | Harness 控制后续调用与请求完成判定 |
| host_hook | 宿主在结束前调用 `end-check` 并执行拒绝结果 | 由外部宿主实施；描述文件不能证明已经接线 |
| cooperative | Agent 按会话协议主动检查 | 流程约定与审计 |

托管控制只作用于 `agent-drive` 的调用。它不修改 Codex、Cursor 或其他应用的配置，不拦截这些应用中独立运行的对话。
适配器持有本机执行权限，是可信执行边界；Harness 不对任意 Agent 代码提供沙箱，也不认证人工身份。

## 接入托管进程

先按 [会话协议](LIFECYCLE.md) 创建 run 并保存当前用户请求与范围。为具体 Agent 编写桥接脚本：读取请求 JSON，
把上下文传给该 Agent 的 CLI 或 SDK，保存该次调用的结构化结果。描述文件示例：

```json
{
  "format": "harness-agent-adapter/1",
  "id": "team-agent",
  "mode": "managed",
  "cwd": ".",
  "command": ["{python}", "bridge.py", "--request", "{request}", "--result", "{result}"],
  "supports_resume": true,
  "timeout_seconds": 600,
  "idle_timeout_seconds": 300
}
```

`cwd` 相对描述文件解析。命令使用 argv，`{python}` 选择当前 Harness 的 Python，`{request}` 和 `{result}` 为独立参数；
不经过 shell。Node、原生可执行程序也可直接作为首项。凭证由进程环境或供应商的本机登录提供，避免写入会归档的 argv。

```bash
python3 migration.py agent-check --adapter /path/agent.json --require-enforcement
python3 migration.py agent-drive --run-dir /path/run --adapter /path/agent.json --operation-id drive-1
python3 migration.py agent-status --run-dir /path/run
```

团队包把命令前缀 `python3 migration.py` 替换为 `python <包根>/scripts/harness.py`。
也可使用 `doctor.py --agent-adapter /path/agent.json --require-agent-enforcement` 检查配置。
静态检查不执行 Agent；`enforcement_available=true` 仅说明可由托管入口控制，`active_enforcement=false` 不宣称已被当前应用选用。
当前提供通用进程协议，具体供应商的登录、SDK 事件和会话标识由各自桥接脚本转换，未内置或认证某家产品的适配器。

### 请求与结果

请求格式为 `harness-agent-turn/1`，包含 `turn_id/run_id/session_id`、`user_request/scope`、`run_dir`、
`harness_command`、`next_action/pending_actions`、原任务进程的 `recovery` 和上一轮收据。
桥接器执行当前请求范围及其前置依赖，使用 `harness_command` 提交真实任务证据；恢复动作优先核对原进程和结果。
`action=resume` 时将 `resume_token` 传给同一个 Agent 会话的续接接口，不能解释为重新执行上一条命令。
`action=execute` 使用当前快照开始一次新调用；`replay_saved_commands=false` 始终保留。

结果写入指定文件，标识必须逐项复制当前请求：

```json
{
  "format": "harness-agent-turn/1",
  "turn_id": "<当前 turn_id>",
  "run_id": "<当前 run_id>",
  "session_id": "<当前 session_id>",
  "status": "yielded",
  "resume_token": {"conversation_id": "<供应商的会话标识>"}
}
```

`status` 可选 `yielded/completed/interrupted`。正常结果要求进程成功退出，`completed` 与 `yielded` 都会重新检查任务。
结果最多 1 MiB，续接标识为非空字符串或对象，最多 16 KiB；不支持续接时省略 token 并声明 `supports_resume=false`。
需要续接的断连用绑定当前轮次的 `interrupted` 结果保留 token。日志输出不作为任务通过或进展的依据。

### 事件

桥接器可将 SDK/CLI 事件转换为请求 `events_path` 指定的 UTF-8 JSONL，每行一个完整 JSON 并以换行结束。
也可在 argv 中用独立的 `{events}` 参数取得同一路径。事件格式为 `harness-agent-event/1`，每条复制
当前 `turn_id/run_id/session_id`，`sequence` 从 1 连续递增，`type` 可选
`started/progress/checkpoint/message/interrupted/completed`，`data` 为对象：

```json
{"format":"harness-agent-event/1","turn_id":"<当前 turn_id>","run_id":"<当前 run_id>","session_id":"<当前 session_id>","sequence":1,"type":"started","data":{}}
```

事件流最多 4 MiB、10000 条，运行器验证所属轮次和顺序后绑定到收据；错轮次或截断的流保留待核对。
事件是观测记录，不授予完成、不重置进展预算，也不替代断连时的结构化结果。

## 循环、预算与等待

运行器优先检查请求完成，再处理独立 Agent 待办。等待人审前必须已有当前评审包；外部等待必须绑定有效 hold。
某个域等待审批或外部服务时，继续其他已授权的独立任务。`tasks/review` 范围只调度所需前沿，不扩展到全量任务。
通过范围检查的回执仍为 `quality_gate_granted=false`，全量质量结论仍由 `verify` 给出。

默认每个 operation 最多 20 轮、连续 3 轮无证据进展、2 次自动续接、1800 秒墙钟时间，分别通过
`--max-turns/--max-no-progress/--max-reconnects/--max-seconds` 配置。实际任务状态、输入、审批、验收与有效 hold
决定进展，Agent 自报进展或重复输出不重置预算。进程另有总时限和日志空闲时限。

预算或无进展停止时退出 1，`can_end_turn=false`，保留待办，不生成成功结束或人工批准。
同一个 operation 的预算与启动次数持久化；不能靠重复调用重置。新 operation 是显式的新调度请求。
用户审批或外部条件恢复后重新读取当前状态，用新 operation ID 和 `--resume` 继续；Harness 不后台轮询或唤醒对话。

```bash
python3 migration.py agent-drive --run-dir /path/run --adapter /path/agent.json --operation-id drive-2 --resume
```

同 ID 的成功调用会重新验证结束回执，输入变化后返回 `stale_operation`，不会复用过期成功或自动重做业务动作。

## 断开与恢复

每次派发前先保存 `agent-controller.json` 中的 pending turn，随后保留 `agent-turns/<turn_id>/`
内的请求、进程状态、日志、结果和不可变收据。运行 Agent 时不占用业务任务锁，Agent 可正常调用任务 CLI。
同一 run 的托管运行器互斥，未决 Agent 进程或损坏的 controller journal 会阻止 `end-check` 放行。

自动续接要求：桥接器声明支持续接、当前结果为有效 `interrupted` 且有 token、原进程已经停止、
没有未决业务任务进程、续接预算尚有余量。POSIX 上同时检查原 PID 和进程组；无法证明已停止时保持待核对。
Windows 沿用受控进程的终态记录；缺少终态时不根据 PID 或日志猜测可以重试。

缺失结果、错轮次结果、进程状态不明或宿主退出时保留 `in_doubt`，不自动重启 Agent。
先读 `agent-status` 和 `resume`，核对原进程与业务后置条件，已有业务结果先走原任务的 `reconcile`。
之后使用 `agent-reconcile` 处理 Agent 调用本身：

```json
{
  "turn_id": "<当前 pending turn>",
  "action": "accept",
  "reason": "已核对原进程结束且结果属于本轮",
  "evidence": ["/path/diagnostic-evidence.txt"]
}
```

```bash
python3 migration.py agent-reconcile --run-dir /path/run --operation-id recover-1 --input /path/reconcile.json
```

`accept` 校验原结果并补记收据，不再次执行。`retry` 要求诊断证据与原进程已停止，保留旧结果，清除旧会话 token；
仅允许后续显式调用重新从当前任务状态开始，本命令不派发动作。缺失进程身份或无法核对的执行仍保留待核对状态。
收据、token 和原始诊断是本机运行数据，不能随发布包分发。

## 离线验收

```bash
python3 examples/managed_agent_demo.py --output /tmp/harness-managed-demo
```

该示例每轮都声称 completed，但运行器会推进两个独立检查后才结束本次限定请求。
示例复用已有合成合同和模拟批准，不调用真实模型或设备，不提供业务验收结论。
