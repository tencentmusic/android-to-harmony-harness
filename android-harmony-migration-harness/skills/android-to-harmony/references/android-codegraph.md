# Android CodeGraph 检索合同

当项目 Profile 配置了 `kb-mcp-for-zc`，或任务需要跨模块定位 Android 调用链、影响范围、接口、
Socket、资源和最终动作时读取本文件。这里是 CodeGraph 工具顺序、预算和降级规则的唯一来源；
业务结论仍由当前源码、协议、录屏和已确认需求裁决。

## 能力边界

- `kb-mcp-for-zc` 当前只提供 Android L1/L2/L3 能力，不提供 HarmonyOS CodeGraph，也不能读取 `.ets`。
- Wiki 是迁移文档候选，`kb_resolve_counterpart` 是 Harmony 路径/路由候选；二者都不证明目标工作树已实现。
- `kb_codegraph_status` 当前不返回 index revision。`indexed=true` 只能证明存在索引，不能证明它与本地 Android HEAD 一致。
- CodeGraph 用于发现候选关系；会进入需求、策略、参数或代码修改的关键事实必须在本地 Android HEAD 回读。
- “没有调用方、没有消费者、没有实现”是负面结论，必须在本地 source root 做全范围搜索，不能只依赖图谱零结果。

## 固定查询顺序

先完成 Harmony 项目路由，读取结构化知识库 catalog 并核对 canonical 能力映射。
Android 查证默认 **wiki 先于 CodeGraph**，详见 feature-rollout 的
`../android-to-harmony-feature-rollout/references/fxwiki-android-discovery.md`。

在 monorepo 存在 `knowledge-base/` 时，**第一步**读本地 vault：

```text
catalog.json + 本 capability 的 code_map/live_android.md（evidence.local_attempt: wiki_read）
```

然后才使用远端索引。从 Profile 取得 `project_id` 和 `module_rel` 提示，执行：

```text
kb_codegraph_status（每个 project_id、每个迁移任务一次）
  → 有 capability/feature_id：get_feature_context，按需读 L1 Wiki（远端）；本地 knowledge-base code_map 优先
  → 无 feature_id：已知符号用 search；模糊业务入口用 explore
  → 按问题选择 node / callers / callees / impact
  → source_read 精读命中片段
  → source_grep 补 CMD、EventBus、反射、资源名、H5 Bridge、配置 key
  → 本地 Android HEAD 定点复核（路径来自 knowledge-base/code_map 或图谱，禁止任务开局全仓 rg）
  → 本地 Harmony rg/git/HAR 声明对照
```

禁止在尚未读取知识库与本任务 `module_rel` 锚点时，对 Android 源做无范围全局搜索。
`search` 和 `explore` 只选能回答当前问题的一个，不为形式重复调用。追入口用 `callers`，追最终动作
用 `callees`，共享影响才用 `impact`。先缩小 `module_rel` 和查询词，再提高 `limit` 或 `depth`。
大房型或聚合页面先拆成入口、展示策略、数据、Socket、播放器、权限/付费等行为切片，不对巨型
Fragment 做一次无限展开。

## 默认软预算

| 阶段 | 单功能切片预算 |
| --- | --- |
| status | 1 次 |
| context / wiki | 0–3 次 |
| search / explore seed | 最多 3 个 |
| node / callers / callees / impact | 合计最多 8 次 |
| source_read | 最多 6 个精确片段 |
| source_grep | 最多 4 个动态边查询 |
| 远端调用总数 | 默认不超过 20 次 |

初始 `limit=20`，确认结果被截断后才升到 `50`；`impact depth=2` 起步。截断结果不能直接进入证据
账本，先缩小模块、符号或行为问题。确有未闭环的证据行，或支付、权限、播放器、Socket 最终动作仍
缺失时可以超预算；超预算前记录要回答的具体问题。

## revision 未验证时的降级

任务开始记录：Android root、branch、HEAD、worktree、`project_id`、`index_revision=unknown`。

1. 图谱命中先记为 `index_candidate`。
2. 房型/类型值、显示策略、权限/支付/礼物、URL/CMD/参数、播放器调用顺序、入口和最终动作，
   必须在当前本地 Android HEAD 复核后才能标为 `confirmed`。
3. 至少抽查入口、中心判断、最终动作三个锚点。任一不存在或明显不一致，将本任务图谱状态标为
   `stale_or_unverified`，后续使用本地 `rg/git`；不能只保留与预期一致的图谱结果。
4. 本地 Android 在任务期间发生变化后，旧图谱命中和已读源码片段重新降为候选，先重建基线。
5. 没有本地源码可复核时，最高证据等级是 `indexed_snapshot_unversioned`，不是“已确认”。

每条图谱证据至少记录：`project_id`、`module_rel`、工具/查询、symbol/path、index revision（当前为
unknown）、本地 Android HEAD、是否本地复核和复核方法。

## Harmony 固定本地流程

Harmony 端始终执行：

```text
git branch/HEAD/status/merge-base
  → rg --files 定位 ArkTS
  → rg 搜入口、策略、状态、路由、Socket、播放器调用
  → 读取当前 .ets 与 HAR/SDK 声明
  → git diff/log 核对当前与历史分支
  → 项目 Profile 的 Hvigor 构建
```

不要对 `.ets` 调用远端 Android source 工具，不要把 Android 图谱边投射成 Harmony 调用边，也不要
把 counterpart/Wiki 候选当作文件存在、路径可达或能力已支持的证据。CodeGraph 超时、未索引或
不可信时，Android 仍先查知识库（若已绑定），再在 Profile 提示的 `module_rel` 内 `rg → read → git`；
全仓 rg 仅用于反证且须记录 wiki/图谱已查锚点。
