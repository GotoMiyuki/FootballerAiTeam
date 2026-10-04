# A：球员数据与事实边界——已完成开发汇总

截至 2026-10-02，已完成 A1、A2.1、A2.2。A 负责实际球员数据的读取、身份、来源、版本和受控更新；[B](./B_执行协作与审查正确性.md) 消费版本进行执行与失效判断，[D0](./D_D0_游戏接入调查.md) 研究未来观察来源。返回 [汇总索引](./README.md)。

## 1. 问题与交付

原流程可能把 Coach 的属性预测自动写入当前球员档案，同时 API 与 Agent 可能从不同目录取数。现在预测保留为建议，所有业务读取使用同一个 Repository，实际更新只能由模型之外的受信应用导入已校验观察。

| 阶段 | 完成行为 | 关键实现 |
| --- | --- | --- |
| A1 | 首次运行、修订或失败均不应用预测；模型工具集合移除事实写入能力，旧直接写入入口拒绝调用 | [Coach](../../agents/coach.py)、[工具集合](../../tools/__init__.py)、[数据库工具](../../tools/database.py) |
| A2.1 | API、Graph、工具统一只读入口；固定身份/快照，保留来源、版本和未知值；actual 缺档案不回退 demo | [数据模块](../../player_data/repository.py)、[快照模型](../../player_data/models.py)、[配置](../../config.py) |
| A2.2 | 校验观察来源与字段，控制初始化、防重、版本冲突和更正；观察与事实投影一起原子提交 | [观察导入](../../player_data/imports.py)、[JSON Repository](../../player_data/json_repository.py) |

## 2. 统一读取流程

`PlayerRepository.read_snapshot(context)` 返回 `PlayerSnapshot`，含 `profile/training/matches/career/metadata`。字段读取返回副本，`to_dict()` 用于任务输入与 checkpoint 序列化。`snapshot_scope()` 使执行期工具读取任务已经冻结的数据，而非每次临时取最新档案。

`PlayerContext(career_id, branch_id, player_id)` 完整隔离生涯、分支和球员；每个标识为 1–64 位字母、数字、下划线或连字符。目录解析检查路径边界和符号链接，拒绝越界读取。

`metadata` 保存 `schema_version/state_version/snapshot_id`、来源和质量标记。状态版本是供相等性比较的不透明值，消费方不推测大小或时间顺序。来源将字段和历史记录指向样本或观察，actual 来源另记录 provider、确认/原件引用、采集与生效时间；E1.1 已补充已存储的游戏/适配版本读取投影。

提交任务后身份与快照固定。切换应用配置或更新实际档案不会替换旧任务输入；[C1](./C_C1_会话恢复与任务延续.md) 新评估才会读取并冻结最新快照。公开输入引用只披露允许的身份、版本和来源类型，完整原件引用与本机路径不进入公开任务 DTO。

## 3. 样本、实际模式与未知值

| 配置 | 当前语义 |
| --- | --- |
| `FAIT_PLAYER_DATA_MODE=demo` | 默认的显式只读平面 JSON 样本适配器 |
| `FAIT_PLAYER_DATA_ROOT` | demo 可替换样本根；actual 必填，且不能与仓库样本根重叠 |
| `FAIT_CAREER_ID/FAIT_BRANCH_ID/FAIT_PLAYER_ID` | 当前读取上下文；提交后的任务保留原身份 |
| `FAIT_DEMO=1` | 工作流演示开关，独立于球员数据模式；不是失败降级方式 |
| `FAIT_DATA_DIR` | 任务库与 checkpoint 目录，独立于球员事实根 |

actual 聚合文件位于 `contexts/<career>/<branch>/<player>/state.json`。读取缺失档案返回缺数据错误，不自动建档或复制样本；API 缺数据为 404，损坏或不兼容为 503。旧平面文件配置只能指向同一根，与显式配置矛盾时拒绝。

规范投影过滤预测顶层字段和非规范别名，记录 `ignored_fields`；不清洗历史样本为真实观察，不猜别名的真值。职业历史中的 `estimated_value_eur` 仍是估计，不改名为游戏已观察身价。

缺字段保持缺失，`null` 表示未知，`0` 表示明确的零。伤病字符串 `"None"` 才表示明确无伤病，缺失或 `null` 不代表健康。当前规范能力值为 0–100，其他字段使用自身量表；PES/FC 原始属性还需要 D/F 的明确映射。

## 4. 受控导入流程

应用调用 `ObservationImporter(repository, source_type=..., provider=...)` 导入 `Observation`。此入口未注册给模型，也没有新增公开 HTTP 事实写接口。载荷自称 `source_type` 不能提供权限，调用方须在模型之外验证采集来源或人工确认凭据。

观察携带完整 context、observation_id、来源、provider、采集时刻、生效时间域/精度、kind、payload 和 expected_state_version。首次初始化使用 `EMPTY_VERSION`。支持 `profile_patch/training_record/match_record/career_event`：历史追加不隐式改写当前属性。

游戏观察还要求 `game_version/adapter_version/source_record_id/raw_reference`；人工确认要求独立 `confirmation_reference`。现实时间使用有时区 instant，游戏时间保留 day/month/week 等精度。未知生效时间是 `None/unknown`，不用采集时刻猜游戏日期。

| 结果 | 存储行为 |
| --- | --- |
| `APPLIED` | 观察和当前投影一起提交，新状态版本生效 |
| `RECORDED_ONLY` | 保存观察并更新聚合版本，保守地不覆盖当前事实；适用于未知、旧/同时间或无法比较的更新 |
| `DUPLICATE` | 同 provider/id 的同语义重试返回原提交版本，不新增记录 |
| `CONFLICT` | 同 id 内容变化，或新观察的期望版本已过期；不写入 |
| `REJECTED` | 来源、身份、字段、数值、时间或更正引用非法；不写入 |

防重语义排除重试采集时刻和 CAS token。更正使用新的 id 和 `corrects_observation_id`，保留旧记录；只有时间可比较且对应当前来源的更正才能更新投影。未知或稀疏信息不会补成零、正常或低风险。

事实、观察、来源、防重索引、revision/hash 位于同一个聚合 JSON。同进程按上下文路径加锁，在同目录写临时文件、序列化、flush/fsync 后原子替换；任一步失败保留旧档案。损坏档案明确失败，不用空状态重置。

## 5. 验收、数据影响与限制

A/B 联合交付时实际执行 55 项 Python 测试、6 项前端事件测试、类型检查及 7 个 Edge 浏览器场景。A 专项覆盖 Coach 只读、共享隔离 Repository、固定快照、路径边界、未知值、初始化、防重/CAS/时间冲突、更正、持久化、并发及序列化/fsync/replace 故障。数量属于 2026-10-01 的联合回归，不是 A 独占数量；本次当前回归另见 [整理验收](./整理验收_2026-10-02.md)。

actual 数据是新受控格式，旧测试档案继续通过显式样本适配读取；没有历史污染修复或真值迁移。球员事实根仍只支持单写入进程：进程内锁不保证跨进程导入竞争，完整性 hash 不等于来源签名，原子替换不等于所有文件系统或断电条件下的灾难恢复。

尚未完成真实游戏导入、游戏量表映射、备份/跨数据库恢复或领域预测校准。D 的未来适配器须先核实身份、时间、版本与量表，再通过本入口导入；C/E 的聊天、建议和选择不能直接改实际球员属性。

原始细节与受控导入 Python 示例见 [A1–B3 原始实施记录](../devolopment_logs/archive/2026-10-roadmap/2026-10-01_A1-B3_实施与验收.md)。后续计划见 [A 任务书](../A方向_球员数据与事实边界_Codex任务书_2026-10-01.md)。
