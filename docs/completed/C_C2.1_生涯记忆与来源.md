# C2.1：生涯记忆、权威来源与更正

后续更新（2026-10-25）：[C2.2相关记忆检索与固定输入](C_C2.2_记忆检索与固定输入.md)已交付，已消费Assessment并注入任务固定集合。下文保留本阶段交付时的范围与测试记录；下一项为E2.2。

2026-10-24后续：[E2.1后续观察与比较](E_E2.1_后续观察与比较.md)已交付，下一项为C2.2。下文保留原子阶段范围和当轮验收。

交付日期：2026-10-24。承接[C1任务持久化](C_C1_会话恢复与任务延续.md)和[E1.2选择与执行记录](E_E1.2_玩家选择与执行记录.md)，完成本地单用户、单任务目录写入进程范围的长期记忆存储、应用/API和界面。仅实施C2.1；没有新增向量数据库、聊天抽取模型、相关性排序、提示词注入或E2评估。

## 1. 权威边界

- C拥有用户明确目标/偏好及记忆引用；A仍拥有球员事实；E仍拥有建议、选择和执行。记忆不写入A的long_term_goals或其他档案字段，也不能在C中更改E行动。
- 用户通过明确保存、确认候选、更正或撤回操作创建目标/偏好版本；普通聊天、原任务目标、A档案目标、模型建议或“不错”等反馈不会自动成为当前偏好、采纳或执行。
- 模型候选只可使用内部 `propose_candidate` 暂存为candidate/unconfirmed。本轮没有接入自动抽取、模型调用、HTTP候选创建或LLM写工具。明确确认形成user_confirmed新版本，原候选和确认关系保留；重复暂存不会复活已确认候选。
- 从E按需读取事件和原记录：建议、选择、执行分别形成引用与确定性短投影。用户自报数值仍属于反馈，不认证游戏统计或建议效果。C不推断执行、不计算评估。
- 所有记录绑定career/branch/player；默认不跨分支共享，也不自动迁移测试目录中的记忆到实际生涯。

## 2. 对象和版本

`MemoryRecord`保存schema_version、稳定memory_id、revision、完整context、kind、content、短projection、source、recorded_at、effective_time及更正关系。

kind为goal/preference/recommendation/decision/execution。source区分user_confirmed、model_candidate、e_record。用户记录保存请求与来源任务；E引用保存事件id/type、对象id/revision/hash、建议id/revision及Mission/subtask/result version/审查/输入指纹来源。建议短投影保留固定基线引用，长文本最多保留1200字符并标truncated；完整内容仍在E原记录中。

用户目标/偏好的更正沿同一memory_id追加revision和corrects_revision，原版置superseded。撤回追加retracted版本并保留原内容和原因，不能隐式恢复已撤回版本。E每个稳定对象对应独立确定性memory_id；后续选择或执行更正通过supersedes_memory_id关联旧引用，不把两个E对象改造成一份可写行动状态。E源对象自身的revision另存在source.object_revision，不能与C的revision混用。

effective_time分开unknown/real/game/match时间域、生效时点、精度与窗口。缺失保持未知，E人工描述窗口仅标unspecified精度，不把现实提交时间当游戏顺序。API可保存带时区的现实instant或明确游戏day/month/week；界面支持未知或人工窗口描述。本轮只记录时间和范围，不自动判断窗口到期、排序不同时间域或选择模型上下文。

## 3. 同步、读取和当前性

同步由用户或应用明确调用，不依赖后台队列，不从请求载荷接收任意E事件。按来源任务的冻结context先过滤同生涯/分支/球员，再逐页读取E的200项事件分页。全量重放按event_id及规范内容hash防重，不使用“最近N条”丢弃历史；同id内容冲突或投影不一致时回滚。

created事件生成建议引用；decision.recorded、execution.recorded/corrected生成独立行动引用。recommendation.superseded/withdrawn事件也进入消费凭据；其当前状态继续由E核验。同步检查事件所属任务、context、原建议/成果版本、行动对象版本及前次/更正关系，来源与对象不能由同步请求自称。

每次GET重新对照E事件、原对象内容hash、当前成果适用性及行动head。即使新更正事件尚未同步，旧引用也会退出当前状态；读取不补投影、不写事件或记忆。来源缺失、损坏或不能核验时保留历史短投影并标source_checked=false，不能继续作为当前材料。

读取视图分开status与usage：

| 情况 | status / usage |
| --- | --- |
| 明确用户记录或E来源当前可核验 | active / current |
| 未确认模型候选 | candidate / unconfirmed |
| 用户旧版本、E旧选择或更正前执行 | superseded / historical_only |
| 用户已撤回或建议来源不可用 | retracted / historical_only |
| 建议当前适用性待重评 | active / historical_only，另标needs_reassessment |

这里current只说明该引用来源当前可用，不证明操作可行、时间窗口适用或执行已被游戏核验。E源失效导致记忆引用退出当前性，不否认过去真实存在的用户反馈。后续C2.2消费者必须同时检查usage、来源、范围和版本，不能只看active或created。

## 4. API、存储和界面

```text
GET  /api/missions/{mission_id}/career-memory
POST /api/missions/{mission_id}/career-memory/sync
POST /api/missions/{mission_id}/career-memory/user-records
```

Mission冻结context作为作用范围，GET与sync读取该context在当前任务库内的记录，包含同context的其他Mission。没有可核验context的旧任务拒绝；不同context在读取前硬过滤。

user-records要求context、request_id、confirmed=true、operation、kind及内容/范围。operation为create/correct/retract/confirm_candidate；变更要求memory_id、expected_revision，更正/撤回还要求原因。source等额外字段拒绝；用户不能直接写recommendation/decision/execution类型。跨context、旧revision、E引用改写或请求冲突409，非法DTO422，未知Mission404。POST成功200 `{record, created}`；同请求重试返回原版本和created=false，即使该版之后已被更正或撤回。

复用workspace.sqlite3增量建立career_memories、career_memory_events、career_memory_requests。内容版本、当前状态、请求防重或E消费凭据在各自同一事务提交，故障整体回滚。沿用Store锁与任务目录单写者锁；没有新增跨A/E/C数据库的事务或G灾难恢复机制。旧任务不清库，不将缺字段旧材料推导成已验证记忆。

工作台“生涯记忆”面板显示context、同步E来源、刷新状态、用户目标/偏好设置、候选确认及版本/更正/撤回历史。提交前将完整请求保存到localStorage，刷新和丢响应后原样重试沿用request_id；编辑形成新请求。E状态操作成功时也刷新已加载记忆的来源状态。普通GET仍只读。

主要实现：[对象](../../career_memory/models.py)、[E只读适配](../../career_memory/sources.py)、[应用](../../career_memory/application.py)、[SQLite](../../career_memory/repository.py)、[界面](../../frontend/src/features/mission/CareerMemoryPanel.tsx)。E仅新增按id读取原事件的小接口；没有改变行动契约或任务固定输入。

## 5. 验证、限制与接续

[12项C2.1专项](../../evaluation/test_career_memory.py)使用真实应用/API/SQLite/E Repository与确定性图节点，覆盖聊天不猜行动/偏好、建议/选择/执行独立来源、202条事件分页重放、并发防重、CAS更正撤回、候选明确确认、跨context隔离、E引用不可改写、来源失效与漏同步防护、事务故障回滚、重启重试，以及隔离actual事实字节与任务固定输入不变。

前端新增4个场景：版本和E来源历史、丢响应后原请求重试、候选与历史引用区分、真实隔离demo API保存目标并核对球员API不变。E来源浏览器场景使用API替身；后端验证使用真实E存储。截图位于忽略目录 `frontend/test-results/c2-1-memory-history.png`。测试范围不等于真实游戏长期生涯或模型记忆质量验收。

本轮完整后端109项通过（含新增12项C2.1与原E1.2）；最后补充长更正原因的截断标记后，C2.1专项12项再次通过。前端单元7项、TypeScript/生产构建、格式检查及全部25个浏览器场景通过。文档链接检查0错误。重跑后端可使用 `python -m unittest discover -s evaluation -p "test_*.py" -q`，前端命令见[运行与验收](../guides/运行与验收.md)。本机使用工作区内TEMP/TMP和 `FAIT_TEST_BROWSER=chrome`。

初次浏览器验收发现新增面板与既有面板使用重复React key，已改为独立key并通过刷新复核；全套默认并发时，共享demo串行队列使旧关联评估场景等待超时，固定1个worker后25项通过，没有放宽业务断言。一次未设置本机浏览器channel的试跑因缺少Playwright内置Chromium未启动，按已有Chrome配置重跑。旧E专项初次在并行冷启动下等待BLOCKED超时，随后单进程完整109项通过，没有修改等待期限或业务契约。

本轮未写游戏存档、当前应用球员档案或既有memory/player.json、memory/sessions.json；前一轮E1.2未提交改动继续保留。C2.1来源核验不是C2.2检索接入：尚未按相关性挑选、冻结记忆集合、注入Manager/专业提示或修改B3指纹。下一项按路线推进E2.1，产生比较记录后完善C2.2，再贯通E2.2。
