# PES2021 游戏适配器

当前支持本机指定 PES2021 1.07.01 / Data Pack 7.00 / SmokePatch V4 专用BAL测试范围。D0/D1/D2的当前接口、映射规则和全部历史记录统一见[D阶段完整文档](../docs/completed/D_游戏接入与采集适配.md#d-current)。旧版长README保留在该文档[历史附录](../docs/completed/D_游戏接入与采集适配.md#history-adapter-readme)。

## 入口

| 模块 | 用途 |
| --- | --- |
| `pes2021_interface` | 隔离存档capture/inspect/stats/compare和测试槽归档票据 |
| `pes2021_runtime_stats` | 正常引擎个人详细对象只读研究 |
| `pes2021_runtime_career` | 指定赛程摘要与最近比赛缓存只读研究 |
| `pes2021_runtime_watch` | 有限轮询与末次有效明细留存 |
| `pes2021_native_package` | 重解析已有受审阅包；不自动创建身份或事件审阅 |
| `pes2021_import` | 显式bind、preview、commit，经A受控导入 |
| `keyboard_probe` | 有界单键输入诊断与显式实验，不是比赛机器人 |

在仓库根目录用 `python -m game_adapters.<模块名> --help` 查看参数。研究读取器保持保守认证标志，正式导入另由受信D2服务校验；实时JSON不能直接当已审阅比赛。快速跳过只有个人进球、助攻、评分；详细动作统计来自正常比赛引擎。

## 本地回归

```powershell
.\venv\Scripts\python.exe -m unittest discover -s game_adapters -p 'test_*.py' -q
.\venv\Scripts\python.exe -m unittest evaluation.test_player_imports evaluation.test_player_repository -q
```

`game_adapters`是命名空间包；上述discovery不传 `-t .`，也可显式列出 `game_adapters.test_*` 模块。测试使用合成/隔离材料，不是新实机验收。真实存档与采集材料保存在被忽略的 `raw/real_test/`，不会随clone取得。知识语料、原始证据以及用户切尔西槽位不属于本次文档整理范围。
