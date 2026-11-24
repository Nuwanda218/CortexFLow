# Workspace Workflow / Task Slot 设计与实施

## 目标与边界

按用户规格在工作页首层加入阶段路线，不替换既有消息泳道和任务详情。Workspace 映射现有 Project，一个 Workspace 至多一份当前 Workflow，切换 Conversation 不切换路线。无后端、调度、DAG、规划模型或版本管理。

## 表示与视觉

Workflow 保存 workspaceId、总体目标、单一 currentSlotId 和有序 Slots。Slot 仅引用既有 taskIds，不复制 Task 状态；历史结论单独保存，Task 执行完成不自动完成 Slot。完成状态为明确的 Mock 阶段结论。下一阶段根据当前阶段之后的位置识别，远期阶段允许没有 Task。

四段横向阶段路线与消息泳道使用不同视觉结构。当前阶段蓝色轻强调，已完成绿色，未来中性。当前阶段默认展开；仅一个查看面板展开，可点击阶段展开/收起。查看焦点和实际当前阶段分别表示，浏览历史或后续计划不改变 currentSlotId。

展开阶段左侧展示目标、约束、计划或成果，右侧为紧凑可滚动 Task 子列表。点击子任务清除任务筛选、必要时切来源对话，然后复用 TaskDetail 和 ExecutionTimeline；不把整个页面强制滚离路线。

未来计划可本地编辑目标/初步计划，可在未来阶段内前后调整顺序。只修改有效规划，不保留旧规划版本；Task 引用与结果保留。当前阶段可新增本地 Mock 补充 Task，初始等待且未派发，已完成 Task 不重新运行。重置演示同时恢复路线、任务和查看状态。

## 文件职责与验证

- `src/data/mockWorkflows.ts`：两工作空间的路线、阶段目标与 Task 引用。
- `src/composables/useWorkflow.ts`：聚合现有 Task，阶段查看/编辑/排序与任务联动。
- `src/composables/useWorkspace.ts`：共用 Workflow 状态、Task 创建与统一重置。
- `src/components/WorkflowRoadmap.vue`：目标与阶段路线。
- `src/components/WorkflowSlotDetail.vue`：按阶段层级展示细节和子任务。
- `src/components/WorkflowPlanDialog.vue`、`WorkflowTaskDialog.vue`：轻量编辑/补充表单，复用 DemoDialog。
- `src/workflow.css`：独立轻量路线视觉与窄窗口适配。
- `tests/workflow.spec.ts`：当前/查看焦点分离，跨对话联动，单源状态，规划编辑，补充任务不改历史，统一重置与窄窗口。

步骤：先跑新增用例确认旧版缺少路线；再实现独立数据、聚合状态、路线与详情；最后检查构建、全部交互、桌面与窄窗口截图。所有数据与修改均明确为 Mock。
