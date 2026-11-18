# 提交与部署规则（AI / 开发者必读）

> **仓库**：`androidtoharmonyharness`（Migration Harness CLI）  
> **合入目标 / 准主线**：`master`（只收 MR；禁止在其上直接开发、commit 或 push）

在改 CLI、Schema、文档或发布流程前，**必须先阅读并遵守**本文。

---

## 1. 本仓与测试服

本仓为**本机 / Cursor 使用的迁移 CLI**，**通常不在测试服 155 上跑常驻服务**。仍须：

- **禁止** SSH 登录测试服（或其它远程机）在服务器工作区直接改本仓代码做功能开发（云开发）。
- 所有功能改动在**本地特性分支**完成，经 **MR 合入 `master`** 后，使用者通过 `git pull` 或团队发版流程获取。

---

## 2. 分支：从准主线拉特性分支，再 MR 合回

- **`master` 只做合入目标**，不是开发分支。
- **禁止** 在 `master` / `release` 上直接改代码、commit、`git push origin master`。
- 每次开发：先更新远程，再从 **`origin/master`** 拉出特性分支（`feature/<topic>` 或 `fix/<topic>`），在该分支上提交并推送，最后开 MR 合入 `master`。

```text
git fetch origin
git checkout -b feature/<topic> origin/master
    → 本地开发并自测（npm test / 约定检查）
    → git push -u origin HEAD
    → 创建 MR，目标分支 master
    → Review 通过后合入 master
    → 本机或 CI 验证；使用方 pull master 验收
```

1. **勿提交**密钥与本机绝对路径到仓库。
2. 不要在远程机器上改 Harness 再不同步回 Git。

---

## 3. AI 检查清单

- [ ] 当前不在 `master` 上直接改、commit、push？
- [ ] 特性分支从最新 `origin/master` 拉出？
- [ ] 本地完成并自测？
- [ ] MR 目标为 **`master`**？
- [ ] 未建议远程 SSH 云开发改本仓？
- [ ] 未提交密钥？

若用户要求在准主线上直接开发，说明规则并给出符合 §2 的步骤。
