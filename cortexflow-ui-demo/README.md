# CortexFlow Desktop · 项目首页与概览 Demo

当前版本以项目作为唯一入口，提供精简项目首页、应用内多标签页和项目默认概览。沿用现有 Vue 3、TypeScript、Vite、Vue Router 与 Lucide，不重建工程、不连接后端、不修改 Core。

## 启动

```powershell
cd D:\CortexFlow\cortexflow-ui-demo
npm ci
npm run dev
```

访问 http://127.0.0.1:5176/ 。生产构建执行 `npm run build`，生产预览执行 `npm run preview`（默认 4173 端口）。已安装依赖时可直接启动。

三标签概览演示：[CortexFlow 概览](http://127.0.0.1:5176/#/project/cortex?demo=overview)。此地址预备「全部项目」「CortexFlow」「LoongArch-CPU-2027」三个标签，聚焦 CortexFlow；普通首页和点击项目的行为保持不变。

## 本轮界面

- 首页逐行展示 CortexFlow、LoongArch-CPU-2027、uxuescript、nuwanda.space、writing_reading。
- 每行仅显示项目名称、概况、最近修改日期和最近 Task，整行可点击；支持按名称与概况搜索、新建项目和重名校验。
- 「全部项目」首页标签固定在左侧，不可关闭。项目标签支持打开、去重聚焦、切换与关闭；标签过多时只滚动项目标签，首页保持可见。
- 切换保留各页面实例及首页搜索/滚动状态。关闭项目释放页面，重新打开获得新实例。关闭活动项目选择右邻，否则左邻或首页；关闭后台项目不切换当前项目。
- 标签可用方向键、Home/End 切换，Delete 关闭项目；首页忽略 Delete。
- 进入项目默认显示概览，左侧仅有「概览」入口。主体为项目名、一句话概况与纵向开发树，无统计、Agent 列表或右侧详情。旧路径 `/#/work` 与 `/#/chat` 返回首页，`/#/project/cortex` 可直接打开项目概览。
- 历史阶段默认折叠，当前阶段默认展开，未来方向通过灰色虚线路径延续。阶段支持展开/折叠，Task 叶节点不进入深层页面。每个项目的树结构、展开状态和滚动位置独立，切换标签后保留。
- 历史 A/B/C 使用「已实现 · 待验证」，不等于正式验收；未知日期显示「—」。仅已确证的项目首页交付使用已有日期记录，其余日期不补造。其他项目只有独立的 Mock 目标路线，不虚构处理器等项目的完成历史。

所有项目概况与开发树均为 **Mock**，不是正式阶段划分或验收记录。首页日期也是固定演示数据；开发树的未知日期留空。新建项目仅保存在页面内存中，刷新恢复初始快照；新项目显示「暂无开发记录」，不自动生成历史。界面不增加解释性横幅、统计或产品说明文案。

## 实现结构

```text
src/main.ts                         路由标识与旧路径重定向
src/components/AppShell.vue         应用外壳，保持打开页面实例
src/components/TopNavigation.vue    轻量应用栏与原标志
src/components/ProjectTabs.vue      固定首页、项目标签、键盘交互
src/components/ProjectHome.vue      四项信息列表、搜索、新建入口
src/components/ProjectWorkspace.vue 项目内部外壳，仅概览导航与独立滚动区
src/components/ProjectOverview.vue  项目标题、概况与独立树展开状态
src/components/DevelopmentTreeNode.vue 递归阶段/Task/分支与状态、日期呈现
src/components/NewProjectDialog.vue 项目创建表单，复用 DemoDialog
src/composables/useProjectTabs.ts    项目集合、活动标签与打开顺序
src/data/homeProjects.ts            独立五项目 Mock，无 Conversation 关联
src/data/projectDevelopment.ts      整理后的阶段、Task、父子结构与可空日期
src/project-home.css               首页、标签及窄窗口布局
src/project-overview.css           纵向开发主线、分支、状态与概览布局
```

路由仅标识活动标签，页面由外壳按项目稳定键保留，并用 `v-show` 切换；不会因切换路由而销毁其他项目页面。原占位页保留为 `ProjectWorkspacePlaceholder.vue`。已有 WorkWorkspace、Workflow、时序图、TaskDetail 和执行记录组件均未删除，后续可讨论作为具体 Task 的子页面，本轮不挂接这些入口。

## 验证与截图

```powershell
npm run build
npm test
```

测试默认使用本机 Edge；没有系统浏览器时可先运行 `npx playwright install chromium`。`DEMO_BROWSER` 可指定浏览器路径。

当前启用 `tests/project-home.spec.ts`、`tests/project-data.spec.ts` 与 `tests/project-overview.spec.ts`，验证首页、标签、概览、树状态及数据语义。旧工作页浏览器用例仍保留在源码中，不作为当前页面验收，不把以前的通过数量计入本轮。

- [首页 · 1440×900](screenshots/project-home-1440.png)
- [首页 · 1600×900](screenshots/project-home-1600.png)
- [两个项目标签及概览](screenshots/project-tabs.png)
- [首页 · 900×900](screenshots/project-home-narrow.png)
- [多标签溢出时首页固定可见](screenshots/project-tabs-narrow.png)
- [CortexFlow 概览 · 1440×900](screenshots/project-overview-1440.png)
- [CortexFlow 概览 · 1600×900](screenshots/project-overview-1600.png)
- [展开历史阶段](screenshots/project-overview-expanded.png)
- [LoongArch 独立开发树](screenshots/project-overview-loongarch.png)
- [概览窄窗口 · 900×900](screenshots/project-overview-narrow.png)

[首页设计](docs/project-home-design.md) · [概览设计](docs/project-overview-design.md) · [验证记录](docs/verification.md)

此前工作页组件与数据暂留源码，当前入口不加载。历史说明见 [上一版工作页文档](docs/previous-workspace-demo.md)，不代表当前页面行为。
