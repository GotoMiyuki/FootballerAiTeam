# E2.2：反馈驱动关联任务（软件交付）

后续验收（2026-10-25）：[M3阶段三验收](M3_阶段三验收与交接.md)已在指定本地PES2021范围通过，包含真实模型和连续十场来源。下文保留软件交付时的测试、失败与当时外部阻塞，不代表当前仍受实机授权阻塞。

日期：2026-10-25。承接[E2.1比较](E_E2.1_后续观察与比较.md)、[C2.2固定记忆](C_C2.2_记忆检索与固定输入.md)及[E方向第8节](../guides/E方向_球员业务闭环与交互_Codex任务书_2026-10-01.md#8-e22反馈驱动关联新任务)。软件闭环已交付；阶段三M3的真实连续生涯验收未通过，当前实机访问授权受阻，详见[M3实机缺口与接续](../roadmap/M3_实机验收与接续.md)。

## 1. 关联创建与来源

继续调用C的 `POST /api/mission-continuations` / MissionService.create_continuation，不在E另建创建事务或防重表。新增可选recommendation_revision、assessment_revision、execution_ids；建议引用必须id/revision成对，评估引用也必须成对，执行必须属于该建议且为当前未更正的记录。仅真实完成任务的reevaluate支持反馈关联，demo和失败retry不能冒充有效反馈来源。

E只读适配核验原建议成果、context和版本。原建议可为current或needs_reassessment，withdrawn/superseded拒绝。所选Assessment必须仍current；后续观察、更正或执行反馈变化导致其失效时409，不能静默换成新版本。比较证据不足或不可比仍可由用户明确发起分析，但保留不足限制，不能声称效果已验证。

提交时在同一Store锁范围内获取原选择快照、所选执行、评估与最新A快照。实际观察包的snapshot_id须与提交快照一致，不混输入版本。若新的A观察不影响该比较签名，允许固定仍有效的所选Assessment，同时明确本次最新快照可能不同于评估的观察快照。新任务仍由C冻结相关记忆；关闭一般生涯记忆不删除玩家明确选择的反馈引用。

## 2. 固定输入、幂等与执行

mission_inputs.continuation_context新增career_feedback，保存原建议来源/版本/内容、所选评估算法/结果/观察引用、原选择、执行窗口/内容/版本/完整源摘要及限制。长执行正文和原因有明确截取标志，反馈预算28000字符、历史加反馈预算48000字符，超预算拒绝，不静默丢关键来源。

Mission.lineage新增feedback_reference短摘要；history_references增加recommendation/assessment/decision/execution与object_id。引用、固定输入、新Mission和request_id防重一起复用C的原创建事务。旧请求省略新增默认字段参与hash，保持默认请求重试兼容；同id不同版本或内容拒绝。已受理请求即使之后原评估失效、服务重启或新任务失败，原样重试仍返回原任务，不重新读取最新来源。

Manager的实际Mission/Hypothesis/Plan提示和Coach的ReAct任务输入经既有continuation_context接收固定反馈，B3既有历史输入指纹涵盖它。运行、报告解释及暂停恢复不重查最新E/A补齐。公开引用、整个输入摘要、反馈摘要及checkpoint来源不一致时拒绝恢复。E读取专业来源也核验完整关联历史与checkpoint一致。

新建议通过source.mission_id指向新Mission的持久化lineage，RecommendationView增加只读origin_feedback。原Recommendation对象格式和旧C源摘要不变；旧选择、执行不复制到新建议。新任务失败不修改原报告、原建议、选择、执行和评估。

## 3. API与页面

```text
GET /api/missions/{mission_id}/feedback-input
GET /api/missions/{mission_id}/recommendations/{recommendation_id}/followups?career_id=...&branch_id=...&player_id=...
```

前者只读原固定反馈，旧普通任务返回空对象，损坏或公开引用不一致409。后者在核验原建议context后读取C已有lineage，列出调整任务的状态、原因、输入引用与新建议id，不维护另一份E关系台账。GET不创建任务、调用模型或刷新评估。

评估面板提供“根据结果调整方案”，显示所选建议/评估版本、执行引用数、证据不足和因果限制，填写原因与新约束后明确创建。草稿含request_id持久化，丢响应后刷新可原样重试；编辑产生新请求id。版本冲突时明确提示并提供用当前版本重新填写的入口，不静默升级请求。旧评估按钮禁用。新任务显示“本次固定反馈来源”，原建议可查看其调整任务，失败任务仍能返回原任务。

## 4. 验证与实际范围

[6项E2.2专项](../../evaluation/test_feedback_followups.py)使用真实A/E/C/B服务、API、SQLite、checkpoint和合成观察，覆盖正常来源、最新A与原评估差异、提交后更正仍固定、请求重试/重启、事务故障回滚、子任务失败保留父任务、跨context/失效来源拒绝、实际Manager/Coach提示和B3指纹、新建议未选择/未执行及关系查询。

完整后端133项通过，前端单元7项、全部33个浏览器场景、类型/生产构建、compileall及本轮前端格式检查通过。新增浏览器场景验证丢响应/刷新/相同请求重试、失败后返回原任务和旧评估禁用。另有[4项M3审计规则测试](../../evaluation/test_stage3_audit.py)通过；合成数据仅验证规则，不作为M3来源。生产构建当前主包约504KB，Vite提示超过默认500KB建议阈值，构建成功；本轮没有修改阈值或展开无关打包重构。

原建议执行支持仍为pending_verification，没有虚构游戏动作接口。本轮未调用真实模型、推进游戏、写存档或修改实际球员事实。两个既有memory文件的哈希与本轮开始一致，前序未提交工作保留。实机启动请求返回 `Computer Use app approval timed out`，因此无法在本轮继续真实十场和玩家反馈验收；没有将软件测试或已有两条个人比赛计作M3完成。
