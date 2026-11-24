# ContextFlow NOW

## 项目定位

面向个人学习、开发与研究的多 Agent 协作工作台：协调现有工具，让工具承担协调，让人专注思考。

## 当前判断

项目已经从后端能力验证进入**系统集成与工作台搭建阶段**。用户已明确认为可以开始搭建整个系统，并指出当前优先缺口是统一的用户交互与端到端体验，而不是继续只扩展后端模块。

后端已有从 Supervisor 消息接收、持久化和解析，到 Task/Delivery、Agent Runtime、执行追踪、报告回传与结构化审核的分阶段实现。统一基线保留完整 C-06 提交链（至 `392ee98`）、既有 UI 提交（`7ce1236`）和 2026-11-24 最新四级工作空间 Demo。`main` 保存验收通过的共同基线，`dev/ui-design` 负责前端设计开发，`dev/infrastructure` 负责后端基础设施；三个分支均包含完整仓库。

目前还没有统一前端把消息、解析、任务、执行、报告和审核串成一个可观察、可操作的工作台。真实 ChatGPT 页面到 Core 的完整提交验证，以及真实会话中的报告回传验证，也没有由现有自动化测试证明。

## 当前阶段

**统一基线与后续开发准备。** 当前已有四级工作空间 Mock Demo；本次仅整合既有成果和 Git 分支，不新增功能设计。已有后端代码和测试是可复用基础，不等同于用户可用的完整系统。

首要产品方向已明确为：建设统一交互入口，能观察核心处理状态并显式触发、验证已有后端流程。第一版具体页面范围、控制 API、宿主形式和真实网页验收安排仍需结合当前代码调研后确定；不在此文档中预选新技术栈。

## 下一步

1. 从统一基线继续开发：前端使用 `dev/ui-design`，后端使用 `dev/infrastructure`；定期将验收成果合回 `main`，并同步回另一开发分支。
2. 盘点现有 Bridge/Receiver/Supervisor Control、Task/Execution/Report API 的可调用边界，找出统一前端尚缺的查询和操作接口。
3. 检查现有桌面/扩展形态与 `uxuescript` 可复用的交互组织方式，形成首个工作台纵向切片和验收条件。
4. 先以 Fake/隔离数据打通统一界面的状态查看和显式操作，再安排受控的真实链路人工验收。

## 状态文档约定

- 本页维护当前方向和下一步。
- [开发阶段状态](04-architecture/implementation-status.md)记录阶段能力、代码基线、测试证据和未验证边界。
- 产品需求与概念设计文档仍保留其候选/未决状态；开始系统搭建不自动批准其中全部历史设想。

## 按需查阅

- [分支约定与统一基线](development-baseline.md)

- [开发阶段状态总览](04-architecture/implementation-status.md)
- [概念架构](04-architecture/architecture-overview.md)
- [开发任务规格：C-04 至 C-06](04-architecture/11.11开发任务.md)
- [产品范围与待决问题](01-product/scope.md) · [未决问题](02-requirements/open-questions.md)
- [Core 使用说明与实现报告](../contextflow-research/docs/CF-CORE-003-usage.md) · [B2 使用说明](../contextflow-research/docs/CF-CORE-004-B2-usage.md)
