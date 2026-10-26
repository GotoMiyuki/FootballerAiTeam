# C2.2：记忆检索与固定输入

后续更新（2026-10-25）：[E2.2软件闭环](E_E2.2_反馈驱动关联任务.md)已接通建议/评估直接关联字段及固定反馈；[M3实机验收](../roadmap/M3_实机验收与接续.md)尚未通过。下文保留C2.2交付时边界。

日期：2026-10-25。承接[C2.1](C_C2.1_生涯记忆与来源.md)、[E2.1](E_E2.1_后续观察与比较.md)及[C方向任务书7.2](../guides/C方向_会话恢复与生涯记忆_Codex任务书_2026-10-01.md#72-c22检索与上下文接入)。本轮交付可复核的相关性选择、任务固定集合、真实Manager/专业提示输入及B3指纹薄适配。E2.2的建议/评估直接发起关联任务与真实连续生涯验收留待下一项。

## 1. 选择规则与来源边界

`career-memory-selection-v1`先按完整career_id/branch_id/player_id读取，再核验有效状态、来源对象与版本、数据模式、声明时间范围，最后按关键词、capability、来源任务关联和记录时间排序。不先截取最近N条。首版是确定性结构/关键词检索，没有新增向量库或相关性模型。

用户明确目标/偏好中，候选、被更正、撤回和尚未生效记录排除。没有声明时间限制的明确偏好视为当前记录，直到更正/撤回；真实时间按UTC时点，游戏日/月/周按A当前已应用来源的游戏日期高水位判断。无法核验的描述窗口、缺少游戏时间或不支持的时间域排除并计数，不用现实经过时间代替游戏推进。

E建议/选择/执行/Assessment沿E事件与原对象读取并核验。未手动同步的E事件可产生只读临时投影，提交时保存实际选取内容，不写入第二套行动状态。只有仍适用的建议可作为当前建议；待重评建议本身排除，其未被更正的历史选择/用户自报反馈可带限制引用，不能变成当前执行许可。Assessment必须是当前有效比较版本，来源观察变化后旧版排除；C同步支持assessment.created/superseded及同id不同revision，不改E评估算法或旧数值。

同context的fixture来源任务记忆不能进入actual数据模式。E和C原对象、A事实及报告均不因检索而修改。资料内的指令作为引用数据，不能扩大工具或调度权限；没有新增Agent记忆搜索工具或扩大私有短期缓存。

## 2. 固定输入、兼容与指纹

新任务与关联重评在提交时将A快照和`career_memory_context`一起写入原mission_inputs事务，公开Mission增加memory_reference摘要。集合记录选择规则版本、选择时点、查询、context、快照id、选取的memory_id/revision、内容摘要、来源对象/版本/摘要、时间、短投影、限制与省略原因。实际A支持观察读取时，一次读取的观察包必须与提交快照一致；变化则拒绝混合版本，提示重新提交。

最多8条、每条序列化累计最多12000字符；正文超过1000字符明确标为截取，同时保留完整源记录摘要。预算在相关性选择后应用。未确认/过期/无关/超预算/来源不可核验等只返回原因和数量，不泄漏其他context正文。相关资料被预算或核验排除时，明确集合不代表完整历史。

执行、暂停恢复、历史查看与原报告解释读取原集合，不查询最新记忆补齐。新评估重新选择并保存当时版本；后续更正不改在途任务。集合、条目、公开引用和整个私有输入均核验摘要；新任务缺集合或checkpoint不一致时拒绝恢复。旧任务无新字段保留LEGACY_NONE并明确无固定记忆记录，不反推已使用记忆。

Manager的Mission/Hypothesis/Plan及Replan提示使用同一固定投影；专业Agent经现有execution_context接收其中与自身capability或目标相关的子集。B3的input_material仅增加实际消费项的id/revision/content_hash，既有依赖失效规则继续适用。无关记录、省略计数、其他专业域记录的变化不改变该子任务指纹。未升级专业输出CONTRACT_VERSION；记忆选择独立以policy_version标识。

`use_career_memory`默认true，false明确保存DISABLED空集合并跳过检索。关联创建仍沿用request_id防重；默认true与旧请求体hash保持兼容，false是不同请求内容，丢响应后原样重试返回原任务/原集合，不重新选取。

## 3. API与页面

```text
GET  /api/missions/{mission_id}/memory-input
POST /api/missions/{mission_id}/career-memory/retrieval-preview  {content}
```

GET只读原固定集合；旧任务返回空对象，损坏或引用不一致409。预览只读当前同context来源，不创建任务、不同步写记忆；正式提交会再次核验与选择，预览不承诺提前锁定来源。已有新任务/关联创建DTO增加use_career_memory，继续拒绝尚未接通的recommendation_id/assessment_id直接关联字段。

页面“本次固定记忆输入”展示实际引用、版本、来源任务、时间、摘要和限制，与可更正的“生涯记忆”面板区分。原报告解释与新评估时间视角明确。新任务/重评可关闭引用，重评有当前来源预览；编辑需求后不显示旧预览。原样网络重试、刷新恢复和编辑生成新request_id沿用原契约。

## 4. 验证与限制

[C2.2专项](../../evaluation/test_memory_retrieval.py)使用临时球员根、真实SQLite/API/E存储与离线模型替身，验证相关性先于时间、候选/范围/数据模式/context隔离、撤回与预算、E漏同步来源、Assessment修订/过期、固定集合与原解释、实际Manager/Coach/Replan提示、专业指纹与成果失效、损坏checkpoint拒绝恢复、关联防重和关闭引用。浏览器新增隔离demo API场景覆盖预览、提交后更正与刷新、关闭引用后的新任务。

完整后端127项通过（含新增9项C2.2）；最后补充固定集合的公开省略项/快照身份核验后，C2.2专项9项再次通过。前端单元7项、全部30个浏览器场景、TypeScript/生产构建、compileall及diff空白检查通过。最后补充固定比较来源的页面展示和浏览器重复运行唯一标识后，新增浏览器场景再次通过。文档检查30份、545个本地链接、0错误。本轮修改的前端文件格式检查通过；全仓格式检查仍报告7个既有未改动文件的格式差异，未扩大改动范围。

重跑命令为 `venv/Scripts/python.exe -m unittest discover -s evaluation -p "test_*.py" -q`、`venv/Scripts/python.exe -m unittest evaluation.test_memory_retrieval -q`，前端在frontend目录运行 `npm test`、`npm run build`、`npx playwright test`。本机TEMP/TMP使用工作区内.web-test-data/c22/temp，浏览器使用FAIT_TEST_BROWSER=chrome；演示服务使用独立.web-test-data/c22/browser-tasks任务根。跨进程CLI完整回归较慢，完整127项运行339秒；没有放宽业务断言或既有等待期限。

所有测试不调用实际模型、不写游戏存档，不将合成观察或demo记忆流程计为真实长期生涯闭环。既有memory/player.json与memory/sessions.json的SHA256保持本轮开始时值不变；前序E1.2/C2.1/E2.1及其他未提交工作继续保留。

下一项为E2.2：消费C现有后续任务入口，建立明确建议/评估来源关系与玩家触发的闭环入口，再按真实有来源观察进行连续生涯验收。
