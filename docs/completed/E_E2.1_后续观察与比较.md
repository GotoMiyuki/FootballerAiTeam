# E2.1：后续观察、冻结规范与可复核比较

后续更新（2026-10-25）：[C2.2相关记忆检索与固定输入](C_C2.2_记忆检索与固定输入.md)已交付，已消费Assessment并注入任务固定集合。下文保留本阶段交付时的范围与测试记录；下一项为E2.2。

M3补充（2026-10-25）：增加已认证规范路径 `profile/attributes/offense/finishing` 的射门终结指标，使用0–100能力点、日期窗口、单次基线及后值减前值，与速度的既有规则一致。前端可显式选择该指标；不能用速度变化代替射门终结。3项专项覆盖独立来源、显式基线更正、零值与窗口约束，相关34项及比较页面8项浏览器测试通过；完整后端140项通过。实机比较仍遵守事先冻结规范和不作因果归因的边界。

交付日期：2026-10-24。承接[E1.2行动记录](E_E1.2_玩家选择与执行记录.md)和[C2.1生涯记忆](C_C2.1_生涯记忆与来源.md)。本轮交付本地单用户范围的观察读取、比较规范、版本化Assessment、API/UI和事务持久化；不实施C2.2检索/提示词注入或E2.2关联任务闭环，不调用模型补造评估。

## 1. 指标、基线与窗口

原E1.1建议缺少指标和窗口，仍保留evaluation_spec=undefined。E2.1另建用户明确确认的不可变ComparisonPlan，绑定原建议/成果版本、context、原固定输入、算法版本、指标量表、最低有效样本数、观察窗口与可选目标。不会把旧建议内容原地改成另一项建议。

规范只能在建议current且当前A的state_version/snapshot_id仍与建议基线一致时创建。出现新事实后不能再为原建议挑有利基线、改变阈值或事后指定窗口；相同request_id的原请求重试仍返回原规范。没有目标则始终UNDETERMINED，不从“训练焦点”推导增长阈值。

首版算法 `observed-comparison-v1`：

| 指标 | 方法与规范单位 | 基线 |
| --- | --- | --- |
| speed | 规范能力点0–100，后值减前值 | 原固定快照的速度来源观察 |
| rating | 有效个人评分均值，规范0–10 | 原快照最后N条可排序比赛观察 |
| goals / assists | 有效个人进球/助攻的每场均值 | 同上 |
| minutes_played | 有效出场分钟的每场均值 | 同上；缺分钟不补零 |

窗口有两种：基线最新有效游戏日期之后的N场（1–20场，仅比赛指标），或明确游戏日期区间（起始日须晚于基线边界）。速度比较只支持日期窗口和单次基线。最低样本数分别作用于前后两侧；比赛均值排除缺值，明确零值计入，并保留有效/缺失样本数。目标是后续值/有效样本均值的明确≥或≤阈值，只有窗口完整且样本满足规范时才判断MET/NOT_MET。

基线日期边界取冻结快照当前来源中可核验的最新游戏日，表示“该游戏日之后”，不声称玩家现实点击时游戏已经推进至某日。日期窗口只在后续已应用观察的游戏日期高水位到达结束日后算完成；现实经过时间不完成窗口。

首版只比较game/day时间域。无法排序、同日多场导致N场边界歧义、provider/source_type或游戏/适配版本不同/未知时默认INCOMPARABLE。不跨生涯/分支/球员，也不把回档分支接进主线；没有通用回档自动识别承诺。

## 2. A的只读观察边界

新增 `JsonPlayerRepository.read_observations(context)`，从一次经过完整性检查的聚合读取构造一致的snapshot与观察视图，暴露稳定provider:observation_id、source_record_id、语义hash、时间域/精度、应用状态、显式更正关系和当前origin路径。进一步复核观察context、语义hash和规范payload；不修改导入、聚合或现有快照读取行为。

E通过此接口读取，不直接打开实际JSON，也不用matches数组下标充当永久比赛id。普通比赛窗口只使用APPLIED且仍对应当前比赛origin的记录；RECORDED_ONLY只计入排除信息。速度可读取窗口内的APPLIED历史能力观察，并剔除被显式更正的版本。固定基线来源可以被后续显式APPLIED更正所替代，但只能沿原观察的更正链，不能另挑基线；新Assessment保存更正后的引用并标明原因，原Plan和旧数值保留。

只读视图及公开评估不包含raw_reference、confirmation_reference、本机来源路径、CAS token或完整checkpoint。FixtureRepository没有实际观察接口，显示不支持比较，不用fixture填成有来源的实际评估。测试里的actual格式观察仍是明确的合成数据。

## 3. Assessment与有效性

Assessment保存稳定assessment_id、revision、plan_id、context、原建议/成果来源、算法版本、原基线快照引用、生成时的后续快照引用、两侧观察key/hash/value、具体值/差值/样本数、缺失/排除信息、执行引用和限制。前后值及差值不做隐藏评分；界面四位小数仅为展示，持久化保留计算值。

证据状态独立于目标判断：

| 情况 | 证据 / 目标 |
| --- | --- |
| 窗口完整且前后有效样本满足规范 | COMPLETE；有显式目标才MET或NOT_MET |
| 窗口未满、基线场数或有效指标不足 | INSUFFICIENT / UNDETERMINED，可显示已知部分变化 |
| 时间、身份、量表、版本或来源不可核验 | INCOMPARABLE / UNDETERMINED，比较数值置未知 |
| 没有目标 | 保持UNDETERMINED，不发明达标规则 |

执行关联可不选，此时始终UNKNOWN，不因采纳或比赛发生推断执行。明确选择的执行须属于该建议/context；更正后沿原执行链解析当前版本，新Assessment记录新的执行id/revision/hash，仍标user_reported。执行未知也可报告观测变化；MET只表示规范内观测目标达到，不能改名“建议有效”。一场表现更好或能力变化都不自动证明训练造成改善。

普通GET重算来源签名但不保存新评估。窗口进展、观察更正或执行更正使签名变化时，旧当前版读取为superseded。显式刷新评估才追加revision，事务中将旧版持久置superseded并追加新版本；从不原地改旧数值。相同选定观察及规范重复计算不新增评估，无关RECORDED_ONLY记录不触发新版本。排除计数和后续快照引用属于该版生成时证据，不冒充实时计数。

## 4. API、事务和消费者

```text
GET  /api/missions/{mission_id}/recommendations/{recommendation_id}/comparison
POST /api/missions/{mission_id}/recommendations/{recommendation_id}/comparison-plans
POST /api/missions/{mission_id}/recommendations/{recommendation_id}/comparison-plans/{plan_id}/assessments
```

GET返回支持状态、指标、不可变Plan和全部Assessment版本及读取有效性。创建Plan要求context、建议revision、request_id、confirmed=true、metric、baseline_count、minimum_samples、window与可选target_value/operator；不接受客户端提供观察值、来源类型或baseline引用。

评估请求要求context、request_id、expected_revision（首次0），可选execution_id。规范/建议/执行/context冲突或当前revision变化409，非法DTO422，未知记录404，成功200 `{record, created}`。同请求跨重启/丢响应重试返回原记录，即使此后数据变化；新request但相同比较签名也返回已有版。重试不隐式创建任务。

在workspace.sqlite3增量增加comparison_plans、assessments、assessment_requests。Plan与其防重同事务，Assessment、旧版失效、新事件和防重同事务；事件故障全部回滚。沿用本地单写者锁，不清库。A与任务SQLite没有跨库事务，快照引用说明本次实际读取版本，不承诺源数据以后不再变化。

沿用独立业务事件查询，新增assessment.created/assessment.superseded，携带原建议source/context和Assessment对象id/revision。envelope.revision仍为建议revision，Assessment版本取object_revision；created的previous_record_id使用`{assessment_id}:v{旧revision}`，旧版的superseded事件指向其原object_revision。API对象的corrects_revision保留同一Assessment内的更正关系。未加入Mission SSE。C2.1继续处理原先已支持的建议/选择/执行事件，显式跳过新增Assessment类型，避免误当Execution。Assessment读取及事件契约已供后续C2.2接入；本轮不把比较记录自动注入记忆或模型。

界面每条建议增加“后续观察与比较”：明确确认规范，按原窗口生成/刷新比较，分别展示证据、目标、执行未知/自报、数值表、观察来源及历史版本。两个写操作分别持久化完整请求草稿，刷新或丢响应原样重试复用request_id；普通刷新只读。游戏操作标签继续待验证。

主要实现：[A观察视图](../../player_data/observations.py)、[对象](../../career_actions/assessment_models.py)、[比较算法](../../career_actions/comparison.py)、[应用/事务](../../career_actions/assessments.py)、[UI](../../frontend/src/features/mission/AssessmentPanel.tsx)。

## 5. 本轮验证与限制

新增[9项E2.1专项](../../evaluation/test_assessments.py)，使用真实A/E/API/SQLite、受控合成观察和确定性图节点。覆盖零/缺失、前后均值手工核对、冻结基线/目标、窗口不足、日期/版本/同日排序不可比、前后观察更正、执行独立及更正联动、请求/并发/CAS、事务回滚、重启、读取不写、事实字节和任务固定输入不变，以及C2.1遇到新事件的兼容读取。

新增4个浏览器场景使用API替身验证：待观察到完成、观测变化与执行未知、旧版/来源保留、不可比不判目标失败、Plan和Assessment两种丢响应重试。截图位于忽略目录 `frontend/test-results/e2-1-observed-comparison.png`。

本轮完整后端118项通过，前端单元7项、生产构建和全部29个浏览器场景通过。补充基线更正说明与界面计算规则后，相关A/E/C后端34项和E2.1浏览器4项再次通过，最终构建/格式检查通过。测试使用工作区内TEMP/TMP和已有Chrome，不调用真实模型。文档链接检查0错误，原有历史验收数量不累计为本轮覆盖率。

没有新增真实游戏比赛、真实模型评估或连续生涯验收；当前工作台数据根没有自动切换，游戏存档与既有两个memory文件未写入。下一项为C2.2：按相关性和版本选择并冻结记忆/比较引用、接入实际任务输入与指纹；完成后再推进E2.2。E2.1通过不表示建议科学质量、效果因果或十场真实闭环完成。
