# 知识库新能力落档

落档前读 catalog，核对 capability、别名、标题和 canonical 路径。已有包更新原包，
确无可复用包时按知识库 page-schema 与 code-map-schema 创建完整功能包。

| 文件 | 内容 |
| --- | --- |
| README.md | 能力身份、applies_to 和主链路路由 |
| implementation.md | 跨端业务事实、场景、接口和开关 |
| code_map/live_android.md | 当前 Android HEAD 复核的入口、主链路 FQN 与关键调用链 |
| code_map/live_harmony.md | 当前 Harmony 实现、来源映射与验收依据 |

页面名和产品 applies_to 使用当前项目规范。补齐双端 code_map；
若某端不适用，记录源码查证与具体依据。新能力的 Android 查证仍遵循
[fxwiki-android-discovery.md](fxwiki-android-discovery.md)。

评审准备与实现期间可写待审核草稿，未验收项标记 partial/unverified。
最终发布知识页遵循当前项目授权和知识库规范，核对两端 revision，更新索引并保留实际执行证据。
知识回灌结果写入开发交接；草稿、知识页和索引成功均不代替业务验收。
