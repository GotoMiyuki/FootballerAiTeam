FootballAI Career Agent 架构升级任务书

根本原则：Root Cause Analysis；

1. 项目背景与现状痛点 (Project Background & Current Pain Points)
目前 FootballAI Career Agent 基于 LangGraph 框架实现，系统中已经定义并存在 Manager、Career、Coach、Nutrition、Analyst、Document 共 6 个 Agent。当前的业务流水线为线性固定流程：用户输入触发 Manager 进行分发，随后交由多个专业 Agent 并行或串行处理，最终由 Document Agent 进行结果拼接并输出 Final Report。

虽然该系统目前能够正常生成最终报告，但存在一个根本性的架构问题：整个系统本质上是一个硬编码的固定工作流（Fixed Workflow），而非具备自主决策能力的智能体工作流（Agentic Workflow）。当前的设计未能满足课程实验对高级 AI 智能体系统的核心要求，导致除 Agent 数量达标外，其余指标体现严重不足。

课程实验核心要求指标
Agent 数量：系统内至少包含 5 个独立的 Agent 角色（当前已满足）。

ReAct 推理：Agent 需具备真正的推理-行动循环，而非单次 LLM 补全。

记忆机制：必须同时具备长期记忆（跨会话）与短期记忆（会话内）。

工具调用：系统内至少实现 2 种以上的真实外部工具调用（拒绝 Prompt 模拟）。

动态协作与多轮决策：Agent 之间能够根据中间结果动态调整执行路径与决策。

本次任务的核心目标： 暂时不要修改实际代码，而是基于当前项目源码完成一次完整的软件架构评审（Review），并输出可落地的架构升级设计方案。

2. 第一阶段：系统整体架构排查 (Phase 1: Architecture Review)
请完整阅读并审阅当前项目的整个代码库，重点针对以下四个核心模块进行深度分析，找出设计漏洞与重构点：

1. Manager 决策流分析：分析当前 Manager 是否仅扮演了一次性任务规划（One-time Planning）的角色。重点排查：它是否具备根据下游 Agent 反馈的 Observation 进行二次重新规划（Replan）的能力？是否能够动态判断是否需要继续调用其他 Agent，或者直接决策结束当前流程？如果目前无法做到动态调度，请从控制流、状态管理等维度说明根本原因。

2. 各专业 Agent 自主性评估：针对 Career、Coach、Nutrition、Analyst、Document 这 5 个专业 Agent 进行逐一剖析。评估它们是否真正拥有自主决策能力。具体表现为：Agent 收到任务后，其内部执行链路是简单的线性结构（Input -> LLM -> Output），还是具备标准的 ReAct 循环（Thought -> Action -> Observation -> Thought -> Finish）？请给出具体的代码证据和分析结论。

3. Document 模块功能定位：分析 Document 节点的本质逻辑。它目前只是机械地进行文本复制、拼接与格式化输出，还是已经具备了高级审阅能力？例如：检查多 Agent 产出信息的缺失、主动向 Manager 或其他 Agent 提出补充请求、重新触发特定节点，或是自动识别并总结不同 Agent 之间的冲突信息？如果缺失这些高级能力，请明确指出代码瓶颈。

4. LangGraph 拓扑结构拓扑图：请梳理并画出当前 LangGraph 的有向图结构（可使用 Markdown Code Block 或文本图表示，如 Manager -> Career -> Document）。在此基础上明确指出：哪些边（edge）属于不可更改的固定流程？整个图中存在哪些无法实现循环（Loop）的死路？哪些节点由于设计缺陷无法承载重新规划（Replanning）的控制流？

3. 第二阶段：课程要求逐项对齐检查 (Phase 2: Compliance Checklist)
对比课程实验的具体考核项，逐一检查当前版本的实现度，并给出改进的技术方向：

(1) ReAct 推理机制：检查目前各节点有没有真正实现 Thought -> Action -> Observation -> Thought 的闭环推理。注意：坚决反对仅在 Prompt 内部声明格式（如在 System Prompt 中写 Thought:/Action:）的伪 ReAct。系统必须真正对接并执行外部实体（如 Tool、Memory Store、搜索引擎、计算器等）。如果没有实现，请说明在 LangGraph 架构下应如何重构节点逻辑。

(2) 长期记忆（Long-term Memory）：排查系统中是否存在如球员画像（Player Profile）、历史数据（History）、训练记录（Training）、历史报告（Previous Reports）以及转会生涯历史（Transfer History）等需要跨 Session 持久化的长期数据。如果目前付诸阙如，请从 Memory Store、本地 JSON、SQLite、Chroma 向量数据库或 LangGraph Native Memory 中选择一种最契合当前架构的方案，并陈述选择它的技术理由。

(3) 短期记忆（Short-term Memory/State）：深度剖析当前 LangGraph 的 State 结构。统计当前状态机中保存了哪些核心字段？这些字段是否能够在不同 Agent 之间安全、无损地共享？是否支持用户进行多轮追问与继续决策？请精准指出当前 State 设计在维持上下文方面的不足之处。

(4) 工具链（Tools）：统计并罗列当前系统内可用的一切真实工具（如 Search API、Calculator、Vector DB、Python REPL、Web Scraper 等）。如果目前工具链为空或仅为 Prompt 模拟，请建议至少加入两个真实可调用的工具，并强调必须实现真实的工程对接与异常处理。

(5) 动态交互能力（Human-in-the-loop）：分析当前请求是否全都是“单向单次触发后直接输出千字报告并结束”的模式。如果是，请指出哪些关键节点可以引入暂停（Interrupt）机制以接入用户交互。例如：当用户输入“我要加盟曼城”时，Career Agent 不应直接盲目生成报告，而应暂停流程并向用户追问“转会预算是多少？”、“球员目前的年龄？”、“是否已有经纪人？”等关键前置条件，待用户提供 Observation 后再继续推进图的执行。

4. 第三阶段：改造方案设计 (Phase 3: Tactical Architecture Redesign)
在不改动具体代码的前提下，请设计一套全新的 Agent 顶层架构方案。设计原则： 尽量保持已有的大部分 Agent 核心业务代码，严禁盲目推翻重写，注重向后兼容性。

新架构核心设计要点
Manager 的进化：详细说明 Manager 应如何演变为具备 Plan（任务规划） -> Replan（根据执行结果动态修正计划） -> Finish（判断整体目标达成并终止） 完整生命周期的控制中心。

Agent 自主权下放：说明各专业 Agent 如何转换为合规的 ReAct 节点，使其能够根据当前 State 自主决定是否调用指定 Tool，以及何时读取或写入 Long-term/Short-term Memory。

Document 节点的重构：讨论 Document Agent 是否应该继续保留，或者将其重构为 Reviewer（信息审查员） 与 Reporter（报告生成器） 的两步式协作流，以此确保最终产出报告的质量与完整性。

5. 第四阶段：改造实施计划与风险评估 (Phase 4: Upgrade Roadmap)
最后 presidential 输出一份完整的 Architecture Upgrade Plan（架构升级路线图）。该路线图必须严格按照实现优先级进行排序（建议采用 P0/P1/P2 划分法，例如：P0 实现 Memory 基础，P1 实现 2 种 Tool，P2 落地 ReAct 推理，P3 升级 Manager 决策流与 LangGraph 动态边）。

对于路线图中的每一项任务，必须包含且不限于以下精细化的 spec 说明：

涉及文件：明确指出需要新建或修改的具体源码路径及文件名。

预计修改量：预估代码行数（LOC）或改动模块的体量（如：轻度修改、重构、全新编写）。

影响范围：说明该改动会波及哪些下游节点或全局 State。

潜在风险：评估可能引入的逻辑死循环、LLM Token 消耗剧增、状态丢失或死锁等工程风险，并给出预防对策。

⚠️ 重要提示： 本阶段严禁直接修改或输出任何具体的业务重构代码。请只输出上述方案与路线图，并静待人工确认与评审。