# CortexFlow Desktop · 四级工作空间 Demo

基于现有 Vue 3、TypeScript、Vite 工程，提供项目首页、项目标签和 Project → Stage → Work Task → Execution 四级浏览。所有业务内容为 Mock，不连接 Core、数据库或真实 Agent。

## 启动

```powershell
cd D:\CortexFlow\cortexflow-ui-demo
npm ci
npm run dev
```

访问 [本地首页](http://127.0.0.1:5176/)。[三标签演示](http://127.0.0.1:5176/#/project/cortex?demo=overview)预备全部项目、CortexFlow、LoongArch-CPU-2027，聚焦 CortexFlow。

```powershell
npm run build
npm run preview
npm test
```

生产预览默认 4173 端口。浏览器检查默认使用本机 Edge；也可通过 `DEMO_BROWSER` 指定可执行文件，或运行 `npx playwright install chromium` 安装测试浏览器。

## 已实现交互

- 全部项目固定首页；搜索、新建、重复项目去重打开、标签切换和关闭沿用原实现。
- 全局标签只切项目。阶段名称进入 Stage，任务名称进入 Task，实施列表进入 Execution；树展开按钮只负责展开和收起。
- 面包屑可以回到任意上级。浏览器前进/后退、直接访问内部地址也会更新同一个项目工作区；不合法或跨项目的节点路径回到项目。
- 统一 DevelopmentNavigator 从同一棵树投影当前尺度：保留直接上级、同层节点、焦点节点的直接下层。选中底色表示浏览位置，节点符号表示工作状态。
- 保留每个项目的当前路径、节点、树展开状态、内容滚动位置与实施委托展开状态。返回同一层次恢复该处滚动；切换项目保留页面实例。关闭标签释放该项目状态；刷新恢复 Mock 初始快照。
- Task 使用 Markdown 上下文和独立实施列表。一项 Task 可包含多次实施；单次实施结束不会自动完成 Task。没有实施时显示空记录状态。
- Execution 支持原始委托展开/收起和复制，显示明确记录的工具步骤、原始 Markdown 报告、文件/模拟提交引用。过程缺失显示「未记录」，unknown 与失败区分。
- 键盘进入下级后焦点转到内容标题。纵向索引和主体分别滚动，支持桌面与较窄窗口。

## 数据与组件

```text
src/data/workspaceGraph.ts                  四级关联实体、统一树和路径派生
src/composables/useDevelopmentWorkspace.ts  项目内焦点、归属校验、展开和滚动
src/composables/useProjectTabs.ts            全局标签、各项目最近路径
src/components/ProjectWorkspace.vue        工作区容器，保留各节点页面实例
src/components/DevelopmentNavigator.vue    根据当前尺度投影同一开发树
src/components/NavigatorNode.vue           递归纵向索引，分离焦点与进度
src/components/WorkspaceBreadcrumbs.vue    可点击层级路径
src/components/ProjectOverview.vue         项目标题、概况、完整阶段树
src/components/StageOverview.vue           阶段目标、更新时间、任务树
src/components/TaskOverview.vue            Markdown 上下文、实施列表
src/components/ExecutionOverview.vue       委托、已记录过程、报告和引用
src/components/MarkdownView.vue            Markdown 渲染，禁用原始 HTML 和图片
src/components/DevelopmentTreeNode.vue     复用树形语言，展开与进入分开
src/workspace-levels.css                    四级页面和动态导航的统一样式
```

`workspaceGraph.ts` 复用前轮整理的历史种子，再统一派生四个页面与索引，避免各页面单独维护任务副本。CortexFlow 包含 A/B/C 历史阶段、项目工作空间设计和工程知识服务集成；当前阶段有六项任务，主 Task「阶段与任务页面设计」包含三次模拟实施，其中一次缺失过程且终态未知。LoongArch 等项目有各自的独立规划。

界面层级路径标有 **Mock**。历史 A/B/C 保留「已实现 · 待验证」边界，未知日期为空；实施时间、委托、报告和提交引用都是虚构演示，不是实际执行或验收证据。新增项目保持空开发记录。

原 WorkWorkspace、Workflow、消息时序图、TaskDetail 等组件保留源码，当前入口不挂载。不实现编辑、自动总结、Context Worker、真实工作派发或 Electron。

## 截图与检查

完整内容截图（将滚动区展开后捕获，实际界面仍在窗口内滚动）：

- [Project 项目概览](screenshots/levels-project-full.png)
- [Stage 阶段概览](screenshots/levels-stage-full.png)
- [Task 任务概览](screenshots/levels-task-full.png)
- [Execution 实施过程](screenshots/levels-execution-full.png)

另有 `levels-project.png`、`levels-stage.png`、`levels-task.png`、`levels-execution.png` 四张 1440×900 视口截图，以及 1600×900 和 900×900 截图。

[实现计划](docs/four-level-workspace-plan.md) · [检查记录](docs/verification.md)。当前启用首页、数据与 `workspace-levels.spec.ts`；旧概览/工作页用例保留为历史来源，不计入本轮结果。
