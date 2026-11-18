# 迁移合同与执行器协议

`format=migration-harness/1` 是唯一合同编号，公开入口为 `scripts/harness.py`。
完整可运行样例见 [examples/demo.py](examples/demo.py)。CLI 使用确定性的结构和语义校验，不依赖 JSON Schema 库。

## 基础字段与依赖

| 字段 | 合同 |
| --- | --- |
| feature_id | 稳定功能 ID，字母数字、下划线、连字符，最多 80 字符 |
| policy | faithful_migration |
| demo | 合成演示为 true，真实模式为 false；创建后不可更改 |
| roots | android、harmony、tools 三个目录，相对合同文件解析，初始化时固定为绝对路径 |
| production_inputs | 影响构建的全部生产源码、依赖锁、配置与资源，不包含生成目录 |
| capabilities | id、behavior、has_ui、depends_on、android_sources[{path,locator}] |
| acceptance | id、capability_id、kind、required:true、behavior、assertions 与状态映射 |
| tasks | id、kind、capability_ids、depends_on、acceptance_ids、inputs、可选 command/max_attempts |

输入组为 `{"root":"harmony","include":["src/**/*.ets","build-profile.json5"]}`。
模式必须匹配文件且不得越界；新增和删除文件也影响摘要。辅助代码、fixture 与工具依赖须声明在 task.inputs。
引擎自动绑定执行核心、运行时、解释器及 argv 中现存文件，不自动推导全部导入与外部环境。

任务种类为 discover / implement / contract / build / device / visual / review。
每个能力恰有一个 discover 和 implement，implement 依赖对应 discover 与前置能力的 implement。
每个验收点恰由一个同 kind 的测试任务承接；测试依赖对应 implement。
恰有一个候选 build，依赖所有 implement；device/visual 依赖该 build。最终 review 的祖先包含其他全部任务。
所有依赖图必须无环。断言支持 equal 和 truthy；equal 按规范 JSON 精确比较，truthy 只接受布尔 true。

## 执行与持久化

非 Agent 任务声明 `command:{argv,cwd,timeout_seconds}`，cwd 为已声明 root 的名称。
内置视觉 task 可用 `visual_comparison` 省略 command，由包内适配器生成；点映射、ROI 和证据校验见 [VISUAL.md](VISUAL.md)。
argv 的解释器为真实绝对路径，`{request}` 与 `{result}` 必须各为一个独立参数；
另支持独立 `{android}`、`{harmony}`、`{tools}`，无 shell 展开。超时为正数秒，上限 7200。
适配器是可信本地程序，具有当前用户权限。

request 包含任务、来源根、input_digest、验收点、demo 和当前 package；适配器输出新的 result.json。
build 返回 `package_path` 并将同一文件列入 evidence；产物为本次 attempt 内新生成的 .hap/.app/.hsp，真实模式要求 ZIP 包。
执行前后输入必须一致。证据是 run 或来源根内的非空普通文件，绑定实际 SHA-256。
设备 context 必须核对 device_id、system_version、app_id、account_ref、package_sha256，
真实模式要求 normal_entry=true、synthetic=false；demo 明确 synthetic=true。原生点还需原始截图和命令日志。

discover / implement / review 使用 `record` 提交 task_id、最新 input_digest、summary 和 evidence。
最终 review 还需 reviewer、完整 acceptance_ids 和空 findings；检查点不授予测试通过。
`harness-state.json` 原子保存操作、事件和收据引用，`contracts/` 保存修订，`attempts/` 保存独立执行目录。
状态从当前输入和证据重新计算；重复 operation-id 校验输入，未决执行先核对现场再 reconcile，不自动重放。

## 项目上下文

合同 `project_profile` 是 `{root,path,locator}` 引用。root 为 android/harmony/tools；path 相对该根，文件非空，不得越界。
Profile 文件：

```json
{
  "name": "live-app",
  "modules": [{"id":"dynamicpublish","root":"harmony","path":"src/dynamicpublish","purpose":"动态编辑与发布"}],
  "knowledge": [{"root":"tools","path":"project.md","locator":"模块与工程约定"}],
  "tools": [{"id":"native-suite","purpose":"设备动作与截图","argv":["/absolute/python3"],"cwd":"tools"}]
}
```

Profile 和知识文件哈希进入任务摘要；它们不是自动维护的索引。`context` 只读列出模块、来源、工具可用性，不执行命令或授予新权限。
任务可用 `tool_ids` 关联工具目录；执行仍使用其显式 command。工具目录不是沙箱或命令白名单。

## 状态与评审材料

新任务使用可选 `test_plan.version=1`，生成顺序、字段、分组和版本修订见 [TEST-PLAN.md](TEST-PLAN.md)。
该模式由 `plan` 编译 acceptance，只要求选中的验证方式；状态库存与执行范围分开。
未声明 test_plan 的合同保留下述逐状态设备/视觉规则。两种模式均保留当前包冒烟与真实证据门禁。

`states` 为非空列表，每项包含：

```json
{
  "id":"photo-source",
  "capability_id":"photos",
  "kind":"dialog",
  "trigger":"点击图片入口",
  "expected":"呈现 Android 当前有效来源选择行为",
  "sources":[{"root":"android","path":"DynamicEditPhotosDelegate.java","locator":"入口点击与条件"}],
  "baselines":[{"root":"android","path":"evidence/photo-source.png","locator":"当前账号正常入口"}]
}
```

kind 为 page/dialog/loading/error/permission/exit/other。每个 UI 能力至少一个状态；未声明 test_plan 时每个状态都需要 device、visual 两类验收。
不可达状态暂缺截图时，允许 `baselines: []`，但必须提供非空字符串 `baseline_gap` 说明原因；有基准时不得同时声明该字段。
缺口随状态参与审批摘要，并在 review-packet 的 baseline_gaps 和 state_coverage 中明示。它允许登记、评审与获批后的实现，不代表视觉验收通过。
缺基准状态的 visual 点只能报告 blocked/not_checked；checked 会被引擎拒绝。必验点和最终门禁不变，补图需 revise 并重新取得受影响域的批准。
每个 device/visual acceptance 必须有对应 `state_id`；计划模式额外声明 covered_state_ids，由生成器映射同组状态。
未声明 test_plan 时同一验收点只能归属一个状态。功能规则的 contract 点可按需关联 state_id。
每点的 assertions 仍由合同声明；截图基准只接受来源根内 PNG/JPEG 及正确签名，文件绑定哈希。引擎不证明截图内容真实性或自动发现未知状态。
初始化、合同修订、审批摘要、任务输入和 checked 视觉结果接收均复验基准扩展名与文件签名。
基准缺失或签名不合法时阻止相关执行与审批，status/review-packet/inspect 仍可诊断；恢复有效文件后按当前输入重新判断结果有效性。

`review_scopes` 每个 capability 恰一项，id 与 capability_id 相同：

```json
{
  "id":"photos",
  "state_ids":["photo-source"],
  "requirements":{"root":"tools","path":"photos-requirements.md","locator":"需求与状态清单"},
  "technical":{"root":"tools","path":"photos-technical.md","locator":"技术方案与平台差异"},
  "ui":{"root":"tools","path":"photos-ui/index.html","bundle":"photos-ui","locator":"关键状态 UI 稿"}
}
```

state_ids 必须完整包含该能力的状态，三份材料都必须存在。非 UI 能力也应在 UI 材料中明确“不涉及可见 UI”的依据，由人确认。
三份材料分别按资源包绑定：`bundle` 是对应 root 下的相对目录，必须包含入口文件；省略时使用入口文件的父目录。
目录中全部普通文件（含子目录和空文件）的路径及哈希进入摘要，新增、删除或修改资源都会使批准失效；资源包拒绝符号链接。
每份材料应放在独立的静态目录，包含它实际展示的图片、CSS、字体和附件。外链、目录外资源及动态数据须先固化进包；
引擎绑定整个声明目录，不解析 HTML/CSS/Markdown 的依赖关系，不抓取远程内容。不要把构建输出或运行中的工具目录当作材料包。
需要独立补审的能力使用不同目录；同目录材料共享资源包，修改其中任何文件会使共同引用它的评审域失效。

审批摘要包含能力及来源、状态、断言、资源包、逐点复用声明和 Profile。修改后对应域失效；新增状态同时使发现检查点重新打开。
approve 保存材料快照及原始路径映射，批准绑定完整资源包。
打开已有 run 先校验已保存合同的完整性；来源、材料或 Profile 缺失时，status 展示受影响的阻塞状态，
review-packet 保留缺失原因，inspect/revise 仍可用于诊断和修正。init/revise 提交的新合同仍须通过完整校验。
build 必须依赖全部 implement，防止通过构建分支绕过审核。

### 联合审批展示入口

CLI `review-packet` 保留 JSON 评审包字段，并返回 `html_path` 与 `packet_path`。
每次在 run 的 `review-packets/` 下生成独立 HTML 与材料快照，展示三项审核结论、input_digest、
需求/技术/UI 原稿、状态来源和基准、缺口、逐点断言及当前结果。源资源按已有归档协议核对哈希后复制；
输出不放进源材料 bundle，避免生成报告反过来使待审摘要失效。缺失材料显示原因，生成失败不留下半成品入口。
展示快照不是批准记录；材料变更后重新生成并展示新 HTML，approve 仍校验当前摘要和真实人工意见。

## 人工决策

discover 通过后，一次调用 approve 可提交多个评审域，三项分别决定：

```json
{
  "gate":"joint",
  "actor_type":"human",
  "reviewer":"实际审核人",
  "summary":"对当前材料的真实审核结论",
  "evidence":["/run/human-review-original.txt"],
  "reviews":[{
    "capability_id":"photos",
    "input_digest":"review-packet 中该域的当前摘要",
    "decisions":{"requirements":"approved","technical":"approved","ui":"approved"}
  }]
}
```

决策为 approved/rejected；任一项未批准，implement 保持 awaiting_human。材料失效后重新生成 packet 并补审受影响域。
demo 强制 actor_type=demo，真实 run 强制 human；本机记录不提供人类身份认证，调用方须保存真实批准依据，不得自动生成真人批准。

全部点及 Agent review 通过后，使用同一 approve 命令提交 `gate:final`，含当前 final_review.input_digest、decision、reviewer、summary、actor_type、evidence。
final 摘要绑定合同、联合批准与全部任务收据；重验、补证或修改材料都会使对应最终批准失效。

## 点级执行结果

contract/device/visual 任务返回 points：

```json
{
  "points":[
    {
      "acceptance_id":"photo-source-functional",
      "status":"checked",
      "observations":[{"assertion_id":"gallery-entry","actual":true}],
      "evidence":["/run/attempts/device-123/photo-source.png","/run/attempts/device-123/actions.jsonl"]
    },
    {"acceptance_id":"photo-cancel-functional","status":"blocked","reason":"前置权限状态不可达"}
  ],
  "context":{
    "device_id":"actual-device","system_version":"actual-version","app_id":"actual.bundle",
    "account_ref":"test-account-alias","package_sha256":"request 中的当前包哈希",
    "normal_entry":true,"synthetic":false
  },
  "phase_seconds":{"navigate":1.2,"wait":0.4,"assert":0.1,"capture":0.2}
}
```

适配器只报告 checked/not_checked/blocked；通过与失败由引擎比较合同预期决定。非 checked 必须给原因且不能混入观察值。
缺少某点或某个必验断言会显示 not_checked；已检查但不匹配显示 failed；未知/重复点、重复断言或非法证据为执行协议错误。
单点失败不抹去同一批次中其他有效点的结果，但任务保持 needs_diagnosis，下游和最终门禁不通过。
每个 checked 点都必须明确引用自己的非空证据；native 点各自需要截图与命令日志，不能仅提交一份未映射的全局截图列表。
同一状态的截图和公共命令日志可以被多个点引用，但真实动作、环境核对和检查必须由适配器完成。

`request.dependency_receipts` 提供直接依赖收据的路径与哈希。视觉任务可声明依赖 device，并复用其当前包原始采集完成比较；
设备动作不必重复。输入变化仍沿依赖失效。build 结果仍采用 package_path/evidence 格式，产物必须产生在本次 attempt 内。
配置内置 `visual_comparison` 时，checked 点还须附上其比较报告的 `{path,sha256}` 文件记录，并列入 evidence；
引擎重新计算真实指标，核对声明基准、同状态设备原图和已审 regions，不能只提交布尔观察值或外部评分。
适配器正常完成观察时退出 0，即使某点不匹配或受阻；基础设施失败则非零退出，此时整次结果不授予点级通过。

## 逐点恢复

默认任务是一个完整套件，每次执行覆盖全部点。只有前置条件可独立重建、验收点之间没有必须连续执行的语义时，
才能在 task 上声明 `independent_points:true`；这项声明参与联合审批摘要。
原生独立套件还须报告 `context.environment_digest`，由适配器根据当前 fixture、配置和前置状态生成，不能照抄旧值。

独立套件的每点收据分别保存观察、输入摘要、环境和原始执行证据。相同输入、包、完整 context 且证据有效的通过点可以保留；
新报告的失败会覆盖旧通过，任何包、依赖或环境变化都禁止合并旧结果。协议错误或非零退出不新增通过点。
`run --points POINT_ID ...` 选择独立点的任务及依赖闭包，`--points` 与 `--tasks` 互斥；最终必验范围不变。

request.acceptance 仍是完整清单，`requested_acceptance_ids` 是本次需要执行的点，`reuse_context` 是可复用结果的环境。
适配器须先核实当前环境；不匹配时重新执行全套，或仅报告本次实际检查的点并保留其他缺口。引擎不会跨环境合并。
普通套件的 requested_acceptance_ids 始终包含全部点。独立套件省略 --points 时自动选择尚未通过的点。

## 中断恢复与预算

request.idempotency_key 绑定 run、task 和输入摘要；`point_idempotency_keys` 为每个验收点提供稳定子键。
同输入重试保留这些键，补验独立点时使用对应的点级键。适配器应将其传入业务接口或持久化动作日志，核对已经完成的副作用；
CLI 提供稳定标识和收据恢复，不代替业务系统实现 exactly-once。

进程已成功退出并写出完整结果、但父进程尚未提交收据时，使用 `reconcile --action accept --task ... --reason ... --evidence ...`。
它校验终态进程记录、原始 request 哈希、当前输入与依赖、命令日志、包、点和证据，然后补交收据，不再次调用适配器。
执行或持久化出现 I/O 错误时保留原 attempt 为 in_doubt，补交收据失败不会清除恢复入口，也不会累计未提交的点引用或阶段耗时。
输入漂移、结果不合法或进程仍无终态时不能接纳。中断 attempt 已有 result.json 或 process.json 时，retry 会要求先 reconcile。
确认业务现场允许重新执行后，可用 `reconcile --action retry` 留下原因与证据，保留旧目录并允许新 attempt；它不授予旧结果通过。
若 process.json 停留在 running，显式 retry 会先校验原 request 与进程记录归属，再在 POSIX 系统探测原 PID 和进程组均已消失；
任一仍存在（包括 PID 被复用）或无权限核验时拒绝重试。探测结果随恢复事件留档，不改写旧进程记录，不推断退出码，也不自动重新执行。
该缺失终态恢复目前支持 macOS/Linux；其他平台或缺失有效 PID 时保持 in_doubt。业务副作用是否允许重复仍须由诊断证据确认。
没有结果和进程记录的中断仍先核对进程和外部副作用，再使用 retry。operation-id 重放不会重复执行已记录的恢复操作；
若恢复操作本身写入失败，修复存储后使用新的 operation-id 重试 reconcile。

max_attempts 限制同一输入摘要的失败次数（包括未完成点、协议/进程失败及明确放弃的中断），成功执行不占失败预算。
累计 attempts 和耗时始终保留；retry 不清零失败次数，合同修订不能调整原 max_attempts。新输入有独立失败预算。
同输入预算耗尽后，可使用 `extend-budget --task ... --additional 1 --reason ... --evidence ...` 留档追加 1..20 次；
追加只适用于当前摘要，随后仍须完成诊断并 retry。预算扩展不改变任何审批或验收结果。

## 续行建议

CLI 对当前 run 的命令返回增加 `continuation`；`capabilities.continuation_advice=true` 表示该入口已接入。
覆盖 status、run、inspect、coverage、report、verify、审批/恢复命令及操作 ID 重放；命令错误返回
`diagnose_command_error`，先核对操作是否已完成，不能盲目再次执行。参数解析的 usage 错误仍遵循 argparse。

```json
{
  "continuation": {
    "status": "must_continue",
    "can_end_turn": false,
    "stop_reason": null,
    "next_action": {"action":"diagnose_task","owner":"agent","task_id":"contracts","command":"inspect"},
    "pending_actions": [],
    "blocked_tasks": [{"task_id":"device","root_task_ids":["contracts"],"external_block_confirmed":false}],
    "reusable_task_ids": ["build"],
    "passed_acceptance_ids": [],
    "scope": "whole_feature",
    "background_worker": false,
    "replay_saved_commands": false
  }
}
```

`pending_actions` 实际包含 next_action 及其余待办；示例省略该重复内容。
动作名和 command 是建议，不是自动执行命令或新授权。记录、重试、预算扩展和审批仍走原协议。

| 当前事实 | 续行行为 |
| --- | --- |
| ready/stale、agent_required | 执行当前输入的任务或完成 Agent 工作；复用有效依赖 |
| needs_diagnosis、缺输入、预算耗尽 | Agent 诊断、补证或修复后依原协议继续；不因下游 blocked 结束 |
| in_doubt | 只读检查已有进程/结果，优先 reconcile；不自动重放 |
| 某域待人审，其他域可执行 | Agent 独立工作排在人工动作之前，can_end_turn=false |
| 仅剩联合人审 | 先准备与当前摘要匹配的 HTML 和完整归档；材料存在且归档哈希有效后才允许请求人审 |
| 最终人工复核可进行 | handoff 生成当前源码和结果绑定的开发交接 HTML，并记录知识回灌完成依据或不适用原因；缺失/失效时先重建 |
| 全任务/点/最终人审通过 | 交接为 current 后 complete；demo 则仅 demo_complete，verify 仍不能返回真实就绪 |

局部批次或选择的点完成后，建议仍检查完整功能。独立的用户局部请求以用户限定范围为准。
`reusable_task_ids` / `passed_acceptance_ids` 仅列当前有效结果，不能跨输入或包复用。
`awaiting_human` 允许交出已经准备好的审核材料，不表示迁移完成。现有有效审批不会重新请求。
错误字符串中出现“设备不可用”“需要用户”不构成外部阻塞证明；会话层按 [LIFECYCLE.md](LIFECYCLE.md)
登记绑定证据、范围和有效期的结构化 hold。保存检查事实并完成全部独立工作后，再由 end-check 判定是否可等待；
用户明确暂停始终优先。

此层只分析状态，不改变引擎 Gate、收据、失败计数或命令退出码，不是后台调度器。
continuation.py、CLI 和报告展示变化不纳入执行输入指纹；核心引擎与验证器的指纹规则保持原样。
升级此层不应令业务包和审批失效，部署时仍须对当前 run 比较指纹和验收结果。

## 开发交接

命令 `handoff --run-dir ... --input ...` 接受 `developer-handoff/1` JSON，返回 `html_path`、`packet_path` 和有效性状态。
字段、正文结构及收尾步骤统一见 [HANDOFF.md](HANDOFF.md)。输出为所选 Harmony 产品工程内的自包含 HTML，
机器输入和证据索引保存在 run 内；不会复制安装包、原始日志或整套截图。
各运行命令均返回 `developer_handoff`。缺失或过期时，continuation 的 Agent 动作为 `prepare_developer_handoff`，
不能用旧的 report-presentation.json 或执行 Markdown 代替。已有执行输入及人工批准的判定规则保持原样。

## 限定范围收尾

用户明确把交付验收收窄为主路径或选定异常时，可使用独立的 `closeout` 记录。
这不是删改既有合同或把未测点设为 passed；原 coverage、预算、联合审批、final gate 和 verify 不变。
普通局部批次完成不能自动创建此记录。

`closeout --run-dir ... --operation-id ... --input packet.json` 接受两种 action：

- `prepare`：包含 `authorization:{actor_type:"human",instruction,evidence}`、当前 `build_task_id`、实际已测 `candidate`、
  `checks:[{id,status:"passed",summary,acceptance_ids,evidence}]`、`excluded_acceptance_ids`、`exclusion_reason`、
  `agent_review:{summary,findings:[],evidence}`、`cleanup`、`limitations` 和最终 `report` HTML 路径。
  checks 映射与排除清单须完整覆盖原验收 ID。candidate 须与当前有效 build 相同，或 HAP/HSP 的全部内部文件逐字节相同。
  新 prepare 的 report 必须是 handoff 当前生成的开发交接 HTML。既有归档仍可读取，其后续建议会要求补齐当前开发交接并重新绑定范围材料。
- `decide`：包含 `actor_type:"human"`（demo 必须为 demo）、`reviewer`、实际人工 `summary`、`evidence`、
  prepare 返回的 `input_digest` 及 `decision:"approved"|"rejected"`。

准备后 `delivery.status=awaiting_human`，continuation 请求 `scoped_final_review`；只有实际人工批准后才为
`scoped_complete`，demo 为 `demo_scoped_complete`。证据、合同、源码或构建收据变化后为 stale，恢复 Agent 修复待办。
`delivery-closeouts/` 保存不可变材料与决定，`delivery-closeout.json` 指向当前记录；同操作 ID 不接受不同输入。
该记录独立于全量执行状态，`whole_feature_continuation` 始终保留原全量待办，verify 不会据此返回 delivery_ready。
本地输入不能认证人的身份，Agent 必须保存真实用户原话，不得把调整范围当成最终验收批准。

## 耗时与外部评价

phase_seconds 可省略，允许 setup/build/install/navigate/wait/assert/capture/compare/cleanup；值为有限非负秒数。
阶段必须不重叠，总和不能超过当前进程实测耗时（容许 0.05 秒计时误差）。不填时保留未知，不估算。

Agent 可使用 `timer --id analysis-1 --phase discovery --action start|stop --operation-id ...` 记录明确区间。
允许 discovery/review_preparation/review_wait/implementation/diagnosis，同一 run 同时仅一个 Agent 区间。
metrics 将进程类别、适配器阶段和 Agent 墙钟区间分开显示，不相加推断模型活跃耗时或 token 成本。

checked 点可附 `evaluations:[{provider,version,metric,value,maximum,evidence}]`。分值为有限数值，0 <= value <= maximum，maximum > 0。
评价证据必须已列入该点 evidence。引擎保存评分供复核，但任何分数都不能抵消缺失断言、失败点或人工门禁。
接入方负责评分含义、校准与算法实现；目前没有绑定 Huawei 的私有接口。
