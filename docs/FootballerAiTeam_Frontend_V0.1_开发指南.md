# FootballerAiTeam 前端开发指南 V0.1

> 目标：为 FootballerAiTeam 建立第一版 Web 前端，使系统从 CLI 多智能体流程升级为“以球员为长期状态、以 Mission 为工作单位、以 Conversation 为交互入口、以 Plan / Agent Activity 为过程展示、以 Report 为最终产物”的足球 AI 工作台。
>
> 本版本优先解决产品信息架构、前后端边界、实时事件协议和首轮可运行页面。不要在 V0.1 中追求复杂可视化、移动端、多人协作或完整设计系统。

---

## 1. 产品定位

FootballerAiTeam **不是聊天机器人产品**。聊天只是用户发起任务、补充信息、追问结果的自然语言入口。

产品核心是：

```text
Player State
    +
Mission
    +
Plan / Agent Collaboration
    +
Result / Report
```

用户真正关心的是：

```text
“我是谁 / 我的足球状态是什么”
        ↓
“我现在需要解决什么问题”
        ↓
“团队正在怎么处理”
        ↓
“当前做到哪一步”
        ↓
“需要我补充什么”
        ↓
“最后形成什么可执行方案”
```

因此，前端设计不能以 ChatGPT 克隆为目标。

---

## 2. V0.1 信息架构

第一版使用一个核心 Workspace 页面，而不是拆成大量独立页面。

```text
┌──────────────────────────────────────────────────────────────┐
│ FootballerAiTeam                     Mission History   Settings│
├────────────────────────┬─────────────────────────────────────┤
│                        │                                     │
│ PLAYER WORKSPACE       │ MISSION WORKSPACE                   │
│                        │                                     │
│ Profile Training Match │ Mission Header                      │
│ ─────────────────────  │ Plan / Progress                     │
│                        │ Agent Activity                       │
│ 当前 Player 数据        │ Review / Revision / Blocked State   │
│                        │                                     │
│                        │ Conversation                        │
│                        │                                     │
│                        │ Final Result / Report               │
└────────────────────────┴─────────────────────────────────────┘
```

V0.1 只要求完成：

```text
Main Workspace
├── Player Panel
│   ├── Profile
│   ├── Training History
│   └── Match History
│
└── Mission Workspace
    ├── Mission Header
    ├── Plan / Progress
    ├── Agent Activity
    ├── Review State
    ├── Conversation
    ├── Human-in-the-loop
    └── Final Result

Global
└── Mission History
```

---

## 3. 核心业务对象

前端不要直接围绕 LangGraph Node 设计。

第一版稳定对象定义为：

```text
Player
Conversation
Mission
Plan
Subtask
AgentActivity
Review
Result
Report
```

关系：

```text
Player
│
├── PlayerProfile
├── TrainingHistory
├── MatchHistory
│
├── Conversations
│
└── Missions
     │
     ├── Mission
     │    ├── Plan
     │    ├── Subtasks
     │    ├── AgentActivity
     │    ├── Review
     │    ├── Result
     │    └── Report
     │
     └── Mission ...
```

---

## 4. Player 与 Mission 的边界

### 4.1 Player State

表示：

```text
“这个球员是谁”
“长期发生过什么”
```

包括：
- 基础档案
- 身体与位置数据
- 长期目标
- 训练历史
- 比赛历史

这些数据跨 Mission 存在。

### 4.2 Mission State

表示：

```text
“现在正在解决什么问题”
```

包括：
- Objective
- Plan
- Hypothesis（如果需要展示）
- Subtasks
- Agent Status
- Review
- Revision / Replan
- Blocked Information
- Final Result

Mission 是一次工作的生命周期。

---

## 5. Conversation 的定位

Conversation 不是产品核心数据结构。它承担三个作用：

### 5.1 发起 Mission

例如：

```text
“新赛季还有一周，我暑假几乎没踢球，怎么准备？”
```

Manager 创建 Mission。

### 5.2 补充 Mission 所需信息

例如系统进入 `BLOCKED`，要求 DOMS、睡眠、疲劳、疼痛等信息。用户通过聊天或结构化表单补充。

### 5.3 追问 Mission 结果

例如：

```text
“为什么第三天安排冲刺？”
```

这是对已有 Mission 的追问，不应该默认创建新的 Mission。

---

## 6. Mission Workspace

右侧不能设计成纯 Chat Stream。

建议结构：

```text
┌──────────────────────────────────┐
│ Mission Header                   │
├──────────────────────────────────┤
│ Plan / Progress                  │
├──────────────────────────────────┤
│ Agent Activity                   │
├──────────────────────────────────┤
│ Review / Revision / Blocked      │
├──────────────────────────────────┤
│ Conversation                     │
├──────────────────────────────────┤
│ Result / Report                  │
└──────────────────────────────────┘
```

---

## 7. Mission Header

至少展示：

```text
Mission Title
Objective
Status
Plan Version
Created At
```

Mission Status 第一版建议：

```text
CREATED
PLANNING
RUNNING
REVIEWING
REVISING
REPLANNING
BLOCKED
COMPLETED
FAILED
```

前端不要自己猜状态，状态由后端提供。

---

## 8. Plan / Progress

这是 V0.1 最重要的 UI 模块之一。

不要只显示 `70%`，应该展示当前 Plan 的结构：

```text
Plan v1

✓ 评估当前身体状态
✓ 识别回归训练风险
● 制定渐进训练安排
○ 审查完整计划
```

每个 Subtask 至少包含：

```text
id
title / objective
status
assigned_agent
revision_count
```

建议 UI Status：

```text
PENDING
RUNNING
COMPLETED
REVISION_REQUIRED
BLOCKED
SKIPPED
```

---

## 9. Agent Activity

目标是让用户知道：

```text
谁在工作
正在做什么
是否已经完成
```

例如：

```text
Manager
✓ Plan created

Analyst
✓ Current-state analysis completed

Coach
● Building training plan

Reviewer
○ Waiting
```

不要展示 Chain of Thought，不要直接暴露模型隐藏推理。

---

## 10. Review / Revision / Replan UI

必须对应后端 Loop Controller v2。

第一版 UI 至少支持：

```text
PASS
REVISE
REPLAN
BLOCKED
```

### PASS

```text
✓ Review passed
```

### REVISE

```text
Reviewer requested revision

Affected:
- subtask_03

Reason:
训练时间线与减量阶段冲突
```

默认由系统继续执行。

### REPLAN

```text
Plan is being reconsidered

Reason:
当前 Observation 不支持原假设
```

前端应明显区分 Revision 和 Replan。

### BLOCKED

这是 Human-in-the-loop 的核心状态。

```text
需要你的信息

肌肉酸痛： 0 ───────── 10
昨晚睡眠： [       ] 小时
疲劳程度： 0 ───────── 10
是否疼痛： [是 / 否]

[继续]
```

`BLOCKED != ERROR`。它表示 Mission 等待用户输入。

---

## 11. Final Result

Mission 完成后提供两层结果。

### 11.1 Conversation Summary

聊天里输出简要结论。

### 11.2 Independent Report

提供独立 Report：

```text
📄 赛前一周恢复与激活方案

Completed
Plan version: 2

[查看报告]
[导出报告]
```

V0.1 先支持查看完整 Markdown / HTML Report，导出至少支持 `.md`。PDF 放后续版本。

---

## 12. Mission History

过去 Mission 应长期保存。

至少展示：

```text
title
created_at
status
short_summary
```

点击后加载：

```text
Mission
Plan
Review History
Result
Report
```

---

## 13. Persistence 与 LLM Context 必须分离

```text
Persistence != LLM Context
```

不要因为历史 Mission 被保存，就把所有历史 Mission 都重新传给 LLM。

### 13.1 Persistent History

完整保存：

```text
Mission metadata
Plan
Subtasks
Result
Report
Review outcome
```

用于用户查看、审计、重新打开历史 Mission。

### 13.2 Player Memory / State

长期有效信息：

```text
Profile
Training history
Match history
Long-term goals
Confirmed preferences
```

### 13.3 Runtime Context

一次实际模型调用只传：

```text
Current Mission
+
Relevant Player State
+
Current Subtask
+
Necessary dependency results
+
Relevant evidence
+
必要的历史摘要
```

禁止把全部 Mission 历史、全部聊天历史、所有旧 Agent 输出无条件进入 Prompt。

---

## 14. 推荐技术栈

### Frontend

```text
React
TypeScript
Vite
```

理由：单页工作台、不需要 SSR、开发简单、与 FastAPI 解耦、适合个人项目。

状态管理：

```text
TanStack Query
+
轻量 local UI state
```

确实需要全局前端状态时再增加 Zustand。V0.1 不建议引入 Redux。

### Styling

```text
Tailwind CSS
```

组件库可使用 `shadcn/ui`，但组件库不是架构依赖。

### Backend

继续使用：

```text
Python
FastAPI
LangGraph
```

FastAPI 负责 HTTP API、SSE Event Stream、Application Service、Event Adapter。LangGraph 继续只处理 Agent Workflow。

---

## 15. 前后端边界

必须建立：

```text
Frontend
    ↓
Application API
    ↓
Application Service
    ↓
LangGraph Runtime
```

禁止：

```text
Frontend
    ↓
直接理解 graph.py / AgentState
```

建议：

```text
LangGraph
   ↓
Internal State
   ↓
Application Adapter
   ↓
Public API / Events
   ↓
Frontend
```

---

## 16. API V0.1

### Player

```http
GET /api/player
GET /api/player/training-history
GET /api/player/match-history
```

### Conversation

```http
POST /api/messages
```

Request：

```json
{
  "conversation_id": "conv_x",
  "content": "新赛季还有一周，我该怎么准备？"
}
```

Response 不等待完整 Mission 完成：

```json
{
  "message_id": "msg_x",
  "mission_id": "mission_x"
}
```

### Mission

```http
GET /api/missions
GET /api/missions/{mission_id}
```

### Blocked Input

```http
POST /api/missions/{mission_id}/input
```

例如：

```json
{
  "values": {
    "doms": 4,
    "sleep_hours": 7,
    "fatigue": 5,
    "pain": false
  }
}
```

### Report

```http
GET /api/missions/{mission_id}/report
GET /api/missions/{mission_id}/report/download
```

---

## 17. SSE 事件协议

Mission 是长任务。V0.1 使用 Server-Sent Events，而不是让前端持续轮询。

```text
GET /api/missions/{mission_id}/events
```

---

## 18. Event Envelope

所有事件统一：

```json
{
  "event_id": "evt_123",
  "type": "subtask.completed",
  "mission_id": "mission_123",
  "timestamp": "2026-09-28T10:30:00+08:00",
  "sequence": 14,
  "data": {}
}
```

字段：

```text
event_id
type
mission_id
timestamp
sequence
data
```

`sequence` 必须有，用于保证事件顺序、处理重连、防止重复事件污染 UI。

---

## 19. V0.1 Event Types

第一版冻结：

```text
mission.created
mission.started
mission.status_changed
mission.blocked
mission.completed
mission.failed

plan.created
plan.updated

subtask.started
subtask.completed
subtask.revision_required

agent.started
agent.completed

review.started
review.completed

revision.started
revision.completed

replan.started
replan.completed

result.created
report.created
```

不要一次设计几十种事件。

---

## 20. Event Contract 原则

事件描述“发生了什么”，而不是“前端怎么画”。

正确：

```json
{
  "type": "mission.blocked",
  "data": {
    "required_inputs": []
  }
}
```

错误：

```json
{
  "type": "show_red_modal"
}
```

UI 决策属于前端。

---

## 21. mission.blocked Schema

```json
{
  "event_id": "evt_45",
  "type": "mission.blocked",
  "mission_id": "mission_123",
  "sequence": 19,
  "data": {
    "reason": "missing_user_input",
    "message": "需要当前恢复状态才能继续制定训练强度",
    "required_inputs": [
      {
        "key": "doms",
        "label": "肌肉酸痛程度",
        "input_type": "scale",
        "min": 0,
        "max": 10,
        "required": true
      }
    ]
  }
}
```

第一版支持：

```text
text
number
scale
boolean
single_select
```

---

## 22. review.completed Schema

```json
{
  "type": "review.completed",
  "mission_id": "mission_123",
  "data": {
    "decision": "REVISE",
    "summary": "训练时间线存在冲突",
    "affected_subtasks": ["subtask_03"],
    "severity": "MEDIUM"
  }
}
```

前端不需要 Reviewer 的全部内部结构，只提供用户理解当前状态所需的信息。

---

## 23. Plan Update

```json
{
  "type": "plan.updated",
  "mission_id": "mission_123",
  "data": {
    "version": 2,
    "reason": "revision",
    "subtasks": []
  }
}
```

前端直接替换当前 Mission Plan View，不要自己合并 LangGraph State。

---

## 24. Event Adapter

必须增加独立 Adapter，例如：

```python
class MissionEventEmitter:
    def mission_started(...): ...
    def subtask_started(...): ...
    def review_completed(...): ...
    def mission_blocked(...): ...
```

Graph Node 不应该到处手写 Public Event。由 Event Adapter 统一创建，使内部 State 改名时前端协议保持稳定。

---

## 25. 前端状态模型

建议以前端 `MissionViewModel` 为中心：

```ts
type MissionViewModel = {
  id: string
  title: string
  objective: string
  status: MissionStatus
  plan?: PlanViewModel
  agents: AgentActivity[]
  review?: ReviewViewModel
  blocked?: BlockedViewModel
  result?: MissionResult
  report?: ReportSummary
}
```

Conversation 单独：

```ts
type ConversationMessage = {
  id: string
  role: "user" | "assistant" | "system"
  content: string
  createdAt: string
  missionId?: string
}
```

不要把整个 Mission State 塞进 Message。

---

## 26. 页面组件建议

```text
src/
├── app/
│   └── App.tsx
├── features/
│   ├── player/
│   │   ├── PlayerPanel.tsx
│   │   ├── ProfileView.tsx
│   │   ├── TrainingHistoryView.tsx
│   │   └── MatchHistoryView.tsx
│   ├── mission/
│   │   ├── MissionWorkspace.tsx
│   │   ├── MissionHeader.tsx
│   │   ├── PlanProgress.tsx
│   │   ├── SubtaskItem.tsx
│   │   ├── AgentActivityPanel.tsx
│   │   ├── ReviewStatus.tsx
│   │   ├── BlockedInputPanel.tsx
│   │   └── MissionResult.tsx
│   ├── conversation/
│   │   ├── ConversationPanel.tsx
│   │   ├── MessageList.tsx
│   │   └── MessageComposer.tsx
│   ├── history/
│   │   └── MissionHistory.tsx
│   └── report/
│       ├── ReportViewer.tsx
│       └── ReportDownload.tsx
├── api/
│   ├── client.ts
│   ├── player.ts
│   ├── missions.ts
│   └── events.ts
├── types/
│   ├── player.ts
│   ├── mission.ts
│   ├── events.ts
│   └── conversation.ts
└── hooks/
    ├── useMission.ts
    ├── useMissionEvents.ts
    └── useConversation.ts
```

不要一开始把目录拆得更细。

---

## 27. 后端目录建议

在现有 Agent Core 外新增 Application Layer：

```text
backend/
├── api/
│   ├── player.py
│   ├── missions.py
│   ├── conversations.py
│   └── reports.py
├── application/
│   ├── mission_service.py
│   ├── conversation_service.py
│   └── report_service.py
├── events/
│   ├── models.py
│   ├── emitter.py
│   └── stream.py
└── core/
    └── existing LangGraph / Agents
```

不要要求现有 Agent 全部迁移目录才能开始前端。

---

## 28. SSE 前端处理

建议 `useMissionEvents(missionId)` 负责：

```text
连接 SSE
解析 Event Envelope
按 sequence 去重
更新 Mission cache
断线重连
```

UI Component 不直接处理 EventSource。

---

## 29. SSE 事件处理策略

收到：

```text
mission.status_changed → 更新 Mission status
plan.updated           → 替换 Plan
subtask.started        → 更新对应 Subtask
review.completed       → 更新 Review
mission.blocked        → 显示 BlockedInputPanel
mission.completed      → 加载最新 Mission + Result
```

---

## 30. SSE 不应成为唯一真相源

SSE 用于实时更新，HTTP API 用于恢复完整状态。

```text
SSE = change notification
HTTP = authoritative snapshot
```

页面刷新后：

```text
GET /missions/{id}
```

恢复完整 Mission，再继续监听 SSE。

---

## 31. Mission History 与 Session

V0.1 不需要先解决复杂多会话模型。

可以先采用：

```text
一个 Browser Session
    ↓
一个当前 Conversation
    ↓
多个 Mission
```

Mission History 独立保存。以后再决定是否支持多个 Conversation Threads。

---

## 32. Runtime / Developer Trace

V0.1 可以保留一个非默认开发者面板：

```text
Developer Trace
────────────────
Mission: ...
Plan version: 2
LLM calls: 8
Review count: 2
Revision count: 1
Replan count: 0
Tokens: ...
Latency: ...
```

要求默认折叠，不影响普通用户主要体验，不展示模型 Chain of Thought。

---

## 33. Visual Direction

V0.1 视觉优先级：

```text
信息层级
>
状态清晰
>
响应速度
>
视觉装饰
```

布局建议：

```text
Player Panel: 30%-35%
Mission Workspace: 65%-70%
```

Mission Workspace 内，Plan / Status 优先于 Conversation。不要把 Conversation 占满整个页面。

---

## 34. Loading 与 Streaming UX

不要只有一个 `Thinking...`。

应该显示真实流程状态：

```text
Creating mission...
Planning...
Analyzing current state...
Building training plan...
Reviewing...
Revising training timeline...
Preparing report...
```

这些文字来自稳定事件状态，不由前端随机模拟。

---

## 35. Error Handling

至少区分：

```text
Mission Failed
Network Error
SSE Disconnected
Agent/Subtask Failed
Blocked
```

其中：

```text
BLOCKED != ERROR
```

UI 必须明显区分。

---

## 36. V0.1 不做什么

明确排除：

- 产品级移动端适配
- 复杂足球比赛可视化
- 实时外部比赛数据 Dashboard
- Agent 自定义编辑器
- 拖拽 Plan
- 用户手动修改 Graph
- 多用户协作
- 权限系统
- OAuth
- 通知系统
- WebSocket 双向协议
- 复杂动画
- 完整 Dark/Light Design System
- PDF 高级排版
- 无限 Mission 上下文自动召回

---

## 37. 开发顺序

不要先把整个 UI 画完，再接后端。推荐纵向切片开发。

### Phase 1：静态 Workspace

完成：

```text
Player Panel
Mission Workspace
Conversation
Plan
Agent Activity
```

使用 Mock Data。

验收：页面结构与信息优先级正确。

### Phase 2：FastAPI Application Layer

完成：

```text
GET Player
POST Message
GET Mission
GET Mission History
```

先不接 SSE。

验收：可以发起真实 Mission，可以获取最终状态。

### Phase 3：Event Adapter + SSE

完成：

```text
Mission Events
SSE Stream
Frontend event reducer
```

验收：Mission 运行时 UI 能实时变化。

### Phase 4：BLOCKED / Human-in-the-loop

完成：

```text
mission.blocked
BlockedInputPanel
submit input
Mission resume
```

验收：缺信息时不会失败或乱重试。

### Phase 5：Review / Revision / Replan

完成：

```text
Review states
Revision UI
Replan UI
Plan version update
```

验收：用户能看懂系统为何继续执行。

### Phase 6：Mission History + Report

完成：

```text
Mission History
Report Viewer
Markdown export
```

---

## 38. 第一轮端到端验收场景

### Scenario A：一次通过

用户：

```text
分析我最近的训练效果。
```

预期：

```text
Mission created
→ Plan
→ Agent Activity
→ Review PASS
→ Result
```

### Scenario B：Revision

制造一个局部需要修订的任务。

预期：

```text
Review: REVISE
↓
affected subtask 标记
↓
Revision started
↓
Subtask 重新运行
↓
PASS
```

不能表现成整个系统突然重新开始。

### Scenario C：Blocked

用户：

```text
昨天踢完球今天要怎么训练？
```

但缺少身体反馈。

预期：

```text
Mission BLOCKED
↓
用户填写 DOMS / 疲劳 / 疼痛
↓
Mission resumed
```

### Scenario D：Replan

核心假设被推翻。

预期：

```text
Replan started
Plan v1 → Plan v2
```

UI 要明确显示这是 Plan 改变，而不是简单 Agent 重试。

### Scenario E：History

完成 Mission 后刷新页面，Mission 仍然存在；重新打开后 Plan、Result、Report 可查看。

---

## 39. 前端验收标准

### 信息架构
- Player 信息与 Mission 信息明显分区
- Conversation 不主导整个页面
- Plan 与 Progress 是 Mission Workspace 核心

### Mission
- 用户能看见当前 Mission
- 用户能看见当前 Plan
- 用户能看见 Subtask 状态

### Agent
- 用户能知道哪些 Agent 在工作
- 不暴露 Chain of Thought

### Review
- PASS / REVISE / REPLAN / BLOCKED 有不同表现

### Human-in-the-loop
- BLOCKED 可以收集用户输入
- 输入后 Mission 可以继续

### Streaming
- 长任务不依赖单一 Loading Spinner
- 页面可以实时显示 Mission 进展

### Persistence
- Mission 完成后可以重新打开
- 历史 Mission 不自动全部进入新 Mission 的 LLM Context

### Report
- Conversation 有简短最终回复
- 完整 Report 可独立查看
- V0.1 至少支持 Markdown 导出

---

## 40. 后端验收标准

- Frontend 不直接依赖 LangGraph State 字段
- Public API 有独立 DTO / Schema
- Public Event 有统一 Envelope
- SSE 有 sequence
- HTTP 可以恢复完整 Mission snapshot
- Event Adapter 独立于具体 UI
- `BLOCKED != FAILED`
- `REVISION != REPLAN`
- Mission History 与 Runtime Context 分离

---

## 41. 不允许出现的实现方式

禁止：

```text
React 根据 graph node 名称判断页面状态
Frontend 直接读取 AgentState JSON
后端发送 show_modal / show_spinner 这种 UI 指令
历史 Mission 全部塞进每次 Prompt
每个 React Component 自己创建 EventSource
只有一个 isLoading Boolean 表示所有 Workflow 状态
BLOCKED 当作 ERROR
Mission == Message
```

---

## 42. 首轮最终目标

V0.1 完成后，用户应该获得这样的体验：

```text
进入 FootballerAiTeam
        ↓
左侧看到自己的球员信息
        ↓
右侧提出一个足球相关需求
        ↓
系统创建 Mission
        ↓
看到 Manager 生成 Plan
        ↓
看到不同 Agent 开始处理 Subtask
        ↓
看到 Review / Revision / Replan 状态
        ↓
如果缺信息，系统明确向用户请求
        ↓
Mission 完成
        ↓
聊天中获得摘要
        ↓
查看 / 导出完整 Report
        ↓
以后可以从 Mission History 重新打开
```

第一版的成功标准不是“页面看起来像一个成熟 SaaS”，而是：

> **用户第一次能直观看懂 FootballerAiTeam 作为一个多智能体足球工作台到底在做什么。**

---

## 43. 给代码 Agent 的执行要求

在开始编码前：

1. 阅读当前 `graph.py`、Manager、Reviewer、Mission / State 定义。
2. 不要改变现有 Agent Core 的业务逻辑，除非为 API/Event Adapter 接入所必需。
3. 先建立 Public DTO / Event Schema，再写 React 组件。
4. 不要让 React 依赖 LangGraph 内部字段。
5. 第一阶段使用 Mock Data 完成 Workspace 后，再连接真实后端。
6. 每完成一个纵向切片就进行真实 E2E。
7. 不要为了“未来可能需要”提前增加大量抽象。
8. V0.1 优先保证：
   - Mission 可理解
   - 状态可观察
   - BLOCKED 可交互
   - Result 可查看
   - History 可恢复

完成 V0.1 后，再讨论：
- 更复杂的足球数据可视化
- RAG Evidence Viewer
- 训练/比赛趋势图
- 日历化 Mission
- 移动端
- 更丰富的报告导出
- 个性化 Dashboard
