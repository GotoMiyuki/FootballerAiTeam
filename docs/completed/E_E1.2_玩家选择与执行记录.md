# E1.2：玩家选择与独立执行记录

2026-10-24后续：[E2.1后续观察与比较](E_E2.1_后续观察与比较.md)已交付，下一项为C2.2。下文保留原子阶段范围和当轮验收。

2026-10-24后续：[C2.1生涯记忆与来源](C_C2.1_生涯记忆与来源.md)已交付，消费E的权威选择/执行记录；下一项先推进E2.1，C2.2检索和任务提示词接入仍待后续。下文保留原子阶段范围。

交付日期：2026-10-24。承接 [M2交接](M2_阶段二验收与交接.md)和 [E1.1建议来源](E_E1.1_建议来源与适用性.md)，完成本地单用户、单任务目录写入进程范围的应用/API/UI与持久化。下一项为C2.1；本轮不实施C2、E2、游戏自动操作或新一轮游戏采集。

## 1. 行为与事实边界

- 选择为 `ADOPTED / REJECTED / DEFERRED`，执行为独立的 `UNKNOWN / CONFIRMED / PARTIAL / NOT_EXECUTED`。采纳不创建执行记录；没有执行反馈始终未知。
- 用户可以未采纳就尝试；拒绝/暂缓已采纳建议相当于撤回当前选择，不删除以前的执行。来源失效、新事实或旧版本建议不得重新采纳，但允许保留拒绝、暂缓和实际历史反馈。
- 首版执行来源由服务固定为 `user_reported`。`CONFIRMED`仅表示用户明确自报已执行，界面显示“自报已执行”，不表示游戏核验。HTTP请求不能指定 `source_type`、观察id或伪造来源。游戏证据关联须另行提供受信读取与验证，本轮没有自动检测执行。
- 实际行动按原文保存，数值也只属于用户反馈。代码不调用 `ObservationImporter`，不更新A的能力、比赛、训练或状态版本。建议操作支持继续为 `pending_verification`。
- 窗口保留 `unknown / real / game / match`及人工描述。未知域不填范围；其他域必填范围。不解析或混合排序这些时间域。`created_at`仅为现实提交时间。
- UI的“等待观察”明确尚未关联后续观察，不产生改善分、达标判断或因果结论。

## 2. 对象、版本与更正

所有操作绑定建议id/revision、完整career/branch/player和服务从原建议读取的成果source；同时保存提交时的建议validity及checked_state_version，原建议基线和报告不改。

`DecisionRecord`含decision_id、revision、status、理由、request_id和previous_decision_id。请求必须显式提供previous_decision_id，第一次为null；后续必须等于当前选择，过期页面的提交返回409。每次选择追加新记录，revision沿该链递增。

`ExecutionRecord`含execution_id、revision、status、实际行动、窗口、用户自报标签和request_id。新增反馈是独立一次行动/窗口，不替换其他反馈。更正提供corrects_execution_id和非空correction_reason；只能更正同一建议尚未被更正的当前记录，revision沿更正链递增。把原反馈改为UNKNOWN并说明原因可撤回原确认，原内容仍在历史。选择和执行链互不替代。

当前投影为一个current_decision_id与多个current_execution_ids，历史始终可查。建议来源后续变为withdrawn或superseded时不抹除行动历史，也不会把历史选择迁到新建议。

## 3. API与事件

```text
GET  /api/missions/{mission_id}/recommendations/{recommendation_id}/activity
POST /api/missions/{mission_id}/recommendations/{recommendation_id}/decisions
POST /api/missions/{mission_id}/recommendations/{recommendation_id}/executions
GET  /api/missions/{mission_id}/recommendation-events?after=0
```

activity要求查询参数career_id、branch_id、player_id，返回完整选择/执行历史及当前投影。两个POST都要求request_id、recommendation_revision和context；额外字段拒绝。选择另填status、reason和previous_decision_id；执行另填status、actual_action、window，以及可选更正关系。

POST成功返回200 `{record, created}`。相同request_id及规范化内容重试返回原记录、created=false；即使适用性变化、原选择已改变或原执行已被更正，也不会重复追加。不同内容、操作、来源任务或建议复用同id为409。未知任务/建议404，context/版本/适用性/更正/CAS冲突409，DTO非法422。刷新后可按最新状态修改内容形成新请求。

复用既有业务事件查询和全局sequence，新增 `decision.recorded / execution.recorded / execution.corrected`。保留原envelope的建议id/revision、context、source，增加object_id、object_revision、previous_record_id；旧E1.1事件缺这些字段时读为null。消费者按object_id读取activity内原记录，不能仅凭某事件判断最新状态。每页仍最多200项，未加入Mission SSE，未接C2摘要或C1建议引用解析。

## 4. 存储与界面

在既有workspace.sqlite3增量建立 `recommendation_actions / recommendation_action_heads / recommendation_action_requests`，不清库，不迁移旧记录为已采纳。不可变对象、当前head、请求指纹及业务事件使用同一 `BEGIN IMMEDIATE`事务；事件失败时全部回滚。服务Store锁覆盖来源检查和本地行动提交，沿用任务目录的单写者进程锁。A事实库与任务SQLite没有跨库事务，提交时适用性检查不承诺之后事实永远不变。

工作台每条建议展示独立状态；展开“记录选择与执行反馈”后分别提交，展开历史可查看原记录与前次/更正关系。请求提交前在localStorage保存完整请求，丢响应、刷新后原样重试复用request_id，修改表单形成新请求。刷新适用性同时刷新行动历史。服务重启从SQLite恢复；普通GET不追加事件或行动记录。

主要实现：[对象与DTO](../../career_actions/models.py)、[应用](../../career_actions/application.py)、[事务存储](../../career_actions/repository.py)、[API](../../backend/main.py)、[交互面板](../../frontend/src/features/mission/RecommendationActivity.tsx)。

## 5. 本轮验证与限制

新增 [9项后端专项](../../evaluation/test_recommendation_actions.py)覆盖：选择/执行独立、未采纳的尝试、不同时间域窗口、并发防重与选择CAS、更正及跨建议拒绝、完整context/来源任务/修订核验、过期/撤回采纳拒绝与历史保留、事务故障回滚、刷新/重启和原请求重试。隔离actual Repository验证含数值的反馈、更正和采纳前后state.json字节与完整快照/hash不变。

本轮后端97项通过：相关API/A/C/E回归52项（包含新增9项），其余B/CLI/事实边界回归45项；前端单元7项、TypeScript/生产构建、全部21个浏览器场景通过。浏览器新增5个E1.2场景验证选择与执行差异、撤回/更正/刷新、两种操作的丢响应重试，旧建议拒绝采纳但允许历史反馈，以及选择冲突后刷新形成新请求。截图位于忽略的 `frontend/test-results/e1-2-actions.png`，不作为真实游戏证据。

测试使用临时SQLite、隔离fixture/actual根、真实应用/Repository与确定性图节点；E浏览器使用API替身，其他浏览器场景使用隔离demo服务。未调用真实模型、操作游戏或写入当前应用球员数据。既有 `memory/player.json`和 `memory/sessions.json`改动保留，不纳入本轮交付。

首次测试因沙箱默认TEMP目录权限失败，将TEMP/TMP改到工作区测试目录后通过；一项浏览器断言改为检查option的原生disabled属性后通过。没有为迁就测试放宽后端契约。重跑命令见[运行与验收](../guides/运行与验收.md)。
