# Implementation：生成、修复、接入与改进

## 目标

在目标 Harmony 工程真实修改代码，并用项目编译证明代码可编译、可打包。编译不是设备可用、视觉
对齐或 Android 等价证据。MVVM 是业务边界约束，不是只写在交付说明里的目录口号。

## 1. 先读事实和现状

1. 读取迁移文档；没有文档时按 `analyze.md` 的证据范围读取 Android 业务闭包。
   所有直接驱动本次改码的 Android 结论，都要按 `../references/android-codegraph.md` 在当前本地
   Android HEAD 复核；未绑定 revision 的图谱结果或旧迁移文档不能单独作为修改依据。
2. 读取目标 `.ets`、相邻同类实现、路由入口、网络封装、主题、资源和模块配置。
3. 搜索目标业务闭包中的 `@Component`/`@ComponentV2` 及配套状态装饰器，确定 V1/V2。
4. 列出本次必须改动的 Page/View、ViewModel、Model/Repository、协议、路由、资源和宿主接线。
5. 修复任务只处理根因和必要闭包，不因“更优”重写无关模块。
6. 完整功能若有用户可见 UI，读取 feature-rollout Harness 的当前联合评审结论：只有
   相关需求、技术、UI 已批准且范围/规格/原型哈希仍有效，才进入 View/Page/Dialog 改码。
   `pending`、拒绝或哈希漂移时先回到 UI Layout Spec、`hmos-design-visual-mobile` 原型和人工审核。

## 2. 加载官方规则

每次代码类任务都读：

- `references/official/mvvm/official-skill.md`
- 新功能：`references/official/mvvm/references/add-func-workflow.md`
- 已有功能整改/修复：`references/official/mvvm/references/refactor-func-workflow.md`
- `references/official/arkui-develop/official-skill.md`
- `references/official/arkui-develop/references/style-guide.md`

按需读取：

- V1/V2 与数组更新：`mvvm/references/v1-v2-mapping.md`
- V1 嵌套观测：`mvvm/references/v1-nested-observation.md`
- 架构反模式：`mvvm/references/anti-patterns.md`
- API：先查 `arkui-develop/references/quick-apis/_index.md`，再精准读取对应卡片。
- 高频错误：按改动内容读取 `quick-rules/` 和 `common-mistakes/` 的对应文件。

不确定的 ArkUI API 必须查目标 SDK 官方资料或工程内可编译样例，禁止凭印象编造属性、枚举或资源名。

官方 develop 参考中的 `devecocli` 安装/构建步骤不适用于这三个已配置工程；只吸收其 ArkUI API、
编码和检查规则。验证统一使用当前 profile 的 Hvigor 命令，不得为了本流程自动安装 `devecocli`。

## 3. MVVM 架构门禁

### 3.1 保持 V1/V2 基线

- 以“本次修改的业务闭包”而非整个仓库计数决定版本。
- 已有闭包默认保持原版本；只有用户明确要求迁移时才整体迁移。
- 同一组件内不得混用 V1/V2 套件。
- 独立新功能优先跟随所在模块的主流版本；没有邻近基线时优先 V2。

### 3.2 职责必须真实分离

| 层 | 可以做 | 不可以做 |
| --- | --- | --- |
| Model/Repository | 实体、接口/缓存访问、响应解析、数据转换 | ArkUI 状态装饰器、UI 组件、页面跳转 |
| ViewModel | UI 业务状态、校验、调用 Repository、异步编排、把 Model 转成 ViewModel 数据 | 直接构建 UI、持有组件实例、把所有全局能力塞进一个类 |
| View | 渲染 ViewModel 数据、持有纯 UI 局部状态、把用户意图上报 | import Model、直接发业务请求、复制业务状态 |
| Page | 创建/注入 ViewModel、组装 View、接页面生命周期入口 | 堆业务规则、解析协议、直接维护多份业务状态 |

纯展示、无业务状态的小组件可以只接收参数和回调，不强制单独创建 ViewModel。

### 3.3 数据流和状态

- 数据：`Repository/Model → ViewModel → View`。
- 事件：`View → ViewModel → Repository/Model`。
- View 不 import Model；列表渲染回调使用 ViewModel/展示模型类型。
- 同一业务状态只有一个 source of truth。
- 能计算出的状态使用 V2 `@Computed` 或 V1 getter，不额外同步字段。
- 单组件纯 UI 状态用 V2 `@Local` / V1 `@State`。
- 多个纯 UI 子组件共享状态，提升到最近公共祖先后向下传递。
- 业务状态、加载态、接口结果和业务校验放 ViewModel。
- Model 是纯数据/数据访问，不使用 `@Observed`/`@ObservedV2`。

### 3.4 目录服从项目业务边界

官方推荐的 `model/ viewmodel/ views/ pages/` 是职责模型，不得越过 profile 的 HSP/宿主边界。
在目标业务目录内使用清晰子目录；已有模块有成熟结构时沿用其结构并保证同等职责分离。

## 4. ArkUI 生成与接入

1. UI Gate 通过后，以获批 Layout Spec 和原型映射 ArkUI 结构与状态；HTML 只作为视觉合同，禁止把
   DOM/CSS 逐字搬进 `.ets`，也禁止因原型可渲染就宣称设备视觉等价。
2. 先做最小可编译骨架，再逐层加入 Model/Repository、ViewModel、View/Page。
3. 状态、回调、集合和对象全部给出明确 ArkTS 类型；禁止 `any` 和隐式类型逃逸。
4. `ForEach/LazyForEach` 使用稳定业务 ID 作为 key，不用索引冒充业务标识。
5. 组件构造参数、链式属性、枚举和 Kit import 必须来自官方参考或项目现有可编译代码。
6. 通过 `this.getUIContext()` 使用需要 UIContext 的全局 UI 能力。
7. `aboutToAppear` 不做重 IO、大计算；初始化入口调用 ViewModel，由其协调异步任务。
8. 优先复用项目现有标题栏、容器、导航、Dialog、网络、主题和通用组件。
9. 图片先检查目标模块已有资源；缺失时从 Android 迁移真实资源，禁止文字占位或编造
   `$r('app.media.xxx')`。
10. shape/selector/gradient 读取 Android XML 的真实圆角、颜色、渐变和状态后实现。
11. 完成 export、PageBuilder/NavDestination/Dialog 注册、宿主参数、返回结果和跨模块依赖。

## 5. 编译闭环

1. 根据修改文件确定受影响模块。
2. 在 profile 指定 `build_cwd` 执行对应 Hvigor 命令。
3. 编译报错时定位并修复语法、类型、import、装饰器、资源、路由或依赖问题，再次编译。
4. 跨模块接入需构建所有受影响模块；只编译叶子文件不能替代真实工程构建。
5. 因 SDK、凭证或环境阻断时，保留完整错误和已验证范围，明确写“未编译通过”；编译失败不能声明完成。

完整功能编译通过后，回到 feature-rollout Harness：

1. 用准确构建产物做设备冒烟，并绑定 SHA-256、设备/系统、账号/房间、时间和证据。
2. 有 UI 时按包根 `runtime/harness/VISUAL.md` 绑定 Android 基线与本次 Harmony 真机截图，
   执行内置双图比较；另对照批准原型进行人工核对并留证。双图指标不代表三方自动验收，
   原型渲染也不能替代真机截图。
3. 执行全部 acceptance id 的正常、失败、切换、退出与重进用例。
4. 按 feature-rollout 当前合同生成开发交接 HTML，取得最终复核并运行 `verify`；如实保留受阻和失败点。
   历史案卷的阶段、验收与收尾命令按其兼容文档执行。

## 6. 默认交付 Markdown

代码任务最终用六块简报：

1. `## 一、完成内容`
2. `## 二、MVVM 分层与数据流`
3. `## 三、修改文件`
4. `## 四、接口、路由、资源与宿主接线`
5. `## 五、编译验证`
6. `## 六、未完成项、依赖与风险`

没有未完成项时第六块写“无”。简单修复可以精简正文，但不能省略 MVVM 判断和编译结果。
完整功能的最终答复链接当前开发交接与验收材料；六块简报不能替代 feature-rollout 的交付合同。
