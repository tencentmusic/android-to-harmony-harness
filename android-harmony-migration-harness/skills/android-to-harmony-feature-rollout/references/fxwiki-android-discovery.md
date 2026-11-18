# Android 查证：知识索引 → CodeGraph → 定点本地

工程存在结构化知识库时按本顺序查证；这是 Agent 工作规则，CLI 不自动调用知识工具。

1. 读取 catalog、本 capability 的 code_map 和相关业务事实，保存实际读取路径与问题。
2. 仍有缺口时，按项目 Profile 的 CodeGraph 配置查询 status、上下文、符号与调用关系。
3. 在当前 Android HEAD 对候选路径或 FQN 做 read、小范围 rg、git 复核。
4. 对“没有实现”等负面结论，记录已查锚点后再扩大范围做反证。

检索记录包含问题、工具与查询、命中文件、分支和 HEAD、结论、剩余缺口。
将记录作为 discover 检查点的 evidence，并在合同来源引用中链接确认的本地文件与 locator。
wiki 和图谱候选不证明 Harmony 已实现；具体行为以当前源码与实际运行证据为准。
图谱预算、降级和跨端边界见基础技能的 `references/android-codegraph.md`。
