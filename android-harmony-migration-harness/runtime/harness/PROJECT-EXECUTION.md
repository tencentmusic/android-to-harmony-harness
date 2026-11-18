# 项目接入、执行与恢复

这些能力属于现有工程接入、构建、测试、真机、视觉和收尾模块，不增加迁移阶段，不改变业务审批顺序。
首次工程接入时识别宿主、HAP/HSP 模块、工具链及适用命令，实际验证后保存 Profile 与收据。
后续任务读取 Profile，按引用摘要检查有效性；源码与候选包仍按本次合同验收。

## 项目 Profile

原有 `name/modules/knowledge/tools` 保持兼容。`tools.available` 只代表能找到可执行文件。
新增可选 `execution_capabilities`，每项格式如下（路径是待配置的示意值）：

```json
{
  "id": "hsp-tests",
  "version": "1",
  "tool_ids": ["python", "hvigor"],
  "inputs": [
    {"root": "harmony", "path": "build-profile.json5"},
    {"root": "harmony", "path": "component/shared/build-profile.json5"},
    {"root": "tools", "path": "hsp-tests-recipe.json"},
    {"root": "tools", "path": "hypium-driver.py"}
  ],
  "probe": {
    "cwd": "tools",
    "argv": ["/absolute/python3", "/absolute/runtime/harmony_adapter.py", "{request}", "{result}", "/absolute/project/tools/hsp-tests-recipe.json"],
    "timeout_seconds": 600
  },
  "command": {
    "cwd": "tools",
    "argv": ["/absolute/python3", "/absolute/runtime/harmony_adapter.py", "{request}", "{result}", "/absolute/project/tools/hsp-tests-recipe.json"],
    "timeout_seconds": 600
  }
}
```

`inputs` 精确列出影响该能力的工程配置、锁文件、SDK 版本文件、适配器及其依赖。
业务源码由任务的 `inputs/production_inputs` 追踪，不放入工程接入摘要。
签名配置只保存位置引用和不含秘密的配置摘要；凭据保留在现有安全存储中。
工具可执行文件、实际 probe/command 程序及显式脚本参数也参与摘要。工具升级、适配器、配置或版本变更使相关能力失效。
只有命令退出成功、probe 回报当前 `input_digest`、`verified=true` 且证据存在并未变化时，才标记 `verified`。
Profile 可声明稳定 `project_id`，收据缓存按该身份查找；旧 Profile 继续按绝对路径隔离。
改 Profile 文件名可复用同一项目的有效收据，工程/worktree 根变化仍需重验环境，不跨路径无条件继承。
能力可选 `environment_keys` 列出影响工具调用的环境变量名，只存取值摘要，不记录秘密原文。
收据缓存位于用户缓存目录，可用 `MIGRATION_HARNESS_PROFILE_CACHE` 指定位置。
缓存放在源码、评审材料和 run 之外，避免接入活动污染审批与构建输入。

项目接入命令不需要先创建迁移 run。`roots.json` 是 `android/harmony/tools` 到本地工程目录的 JSON 映射：

```bash
python3 scripts/harness.py project-discover --root /path/harmony --project-id stable-project --deveco-home /path/DevEco --output /path/onboarding-draft.json
python3 scripts/harness.py project-context --profile /path/project-profile.json --roots /path/roots.json
python3 scripts/harness.py project-verify --profile /path/project-profile.json --roots /path/roots.json --capabilities signed-build hsp-tests
```

project-discover 通过 JSON5 解析器读取模块配置，识别 HAP/HSP 并生成 Profile 草稿、测试构建参数与缺项；
不执行 probe，不推测签名包路径或私有测试结果。先核对输出的 profile、模块映射和 recipe_templates，
再配置真实命令，使用 project-verify 获取收据。JSON5 解析依赖团队 requirements.txt 的 json5。

`project-context` 与任务 `context` 只读检查，绝不自动安装或操作设备。
`project-verify` 显式执行所选 probe，保留请求、进程、结果、日志和一次性接入成本。
接入进程中断后先 `project-reconcile --capability <id> --action accept --reason <诊断> --evidence <证据>`（同样传入 profile/roots），核对原请求和完整结果，不重放安装等动作。
进程已中断但结果未完成时，使用 `--action retry` 明确恢复后再验证：未启动的调用可直接恢复；
已启动的调用须通过 POSIX PID 与进程组不存在检查，Windows 已启动进程的此类恢复仍需额外平台支持。
确需重试时使用 action=retry；需要终态进程身份并确认原进程与进程组消失，不可验证时保留未决状态。
只选择失效或新增的能力；命令退出 0 表示本次所选项验证成功，其他未选项可以仍待接入。
demo 收据与真实模式隔离；项目 ready 不授予业务验收或交付门禁。

## 任务消费

执行任务可声明 `project_capability_ids`。缺少验证只阻塞依赖该能力的任务，其他独立任务继续。
`project_command` 可从所选能力绑定已经验证的命令，省去每个任务重复维护 argv：

```json
{
  "project_capability_ids": ["hsp-tests"],
  "project_command": "hsp-tests"
}
```

合同编译会固化该命令；命令修改后明确 revise 绑定，避免静默替换执行内容。
执行请求的 `project_execution` 提供当前验证收据与调用配置。
旧 Profile 没有这些字段时仍可加载，`project_readiness.status=not_configured`，不把清单冒充验证通过。

可选 execution_presets 提供 artifact-build、guarded-device、guarded-visual、network-transport 和 semantic-detector。
分别要求配置 build_manifest、device_guard、device_guard、network_exit、detectors；预设按任务 kind 校验。
后两项适用于该任务的全部验收点，只有部分点需要时应按相同风险分组声明任务。
`execution-plan --run-dir ...` 列出每个任务 required/verified/missing，verified 表示能力 probe 当前有效且所需字段已配置，
实际执行、自检与验收仍需完成。缺配置只使消费任务 input_missing；预设不自动猜测业务条件或出口参数。

## Harmony 生命周期适配器

`harmony_adapter.py REQUEST RESULT RECIPE` 通过同一执行器运行项目 recipe。
每个 step 声明 `id/phase/command`，phase 沿用现有九种阶段；命令使用 argv，无 shell 拼接。
command 的 `cwd` 指根名，可用 `cwd_path` 指其内部工程目录。
独立参数 `{android}/{harmony}/{tools}/{output}/{request}/{result}` 替换为真实路径，`{output}` 是本次 attempt。
各 step 自动保存进程和耗时；失败后执行 cleanup step，并返回原因类别和恢复位置。

设备测试 recipe 可加 `test_module`：

```json
{
  "test_module": {
    "root": "harmony", "cwd_path": "apps/live",
    "name": "shared", "type": "hsp",
    "build_argv_prefix": ["/absolute/node", "/absolute/hvigorw.js"]
  },
  "steps": [
    {"id": "install-tests", "phase": "install", "command": {"cwd": "tools", "argv": ["/absolute/python3", "hypium-driver.py", "install", "{output}"]}},
    {"id": "run-tests", "phase": "assert", "command": {"cwd": "tools", "argv": ["/absolute/python3", "hypium-driver.py", "run", "{request}", "{output}"]}},
    {"id": "restore-app", "phase": "cleanup", "command": {"cwd": "tools", "argv": ["/absolute/python3", "hypium-driver.py", "cleanup", "{output}"]}}
  ],
  "normalized_result": "hypium-results.json"
}
```

HSP 设备测试选择 `genOnDeviceTestHap`；HAP 选择 `assembleHap`，目标模块均为 `module@ohosTest`。
`build_argv_prefix` 可直接采用团队包 doctor 返回的 Node/Hvigor 参数数组；Windows 使用实际 `node.exe` 与 `hvigorw.js`，
路径含空格时仍为独立参数。旧 recipe 的单字符串 `hvigor` 保持兼容，也可传入参数数组。
宿主、设备、测试安装、启动、收集和清理由一次接入的项目 driver 确定，具体工具参数仍需该工程实测。
driver 将真实测试结果转为合同的 points 格式，放到 `{output}/hypium-results.json`；probe 使用独立 recipe 或按请求 format 输出真实断言的 `verified` 结果。
适配器不推测 Hypium 私有结果格式，不用“命令成功”替代断言成功。

签名构建 recipe 可声明 `modules`，每项包含 `root/path/archive_name/signature_evidence`。
前置 steps 运行构建和签名检查；`signature_evidence` 是本次 output 内生成的 JSON，包含
`verified=true/package_sha256/verifier`，必须绑定对应 HAP/HSP 的实际哈希。
`package_manifest` 使用明确的模块清单生成本次 `candidate.app`，按模块校验签名收据与打包字节。
签名算法由项目可信验签工具执行，共享适配器负责核对证据和包身份。
构建任务加 `execution_policy.build_manifest=true` 后，执行器强制检查模块清单及其证据。
不以扫描通配文件或修改时间选包，Profile 中的基线构建通过也不能替代本次候选构建。

## 网络、设备与检测器约束

任务可选 `execution_policy`；这些规则进入执行输入与相关联合评审摘要，现有验收点与断言仍完整保留。

| 字段 | 内容 | 校验 |
| --- | --- | --- |
| `network_exit` | 验收点 ID 到预期 `url/headers/query/method/body` 的映射 | point 的 `network_capture` 引用本点证据；检查真实最终请求，覆盖重复、大小写冲突的 header |
| `device_guard` | 当前设备、包、前台、方向及适用的账号/开关/房间/对局条件 | point 的 `suite_receipt` 保存逐次新采集，逐项校验环境与包，布局使用时距采集不超过 3 秒 |
| `detectors` | 验收点到 rule、正反例 samples、可选 baseline 的映射 | 执行前实际运行检测器自检，执行时再检查本点 layout_capture 与截图哈希及语义结构 |

网络 capture 格式为 `boundary="transport"`、完整 URL、method、必要 body 和 `headers=[[name,value],...]`。
capture 另带当前请求 `input_digest` 与采集 `captured_at`；网络和设备采集时刻都必须落在本次进程的开始、结束区间内，旧证据不能仅凭内部时间差小而重放通过。
保留生产参数构造与拦截器，只在最终传输处截取并替换响应。采集应脱敏且保留断言所需字段；禁止保存鉴权秘密。
执行器独立核对最终请求，即使 adapter 观察值自报成功，也不能掩盖出口参数冲突。
这个协议不能自行证明外部 driver 接在了真实出口，项目接入需审查该截获位置。

`DeviceSuite` 接收一次接入的 `observe/act` driver。observe 每次返回全新
`capture_id/captured_at/context/controls/evidence`；context 包括实际安装包哈希及适用动态条件。
controls 使用语义 ID、visible、enabled、hit_testable、occluded 和 effect。
action 重新定位唯一可触达控件，只允许 navigation/read/selection；消费等动作停止并采集诊断。
每次动作后再采集；wait 按条件和期限轮询；中途换对局、包或方向返回 environment_changed 与恢复步骤。
`run` 将同路径多个点连续执行，逐点写收据，失败后其余点为未检查。安全恢复和同包复用仍走原有依赖/逐点恢复规则。
声明式 JSON、逐步收据与 pending action 恢复见 [DEVICE-SUITES.md](DEVICE-SUITES.md)。

检测器 rule 使用 `required_controls/forbidden_controls/context`，根据控件结构和目标身份判定，不依赖固定昵称。
samples 为 `{role,input,expected}`，input 是标准 artifact 引用，必须有已确认正例和相似错误态反例。
可选 baseline 包含 `image/layout` 引用；image 必须属于本点声明的 Android 基准，layout 绑定 image_sha256 并满足语义规则。
基准取错为活动弹层或检测器假阴性时，报告 tool_failure，先修复检测工具或材料。
当前 point 的 `layout_capture` 同样绑定本点截图。基准的来源语义与原始布局可信性仍由采集适配器和人工核对。
模型断言、真实请求出口、受控设备响应、正常服务 UI 的证据层级仍按测试计划分别声明，不自动互相提升。

## 问题记录与计时

adapter 的未检查或受阻 point 可附 `failure={category,code,step,reason,recovery_point}`。
category 为 `precondition_missing/tool_failure/product_failure/environment_changed/evidence_missing/unknown`。
执行器提供分类恢复建议；环境和工具故障不会自动变成通过或业务缺陷。
问题指纹由能力、任务种类、类别、稳定 code 与 step 组成，不包含 operation ID 或源码版本。
问题保留累计 attempts 与 diagnoses；相同诊断和证据重复 retry 会被拒绝，连续三次相同问题即使换输入仍需诊断。
原有输入级预算、未决进程 reconcile 和人工追加预算规则继续生效。

metrics 记录受控执行的逐次进程、阶段、execution/rework 成本、复用的项目接入收据及问题历史。
同一任务失败后的执行自动关联问题；为设备问题返修而重新构建等跨任务工作，用 `run --problem-ids <问题ID>` 显式关联，避免靠时间先后猜测因果。
项目接入是历史成本，与本次候选执行分开。已有历史缺失数据不回填，未登记外部耗时为 null/unknown。
review_wait 与独立 Agent 工作可重叠；Agent 区间另给去重墙钟，不能把区间和执行器秒数直接相加。
登记匹配 gate、范围和摘要的实际审批决定时自动结束对应 review_wait；部分范围决定不会关闭其他范围等待。
ended_at 是登记时间，不冒充用户实际回复时间。计时从显式 start 开始，准备材料前就应启动 preparation timer。
