# 项目首页、标签页与项目概览验收

日期：2026-11-24（Asia/Shanghai）。仅修改 `D:\CortexFlow\cortexflow-ui-demo`。

## 实际结果

- `npm run build` 通过：TypeScript 检查和 Vite 生产打包成功。
- `npm test -- --timeout=15000`：18 passed，约 14 秒，使用本机 Edge。
- 本机 HTTP 入口：`http://127.0.0.1:5176/`。
- 三标签概览演示：`http://127.0.0.1:5176/#/project/cortex?demo=overview`。
- 页面运行/控制台 error 检查通过，无外部服务请求。
- 实际截图经目视检查：1440×900、1600×900、900×900；项目首页、CortexFlow 概览、展开历史、LoongArch 独立树、窄窗口布局正常。

## 检查覆盖

1. 五个指定项目，唯一 ID，数据仅含 ID 与四个展示字段。
2. 首页只有名称、概况、最近 Task、修改日期，无旧导航、状态标签或统计元素。
3. 项目搜索、大小写匹配、无结果与清空搜索。
4. 多项目在应用内分别打开，重复打开只聚焦原标签。
5. 切换保留项目 DOM/组件实例及首页搜索值。
6. 关闭后台项目不切活动页，关闭最后项目回到首页。
7. 方向键、Home/End、Delete；首页不会被关闭。
8. 打开项目后焦点转入新标签，离开隐藏的首页行。
9. 新建项目、重名校验、自动打开新标签。
10. 旧聊天/工作地址回首页，项目直达和未知项目回退。
11. 多标签溢出时首页固定可见，项目标签内部滚动。
12. 桌面/窄窗口渲染，无文档横向溢出、页面异常或外部请求。
13. 三标签演示聚焦 CortexFlow；固定首页名称为「全部项目」。
14. 项目内只有概览导航，主体无右侧详情、Agent、统计或工作流面板。
15. 历史默认折叠，当前默认展开；阶段展开/收起与嵌套分支正常。
16. 每个项目拥有独立开发树，切换标签保留各自展开与滚动位置。
17. 未来路径为灰色虚线；Task 叶节点未接入深层交互。
18. 开发数据 ID 唯一，未知日期 null，历史 A/B/C 为 implemented 而非 completed，不虚构正式验收。

打开项目焦点和多标签首页可见性均先复现失败，再修复并通过回归。

## 截图

- `screenshots/project-home-1440.png`
- `screenshots/project-home-1600.png`
- `screenshots/project-tabs.png`
- `screenshots/project-home-narrow.png`
- `screenshots/project-tabs-narrow.png`
- `screenshots/project-overview-1440.png`
- `screenshots/project-overview-1600.png`
- `screenshots/project-overview-expanded.png`
- `screenshots/project-overview-loongarch.png`
- `screenshots/project-overview-narrow.png`

## 验证范围

当前测试入口包含项目首页、标签机制与默认项目概览。旧工作页用例保留为历史来源且不计入本轮通过数量；对应旧功能不挂载于概览。旧说明保存在 `previous-workspace-demo.md` 和 `previous-workspace-verification.md`。

所有项目概况与开发树均为 Mock，不是正式阶段划分或验收记录。开发树未知日期为空，仅首页交付使用前轮确证的日期；其他项目不补造完成历史。新建项目、搜索、标签和展开状态仅在当前页面会话内保留，刷新恢复初始数据。

本轮不实现编辑器、自动总结、Git 同步或 Task 详情跳转。已有 Workflow、消息时序、任务详情和执行记录组件未删除；原占位容器也保存为 `ProjectWorkspacePlaceholder.vue`。未连接后端，未修改 Core、Bridge 或仓库原先已有的未提交内容。
