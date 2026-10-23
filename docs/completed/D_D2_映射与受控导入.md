# D2：原生数据映射、受控导入与路线隔离

日期：2026-10-23。承接 [D1 独立采集验收](./D_D1_独立采集验证.md)，D2.1/D2.2 已完成指定 PES2021 1.07.01 / Data Pack 7.00 / SmokePatch V4、专用 BAL 测试范围的实现与验收。接口为 [pes2021_import.py](../../game_adapters/pes2021_import.py)，映射版本 `pes2021-canonical-v1`。

## 实际交付

新增受信采集应用 `Collector`。输入只能通过 D1 `read_package` 重新校验原件、原生字节和明确审阅的事件关联，不能直接提交任意 JSON 作为已认证事实。处理顺序为明确绑定 → dry-run → 核对预览 token → 逐项调用现有 `ObservationImporter`。未注册模型工具，不修改执行图或自动启动分析。

真实原生档案已导入独立的 `local_data/game_imports/pes2021-d2-acceptance-20261023` actual 仓库：25项能力、一份快照、两条个人比赛、三条没有个人出场证明的生涯事件，共6条 APPLIED 观察。重新创建采集器后再次导入，6条均为 DUPLICATE，聚合文件字节不变。当前应用配置和 `memory` 未切换至这个验收目录。

本机原始验收在 `game_adapters/raw/real_test/pes2021-bal-20261002-d1/d2-20261023/`：绑定结果、预览、逐项导入结果、投影、重复结果、测试日志，以及 `delivery-acceptance.json` 最终验收清单。这些真实材料以及 actual 数据均受 Git 忽略；仓库交付实现、测试与规则。

## 字段映射与语义

25项能力均有 D1 原生布局与逐项独立页面对照，按 PES 显示的能力分数原值映射；不截断、不补值、不按理论7位存储范围缩放。仅接受 A 的0–100契约可表示的值，越界直接拒绝。此处映射不证明与其他足球游戏或现实运动能力可互换。

| 原生路径/名称 | A 规范字段 | 规则与单位 |
| --- | --- | --- |
| snapshot.identity.name | profile.name | 原生姓名文本；生涯识别仍由独立绑定决定 |
| attributes.offensive_awareness | attributes.offense.attacking_awareness | PES进攻意识原值 |
| ball_control、dribbling、tight_possession、low_pass、lofted_pass、finishing、heading、place_kicking、curl | attributes.offense 下同名字段 | 9项各自原值；不合并成 passing/shooting |
| speed、acceleration、kicking_power、physical_contact、balance、stamina | attributes.physical 下同名字段 | 6项各自原值 |
| jump | attributes.physical.jumping | PES跳跃能力原值，不换算跳跃高度 |
| defensive_awareness、ball_winning、aggression | attributes.defense 下同名字段 | 3项各自原值 |
| gk_awareness、gk_catching、gk_clearing、gk_reflexes、gk_reach | attributes.goalkeeping 下同名字段 | 5项各自原值 |
| 正常终场 target.counters.goals/assists | match_record.goals/assists | 该场个人次数，整数 |
| 正常 target.rating_candidate.value | match_record.rating | 已独立核对的原生评分，0–10；notes 标明 normal_engine 来源 |
| 正常 counters.shots/shots_on_target/passes | match_record.key_stats 下同名字段 | 该场个人次数，整数 |
| 正常 rates_percent.pass_success.value | key_stats.pass_accuracy | 成功传球数/传球数×100；无分母或不一致时省略 |
| 模拟个人摘要 goals_candidate/assists_candidate/rating_candidate | match_record.goals/assists/rating | 已有正样本和写入代码验证；notes 标明 summary_simulation，不与动作引擎评分混淆 |
| 未入选或替补跳过后个人行缺失 | career_event | 分别保留 not_selected_reviewed、bench_selection_only_actual_participation_unknown；不增加个人比赛，不补零 |

事件的游戏日期保持 `time_domain=game`、`time_precision=day`；采集时间保留原有时区。无精确日期的规范处理使用 unknown/RECORDED_ONLY，不伪造日期。原件路径、SHA256、明确事件审阅引用及映射版本记录于 `raw_reference`；数值来源不使用截图。

以下继续排除在规范事实之外：分钟候选的一般时长语义、角色/位置、未核实的赛事与对手文本、累计双桶、总评、其他特征、训练负荷/RPE、现实疲劳与伤病概率。原生 Dribble 不直接命名为过人；盘带距离不冒充全场跑动距离。快速跳过的完整动作统计缺失时省略相应字段，不清空已有事实。完整原始数据仍由来源引用保留。

## 身份、持久绑定和回档

`collectors/pes2021_native/bindings.json` 保存带完整性摘要的绑定表：明确审阅的来源生涯/分支/球员、本地目标 context、原生 ID/姓名/俱乐部、准确环境、适配器和映射版本、基线原件及确认依据。首次目标必须是 EMPTY actual context；不接管未知历史，不隐式加载 demo。绑定不可静默替换，一个 context 不接受另一条来源路线。

原生 ID 不视为全局唯一 ID。观察和来源记录 ID 由已明确分配的来源 context 与本地 capture/event ID 确定性生成，不使用采集时间或比分作为赛事身份。同场详细采集先由 D1 合并核对，D2 只生成一条候选个人比赛。

| 情况 | 行为 |
| --- | --- |
| 同一来源事件、内容未变 | 原身份重试，A 返回 DUPLICATE |
| 同一事件规范值或来源改变 | 拒绝自动导入；要求明确更正或新的审阅路线 |
| 明确同场更正 | corrections 指定 supersedes 与 reason；生成新观察并传 corrects_observation_id，不追加第二场 |
| 未见过的记录早于已导入高水位 | rollback_review_required；不更新当前路线 |
| 已见赛程锚点被赋予另一个事件 ID | route_review_required；不能靠换事件名绕过防重，锚点仅检测冲突而不充当赛事 ID |
| 已导入旧包原样复读 | 仅防重，无历史覆盖；旧内容与更正后内容不一致则拒绝 |
| 明确重踢、新分支 | 先取得新分支的 D1 审阅包，再绑定独立 context；可记录 parent_binding，但不复制任何历史 |
| 另一生涯/球员/俱乐部或版本改变 | 未绑定或版本/对象不匹配时拒绝；重新审阅来源与字段映射 |

新分支只是独立上下文及可选父绑定说明，没有自动共享基线或合并历史。未认证的原生持久 GUID、任意回滚识别仍不宣称实现；D2 依赖明确审阅的本地绑定，歧义时停止。

## 预览、提交与失败处理

dry-run 不创建/修改球员聚合或收据，展示目标 context、当前版本、原件引用/hash、规范 payload、映射版本、遗漏字段和拟提交 Observation。token 绑定具体观察、绑定表和预期聚合版本；提交前重新读取原件。预览后数据、绑定或聚合变化，提交返回 CONFLICT 且不写观察。

每项只调用 A 的受信 `ObservationImporter(source_type='game_observation', provider='pes2021_native')`，逐项消费返回版本；不在冲突后读取新版本强行覆盖。APPLIED、DUPLICATE、RECORDED_ONLY、CONFLICT、REJECTED 原样返回，错误保留 ERROR 和未尝试项目。RECORDED_ONLY 不表示当前投影更新。

收据位于 `collectors/pes2021_native/receipts/`，包含逐项结果和原件引用；A 观察日志是最终事实依据。响应丢失或部分成功后重新预览并使用原 ID 重试，已提交项防重，未完成项继续。收据写入失败也可从 A 状态恢复，不另造一组事件身份。

使用应用唯一写入进程调用服务。独立 CLI 写入需显式声明 `--sole-writer`，表示该 actual 根没有其他写入进程；这不是分布式锁，也不提供跨进程并发保证。验收使用专用根，未与 Web 或其他 CLI 共享写入。

## CLI

下面以已验收的专用数据集为例。首次绑定使用显式目标和依据；已有验收根无需再次创建绑定。

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_import bind game_adapters/raw/real_test/pes2021-bal-20261002-d1 --actual-root local_data/game_imports/pes2021-d2-acceptance-20261023 --sole-writer --career pes2021-test-20261002 --branch round8-controlled --player test-player-001 --basis "明确核对的专用测试生涯和D1事件清单"
.\venv\Scripts\python.exe -m game_adapters.pes2021_import preview game_adapters/raw/real_test/pes2021-bal-20261002-d1 --actual-root local_data/game_imports/pes2021-d2-acceptance-20261023
.\venv\Scripts\python.exe -m game_adapters.pes2021_import commit game_adapters/raw/real_test/pes2021-bal-20261002-d1 --actual-root local_data/game_imports/pes2021-d2-acceptance-20261023 --sole-writer --preview-token <刚取得的preview_token>
```

更正使用 `--corrections <JSON路径>`，格式为 `{ "r8-event-001": { "supersedes": "旧observation_id", "reason": "明确的同场更正依据" } }`；预览和提交使用同一更正文件。新分支绑定可传 `--parent-binding <原binding_id>`，来源包必须已有独立审阅的新分支身份。

## 验证与边界

完整适配器 **142项通过**，其中 D2 新增21项；A 的 `evaluation.test_player_imports`、`evaluation.test_player_repository` **13项通过**。覆盖实际 Repository 写入、来源/版本、预览零写、重复与重启、同赛程改名、防回档、更正、防跨生涯、分支隔离、固定快照、部分失败/响应丢失、未知及越界拒绝、收据和绑定损坏。

真实验收使用既有原生字节，未新增实机比赛。切尔西槽1、测试槽2及两个应用 memory 文件的保护摘要保持不变。D2 的完成范围是指定原生样本的受控接入；不代表跨版本兼容、全自动生涯识别、Web 上传入口或职业建议闭环。快速跳过按游戏机制仅生成个人进球、助攻、评分，见[机制边界及历史研究](../D2_PES2021_快速跳过动作统计复核_2026-10-23.md)。后续新增一场[正常比赛后台明细留存实测](../D2_PES2021_正常比赛自动运行验证_2026-10-23.md)，未将该场自动导入本验收仓库；AI接管和菜单自动化仍未实现。
