# FootballAI Career Agent

基于 LangGraph 的足球运动员职业生涯多智能体协作系统。输入你的需求，多个 AI Agent 自动分工协作，生成专业的训练计划、营养方案、职业分析或公关声明。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 API Key（编辑 .env 文件）
OPENAI_API_KEY=your-deepseek-or-openai-key
OPENAI_BASE_URL=https://api.deepseek.com/v1
MODEL_NAME=deepseek-chat
TAVILY_API_KEY=your-tavily-key   # 可选，用于联网搜索

# 3. 运行
python app.py
```

## 它能做什么

| 场景 | 示例输入 | 涉及的 Agent |
|------|---------|-------------|
| 训练计划 | "三个月后参加大学联赛，想提升爆发力和减重" | Coach + Nutrition + Analyst |
| 营养方案 | "帮我制定一份增肌期的饮食计划" | Nutrition |
| 表现分析 | "分析我最近的训练效果和比赛表现" | Analyst |
| 职业规划 | "我适合踢英超还是德甲" | Career |
| 公关声明 | "帮我回应媒体关于转会皇马的传闻" | Document |
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
- 球员数据库读写（档案、训练史、比赛史）

**质量保障闭环**：Reviewer 审查 → 不通过 → Manager 重新规划（最多3轮）→ Agent 重新执行 → 再次审查

**人工审核点**：生成最终报告前暂停，可查看中间结果并选择继续 / 重新规划 / 退出。

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
├── knowledge/          # 足球专业文献（PDF/TXT）
└── utils/
    ├── helpers.py      # 工具函数
    └── sessions.py     # 会话管理
```

## 命令行用法

```bash
python app.py                              # 交互模式，新会话
python app.py "帮我制定训练计划"            # 直接输入需求
python app.py --list                       # 列出历史会话
python app.py --continue <thread_id>       # 恢复历史会话
```

## 多轮对话

系统支持会话持久化，可以追问：

```
第1轮: "三个月后参加大学联赛，想提升爆发力"
第2轮: "最近训练后膝盖有点不舒服，调整一下计划"    # Agent 会基于上下文调整
第3轮: "顺便帮我看看现在的市场价值"
```

## 依赖

- Python 3.10+
- LangGraph + LangChain
- DeepSeek API（或 OpenAI 兼容接口）
- ChromaDB（向量检索）
- Tavily API（可选，联网搜索）
