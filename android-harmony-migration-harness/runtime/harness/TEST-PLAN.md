# 按迁移变化生成测试计划

新任务使用 `test_plan.version=1`；没有此字段时显式声明逐状态验收。生成器负责确定性展开和校验，
源码语义、影响分析和分组由当前 Agent 完成，CLI 不调用模型、不自动证明相关性。

## 生成顺序

1. 从用户需求、Android 行为与鸿蒙现状确定本次新增、替换、删除和必须保持的兼容行为。
   为每个变化记录 before/after 和稳定的源码/需求分析依据。需求状态清单保持完整，先确定变化再写场景。
2. 沿实际调用、资源或共享实现关系查找受影响结果。只生成这些路径的主流程、关键异常及独立兼容风险；
   未修改且无影响路径的业务、未迁移的源端历史能力登记为不适用，不生成测试。不可达或失败不是不适用依据。
3. 按实现方式和失败风险分组消费者，选代表入口。独立解析、权限、生命周期或不同播放器分别覆盖。
   源码引用/资源完整性用批量审计，真机集中在不同接入模式；保留分组依据与覆盖状态，避免页面乘异常的组合展开。
4. 每个场景选择验证方式：纯逻辑/文件/包体审计用 contract；系统 API 与真实消费链用 device；
   布局、图层、裁切、运动等可见风险才加 visual。同一场景只有独立风险才需要多种方式。
   同一流程的点使用同一 task 批量执行，视觉依赖 device 并复用当前包证据。保留至少一次当前包设备冒烟。
5. 编译计划，检查场景数、方法数、断言数及执行套件数；数量明显偏大时回查重复展开，保留独立风险。
   没有固定用例数量上限，也不把预估套件数当实测执行次数。将计划与需求/技术/UI 一起评审。
6. 实现后对照实际 diff 增量校正。更新稳定的差异分析材料，再编译和 revise；未变化域沿用有效批准。
   执行器批量返回点级结果，Agent 先读取摘要，仅展开失败或未知项；复测遵守同包、同环境和输入有效性规则。

## 数据与编译

草稿合同提供 roots、Profile、capabilities、states、review_scopes 和 task 模板。模板中的验收映射可为空；
plan 会生成 acceptance 和 task.acceptance_ids，移除未使用的测试任务并保留其上游依赖。
源码、评审材料、可执行适配器及依赖仍须满足完整合同校验。输出路径必须是新文件。

```bash
python3 scripts/harness.py plan \
  --contract /path/to/draft.json --input /path/to/test-plan.json --output /path/to/planned.json
```

计划示例片段（实际计划须覆盖每个变化，状态须被场景覆盖或列为不适用）：

```json
{
  "version": 1,
  "changes": [{
    "id": "resource-source", "capability_id": "images",
    "before": "读取包内图片", "after": "读取下载缓存并刷新",
    "sources": [{"root":"tools","path":"scope.md","locator":"资源来源变化与调用链"}]
  }],
  "scenarios": [{
    "id": "image-refresh", "capability_id": "images", "change_ids": ["resource-source"],
    "state_ids": ["image-empty", "image-ready"], "behavior": "下载就绪后刷新普通图片消费者",
    "risk": "异步更新丢失或错绑旧图", "grouping_reason": "共享图片绑定组件的一次加载流程",
    "observable_ui": true,
    "verifications": [{
      "kind": "device", "task_id": "image-suite", "reason": "验证 ArkUI 绑定及刷新",
      "assertions": [{"id":"refresh-current-key","operator":"truthy","expected":true}]
    }]
  }],
  "excluded_states": []
}
```

`sources` 引用真实文件并绑定哈希。用稳定分析快照记录变化依据；避免把即将编辑的业务源码直接作为审批材料，
导致实现过程不断使批准失效。快照需引用来源位置并记录基线，实际执行输入仍覆盖真实代码与产物。

不适用项格式：`{state_id,capability_id,reason_code,reason,sources}`，reason_code 仅支持 unchanged/not_migrated。
同一状态不能同时被排除和覆盖；一个变化至少有一个场景，每个能力仍须有验收覆盖。
`observable_ui=false` 的场景不允许 visual；需要 visual 的全部覆盖状态均须有基准或明确缺口。

编译生成 `<scenario-id>-<kind>` 验收点，含 `scenario_id`、代表 `state_id` 与完整 `covered_state_ids`。
一个点的断言必须支撑全部声明的覆盖范围。原生适配器继续绑定真实设备、账号、当前包、截图和动作日志；
视觉分组中的每个状态都检查其基准。新增字段不改变 checked/blocked/not_checked 与引擎断言判定。

正式截图比较按单状态场景配置 `verifications[].visual_comparison`，由 `plan` 生成任务绑定，
字段与限制见 [VISUAL.md](VISUAL.md)。源设备验收 ID 使用生成后的 `<scenario-id>-device`。
同一个设备套件仍可批量执行多个点，不需要为每张截图重复构建或安装。
未配置比较器的自定义 visual 命令只允许演示模式，不能作为正式验收。

## 计划修订

计划模式在 init 时确定，已有旧 run 升级须创建新 run，旧审批/结果不能直接搬入。
同一 run 保留能力与需求状态历史；原验收 ID 的定义保持不变，改变预期必须使用新 ID。
新计划携带 `revision:{previous_digest,reason,replacements:[],retirements:[]}`，previous_digest 为上版完整 test_plan 的摘要。
`plan` 的摘要输出可用于下一次修订。只修改已有状态基准时沿用原计划即可。

- 合并：`replacements` 每项 `{acceptance_id,capability_id,replacement_ids,reason}`，新点必须保留原方法、能力、覆盖状态和全部断言。
  旧失败可由等价新点重新验证，旧通过不会自动授予新点。
  未解决的失败随替代关系传递，新点通过前不能再通过移除新点绕过失败。
- 移除不适用项：`retirements` 每项 `{acceptance_id,capability_id,reason,sources}`，须有 diff 或范围依据。
  最新已记录结果失败的点即使因源码变化而过期也不能直接移除；先修复复测或保留等价检查。
- 每个移除项恰有一种说明。合同版本、原收据、失败次数和已移除套件的耗时保留；同任务 ID 不能重设失败预算。

计划与证据进入任务和联合审批摘要；修订后依当前输入重新判定结果及审批有效性。
真正由用户缩减交付范围且仍有失败/未测项时，使用现有 closeout 明示剩余限制，不借计划修订获得全量通过。

## 回归依据

自动回归覆盖后台无视觉、分组状态、视觉基准缺口、真实包/证据校验、计划变更失效、失败保护和显式验收。
动态资源案例的离线计划样例验证 42 个库存状态如何映射为选定场景，计数仅代表生成结果，不证明业务验收或节省比例。
