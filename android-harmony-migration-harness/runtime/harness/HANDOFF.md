# 开发交接

最终交给开发接手人的入口是自包含中文 HTML。它回答交付了什么、还缺什么、从哪里接代码及如何继续工作。
联合审批仍用 `review-packet`；执行诊断仍用 `report`。两者的机器记录保留在 run，交接正文不展示执行流程、
operation ID、摘要字符串、计时流水、原始日志或候选安装包。

## 生成步骤

1. 核对当前源码 diff、实际实现、已审需求/技术/UI 资料和验收结果；从接手人角度编写交接内容。
2. 明确鸿蒙源码根及本次产品的 DevEco 工程目录。`project.root` 固定为 `harmony`，`project.path` 相对合同的
   Harmony root；例如源码根是大仓而本次交付是独立直播 App 时填 `apps/live`。工程根本身已是直播目录则填 `.`。
3. 完成适用的知识回灌：将经验证的结论写入项目知识库，按项目约定更新索引，并把实际写入记录作为内部依据。
   无可沉淀知识时明确写出不适用原因；计划稍后执行不能记为 completed。知识操作不展示在交接正文。
4. 按 [输入示例](examples/developer_handoff.json) 准备 JSON，再执行：

   ```bash
   python3 scripts/harness.py handoff --run-dir /path/to/run --input /path/to/handoff.json
   ```

5. 打开返回的 `html_path`，核对内容、选定图片、桌面和手机可读性。全部点和 Agent review 通过后，展示此文档
   并收集最终人工结论。限定范围交付在 `closeout prepare.report` 中引用同一个 HTML，保留用户范围原话和排除项。

完成条件：HTML 位于 **所选鸿蒙工程** 的 `androidtoharmonyharnessdoc/`，未完成项前置，内容与当前实现/测试相符，
适用的知识回灌有真实写入依据，`developer_handoff.status=current`。文件名带功能、run 短标识与内容版本；旧快照保留。
输入和机器索引保存在 `run/developer-handoffs/`，生成不会改写验收结果或登记人工批准。

## 正文顺序

| 顺序 | 内容 | 接手人需要的信息 |
| --- | --- | --- |
| 1 | 总体情况 | 简短结论、最终交付的功能、当前合同验证概况 |
| 2 | 未完成项与后续工作 | 事项、影响、下一步、跟进人或角色；包含未做、受阻、已知问题和待确认项 |
| 3 | 需求范围 | 本次包含与不包含的范围，最终行为及重要边界 |
| 4 | 技术方案 | 实际落地设计、模块职责、关键取舍和与原方案的差异 |
| 5 | UI 稿与实现效果 | 涉及 UI 时按关键状态选定设计/双端对照/实现图片并注明含义；后台逻辑说明不适用原因 |
| 6 | 代码交接 | 仓库、开发分支、代码版本、目标分支，主要入口及其改动职责 |
| 7 | 验证与复现 | 已验证的行为及限制，开发接手后最短复现步骤与环境条件 |
| 8 | 接入与合入 | 依赖、开关、初始化、联调、合入注意事项和适用的回退方法 |

接口与配置、数据与兼容、生命周期与清理、问题定位等按本次影响在 `topics` 补充。没有相关改动时不用扩写无关章节。
仓库/分支/提交/合入状态须实际核对；未提交的改动要说明，不能把原始任务的旧状态当成当前状态。
正文使用业务名称解释结果；工具自动补入所有尚未通过的合同**行为**，即使 `pending=[]` 也不会隐藏失败、未检查和受阻点。
合同统计与用户限定范围的补充检查须在 `verification.summary` 中区分，不能把排除项描述成通过。

## 输入约定

`format` 为 `developer-handoff/1`。具体字段见示例，结构校验包括：

- `overview.summary/delivered`、`requirements.summary/in_scope/out_of_scope`、`technical.summary/decisions`：面向开发者的文字。
- `pending`：必须提供数组；每项含 `item/impact/next_step/owner`，无额外待办时显式传空数组。
- `ui.applicable`：本次是否交付 UI 变化。true 时提供 `summary` 和非空 `images`；false 时提供 `reason`，不附图片。
  图片引用为 `{root,path,locator}`，对应已存在的 PNG/JPEG/WebP，`locator` 作为说明文字。
  仅嵌入精选图片；不自动遍历全部验收截图，不生成新的视觉测试。
- `code`：`summary/repository/branch/revision/target_branch` 和非空 `entries:[{path,purpose}]`。
  代码路径相对合同的 Harmony root，必须实际存在；文档接收方使用所在项目访问源码。
- `verification/integration`：各含 `summary` 与非空 `steps`；`topics` 可省略，每项为 `{title,summary,items}`。
- `knowledge`：`status` 为 completed 或 not_applicable，均需 `summary`；completed 还需非空
  `evidence:[{root,path,locator}]`，指向实际知识写入与索引更新记录。文件存在性和完整性由工具校验；
  内容是否构成有效知识及是否实际写入目标知识库，由 Agent 复核，CLI 不提供知识库服务或语义认证。

## 有效性与恢复

交接快照绑定当前合同、联合审核、任务输入/收据、逐点结果、选定图片和知识依据。源码、材料、证据改变或文档被改写后，
状态为 stale，按当前输入重新生成。最终人工批准本身不让已有交接失效。仅生成文档不会使未通过项变为 passed。
准备结束时，continuation 对缺少或失效的交接返回 `prepare_developer_handoff`；限定范围记录引用了另一份 HTML 时返回
`prepare_scoped_delivery`。既有最终批准继续沿用有效记录；补交接不等于需要重新执行业务测试。

输出目录必须与构建/测试输入及评审 bundle 分离。若合同用过宽通配符将新文档纳入执行输入，生成会回滚 HTML 并报错；
应修正输入边界、按合同修订规则处理影响后再导出，不能跳过摘要校验。知识回灌改到了已绑定来源时同样需要核对其影响。
此功能只要求实际完成的写入记录，不自动向外部知识库写数据。

`verify` 继续表达原有验收及最终人工 Gate；开发交接和知识依据由 continuation 检查。`delivery_ready` 与
`developer_handoff=current`、有效的最终人工结论共同构成本工作流的交付条件。demo 始终不代表业务交付。
