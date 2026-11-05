# 需求追溯索引

需求正文与证据只在[功能需求](../02-requirements/functional-requirements.md)、[非功能需求](../02-requirements/non-functional-requirements.md)维护；本表是导航关系，不是第二份需求规格。每条需求内均有状态、来源与验收草案。

痛点 P01–P05、目标 G01–G05 定义见[产品愿景](../01-product/vision.md#痛点与目标)。这里的关联为整理者归纳，不提升任何条款的确认状态。

## 一个完整追溯链

P01：跨工具窗口切换与人工转发 → G01：降低协调成本 → [FR-02](../02-requirements/functional-requirements.md#fr-02)：统一消息流 → [交互：消息流](../03-design/interaction-design.md#ix-message) → [AC-01 步骤 6](../01-product/user-scenarios.md#ac-01)：无需切换 Codex 窗口即可查看任务结果。

原始依据：[S01-A3](../00-audit/material-audit.md#s01-a3)，L104–144；[S01-B3](../00-audit/material-audit.md#s01-b3)，L839–854。方案依据见 D01、D12。

## 功能需求 → 设计 → 验收

“条内验收”链接到对应需求的验收草案，用于没有独立端到端场景的细则；不会假称全部 24 条已经被单一场景覆盖或验证通过。

| 需求 | 痛点 → 目标 | 主要设计定位 | 验收定位 | 重要方案 |
| --- | --- | --- | --- | --- |
| [FR-01](../02-requirements/functional-requirements.md#fr-01) | P01、P05 → G01、G05 | [统一输入](../03-design/interaction-design.md#ix-message) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 1、5 | D02 |
| [FR-02](../02-requirements/functional-requirements.md#fr-02) | P01、P04 → G01、G04 | [消息流](../03-design/interaction-design.md#ix-message) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 6 | D01、D12 |
| [FR-03](../02-requirements/functional-requirements.md#fr-03) | P01、P03 → G01、G03 | [建议卡片](../03-design/interaction-design.md#ix-draft) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 2；条内验收 | D03 |
| [FR-04](../02-requirements/functional-requirements.md#fr-04) | P03 → G03 | [建议卡片](../03-design/interaction-design.md#ix-draft) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 3 | D04 |
| [FR-05](../02-requirements/functional-requirements.md#fr-05) | P01、P02、P03 → G01–G03 | [闭环](../03-design/workflow-design.md#wf-loop)、[状态](../03-design/workflow-design.md#wf-task) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 4、6 | D04、D11 |
| [FR-06](../02-requirements/functional-requirements.md#fr-06) | P01、P04 → G01、G04 | [详情](../03-design/interaction-design.md#ix-detail) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 6 | D12 |
| [FR-07](../02-requirements/functional-requirements.md#fr-07) | P01、P04 → G01、G04 | [回传与队列](../03-design/workflow-design.md#wf-queue)、[内容边界](../03-design/context-management.md#ctx-transfer) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 7；[AC-02](../01-product/user-scenarios.md#ac-02) | D08、D12 |
| [FR-08](../02-requirements/functional-requirements.md#fr-08) | P04 → G04 | [队列](../03-design/workflow-design.md#wf-queue) | [AC-02](../01-product/user-scenarios.md#ac-02) | D08 |
| [FR-09](../02-requirements/functional-requirements.md#fr-09) | P03、P04 → G03、G04 | [队列操作](../03-design/interaction-design.md#ix-queue) | [AC-02](../01-product/user-scenarios.md#ac-02)；条内验收 | D08 |
| [FR-10](../02-requirements/functional-requirements.md#fr-10) | P04 → G04 | [通知呈现](../03-design/ui-design.md#ui-queue) | [AC-02](../01-product/user-scenarios.md#ac-02) | D08 |
| [FR-11](../02-requirements/functional-requirements.md#fr-11) | P02 → G02 | [流程图](../03-design/workflow-design.md#wf-flow) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 3、6；条内验收 | D06 |
| [FR-12](../02-requirements/functional-requirements.md#fr-12) | P02 → G02 | [流程关系](../03-design/workflow-design.md#wf-flow) | 条内验收（并行 / 汇合数据，非已批准运行场景） | D06、D11 |
| [FR-13](../02-requirements/functional-requirements.md#fr-13) | P02 → G02 | [消息泳道](../03-design/workflow-design.md#wf-swim) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 9 | D07 |
| [FR-14](../02-requirements/functional-requirements.md#fr-14) | P02、P04 → G02、G04 | [选中详情](../03-design/interaction-design.md#ix-detail) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 6、9 | D05、D12 |
| [FR-15](../02-requirements/functional-requirements.md#fr-15) | P02、P03 → G02、G03 | [视图关联](../03-design/workflow-design.md#wf-link) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 9；条内验收 | D07 |
| [FR-16](../02-requirements/functional-requirements.md#fr-16) | P04 → G04 | [面板](../03-design/ui-design.md#ui-panels) | [AC-03](../01-product/user-scenarios.md#ac-03) | D05 |
| [FR-17](../02-requirements/functional-requirements.md#fr-17) | P04 → G04 | [布局预设](../03-design/ui-design.md#ui-presets) | [AC-03](../01-product/user-scenarios.md#ac-03) | D10 |
| [FR-18](../02-requirements/functional-requirements.md#fr-18) | P04 → G04 | [静默控制](../03-design/interaction-design.md#ix-queue) | [AC-02](../01-product/user-scenarios.md#ac-02) | D10 |
| [FR-19](../02-requirements/functional-requirements.md#fr-19) | P02、P03 → G02、G03 | [工作空间](../03-design/context-management.md#ctx-workspace) | 条内验收（跨空间信息区分）；AC-01 仅覆盖进入空间 | D09 |
| [FR-20](../02-requirements/functional-requirements.md#fr-20) | P03、P05 → G03、G05 | [信息种类](../03-design/context-management.md#ctx-types)、[传递](../03-design/context-management.md#ctx-transfer) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 5；条内验收 | D09 |
| [FR-21](../02-requirements/functional-requirements.md#fr-21) | P01、P03 → G01、G03 | [显式指派](../03-design/interaction-design.md#ix-message) | 条内验收（单目标待确认任务） | D04 |
| [FR-22](../02-requirements/functional-requirements.md#fr-22) | P03 → G03 | [执行控制](../03-design/interaction-design.md#ix-control) | 条内验收；运行中暂停标准未定义 | D04 |
| [FR-23](../02-requirements/functional-requirements.md#fr-23) | P02、P03 → G02、G03 | [异常控制](../03-design/interaction-design.md#ix-control) | [AC-04](../01-product/user-scenarios.md#ac-04) | D04 |
| [FR-24](../02-requirements/functional-requirements.md#fr-24) | P02、P05 → G02、G05 | [协作闭环](../03-design/workflow-design.md#wf-loop)、[角色职责](../04-architecture/architecture-overview.md#arch-responsibilities) | [AC-01](../01-product/user-scenarios.md#ac-01)，步骤 2、8；信息可达方式未验证 | D02、D03、D09 |

## 质量目标追溯

| 目标 | 痛点 → 产品目标 | 对应设计 | 验收 / 缺口 |
| --- | --- | --- | --- |
| [NFR-01](../02-requirements/non-functional-requirements.md#nfr-01) | P01、P04 → G01、G04 | 消息与详情分层，三栏聚焦 | AC-01、AC-03；改善基线 Q16 |
| [NFR-02](../02-requirements/non-functional-requirements.md#nfr-02) | P02、P03 → G02、G03 | [共享协作事实](../03-design/workflow-design.md#wf-link) | 状态与传递事实条内验收；恢复 Q08、Q16 |
| [NFR-03](../02-requirements/non-functional-requirements.md#nfr-03) | P04 → G04 | [队列与静默](../03-design/interaction-design.md#ix-queue) | AC-02；自动判据 Q03 |
| [NFR-04](../02-requirements/non-functional-requirements.md#nfr-04) | P03、P05 → G03、G05 | [信息来源](../03-design/context-management.md#ctx-types) | 需求条内验收；保存 Q07 |
| [NFR-05](../02-requirements/non-functional-requirements.md#nfr-05) | P03 → G03 | [审阅](../03-design/interaction-design.md#ix-draft)、[相关性](../03-design/context-management.md#ctx-transfer) | AC-01 只读限制；强制权限、安全政策未验证 |
| [NFR-06](../02-requirements/non-functional-requirements.md#nfr-06) | P01 → G01 | [角色与工具边界](../04-architecture/architecture-overview.md#arch-responsibilities) | 概念检查，不要求首版通用插件实现 |
| [NFR-07](../02-requirements/non-functional-requirements.md#nfr-07) | P02、P04 → G02、G04 | [视觉规则](../03-design/ui-design.md#ui-visual) | 条内可辨认性检查，量化标准未定义 |

## 未决事项的回写方向

Q01 → 确认粒度；Q02 / Q06 → 对话接入与会话边界；Q03 / Q10 → 回传与专注策略；Q04 → 提取触发；Q05 / Q09 → 首版与并行；Q07 / Q15 → 保存与队列操作；Q08 → 异常与控制；Q11 / Q13 / Q14 → 图与画布规则；Q12 → 多接收者；Q16 → 验证指标。

完整问题、候选方案、证据和受影响条款只维护在[未决问题](../02-requirements/open-questions.md)，此处不复制其完整讨论。
