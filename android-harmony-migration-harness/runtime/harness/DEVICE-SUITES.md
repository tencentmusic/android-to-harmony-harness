# 声明式连续设备套件

项目 driver 负责真实采集和动作，通用核心负责条件、步骤、证据和恢复。
现有 callback `DeviceSuite.run` 保持兼容；JSON 套件使用 `run_declarative`。
`observe` 返回全新布局/截图及可信上下文，`act` 只操作本次语义目标，约束见
[执行防护](PROJECT-EXECUTION.md#网络设备与检测器约束)。

任务可声明 `device_suite={root,path,locator}`，把模板纳入输入摘要与相关评审，点与断言 ID 必须与合同一致。
模板、driver 及其依赖同时放入任务 inputs，修改模板或换候选包后使用新 attempt。

```json
{
  "format": "migration-device-suite/1",
  "points": [{
    "acceptance_id": "details-device",
    "steps": [
      {"id":"open-details","op":"action","selector":"details-entry",
       "postcondition":{"required_controls":["details-title"]}},
      {"id":"wait-content","op":"wait","timeout_seconds":10,
       "condition":{"forbidden_controls":["loading"],"required_controls":["details-content"]}},
      {"id":"details-ready","op":"checkpoint","condition":{"required_controls":["details-content"]}},
      {"id":"capture-details","op":"capture"}
    ],
    "observations": [
      {"assertion_id":"details-visible","condition":{"required_controls":["details-title","details-content"]}}
    ]
  }]
}
```

`steps` 全局 ID 唯一，操作为 action/wait/assert/capture/checkpoint。action 必须声明后置条件；
wait 使用有限轮询，assert 不匹配报告 product_failure，checkpoint 不匹配报告 environment_changed。
所有条件字段共同满足才为 true：可见控件 required_controls/forbidden_controls、context 精确匹配，
以及 `values=[{path:["key",0,"field"],equals:...}]`。观察值可使用 condition 布尔值或非空 path，
仅导出 assertion_id/actual；最终预期由合同判定，模板不能自报 passed。

```python
suite = DeviceSuite(package_sha256, observe, act, required_context=current_conditions)
result = suite.run_declarative(template, request_path, attempt_dir / "device-suite")
```

同一次调用复用安装、导航和 driver。每步前落盘 pending，完成后写独立不可变 step 收据，
每点导出当前观察、截图/布局证据和 suite_receipt；失败保留步骤、类别和后续未检查点。
声明 device_suite 的任务还须提供 suite_binding，绑定本次 request、模板、引擎、设备与包；
核心独立校验采集时间窗，不能仅登记模板却忽略它的执行证据。required_context 必须包括实际 device_id。
`suite-summary.json` 是默认读取入口，失败时再展开对应 failure、step 和原始采集文件。
checked 代表已完成采集，是否通过仍由 Harness 比对合同中的实际预期。
request.requested_acceptance_ids 存在时只执行所选点；声明 independent_points 的场景应各自具备从当前状态到达目标的步骤与前提。

中断后只在原 attempt、原 request、原模板、原包和环境下显式 `resume=True`。
恢复先重新采集环境和最近 checkpoint，核对所有已保存证据；已完成动作不重复执行。
pending action 需要 `reconcile={"step_id":"open-details","evidence":[...诊断文件...]}`，
并实测声明的后置条件，才可接纳该动作；未满足时保留未决，恢复环境后创建新 attempt。
只读步骤可重新观察，未知动作结果不会自动重试。
若受控失败能确认尚未调用 act（如目标不可用），不保留未决动作；修复前提后可在原 attempt 显式 resume，首次执行该动作。
act 已调用或进程突然退出时仍保留 pending，不能凭错误类别推断动作未发生。
这套恢复仅适用于该 adapter 的原 attempt；跨进程结果接纳仍经过顶层 reconcile 的执行时间窗校验，
换 attempt 不复制旧采集来伪造本轮设备通过。
