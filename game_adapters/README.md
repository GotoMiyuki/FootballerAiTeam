# PES 2021 BAL 底层读取研究（D1）

用户已明确要求直接从游戏底层取数。当前主线为隔离 BAL 存档副本解密与原生球员/赛程解析，统一使用 [pes2021_interface.py](./pes2021_interface.py) 的 `capture / inspect / stats / compare / prepare-reuse`。命令、Python 接口和字段边界见 [只读脚本接口文档](../docs/D1_PES2021_只读脚本接口与字段边界_2026-10-05.md)；最新实际样本见 [测试槽轮换与重载分支验证](../docs/devolopment_logs/2026-10-06_D1_测试槽轮换与重载分支验证.md)。此前 [首发主动跳过日志](../docs/devolopment_logs/2026-10-05_D1_首发主动跳过与统计分层验证.md) 保留。以下页面读取器只作独立对照，其门槛不能用于宣称底层 D1 完成。

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface capture --slot 3 --output game_adapters/raw/real_test/my-new-capture
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface inspect game_adapters/raw/real_test/my-new-capture/save.bin
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface stats game_adapters/raw/real_test/my-new-capture/save.bin
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface prepare-reuse --slot 2 --expected-native-player-id 2147483834 --output game_adapters/raw/real_test/my-new-reuse-archive
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface compare <前一隔离副本> <后一隔离副本>
.\venv\Scripts\python.exe -m unittest game_adapters.test_pes2021_save game_adapters.test_pes2021_save_diff game_adapters.test_pes2021_bal_research game_adapters.test_pes2021_interface -v
```

采集目录必须尚不存在。存档四块明文 SHA512 都需符合内置摘要；原件保存进行中或变化则拒绝采集。接口可读身份、保存日期、能力候选、赛程比分、单场个人条目和独立累计候选；未知字段不填零，不生成正式个人比赛事件。单场、未命名累计双桶、24B 缓存分别输出，不能相互代替；射门/传球等未定位详细统计为 null。指定环境下 D1.2 运行检查已补齐，累计范围、换人/角色语义、24B 未知字段和持久分支身份仍待认证，D1 整体/D2 未完成。

槽位 1 是用户切尔西生涯，禁止作为轮换目标。测试槽归档、核对原生 ID 及哈希后可正常在游戏菜单覆盖；`prepare-reuse` 仅生成精确归档凭证，实际覆盖前须再次复核原件与凭证。原隔离归档保持不变。

```powershell
.\venv\Scripts\python.exe -m game_adapters.pes2021_save game_adapters/raw/real_test/pes2021-bal-20261002-d1/underlying/BL00000001.t0.bin --bal
.\venv\Scripts\python.exe -m unittest game_adapters.test_pes2021_save -v
```

原始副本读取只输出预览；可选 `--output <新研究目录>` 保存解密分块，不覆盖原件。输入/输出都限制在隔离真实测试根。输出 `experimental_native_snapshot`，保留原文件及每块 hash、原生身份、字段字节/位位置。能力字段仍为上游布局候选，单场统计已由研究模块输出候选，OVR 未定位；保存快照不代表实时状态。没有 A 导入或游戏写回功能。

`pes2021_memory_probe.py` 是有限只读进程实验，仅请求查询/读取权限，核对准确 exe 路径。按指定原生 ID 和姓名筛选候选，最多扫描 2 GiB / 25 秒，不输出完整转储，不选择未经核实的当前指针。当前实际实验尚未定位有效球员候选，不能宣称实时读取已实现。

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

`keyboard_probe.py` 是独立 Win32 SendInput 单键实验工具。必须提供当前 PES 窗口 HWND；默认仅诊断，显式 `--execute` 才发送按下/释放。发送前核对路径、前台窗口、已按键状态，持键期间监测焦点，异常仍释放按键。每次实验写新 JSON，不覆盖旧证据。

实验只证明指定菜单中单键有效；`events_inserted` 表示 Windows 接收输入，实际响应需截图核对。它不是无人值守比赛机器人，不安装虚拟手柄/驱动，也不修改 DualSense 设置。
