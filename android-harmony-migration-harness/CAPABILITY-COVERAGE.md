# 运行时能力与验证边界

统一运行时不能仅凭命令存在、capabilities=true 或演示通过认定保留了业务能力。
维护时同时检查入口、执行实现、合同绑定、正反例、发布安装后的调用；删除实现必须登记替代路径或明确缺口。

| 环节 | 当前实现与回归入口 | 边界 |
| --- | --- | --- |
| 工程发现与准备 | onboarding.py、project.py；test_onboarding_plan、test_project_readiness | 工程草稿不是已验证能力；真实构建、设备和账号由项目提供 |
| 来源与需求 | contract.py 的 sources/states/review_scopes；test_engine、test_review_html | 文件存在、图片可解码和哈希正确不能证明语义正确，仍需核对目标页面与身份 |
| 按变化生成测试 | test_plan.py；test_test_plan、test_visual_integration | 单状态 visual_comparison 可从 verification 编译到 task；未知 verification 字段拒绝 |
| 联合审批 | review_html.py、engine.py；test_review_html、test_recovery、test_project_readiness | 材料变化使对应批准失效；演示批准不能用于真实任务 |
| 实现及真实构建 | engine.py、harmony_adapter.py；test_engine、test_execution_support | 构建命令和签名配置来自项目；单元测试不证明本机真实工程已构建 |
| 包清单与安装身份 | execution.py 的 build_manifest/device_guard；test_execution_support | 需声明对应 execution_presets/策略，未声明不自动启用；独立采图不证明包身份 |
| 设备操作与断言 | suite.py、execution.py；test_declarative_suite、test_execution_support | 内核校验收据；可信驱动负责实际操作。旧业务读取历史 JSON 的适配器须另行接入 |
| 网络与页面检测 | execution.py；test_execution_support | network-transport、semantic-detector 按任务显式配置；不自动插桩所有请求 |
| 截图与像素比较 | capture.py、visual.py、visual_adapter.py、visual_binding.py；test_capture、test_visual* | 非演示 visual 必须绑定当前 device 收据；诊断缩放不能正式验收；像素指标不证明账号/业务状态相同 |
| 图片证据 | foundation.validate_image；test_image_evidence | 校验 PNG/JPEG 完整性并解码；禁止仅靠文件头认定有效截图 |
| 重试与恢复 | engine.py、runtime.py；test_recovery、test_declarative_suite | 未决进程先核对；不重放未知副作用；旧包/环境变化不能继承通过 |
| 会话续行与结束 | session.py、continuation.py、agent_runner.py；test_session、test_continuation、test_agent_runner | 托管入口可强制继续；外部宿主仍须接入 hook，不提供后台自动唤醒 |
| 限定范围收尾 | closeout.py；test_closeout | scoped_complete 不等于全量 delivery_ready，剩余限制必须披露 |
| 开发交接及知识归档 | handoff.py；test_handoff | 当前证据与交接页绑定；知识归档须完成或说明原因，不自动发布 wiki |
| 安装及发布 | install.py、doctor.py、selftest.py；tests/test_package_release、test_launcher_install | 自检实际调用比较器正反例和视觉生命周期；不能替代 Windows 原生、真机构建测试 |

## 尚未迁入的原子能力

与统一前提交 `bc5687a^` 对照，以下实现没有等价内置执行路径，不能描述为已经恢复：

- **系统录屏、导出及中断恢复**：旧 `harness_recording.py` 的录屏所有权、授权等待、MP4 校验和恢复逻辑已删除。
  当前 `capture-screen` 只采静态截图及布局；`native_screen_recording=false`。已有附件视频可由 Agent 分析，
  不代表内核具有录屏执行能力，也不自动证明动画、过渡或连续行为已验收。
- **知识库身份桥接及查重**：旧的 catalog 规范化查询、冲突拒绝和 bridge 文件生成已删除。
  当前保留 Agent 查证与落档规则，`knowledge_identity_bridge=false`；自动化唯一性与冲突保护尚未恢复。
- **Android 原生参考探针**：旧 `harness_device.android_reference` 内置 adb 前台/包检查、采图、布局、
  操作与未决结果恢复。当前 `capture-screen` 仅支持 Harmony hdc，`native_android_reference=false`；
  Android 取证需外部工具及项目适配，通用 command 不是等价内置驱动。
- **设备屏幕超时准备**：旧 `harness_device.prepare_screen_timeout` 读取并验证目标设备的超时覆盖。
  当前没有对应执行器，`device_screen_timeout_preparation=false`；设备熄屏需项目显式处理。
- **Harmony 原生导航驱动**：旧 `harness_device.execute` 内置启动、点击、返回、滑动和等待控件。
  当前声明式套件提供防护及恢复协议，实际 `observe/act` 由项目提供，`native_harmony_navigation=false`。
  `capture-screen` 只恢复静态采集，不等于内置整套导航驱动。
- **带累计消费上限的动作授权**：旧 `harness_preflight.authorize_step` 按账号、目标、场景、币种登记授权，
  未决动作仍保留消费额度。当前 guarded suite 只允许 read/navigation/selection，
  `bounded_paid_action_authorization=false`；拒绝付费动作的保护仍在，但没有等价的授权消费账本。
- **保留证据的 worktree 搬迁**：旧操作员工具 `relocate_worktree.py` 处理未决操作、Git worktree 移动、
  历史路径和快照一致性。当前只有位置变化后的能力失效检查，
  `evidence_preserving_worktree_relocation=false`；普通复制/改路径不保证原批准与收据可复用。

旧格式任务自动转换、跨任务构建缓存、并行设备执行、自动宿主 hook 与后台唤醒也不是当前能力。
前者显式拒绝并保留原档案；后几项由 capabilities 明示限制，不能用同名命令或自然语言承诺替代实现。

## 维护检查

```sh
python3 scripts/refresh_release.py
python3 -m unittest discover -s runtime/harness/tests
python3 -m unittest discover -s tests
python3 scripts/selftest.py
```

`selftest` 的视觉检查覆盖相同图片通过、不同图片失败、内置任务生成比较报告，以及正式合同缺绑定时拒绝。
`examples/visual_demo.py --output /new/directory` 可单独跑小型视觉生命周期，结果只能为 demo_only。
任何公开能力的删除都应先使对应行为测试失败，再完成替代接入或显式发布为不支持；不能只刷新能力清单和文件哈希。
