---
name: android-to-harmony
description: "Android→HarmonyOS 跨端迁移专用。仅当用户明确要求将 Android 实现、资源、接口或页面迁到 HarmonyOS/ArkTS，或同一任务同时给出 Android 来源与 Harmony 目标并要求对照、补齐、实现或迁移验收时使用；只有同一迁移事项的连续后续可以继承，新话题必须重新判断。Harmony-only 的代码问答、Bug、ArkTS/Hvigor 编译、ohpm/HAR/依赖、Git 移动合并、普通重构或 Code Review 不使用；仅因出现“分析、修复、接入、编译”等通用词不触发。"
---

# Android → HarmonyOS 统一迁移

本 Skill 是项目路由、架构和基础迁移规范的唯一入口。完整业务功能、房型或大页面迁移必须同时使用当前
`android-to-harmony-feature-rollout` 的可恢复 Harness；不要转调其他旧的 `android-to-harmony-*`、
`harmony-code-gen-*` 或 `ohos-harmony-code-gen-*` Skill。

完整功能包含用户可见 UI 时，由 feature-rollout 接入 `hmos-design-visual-mobile`：先形成
可追溯的 UI Layout Spec 和关键状态 HTML 原型，经人工批准并由 Harness 绑定文件哈希后，才能修改
ArkUI View/Page/Dialog。HTML 是编码前视觉合同，不是 ArkTS 实现、设备验证或 Android 等价证据。
实现后由 feature-rollout 按当前合同提交逐点功能和视觉证据，再完成人工复核与开发交接。
编排、审批和完成条件统一读取该技能；已有任务由同一入口按保存格式恢复。

收到完整功能迁移资料后，读取 `../android-to-harmony-feature-rollout/references/evidence-first-discovery.md`，
在当前任务核查源码与效果，再把事实、证据和建议交人工审核；审核前只做查证与材料，不继续业务
执行。保持原资料输入流程与既有审批门禁，源码能回答的问题先自行查证。

## 完整团队包的路径与运行环境

本目录是完整包的基础技能，工作流与 UI 技能位于同级目录。完整迁移统一使用包根的
`scripts/harness.py`，它选择 `.venv` 中经过依赖锁验证的 Python。包根是本 Skill 上两级目录。
Windows 或解压即用时先读包根 `WINDOWS.md`，可按路径直接加载相应技能，也可使用 `scripts/install.py --register-only` 注册当前目录。第一次使用按包根 README 执行 `scripts/configure.py`；路由读取包根 `.local/projects.json`。
Profile 中 `${HARMONY_ROOT}`、`${ANDROID_ROOT}`、`${BUILD_CWD}`、`${ANALYSIS_OUTPUT_DIR}`、
`${DEVECO_HOME}`、`${KNOWLEDGE_ROOT}` 都须替换为路由返回的 `resolved_paths`，不得把占位符当真实路径执行。
如果项目绑定了双端知识库，Android 查证先读知识索引再查 CodeGraph，见
`references/android-codegraph.md` 与 `../android-to-harmony-feature-rollout/references/fxwiki-android-discovery.md`（文件内容为通用知识库流程）。
已有目标目录可明确配置映射；不按项目文件夹名称猜测，不使用发布者目录。未知项目先查证并新增适用 Profile。

CodeGraph、知识检索器、devecocli 和场景扩展技能是可选增强。未安装时使用包内 MVVM/ArkUI 参考、
目标 SDK/HAR 声明、现有可编译代码和必要的官方文档核实。真实 Hvigor 构建和证据门禁继续执行。
UI 原型可用包根 `scripts/render_prototype.py` 调用本机 Chrome/Edge 渲染；Viewport 以当前 case 为准。

## 0. 先判断是否属于跨端迁移

在路由项目、读取 profile 或进入工作流之前，先判断：

`activate = explicitMigration || contextualMigration || migrationContinuation`

- `explicitMigration`：用户明确要求从 Android/安卓迁到 HarmonyOS/鸿蒙/ArkTS，或明确使用“转鸿蒙、跨端迁移、Android as-is → Harmony to-be”等表达。
- `contextualMigration`：同一任务同时具备可识别的 Android 源、可识别的 HarmonyOS 目标，以及把源端行为对照、同步、补齐、实现或验收到目标端的要求。
- `migrationContinuation`：当前连续任务此前已经通过门禁，后续请求仍服务于同一迁移闭包。新话题必须重新判断，不能沿用旧迁移上下文。

三项均不成立时，退出本 Skill，按普通问答、诊断、开发或审查流程处理；不执行
`route_project.py`，不加载项目 profile、MVVM/ArkUI 参考，不套用五块/六块模板，也不因本
Skill 强制编译。

以下情况不激活：

- 只有 HarmonyOS/ArkTS 代码的问答、业务分析、Bug、编译、依赖/HAR/ohpm、重构、主题/资源/路由调整；
- Harmony 工程内的 HAP/HSP 关系、Git 合并或目录移动、普通 Code Review；
- 只有 Android 代码，但没有迁往 HarmonyOS 的目标或意图；
- 仅出现“分析、修复、接入、改进、重构、编译通过”等通用动作，或仅因当前目录属于受支持项目；
- 用户正在审查、修改或优化本 Skill 本身。

若用户同时提供 Android 源与 Harmony 目标，即使没有逐字说“迁移”，只要明确要求跨端对照或
落地，也应激活；若跨端意图仍不明确，保持普通任务流程。

## 1. 通过门禁后再路由项目

任务开始时必须先定位目标路径：

1. 用户给了 Harmony 目标文件或目录时，使用该路径。
2. 否则使用当前工作目录。
3. Android 源是激活证据，但不用于选择 Harmony profile；路由仍以目标 Harmony 路径优先。
4. 执行：

```bash
python3 <本Skill目录>/scripts/route_project.py --path <目标路径>
```

命中时读取返回的 `profile` 文件，并在整个任务中使用该项目规范。未命中但目标路径已明确时，
使用包根 `scripts/harness.py project-discover` 生成待核对的 JSON Project Profile，读取真实工程规范，
再按统一工作流的 `PROJECT-EXECUTION.md` 验证能力；不要求项目属于下表中的业务类型。
只有目标路径无法确定时才补充确认；禁止套用“相近项目”的网络、路由、主题或编译规则。

当前支持：

| 项目类型（通过本机配置绑定目录） | Profile |
| --- | --- |
| 宿主桥接项目 | `profiles/host-bridge.md` |
| 实时互动项目 | `profiles/interactive-demo.md` |
| 双端功能项目 | `profiles/dual-platform.md` |

## 2. 再判断迁移任务模式

| 用户意图 | 模式 | 必须执行 |
| --- | --- | --- |
| 梳理 Android 业务事实、接口清单或迁移文档，供 Harmony 迁移使用 | Analysis-Only | `workflows/analyze.md` |
| 依据 Android 来源生成或修复 Harmony 代码，接入接口、路由、资源或页面 | Implementation | `workflows/implement.md` |
| 梳理并生成、完整迁移、端到端落地 | End-to-End | 先 Analyze，再 Implement |

“生成迁移文档”属于 Analysis-Only；只有宾语明确是代码、页面、组件、接口接入、资源或路由时才进入
Implementation。

## 3. Analysis-Only 的硬边界

- 默认只完成 Android 业务事实梳理，不设计目标 MVVM、目录拆分或状态架构。
- 只有用户明确要求“补架构设计/MVVM 方案”时，才在正文之后追加架构部分。
- 整个类使用 `templates/whole-class.md`，固定五块。
- 单一功能使用 `templates/single-feature.md`，固定六块。
- 默认输出 Markdown 到 profile 配置的 `analysis_output_dir`。
- 不修改 ArkTS、资源、路由或工程配置，不执行编译，不打包资源；用户另有明确要求时再做。

## 4. Implementation 的架构门禁

所有已通过第 0 节门禁的迁移实现任务都必须先读：

1. `references/official/mvvm/official-skill.md`
2. 新功能读 `references/official/mvvm/references/add-func-workflow.md`
3. 已有代码整改读 `references/official/mvvm/references/refactor-func-workflow.md`
4. `references/official/arkui-develop/official-skill.md`
5. 当前项目 profile

完整功能先检查 feature-rollout 返回的当前审核结论。当前需求、技术、UI 材料的批准有效后才修改
对应业务逻辑与可见 UI；历史案卷按该技能的兼容合同恢复。

代码必须满足：

- 先检查目标业务闭包使用 V1 还是 V2；默认保持现状，禁止在同一组件混用。
- View/Page 只负责渲染、组装和上报用户意图，不直接访问 Model。
- ViewModel 是 UI 业务状态的唯一来源，负责业务编排和状态转换。
- Model/Repository 负责实体、网络、本地数据和响应转换，不放 ArkUI 状态装饰器。
- 数据向下 `Model → ViewModel → View`，事件向上 `View → ViewModel → Model`。
- 同一状态只有一个 owner；可计算值不重复存储，纯 UI 局部状态就近持有。
- 纯展示小组件不为“形式上的 MVVM”强造 ViewModel。
- 必须同时完成 profile 要求的网络、主题、资源、export、路由、宿主参数和回调接线。
- 完整功能必须由 Harness 的构建任务执行 profile 对应的真实 Hvigor 命令并绑定日志和
  ZIP 型 Harmony 包；编译失败或自由文本收据不能声明构建通过。
- 完整功能构建后回到 feature-rollout 执行设备、视觉、功能验收、开发交接与最终复核；
  构建通过只是中间结果，不能表述为迁移完成或 Android 等价。

详细步骤见 `workflows/implement.md`。

## 5. 规则权威边界

- 业务行为：Android 源码、梳理文档和目标工程已有实现。
- UI 结构与显隐：Android 行为证据和已批准 Layout Spec；视觉几何与样式按设计稿、录屏/截图、
  Android 资源和目标工程主题依次裁决。通用 Harmony Token 只补没有项目真值的细节。
- CodeGraph：只负责 Android 代码发现和影响分析，不是独立业务权威；查询合同、预算和本地复核规则见
  `references/android-codegraph.md`。
- MVVM 分层、状态归属、数据流：官方 MVVM 参考。
- ArkUI API 与编码质量：官方 ArkUI develop 参考及目标工程可编译样例。
- 模块落点、网络、主题、路由、资源、构建：当前项目 profile。
- 文档格式：本 Skill 的五块/六块模板。
- 用户本次明确要求高于默认规则，但不要据此改写无关业务。

本 Skill 不会自动下载、更新、checkout 或提交任何官方仓库。官方参考只做人工检查和人工替换，
来源记录见 `references/official/SOURCE.md`。
