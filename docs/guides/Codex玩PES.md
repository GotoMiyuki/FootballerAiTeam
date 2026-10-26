# Codex玩PES

核对日期：2026-10-25。适用环境：本机Windows、PES2021 1.07.01 / Data Pack 7.00 / SmokePatch V4、专用BAL测试生涯。本文按用户原始要求、D阶段逐轮记录、输入审计、现有脚本和当前computer-use接口整理，供新对话直接接续；本次编写没有新发游戏按键或修改设置。

## 1. 新Agent先读这一页

**已经有可复用的输入方法，不要因为普通Return/Space没反应就重新从“键盘能不能用”开始调查。** 已实测的组合是：computer-use选取/激活/观察游戏窗口，项目现有 `keyboard_probe.py` 发送一次约100ms的键盘按下与松开，然后重新观察画面。

1. 阅读本会话提供的computer-use技能和配套接口说明，取得当前PES窗口；窗口已存在时优先复用。
2. 激活PES，检查当前画面和该窗口的输入法。游戏重启、新对话或工具重置后都重新取窗口、PID和HWND。
3. 使用既有输入脚本先诊断，再发送**一个**有明确用途的键；默认 `scan + 100ms`。
4. 重新看游戏画面，确认高亮/页面确实改变，再决定下一键。不要照抄旧轮次的按键序列。
5. 进入测试生涯时选择“载入”，核对槽位2、测试球员及Derby；准备覆盖测试槽前先归档。**槽位1的切尔西档和原DualSense设置受保护。**

这份文档记录方法和已存在的用户授权背景，不自行授予新权限。使用时遵守当前用户要求、工具权限和更高优先级规则；真实的应用访问授权超时仍须按授权流程解决，不能用输入脚本绕过。

## 2. 哪些能力已经实测

| 能力 | 已有证据 | 边界 |
| --- | --- | --- |
| 实体键盘能操作 | 用户现场确认，菜单大部分可用方向键和Enter/Esc | 不等于所有自动化输入接口都兼容 |
| 虚拟键盘菜单导航 | 2026-10-02，10次独立输入逐步核对；scan和vk均有成功样本 | 不能断言scan是唯一成功因素，或原接口失败已确定由时长造成 |
| 载入专用BAL、推进赛程、正常保存 | D后续多轮真实操作 | 每次仍核对当前场景、对象和槽位 |
| 进入正常比赛，暂停、恢复、中场和赛后确认 | 2026-10-23，round12完成一场正常比赛；有45次输入台账 | 没有验收完整的球场操作机器人 |
| 不发送球场动作也能推进至终场 | 同场2–0，后台留存59条人员明细，中场仍需两次确认 | 不等于目标球员由AI完整代踢；无输入样本有站位不动等偏差 |
| 通用界面输入在标题页无反应 | 早期D记录及2026-10-25的M3访问检查 | 仅证明该输入尝试无可见响应，不推翻独立输入脚本的历史成功 |

2026-10-02的“标题页尚未验收”是当时范围；2026-10-23操作已推进到载入测试档和完赛，同轮有 `input-001-title.json` 的Enter扫描码审计。但单独的JSON不证明页面跳转，新会话仍须对标题页做一次局部验证，不能仅凭旧文件宣称本次已成功。

## 3. 先取得正确窗口，不复用旧句柄

安装目录为 `%USERPROFILE%\Desktop\pes`，游戏为 `PES2021.exe`，设置程序在同目录。工作区为 `%USERPROFILE%\Desktop\footballerAITeam`。

先读取**当前会话实际提供的**computer-use `SKILL.md`，再读其 `guidance.md`、`api.md`和权限说明。插件版本路径会更新，不复制历史缓存路径。当前技能使用 `node_repl`中的 `@oai/sky`；如果会话提供其他接口，先读对应文档，不把不同REPL的变量或API混用。

以下是技能所用sky接口的示例。先在node_repl初始化并列出候选，停下核对结果：

```js
if (!globalThis.sky) {
  globalThis.sky = (await import("@oai/sky")).sky;
}
globalThis.pesCandidates = (await sky.list_windows()).filter((w) =>
  /pes2021\.exe/i.test(String(w.app)) ||
  /eFootball PES 2021/i.test(String(w.title || ""))
);
nodeRepl.write(JSON.stringify(pesCandidates, null, 2));
```

只有返回结果确实是目标游戏、且恰好一个窗口时，下一次调用才选择和激活：

```js
if (globalThis.pesCandidates.length !== 1) {
  throw new Error("PES窗口不唯一或不存在，先重新核对返回列表");
}
globalThis.pesWindow = await sky.get_window({
  id: pesCandidates[0].id,
  app: pesCandidates[0].app,
});
await sky.activate_window({ window: pesWindow });
globalThis.pesState = await sky.get_window_state({ window: pesWindow });
globalThis.pesWindow = pesState.window;
```

截图由工具直接展示。不要为看图另行打印base64；也不要用旧截图坐标或无障碍索引点击新页面。每次有状态变化后刷新并更新 `pesWindow = pesState.window`。

**sky的Window.id按API定义是不透明窗口标识，不直接假定它就是原生HWND。** 原生输入脚本需要下面进程元数据里的 `MainWindowHandle`，然后由脚本再次核对EXE路径和前台窗口。历史数字如2034446、591648及旧PID只属于原轮次，禁止直接复用。

游戏不在运行时，可按当前工具说明启动已有EXE并重新枚举窗口。本机曾出现直接启动后退出，而从游戏目录的资源管理器双击成功的情况；只能作为启动方式线索，工作目录是不是根因尚未认证。不要通过运行框或资源管理器执行shell命令，也不需要为此启动Sider、安装驱动或改补丁。

## 4. 可复用的虚拟键盘命令

实现与离线测试：[keyboard_probe.py](../../game_adapters/keyboard_probe.py)、[test_keyboard_probe.py](../../game_adapters/test_keyboard_probe.py)。它用Win32 SendInput，按下和松开分开发送，不安装虚拟手柄，不注入游戏代码。代码导入和 `--help` 均不会发键。

本项目已有用户明确要求测试并复用虚拟键盘的记录。当前任务已授权操作游戏且可复用该脚本时，按下面方式执行；这与另写PowerShell UI Automation或自行改造computer-use内部协议不同。

### 4.1 只诊断，不发键

命令在仓库根目录通过执行工具运行，保持游戏在前台。不要手动切到前台终端再要求脚本给后台游戏发键。每个执行调用的PowerShell变量可能不保留，以下块都独立重新查询进程。

```powershell
$pesProcesses = @(Get-Process -Name PES2021 -ErrorAction Stop)
if ($pesProcesses.Count -ne 1) { throw '先核对当前唯一PES进程' }
$pesHandle = $pesProcesses[0].MainWindowHandle.ToInt64()
if ($pesHandle -eq 0) { throw '没有主窗口句柄；先恢复窗口并重查执行桌面' }
$pesAudit = Join-Path 'game_adapters/raw/real_test' ('pes-control-' + [Guid]::NewGuid().ToString('N') + '/diagnostic.json')
.\venv\Scripts\python.exe -m game_adapters.keyboard_probe --hwnd $pesHandle --output $pesAudit
```

检查 `status=diagnostic_only`、`target.executable`、`target.hwnd`、`target.foreground_hwnd`、`target.layout`、`sender_integrity_rid`和 `target.integrity_rid`。诊断成功**不检查当前是否有键按住**；这项防护在实际发键时执行。

### 4.2 一次Enter，100ms，然后立刻观察

先在computer-use看清当前画面，确认Enter正是本步需要的动作，再调用：

```powershell
$pesProcesses = @(Get-Process -Name PES2021 -ErrorAction Stop)
if ($pesProcesses.Count -ne 1) { throw '先核对当前唯一PES进程' }
$pesHandle = $pesProcesses[0].MainWindowHandle.ToInt64()
if ($pesHandle -eq 0) { throw '没有主窗口句柄，停止输入' }
$pesAudit = Join-Path 'game_adapters/raw/real_test' ('pes-control-' + [Guid]::NewGuid().ToString('N') + '/enter.json')
.\venv\Scripts\python.exe -m game_adapters.keyboard_probe --hwnd $pesHandle --key enter --encoding scan --hold-ms 100 --execute --output $pesAudit
```

接着在node_repl单独刷新；不要没看结果就继续发下一键：

```js
globalThis.pesState = await sky.get_window_state({ window: pesWindow });
globalThis.pesWindow = pesState.window;
```

方向、返回和暂停复用同一命令，只将 `--key`改为 `right/left/up/down/esc/v`，输出使用新文件名。当前白名单是 `enter esc left up right down x v a q e space`；不支持WASD、多键组合、连续持键或虚拟手柄，不能在参数中拼出不存在的组合键。

`PES_PATH`由脚本所在仓库的上级目录拼接 `pes/PES2021.exe`，当前恰好对应桌面相邻两个文件夹。若换到其他worktree或安装位置，可能触发路径拒绝；先核对源码和实际位置，不伪造路径或删除校验。

### 4.3 如何解释执行结果

| 检查 | 能证明什么 |
| --- | --- |
| down/up各 `inserted=1`，无输入失败 | Windows接收了两次输入事件；仍不证明PES响应 |
| `foreground_after`仍为目标，未标 `focus_lost` | 本次监测没有发现失焦；不能代替页面检查 |
| 刷新后预期高亮/页面变化 | 才能把当前操作记为游戏实际接受 |
| 统计采集JSON成功 | 证明读取结果，不自动证明导航或比赛事件身份 |

工具只检查所列键、修饰键和鼠标按钮的按住状态，不读取手柄按钮。测试时实体键盘、鼠标和手柄应保持静止。允许按压范围20–500ms，默认100ms；本机500ms方向键曾自动连移多项，600ms被拒绝。不要为了“没反应”直接拉长按压或盲连按。

## 5. 本机键位与操作场景

Settings键盘页曾核对：确认A映射到键盘X，Start映射V，左摇杆方向为箭头，十字键为数字键盘8/4/2/6。**手柄图标A/X与键盘字母a/x不是一回事。** 菜单实测仍可用Enter/Esc，不能仅按图标猜键。

| 场景 | 使用的键 | 已验证的行为与注意点 |
| --- | --- | --- |
| 大部分目录菜单 | 箭头、Enter、Esc | 选择、确认、返回；具体方向根据当前画面决定 |
| 标题页“按任意按钮” | 先尝试一次Enter的独立扫描码输入 | 历史同轮有标题输入及后续流程；本次必须看画面确认，不重复普通Return/Space无效尝试 |
| 正常比赛暂停/恢复 | `v` | 本机Start；`esc`曾两次没有暂停，勿据此判键盘失效 |
| 中场 | Enter →观察→ Enter | round12先确认半场总览，再选择“下半场”；不是所有菜单都固定两次Enter |
| 赛后精彩回放 | `v` | 当前画面出现Start“跳过”时可跳过回放；与快速跳过整场比赛不同 |
| 全场总览、评分结果 | Enter | 逐页确认，完成结算；勿误选退出/重赛 |
| 暂停后的“选择控制方/个人设定” | `a` | 本机对应图标X，曾打开个人设置；不是“按键盘A确认” |
| 提示LB/RB翻页 | `q/e`为既有映射 | 工具提供这两个键；每个具体页面仍现场核对，不能推广成所有情境均验收 |
| 提示A确认或传球 | `x`为已核对映射 | 不是通用“进入标题页必成功”保证；球场动作的控制质量未系统验收 |

射门、冲刺、方向持键与组合操作不在这份指南的已验收能力内。现有脚本能发送某个键，不表示已知道该键在所有场景的足球动作。不要虚构“Codex已会正常踢完整场”或“AI已接管目标球员”。

## 6. 没反应时按这个顺序排查

| 现象 | 下一步 |
| --- | --- |
| 应用访问授权超时、窗口尚未授权 | 先解决工具的应用访问问题；不能据此声称按键兼容性失败，也不通过shell绕过授权 |
| 锁屏、权限弹窗、当前不是游戏桌面 | 停止输入，按当前工具流程恢复可操作桌面；不要尝试控制LockApp或安全设置 |
| 普通computer-use按键成功返回但画面不动 | 不反复敲Return/Space。核对焦点与输入法后，复用已授权的独立100ms单键试验 |
| `Window is unavailable in this execution desktop`、进程句柄为0 | 重新用窗口工具选取游戏；检查执行工具是否处于能访问交互桌面的环境。历史上沙箱看不到窗口，按审批流程切换执行环境后可见 |
| `PES is not foreground; no input sent` | 回到computer-use激活PES并观察；脚本本身不会抢焦点 |
| `Physical key/button is held` | 松开实体键盘/鼠标，待手柄静止后重新诊断；不移除安全检查 |
| Sender integrity低于游戏 | 核对两端实际RID及启动方式；不要把“沙箱外”误当“管理员”，不默认要求提权或关闭UAC |
| `Window process is not the permitted PES executable` | 当前句柄属于别的窗口，或worktree/安装路径与脚本的允许路径不同；核对路径，不绕过保护 |
| 出现中文候选框、组合输入或字符拦截 | 取消组合，再针对游戏窗口切换输入法，重新激活并诊断游戏线程布局 |
| 进入比赛后Esc无效 | 按本机Start键V做一次暂停验证，重新观察 |
| 按一下跳了多个菜单项 | 检查按压时长是否过长；回到100ms单键并逐步观察 |

英文输入法是排查条件之一，不是已确定的唯一根因。历史游戏线程有中文 `0x8040804` 和英文 `0x4090409` 的直接诊断，切到英文后普通按键仍可能无效；round12也有中文布局下独立扫描码输入成功。不能只看资源管理器任务栏的ENG就认定PES窗口也是英文。

2026-10-23在场内出现中文候选时曾用 `sky.press_key({ window: pesWindow, key: "Control_L+space" })`切换/取消后恢复观察，但快捷键效果依本机输入法设置而变；使用前后都要看游戏窗口，必要时以脚本 `target.layout`复核。不使用Windows键快捷键，也不为切换输入法改系统安全设置。

若上述独立输入在同一画面仍无效，记录当前阶段、输入路径、HWND/PID、焦点、布局、按压时长及前后画面，再请求玩家做一个具体的局部动作。玩家手动按键是定位边界的方法，不应因为普通接口失败就直接宣称整套输入都不可用。

## 7. Settings和DualSense

已有键盘输入成功，不需要为了每次恢复操作重新设置键盘。控制器页设备为 `--` 或接口不可选，不是键盘被禁用的证据。不要因“可能没硬件DirectInput”去改用户原DualSense配置、重排控制器或安装虚拟驱动。

确实需要检查Settings时，先记录原页面与 `settings.dat`摘要，只检查目标键盘/显示项。调整之后须点击该程序的确认/保存，并重新打开核对；关闭窗口或取消不能算已保存。曾有显示模式调整由Settings写盘、页面恢复但缺少最初字节副本的情况，所以只能说“页面设置恢复”，不能说“文件逐字节未变”。未改设置则不要为了留证再保存一遍。

## 8. 载入、推进与保存专用生涯

主菜单找到BAL个人生涯入口，选择**载入**，按槽位、球员和俱乐部核对后进入。菜单名称/高亮位置以当前画面为准，不能照搬 `down,right,enter…` 的旧序列；不因没有看到加载项就确认“新游戏”。

| 对象 | 约束 |
| --- | --- |
| 槽位1 / `BL00000000` | 用户切尔西档，不覆盖、不用于测试轮换 |
| 槽位2 / `BL00000001` | 专用测试档，可按既有授权在归档后覆盖 |
| 测试目标 | 测试球员、Derby，原生ID2147483834；同ID不足以区分所有生涯，仍核对俱乐部、槽位及来源 |

每轮覆盖测试槽前先归档，输出必须是新目录：

```powershell
$pesArchive = Join-Path 'game_adapters/raw/real_test' ('pes-slot2-' + [Guid]::NewGuid().ToString('N'))
.\venv\Scripts\python.exe -m game_adapters.pes2021_interface prepare-reuse --slot 2 --expected-native-player-id 2147483834 --output $pesArchive
```

检查 `reuse-ticket.json`中的slot、姓名、日期、original_path和archived_sha256，并立即复核源文件hash，再通过游戏正常保存菜单选择槽位2及覆盖确认。票据只证明磁盘原件已归档，不会执行覆盖，也不自动证明当前进程中加载了哪个生涯。工具拒绝为槽位1生成复用票据，见[实现](../../game_adapters/pes2021_interface.py)。

**磁盘存档与当前内存进度可能不同。** 2026-10-23那场正常比赛结束时没有手工保存；不能把重新载入旧槽后重踢的相同赛程自动追加到原分支。重新加载前核对未保存进度，发生重踢/回档需按D的审阅与分支规则处理。

赛前先确认是否入选；未入选时没有目标球员的正常出场选项，不通过空记录推断0分钟出场。快速跳过只更新个人进球、助攻、评分；需要射门/传球等动作明细时选择正常比赛引擎。赛后先留存明细，再继续退出结果流程；离开后详细对象可能释放。

## 9. 操作界面与底层取数分工

界面用来确认导航、选中对象、入选状态和实际按键响应；数据主线使用原生读取器，不重新依赖OCR抄统计。正常引擎详细数据应在开赛前启动有限留存器，保留执行会话，比赛期间处理菜单，结束后核对manifest和最后有效检查点；不要在同一个工具调用里阻塞等待整场比赛。

入口为 `python -m game_adapters.pes2021_runtime_watch --help`、`pes2021_runtime_stats --help`和 `pes2021_runtime_career --help`。PID取当前进程，输出为隔离根内的新目录/新JSON。watcher只留存，不开赛、不暂停、不保存游戏，也不自动完成生涯/事件审阅或D2导入。完整数据接口见[D当前说明](../completed/D_游戏接入与采集适配.md#d-current)。

不要把不发球场动作的完整比赛当AI代踢验收。BAL选择控制方未能成功移到中立侧、光标切换显示固定的历史观察仍有效；真正AI接管和全流程无人值守尚未实现。比赛身份、连续性和玩家真实选择也不能靠“按键脚本跑完了”认证。

## 10. 依据、复核与对话交接

| 来源 | 用途 |
| --- | --- |
| [D输入可行性第7节](../completed/D_游戏接入与采集适配.md#history-d-07-7-独立虚拟键盘实现与现场验证) | 独立输入成功、布局/完整性、100ms和逐步截图证据 |
| [D早期设置与输入排查](../completed/D_游戏接入与采集适配.md#history-d-16) | 键盘映射、Settings保存、控制器与显示模式边界 |
| [正常比赛实测](../completed/D_游戏接入与采集适配.md#history-d-11) | Start为V、中场/赛后菜单、明细留存、AI接管未实现 |
| [输入脚本](../../game_adapters/keyboard_probe.py)、[6项离线测试](../../game_adapters/test_keyboard_probe.py) | 当前白名单、参数、保护检查和输出格式 |
| [M3接续](../roadmap/M3_实机验收与接续.md) | 2026-10-25普通接口标题页失败的当前上下文，不视为新输入验收 |

本机原始材料在Git忽略目录，不随仓库分发：10/02的 `pes2021-bal-20261002-01/setup/keyboard-probe-20261002143249483/`；10/23的 `pes2021-bal-20261002-d1/underlying/round12-auto-engine-20261023/`（含input-001、031、036、040–045及acceptance）；10/25的 `m3-access-20261025-160456/access-check.json`。以上均相对 `game_adapters/raw/real_test/`。脚本JSON与画面观察各有职责，不能用前者补造后者。

只复核工具、不操作游戏的命令：

```powershell
.\venv\Scripts\python.exe -m game_adapters.keyboard_probe --help
.\venv\Scripts\python.exe -m unittest game_adapters.test_keyboard_probe -v
.\venv\Scripts\python.exe tools/check_docs.py
```

本次文档复核实际通过上述6项离线输入测试，核对round12全部45份输入JSON的历史SHA256，并以PowerShell解析器检查4个命令块的语法；没有执行其中的游戏诊断/发键/归档动作。全仓本地链接与45个合并历史正文校验也通过，不把这些检查称为新的实机输入成功。

交给另一个对话的简短任务说明：

> 请先读 `docs/guides/Codex玩PES.md`及当前computer-use技能。复用已授权的 `game_adapters.keyboard_probe`：重新取得当前PES进程和原生HWND，激活窗口，先诊断，再用scan、100ms一次发一个键，每步重新观察。菜单主要用方向键和Enter/Esc，场内Start为V。普通Return/Space失败不能推翻独立输入脚本的历史成功。保护槽位1切尔西和DualSense，槽位2归档后才能覆盖；不要复用旧句柄、盲连按或把无输入完赛称作AI代踢。具体推进仍遵守当前任务的授权与验收范围。
