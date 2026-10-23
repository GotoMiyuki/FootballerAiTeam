# PES 2021 BAL 原生采集与受控导入（D1/D2）

2026-10-23 D2：已完成指定构建、专用测试生涯的[映射与受控导入](../docs/completed/D_D2_映射与受控导入.md)。真实原生样本向独立 actual 仓库导入6条观察（25项能力、2条个人比赛、3条生涯事件），复读6条均为 DUPLICATE；142项适配器与13项A导入/仓库测试通过。采用明确审阅的本地绑定和事件身份，D2.1/D2.2 在此范围验收；[快速跳过完整动作统计](../docs/D2_PES2021_快速跳过动作统计复核_2026-10-23.md)仍未取得。

受信入口 `python -m game_adapters.pes2021_import` 提供 bind / preview / commit；写入采用专用 actual 根及明确单写入进程。底层 reader 的 `d2_ready=false` 仍表示原始预览不自行授权写入，正式提交由 D2 服务验证。

以下保留之前轮次的接口与历史结论。

2026-10-22 收尾：[D1 独立采集验收](../docs/completed/D_D1_独立采集验证.md)已完成 D1.1/D1.2，范围为指定构建与专用测试生涯，使用明确审阅的本地绑定和事件清单。25项能力、五场原生结算、重复读取及拒绝检查通过，完整适配器121项测试通过。D2 映射/导入尚未实现；累计范围、任意回滚和其他未认证字段继续保持未知。

当前统一样本入口：

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_native_package game_adapters/raw/real_test/pes2021-bal-20261002-d1 --summary
```

该入口读取原生字节并核对审阅关联，输出只读预览；通用实验 reader 的保守标志不自动升级。以下为各底层接口及逐轮历史，其中“D1未完成”和测试数量均为对应轮次状态。

架构与后续研究入口：[数据流架构与采集边界](../docs/D1_PES2021_数据流架构与采集边界.md)。2026-10-22 已从生产代码拆分正常比赛与摘要模拟路径：四场既有原生数据共88条个人记录，全部符合胜/平/负评分表及进球/助攻加分规则；同时追到最近比赛对象的实际重置体。后续暂停重复寻找普通Quick Skip完整动作表，按明确函数/结构做定点验证。局部索引覆盖46个函数、73个代码片段；不是全程序闭图，D1/D2门槛不变。本轮没有新增比赛或生产适配器改动。

已实现本机指定构建的运行时个人详细统计只读预览：[运行时采集与真实验证](../docs/D1_PES2021_运行时个人详细统计采集_2026-10-07.md)。正常比赛进行中及终场可直接取得个人计数、盘带距离和平均速度，无需打开个人统计页；取得进球、助攻和非满成功率正样本。2026-10-08 三次真实模拟对照表明：首发主动跳过仍写入存档分钟/评分候选与累计，未入选结算没有目标单场条目；跳过的完整动作统计尚未取得，详见 [真实模拟与对象可用性](../docs/D1_PES2021_自动留存与赛前身份线索_2026-10-08.md#6-解锁后的三次真实模拟对照round6)。此前 [底层详细统计调研](../docs/D1_PES2021_单场详细统计底层采集调研_2026-10-06.md) 保留网络/源码线索。

2026-10-09：[原生写入与缓存归属](../docs/D1_PES2021_自动留存与赛前身份线索_2026-10-08.md#round7) 已分类 typed 24B `+12` 为原生门将扑救，58 条真实记录逐条对照一致并含非零样本。`recent_cache_binding` 按缓存自己的序号、日期、球队头核对本地赛程；目标还需匹配侧别和 packed 引用，全局搜索的其他命中保持未分类。T8 缓存归属 9/23 比赛；T9/T10/T11 的指定缓存头和80槽 ID 均为空。这个锚点仍不是持久赛事 ID，缓存缺失不代表未出场。

2026-10-22：[round8 真实运行验证](../docs/D1_PES2021_自动留存与赛前身份线索_2026-10-08.md#round8) 补齐正常比赛自动留存正向验收：834 次轮询、0 次读错、8 份 checkpoint，详细对象释放后仍保存 61 条终场记录；评分 5.0 与随后自动出现的评分列表一致，七段指纹均通过真实加载核对。61 条原生缓存与详细表逐条一致，正常保存后整个缓存字节一致。新增 `pes2021_runtime_career`，从独立生涯根读取请求赛程和缓存；四次模拟在**保存前**取得比分，首发跳过目标为90分钟/6.0，替补跳过及未入选的目标行缺失保持 null。完整适配器 **107 项测试通过**。实际中途换人、累计跨季范围、结算检测与持久生涯/事件身份仍待认证，D1 未完成，D2 关闭。

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_runtime_stats --pid <当前PES进程ID> --native-player-id 2147483834 --output game_adapters/raw/real_test/<已有目录>/new-runtime.json
.\venv\Scripts\python.exe -m unittest game_adapters.test_pes2021_runtime_stats -v
```

运行时命令是独立实验接口：只申请进程查询/读取权限，核对准确 exe 路径和七段 loaded-code SHA256，固定指针链有读取预算并检查前后对象/阵容稳定；不匹配会拒绝采集。当前输出 34 类原生计数、两类连续值、分钟、评分/初始前11人标志候选和派生比例；无分母的成功率及原生零评分为 null。`raw_record_blocks` 保留双方80槽完整680000B字节，以 base64+SHA256 编码，未知及空槽字段可以后续重解析；旧报告未补造字节。原生 Dribble 不直接命名为过人，当前阵容顺序不等于赛后菜单排序。记录存在、零分钟或非零速度均不能单独认证出场；目标 ID 重复不自动选行。比赛计数不是原子帧，生涯分支/事件身份与生命周期仍须验证，所有 ready 门槛为 false。对象缺失返回 unavailable；未知版本或读取失败返回 rejected；新 JSON 只写隔离真实测试根，无 A 导入。parser 的保守验证标志不会自动认证任意采集，本轮独立验收单独保存在 round8 证据中。

新增 [自动留存与赛前身份线索](../docs/D1_PES2021_自动留存与赛前身份线索_2026-10-08.md)：`pes2021_runtime_watch` 在有限时长内轮询，周期留存，并在对象不可用、切换、停止或读取失败时保留最后有效数据。输出新目录中的 checkpoint 与 manifest，当前单份上限2MiB、默认总32MiB/64份，并预留末份与清单预算；进程创建时间变化会停止，同地址计数回退要求身份复核。token 和观察段均不是比赛 ID；轮询可能漏掉短暂对象。既有真实日志离线回放与 round8 正常比赛真实留存均已通过。round6 首发联赛跳过前后真实轮询598次、round8替补跳过120次均 unavailable、读错0。隔离研究脚本另对未入选与首发杯赛模拟做 2 毫秒目标间隔的管理指针诊断，合计 50,164 次均为空、实际最大间隔小于5毫秒；结果只限观察窗口，不能证明任何模拟 buffer 均不存在。下一场/邮箱原生入口仅是静态线索，邮件尚未解码。

本轮正常比赛后目标 24B 条目在首发跳过后消失；评分仍存在于原生赛程个人条目，不能由缓存缺失推断未出场。累计低位计数在未入选时也增长；两个累计桶在足总杯中也同步增长，仍不命名季/生涯范围。所看赛前邮件只独立核对接收日期、首发位置，没有提供原生事件 ID；生涯菜单中静态候选使用的管理根为空，尚非可用 BAL 邮箱接口。证据摘要索引为隔离目录 `underlying/round6-validation.json`，D1/D2 正式门槛仍未通过。

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_runtime_watch --pid <当前PES进程ID> --native-player-id 2147483834 --duration-seconds 300 --output game_adapters/raw/real_test/<已有目录>/new-watch
.\venv\Scripts\python.exe -m game_adapters.pes2021_runtime_watch --replay game_adapters/raw/real_test/<已有目录>/trace.json --native-player-id 2147483834 --output game_adapters/raw/real_test/<已有目录>/new-replay
.\venv\Scripts\python.exe -m unittest discover -s game_adapters -p 'test_*.py'
```

回放 trace 的 `samples` 每项需有隔离根内 `path`、原件 `sha256` 和按序 `elapsed_seconds`；回放来源明确标记 `offline_replay`，不能作为真实采集验收。源 JSON 不改写，不可用记录保持 null；所有正式导入门槛仍为 false。

独立生涯内存预览无需先保存，调用方提供本地研究赛程序号、原生目标 ID、预期俱乐部索引及 packed 引用：

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_runtime_career --pid <当前PES进程ID> --fixture-sequence 2558 --native-player-id 2147483834 --expected-team-index 244 --expected-packed-index 3482255360 --output game_adapters/raw/real_test/<已有目录>/new-career.json
```

该接口核对十段加载代码与准确进程、重复读取生涯根/赛程/缓存，单次成功读取8546B。只支持序号与原生表索引一致的本机构建；球队不符、ID重复、引用/侧别冲突、未知代码或读取变化均拒绝绑定。保留596B赛程和1940B缓存完整原始字节；未完成不分类比分/个人统计，旧场缓存不绑定新场，目标缺失保持 null。它读取模拟摘要，不提供跳过场次的完整射门/传球/距离，不认证生涯分支或持久 event ID；所有 ready 门槛仍为 false。

用户已明确要求直接从游戏底层取数。当前主线为隔离 BAL 存档副本解密与原生球员/赛程解析，统一使用 [pes2021_interface.py](./pes2021_interface.py) 的 `capture / inspect / stats / compare / prepare-reuse`。命令、Python 接口和字段边界见 [只读脚本接口文档](../docs/D1_PES2021_只读脚本接口与字段边界_2026-10-05.md)；最新实际样本见 [测试槽轮换与重载分支验证](../docs/devolopment_logs/2026-10-06_D1_测试槽轮换与重载分支验证.md)。此前 [首发主动跳过日志](../docs/devolopment_logs/2026-10-05_D1_首发主动跳过与统计分层验证.md) 保留。以下页面读取器只作独立对照，其门槛不能用于宣称底层 D1 完成。

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface capture --slot 3 --output game_adapters/raw/real_test/my-new-capture
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface inspect game_adapters/raw/real_test/my-new-capture/save.bin
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface stats game_adapters/raw/real_test/my-new-capture/save.bin
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface prepare-reuse --slot 2 --expected-native-player-id 2147483834 --output game_adapters/raw/real_test/my-new-reuse-archive
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface compare <前一隔离副本> <后一隔离副本>
.\venv\Scripts\python.exe -m unittest game_adapters.test_pes2021_save game_adapters.test_pes2021_save_diff game_adapters.test_pes2021_bal_research game_adapters.test_pes2021_interface -v
```

采集目录必须尚不存在。存档四块明文 SHA512 都需符合内置摘要；原件保存进行中或变化则拒绝采集。接口可读身份、保存日期、能力候选、赛程比分、单场个人条目和独立累计候选；未知字段不填零，不生成正式个人比赛事件。单场、未命名累计双桶、24B 缓存及其头诊断分别输出，不能相互代替；存档尚未定位的射门/传球详细统计为 null。指定环境下既有 D1.2 运行检查及 round8 正常比赛 live 留存已补齐，累计范围、实际换人/角色语义和持久分支身份仍待认证，D1 整体/D2 未完成。

槽位 1 是用户切尔西生涯，禁止作为轮换目标。测试槽归档、核对原生 ID 及哈希后可正常在游戏菜单覆盖；`prepare-reuse` 仅生成精确归档凭证，实际覆盖前须再次复核原件与凭证。原隔离归档保持不变。

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_save game_adapters/raw/real_test/pes2021-bal-20261002-d1/underlying/BL00000001.t0.bin --bal
.\venv\Scripts\python.exe -m unittest game_adapters.test_pes2021_save -v
```

原始副本读取只输出预览；可选 `--output <新研究目录>` 保存解密分块，不覆盖原件。输入/输出都限制在隔离真实测试根。输出 `experimental_native_snapshot`，保留原文件及每块 hash、原生身份、字段字节/位位置。能力字段仍为上游布局候选，单场统计已由研究模块输出候选，OVR 未定位；保存快照不代表实时状态。没有 A 导入或游戏写回功能。

`pes2021_memory_probe.py` 是此前有限只读进程扫描实验，仅请求查询/读取权限，核对准确 exe 路径。按指定原生 ID 和姓名筛选候选，最多扫描 2 GiB / 25 秒，不输出完整转储，不选择未经核实的当前指针。该旧扫描尚未定位有效球员候选；此次 `pes2021_runtime_stats.py` 经原生 getter 定位实现了另一条详细统计读取路径，不依赖旧扫描结果。

`pes2021_save_diff.py` 比较两份隔离副本的解密分块，输出原件/分块 hash、固定偏移变化计数、有限字节样本及候选能力变化。区块插入导致的后续偏移变化不进行自动对齐；区块移动、日期推进或字节变化不能直接认定为比赛事件。姓名/原生 ID 相同也不认证为同一生涯分支。

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_save_diff <首场前隔离副本> <赛后隔离副本> --output <隔离根内的新JSON文件>
.\venv\Scripts\python.exe -m unittest game_adapters.test_pes2021_save game_adapters.test_pes2021_save_diff -v
```

现有 T0–T7 及同节点重载另存样本，含未入选结算、首发主动跳过、替补入选跳过及重模拟分支。T2/T4/T5 已补齐三个受控赛后推进节点，重复采集、失败/停止与原件保护检查通过。35 项原生解析/接口测试、6 项输入保护测试通过；运行验收不代替字段语义与正式事件身份认证。T5 的 25 项能力已逐项核对一次。已完赛同锚点变化或保存日期倒退会触发分支复核提示，文件哈希不会作为比赛 ID。切尔西只读样本与测试档共享原生 ID，但属于不同生涯；累计引用不匹配时只保留诊断，不自动提取累计统计。

## 页面对照工具（此前方案）

此模块读取隔离目录中的截图原件和逐字段人工核实记录，只输出原始预览。当前支持本机已核对的 PES 2021 1.07.01 / Data Pack 7.00 / SmokePatch V4 / 一球成名，方案为 `pes2021-bal-manual-pages-v1`。截图来自真实界面；字段由操作者逐页核实。没有 OCR、存档解密、进程内属性读取或 A 领域导入。

## 使用

在仓库根目录执行：

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_pages game_adapters/raw/real_test/pes2021-bal-20261002-d1
.\venv\Scripts\python.exe -m game_adapters.pes2021_pages game_adapters/raw/real_test/pes2021-bal-20261002-d1 --capture t0
.\venv\Scripts\python.exe -m unittest game_adapters.test_pes2021_pages -v
```

成功返回 `status=preview_only`，失败返回 `status=rejected` 和错误代码，退出码为 1。真实原件只存 `raw/real_test/`，默认 Git 忽略；合成样本必须放在 `raw/fixtures/` 并声明 `purpose=fixtures`。测试用临时合成数据不会认证为真实游戏样本。

## 页面包

```text
<dataset>/
  environment.json
  binding.json
  event_ledger.json
  captures/<capture_id>/
    manifest.json
    verified_fields.json
    pages/*.jpg|png
  setup/                     # 建档、身份绑定、事件确认和实验日志
```

`environment.json` 保存 schema、来源用途、游戏/补丁/模式及采集方法。`binding.json` 保存 `career_id/branch_id/player_id` 三元身份、实际槽位和球员信息、核对依据，状态为 `candidate` 或 `verified`。核实绑定要求引用实际存在、身份匹配且状态为 verified 的本地 JSON 确认文件；候选绑定可保留 raw，但不能通过快照/比赛门槛。内部 ID 由已核对的登记分配，姓名不是原生唯一 ID。

manifest 记录 schema/adapter/capture ID、三元身份、带时区的采集时间、游戏日期精度、质量、事件引用及每张页面的相对路径/SHA256。路径越界、原件缺失/摘要不符、不完整包及不支持环境会被拒绝。哈希只检验完整性，不证明截图真实性或可读性；当前需要逐页人工对照。

字段记录示例：

```json
{
  "state": "observed",
  "value": 70,
  "source": "page_review",
  "scope": "snapshot",
  "review": {"status": "reviewed", "reviewer": "assistant"},
  "evidence": [{"page_id": "identity", "label": "综合评分", "region": "个人资料右上角"}]
}
```

原始字段按 `identity/profile/season/career/schedule/match` 命名，scope 分别为 `identity/snapshot/season/career/fixture/match`。这保证季累计与球队结果有明确来源范围，不能替代个人单场统计。综合评分等原始值没有转换成 A 的规范属性；量表映射在 D2 单独验证。

缺失状态为 `not_provided/not_captured/unreadable/pending_review/parse_error/not_applicable`，均有原因。除待核实提示外，缺失值必须为 null；预览不暴露待核实提示值为观察。页面明确显示的 0 保留为 0，`---` 保持不适用，未读到评分不会变成 0。助手核实不等于用户确认导入。

游戏时间只有 `day` 或 `unknown`，未知必须有原因。页面上的下一场日期是 `schedule.next_fixture_date`，不能当作当前日期。采集时间始终独立保留。

## 比赛与门槛

单场字段需要引用人工台账中已完成事件。事件有稳定 `event_id`、推进序号、关联 capture IDs 和确认依据，确认文件必须存在且身份、事件 ID、完成状态一致。没有原生事件 ID 时由经过核对的持久台账分配，不能按截图数量或采集时间生成新比赛。

同事件多包合并成一场；同字段值不一致返回 `event_conflict`。`match.participation` 区分首发、替补出场、已出场但角色未知、替补未出场、未入选。没有明确个人参与证据时不能通过比赛门槛。

- `snapshot_ready`：核实绑定、球员姓名和综合评分已取得。
- `match_ready`：核实绑定、完成事件和个人参与状态已取得。
- `d1_minimum_sample_ready`：页面包结构满足一个快照加一场事件，fixture 也可用于测试该条件。
- `real_game_sample_ready`：上述条件成立，且来源根和用途均为 `real_test`。它是样本覆盖提示，仍须人工核对真实原件与运行证据。

读取器不调用 Agent、Repository 或 Importer，`application_writes=0`。后续导入需要独立的身份/语义映射与明确目标。本页历史页面模块的测试不能代替底层真实运行验收；原生路线最新覆盖见上方开发日志。

## 输入实验

2026-10-23：[正常比赛自动运行验证](../docs/D2_PES2021_正常比赛自动运行验证_2026-10-23.md)已完成一场无球场操作的正常比赛，后台自动留存59条人员原生记录，并核对终场及生涯结算身份。BAL目标球员完整AI接管、全流程菜单自动化仍未实现；快速跳过按既定机制仅采集个人进球、助攻、评分。

`keyboard_probe.py` 是独立 Win32 SendInput 单键实验工具。必须提供当前 PES 窗口 HWND；默认仅诊断，显式 `--execute` 才发送按下/释放。发送前核对路径、前台窗口、已按键状态，持键期间监测焦点，异常仍释放按键。每次实验写新 JSON，不覆盖旧证据。

实验只证明指定菜单中单键有效；`events_inserted` 表示 Windows 接收输入，实际响应需截图核对。它不是无人值守比赛机器人，不安装虚拟手柄/驱动，也不修改 DualSense 设置。
