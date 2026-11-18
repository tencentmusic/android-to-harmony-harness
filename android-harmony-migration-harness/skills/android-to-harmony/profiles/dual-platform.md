# Android/Harmony Feature Profile

用于绑定一个包含 Android 与 HarmonyOS 双端源码的功能型项目。项目路径、模块和工具链由
`config/projects.json` 或运行时路由提供；本文件只描述通用约束，不包含真实业务项目名称、仓库或接口。

## 路径

- Harmony 根/编译目录：`${HARMONY_ROOT}`
- Android 根：`${ANDROID_ROOT}`
- 分析输出：`${ANALYSIS_OUTPUT_DIR}`

## 代码边界

- 先从目标入口追踪页面、状态、协议、资源和回调，再确定迁移闭包。
- Page/View 只负责展示和事件转发；网络与数据访问放在 Repository/Model，状态编排放在 ViewModel。
- 业务文件禁止硬编码 URL、密钥、设备路径或环境专属常量。

## 网络与资源

- 使用目标工程已有的网络封装和 URL 配置，不在业务层绕过 Repository 直接访问底层客户端。
- 主题、图片和布局值以目标工程真实资源为准，不编造资源名或默认品牌值。

## 编译

按目标工程的 `build-profile.json5` 和路由返回的 `${BUILD_ARGV_PREFIX}` 执行受影响模块构建。
构建通过只说明目标产物构建成功，不能替代设备业务验收。
