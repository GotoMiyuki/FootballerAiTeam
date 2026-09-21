# LoopController V2：Telemetry 与回归测试记录

日期：2026-09-21

## 范围

落实《FootballerAiTeam_LoopController_v2_修改指南》的遥测要求及第 24 章 Test A–F。

- 状态中的 `telemetry` 汇总以下字段：`llm_call_count`、`manager_call_count`、`reviewer_call_count`、`agent_call_count`、`review_count`、`revision_count`、`replan_count`、`latency_ms`、`input_tokens`、`output_tokens`。
- 事件明细只保存节点名、角色、耗时和模型返回的 token 元数据；不保存提示词或模型回复正文。
- LLM 调用由 `BaseAgent` 的统一包装器记录；图执行边界负责汇总节点级统计，因此静态检查、BLOCKED 和真实模型调用均可被区分。

## 执行命令

```powershell
& .\venv\Scripts\python.exe -m unittest evaluation.test_looping_plan -v
& .\venv\Scripts\python.exe -m compileall -q agents graph.py utils evaluation
```

## 结果

`unittest`：7 项通过，0 项失败（其中包含指南 Test A–F 与 1 项基础数据结构回归）。语法编译检查通过。

| 场景 | 验收结果 |
| --- | --- |
| A：正常任务 | `PASS → Document`；Reviewer=1、Revision=0、Replan=0，并验证遥测字段。 |
| B：单点局部错误 | 仅重跑 `subtask_03`；其他已完成任务与结果版本保留。 |
| C：跨任务冲突 | 仅重跑最小集合 `subtask_03`、`subtask_04`。 |
| D：缺少用户信息 | 首次执行停止在 `human_input`；补充信息前没有自动重试，补充后才局部修订。 |
| E：核心假设被证伪 | `Reviewer → Replan → Manager Replanning → 新 Hypothesis/Plan`；验证 replan 次数及输入/输出 token 汇总。 |
| F：低价值 finding | LOW finding 被规范为 `KEEP/PASS`，不会进入 Revision 循环。 |

## 说明

测试使用确定性节点替身，不访问外部模型。Test E 的模型替身提供固定 token usage，以验证统计逻辑；生产运行会读取实际 LangChain/provider 响应中的 `usage_metadata` 或 `response_metadata.token_usage`。
