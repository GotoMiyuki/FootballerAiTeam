# B：执行、协作与审查正确性——已完成开发汇总

截至 2026-10-02，已完成 B1.1–B1.2、B2.1–B2.3、B3.1–B3.2。本文将 B1、B2、B3 合为一条从有效输入到发布交付的流程；B4 全面状态收敛仍待实施。返回 [汇总索引](./README.md)。

## 1. 问题与完整流程

原流程可能在模型失败后给出固定业务答案、把审查异常视为 PASS、把最后一个工具消息当结论，或让下游使用未传入/已过期的上游结果。现在执行结果、审查完成、正文发布和依赖有效性分别校验，任一必需条件失败都会形成可解释的真实状态。

完整路径为：冻结 A 的输入 → 校验 Plan → 按子任务构建最小依赖上下文 → 执行有限工具循环 → 校验专业结果 → 接受并赋予来源版本/指纹 → 审查当前版本 → 明确批准报告生成 → 检查正文并记录 hash → 服务再次核验后发布。局部修订或重规划会先使相关旧结果失效，再进入相同接受与发布路径。

| 子阶段 | 交付 | 主要实现 |
| --- | --- | --- |
| B1.1 | 执行、审查可用性、交付状态端到端一致 | [执行契约](../../execution_contracts.py)、[Loop 契约](../../loop_contracts.py)、Graph、后端映射与 UI |
| B1.2 | 专业 Agent、Manager、Document 失败不补固定业务答案 | [Agent 实现](../../agents/base.py)、服务与 CLI |
| B2.1 | 显式工具循环退出原因和结构化工具错误 | [ReAct 基类](../../agents/base.py)、[工具错误](../../tools/errors.py) |
| B2.2 | 专业 schema、Plan 与 Reviewer 分别校验 | [输出校验](../../output_validation.py)、Graph 接受入口 |
| B2.3 | 专业输入审查与正文确定性门槛，发布绑定版本/hash | [正文校验](../../report_validation.py)、[Document](../../agents/document.py)、服务与下载入口 |
| B3.1 | 首次与修订都消费有效依赖、证据和版本 | [执行上下文](../../execution_context.py)、[注册中心](../../registry.py)、提示词 |
| B3.2 | 指纹复用、下游失效、重规划保留无关有效结果 | [结果有效性](../../result_validity.py)、[Manager](../../agents/manager.py)、Graph 与 UI |

## 2. B1：执行、审查与交付分别表达

| 层次 | 当前状态语义 |
| --- | --- |
| 执行 | `SUCCEEDED/NO_RESULT/FAILED/BLOCKED` 分别代表成功、无有效结果、技术失败和缺必需外部输入 |
| 子任务 | `COMPLETED` 仅表示专业输出已通过接受校验；`FAILED` 不自动转为业务修订；`INVALIDATED` 表示旧结果不再适用于当前交付 |
| 专业结果 | 保存 `validated/validity/source_version/input_fingerprint/input_material` 及球员/依赖版本引用 |
| 审查 | `NOT_RUN/COMPLETED/UNAVAILABLE`；未完成时 decision 为空，不认证 reviewed_subtasks |
| 交付 | `NOT_GENERATED/PUBLISHABLE`；正文 hash、检查范围与被审查结果版本独立记录 |

全部计划子任务默认必需。有限观察可作为 PARTIAL 数据保留，但没有授权其替代完整依赖或完成 Mission，也未增加 Mission.PARTIAL。技术故障终止为失败，不要求用户补造数据来修复；只有缺少必需外部信息才进入 BLOCKED。

失败路径可以保留可核对的代码计算、工具观察和专业摘要，仍不能补出完整训练、营养或职业方案。Reviewer 不可用和旧记录缺审查元数据都保留未验证事实，不转换为普通 PASS。Document 失败不撤掉已经有效的分析，但不宣称交付了报告。

新增公开事件包括 `subtask.failed/subtask.invalidated/agent.failed/review.unavailable/review.invalidated`。API/SSE/任务界面、审查面板和下载入口消费相同语义；公开 DTO 不暴露原始提示、工具参数或异常堆栈。

## 3. B2：输出接受与有限工具循环

ReAct 返回 `FINAL/BUDGET_EXHAUSTED/MODEL_FAILED/EMPTY_OUTPUT/INVALID_CONTENT/TOOL_ERROR`，而不是任意最后一条消息。耗尽预算时 ToolMessage 仍只是观察，不是模型最终结论。工具调用保留 call id、SUCCESS/ERROR、required、error_type 与有效观察；未知工具、非法参数和执行异常有结构化结果。

SearchTool 是可选补充检索，其失败可以作为限制保留；其他当前必需工具失败不能靠随后一段模型文字冒充任务成立。失败、重试与预算都受有界执行约束，但这不是 G 的整任务超时或取消实现。

专业输出校验五种 capability 的必需字段、类型和当前可解释的业务约束，不以“JSON 能解析”“文本非空”或模型自报成功作为接受条件。数值排除 bool、NaN 和 Infinity；当前结构/范围校验不替代科学规则校准。

Plan 校验任务身份、capability、目标、依赖存在/无环与预算，不接受规划器自报 completed。Reviewer 必须覆盖当前必需结果；严重 finding 不能通过删掉审查范围变成 PASS；REPLAN 要有核心证据，INFO/LOW 不触发无意义循环。

### 正文发布门槛

当前策略是 **专业输入审查 + 正文确定性检查**。正文须非空、模式与任务要求匹配，有依据和限制；支持的带标签球员数值及引用 URL 经过校验，并保存正文 hash。数值标签覆盖年龄、身高、体重、综合评分等已支持内容，没有声明验证所有散文或引用语义。

发布元数据明确 `semantic_review=NOT_PERFORMED`。生成前的人类批准也不代表生成后的全文已审核。正文改变会使原 hash 无效，专业支撑结果改变会撤销旧审查与交付。`MissionService.complete()` 再次复核门槛，报告读取/下载和前端按钮还要求当前 COMPLETED/PUBLISHABLE。

## 4. B3：首次执行与修订的统一依赖上下文

上下文按 Mission/subtask/Plan 身份构建，包含 capability/goal、全局与局部约束、实际使用假设、范围内确认输入、[A 的固定身份/快照/版本](./A_球员数据与事实边界.md)，以及显式依赖的有效 payload、source_version、证据和限制。首次调用与 Revision 共用入口，材料实际进入提示词。

依赖按 capability 取最小完整字段，不把全部观察历史或某 Agent 的最后一个字符串当输入。超过 64,000 字符明确失败，不简单截断关键证据。内容使用副本，上游文本不能增加工具权限或控制 Graph 路由。

接受依赖时核验祖先结果的当前指纹；即使直接依赖还带 completed 标签，只要上游输入已变就拒绝消费。损坏依赖循环同样拒绝。结果身份是子任务身份，同一 Agent 的不同任务不会混成一份有效成果。

### 指纹与复用

指纹包含结果契约版本、capability/goal/purpose、Mission objective、相关约束、实际使用假设、确认输入、球员快照/状态版本、依赖集合和结果 source_version。[C1.2](./C_C1_会话恢复与任务延续.md) 后续接入的显式历史也进入实际输入与指纹。Plan 展示版本、优先级、语气或读取时刻不单独造成伪失效。

目前使用整份球员版本做保守失效，尚无字段级影响分析。重规划先校验新 Plan，再按依赖拓扑认证复用，不只比较 capability 与 goal。

| 变化 | 当前处理 |
| --- | --- |
| 局部修订目标结果 | 目标及传递下游立即失效，独立有效任务保留 |
| 上游成果新版本或相关输入变化 | 下游不能继续沿用旧 completed，需要重新接受 |
| 删除子任务 | 移到失效历史，不残留为当前完成成果 |
| 新任务或同 id 的新语义 | 不能继承旧 Agent 输出 |
| 支撑结果失效 | 清除 PASS、reviewed_data、Document 聚合、正文、摘要与可发布状态 |
| 修订/规划预算耗尽 | 按真实失败结束，不保留虚假 completed |

Reducer 使用明确有效性和删除标识，不能靠空字典假定清空旧键。前端可显示失效历史与保留的有效专业摘要，但不展示旧报告下载为当前交付。

## 5. 验收证据与兼容边界

2026-10-01 A/B 联合交付的完整结果为 55 项 Python 测试、类型检查、6 项前端事件测试和 7 个 Edge 浏览器场景通过。覆盖正常与非法输出、空/异常模型响应、工具耗尽、Plan/Reviewer 拒绝、正文门槛、首次/修订依赖、链式失效与独立任务保留、预算终止、删除/新增任务及范围内确认输入。

真实图失败场景核对 API、持久化快照和事件中没有 `report.created/mission.completed` 成功残留。浏览器包含正常完成、局部修订、重规划、缺输入续跑，以及失败、已失效和旧记录场景；使用临时样本、确定性节点和 API 替身，不访问真实模型。最新整库复核见 [整理验收](./整理验收_2026-10-02.md)，历史数量不累计。

旧审查、报告或结果缺少新元数据时显示未验证，缺指纹不能自动复用；未实施全面历史迁移。新旧 Plan/Review/Hypothesis 表达仍通过薄适配共存，B4 尚未收敛。事实写入边界由 A 负责，C/G 通过应用接口接入，不重写 B 的调度语义。

本阶段没有完成真实模型建议质量、全文语义审查、领域阈值校准或真实游戏闭环。原始验收与详细命令见 [A1–B3 原始实施记录](../devolopment_logs/archive/2026-10-roadmap/2026-10-01_A1-B3_实施与验收.md)，后续计划见 [B 任务书](../B方向_执行协作与审查正确性_Codex任务书_2026-10-01.md)。
