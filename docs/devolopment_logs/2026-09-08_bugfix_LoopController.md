# FootballAI Career Agent 开发日志 · 第 2 期 / v1.1.1（两阶段 Looping Plan 改造）
*日志日期：2026-09-08 | 编写人：Codex*

---

## 1. 上下文快照

- **分支 / Tag**：`bugfix-RAG`
- **依赖变动**：否（两轮改造均未新增或升级第三方库）
- **关联文档/Issue**：[Manager_Reviewer_Looping Plan修改指南.md](../socalled_debugginig_stuffs/Manager_Reviewer_Looping%20Plan修改指南.md)、[FootballerAiTeam_LoopController_v2_修改指南.md](../socalled_debugginig_stuffs/FootballerAiTeam_LoopController_v2_修改指南.md)

---

## 2. 本期摘要

本期不是一次单独的 Loop Controller v2 修补，而是完成了两轮连续的工作流重构。第一轮依据 Manager/Reviewer/Looping Plan 指南，将系统从“意图匹配后固定调用 Agent”改造成由 Manager 生成 Mission、Hypothesis、动态 Plan 和问题导向 Subtask 的框架，并让 Capability 与具体 Agent 解耦。第一轮已经建立了正确的业务语义，但真实运行显示其控制层仍偏宽松：Reviewer 结果仍混有 legacy 语义、`manager_assess` 容易把局部问题升级成重规划、Agent 级 `domain_outputs` 也不足以支撑精确审查。第二轮据 Loop Controller v2 指南补齐版本化 SubtaskResult、原生 ReviewResult、PASS/REVISE/BLOCKED/REPLAN 四路状态机、Revision/Replan 分离与循环预算，最终把第一轮的“能动态规划”收敛成“能稳定、可解释地动态闭环”。

---

## 3. 核心内容详解

### 3.1 架构与设计决策

- **两阶段目标与演进**：

  | 阶段 | 解决的核心问题 | 主要落地 | 仍暴露的缺口 |
  | :--- | :--- | :--- | :--- |
  | 第一轮：Manager / Reviewer / Looping Plan | 去掉 intent → 固定 Agent 流程，让系统先理解“为什么做、要解决什么问题” | Mission、动态 Plan、Subtask、Capability 注册、Hypothesis、Observation、结构化 Reviewer finding、终止条件 | 执行结果仍以 Agent 级输出为主；Review 与 Manager 决策的边界尚不够强；局部 finding 仍可能放大成全量重跑 |
  | 第二轮：Loop Controller v2 | 把上述语义对象变成真正可执行、可控的闭环协议 | SubtaskResult v2、ReviewResult v2、LoopControl、HITL、版本化结果、四路路由、Revision/Replan 预算 | Telemetry 与正式 Test A-F 按本期范围暂未实施 |

- **数据结构选型**：第一轮把 `mission`、`plan`、`hypotheses`、`observations`、`review` 纳入一等 State，并将 Plan 的 `capability` 与具体执行 Agent 分离。第二轮新增 `Subtask`、`SubtaskResult`、`ReviewFinding`、`ReviewResult`、`LoopControl` 等 V2 TypedDict；旧字段继续保留为兼容层，避免一次性推翻既有 Agent。

- **流程/状态机**：第一轮先把宏观流程改为“问题理解 → 动态计划 → 执行 → 审查 → 状态更新 → 重规划”。第二轮将其收紧为可路由的明确状态，解决此前“Reviewer 发现问题后下一步到底做什么”不够确定的问题。

  ```text
  用户
    -> Manager(Planning：Mission + Hypothesis + Plan)
    -> Subtask Router（Capability -> 当前可用 Agent）
    -> Agent / Tool
    -> Observation + SubtaskResult(source_version)
    -> Reviewer（只报告 finding）
       -> PASS    -> Document
       -> REVISE  -> Manager(Revision) -> 仅 Target Subtask
       -> BLOCKED -> Human Input -> 重新规范化 decision
       -> REPLAN  -> Manager(Replanning) -> 新 Hypothesis / Plan
  ```

- **为什么第一轮成果不足以直接完成目标**：第一轮重点是建立 Manager 的决策语义，因此采用了渐进迁移：旧 `domain_outputs`、旧 `review.status`、`manager_assess` 仍在运行路径中。这样降低了改造风险，却也保留了三个控制漏洞：无法保证每个 Subtask 都有可审查 Observation、无法凭 Agent 级输出只重做一个问题、也无法严格区分“缺少外部信息”“局部修订”“核心假设被推翻”。第二轮没有否定第一轮的 Mission/Plan 模型，而是为它补上执行契约、状态机和预算。

- **接口/模块边界约定**：Manager 决定 Mission、Plan、Revision 与 Replan；Reviewer 只发现问题并输出 `decision + findings`，不改写 Plan/Observation、不执行 Agent；Graph 执行适配层将公开 Agent 输出转成结构化结果；Document 只消费当前 Plan 范围内的有效结果。该边界是避免 Reviewer 变成第二个 Manager、避免 Agent 输出被删除重跑的关键。

- **信息缺口与安全边界**：第一轮已经将 Information Gap 作为 Plan/Hypothesis 的驱动因素；第二轮把它正式落实为 BLOCKED/Human-in-the-loop。BLOCKED 不自动转成 Replan，也不把“等待用户事实”计为一次失败的 Revision；用户补充的信息仅进入相关 finding 与目标任务的 revision context。

- **范围控制**：两轮均遵守第一份指南第 23 章的约束：没有新增大量 Agent、没有替换 LangGraph 或 Vector DB、没有引入 PostgreSQL、Web UI、复杂 Bayesian/MCTS 或长期记忆系统。本期优先解决工作流控制本身，而非扩张技术栈。

### 3.2 关键代码实现详解

**改动点 A：第一轮——动态 Plan 使用 Capability，而非硬编码 Agent（`agents/manager.py`、`registry.py`）**

- **意图**：先解决“Manager 根据意图直接写死 Agent 调用顺序”的问题，使 Plan 表达待解决的问题，执行器可以被替换。
- **关键代码**：

  ```python
  # ❌ 旧思路：按 intent 生成固定 Agent 列表
  # tasks = [Coach, Nutrition, Analyst]

  # ✅ 第一轮：Plan 只描述问题、能力、优先级与依赖
  {
      "id": "subtask_01",
      "goal": "评估当前竞技状态",
      "capability": "performance_analysis",
      "priority": 1,
      "depends_on": [],
      "status": "pending",
  }

  # Runtime 再从注册表解析 capability 到当前执行器
  executor = get_capability_executor(task.get("capability"))
  ```

**改动点 B：第二轮——Review 归一化阻止修复放大（`loop_contracts.py`）**

- **意图**：第一轮虽有结构化 finding，但缺少统一的决策协议，低价值问题或无 scope 问题仍可能驱动循环。V2 将 finding 的严重度、动作和作用域同时纳入校验。
- **关键代码**：

  ```python
  # ✅ 低价值问题不能进入循环；MEDIUM 不能直接升级为 Replan
  if severity in {"INFO", "LOW"}:
      action = "KEEP"
  elif severity == "MEDIUM" and action == "REPLAN":
      action = "REVISION"

  # ✅ 没有明确 Subtask scope 的循环动作不被授权
  if action in {"REVISION", "REPLAN", "BLOCKED"} and not ids:
      action = "KEEP"
  ```

**改动点 C：第二轮——BLOCKED 恢复与版本化结果（`graph.py`、`agents/document.py`）**

- **意图**：补上第一轮未严格定义的执行结果契约，避免非空 Agent 文本被误判为“完成”，也避免 Replan 后旧结果混入最终报告。
- **关键代码**：

  ```python
  # ✅ 执行器公开声明 BLOCKED 时写入结构化状态，而不是 bool(output) 即完成
  legacy_status, result_status, blocked_reason, uncertainties = (
      _executor_outcome(parsed_result, output)
  )

  # ✅ 用户补充后重新计算 canonical decision，不硬编码为 Revision
  revised_review = normalise_review_result(...)

  # ✅ Reporter 只投影当前 Plan 中已完成任务的最新版本
  if str(task.get("status", "")).strip().lower() != "completed":
      continue
  ```

---

## 4. 调试踩坑时间线

| 轮次 | 我的操作 / 触发条件 (Action) | 系统报错 / 观察结果 (Observation) | 最终修正决策 (Decision) |
| :--- | :--- | :--- | :--- |
| 1 | 按第一份指南审查 Manager 的任务分配方式 | 原流程仍以 intent 和 Agent 名称为中心，Plan 难以表达“当前要验证的业务问题” | 引入 Mission / Plan / Subtask 三层；Plan 用 capability、priority、depends_on 描述执行假设 |
| 2 | 为第一轮动态 Plan 接入领域 Agent | 同一能力可能承担多个 Subtask，而 `domain_outputs` 仍按 Agent 名称存储，无法天然区分每个问题的结果 | 保留 `domain_outputs` 兼容，同时引入 `current_subtask`、observations 和 capability 解析 |
| 3 | 依据第一轮 Reviewer finding 进行循环修复 | Reviewer 结果虽已结构化，但 legacy `passed/failed`、`manager_assess` 与 Agent 级输出仍可能将局部问题升级为粗粒度重跑 | 以第二份指南新增 `ReviewResult v2`、`LoopControl`、Manager 三模式和四路 Graph 路由 |
| 4 | 对显式返回 `{status: "BLOCKED"}` 的 Agent 输出做图级冒烟 | 非空 JSON 会被 `bool(output)` 判为完成，缺少外部信息不会进入 Human-in-the-loop | 执行器适配层读取公开 `status`、`blocked_reason`，将 BLOCKED 写入 SubtaskResult 与 Plan 状态 |
| 5 | 验证 BLOCKED 与 REPLAN finding 同时存在的恢复 | 用户补充信息后原实现固定进入 Revision，canonical decision 与实际路径可能不一致 | Human Input 节点转换已解决的 BLOCKED finding 后重新归一化；保留独立 REPLAN finding 的路由权 |
| 6 | 以 Replan 后的新 Plan 生成最终报告 | 累积 observations 会让被移除、跳过或已替换的旧任务进入 Document | Document 只读取当前 Plan 内 completed Subtask 的最新 `source_version`；历史结果保留但不参与投影 |
| 7 | 运行既有 `evaluation/test_looping_plan.py` | 两个旧断言仍要求 Reviewer 修改 Plan、Revision 删除 Agent output，与两轮指南的职责边界冲突 | 本期按“测试用例不实施”范围不修改测试；后续按 V2 Test A-F 重写测试语义 |

---

## 5. 下一步计划

- **[ ] 明确待办（Todo）**：
  1. 按 Loop Controller v2 指南的 Test A-F 重写并补齐自动化测试，替换依赖旧副作用的断言。
  2. 实施 Telemetry，记录 LLM/Reviewer/Agent 调用次数、Revision/Replan 次数、延迟与输入/输出 token，验证两阶段改造的成本收益。
  3. 若产品要求退出 CLI 后继续 BLOCKED 会话，引入持久化 checkpointer，并定义会话恢复、信息保留和清理策略。
- **❗️ 阻塞项 / 待确认疑点（Blockers）**：
  - 当前 `MemorySaver` 仅支持同一进程内恢复 BLOCKED；是否需要跨进程、跨命令恢复及其数据存储位置尚未确认。
  - 旧回归测试中有两项断言与新架构职责边界冲突；测试迁移的范围、验收口径及是否保留 legacy 兼容测试需在测试阶段确认。
  - 本期明确排除了 Telemetry 与测试用例实现，尚无真实线上调用数据来量化 Revision/Replan 的 token 与时延收益。

---

## 6. 开发随笔

> 这次的开发还是很有意思的，在解决一个不小的 bug 中，遭遇了一些有点搞笑的小问题。
> 
> 先讲一讲主要的部分吧，在上一轮修改中我们是完成了 RAG 的全链路改造，在那之后整个 Agent 系统的确更有条理了，新增了一个 reviewer 来让用户目的得以全程约束任务。但是仍有问题，就是整个系统，尤其是 Manager 的逻辑：之前提到过的“目的驱动”的运作方式在系统中还是不够贯彻，仍有很大的优化空间；而且在“拆分”这个环节规划得不够细致，将整个环节交给 LLM 来负责，输入输出其实都是规范化不足的自然语言 prompt 等等。这其实不利于整体效率的提高。除此之外，在其它部分 Codex 也检查到还有一些类似的“太 prompt”的设计。
> 
> 总的来说，在这个大作业的初期我一直在尽可能地*去硬编码化*，而这几轮改动其实我都在让这个系统“更加系统”，把各种职责从 Prompt 中的“智能行为”进一步固化成系统层面的“任务编排机制”。我觉得这样做是非常有必要的，毕竟如果把任务大段地交给 prompt和 LLM，整个系统就会变得更加一本正经的胡说八道了。（当然这点我在刚开始创项目的时候也有意识，不过那个时候从零搭起的系统实在是太“if-else”了）
> 
> 在本次改动的途中发现的另外一个问题就是 Agent Looping 相关的了。第一轮代码重建后，发现系统的 Looping 完全只是“能转”，基本上没有成本控制、范围约束的意识：一次提问-报告的运行就常常用时十多分钟、消耗*二十多万* token ，这对于我这个体量来说明显偏高了。所以这也是我在本次更新中的二阶段主要解决的问题，将整个系统的“敛散性”进行一些约束收敛。
>

> 这次开发途中还有几个小插曲（笑：
> 上午最后一节课下课之前我先构建好了二阶段的修改指南，开了 GPT-5.6 Sol Ultra 去做，结果就是非常不出意料地在一个多小时候到达五小时限额了。本来想着给 Codex 换成 deepseek 的 API（毕竟之前薅了学校 300 块全充里面了），但是在 cc switch 里面换了之后又觉得同一轮任务还是用同一个厂的模型比较好，于是就尝试换回去了。
> 
> 结果无论在 cc switch 里面怎么调，Codex 界面还是显示 `已通过 API 密钥登录` 
> 
> 按理来说，我直接重新在客户端重新登录就好了，但因为这个 GPT 账号是从AI贩子那里买的，直接重新登录的话很可能又要重新买个临时虚拟电话卡收验证码。所以最后我研究了一下就从 CLI 端重新登录，原因大概是客户端左下角的“退出登录”是一个用户主动终止会话的 UI 操作，会清除本地所有认证缓存并且主动向 OpenAI 服务器发起“吊销当前会话”的请求，之后重新登录就会被视为新设备了。而 CLI 端执行 `codex logout` 就不会有这样的风险，仅清除当前这台机器上 Codex CLI 的本地凭证缓存（因为我此时浏览器上还能登录买来的账号）
---
