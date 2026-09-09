# FootballAI 系统架构重构任务

## 项目背景

FootballAI 已基本完成 Multi-Agent、LangGraph Workflow、Memory、Tool Calling 等核心功能，能够实现足球运动员职业规划、训练建议、营养分析、比赛分析等功能。从功能实现角度来看，项目已经具备较高完成度。

然而，随着功能不断扩展，系统开始暴露出新的架构问题。这些问题已经不仅仅来源于 Prompt，而是来源于整个 Workflow 的设计。

目前，Manager 能够较准确地理解用户真正的需求，并决定应该调用哪些 Agent，但这种理解并没有随着 Workflow 一直保留下去。各 Agent 更多是在执行自己的局部任务，而最终 Document Agent 也只是简单整合各 Agent 的输出，而不是围绕用户真正的需求生成最终结果。

例如，当用户询问"请帮我回应媒体关于加盟皇马的传闻"时，Manager 实际已经理解这是一个公关任务，但 Career Agent 依然倾向于输出职业发展分析，Document 最终也只是整理这些分析，而没有真正完成"回应媒体"这一目标。这说明整个系统在执行过程中发生了 Intent（用户真实意图）的丢失。

因此，本次工作的目标不是继续增加 Prompt，也不是继续增加 Agent，而是重新设计整个系统的 Intent Flow，使用户意图能够贯穿整个 Workflow，而不是在中间逐渐被 Task 所替代。

---

## 工作原则

整个分析和重构过程中，请优先关注系统架构，而不是单个 Prompt。

不要通过增加 if-else、关键词判断或继续扩充 Prompt 来修复当前问题，因为这些方式只能解决当前案例，而不能保证未来系统继续扩展时仍然稳定。

请始终从以下几个角度思考：

- Manager 是否真正承担了系统的大脑，而不仅仅是调度器；
- Agent 是否真正具有清晰的领域职责，而不是不断累积功能；
- Document 是否真正完成了用户目标，而不仅仅负责生成 Markdown；
- Intent 是否能够贯穿整个 Workflow，而不是只存在于 Manager。

所有修改都应遵循高内聚、低耦合原则，使未来新增能力时尽量不需要修改已有 Prompt 或已有 Agent。

---

## 第一阶段：理解现有系统

请不要立即修改代码，而是完整阅读整个项目，理解当前 LangGraph 的执行流程以及各 Agent 之间的数据流。

重点分析 Manager 如何理解用户需求、如何生成任务、任务如何传递给各 Agent、各 Agent 如何组织 Prompt、Tool 如何参与执行，以及 Document 最终如何生成结果。

请绘制当前系统的执行流程，并分析整个 Workflow 中哪些信息属于"用户真实需求"，哪些信息只是"执行过程中的局部任务"。

完成后，请生成当前架构分析文档，并说明 Intent 在整个流程中的传递路径。

---

## 第二阶段：Root Cause Analysis

在充分理解系统之后，请开始分析问题的真正来源，而不是直接修改代码。

首先分析 Manager 当前是否真正承担了全局控制中心的职责。Manager 是否只负责"选择调用哪个 Agent"，还是应该负责维护整个任务生命周期，包括用户目标、输出形式、成功标准以及整体执行方向。

随后分析各 Agent 是否真正理解 Manager 的意图。重点关注 Career、Skill Coach、Nutrition、Analyst 与 Document，判断它们是在完成 Manager 的目标，还是仅仅完成自己的默认分析模板。

请重点分析 Document Agent 的职责。当前 Document 更像一个信息整合器，而不是任务完成者。请思考它是否应该重新读取 Manager 最初的目标，并围绕这个目标组织所有 Agent 的输出，而不是简单按照输入顺序进行汇总。

整个分析过程中，请结合实际代码进行说明，不要停留在经验判断或 Prompt 猜测。

---

## 第三阶段：重新设计 Intent Flow

在完成 Root Cause Analysis 后，请重新设计整个系统的信息流，而不是继续修改 Prompt。

建议思考是否建立统一的 Mission 对象，由 Manager 在 Workflow 开始时创建，并在整个生命周期内持续维护。

Mission 可以包含用户目标、整体 Intent、预期输出形式、成功标准以及上下文信息。所有 Agent 在执行局部任务时都能够读取同一个 Mission，而不是只读取属于自己的局部 Task。Document 在最终生成结果时，也应重新读取 Mission，并以 Mission 为最高目标组织所有内容。

请重点比较当前 Task Flow 与新的 Mission Flow 的区别，并分析新的设计在扩展性、可维护性以及 Prompt 稳定性方面能够带来的改善。

---

## 第四阶段：重新审视 Agent 职责

本次重构尽量保持现有 Agent 数量，不建议继续拆分新的 Agent，而是重新整理各 Agent 的领域边界。

Career 应聚焦长期职业发展，包括职业规划、转会建议、合同分析、联赛选择以及职业价值等问题；Skill Coach 应聚焦竞技能力，包括技术、战术、训练以及球队适配性分析；Nutrition 负责运动营养与恢复；Analyst 负责比赛与数据分析；Document 则负责所有最终面向用户的信息表达，包括综合报告、官方声明、采访回答、新闻稿以及社交媒体内容。

这里定义的是领域（Domain），而不是固定功能列表。Prompt 不应该通过列举大量允许或禁止的行为来约束 Agent，而应该描述 Agent 所负责的问题领域，使 Agent 保持职责边界清晰，同时保留一定的推理自由度。

---

## 第五阶段：Prompt 与 Workflow 优化

Prompt 不应继续发展成一个包含所有情况的超长模板，而应采用动态加载的方式。

Manager 应向各 Agent 下发标准化任务，其中包含当前 Mission、所属领域以及输出类型。Agent 根据这些信息动态选择对应 Prompt，而不是依靠一个 Prompt 覆盖所有情况。

同时，请重点考虑 Manager 与 Document 的连接方式。

Manager 不应仅仅生成一次 Task 后退出，而应作为整个系统的 Intent Holder 持续存在。Document 在最终生成结果之前，应重新获取 Manager 保存的 Mission，以确保最终输出始终围绕最初的用户目标，而不是围绕中间 Agent 最擅长输出的内容。

整个 Workflow 应从以 Task 为中心逐步转变为以 Mission 为中心。

---

## 第六阶段：实施重构

在完成上述分析之后，再开始修改代码。

请采用渐进式重构，每次只修改一个模块，并说明修改原因、解决的问题以及对整个架构带来的改善。

每完成一个阶段，请同步更新重构文档，记录当前设计与原设计之间的区别，以及为什么新的设计更加符合 Multi-Agent 系统的设计原则。

---

## 最终目标

最终系统应形成一条完整且连续的 Intent Flow。

Manager 负责理解用户真正的目标，并创建整个 Workflow 的 Mission；各 Domain Agent 围绕同一个 Mission 完成自己的局部工作，而不是各自完成互不关联的分析；Document 在最终输出前重新读取 Mission，并结合所有 Agent 的结果完成真正符合用户需求的最终回答。

整个系统最终应以 Mission 为核心，而不是以 Prompt 或 Task 为核心。新增能力时，应尽可能通过增加新的 Prompt、Tool 或领域能力实现，而不需要修改已有 Agent 的职责边界，从而保证整个系统具有良好的可扩展性与长期可维护性。