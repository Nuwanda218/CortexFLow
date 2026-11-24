# 四级工作空间 · 最小交互实现

本轮范围：Project → Stage → Work Task → Execution，在同一项目标签内导航；不增加全局层级标签、真实派发、Context Worker、Git 同步或后端接口。原工作台组件继续保留。

统一实体在独立 TypeScript Mock 模块关联。现有项目开发树作为历史素材，整理为 Project.stageIds、Stage.taskIds、WorkTask.executionIds 与对应归属字段；页面和 DevelopmentNavigator 均从同一实体索引读取。当前阶段改为「项目工作空间设计」，包含指定六项工作；主 Task「阶段与任务页面设计」包含多次模拟实施。Task 与单次 Agent 记录不混用。

统一 DevelopmentNavigator：Project 尺度显示项目和阶段；Stage 尺度显示项目参照、阶段邻居及焦点阶段的 Task；Task 尺度显示直接上级 Stage、同阶段 Task 和焦点 Task 的 Execution；Execution 尺度显示直接上级 Task 与实施记录邻居。选中样式代表浏览焦点，状态点代表实际记录进度。

面包屑允许任意上级回退；标题点击进入，独立展开按钮只折叠。所有内部路径仍属于同一 projectId。项目标签记住其最后内部路径；项目内按焦点键保存滚动，展开集合归属项目，内容用 KeepAlive 保留提示词折叠等局部状态。未知或跨归属路径回退到项目，不渲染错误实体。

Task / Execution 使用安全 Markdown 渲染，禁用原始 HTML 与图片加载。Execution 委托可展开和复制，过程仅列显式步骤；null 过程显示未记录，不根据报告填补。文件/提交仅为标明 Mock 的引用，内容不暗示真实运行。

验证：完整首页→项目→阶段→任务→实施链路、面包屑、同层切换、标签去重与内部状态、空实施、缺失过程、Markdown 标题/列表/代码/引用、委托复制、非法跨归属路径、桌面/窄窗口、四级截图。先跑新增用例确认缺失，再实现、构建和验收。
