# 分支约定与统一开发基线

日期：2026-11-24（Asia/Shanghai）。本次只整合既有成果与 Git 分支，不新增产品功能。

## 三个长期分支

- `main`：已验收的产品阶段成果。
- `dev/ui-design`：前端 UI、交互、设计、Mock 和前端测试。
- `dev/infrastructure`：Core、Bridge、Agent Runtime、持久化和后端基础设施。

三个分支均保存完整代码，不按目录拆仓。两个开发分支从同一统一基线开始，阶段验收后合回 `main`，再将 `main` 合入另一开发分支。阶段节点使用标签记录，不增加长期阶段分支。

本地分支分别跟踪同名 `origin/*` 分支。同步前先 fetch 并检查差异，普通推送；远端已变化时先合入并验证，不强推、不重写已发布历史。

## 基线来源

- Core：原远端 `main` 的 `392ee98`，完整保留 `ba50275`（C-01）、`7860df4`（C-03）、`0582f7a`（任务书）、`cc31310`（C-04）、`5b3a81b`（C-05）、`392ee98`（C-06）的提交链及此前 A/B 阶段历史。
- UI：完整保留 `7ce1236`，并提交此前未提交的四级工作空间源码、依赖锁文件、测试、说明、favicon 与截图。
- 文档：纳入原有状态文档、消息设计历史稿和 C-02 调研报告，更新已过时的分支和目录说明。历史稿中的本机图片路径仍按原文保留，不作为当前实现或已批准设计的证据。
- 合并保留双方提交历史。发布基线标签为 `baseline/ui-core-unified-v1`。

## 本机工作树

| 目录 | 分支或状态 |
| --- | --- |
| `D:\CortexFlow-main` | `main` |
| `D:\CortexFlow-ui` | `dev/ui-design` |
| `D:\CortexFlow-stage-c` | `dev/infrastructure` |
| `D:\CortexFlow` | 原始脏工作树，保留在旧提交的 detached HEAD，不用于继续开发 |
| `D:\CortexFlow\.claude\worktrees\feat-c-01-supervisor-binding` | 原始 C-01 脏工作树，保留 detached HEAD，不用于继续开发 |

## 未提交材料的保留

整合前备份位于 `D:\CortexFlow-backups\20261124-202546`：包含全部引用的 Git bundle、三个工作树的二进制差异、文件快照及 SHA-256 清单。依赖、缓存和浏览器 profile 没有重复打包，仍原样留在原目录。

未纳入产品源码的 C-02 原型、原始 Agent 输出、C-03 执行证据、运行 Journal、SQLite/WAL/SHM、手工验证脚本和本机配置，在原目录和相应备份中保留。它们不是可随意丢弃的缓存，也不是构建和测试基线的隐含依赖。

旧分支退役前，须验证全部旧分支提交均为最终 `main` 的祖先，原始工作树内容与备份匹配，并将新的三个分支同步到远端。原有阶段标签保持不变。
