# FootballerAiTeam Web 工作台 V0.1：运行与验收

## 运行

要求 Node.js 22.12+（本轮使用 24.19.0）、Python 3.10+；真实任务继续使用仓库原有 `.env`、Agent、工具与模型配置。

在仓库根目录安装后端依赖，在 `frontend` 安装前端依赖：

```powershell
& .\venv\Scripts\python.exe -m pip install -r requirements-web.txt
cd frontend
npm.cmd ci
```

终端 A，在仓库根目录启动后端：

```powershell
& .\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

终端 B，启动前端：

```powershell
cd frontend
npm.cmd run dev
```

工作台：<http://127.0.0.1:5173/>，API 文档：<http://127.0.0.1:8000/docs>。

后端绑定本机，面向指南中的单球员工作台。Vite 将 `/api` 代理到 8000 端口。自定义 API 地址可在 `frontend/.env.local` 设置 `VITE_API_BASE_URL`（填写 origin，不加 `/api`）；若前端 origin 改变，需要同步配置后端 CORS。生产构建由 `npm.cmd run build` 生成 `frontend/dist`，部署时同样需要代理 `/api` 到 FastAPI，并关闭代理的 SSE 缓冲。

## 演示模式

在后端启动终端中设置：

```powershell
$env:FAIT_DEMO='1'
& .\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

页面会显示 DEMO 标识。新任务输入框提供四种确定性演示场景：一次通过、局部修订、人工输入、重新规划。球员档案与训练/比赛历史读取仓库 JSON；演示报告明确标注示例，不调用模型、不修改球员记忆。

切回真实模式前停止演示后端，在同一终端执行 `$env:FAIT_DEMO='0'` 后重新启动。真实模式生成报告前保留 CLI 原有的人工确认步骤。

## 已实现的交互

- 左侧独立球员档案、训练记录与比赛记录；使用长期球员数据，不展示旧预测字段作为实际状态。
- 右侧 Mission Header、Plan/Subtask 进度、Agent Activity、Review/Revision/Replan、Conversation、Report。
- 明确的新建任务入口；完成任务的报告解释沿用原 Mission 与原材料，不触发专业图。新比赛/新约束可通过“根据新情况重新评估”创建关联任务，选择原报告/有效专业结果；失败任务可创建关联重试，子任务可返回来源任务。
- 关联创建草稿与 request_id 保存在浏览器本地，网络响应丢失后刷新并重试沿用同一请求；修改要求或历史选择生成新请求身份。
- BLOCKED 显示后端请求的信息；表单支持 text、number、scale、boolean、single_select；后端验证类型、范围和必填项。
- 审查结果 PASS/REVISE/REPLAN/BLOCKED 各有独立展示，可查看审查历史。
- 任务失败、网络故障、SSE 断线与等待人工输入分别展示；执行中断时不会保留运行中的 spinner。
- 完整 Markdown 报告独立查看，GFM 表格/列表支持，禁用原始 HTML 执行；支持 `.md` 下载。
- E1.1 训练焦点建议面板：显示成果/审查来源、固定基线、条件和游戏执行支持；刷新适用性后数据变化标待重评，失效/替代建议保留历史。可以查看来源报告，选择与执行反馈待 E1.2。
- 任务历史搜索与重新打开；刷新恢复已选任务、计划、审查、结果和报告。

## 边界与持久化

`backend/models.py` 定义公共 DTO 与支持的 Event Types。C1.1 增加原输入引用、可用操作与 `message.created` 解释消息事件；解释不会重新发布 `result.created/report.created/mission.completed`。C1.2 增加 lineage 和 `POST /api/mission-continuations`，返回新 mission_id/message_id 及 created 标记。`backend/events.py` 是内部 Graph State 到公开快照/事件的唯一映射边界。前端只读取 Public API，不理解 graph node 或 AgentState。

事件 Envelope 保留 `event_id/type/mission_id/timestamp/sequence/data`。`data` 提供语义字段及公开 `snapshot`；事件与快照在同一 SQLite 事务中保存。前端按 Mission 与 sequence 去重，计划按完整版本替换，连接恢复或事件缺口时通过 HTTP 读取快照。HTTP 返回旧快照时不会覆盖更新的 SSE 缓存。SSE 支持 `Last-Event-ID` 与 `after` 重放，并发送心跳。

E1.1 的 `career_actions/models.py` 定义建议与独立业务事件 DTO。`GET /api/missions/{id}/recommendations` 只读核验原来源和最新同 context 数据，返回建议及 current/needs_reassessment/superseded/withdrawn；`GET /api/missions/{id}/recommendation-events?after=0` 提供 created/superseded/withdrawn 重放，每页最多 200 项。业务事件未加入 Mission SSE；消费者不能仅凭 created 判断当前有效或已采纳。建议列表由前端 Query 获取，任务变化、重新打开及“刷新适用性”重新核验。

默认保存目录：

```text
memory/web/live/     真实任务
memory/web/demo/     演示任务
```

每个目录包含 `workspace.sqlite3`（公开任务、对话、事件、报告，以及 C1.2 的完整固定输入和创建请求去重）和 `checkpoints.sqlite3`（真实运行时的内部 checkpoint）。设置 `FAIT_DATA_DIR` 可覆盖目录；此时调用方应自行保持演示和真实模式数据隔离。Mission、初始消息/事件、输入及去重记录在同一事务创建；同一 request_id 不同内容返回 409。

E1.1 增加 recommendations/recommendation_batches/recommendation_events 三张表，记录与其业务事件同事务提交；交付和重启补投影防重，普通 GET 不写库。缺少合法成果、原 checkpoint 或完整输入时不生成当前建议，旧报告仍可查看。建议不改变球员事实，游戏操作一律待验证；未提供指标/单位/窗口时不展示效果结论。

C1.3 的 CLI 默认使用独立的 `local_data/cli/fixture` 或 `local_data/cli/actual`，由 `FAIT_CLI_DATA_DIR` 覆盖。CLI 与 Web 复用任务应用和存储格式；每个任务目录使用进程写入锁，不允许两个服务同时写入，只读历史不占锁。`--continue` 可跨进程恢复原缺输入/报告审批暂停；旧 CLI JSON 元数据继续只读且不可恢复。详见 [C1.3 实施与验收](completed/C_C1_会话恢复与任务延续.md)。

服务重启后，完成任务可只读查看与解释；BLOCKED 任务续跑前核对 checkpoint、等待原因与原输入快照。缺失或不兼容时保留旧任务，显示无法恢复，不重建初始状态。正在执行或尚未启动的任务会标记为 FAILED 并保留快照，用户可创建关联重试；重复提交旧创建请求仍返回原任务，不自动重启执行。

普通新 Mission 创建全新的 Runtime Context。关联任务在提交时冻结同一生涯/分支/球员的最新快照与选定历史，执行只读持久化输入；原报告（最多 12,000 字符，记录是否截断）和有效结果的投影进入实际 Manager/专业输入及指纹，旧 completed 子任务不被导入新任务为 completed。历史预算为 32,000 字符，超过时要求减少选择；尚未接通的 E 建议/评估 id 拒绝引用。最新数据不可用时不创建任务、不回退旧快照或 demo。

报告解释只传原目标、原输入引用、原报告（最多 24,000 字符）与当前 Mission 最近六条解释消息，不读取最新球员档案，不带入其他任务对话。工作队列串行执行；Agent 球员访问保持 A/B 的只读快照边界。演示模式仅验证关联创建/跳转流程，专业结果不当作已验证材料；实际历史提示注入由后端真实节点的模型替身测试验证。

`graph.py` 允许注入 checkpointer 和固定的 continuation_context；默认仍使用 MemorySaver，原有节点与路由业务未改动。

## 自动测试

仓库根目录：

```powershell
& .\venv\Scripts\python.exe -m unittest evaluation.test_recommendations evaluation.test_cli_persistence evaluation.test_reevaluation evaluation.test_session_semantics evaluation.test_looping_plan evaluation.test_web evaluation.test_dependency_context evaluation.test_result_invalidation evaluation.test_player_repository evaluation.test_player_imports -v
& .\venv\Scripts\python.exe -m compileall -q career_actions backend player_data graph.py evaluation
```

前端目录：

```powershell
npm.cmd test
npm.cmd run build
npm.cmd audit
```

`evaluation.test_web` 验证 API、报告下载、任务历史、追问沿用 Mission、字段验证、事件序号/重放及跨服务重启恢复。`evaluation.test_reevaluation` 验证新身份、v1/v2/v3 固定输入、有效历史、原子创建、并发/重启去重、失败保护及真实 Manager/Coach 提示注入。真实图接入测试使用确定性模型/工具节点和真实 LoopController + SQLite checkpoint，不访问外部模型。

`evaluation.test_recommendations` 验证来源/审查版本、固定输入、并发/重启防重、GET 只读、来源失效/记录冲突、当前适用性、同 Agent 多任务、事务回滚保护报告及隔离 actual Repository 事实不变。

`frontend/e2e` 提供四种流程、失败契约、C1.1 会话语义、C1.2 关联创建及 E1.1 建议来源/适用性/历史的 Playwright 测试。先启动使用隔离样本根/任务根的演示后端和 Vite，再执行（已有 Chrome 可设置 `FAIT_TEST_BROWSER=chrome`）：

```powershell
npx.cmd playwright install chromium
npm.cmd run test:e2e
```

C1.2 本轮实际运行结果：41 项后端测试、7 项前端单元测试、TypeScript/生产构建及全部 13 个浏览器场景通过。浏览器含实际 demo API 流程与模拟网络响应丢失/刷新重试场景，使用隔离数据，不调用真实模型。详见 [C1.2 实施与验收](completed/C_C1_会话恢复与任务延续.md)。

E1.1 本轮实际运行结果：73 项后端回归、7 项前端单元测试、TypeScript/生产构建及全部 16 个浏览器场景通过。最后来源审查引用完整性补充后，10 项 E 专项再次通过；截图定位调整后，3 个建议浏览器场景再次通过。E UI 使用模拟 API 响应，后端使用真实 SQLite/Repository 与确定性节点；未调用真实模型或操作游戏。详见 [E1.1 实施与验收](completed/E_E1.1_建议来源与适用性.md)。

## 可选真实模型验收

```powershell
& .\venv\Scripts\python.exe -m evaluation.smoke_web_live
```

该命令会调用已有模型，消耗 API tokens。它复制球员记忆到 `.web-test-data/live-*/player-memory`，然后验证新 API → 现有 Agent Core → 审查/修订 → 人工确认 → 最终报告。运行日志、任务指标、报告留在被 Git 忽略的 `.web-test-data`，不会修改原始球员 JSON。脚本仅用于开发验收。

## 已知范围

没有新增多人权限、OAuth、通知、PDF 导出、复杂足球可视化或移动端产品化适配。历史入口展示本轮 Web 新增的 Mission；旧 CLI `sessions.json` 仅保存会话元数据，不含完整 Plan/Review/Report，因此没有伪造迁移为可恢复 Web Mission。
