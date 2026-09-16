# FootballerAiTeam：Loop Controller v2 + Reviewer Result Schema + Revision/Replan/Blocked 状态机

> 目标：针对最近一次真实运行暴露出的“Reviewer 过度触发全量重跑、Loop 放大修复、Subtask 缺少可审查 Observation、运行时间过长、Token 消耗过高”等问题，重构 Manager / Reviewer / Loop Controller 的闭环。
>
> 核心目标：**把“发现问题”与“如何修复、修复多少、是否应该继续循环”彻底分离。**

---

## 0. 本次重构范围

本阶段只处理三个部分：

1. **Manager**：动态生成 Mission / Plan；解释 Reviewer findings；决定 `KEEP / REVISION / REPLAN / BLOCKED`；只重做受影响的 Subtask；真正需要改变问题假设时才重建 Plan。
2. **Reviewer**：从“通过/不通过”升级为结构化审查；每个 Finding 必须说明严重程度、影响范围和建议动作；Reviewer 不直接修改 Plan，也不直接调用 Agent。
3. **Loop Controller**：明确控制 `Revision / Replan / Blocked`；设置 Loop Budget；防止全量重跑与无限循环；对“没有新信息/修复没有改善”的情况及时停止。

本阶段不要做：
- 新增大量 Agent
- UI 重构
- 数据库迁移
- RAG 架构重构
- 大规模 Prompt 重写
- 通过关键词判断解决问题
- 把 Agent 调用关系重新写死成大量 `if/else`

---

# 1. Root Cause：这次日志真正暴露了什么

## 1.1 当前路由仍然是 Agent 级，而不是 Subtask 级

当前 `graph.py` 的 Manager 路由从 `mission.domain_contributions` 查找需要执行的 Agent，并通过 `domain_outputs` 判断 Agent 是否完成。也就是说当前实际执行单位接近：

```text
Agent = execution unit
```

而不是：

```text
Subtask = execution unit
```

因此一旦某个 Subtask 出问题，系统很容易只能“重跑整个 Agent”，进而扩大修复范围。当前代码对此有明确实现：`route_after_manager()` / `route_after_assess()` 都围绕 `domain_contributions` 和 `domain_outputs` 做 Agent 级路由。citeturn589914view2turn312044view0

---

## 1.2 Reviewer FAIL 与 Replan 当前是粗粒度一对一关系

当前 `route_after_reviewer()` 基本是：

```text
review_passed == True
    → Document

review_passed == False
    → manager_assess
```

也就是 Reviewer 非通过会统一进入 Manager Assess / Replan 路径。citeturn312044view1

这无法表达：

```text
一个局部问题
≠
整个 Plan 失效
```

必须拆成：

```text
Finding
├─ KEEP
├─ REVISION
├─ BLOCKED
└─ REPLAN
```

---

## 1.3 当前 `domain_outputs` 是 Agent 级结果，但 Reviewer 需要 Subtask 级结果

当前 `AgentState` 已经存在 `mission`、`domain_outputs`、`plan_version`、`replan_reason`、`review_passed`、`review_findings`、`review_conflicts`、`review_gaps` 等字段，但仍主要按照领域 Agent 管理结果。citeturn589914view1

随着 Manager 动态生成多个 Subtask，State 应至少开始支持：

```text
Subtask ID
    ↓
Subtask status
    ↓
Subtask result
    ↓
Observation
    ↓
Review finding
```

不要求一次性删除 `domain_outputs`；可采用兼容迁移。

---

## 1.4 “Agent 完成”与“Reviewer 可审查”目前不是同一契约

本次实际运行第二轮 Review 报告：

```text
子任务未形成可审查 Observation：
subtask_03
subtask_05
subtask_06
```

fileciteturn0file0L57-L66

这意味着当前至少存在：

```text
Agent：输出了结果，所以 Done
Reviewer：没有结构化 Observation，所以无法检查
```

必须建立统一的 Subtask Result Contract。

---

# 2. 关键设计原则

## 原则 A：Reviewer 负责“发现问题”，Manager 负责“决定动作”

```text
Reviewer
    ↓
Findings

Manager
    ↓
Action
```

Reviewer 不负责：
- 重写 Plan
- 删除 Agent 输出
- 决定所有任务是否重做
- 自己调用 Agent

## 原则 B：默认局部修复，不默认全局重规划

优先级：

```text
KEEP
  ↓
REVISION
  ↓
BLOCKED
  ↓
REPLAN
```

只有真正影响 Mission / 核心假设的问题，才允许进入 Replan。

## 原则 C：Subtask 是最小可重做单位

不要：

```text
Reviewer → Coach failed → 重跑 Coach
```

而应该：

```text
Reviewer
  ↓
subtask_03
  ↓
REVISION
  ↓
Coach 执行 subtask_03
```

## 原则 D：缺信息不是 Agent 失败

例如用户只说“昨天踢了一场野球”，但没有提供 DOMS、睡眠、疲劳、疼痛等信息，则应考虑：

```text
BLOCKED
reason = missing_user_input
```

而不是让 Agent 自行虚构数据，也不是无意义重试。

本次 `subtask_01` 就属于这一类信息缺口。fileciteturn0file0L25-L34

## 原则 E：Loop 的目标不是无限提高文本质量

必须有明确停止条件：

```text
Goal satisfied
OR
No meaningful improvement
OR
Max revision reached
OR
Max replan reached
OR
Blocked by missing information
```

---

# 3. V2 State：从 Boolean 走向结构化状态

当前：

```python
review_passed: bool
review_findings: List[str]
review_conflicts: List[dict]
review_gaps: List[str]
plan_version: int
```

建议新增结构。

## 3.1 Subtask

```python
from typing import TypedDict, Literal

SubtaskStatus = Literal[
    "PENDING",
    "RUNNING",
    "COMPLETED",
    "REVISION_REQUIRED",
    "BLOCKED",
    "SKIPPED",
]

class Subtask(TypedDict, total=False):
    id: str
    objective: str
    capability: str
    assigned_agent: str
    dependencies: list[str]
    status: SubtaskStatus
    revision_count: int
    last_result_version: int
    observation: dict
    result: dict
    blocked_reason: str
```

关键点：`objective` 是 Subtask 身份核心；`assigned_agent` 是当前执行方案，不应该成为硬编码的业务身份。

---

# 4. Subtask Result Contract

所有进入 Reviewer 的 Agent 结果至少满足：

```python
class SubtaskResult(TypedDict, total=False):
    subtask_id: str
    status: str
    observation: dict
    recommendation: str
    evidence: list
    assumptions: list
    uncertainties: list
    constraints_checked: list
    source_version: int
```

## Observation 不等于思维链

不要保存：

```text
我先思考……然后我觉得……所以……
```

应该保存可审查结果：

```json
{
  "facts": [
    "最近两周训练量显著低于历史水平"
  ],
  "findings": [
    "当前没有直接恢复赛季末峰值负荷的依据"
  ],
  "data_used": [
    "training_history",
    "match_history"
  ]
}
```

---

# 5. Reviewer Result Schema v2

不要再主要返回：

```python
review_passed: bool
review_findings: [...]
```

建议：

```python
ReviewDecision = Literal[
    "PASS",
    "REVISE",
    "REPLAN",
    "BLOCKED",
]

FindingAction = Literal[
    "KEEP",
    "REVISION",
    "REPLAN",
    "BLOCKED",
]

Severity = Literal[
    "INFO",
    "LOW",
    "MEDIUM",
    "HIGH",
    "CRITICAL",
]

class ReviewFinding(TypedDict, total=False):
    id: str
    subtask_ids: list[str]
    severity: Severity
    category: str
    description: str
    evidence: list
    action: FindingAction
    reason: str

class ReviewResult(TypedDict, total=False):
    decision: ReviewDecision
    findings: list[ReviewFinding]
    reviewed_subtasks: list[str]
    blocking_information: list[str]
    summary: str
```

重点：`findings` 解决“发现了什么”；`decision` 解决“整体应该怎么办”；`subtask_ids` 解决“影响范围多大”。

---

# 6. Reviewer 决策语义

## PASS

没有实质问题。允许存在 INFO / LOW 级问题，但不得让低价值问题触发 Loop。

## REVISE

局部问题，不改变 Mission：

```text
Subtask_03 时间线冲突
↓
只 Revision Subtask_03
```

如明确存在跨任务依赖，可扩展到 `Subtask_06` 等相关节点，但不能默认全量重做。

## BLOCKED

当前缺少关键输入：

```text
不要重新调用 Agent
不要假设数据
不要 Replan
```

进入 Human-in-the-loop，或者在问题非阻塞时明确记录“不确定性”。

## REPLAN

只有以下情况：

1. 核心假设被证伪；
2. 多个 Subtask 共享的基础假设错误；
3. Mission 本身需要重新解释。

---

# 7. Revision 和 Replan 必须完全分开

```text
               Reviewer
                  │
          ┌───────┼────────┐
          ▼       ▼        ▼
        PASS    REVISE   BLOCKED
          │       │        │
          ▼       ▼        ▼
      SYNTHESIS Manager  USER INPUT
                  │
               Revision
                  │
                  ▼
                Agent
                  │
                  ▼
               REVIEW
```

而：

```text
Reviewer
   ↓
REPLAN
   ↓
Manager Replanning Mode
   ↓
New Hypothesis / New Plan
```

因此：

```text
REVIEW ≠ REPLAN
REVISION ≠ REPLAN
BLOCKED ≠ REPLAN
```

---

# 8. Loop Controller v2

不要继续：

```python
while not review_passed:
    ...
```

而应显式管理预算：

```python
class LoopBudget(TypedDict):
    max_revisions: int
    max_replans: int
    max_total_iterations: int
```

第一版可以使用保守预算：

```python
MAX_REVISIONS_PER_SUBTASK = 1
MAX_REPLANS_PER_MISSION = 2
MAX_TOTAL_LOOPS = 4
```

预算不是为了让系统变笨，而是为了防止失败状态不断扩大。

---

# 9. 建议的状态机

```text
                    ┌──────────────┐
                    │   MANAGER    │
                    │ Plan/Hypoth. │
                    └──────┬───────┘
                           ↓
                     EXECUTE TASKS
                           ↓
                    STRUCTURED RESULT
                           ↓
                       REVIEWER
                           │
              ┌────────────┼────────────┐
              ↓            ↓            ↓
            PASS         REVISE       BLOCKED
              │            │            │
              ↓            ↓            ↓
          SYNTHESIS    MANAGER      HUMAN INPUT
                           │
                       REVISION
                           │
                           ▼
                         Agent
                           │
                           ▼
                        REVIEW

              Reviewer
                  │
                  ↓
               REPLAN
                  │
                  ▼
           Manager Replanning
                  │
           Update Hypothesis
                  │
                  ▼
             New Dynamic Plan
                  │
                  └────→ EXECUTE
```

---

# 10. Graph 路由修改

当前 Reviewer 非通过直接进入 `manager_assess`。citeturn312044view1

建议改成：

```python
def route_after_reviewer(state: AgentState) -> str:
    review = state.get("review", {})
    decision = review.get("decision")

    if decision == "PASS":
        return "document"

    if decision == "REVISE":
        return "manager_revision"

    if decision == "BLOCKED":
        return "human_input"

    if decision == "REPLAN":
        return "manager_replan"

    return "document"
```

具体节点名可以按现有代码调整；关键是四种状态必须有独立语义。

---

# 11. Manager：三个 Mode，而不是三个 Agent

## 11.1 PLANNING

第一次进入：

```text
User Input
↓
Mission
↓
Hypotheses
↓
Dynamic Subtasks
```

## 11.2 REVISION

收到 `decision = REVISE`：

```text
Reviewer findings
↓
定位 affected subtasks
↓
生成最小修改计划
```

必须约束：

```text
默认保留未受影响 Subtask
不得默认重建整个 Mission
不得删除有效结果
Revision 的目标是解决 Finding，而不是重新生成答案
```

## 11.3 REPLANNING

只有收到 `decision = REPLAN` 才进入：

```text
重新检查 Hypothesis
重新检查 Mission
重新检查 information gaps
重新生成动态 Plan
```

---

# 12. Hypothesis 应正式进入 State

```python
HypothesisStatus = Literal[
    "OPEN",
    "SUPPORTED",
    "WEAKENED",
    "REJECTED",
]

class Hypothesis(TypedDict, total=False):
    id: str
    statement: str
    confidence: float
    status: HypothesisStatus
    supporting_evidence: list
    contradicting_evidence: list
    created_in_plan_version: int
    updated_in_plan_version: int
```

示例：

```text
H1：训练中断导致当前竞技状态不足 0.60
H2：昨日野球造成额外疲劳       0.30
H3：存在未发现的疼痛/伤病       0.10
```

Observation 回来后允许更新置信度或状态；只有核心假设明显被削弱/证伪时才触发 Replan。

不要求第一版实现正式贝叶斯更新；结构上先保存 `confidence + evidence + status` 即可。

---

# 13. Dynamic Plan Schema

```python
class Plan(TypedDict, total=False):
    version: int
    objective: str
    hypothesis_ids: list[str]
    constraints: list[str]
    information_gaps: list[str]
    subtasks: list[Subtask]
    dependencies: dict[str, list[str]]
    revision_targets: list[str]
    termination_reason: str
```

重要：

```text
Plan version 增加
≠
所有 Subtask 重新执行
```

例如：

```text
Plan v1
01 ✓
02 ✓
03 ✗
04 ✓

Revision

Plan v1.1
01 KEEP
02 KEEP
03 REVISE
04 KEEP
```

只有核心问题/假设变化时，才真正进入 Plan v2。

---

# 14. 针对本次日志应如何处理

本次 Manager 初次动态生成了 6 个 Subtask，包括恢复状态、前2天、3-5天、6-7天、监测指南以及最终整合计划。fileciteturn0file0L25-L34

第一次 Reviewer 实际发现三类问题：

### A：Subtask_01 缺少野球赛后直接信息

正确：

```text
BLOCKED / information gap
```

不要让模型自行生成 DOMS、疲劳、睡眠等不存在的数据。

### B：Subtask_02 / 03 时间线冲突

正确：

```text
REVISION
scope = [subtask_03]
```

必要时因依赖关系包含相关整合 Subtask。

### C：Subtask_03 / 04 减量安排冲突

正确：

```text
REVISION
scope = [subtask_03, subtask_04]
```

而不是：

```text
重开 01~06
```

本次日志记录的 Manager v2 恰恰选择了“重开所有子任务并新增协调子任务”，这是要重点消除的行为。fileciteturn0file0L52-L56

---

# 15. 不要继续使用“删除 Agent output → 重新执行 Agent”作为默认 Revision 机制

当前 `graph.py` 有 `_DELETE_SENTINEL = "__DELETE_KEY__"`，并明确用于 Manager Assess 清除低质量输出后触发重新执行。citeturn589914view0

这个机制可以保留兼容，但不应再作为默认 Loop 控制方式。

优先：

```text
Reviewer finding
↓
affected_subtasks
↓
仅重新执行这些 Subtask
↓
写入新的 result version
```

如果暂时无法完成 Subtask 级存储，也至少建立：

```text
subtask_revision_targets
```

不要因为单一 Subtask 有问题就删除整个 Agent 的全部有效结果。

---

# 16. `domain_outputs` 采用兼容迁移

不要求一次性推翻。

第一阶段保留：

```python
domain_outputs
```

同时加入：

```python
subtasks
subtask_results
review
hypotheses
loop_control
```

形成：

```text
domain_outputs
    ↓
兼容旧 Agent

subtask_results
    ↓
新 Loop Controller
```

稳定后再逐渐降低对 `domain_outputs` 的依赖。

---

# 17. Loop Controller 核心伪代码

```python
def handle_review(state):
    review = state["review"]

    if review["decision"] == "PASS":
        return "FINISH"

    if review["decision"] == "BLOCKED":
        return "WAIT_FOR_USER"

    if review["decision"] == "REVISE":
        targets = collect_revision_targets(review)

        if not targets:
            return "FINISH"

        if exceeds_revision_budget(state, targets):
            return "FINISH_WITH_WARNINGS"

        return "REVISION"

    if review["decision"] == "REPLAN":
        if exceeds_replan_budget(state):
            return "FINISH_WITH_WARNINGS"

        return "REPLAN"
```

核心不是函数名字，而是：

```text
Review decision
        ↓
Loop Controller
        ↓
Action
```

---

# 18. Revision Controller

```python
def build_revision_plan(state):
    review = state["review"]
    targets = []

    for finding in review["findings"]:
        if finding["action"] != "REVISION":
            continue

        for subtask_id in finding["subtask_ids"]:
            if subtask_id not in targets:
                targets.append(subtask_id)

    return {
        "mode": "REVISION",
        "targets": targets,
    }
```

Revision 只执行 `targets`，不重新扫描所有 Agent。

---

# 19. 防止 Reviewer “永远发现问题”

Reviewer Prompt 不应要求：

```text
找出所有可能的问题
```

应明确：

> 只报告会影响 Mission 正确性、可执行性、安全性或内部一致性的实质问题；低价值措辞、个人偏好和可接受的不确定性不要触发 Revision。

严重度建议：

```text
LOW
→ KEEP

MEDIUM
→ REVISION

HIGH
→ REVISION / REPLAN

CRITICAL
→ REPLAN / BLOCKED
```

这样 Reviewer 可以保持严格，但严格不再等于“必须继续循环”。

---

# 20. 防止“修复放大”

Manager Revision Prompt 必须明确：

```text
1. 默认保留 Reviewer 未指认有问题的 Subtask。
2. 不得因为一个 Subtask 的问题而默认重建整个 Plan。
3. 只有当 Finding 明确影响其他 Subtask 时，才扩大 revision scope。
4. 不得删除未受影响的已有有效结果。
5. Revision 的目标是修复 Finding，而不是重新生成一份全新的答案。
```

---

# 21. Token / Latency：本阶段顺手做的控制

这次日志从第一次 Plan 到多轮 Manager Assess / Reviewer，再到最终 Document，已经出现明显的重复 Agent 调用。fileciteturn0file0L43-L70

本阶段至少做到：

## 21.1 Revision 只传必要上下文

目标 Agent 不要再次接收整个历史：

```text
Mission summary
+
Target subtask
+
Relevant dependencies
+
Reviewer finding
+
Previous result
```

## 21.2 不要重跑 unaffected Agent

例如只修 `subtask_03`，不要重新运行 Nutrition / Career / Performance，除非它们明确属于 affected scope。

## 21.3 增加调用遥测

建议加入：

```python
llm_call_count
manager_call_count
reviewer_call_count
agent_call_count
review_count
revision_count
replan_count
latency_ms
input_tokens
output_tokens
```

这样才能定位“20 万 token 到底烧在哪里”，不要凭感觉优化。

## 21.4 RAG / Embedding 模型初始化要检查生命周期

本次日志显示每次运行都出现 ChromaDB、Embedding、Reranker 的加载日志。fileciteturn0file0L35-L42

这一点更偏运行时间问题而非 Token 问题；排查目标是确认这些模型是否在每轮 Node / 每个请求重复初始化。应尽可能在进程级复用，而不是每次调用重新加载。

---

# 22. 当前 `manager_assess` 如何改

不要急着删除。

先将其职责重新定义为：

```text
Manager Decision / Loop Controller
```

而不是：

```text
Reviewer fail
→ 全量 Replan
```

它应至少能够表达：

```text
KEEP
REVISION
BLOCKED
REPLAN
FINISH
```

如果最终 `manager_assess` 过于复杂，再把内部逻辑拆成纯 Python 函数，而不是增加新的 Agent。

---

# 23. 推荐 Graph

```text
                         START
                           │
                           ▼
                       MANAGER
                     Planning Mode
                           │
                           ▼
                    SUBTASK ROUTER
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
           Agent A       Agent B      Agent C
              └────────────┼────────────┘
                           ▼
                    STRUCTURED RESULTS
                           │
                           ▼
                        REVIEWER
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
           PASS          REVISE        BLOCKED
             │             │             │
             ▼             ▼             ▼
         DOCUMENT      MANAGER       HUMAN INPUT
                           │
                     Revision Mode
                           │
                           ▼
                      Target Agents
                           │
                           ▼
                        REVIEWER

                Reviewer → REPLAN
                           │
                           ▼
                    Manager Replan
                           │
                      New Hypothesis
                           │
                      New Dynamic Plan
                           │
                           └────→ EXECUTE
```

---

# 24. 必须增加的测试用例
将测试日志及其结果放入docs/tests_longs内：
## Test A：正常任务

```text
PASS
→ Document
```

预期：1 次 Reviewer，0 次 Revision，0 次 Replan。

## Test B：单个 Subtask 局部错误

```text
Reviewer:
subtask_03 = REVISION
```

预期：只重跑 Subtask_03；其他有效 Subtask 保留。

## Test C：跨 Subtask 冲突

```text
subtask_03 ↔ subtask_04
```

预期：只 Revision 03/04 或明确的最小受影响集合。

## Test D：缺少用户信息

```text
subtask_01 BLOCKED
```

预期：Human-in-the-loop；禁止虚构数据；禁止无意义重试。

## Test E：核心 Hypothesis 被证伪

```text
Reviewer
→ REPLAN
→ Manager Replanning
→ New Hypothesis / Plan
```

## Test F：Reviewer 连续指出低价值问题

```text
LOW finding
→ KEEP
```

不能进入无限 Revision。

---

# 25. 这次重构的硬验收标准

## Loop

- 不允许因为一个局部 Finding 默认全量重跑。
- `REVISION` 与 `REPLAN` 必须有不同路径。
- `BLOCKED` 不得自动转成 Replan。
- 每个 Subtask 必须存在可审查 Observation。
- 同一个 Subtask 默认最多 Revision 1 次。
- Mission 默认最多 Replan 2 次。
- Loop 必须有总预算。

## Reviewer

- 必须输出 `decision`。
- 每个重要 Finding 必须有 `subtask_ids`。
- 每个 Finding 必须有 `severity`。
- 每个 Finding 必须有 `action`。
- Reviewer 不直接执行修改。

## Manager

- Planning、Revision、Replanning 语义分开。
- Revision 默认只修改 affected scope。
- Replan 必须说明 Hypothesis / Mission 为什么发生变化。

## 性能

至少能够统计：

```text
LLM calls
Reviewer calls
Agent calls
Revision count
Replan count
Latency
Input tokens
Output tokens
```

不要在没有这些数据的情况下继续凭感觉做 Token 优化。

---

# 26. 推荐实施顺序

### Step 1：RCA

完整阅读：

```text
graph.py
Manager
Reviewer
各 Agent output schema
registry.py
```

重点确认：

```text
谁创建 Subtask？
谁保存 Subtask？
谁决定 Agent？
谁修改 domain_outputs？
谁触发 manager_assess？
```

当前仓库的 `task_debug.md` 也明确要求先完成 RCA、理解 Manager / Task / Agent / Tool / Document 的数据流，再开始代码修改，因此不要跳过这一步。citeturn627476view3

### Step 2：只改数据结构

先加入：

```text
Subtask
SubtaskResult
ReviewFinding
ReviewResult
LoopControl
Hypothesis
```

### Step 3：统一 Agent 输出

确保每个需要 Reviewer 的 Subtask 都产生：

```text
status
observation
result
```

先解决本次日志的 Observation 缺失问题。fileciteturn0file0L57-L66

### Step 4：改 Reviewer

从：

```text
boolean pass/fail
```

变成：

```text
ReviewResult
```

### Step 5：改 Manager

增加：

```text
Planning Mode
Revision Mode
Replanning Mode
```

### Step 6：最后改 LangGraph edges

建立：

```text
PASS
REVISE
BLOCKED
REPLAN
```

四条独立路径。

### Step 7：加入 telemetry

记录 token、延迟、调用次数和 Loop 次数，再进行下一轮优化。

---

# 27. 最终目标架构

```text
                 USER
                  │
                  ▼
              MANAGER
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
   HYPOTHESES            MISSION
        │                   │
        └─────────┬─────────┘
                  ▼
             DYNAMIC PLAN
                  │
               SUBTASKS
                  │
                  ▼
               AGENTS
                  │
                  ▼
         STRUCTURED OBSERVATIONS
                  │
                  ▼
               REVIEWER
                  │
        ┌─────────┼─────────┐
        ▼         ▼         ▼
       PASS     REVISION   BLOCKED
                  │
                  ▼
               MANAGER
                  │
         ┌────────┴────────┐
         ▼                 ▼
     Revision           Replan
         │                 │
         │           Update Hypothesis
         │                 │
         └────────┐   ┌────┘
                  ▼   ▼
                 PLAN
                  │
                  ▼
               EXECUTE
```

最终需要解决的不是“更强的 Reviewer”，也不是“更聪明的 Manager”，而是一套**有粒度、有预算、有状态、有终止条件的闭环控制机制**。

必须始终区分：

```text
Finding      = Reviewer 发现了什么
Revision     = 局部修复已有 Plan
Replan       = 核心假设 / 任务结构发生变化
Blocked      = 当前无法继续，需要外部信息
```

只要这四者不再混为一谈，当前日志里的“重复 Review、全量重跑、巨量 Token / 时间消耗”才会真正从架构层面得到控制。
