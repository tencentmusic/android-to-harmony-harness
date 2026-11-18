# 实时采集与视觉比较

`capture-screen` 和 `compare-visual` 不需要迁移 run，可以直接检查图片处理能力。
完整迁移使用同一比较核心，由 `visual_comparison` 绑定当前包的设备收据、已审状态和断言。

## 单页检查

在团队包目录执行，输出目录必须不存在：

```sh
python3 scripts/harness.py capture-screen \
  --hdc /absolute/path/to/hdc --target DEVICE_ID \
  --bundle com.example.app --output-dir /tmp/visual-capture

python3 scripts/harness.py compare-visual \
  --baseline /absolute/android-state.png \
  --harmony-capture /tmp/visual-capture/screen.png \
  --state-id profile-card --output-dir /tmp/visual-comparison
```

采集前后都核对设备前台包，保留 PNG、布局、命令日志和 SHA256；不安装、不导航、不改变账号状态。
未指定 target 时只允许一个在线设备。`capture.json` 明确 `package_attested=false`：截图采集不能证明安装包身份。
失败目录保留命令与诊断，不生成成功收据，不覆盖已有目录。

比较真正调用 Pillow、NumPy 和 scikit-image，计算窗口 SSIM 与归一化 MAE。
固定阈值为 SSIM >= 0.85 且 MAE <= 0.12；全图和所有必验 ROI 都必须满足。
动态 mask 需要说明理由；与 mask 相交的 SSIM 窗口不参与评分，屏蔽区不能影响周边分数。
屏蔽后须有可计算窗口且至少保留全图 5% 的像素。拒绝越界区域、坏图、透明图和尺寸不一致。

输出 `comparison.json`、`comparison.png`、`difference.png` 和解码后的两端 PNG。
报告绑定原始图片、区域、算法、依赖版本及结果哈希。图像 `passed` 仅表示所选两图的像素条件成立，
`acceptance_scope=image_comparison_only`、`quality_gate_granted=false`，不授予迁移交付通过。
`compare-visual` 匹配退出 0，不匹配退出 1，输入错误退出 2。

设备物理分辨率不同，或账号、装扮、视频、页面状态不同时，先修正采集口径。
只需要观察差异时，可以明确使用：

```sh
python3 scripts/harness.py compare-visual \
  --baseline /absolute/android-state.png \
  --harmony-capture /tmp/visual-capture/screen.png \
  --state-id profile-card --output-dir /tmp/visual-diagnostic \
  --diagnostic --resize-to-baseline
```

该操作记录原图 SHA、尺寸、LANCZOS 变换与输出 SHA，写入 `transform.json`；
比较报告 `diagnostic=true`、`gate_eligible=false`，不能用于正式原生验收。
不会根据分数自动判断账号和业务状态是否一致。

## 迁移任务接入

视觉 task 省略 `command`，声明每个归属点的 `visual_comparison`。引擎自动使用包内 `visual_adapter.py`，
不从项目的历史结论文件读取 `actual`。示例片段：

```json
{
  "id": "profile-visual",
  "kind": "visual",
  "capability_ids": ["profile"],
  "depends_on": ["profile-device"],
  "acceptance_ids": ["profile-card-visual"],
  "inputs": [{"root":"harmony","include":["src/profile/**/*.ets"]}],
  "visual_comparison": {
    "profile-card-visual": {
      "assertion_id": "card-pixels-match",
      "baseline_index": 0,
      "capture_from": {
        "task_id": "profile-device",
        "acceptance_id": "profile-card-device",
        "image_name": "profile-card.png"
      }
    }
  }
}
```

`assertion_id` 绑定该点的 `truthy/expected=true` 或 `equal/expected=true` 必验断言。
`baseline_index` 默认 0，对应同一 state.baselines；基准仍来自独立 Android 根。
当前内置比较每点只支持一个 state；分组状态需拆成各自的视觉点。
源 task 必须是直接 device 依赖，源点必须覆盖相同状态且为 passed。
有多张截图时用 `image_name` 精确选定唯一原图；无匹配或有歧义均受阻。

适配器校验上游收据、输入摘要、候选包与全部证据哈希，原样沿用其原生环境，重新计算真实图片。
引擎接收结果时再次计算指标，核对报告的状态、已审基准、原始设备截图、regions、全部图像产物和断言值。
不能提交 `actual=true` 覆盖失败分数，也不能使用另一个状态或诊断模式的报告。
未配置的额外必验断言仍是 not_checked，不自动补 true。

可选 `regions` 参与联合评审摘要；修改 mask 或 ROI 后需重新审核受影响域：

```json
{
  "viewport": {"width":1080,"height":2400},
  "masks": [{"id":"video","x":0,"y":0,"width":1080,"height":1200,"reason":"动态直播画面"}],
  "required_rois": [{"id":"card","x":0,"y":1200,"width":1080,"height":1200}]
}
```

区域坐标为两张同尺寸原图的整数像素；不做隐式缩放或裁切。省略 regions 时比较整个画面。
缺基准、尺寸不符、源收据失效和环境不一致均报告 blocked，继续可独立比较的点。
非演示的 visual 任务必须配置 `visual_comparison`，不接受仅返回历史 `actual=true` 的自定义命令。
已有当前格式任务恢复后同样检查；缺绑定时需修订合同，重新审核受影响域并重新取证。
演示模式保留自定义适配器用于离线协议测试，始终不能授予真实交付通过。

使用 `plan` 时，可将同样的 `visual_comparison` 对象放在场景的 visual verification 中。
编译器按 `<scenario-id>-visual` 生成 task 绑定；`capture_from.acceptance_id` 指向生成后的设备点 ID。
同一 visual task 的所有点都须配置，模板省略 `command`；场景限一个状态。
配置参与计划、联合评审和任务摘要；编译结果与计划不一致时拒绝初始化，未知 verification 字段也会报错。

## 安装与旧任务

安装器同步注册技能与 `migration-harness` 启动器，并备份旧入口；`doctor` 静态检测 PATH 与选定技能运行时是否一致。
`--bin-dir` 可选择命令目录，自定义 `--skills-dir` 时默认使用其父目录下的 `bin`。
陌生启动器需要显式 `--replace`；安装失败共同回滚技能和启动器。

旧任务 `experiment-state.json` 不会被改名或复制批准来冒充 `harness-state.json`。
单页采集和比较可直接执行；完整迁移需当前格式合同和新目录，旧档案保持原样。
