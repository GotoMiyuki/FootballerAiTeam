# FootballAI Career Agent

基于 LangGraph 的足球运动员职业生涯多智能体协作系统。输入你的需求，多个 AI Agent 自动分工协作，生成专业的训练计划、营养方案、职业分析或公关声明。

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

## 它能做什么

包括但不限于
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

**ReAct 推理循环** — 每个 Agent 内置 Think → Act → Observe → Finish 循环，自主决定何时调用工具、何时输出结果。

**两种记忆机制**：
- 短期记忆：滑动窗口（默认5条），保持上下文连贯
- 长期记忆：球员档案、训练/比赛/职业历史持久化到 JSON 文件，支持跨会话读取

**四种工具**：
- RAG 知识库检索（ChromaDB + 足球专业文献）
- 联网搜索引擎（Tavily API）
- 运动营养计算器（BMI / BMR / TDEE / 宏量营养素）
- 球员快照只读访问（档案、训练史、比赛史）；事实更新使用独立的受控观察导入接口

**质量保障闭环**：Reviewer 审查 → 不通过 → Manager 重新规划（最多3轮）→ Agent 重新执行 → 再次审查

**人工审核点**：生成最终报告前暂停，可查看中间结果并选择继续 / 重新规划 / 退出。

**数据与交付边界（2026-10-01）**：预测只作为建议，不写入球员事实。API、Graph 和工具使用同一 Repository 快照；真实档案须显式配置 `FAIT_PLAYER_DATA_MODE=actual` 与独立的 `FAIT_PLAYER_DATA_ROOT`，缺数据时不回退样本。模型、工具、输出结构或审查失败均不生成固定答案。最终交付要求当前专业成果通过审查、正文通过确定性检查；正文尚未进行全文语义审查。

已完成开发统一见 [路线图开发汇总](docs/completed/README.md)。数据配置与导入见 [A 汇总](docs/completed/A_球员数据与事实边界.md)，执行与审查契约见 [B 汇总](docs/completed/B_执行协作与审查正确性.md)。

**历史与续跑边界（2026-10-02，C1）**：查看历史只读；解释旧报告沿用原任务材料与数据版本，解释失败不撤回报告。Web 与 CLI 的缺输入、报告审批从原 SQLite checkpoint 续跑；原快照或 checkpoint 缺失时拒绝恢复。重新评估或失败重试创建有关联的新任务，提交时冻结同一球员的最新快照与选定历史；请求去重与完整输入持久化，新任务失败不覆盖原报告。详情见 [C1 会话恢复与任务延续汇总](docs/completed/C_C1_会话恢复与任务延续.md)。

**建议来源与适用性（2026-10-02，E1.1）**：工作台可查看已验证、当前有效且通过版本审查的训练焦点建议，保留原任务/成果版本、固定基线和条件。重复投影防重，最新数据变化时标待重评，失效来源保留历史但退出当前状态。游戏操作仍待验证，缺少指标与窗口时不计算效果；选择与执行反馈将在 E1.2 实现。接口、数据边界和验收见 [E1.1 建议来源与适用性汇总](docs/completed/E_E1.1_建议来源与适用性.md)。

## 项目结构

```
footballerAITeam/
├── app.py              # 主入口，CLI 交互
├── graph.py            # LangGraph 状态图定义与路由
├── config.py           # 配置（API Key、路径）
├── registry.py         # Agent 注册中心（新增Agent只需在此添加）
├── agents/
│   ├── base.py         # Agent 基类（ReAct 循环 + 短期记忆）
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
├── memory/             # 持久化数据（球员档案、历史记录）
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
# Web 工作台 V0.1

根据 `docs/FootballerAiTeam_Frontend_V0.1_开发指南.md` 新增 React + TypeScript + Vite 前端及 FastAPI Application Layer。现有 CLI 继续独立运行。

安装 Web 依赖：

```powershell
& .\venv\Scripts\python.exe -m pip install -r requirements-web.txt
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

访问 <http://127.0.0.1:5173/>。不调用模型的演示模式在启动后端前设置 `$env:FAIT_DEMO='1'`；真实与演示任务默认分开保存。

详细说明和验收命令见 [前端运行与验收](docs/Frontend_V0.1_运行与验收.md)。
