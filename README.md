# FootballAI Career Agent

基于 LangGraph 的足球运动员职业生涯多智能体协作系统。输入需求后，系统根据球员快照和任务约束分工，生成训练、营养、表现与职业建议，或公关声明等报告。

文档入口：[版本变化](CHANGELOG.md)、[开发日志](docs/development_logs/README.md)、[操作指南](docs/guides/运行与验收.md)、[验收交接](docs/completed/README.md)、[报告证据](docs/reports/README.md)。完整分类与当前/历史说明见[文档导航](docs/README.md)，写作约定见[文档管理规范](docs/guides/文档管理规范.md)。

当前版本：**v0.4.0 — Career Companion MVP**。版本由根目录[VERSION](VERSION)与前端元数据声明，[发布准备与验证](docs/development_logs/2026-10-27_v0.4.0发布准备.md)记录本次版本统一和文档CI；远程状态见[GitHub发布记录](https://github.com/GotoMiyuki/FootballerAiTeam/releases/tag/v0.4.0)。

2026-10-27：[M4已通过当前本地单用户Windows Career Companion的production-like MVP验收](docs/completed/M4_阶段四验收与交接.md)。软件验收沿用该日期与范围，发布准备未新增实机、外部模型或实际库迁移结论。

历史阶段记录（2026-10-25）：[M3已通过指定本地PES2021范围验收](docs/completed/M3_阶段三验收与交接.md)：十场连续赛程、真实采纳与菜单调整、57→57观测比较及真实模型新建议已贯通。新建议未自动采纳；不据此声称训练有效或AI代踢。其后开展的M4已完成上述范围验收，PES底层写回仍属后续方向。

阶段二已通过指定PES2021测试范围的[M2验收](docs/completed/M2_阶段二验收与交接.md)；E1.2、C2.1、E2.1、C2.2与[E2.2反馈闭环](docs/completed/E_E2.2_反馈驱动关联任务.md)已交付。此前实机输入失败与恢复背景保留在[M3接续记录](docs/roadmap/M3_实机验收与接续.md)。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key（编辑 .env 文件）
OPENAI_API_KEY=your-api-key
OPENAI_BASE_URL=your-api-url
MODEL_NAME=your-api-model

TAVILY_API_KEY=your-searching-key   # 可选，用于联网搜索

# 3. 运行
python app.py
```

模型调用使用 `ChatOpenAI`：配置 OpenAI 兼容的模型、API 地址和密钥；专业 Agent 的工具循环还要求模型支持工具调用。默认地址为 `https://api.deepseek.com/v1`、默认模型为 `deepseek-chat`，均可用上述环境变量覆盖。Tavily 未配置或检索失败时返回工具不可用/失败；这是可选工具错误，不自动判定整个任务失败，也不生成替代搜索答案。

## 它能做什么

以下是可提交的任务示例；具体分工由 Manager 生成的计划决定：

| 场景 | 示例输入 | 涉及的 Agent |
|------|---------|-------------|
| 训练计划 | "三个月后参加大学联赛，想提升爆发力和减重" | Coach + Nutrition + Analyst |
| 营养方案 | "帮我制定一份增肌期的饮食计划" | Nutrition |
| 表现分析 | "分析我最近的训练效果和比赛表现" | Analyst |
| 职业规划 | "我适合踢英超还是德甲" | Career |
| 公关声明 | "帮我回应媒体关于转会曼城的传闻" | Document |
| 赛前准备 | "为我制定一份完整的赛前准备方案" | Coach + Nutrition + Career |

## 系统架构

```
用户输入
  │
  ▼
Manager（总经理）          ← 理解意图，创建 Mission，分派任务
  │
  ├──→ Coach（技能教练）    ← RAG知识库检索 + 联网搜索 → 训练计划
  ├──→ Nutrition（营养师）  ← 营养计算器 → 饮食方案
  ├──→ Analyst（分析师）    ← 训练/比赛数据 → 风险诊断
  ├──→ Career（经纪人）     ← 联网搜索 + 估值模型 → 职业建议
  │
  ▼
Reviewer（审查员）          ← 检查质量，发现冲突则打回重做
  │
  ▼
Document（报告官）          ← 整合所有产出，生成最终报告
  │
  ▼
最终报告（训练计划 / 公关声明 / 商业评估 / 媒体应答）
```

## 核心设计

**有界工具循环** — Coach、Nutrition、Analyst、Career 使用 ReAct 循环，默认最多 5 次模型迭代；Manager、Reviewer、Document 通过各自的直接模型调用完成规划、审查和正文生成。工具消息本身不能代替有效专业结论。

**任务状态与历史材料**：

- 专业节点使用任务固定快照、显式依赖和选定历史构建输入。基类保留默认 5 条的滑动窗口辅助方法，当前主流程并未调用这些方法管理任务记忆。
- 球员档案和训练/比赛/职业历史通过 Repository 跨会话读取；任务、报告、消息和暂停位置分别持久化在任务 SQLite 与 checkpoint 中。C2 的长期目标/决策记忆及相关性检索尚未实施。

**四种工具**：

- RAG 知识库检索（ChromaDB + 足球专业文献）
- 联网搜索引擎（Tavily API）
- 运动营养计算器（BMI / BMR / TDEE / 宏量营养素）
- 球员快照只读访问（档案、训练史、比赛史）；事实更新使用独立的受控观察导入接口

**审查与修订**：Reviewer 区分 PASS、局部修订、核心变化重规划和缺输入等待；技术失败按真实失败结束。默认每个子任务最多修订 1 次、每个 Mission 最多重规划 2 次，修订与重规划共用 4 次 Loop 预算；这些预算分别约束对应动作，不能相加为固定执行轮数。正文通过结构、明确要求、支持的球员数值标签及引用 URL 检查后才可发布，全文语义和建议科学质量仍未认证。

**报告生成审批**：生成正文前暂停，用户可查看专业结果并明确批准继续，或保留暂停后退出。审批输入不能触发手工重规划；已完成任务根据新要求重评时创建关联新任务。

**数据与交付边界（2026-10-01）**：预测只作为建议，不写入球员事实。API、Graph 和工具使用同一 Repository 快照；真实档案须显式配置 `FAIT_PLAYER_DATA_MODE=actual` 与独立的 `FAIT_PLAYER_DATA_ROOT`，缺数据时不回退样本。模型、工具、输出结构或审查失败均不生成固定答案。最终交付要求当前专业成果通过审查、正文通过确定性检查；正文尚未进行全文语义审查。

已完成开发统一见 [路线图开发汇总](docs/completed/README.md)。PES2021 指定测试生涯的底层采集与受控导入见 [D阶段完整说明](docs/completed/D_游戏接入与采集适配.md#d-current)；快速跳过按游戏机制仅提供个人进球、助攻、评分，正常比赛可后台采集动作明细。数据配置与导入见 [A 汇总](docs/completed/A_球员数据与事实边界.md)，执行与审查契约见 [B 汇总](docs/completed/B_执行协作与审查正确性.md)。

**历史与续跑边界（2026-10-02，C1）**：查看历史只读；解释旧报告沿用原任务材料与数据版本，解释失败不撤回报告。Web 与 CLI 的缺输入、报告审批从原 SQLite checkpoint 续跑；原快照或 checkpoint 缺失时拒绝恢复。重新评估或失败重试创建有关联的新任务，提交时冻结同一球员的最新快照与选定历史；请求去重与完整输入持久化，新任务失败不覆盖原报告。详情见 [C1 会话恢复与任务延续汇总](docs/completed/C_C1_会话恢复与任务延续.md)。

**建议来源、选择与执行（2026-10-24，E1.1/E1.2）**：工作台可查看有专业来源和固定基线的训练焦点建议，分别记录采纳/拒绝/暂缓与独立的用户自报执行。请求防重、更正、撤回及重启保留历史；过期建议不得当前采纳。游戏操作仍待验证，反馈不写入球员事实，尚未比较效果。接口与验收见[E1.1建议来源](docs/completed/E_E1.1_建议来源与适用性.md)、[E1.2选择与执行](docs/completed/E_E1.2_玩家选择与执行记录.md)。

## 项目结构

```
footballerAITeam/
├── app.py              # 主入口，CLI 交互
├── graph.py            # LangGraph 状态图定义与路由
├── config.py           # 配置（API Key、路径）
├── registry.py         # Agent 与 capability 注册表；新增能力还需执行工厂和契约支持
├── agents/
│   ├── base.py         # Agent 基类（有界 ReAct 循环及窗口辅助方法）
│   ├── manager.py      # 总经理（Mission 创建 + 意图守护）
│   ├── coach.py        # 技能教练（训练计划）
│   ├── nutrition.py    # 运动营养师（饮食方案）
│   ├── analyst.py      # 表现分析师（数据诊断）
│   ├── career.py       # 职业经纪人（规划 + 转会分析）
│   ├── reviewer.py     # 信息审查员（质量把关）
│   └── document.py     # 报告生成官（最终产出）
├── tools/
│   ├── rag.py          # RAG 知识库检索
│   ├── search.py       # 联网搜索
│   ├── calculator.py   # 营养计算器
│   └── database.py     # 球员数据库
├── prompts/
│   └── agent_prompts.py  # 三层 Prompt 架构
├── memory/             # 默认样本、旧会话元数据及默认 Web 任务目录
├── player_data/        # 统一快照、actual 聚合存储及受控观察导入
├── game_adapters/      # 独立游戏读取、来源校验和受控映射
├── backend/            # Mission 应用、API、任务存储及持久化运行时
├── frontend/           # React + TypeScript 工作台
├── career_actions/     # 有来源建议、适用性读取与独立业务事件
├── knowledge/          # 足球专业文献（PDF/TXT）
└── utils/
    ├── helpers.py      # 工具函数
    └── sessions.py     # 会话管理
```

## 命令行用法

```bash
python app.py                              # 交互模式，新会话
python app.py "帮我制定训练计划"            # 直接输入需求
python app.py --list                       # 只读 CLI 任务索引和旧元数据
python app.py --show <mission_id>          # 只读原任务/报告/输入引用
python app.py --continue <mission_id>      # 恢复原缺输入或报告审批暂停
python app.py --reevaluate <mission_id> "新要求" --reason "新比赛"
python app.py --retry <mission_id> "重试要求" --reason "明确重试"
```

CLI 使用独立目录 `local_data/cli/fixture/` 或 `local_data/cli/actual/`，可用 `FAIT_CLI_DATA_DIR` 覆盖；真实球员访问仍需配置 actual 模式与数据根。目录保存任务库和 SQLite checkpoint；同一目录只允许一个 CLI/Web 写入进程，历史读取不占用写入锁。关联创建可提供 `--request-id`，相同内容重试返回同一任务；修改内容使用新请求身份。

## 多轮对话

Web 工作台持久化任务、报告和对话。完成后可解释原报告；根据新比赛或新约束调整方案时，可选择原报告/有效专业结果并创建关联任务，使用提交时的最新球员状态，仍可返回来源任务。失败任务也可创建关联重试。暂停任务的补信息与报告生成审批保留原任务编号和原快照。

CLI 在等待输入/审批时输入 `q` 保存暂停并退出，稍后用 `--continue` 从原状态恢复，使用原快照。完成任务的 `--show/--continue` 只读，不要求模型配置；根据新数据分析使用 `--reevaluate` 的新身份。旧 `memory/sessions.json` 只保存元数据，保留未验证历史，不推断或迁移成可恢复任务。

## 依赖

- Python 3.10+
- LangGraph + LangChain
- DeepSeek API（或 OpenAI 兼容接口）
- ChromaDB（向量检索）
- Tavily API（可选，联网搜索）
## Web 工作台 v0.4.0

Web使用React + TypeScript + Vite前端及FastAPI应用层；CLI复用任务应用与存储格式。当前操作以[运行指南](docs/guides/运行与验收.md)为准，[前端初始设计](docs/guides/前端初始设计指南.md)保留设计背景。

安装 Web 依赖：

```powershell
& .\venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-web.txt
cd frontend
npm.cmd ci
```

在仓库根目录启动真实后端（读取原有 `.env` 模型配置）：

```powershell
& .\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

另开终端启动前端：

```powershell
cd frontend
npm.cmd run dev
```

访问 <http://127.0.0.1:5173/>。不调用模型的演示模式在启动后端前设置 `$env:FAIT_DEMO='1'`；真实与演示任务默认分开保存。球员数据源由 `FAIT_PLAYER_DATA_MODE/ROOT` 另行配置，默认仍为样本；真实模型运行不表示已经读取PES生涯。

详细说明和验收命令见 [前端运行与验收](docs/guides/运行与验收.md)。
