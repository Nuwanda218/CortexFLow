# CortexFlow Desktop 第一轮 Demo

用户已指定 Vue 3 + TypeScript + Vite、独立目录、本地 Mock 与 ChatGPT 浅色风格。当前实现范围仅为设计验证原型。

## 设计

58px 顶栏，248px 浅灰侧栏，白色工作区。顶部聊天/工作分段导航；项目和对话沿用轻量树形导航。工作页采用流程、任务列表、执行阶段三层信息，右侧详情按需打开。小窗口详情转为遮罩抽屉，侧栏可折叠。

所有内容统一标记 MOCK；已配置不表示在线。根据后续反馈，工作页已由节点分支图改为五条顺序泳道，按时间展示 Supervisor、Core 与 Agent 之间的消息往返；不绘制 Agent 间自动交接。待派发明确标为未发送，未知结果为本地观测。等待、执行中、已完成、失败、未知均有文字与图标。更新设计见 `swimlane-design.md`。

## 边界

全部文件位于 cortexflow-ui-demo/。不修改已有文档与 Core，不连接 API、SQLite、Electron 或 ChatGPT。不存储真实凭据。聊天容器保留独立组件和宿主替换边界。

## 实施与验收

- [x] 写浏览器交互验收用例，确认初始空壳不能通过。
- [x] 建立类型、独立 Mock 模块与组合式状态；实现上下文切换、筛选与节点联动。
- [x] 拆分 AppShell、TopNavigation、ProjectSidebar、ChatWorkspace、WorkWorkspace、AgentFlow、TaskList、ExecutionTimeline、TaskDetail。
- [x] 加入新建项目/对话、可关闭详情、报告预览、重置演示、键盘可操作弹窗与反馈。
- [x] 执行类型检查和生产构建，浏览器检查必需交互及空/失败/未知状态。
- [x] 检查 1440×900、1600×900、窄窗口；输出聊天、工作、详情截图与启动说明。

状态源为结构化 Mock 快照，UI 不模拟真实运行。新建仅在当前页面内存有效，刷新恢复固定数据。
