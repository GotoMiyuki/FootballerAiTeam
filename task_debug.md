# FootballAI 重构与问题排查任务

禁止直接修改代码直到完成 Root Cause Analysis（RCA）。

## 你的角色

你是一名资深 AI Agent Framework 工程师。

你的目标不是机械修改代码，而是：

> **定位 FootballAI 多智能体系统中 Agent 路由与 Prompt 执行存在的架构问题，并完成可扩展的重构。**

禁止采用：

- 增加更多 if-else
- 增加更多 Prompt
- 增加更多关键词判断

这些方案只能暂时修复问题，而不能解决架构缺陷。

请优先考虑：

- 单一职责原则（Single Responsibility Principle）
- Prompt Engineering
- Multi-Agent Architecture
- LangGraph Workflow
- ReAct Planning

## 项目背景

目前 FootballAI 已基本完成 Multi-Agent、Memory、Tool 以及 LangGraph Workflow 等核心功能，整体架构已经具备较高完成度。但随着功能不断扩展，部分 Agent 已开始出现职责混乱、Prompt 互相干扰以及 Manager 调度能力不足等问题。

例如，当用户询问「转会传闻」「花边新闻」「媒体采访」「官方声明」等问题时，Career Agent 依然按照默认职业规划模板进行分析，而没有根据 Manager 下发的具体任务完成对应工作。这说明当前系统已经不仅仅存在 Prompt 问题，而是 Agent 架构、任务调度以及职责划分开始出现设计缺陷。

本次任务不是简单修复 Bug，而是从系统架构角度完成一次完整的 Root Cause Analysis（RCA），并在此基础上进行重构。

---

## 工作原则

整个过程中，请始终遵循以下原则。

不要看到 Bug 就立即修改代码，而是先理解系统整体架构，再定位真正原因。

不要依赖增加 Prompt、增加关键词判断或增加 if-else 的方式修复问题，这类修改只能暂时解决当前案例，无法保证系统未来的可维护性。

任何修改都应优先考虑以下几个方面：

- Multi-Agent 架构是否合理
- Agent 是否符合单一职责原则（Single Responsibility Principle）
- Manager 是否真正承担了 Planning 的职责
- Prompt 是否仍然保持高内聚、低耦合
- 新功能是否能够在不修改旧 Prompt 的情况下扩展

---

## 第一阶段：系统分析

首先不要修改任何代码。

请完整阅读项目源码，理解 LangGraph 的执行流程，明确每个 Agent 的职责、输入、输出以及相互之间的数据流。

重点分析：

- Manager 如何生成任务；
- Task 如何传递给各 Agent；
- Agent 如何组织 Prompt；
- Tool 如何被调用；
- Document 如何汇总各 Agent 输出；
- 最终结果如何返回给用户。

完成后，请生成一份系统架构分析文档，说明当前 Workflow 的执行流程，并指出各模块之间的数据流关系。

---

## 第二阶段：Root Cause Analysis

在完全理解系统之后，请开始定位当前存在的问题。

首先分析 Manager 是否真正完成了 Planning。

检查 Manager 输出的 sub_task 是否只是简单描述了一个主题，例如"分析职业规划"，还是已经明确告诉 Agent 应该执行什么动作，例如"请发布一份官方声明回应媒体关于转会的传闻"。

如果 Manager 输出仍然停留在主题层面，请分析为什么这种设计无法驱动 Agent 完成不同类型的任务。

随后分析各 Agent 的 Prompt。

重点检查 Career Agent 与 Document Agent 是否承担了多个完全不同的职责，例如职业规划、公关、商业分析、媒体声明等。如果存在这种情况，请分析 Prompt 为什么会逐渐失控，并解释为什么 LLM 会优先执行默认模板，而不是 Manager 临时下发的任务。

同时分析 Prompt 中"最高优先级"等描述是否真正能够保证模型遵循 Manager 指令。如果不能，请结合 Transformer 的注意力机制以及 Prompt Engineering 的最佳实践解释原因，而不是停留在经验层面。

整个分析过程请结合实际代码，而不是主观猜测。

---

## 第三阶段：提出重构方案

完成 Root Cause Analysis 后，请不要立即修改代码，而是设计至少两种可行的重构方案。

例如，可以设计一种基于 Mode Routing 的方案，由 Manager 输出标准化任务对象，每个 Agent 根据 mode 自动选择对应 Prompt。

也可以设计另一种方案，将职责过重的 Agent 拆分成多个职责单一的新 Agent，例如 Career Planner、Transfer Agent、PR Agent、Business Agent 等。

请分别分析两种方案的优缺点，包括复杂度、可维护性、Prompt 长度、扩展能力以及未来增加 Agent 的成本，并给出推荐方案。

---

## 第四阶段：开始实施

只有完成前三阶段之后，才允许开始修改代码。

修改过程中请保持小步迭代，每完成一个模块，都说明修改原因以及希望解决的问题，而不是一次性完成所有修改。

例如，可以先完成 Manager 的重构，再修改 Career Agent，最后调整 Document Agent。

每一步修改都应说明：

- 为什么这样修改；
- 修改解决了什么问题；
- 是否影响其他 Agent；
- 是否提高了系统扩展能力。

---

## 重构目标

最终系统应满足以下要求。

Manager 不再输出模糊的自然语言任务，而是输出标准化任务对象，例如：

```json
{
  "agent": "career",
  "mode": "public_relation",
  "goal": "发布官方声明回应媒体转会传闻",
  "constraints": [
    "正式语气",
    "150 字以内",
    "不得确认未公开信息"
  ]
}
```

Agent 不再依赖一个超长 Prompt 处理所有情况，而是根据 mode 自动加载对应 Prompt。

Career Agent 应只负责职业发展相关工作，而不是同时承担职业规划、公关、商业分析等多个角色。

Document Agent 也应根据不同 mode 输出不同类型文档，而不是通过一个 Prompt 同时生成报告、新闻、声明以及总结。

整个系统应尽量遵循单一职责原则，使新增功能时只需要增加新的 Prompt 或新的 Agent，而不是不断修改已有 Prompt。

---

## 最终交付

完成所有修改后，请输出一份完整的《Refactor Report》。

报告中应包含：

1. 当前系统存在的问题；
2. Root Cause Analysis；
3. 原设计为什么容易出现 Prompt 冲突；
4. 新架构如何解决这些问题；
5. 修改了哪些模块；
6. 新架构相比旧架构有哪些优势；
7. 后续还能如何继续扩展，例如增加 Medical Agent、Psychology Agent、Scout Agent 等，而无需修改现有系统。

整个报告应尽量从软件架构和 AI Agent Framework 的角度进行分析，而不是仅仅描述代码修改内容。