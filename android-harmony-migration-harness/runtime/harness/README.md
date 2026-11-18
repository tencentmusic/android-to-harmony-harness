# Migration Harness 联合评审与逐点验收

唯一公开入口：`scripts/harness.py`，合同与能力格式为 `migration-harness/1`。
通过 `android-to-harmony-feature-rollout` 技能加载。旧格式不加载其他引擎，也不转换原始审批和证据。
要求 Python 3.9+；工程 JSON5 发现使用团队依赖 json5。真实部署、发布与人工批准沿用当前任务授权和审核规则。

本目录是唯一维护源，发布包直接包含本目录；`scripts/refresh_release.py` 刷新能力和发布哈希。
下文命令均在团队包根目录执行。

## 已实现

| 能力 | 执行规则 |
| --- | --- |
| 联合审核 | 每个能力同时提交需求、技术、UI 资源包及完整状态映射；三项分别结论，绑定全部资源哈希并留存审核快照；未批准时实现任务为 awaiting_human |
| 按变化生成 | 新任务先声明变化与影响路径，plan 编译相关场景、选择验证方式并按套件批量执行；见 TEST-PLAN.md |
| 状态覆盖 | 需求状态与所选场景分开，分组点保留覆盖映射；显式验收合同保持逐状态设备与视觉规则 |
| 续行建议 | CLI 的 continuation 区分 Agent 待办、依赖阻塞与已准备好的人审；局部失败、批次上限及报告生成不被当成完整任务结束 |
| 会话与结束检查 | 保存本轮请求、范围、外部等待、检查点和停止回执，resume 只读恢复；宿主显式调用结束检查，见 LIFECYCLE.md |
| 声明式套件 | JSON 步骤与观察、逐步收据和显式中断恢复，见 DEVICE-SUITES.md |
| 批量执行、逐点结果 | 一次执行多个点，每点提供观察值和证据；声明独立的点可在同输入同环境下补验，缺点或缺断言显示 not_checked |
| 内置视觉比较 | 独立 capture-screen/compare-visual 与任务 visual_comparison 共用真实 SSIM/MAE、必验 ROI、动态 mask 和差异图；详见 VISUAL.md |
| 最终人工复核 | Agent 全范围复核通过后，还要有人对当前产物与证据确认；记录自动检查不等于人工批准 |
| 开发交接 | handoff 生成自包含 HTML 到所选鸿蒙工程 androidtoharmonyharnessdoc/；未完成项前置，收尾校验知识回灌依据，详见 HANDOFF.md |
| 项目上下文 | Profile 声明模块、知识材料、工具用途与 argv；context 返回可用性和来源，任务可关联 tool_ids |
| 项目接入复用 | project-verify 验证并缓存项目能力；后续任务只检查相关配置、工具和适配器摘要；见 PROJECT-EXECUTION.md |
| 执行防护 | 可选最终网络出口检查、检测器正反例、连续设备套件及显式签名产物清单，仍使用现有任务和逐点门禁 |
| 执行优化 | 低成本契约检查优先；--tasks 定向选择依赖闭包；复用仍有效的已完成任务；依赖收据可供视觉适配器复用同包采集 |
| 耗时 | 执行器类别累计时间、适配器细分阶段时间、显式 Agent 墙钟计时分别统计 |
| 评分接入 | 每点可附 provider/version/metric/分数/证据；评分不替代必验断言，不直接授予通过 |

## 入口

在团队包根目录执行：

```bash
python3 scripts/harness.py capabilities
python3 -m unittest discover -s runtime/harness/tests -v
python3 runtime/harness/examples/demo.py --output /tmp/migration-good
python3 runtime/harness/examples/demo.py --output /tmp/migration-omitted --omit-popup
```

示例目录必须不存在。正常示例返回 `demo_only`，示例程序退出 0；漏验示例显示弹窗视觉 `not_checked`，退出 1。
两者均为合成来源、模拟评审和设备证据。CLI `verify` 对任何 demo 均退出 1，无法产生真实交付结论。

真实迁移由 [AGENT.md](AGENT.md) 驱动，合同与适配器格式见 [CONTRACT.md](CONTRACT.md)，共建边界见 [EXTENSIONS.md](EXTENSIONS.md)。
项目首次接入、Profile 能力复用、受控执行器与分类恢复见 [PROJECT-EXECUTION.md](PROJECT-EXECUTION.md)。
宿主接入、结束检查、恢复与新增耗时见 [LIFECYCLE.md](LIFECYCLE.md)。CLI 不自动安装 Codex hook。
通用 Agent 的 CLI/SDK 桥接、托管结束控制、预算和断连恢复见 [AGENT-RUNNER.md](AGENT-RUNNER.md)。
新任务先读 [TEST-PLAN.md](TEST-PLAN.md)，确认 `change_driven_test_plan=true` 后编译计划；模型完成语义分析，CLI 确定性生成验收合同。

恢复或准备结束对话时读取 `status.continuation`。`can_end_turn=false` 表示 Agent 仍应执行
`next_action`，不是输出一份“未完成”报告就结束。用户询问进度不撤销原授权。已有人审批准沿用，
只有当前材料尚未批准时才交出已生成的审核入口；真实外部阻塞需独立诊断证据。
能力检查须包含 `continuation_advice=true`，避免源码有修复而当前 CLI 安装仍未接入。
此功能不启动后台工作，也不改变 verify、验收或审批规则；[详细协议](CONTRACT.md#续行建议)。

```bash
python3 scripts/harness.py audit --contract /path/to/old-contract.json
python3 scripts/harness.py plan --contract /path/to/draft.json --input /path/to/test-plan.json --output /path/to/planned.json
python3 scripts/harness.py start --contract /path/to/contract.json --run-dir /path/to/new-run
python3 scripts/harness.py context --run-dir /path/to/new-run
python3 scripts/harness.py review-packet --run-dir /path/to/new-run
python3 scripts/harness.py approve --run-dir /path/to/new-run --operation-id joint-1 --input /path/to/human-decision.json
python3 scripts/harness.py run --run-dir /path/to/new-run --operation-id batch-1
python3 scripts/harness.py coverage --run-dir /path/to/new-run
python3 scripts/harness.py report --run-dir /path/to/new-run
python3 scripts/harness.py handoff --run-dir /path/to/new-run --input /path/to/handoff.json
python3 scripts/harness.py metrics --run-dir /path/to/new-run
python3 scripts/harness.py verify --run-dir /path/to/new-run
```

`audit` 只读报告显式验收合同缺口，不导入旧审批和 passed。`review-packet` 默认生成联合审批 HTML，返回 `html_path` 和 `packet_path`，同时保留原 JSON 字段；`report` 生成 run 内的中文 Markdown。
最终开发交接使用 [HANDOFF.md](HANDOFF.md) 的 `handoff`，能力标记为 `developer_handoff_html=true`。
该 HTML 与机器执行报告分别生成；单独 report 不满足最终交接要求。输出目录以本次鸿蒙产品的 DevEco 工程为准。
HTML 位于 `run/review-packets/<唯一编号>/index.html`，集中展示需求、技术、UI、状态来源、基准缺口、断言及各域摘要。
材料及其完整 bundle 复用审批归档校验复制到同目录，保留相对资源路径；生成过程不修改源材料、审批或验收状态。
Markdown/文本以完整原文嵌入，HTML UI 在禁用脚本和联网的沙箱中预览；交互效果仍须独立验收。
每次生成独立快照；材料变化后重新生成并将新的 HTML 链接交给审核人。缺失材料的域仍显示失效原因，不阻断其他域查看。
准备好材料后才发起人工审核；不会把“同意开发工具新版”当作对某项业务方案或 UI 的批准。

## 执行与证据

执行期间同一 run 加锁，任务串行，避免单设备竞争。
`run --tasks visual-popup` 仅执行指定任务及其依赖中尚未通过的可执行任务，仍需经过相同的人工门禁；不绕过失败、不缩减最终必验范围。
`retry` 需诊断原因与证据。失败预算按输入摘要累计，成功迭代不消耗失败预算；累计执行次数始终保留，追加预算使用 extend-budget 留档。
中断或收据落盘失败后已有完整结果时，优先 reconcile 校验并补交收据；已有结果或进程记录时 retry 会要求先核对恢复路径。
进程终态未落盘时，macOS/Linux 上可用 reconcile --action retry，在确认原进程与进程组消失并提供业务诊断证据后显式重试；旧结果不能直接接纳。
更新来源、材料、候选包或证据后，关联结果失效；没有依据时不跨包继承设备结果。
材料改名或缺失不会关闭诊断入口，status/inspect/revise 可用于定位和修复；新合同仍需完整校验。

多个点可以放在同一 task，复用导航和安装。若视觉任务只需比较已采集画面，将其声明为相应 device 任务的下游，
通过 request.dependency_receipts 读取当前包的原始截图与日志；视觉适配器仍须针对每个 state 独立比较并提交结果。
这不是无依赖的截图缓存：上游证据变化会使下游结果失效。跨设备并发、跨包结果缓存、自动影响分析尚未实现。

显式验收合同修订可以新增状态与验收点，不能删除或改写既有行为/预期，不能清零失败记录。新增状态会使相关发现检查点和联合审核失效。
计划模式支持有依据的合并与不适用项移除，保留历史并保护失败检查，规则见 TEST-PLAN.md；已有显式验收 run 不自动切换模式。
若原需求本身需要纠正，创建新 run 并保留旧记录。审核按 capability 分域；需要独立补审的材料应放入不同资源目录。
逐点补验的声明、环境约束、适配器幂等键和恢复命令见 [CONTRACT.md](CONTRACT.md)。

## 结果边界

通过数是声明的验收点数，不是自动发现的全部需求覆盖率。状态清单的语义完整性、真实设备行为、图像比较与人类身份仍需可信适配器和人工复核。
审批要求身份类型、审核人、原始批准证据、三项结论和当前 digest；本机 CLI 不提供身份认证，不能防止有本地写权限的人伪造材料。
内置确定性图片比较与 HDC 采集，接入方式和限制见 [VISUAL.md](VISUAL.md)。未内置 Huawei SDK、设备身份认证、评分模型、模型 API 或自动知识索引。

退出码：输入错误为 2；`run` 有执行失败/未检查/受阻结果等未决执行任务时为 1；等待人工时可能为 0，须查看状态。
`verify` 仅在真实模式、当前全部任务与验收点有效且最终人工批准时退出 0、返回 `delivery_ready`。

耗时不把等待算作模型活跃时间。细分阶段由适配器报告，与进程时长分开保留；没有计时的数据不推算，也不声称已获得真实项目加速比例。
