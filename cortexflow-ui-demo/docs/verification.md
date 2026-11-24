# 四级工作空间检查记录

日期：2026-11-24（Asia/Shanghai）。本轮仅修改独立 Vue Demo。

## 实际检查结果

- `npm run build` 通过：vue-tsc 类型检查和 Vite 生产构建成功。
- `npm test -- --timeout=15000`：**24 passed，18.5 秒**，本机 Edge。
- 入口：`http://127.0.0.1:5176/#/project/cortex?demo=overview`。
- 浏览器截图覆盖 1440×900、1600×900、900×900，无页面异常或控制台 error。
- 首页检查无外部服务请求；工作区使用本地关联 Mock，Markdown 禁用原始 HTML 与图片。
- 四级完整内容截图已生成并目视检查；窗口中索引和文档各自滚动。完整截图临时展开滚动区捕获，不代表实际窗口被拉长。

## 本轮覆盖

1. 保留五项目首页、搜索、创建/重名校验、固定首页标签和项目标签键盘操作。
2. 在单个项目标签内部完成 Project → Stage → Task → Execution 导航，不增加层级标签。
3. 面包屑直返上级；阶段展开按钮与名称入口互不混淆。
4. 统一索引随尺度变化，提供上级、同层和直接下层节点；焦点与开发状态独立。
5. 历史阶段、未来阶段、六个当前阶段 Task 和多次实施由同一关联模块派生。
6. 数据 ID 唯一、实体归属互相对应、无孤立 Task/Execution；完成实施不自动完成产品 Task。
7. Task 文档包含标题、列表和代码块；实施列表支持三条记录及无记录状态。
8. 委托展开、复制到剪贴板；明确步骤、工具记录、原始报告和文件/Mock 提交引用正确呈现。
9. 即使存在报告，缺失过程仍显示未记录，unknown 不推测为失败或完成。
10. 切换 CortexFlow 与 LoongArch 保留当前内部路径、选中节点及文档滚动。
11. 返回项目恢复用户选择的树折叠状态，键盘导航将焦点转到新页面标题。
12. 跨项目伪造归属路径安全回退；窄窗口无整页横向溢出。

补齐缺失 favicon 后，全量构建和检查再次通过。折叠恢复与键盘焦点先复现失败，再修复并通过回归。完整页面链路中的报告标题与页面标题分别定位，避免测试把 Markdown 内部标题误认为页面标题。

## 截图

- `screenshots/levels-project-full.png`：Project 完整开发树。
- `screenshots/levels-stage-full.png`：Stage 目标与六项任务。
- `screenshots/levels-task-full.png`：Task 全文及三次实施。
- `screenshots/levels-execution-full.png`：Execution 展开委托、过程、完整报告和引用。
- `screenshots/levels-{project,stage,task,execution}.png`：1440×900 原始视口。
- `screenshots/levels-execution-1600.png`、`screenshots/levels-narrow.png`：1600×900 与 900×900。

## 范围与限制

所有业务内容为 Mock。历史 A/B/C 仍是已实现待验证，未知历史日期不补造。本轮新增模拟实施日期、报告和引用不作为真实开发验收结论。界面 Mock 标识位于层级路径栏。

页面会话内保存状态；刷新重置，关闭项目标签释放状态。不实现编辑器、上下文自动归纳、Agent 调度、Core 接口、数据库或 Electron。

当前测试入口是 `project-home.spec.ts`、`project-data.spec.ts`、`workspace-levels.spec.ts`。旧概览/工作页用例及旧富详情组件保留源码，但不计入本轮通过数量。未改动仓库已有的 Core/Bridge 业务和其他未提交内容。

