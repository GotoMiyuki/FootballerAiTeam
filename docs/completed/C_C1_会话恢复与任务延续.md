# C1：会话恢复与任务延续——已完成开发汇总

2026-10-24后续：[C2.1生涯记忆与来源](C_C2.1_生涯记忆与来源.md)已交付，消费E的权威选择/执行记录；E2.1也已交付，下一项为C2.2检索和任务提示词接入。下文保留原子阶段范围。

截至 2026-10-02，C1.1–C1.3 已完成；本文于 2026-10-23 按源码复核。本文描述 Web 与 CLI 的统一行为；C2 长期记忆与相关性检索尚未实施。下文原 SQLite checkpoint 恢复要求面向真实执行模式；工作流 demo 使用独立模拟路径，不能作为真实恢复验收证据。返回 [汇总索引](README.md)。

## 1. 四种操作的任务与数据身份

| 用户操作 | 执行行为 | 使用材料 |
| --- | --- | --- |
| 查看/刷新/打开历史 | 读取原 Mission，不运行专业图或模型 | 原任务、报告、消息与输入引用 |
| 解释旧报告 | 原任务内独立解释消息，不重做分析 | 原目标/报告/版本与最近六条解释消息 |
| 补缺输入、批准报告 | 恢复同一 Mission 的原 checkpoint | 原固定快照与等待位置 |
| 新比赛/新约束后重评，或明确重试失败任务 | 创建关联的新 Mission 和独立 checkpoint | 提交时最新同球员快照与选定原历史 |

例如来源任务用 v1 分析，用户提交重评时档案为 v2，排队期间又变为 v3：旧解释仍用 v1，新任务固定 v2，执行时不换成 v3。查看、解释、恢复、新分析四种操作不会混用身份。

| 子阶段 | 完成交付 |
| --- | --- |
| C1.1 | 公开原输入引用与可用操作，历史查询只读；解释读取原业务材料并追加独立消息，Web 暂停恢复校验，解释失败独立表达 |
| C1.2 | 关联评估/重试的应用、API 和 UI；完整输入、选定历史与请求防重原子持久化 |
| C1.3 | CLI 复用同一任务应用与 SQLite runtime，跨进程原暂停恢复、只读历史、关联创建与目录写入互斥 |

## 2. 历史与解释

`MissionView.input_reference` 保存生涯/分支/球员、state_version、snapshot_id 和允许公开的来源类型。`VERIFIED` 表示 Repository 输入已固定，不认证它是真实游戏数据；`demo_fixture` 仍是样本。工作流演示为 DEMO，旧缺字段记录为 UNVERIFIED，不通过读取最新数据补造历史来源。

`available_operations` 按状态、发布门槛、等待原因和恢复错误投影可用操作。历史 GET 只读核验可恢复性，不初始化 checkpoint 表、不调用真实模型或修改任务。

`MessageRequest.intent=explain` 生成独立 `MessageView`，保存 kind、operation_status、in_reply_to，只发布 `message.created`。解释失败、空输出或非文本输出标记消息失败，原任务保持 COMPLETED，原报告与审查不撤回，也不补固定答案。

解释不绑定工具，不读最新 Repository 或 checkpoint，不带其他任务聊天；输入预算为原报告最多 24,000 字符和本任务最近六条解释消息。旧 `followup` 在完成任务时兼容解释；需要重新生成方案时使用新任务入口。

## 3. 原暂停恢复

Web/CLI 均先只读检查原 SQLite checkpoint，核对等待节点、原快照结构、身份、状态版本和等待原因，再通过现有 `validate_pause()` 进入执行。任一缺失或不兼容都在追加消息、改状态或执行节点之前拒绝，不调用 `create_initial_state()` 重建一份任务冒充恢复。

支持的位置是缺输入与报告审批。缺输入通过现有类型/范围/必填表单校验进入原 checkpoint，再按 B 的原修订路径推进；报告审批是明确布尔批准，批准后只执行 document，不重做 Manager/专业节点。普通聊天不能当作审批。旧 `followup` 只兼容单一 information 文本，多字段或审批使用明确表单。

原报告读取仍受 B 的发布门槛约束。运行中或排队任务服务重启后按既有策略标为 FAILED，不自动重放任意节点；用户可明确新建关联 retry。这是 C1 支持暂停位置的恢复，不是 G 的崩溃自动恢复或取消机制。

## 4. 关联评估与创建防重

Python 入口为 `MissionService.create_continuation(Continuation)`，公开 API 为 `POST /api/mission-continuations`，202 返回 `message_id/mission_id/created`。

请求含 parent_mission_id、conversation_id、request_id、reason、content、operation，以及 include_report 和 selected_results。新 Mission 与来源保持同一会话关系，但有新身份、lineage 和 checkpoint。`retry` 来源必须 FAILED，且不引用失败报告；显式 player_context 必须与来源 career/branch/player 完全相同。

来源不存在为 404，身份/状态/历史/请求冲突为 409，DTO 非法为 422。相同 request_id 同内容跨并发、重启或响应丢失重试返回原身份，`created=false`，不再取新快照或重复排队；修改要求、原因或历史选择须使用新 request_id。

复用 `workspace.sqlite3` 新增 `mission_inputs/creation_requests`。Mission、完整固定输入、初始消息/事件与创建防重在一个事务中保存，中途失败整体回滚。提交新任务时读取并冻结最新同球员快照，最新数据不可读时拒绝创建；执行时仅还原已保存输入并校验 schema/mode/hash 和公开引用，输入缺失或损坏时明确失败，不重新读取最新数据补齐，也不回退 demo。没有球员库与任务库的跨数据库事务。

选定历史包含原目标、新要求、原因、输入引用和版本证据。原报告最多 12,000 字符，记录完整原文 hash 与截断标记；最多选择八个有效专业结果，总历史预算 32,000 字符，超预算明确拒绝。结果从原只读 checkpoint 核验结构、有效性、当前指纹、来源版本及快照一致性，失败/失效/未验证或错误版本拒绝。

历史实际进入 Manager 和专业节点提示及 B3 指纹，新任务的成果初始为空，不继承旧 completed。历史参考不等于当前事实或玩家已执行。[E](E_E1.1_建议来源与适用性.md) 的 recommendation/assessment id 当前只是预留字段，未接通解析时非空引用拒绝。

Web 的 ReevaluationPanel 支持原因、新要求、历史选择、切换新任务及返回来源。浏览器草稿保留请求身份以应对响应丢失；原样重试复用请求，编辑任意字段生成新身份。子任务失败不会覆盖父任务报告、审查或原输入。

## 5. CLI 与运行目录

```text
python app.py "需求"                         新建持久化 Mission
python app.py --list                         只读任务索引与旧元数据
python app.py --show <mission_id>            只读原任务/报告
python app.py --continue <mission_id>        恢复原缺输入或审批暂停
python app.py --reevaluate <id> "新要求" --reason "原因"
python app.py --retry <failed_id> "重试要求" --reason "原因"
```

等待输入或审批时用 `q` 保存暂停退出，后续 `--continue` 从同一任务根恢复。已完成任务的 continue 只读，不要求模型配置；审批时回车/y/yes/确认是明确批准，其他文字不会触发重规划。关联命令可用 `--request-id` 防重；重复创建不隐式恢复暂停，恢复仍使用 continue。CLI 当前默认只引用来源报告，专业结果选择由 API/UI 提供。

CLI 默认目录为 `local_data/cli/fixture/` 或 `local_data/cli/actual/`，可由 `FAIT_CLI_DATA_DIR` 覆盖。Web 默认为 `memory/web/demo/` 或 `memory/web/live/`，可由 `FAIT_DATA_DIR` 覆盖。任务目录保存 workspace/checkpoints 两份 SQLite，独立于 A 的球员事实根。

`CLIMissionService` 是同一 MissionService 的阻塞适配。`SessionRepository` 用只读连接投影索引，缺目录返回空，不初始化库。旧 `memory/sessions.json` 继续作为未验证元数据历史，不迁移为可恢复 Mission。

`WorkspaceWriterLock` 在服务读改任务前取得目录进程锁，Windows 使用 msvcrt，其他平台用 fcntl。同目录第二个 CLI/Web 写入服务启动拒绝，只读历史可并行；关闭或失败时释放连接与锁。锁文件存在不等于占用，无需删除；该锁只约束任务目录，不解决球员导入跨进程竞争或跨数据库恢复。

主要代码：[任务服务](../../backend/service.py)、[关联请求](../../backend/continuations.py)、[暂停校验](../../backend/task_context.py)、[CLI 适配](../../backend/cli.py)、[写入锁](../../backend/writer_lock.py)、[会话访问](../../utils/sessions.py)、[命令入口](../../app.py)。

## 6. 验收与兼容

| 历史轮次 | 当轮真实执行结果 | 主要证据 |
| --- | --- | --- |
| C1.1 | 33 项后端、7 项前端、构建、10 个浏览器场景通过 | 只读、解释失败、v1/v2、跨服务续跑、无效 checkpoint、明确审批 |
| C1.2 | 41 项后端、7 项前端、构建、13 个浏览器场景通过 | v1/v2/v3、并发/重启防重、SQL 故障回滚、真实提示消费、网络丢响应 |
| C1.3 | 50 项后端与编译检查通过；当轮未重跑前端 | 九项 CLI 专项，跨独立进程审批/缺输入恢复、同目录锁与关联创建 |

这些是各轮回归数量，不相加。测试使用隔离根、真实应用/SQLite/LangGraph、确定性节点和模型替身，CLI 独立进程验证原输入不变，浏览器含 demo 与 API 替身。当前复核见 [整理验收](../development_logs/2026-10_路线图实施记录.md#history-oct-01)。

增量表和 JSON 字段保留旧任务读取，不清库、不将旧元数据迁移为已验证输入。回退代码可以保留表和任务库，但旧 CLI 不具备新暂停恢复语义。前后端应同步回退新增消息/lineage 能力，或以 GET 刷新兼容。

C2.1已于2026-10-24交付明确目标/偏好及E来源记忆，更正、撤回和重放见[C2.1](C_C2.1_生涯记忆与来源.md)。C2.2相关性检索、固定记忆和提示词接入仍未实施；C1建议/评估引用解析仍待E2.2接通。原始记录见 [C1.1](../development_logs/2026-10_路线图实施记录.md#history-oct-03)、[C1.2](../development_logs/2026-10_路线图实施记录.md#history-oct-04)、[C1.3](../development_logs/2026-10_路线图实施记录.md#history-oct-05)；后续见 [C 任务书](../guides/C方向_会话恢复与生涯记忆_Codex任务书_2026-10-01.md)。
