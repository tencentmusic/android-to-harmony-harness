# Windows 原生与解压即用

适用于 Windows 10/11、Python 3.9–3.12、Git。代码已适配 Windows；发布时本机为 macOS，因此 Windows 原生的文件锁、进程取消、实际 DevEco 构建与设备链需要在同事机器验证。macOS 回归和模拟 Windows 分支不替代这些结果。

## 1. 解压并准备依赖

在 PowerShell 进入解压后的 `android-harmony-migration-harness` 目录。路径含中文和空格可以使用；不要直接在 ZIP 预览里运行。

```powershell
python --version
python scripts/bootstrap.py
python scripts/doctor.py
python scripts/selftest.py --output windows-selftest.json
```

`python` 必须是上述版本；有多个版本时可使用已安装的 `py -3.11` 或 `py -3.12`。后续脚本自动选择本目录 `.venv\Scripts\python.exe`。无须激活虚拟环境，也不需要调整 PowerShell 执行策略。移动包后需重新创建虚拟环境；`.venv` 是环境产物，不作为可搬移发布内容。

依赖锁为已有 macOS 验证版本，bootstrap 只接受当前 Python/平台可用的二进制 wheel。下载失败会保留 `.local/dependencies.log` 并明确失败，不会把缺依赖标为通过。有已准备好的 wheels 可加 `--wheelhouse "D:\wheels"`；已有全部依赖的 Python 可跳过 bootstrap。

自检检查文件锁冲突与释放、中文路径和 JSON、Python 执行器、超时子进程清理、会话恢复和结束检查。所有工程和证据都是临时合成材料，不修改业务工程、不连接设备。

## 2. 绑定真实工程和工具链

替换下面示例路径，不依赖目录名称猜项目类型：

```powershell
python scripts/configure.py --project interactive-demo --harmony-root "D:\project\demo-harmony" --android-root "D:\project\demo-android" --deveco-home "C:\Program Files\Huawei\DevEco Studio"
python scripts/doctor.py --project interactive-demo
```

DevEco 必须提供实际 `tools\node\node.exe` 和 `tools\hvigor\bin\hvigorw.js`。工具安装在其他目录时传实际目录；检测失败会返回缺失项。构建目录可用 `--build-cwd` 覆盖。SDK 另装时按工程设置配置 `DEVECO_SDK_HOME`，不要套用 macOS 的 `Contents` 路径。

Agent 使用路由或 doctor 返回的 `toolchain.build_argv_prefix` 配置项目 Profile 的构建 recipe，保留工程的 module/product/buildMode/task 参数。Node、Hvigor 和后续每个实参分别传入，以保留空格及特殊字符。

## 3. 直接告诉 Agent 使用

无需执行 install。把下面这句话中的目录替换为真实解压目录，再补充 Android 来源、Harmony 目标和需求资料：

> 读取 D:\tools\android-harmony-migration-harness\START_HERE.md，使用其中的 Migration Harness 完成这次需求。

如果希望后续任务自动发现技能，可选执行：

```powershell
python scripts/install.py --register-only
```

Windows 默认创建三份带完整性检查的 Skill 转发入口：基础规范、统一迁移工作流和 UI 资料。不复制引擎，不要求管理员权限或开发者模式。重复注册同一包保持幂等；遇到旧入口或同事修改过的入口会保留并提示。明确替换时加 `--replace`，原入口先备份，退役的独立工作流入口一并移入备份。也可以不加 `--register-only`，安装到版本目录。

读取包内 `skills/android-to-harmony-feature-rollout/SKILL.md`，运行 `python scripts/harness.py capabilities` 检查统一入口。
工程执行能力通过 JSON Profile 接入，见包内 `runtime/harness/PROJECT-EXECUTION.md`。

注册后不要移动原解压目录；移动后对新位置重新注册。未注册的便携使用只需告诉 Agent 新位置。业务 run 绑定两端源码与证据路径，不支持把 macOS 的旧 run 直接当成 Windows 当前运行证据；移机需重新核验，不能改历史哈希来获得通过。

## 4. 平台边界和验收

- Windows 的状态读写共用非阻塞字节锁；读取保守串行化，不会绕过写锁。总时限、无输出时限和实际退出码继续区分。
- 业务适配器使用原生 `.exe` 或 Python argv，按当前合同的 request/result 协议接入。截图按实际 `hdc.exe` 路径调用，设备、账号、签名仍须准备。
- 自检通过后，首个业务任务仍须完成真实 Hvigor 构建、安装、截图、视觉和功能验证。Windows 原生自检结果、DevEco/SDK 版本和首个业务验收结果应一起记录。

平台实现参考：[Microsoft 文件锁语义](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170)、[Python Windows 子进程与 batch 行为](https://docs.python.org/3.11/library/subprocess.html)。
