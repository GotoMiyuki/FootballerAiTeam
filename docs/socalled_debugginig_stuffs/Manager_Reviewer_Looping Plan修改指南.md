# Manager / Reviewer / Looping Plan & Hypothesis 修改指南

> 目标：先完成 FootballerAiTeam 的核心决策闭环重构，再考虑 UI、RAG 深化、数据库迁移等外围工程。
>
> 本阶段只解决三个问题：
> 1. Manager 从“直接分派 Agent”升级为“动态任务分解 + 规划 + 重规划”的任务驱动中心。
> 2. Reviewer 从“最终检查答案”升级为系统性的结果审查/验证机制。
> 3. 建立 `Hypothesis → Plan → Execute → Observe/Review → Update → Replan` 的闭环。
>
> **重要原则：不要通过大量 if/else、固定 Agent 顺序、固定任务模板，把新的架构重新做成硬编码 Workflow。** 本阶段的目标是建立稳定的机制，而不是固定的业务流程。

---

## 1. 本阶段的总体架构目标

当前系统应逐渐从：

```text
User
  ↓
Manager
  ↓
固定/半固定 Agent 调用
  ↓
Reviewer
  ↓
Final Answer
```

演进为：

```text
User
  ↓
Manager
  ├─ 理解 Intent
  ├─ 建立 Mission / Goal
  ├─ 形成初始 Hypothesis（必要时）
  └─ 动态生成 Plan
         ↓
   Orchestration / Execution Runtime
         ↓
      Agent / Tool
         ↓
      Observation
         ↓
      Reviewer
         ↓
   State + Hypothesis Update
         ↓
       Replan
         ↓
      下一轮执行
         ↓
    Goal Satisfied
         ↓
      Synthesis
```

核心思想：

> **Manager 决定“现在应该解决什么问题”；执行机制负责“如何执行”；Agent 负责专业领域推理；Reviewer 负责判断结果是否可信、完整、符合约束；新的观察结果再反过来改变 Plan。**

---

# 2. Manager 重构

## 2.1 Manager 的新职责

Manager 不应只是：

```text
用户说什么
→ 判断需要哪个 Agent
→ 调 Agent
```

而应承担以下职责：

```text
1. Intent Understanding
2. Goal / Mission Formation
3. Problem Decomposition
4. Hypothesis Formation（当问题存在不确定性时）
5. Plan Generation
6. Task Prioritization / Dependency 判断
7. Task Assignment
8. Constraint 管理
9. 根据 Observation / Review 结果进行 Replan
10. 判断任务是否已经达到终止条件
```

Manager 是**决策层**，不是底层执行引擎。

---

## 2.2 Manager 不应该做什么

不要让 Manager 承担以下职责：

```text
- 直接执行专业任务
- 写死所有 Agent 的调用顺序
- 根据 intent 写大量固定 if/else Workflow
- 自己替代每个专业 Agent 做领域判断
- 看到任何 Reviewer 问题都要求所有 Agent 全部重做
- 把一次 Plan 当成不可修改的执行脚本
```

特别注意：

> **Manager 可以决定“这个任务交给哪个 Agent / Capability”，但不应把“某类问题永远必须调用某些 Agent”写死。**

---

# 3. Manager 的核心抽象：Mission、Plan、Subtask

建议明确区分三层对象。

## 3.1 Mission：为什么做

Mission 表达用户真正想达成的目标，不应该过度具体地规定执行方式。

建议至少包含：

```text
mission_id
objective
constraints
context
required_deliverable
success_criteria
```

示例：

```json
{
  "objective": "帮助球员在7天后的决赛中达到最佳比赛状态",
  "constraints": [
    "比赛距离当前7天",
    "不能因训练安排增加比赛日疲劳"
  ],
  "required_deliverable": "match_week_preparation_plan",
  "success_criteria": [
    "覆盖训练、恢复和比赛日前准备",
    "与当前球员状态一致",
    "关键建议经过审查"
  ]
}
```

这里**不要**直接写：

```json
"agents": ["Coach", "Nutrition", "Performance"]
```

因为 Mission 描述的是业务目标，而不是实现方案。

---

## 3.2 Plan：现在准备怎么解决

Plan 是 Manager 当前对问题的执行假设，不是固定 Workflow。

Plan 应该可以动态变化。

建议概念上包含：

```text
plan_id
version
objective
hypotheses
subtasks
dependencies
constraints
termination_conditions
```

其中最重要的是 `subtasks`。

---

## 3.3 Subtask：当前要解决的一个具体问题

Subtask 应尽量描述“问题/目标”，而不是简单描述“调用某 Agent”。

推荐：

```json
{
  "id": "subtask_01",
  "goal": "评估球员当前竞技状态",
  "purpose": "验证当前状态下降是否与训练负荷有关",
  "capability": "performance_analysis",
  "priority": 1,
  "depends_on": [],
  "status": "pending"
}
```

而不是：

```json
{
  "agent": "PerformanceAgent",
  "task": "分析数据"
}
```

因为前一种设计允许以后替换 Agent、Capability 或执行方式，而不改变 Mission/Plan 层。

---

# 4. Plan 必须是动态生成的，而不是静态模板

## 4.1 禁止的设计

不要写：

```python
if intent == "match_preparation":
    tasks = [
        "training",
        "nutrition",
        "performance"
    ]
```

也不要：

```python
MATCH_PREPARATION_WORKFLOW = [
    Coach,
    Nutrition,
    Analyst,
    Reviewer
]
```

这会把当前系统重新变成硬编码 Pipeline。

---

## 4.2 推荐设计

让 Manager LLM 生成结构化 Plan：

```text
User Input
   ↓
Manager
   ↓
Structured Mission
   ↓
Structured Plan
```

然后底层执行机制只负责：

```text
读取 Plan
→ 找到 ready Subtask
→ 根据 capability / assignment 找执行者
→ 执行
→ 写回 State
```

即：

> **LLM 动态决定“做什么”；代码负责稳定地执行“这个计划”。**

---

# 5. Manager 的任务分解原则

Manager 分解任务时应遵循：

### 5.1 先问“需要解决哪些问题”，再问“谁做”

例如：

```text
用户：
“我一周后决赛，但最近三场只踢了30分钟，帮我准备。”
```

不要直接生成：

```text
Coach → Training
Nutrition → Nutrition
Performance → Performance
Career → Career
```

而应先得到类似：

```text
A. 评估当前竞技状态
B. 分析近期出场时间与比赛角色变化
C. 判断未来7天最重要的准备风险
D. 制定比赛周训练方案
E. 制定比赛周营养方案
```

然后再决定这些 Subtask 最适合由谁处理。

---

### 5.2 允许并行，也允许依赖

Plan 不一定是一条链。

例如：

```text
A 竞技状态评估 ─────→ D 训练方案 ──→ F 最终整合
        │
        └────────────→ E 风险评估

B 比赛角色分析 ─────→ D 训练方案

C 营养状态评估 ─────→ E 风险评估
```

因此 Subtask 应支持：

```text
depends_on
status
priority
```

Manager 决定依赖关系，而不是由代码预设所有任务顺序。

---

# 6. Hypothesis：什么时候需要，什么时候不需要

并不是每个用户请求都需要 Hypothesis。

## 6.1 低不确定性任务

例如：

> “帮我把今天的训练记录整理成表格。”

没有必要建立复杂假设。

可以：

```text
Mission
→ Plan
→ Execute
→ Review
→ Done
```

## 6.2 高不确定性任务

例如：

> “我最近比赛表现越来越差，帮我看看为什么。”

此时 Manager 不应该马上选定某个原因。

应该维护多个候选 Hypothesis：

```text
H1：近期训练负荷过高
H2：比赛角色/战术职责发生变化
H3：比赛参与度变化导致竞技表现下降
H4：恢复/营养不足
```

Hypothesis 表示“当前认为可能成立、但尚未确认的解释”。

---

# 7. Hypothesis 数据结构

建议至少支持：

```python
class Hypothesis:
    id: str
    statement: str
    confidence: float
    status: Literal[
        "open",
        "supported",
        "weakened",
        "rejected"
    ]
    supporting_evidence: list[str]
    contradicting_evidence: list[str]
```

第一版不要求实现严格的 Bayesian inference。

`confidence` 可以先作为 Manager 的结构化判断指标；重点是保留：

```text
当前认为是什么
为什么这么认为
哪些证据支持它
哪些证据反驳它
当前状态
```

---

# 8. Looping Plan：Plan 不是一次性脚本

这是本次重构最核心的概念。

Plan 应被视为：

> **基于当前 State 和当前 Hypothesis 所生成的“下一步最佳行动方案”。**

因此：

```text
Plan v1
 ↓
Execute
 ↓
Observation
 ↓
State Update
 ↓
Hypothesis Update
 ↓
Plan v2
```

Plan 可以被修改、缩减、扩展、替换。

不要设计成：

```text
Plan generated once
→ execute all tasks
→ final
```

---

# 9. Loop 的完整宏观闭环
（其实这个图是GPT生成的，我觉得没有很好地体现looping感，不过只要逻辑对就行了）
推荐实现成以下逻辑：

```text
                 ┌─────────────────────┐
                 │        User         │
                 └──────────┬──────────┘
                            ↓
                 ┌─────────────────────┐
                 │      Manager        │
                 │ Intent / Mission    │
                 │ Hypothesis          │
                 │ Plan                │
                 └──────────┬──────────┘
                            ↓
                    ┌───────────────┐
                    │   Subtasks    │
                    └───────┬───────┘
                            ↓
                    Execute ready tasks
                            ↓
                ┌───────────┴───────────┐
                ↓                       ↓
             Agent                  Tool/Data
                └───────────┬───────────┘
                            ↓
                       Observation
                            ↓
                 ┌─────────────────────┐
                 │      Reviewer       │
                 │ Review / Validation │
                 └──────────┬──────────┘
                            ↓
                      Update State
                            ↓
                  Update Hypotheses
                            ↓
                 ┌─────────────────────┐
                 │ Manager Replan      │
                 └──────────┬──────────┘
                            ↓
                       Plan vN+1
                            │
                 ┌──────────┴──────────┐
                 │                     │
             Goal Met              Not Met
                 │                     │
                 ↓                     └──────→ Next Loop
             Synthesis
```

---

# 10. Replan 不等于“全部重做”

这是实现时最容易犯的错误之一。

Reviewer 发现问题后，Manager 应该决定：

```text
哪些 Subtask 通过？
哪些 Subtask 失败？
哪些结论仍然有效？
哪些新信息改变了原来的 Hypothesis？
下一轮到底需要做什么？
```

例如：

```text
Training    ✓ passed
Nutrition   ✓ passed
Performance ✗ insufficient evidence
```

正确的 Replan：

```text
保留 Training
保留 Nutrition
重新执行 Performance
```

而不是：

```text
Training → 重做
Nutrition → 重做
Performance → 重做
```

因此每个 Subtask 都应该拥有独立状态：

```text
pending
running
completed
needs_revision
failed
skipped
```

---

# 11. Reviewer 重构：从“最终评分器”变成“结果审查器”

Reviewer 的职责不是简单判断：

> “这段回答好不好？”

而是判断：

> **当前结果能不能被系统安全地接受，并写入后续决策 State？**

推荐 Reviewer 至少审查以下维度：

```text
1. Completeness
2. Consistency
3. Evidence / Provenance
4. Validity
5. Constraint Compliance
6. Risk / Uncertainty
```

---

# 12. Reviewer 的六类审查

## 12.1 Completeness

检查：

```text
Subtask 要求有没有完成？
Mission 的关键要求有没有遗漏？
```

## 12.2 Consistency

检查：

```text
Agent A 和 Agent B 是否冲突？
不同结论是否建立在互相矛盾的前提上？
```

## 12.3 Evidence / Provenance

不是要求“每句话都必须 citation”。

检查的是：

```text
关键事实从哪里来？
关键结论依赖什么？
这个结论的依据是否足够？
是否把推测说成了事实？
```

证据来源可以是：

```text
User input
Player data
Historical data
RAG knowledge
External search
Calculator / Tool
Agent reasoning
Derived calculation
```

## 12.4 Validity

检查：

```text
计算是否正确
数字是否自洽
推理是否存在明显逻辑错误
```

## 12.5 Constraint Compliance

检查：

```text
是否违反用户约束
是否违反 Mission 约束
是否忽略比赛时间、球员条件等关键上下文
```

## 12.6 Risk / Uncertainty

检查：

```text
是否过度自信
是否存在未经证实的强结论
高风险建议是否需要更谨慎表达
```

---

# 13. Reviewer 的输出不要只是“通过 / 不通过”

推荐结构化：

```json
{
  "status": "needs_revision",
  "overall_confidence": 0.78,
  "findings": [
    {
      "subtask_id": "subtask_03",
      "type": "unsupported_claim",
      "severity": "medium",
      "description": "训练负荷判断缺少近期训练数据支持",
      "action": "request_additional_data"
    },
    {
      "subtask_id": "subtask_05",
      "type": "constraint_violation",
      "severity": "high",
      "description": "比赛仅剩3天，但方案仍安排高强度训练",
      "action": "revise_subtask"
    }
  ]
}
```

Reviewer 应告诉 Manager：

```text
哪里有问题
问题是什么类型
严重程度
为什么是问题
建议采取什么行动
```

而不是替 Manager 决定整个系统下一轮怎么跑。

---

# 14. Reviewer 与 Manager 的职责边界

非常重要：

```text
Reviewer：
“这里有问题。”

Manager：
“基于这个问题，下一步应该做什么？”
```

不要让 Reviewer 直接变成第二个 Manager。

Reviewer 可以提出：

```text
request_more_evidence
revise_subtask
resolve_conflict
recalculate
```

但最终由 Manager 决定：

```text
保留
重做
新增任务
改变 Hypothesis
终止任务
```

---

# 15. Observation 是 Loop 中不可缺少的中间状态

每次 Agent 执行结束后，不要只把最终文本塞回 `domain_outputs`。

系统至少应该能知道：

```text
哪个 Subtask 执行了
产生了什么 Result
产生了什么 Evidence
出现了什么 Uncertainty
Reviewer 给出了什么 Finding
这些信息改变了什么 Hypothesis
```

可以在 State 层逐渐形成类似：

```python
Observation(
    subtask_id=..., 
    result=..., 
    evidence=..., 
    findings=..., 
    confidence=...
)
```

第一版可以保持轻量，不要求一次把所有数据结构设计到最终状态。

---

# 16. Hypothesis Update 的核心原则

每轮执行结束后，Manager 不应该机械地问：

> “任务做完了吗？”

而应该进一步问：

> **“新信息是否改变了我对问题的判断？”**

例如：

```text
H1：训练负荷过高       confidence 0.55
H2：战术角色变化       confidence 0.30
H3：恢复不足           confidence 0.15
```

得到新 Observation：

```text
最近两周训练负荷正常
最近5场比赛位置发生明显变化
```

则：

```text
H1 → weakened / rejected
H2 → supported
H3 → unresolved
```

随后生成 Plan v2。

---

# 17. 不要为了“看起来像 Agent”而强行使用 Bayesian 算法

第一阶段不建议把 Hypothesis 系统复杂化为正式 Bayesian Network、MCTS 等。

本项目当前更重要的是建立正确的控制流：

```text
Hypothesis
→ Evidence
→ Update
→ Replan
```

而不是数学上多复杂。

只要能够结构化表达：

```text
支持了什么
削弱了什么
还不知道什么
下一步应该调查什么
```

第一版就已经成立。

---

# 18. Loop 的终止条件必须显式存在

任何循环都必须有明确的停止条件。

至少支持：

```text
1. Goal satisfied
2. Required evidence sufficient
3. No meaningful information gap remains
4. Maximum iteration reached
5. Replanning no longer improves the plan
6. Critical failure / safety condition
```

建议 State 中记录：

```text
iteration
max_iterations
termination_reason
```

禁止出现：

```text
Reviewer → Manager → Agent → Reviewer → Manager → ...
```

只因为 Reviewer 每次都能找出一个“可以优化的小问题”。

---

# 19. “Information Gap” 是 Loop 的重要驱动因素

当 Hypothesis 不确定时，Manager 应优先考虑：

> **现在缺少什么信息？哪个 Subtask 最能减少当前不确定性？**

例如：

```text
H1 训练负荷过高      0.55
H2 战术角色变化      0.30
H3 恢复问题          0.15
```

此时一个好的下一步不一定是：

```text
“直接写训练计划”
```

而可能是：

```text
“分析最近5场比赛的出场位置、比赛职责和高强度跑动变化”
```

因为这个任务能有效区分 H1 与 H2。

因此未来 Plan 可以逐渐增加：

```text
information_gap
expected_information_gain
```

但第一版可以只保留语义层面的 `purpose`，不必实现真正的信息增益算法。

---

# 20. 推荐的最小 State 演进方向

不要求立即一次完成所有字段重构，但最终应该逐步支持：

```text
AgentState
│
├── user_context
├── mission
├── plan
│   ├── version
│   ├── subtasks
│   └── termination_conditions
│
├── hypotheses
│
├── observations
│
├── review
│   ├── findings
│   └── status
│
├── iteration
├── current_subtask
└── final_result
```

重点是：

> **Plan、Hypothesis、Observation、Review 应成为一等状态，而不是只存在于 Agent prompt 或日志里。**

---

# 21. 推荐的 LangGraph 宏观节点

第一阶段不要增加大量 Agent，只调整 workflow 层。

可以抽象成：

```text
START
  ↓
Manager / Understand
  ↓
Manager / Plan
  ↓
Select Ready Subtask
  ↓
Execute Agent / Tool
  ↓
Review Result
  ↓
Update State / Observation
  ↓
Manager / Replan
  ↓
Termination Check
  ├── continue → Select Ready Subtask
  └── finish   → Synthesis / Document
```

如果当前 Manager 本身已经包含 Planning、Replanning 逻辑，可以先保留一个 Manager Node，通过 mode 区分：

```text
Manager(mode="initial_plan")
Manager(mode="replan")
```

不要为了抽象而新增大量 Agent。

---

# 22. 一个完整示例

用户：

> “我一周后有决赛，但最近比赛表现不好，帮我准备。”

### Round 0：Manager

```text
Mission:
最大化决赛竞技状态
```

Hypotheses：

```text
H1：训练负荷问题
H2：战术角色问题
H3：恢复问题
```

Plan v1：

```text
A：分析近期训练负荷
B：分析近期比赛表现和角色
C：分析恢复/营养情况
```

### Round 1：Execute

```text
A → Performance
B → Analyst
C → Nutrition
```

### Round 1：Reviewer

发现：

```text
A：数据充分 ✓
B：数据充分 ✓
C：用户没有提供足够恢复数据
```

### Hypothesis Update

```text
H1 weakened
H2 supported
H3 unresolved
```

### Replan：Plan v2

```text
保留 A、B 结果

新增 D：分析战术角色变化对当前表现的影响
新增 E：向用户获取睡眠/恢复信息
```

C 不一定重新执行。

### Round 2

D 得到：

```text
最近球队改打4-3-3
球员从8号位转为更保守的6号位
```

H2 得到进一步支持。

Manager 更新 Plan：

```text
训练：维持状态，不激进增加训练负荷
战术：重点进行6号位职责专项准备
恢复：根据新获得的信息调整
营养：维持比赛周方案
```

最后 Reviewer 对完整方案进行最终审查。

这才形成一个真正的：

```text
Hypothesis
→ Investigation
→ Evidence
→ Belief Update
→ Replan
→ Action
→ Final Review
```

---

# 23. 本阶段明确不做的事情

为了防止重构范围失控，本阶段先不要做：

```text
- 新增大量 Agent
- 更换 LangGraph
- 更换 Vector DB
- 引入 PostgreSQL
- 设计 Web UI
- 引入复杂 Bayesian / MCTS 算法
- 做完整的长期 Memory 系统
- 把所有 Agent 输出都改造成极其复杂的数据结构
```

这些都不是当前核心瓶颈。

当前核心瓶颈是：

> **让系统形成稳定、可解释、可循环的“问题理解 → 动态计划 → 执行 → 审查 → 状态更新 → 重规划”闭环。**

---

# 24. 重构完成后的验收标准

## Manager

- 能根据用户输入动态生成 Mission。
- 能将 Mission 动态分解为 Subtasks。
- Subtask 描述解决的问题，而不是简单写死 Agent 名称。
- 支持任务优先级与依赖关系。
- 能根据 Observation / Reviewer Findings 修改 Plan。
- 不依赖大量 intent → 固定 Agent 列表的 if/else。

## Reviewer

- 不只检查最终文本质量。
- 至少覆盖 Completeness、Consistency、Evidence、Validity、Constraint、Risk 六类审查。
- 输出结构化 Findings。
- 能指出具体 Subtask 的问题。
- 不直接接管整个 Replan 决策。

## Hypothesis / Loop

- 支持多个候选 Hypothesis。
- Hypothesis 有状态和 confidence。
- Agent 结果能够形成 Observation。
- Observation 能影响 Hypothesis。
- Hypothesis 更新能影响下一版 Plan。
- Replan 不会自动重做所有任务。
- 存在明确终止条件和最大迭代次数。
- 能避免 Reviewer ↔ Manager ↔ Agent 无限循环。

---

# 25. 最终设计原则

本次重构请始终遵守以下三句话：

> **1. Manager 决定“为什么做、现在解决什么问题、下一步做什么”。**

> **2. Reviewer 判断“当前结果能否被接受，以及哪里存在问题”。**

> **3. Plan 是当前假设，不是固定脚本；新信息出现后，Plan 可以被修改。**

最终希望得到的不是一个更复杂的固定 Pipeline，而是一个能够根据上下文动态改变行为、同时又受到结构化 State、Reviewer 和终止条件约束的 Agentic Workflow。
