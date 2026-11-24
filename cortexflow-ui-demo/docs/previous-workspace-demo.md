# CortexFlow Desktop · UI Demo

独立运行的桌面交互设计原型，采用 ChatGPT 风格的浅灰侧栏、白色工作区与克制的状态色。**全部项目、任务、Agent 配置、事件、报告和审核均为 Mock**。不连接 Core、Bridge、ChatGPT、SQLite 或真实 CLI；没有 Electron 集成。

## 启动与构建

需要 Node.js 22.18+（本机已用 Node 24.15 验证）和 npm。

```powershell
cd D:\CortexFlow\cortexflow-ui-demo
npm ci
npm run dev
```

访问 http://127.0.0.1:5176/ 。默认进入工作页，聊天页为 `/#/chat`。端口固定为 5176，避免自动换端口造成预览错位。仅监听本机。

```powershell
npm run build
npm run preview
```

构建先执行 TypeScript 检查，再生成 `dist/`；生产预览默认访问 http://127.0.0.1:4173/ 。不要直接双击 `dist/index.html`，请通过 HTTP 服务访问。前端使用 hash 路由与相对资源路径，方便以后迁移到桌面宿主。

## 交互体验路线

工作页从上到下为 **Workspace 工作路线 → 协作消息时序 → 对话 Task 与执行记录**。路线归属 Project/Workspace，切换对话不会换一份全局计划；下面的消息和任务仍按当前对话组织。

1. 切换「聊天 / 工作」，两个页面共用项目和对话上下文。聊天页仅显示 **WebView Integration Placeholder**，不含 iframe 或仿造的聊天输入框。
2. 展开/折叠项目、选择项目或对话。切换到「Agent 接入方案」会看到另一组任务；「下一步想法」和「内容与体验探索」展示无任务状态。
3. 工作页使用五条顺序泳道展示 Supervisor、Core 与各 Agent 的消息往来，时间从上往下推进。可切换「当前对话 / 选中任务」，点击消息查看内容并联动任务详情。点击 Agent 泳道标题筛选关联任务，再次点击取消。状态筛选、任务标题/ID 搜索可组合使用。「清除筛选」恢复列表。任务按最近更新时间降序显示。
4. 点击任务打开详情；关联 Agent 与下方七阶段记录同步更新。筛选排除当前任务时自动选择首条可见任务，无匹配时清空执行摘要。
5. 选择已完成的「梳理任务详情的信息层级」，预览 Mock 报告与审核记录。「检查窄窗口的布局适配」示范执行完成但尚未审核。
6. 按失败/未知筛选，查看两种状态的不同说明。选择 dsh 可看到「未配置」与「等待派发」，不把配置误写成在线。
7. 「新建项目」「新建对话」提供名称输入、空白校验、取消、创建反馈；仅保存于页面内存，刷新即恢复。右上角设置入口可以重置演示数据。
8. 侧栏开关、列表滚动、独立详情滚动、Toast、报告弹窗和 Escape 关闭均可操作。窄窗口详情变为遮罩抽屉，支持焦点约束与关闭后焦点恢复。
9. 工作路线中的 S02 为当前阶段，默认展开。点击 S01 查看阶段成果，点击后续阶段查看计划；「回到当前阶段」恢复查看焦点。浏览历史不会改变实际 currentSlotId；再次点击阶段或右上角收起按钮可收起详情。
10. 当前阶段以紧凑列表呈现六条已有 Task，直接复用现有状态。点击阶段 Task 会清除冲突筛选，必要时切到来源对话，打开原 TaskDetail，并在原任务表内部定位对应行。历史阶段不复制另一套任务或报告。
11. 选择 S03/S04 可编辑阶段名称、目标和初步计划，也可用前移/后移在未开始的阶段中调整顺序。阶段 ID 保持稳定，只保留当前有效计划，已有 Task 和结果不丢失。远期阶段不虚构 Task。
12. 当前阶段可「新增补充任务」。它会获得新的 Mock Task ID，初始等待、尚未派发；原已完成 Task 不重新变成运行中。新任务尚无 Core/Agent 通信记录，只有本地演示条目。
13. 「重置演示数据」同时恢复初始 Workflow、阶段顺序、任务与查看状态。两份初始 Workspace 各有一份路线；新建项目没有自动生成的规划，显示明确空状态。

所有按钮操作仅改变本地演示 UI；不会真实派发、执行、重试或审核任务。状态为固定快照，不播放伪实时进度。

## 数据与组件

```text
src/
  main.ts                      Vue 入口、hash 路由
  types.ts                     Project / Conversation / Task / Execution 类型
  data/mock.ts                 独立结构化 Mock：2 项目、5 对话、10 任务、3 Agent
  data/mockWorkflows.ts         每个初始 Workspace 一份路线，以 Task ID 关联阶段
  composables/useWorkspace.ts   当前上下文、筛选、选择、创建、重置与反馈
  composables/useWorkflow.ts    阶段查看、计划编辑排序、补充任务与详情联动
  components/
    AppShell.vue               外壳、全局弹窗与反馈
    TopNavigation.vue          聊天/工作导航、侧栏开关
    ProjectSidebar.vue         项目与对话树
    ChatWorkspace.vue          独立 WebView 宿主占位容器
    WorkWorkspace.vue          工作空间组合与上下文标题
    WorkflowRoadmap.vue        总体目标与四段阶段路线，区分当前和查看焦点
    WorkflowSlotDetail.vue     阶段目标、成果、约束及关联任务
    WorkflowPlanDialog.vue     后续规划编辑，复用 DemoDialog
    WorkflowTaskDialog.vue     创建新的本地补充任务，保留执行历史
    AgentSequence.vue          五条泳道、消息箭头、范围切换与任务联动
    TaskList.vue               搜索、状态筛选、可滚动任务表
    ExecutionTimeline.vue      七阶段执行状态摘要
    TaskDetail.vue             来源、目标、事件、报告与审核
    StatusBadge.vue            五类状态的文字/图标/颜色
    DemoDialog.vue             通用弹窗与键盘焦点管理
  styles.css                   CSS Variables、桌面布局和响应式规则
  sequence.css                 顺序泳道的独立样式
  workflow.css                 阶段路线与渐进式详情的独立样式
tests/                         Playwright 交互、键盘与数据一致性验收
screenshots/                   浏览器实际渲染截图
docs/                          设计计划与验证记录
```

Mock Agent：Codex CLI、Claude CLI、dsh。Codex/Claude 为「演示配置已填写」，dsh 为未配置，**均不代表在线状态**。任务覆盖执行中、完成、等待、失败、unknown；报告生成、执行完成与审核通过分别建模。事件时间落在各自任务的创建/更新时间内。

Workflow 的完成结论与 Task 执行状态分开保存。`currentSlotId` 是唯一的主要当前阶段；查看焦点单独保存。Slot 只持有 `taskIds`，标题、状态、目标和结果从现有 Task 读取。已完成阶段展示结论与成果，当前阶段展开具体工作，近期阶段保留初步计划，远期阶段只有概括方向。Task 全部结束也不会自动推进 Slot。

`src/data/mockMessages.ts` 根据固定 Mock 阶段快照建立独立消息记录，包括指令下发、任务派发、进度回报/转交、报告回传/转交与审核反馈。待派发箭头明确标为「未发送」；未知结果只画 Core 本地观测注记，不伪造 Agent 回传。图中没有 Agent 间自动交接。时间轴选择较早任务时会将相关消息滚入画布可见区域，窄窗口支持画布内部横向滚动。

聊天未来可将 `ChatWorkspace` 中的 `.webview-host` 作为宿主区域边界，映射 Electron `WebContentsView` 的位置与可见性；当前不实现此协议，也没有真实 ChatGPT 项目映射。

## 检查与截图

```powershell
npm test
```

本机测试默认使用已安装的 Microsoft Edge。其他环境如未安装可用浏览器，先执行 `npx playwright install chromium`；也可通过 `DEMO_BROWSER` 指定浏览器可执行文件。测试会自行启动或复用 5176 端口的 Demo。

截图：

- [工作页 · 1440×900](screenshots/work.png)
- [任务详情 · 1440×900](screenshots/task-detail.png)
- [工作页与详情 · 1600×900](screenshots/work-1600.png)
- [聊天占位页 · 1600×900](screenshots/chat.png)
- [泳道消息选择与详情联动](screenshots/swimlane-message.png)
- [窄窗口泳道](screenshots/swimlane-narrow.png)
- [Workspace 工作路线 · 1440×900](screenshots/workflow.png)
- [查看历史阶段及成果](screenshots/workflow-history.png)
- [阶段任务与既有详情联动 · 1600×900](screenshots/workflow-task-detail.png)
- [窄窗口阶段路线 · 900×900](screenshots/workflow-narrow.png)

详细结果见 [验证记录](docs/verification.md)。

## 下一轮视觉讨论建议

1. 在真实桌面窗口中比较 13px 与 14px 正文，以及侧栏 240px 与 260px 的阅读舒适度。
2. 确认详情面板默认常驻还是按需展开，并讨论长任务标题与更多 Agent 时的信息密度。
3. 固化状态文案、选中态、边框和间距为正式设计 token，再用真实脱敏记录校准内容长度。

本 Demo 用于前端设计验证，不包含正式后端或 Electron 集成。
