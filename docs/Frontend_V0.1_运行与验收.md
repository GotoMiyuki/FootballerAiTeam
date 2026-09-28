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
- 明确的新建任务入口；追问沿用当前 Mission，不触发 Manager 重新创建任务。
- BLOCKED 显示后端请求的信息；表单支持 text、number、scale、boolean、single_select；后端验证类型、范围和必填项。
- 审查结果 PASS/REVISE/REPLAN/BLOCKED 各有独立展示，可查看审查历史。
- 任务失败、网络故障、SSE 断线与等待人工输入分别展示；执行中断时不会保留运行中的 spinner。
- 完整 Markdown 报告独立查看，GFM 表格/列表支持，禁用原始 HTML 执行；支持 `.md` 下载。
- 任务历史搜索与重新打开；刷新恢复已选任务、计划、审查、结果和报告。

## 边界与持久化

`backend/models.py` 定义公共 DTO 与冻结的 21 个 Event Types。`backend/events.py` 是内部 Graph State 到公开快照/事件的唯一映射边界。前端只读取 Public API，不理解 graph node 或 AgentState。

事件 Envelope 保留 `event_id/type/mission_id/timestamp/sequence/data`。`data` 提供语义字段及公开 `snapshot`；事件与快照在同一 SQLite 事务中保存。前端按 Mission 与 sequence 去重，计划按完整版本替换，连接恢复或事件缺口时通过 HTTP 读取快照。HTTP 返回旧快照时不会覆盖更新的 SSE 缓存。SSE 支持 `Last-Event-ID` 与 `after` 重放，并发送心跳。

默认保存目录：

```text
memory/web/live/     真实任务
memory/web/demo/     演示任务
```

每个目录包含 `workspace.sqlite3`（公开任务、对话、事件、报告）和 `checkpoints.sqlite3`（真实运行时的内部 checkpoint）。设置 `FAIT_DATA_DIR` 可覆盖目录；此时调用方应自行保持演示和真实模式数据隔离。

服务重启后，完成任务和 BLOCKED 任务保持可用；人工输入可基于持久化 checkpoint 继续。正在执行的任务会标记为 FAILED 并保留快照，用户可新建任务重试；V0.1 没有后台任务自动重启机制。

新 Mission 创建全新的 Runtime Context；历史持久化不参与新任务 Prompt。追问只传当前报告（最多 24,000 字符）与当前 Mission 最近六条对话。工作队列串行执行，避免单球员记忆工具并发写入。后端会保留现有 Agent Core 对长期球员记忆的正常工具行为。

`graph.py` 唯一接入改动是允许注入 checkpointer；默认仍使用 MemorySaver，原有节点与路由业务未改动。

## 自动测试

仓库根目录：

```powershell
& .\venv\Scripts\python.exe -m unittest evaluation.test_looping_plan evaluation.test_web -v
& .\venv\Scripts\python.exe -m compileall -q backend graph.py evaluation
```

前端目录：

```powershell
npm.cmd test
npm.cmd run build
npm.cmd audit
```

`evaluation.test_web` 验证 API、报告下载、任务历史、追问沿用 Mission、字段验证、事件序号/重放及跨服务重启恢复。真实图接入测试使用确定性模型/工具节点和真实 LoopController + SQLite checkpoint，不访问外部模型。

`frontend/e2e/workspace.spec.ts` 提供四种场景的可复跑 Playwright 测试。先启动演示后端和 Vite，再执行：

```powershell
npx.cmd playwright install chromium
npm.cmd run test:e2e
```

本轮浏览器验收通过工具实际操作工作台完成；Playwright 文件另行保留供后续 CI/本地回归。

## 可选真实模型验收

```powershell
& .\venv\Scripts\python.exe -m evaluation.smoke_web_live
```

该命令会调用已有模型，消耗 API tokens。它复制球员记忆到 `.web-test-data/live-*/player-memory`，然后验证新 API → 现有 Agent Core → 审查/修订 → 人工确认 → 最终报告。运行日志、任务指标、报告留在被 Git 忽略的 `.web-test-data`，不会修改原始球员 JSON。脚本仅用于开发验收。

## 已知范围

没有新增多人权限、OAuth、通知、PDF 导出、复杂足球可视化或移动端产品化适配。历史入口展示本轮 Web 新增的 Mission；旧 CLI `sessions.json` 仅保存会话元数据，不含完整 Plan/Review/Report，因此没有伪造迁移为可恢复 Web Mission。
