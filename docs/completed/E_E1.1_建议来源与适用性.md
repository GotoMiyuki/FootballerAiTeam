# E1.1：建议来源与适用性——已完成开发汇总

截至 2026-10-02，已完成 E1.1 的训练焦点建议投影、来源核验、防重、只读应用/API/UI 和独立事件查询。玩家选择、执行反馈、效果评估及后续闭环尚未实施，不将 E1 整体标记完成。返回 [汇总索引](./README.md)。

## 1. 建议如何形成

已发布任务中有效的 `skill_training.focus_areas` 被确定性映射为独立建议，每个焦点一条。建议不由报告散文猜测，不等于用户采纳，也不修改 [A 的球员事实](./A_球员数据与事实边界.md)。

流程为：读取原任务完整固定输入与 checkpoint → 核验 B 的有效成果、审查版本和正文发布门槛 → 按子任务/成果版本映射 → 保存建议、批次与事件 → 只读查询再次核对原来源 → 用同 context 最新快照判断适用性。新数据不会覆盖原建议、固定基线或原报告。

主要代码：[来源读取](../../career_actions/sources.py)、[应用服务](../../career_actions/application.py)、[对象](../../career_actions/models.py)、[存储与事件](../../career_actions/repository.py)、[工作台面板](../../frontend/src/features/mission/RecommendationsPanel.tsx)。

## 2. 来源门槛与稳定身份

`MissionResultReader` 在同进程内只读原 SQLite checkpoint 和 `mission_inputs`，不调用模型、工具或图，也不向前端公开完整内部状态。只有实际任务运行路径、VERIFIED 输入、有效发布报告和当前成果/审查一致时可投影；工作流 demo 不能冒充已验证来源。

核对专业结果结构、validated/CURRENT、输入指纹、当前成果版本及审查覆盖；公开 Plan/Report/Review 与 checkpoint 必须一致，公开审查为 COMPLETED/PASS。完整输入的 schema/mode/hash、快照身份与公开引用相同，再按子任务调用 `validate_specialist`。非空报告或 `domain_outputs[Agent]` 不能代替合法来源。

映射版本 `training-focus-v1`。来源键由 Mission、subtask、result version、`/focus_areas/{index}` 和映射版本组成，生成稳定建议 id；同 Agent 的不同任务独立，新成果版本新建 id，不覆盖旧内容。每成果最多 32 个焦点，结构化 payload 最多 32,000 字符，超预算明确不可投影。没有焦点的合法报告可以正常发布，建议为空。

游戏版本只取固定快照当前字段 origins 引用的来源，不使用未应用的 RECORDED_ONLY 观察。没有 D 的实际操作证据，故执行支持始终 `pending_verification`；缺少指标/量表/单位/窗口时 evaluation_spec 为 `undefined`，不显示效果分。

## 3. 对象、存储与事件

| 对象部分 | 保存内容 |
| --- | --- |
| 身份 | recommendation_id、初始 revision 1、career/branch/player |
| source | Mission/subtask、capability、成果版本、payload 位置/hash、输入指纹、审查及映射版本 |
| content | 原焦点、预期目标、依据和限制 |
| applicability | 原输入引用、固定条件、已知游戏版本；模式/窗口未知原因 |
| execution_support | 待验证状态，操作证据引用为空 |
| evaluation_spec | 固定基线引用、空指标、未知窗口及原因 |
| 读取视图 | validity、validity_reason、checked_state_version |

复用任务目录 `workspace.sqlite3`，增量建立 recommendations、recommendation_batches、recommendation_events 三表。插入、旧版替代、事件和批次同事务提交；来源唯一约束与 Store 锁支持重复/并发投影。相同来源的内容、审查、基线或规范冲突时回滚，不覆盖。重放保留原创建时间，已替代/撤回记录不自动激活。

事件为 `recommendation.created/superseded/withdrawn`，带 event_id、全局递增 sequence、schema_version、建议 id/revision、context、source 与时间。按 Mission/after 查询，每页最多 200 项，单个 Mission 序号不保证连续。它们独立于 Mission SSE；created 不是永久有效或已采纳的证明。

## 4. 投影时机与读取适用性

`MissionService.complete()` 在报告门槛通过、正文保存后投影，再发布任务完成。服务启动对已完成任务核验来源并幂等补投影，不运行图或用最新档案改旧输入。缺 checkpoint/完整输入/合法来源时保留原报告，建议不可用。

建议存储失败与合法报告分开处理；事件插入失败时整批回滚，没有半条建议，可能时保存 UNAVAILABLE 原因。任务失败撤回当前建议，重复撤回不重复事件。

```text
GET /api/missions/{mission_id}/recommendations
GET /api/missions/{mission_id}/recommendation-events?after=0
```

接口只读：列表返回 mission_id/availability/reason/items，未知 Mission 为 404；availability 不代替每项 validity。GET 不补投影、不追加事件，不运行模型或写入球员事实。应用 `get(recommendation_id, PlayerContext)` 核验完整身份，供后续业务使用。

| 读取时的情况 | 返回 validity |
| --- | --- |
| 来源合法，最新 state_version/snapshot_id 与原基线一致 | `current`，仅证明来源与数据一致，操作仍待验证 |
| 最新快照变化、不可读或身份错误 | `needs_reassessment` |
| 原成果已被新版本替代 | `superseded` |
| 原成果失效/失败、审查不可用、checkpoint 不一致或保存内容与投影不符 | `withdrawn` |

GET 状态检查不回写旧建议、事件或报告，显式写入侧替代/撤回才产生持久化事件。同 context 最新快照只读一次用于适用性；不同生涯/分支/球员不混用。原内容和固定基线始终保留。

## 5. 界面、验收与后续

工作台在完成/失败任务中提供“训练焦点建议”面板，支持刷新适用性、展开来源/审查/条件/基线/游戏版本/窗口及查看来源报告。网络错误独立展示，重新打开或任务变化时重新核验；未提供采纳、执行或自动报告抽取按钮。

当轮完整后端 73 项通过，补充来源审查完整性后 E 专项 10 项再次通过；前端 7 项单元、生产构建、16 个浏览器场景通过，3 个建议场景调整定位后复核通过。这些是历史轮次数量，不累计；本次复核见 [整理验收](./整理验收_2026-10-02.md)。

专项覆盖自动交付、审批前不投影、并发/重启/重放防重、GET 只读、基线与事实 hash 不变、不同 context、最新数据变化、来源失效/错审查/内容冲突、事务失败保护报告、同 Agent 多任务及成果替代。使用隔离实际格式 Repository、受控构造观察、真实 SQLite/checkpoint 与确定性节点；浏览器 E 场景是模拟 API 响应，不是实际游戏。

三张表是增量建表，不清库、不把旧任务迁移为已验证建议。回退可保留表和原任务/checkpoint；重放应核对来源，不能靠删历史消除冲突。CLI 共享应用会保存建议，但当前展示入口仅 Web/API。

下一步 E1.2 分别记录选择和实际执行，事件稳定后 C2 消费；E2 比较/反馈与 [C1](./C_C1_会话恢复与任务延续.md) 建议/评估引用解析仍待接通。真实游戏操作与效果未验证，不宣称完成生涯闭环。原始验收见 [E1.1 实施记录](../devolopment_logs/archive/2026-10-roadmap/2026-10-02_E1.1_实施与验收.md)，后续见 [E 任务书](../E方向_球员业务闭环与交互_Codex任务书_2026-10-01.md)。
