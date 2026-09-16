# FootballAI Career Agent

> 基于 LangGraph 的足球运动员职业生涯多智能体系统
>
> AI Agent Course Project
>
> Framework: LangGraph + LangChain
>
> Model: deepseek V4

---

# 1. Project Goal

构建一个真实的多智能体（Multi-Agent）系统，用于模拟一名足球运动员职业成长过程中，由多个专业团队共同协作，为运动员制定训练计划、饮食计划、职业规划以及成长建议。

本项目重点展示：

- Multi-Agent Collaboration
- Planning / Reasoning
- Memory
- Tool Calling

而不是构建聊天机器人。

---

# 2. Overall Architecture

```
                    User
                     │
                     ▼
            General Manager Agent
                     │
      ┌──────────────┼──────────────┐
      │              │              │
      ▼              ▼              ▼
 Nutrition      Skill Coach     Performance Analyst
      │              │              │
      └──────────────┼──────────────┘
                     │
             Career Agent
                     │
                     ▼
             Document Agent
                     │
                     ▼
                 Final Answer
```

General Manager 负责规划。

其它 Agent 专注自己的专业领域。

---

# 3. Agent Design

## 3.1 General Manager

职责：
- 接收用户目标（如：“我要三个月后参加大学足球联赛...”）
- **意图分析**：解析用户输入，识别核心关键词（如“速度”、“体脂”、“比赛”、“转会”）。
- **动态路由决策**：根据识别到的意图，动态决定调用哪些 Agent。如果涉及多维度（如训练+饮食），则按顺序排列节点。
- **Agent 调用执行**：触发目标 Agent 并传入必要上下文。
- **多 Agent 结果聚合**：接收 Nutrition、Coach、Analyst 等 Agent 的原始输出，进行冲突消解和逻辑串联。
- **最终方案输出**：输出包含完整规划、饮食、训练和职业建议的最终文档。

ReAct 执行流程示例（细化）：
User: "三个月后要参加大学联赛，目前体重有点重，想提升爆发力。"

- **Thought 1（解析任务）**：用户需要“减重”和“爆发力提升”。这涉及到体能和营养。需要分别询问 Skill Coach 和 Nutrition Agent。
- **Action 1**：调用 `Skill Coach` 节点，附加指令“为赛季冲刺阶段制定爆发力提升周训练计划”。
- **Observation 1**：Coach 返回了包含冲刺、敏捷和力量训练的周计划表。
- **Thought 2（评估现状）**：看到了训练强度很高。为了确保身体能承受，需要运动员配合特定的高蛋白饮食和饮食控制。
- **Action 2**：调用 `Nutrition` 节点，附加指令“根据当前体重和 Coach 提供的训练计划，制定减脂增肌饮食方案”。
- **Observation 2**：Nutrition 返回了每日热量和食谱。
- **Thought 3（最终整合）**：训练和饮食都有了。为确保训练计划的高效执行，需要整理成一份可交付给球员的周度方案。
- **Action 3**：调用 `Document Agent` 节点，传入 Coach 和 Nutrition 的输出，指令“整理成中文周报告”。
- **Observation 3**：Document Agent 返回排版整洁的 PDF/文本报告。
- **Final Answer**：将最终报告返回给用户。
- 
## 3.2 Nutrition Agent

## 3.2 Nutrition Agent

职责：
- 分析球员当前的身体数据，制定科学的营养摄入方案。

**输入格式（结构化）**：
从 Player Database 自动加载：
```json
{
  "height": 173,
  "weight": 58,
  "position": "RW",
  "age": 20,
  "training_intensity": "High"  // 根据 Coach 的训练计划估算
}

输出：

{
  "daily_calories": 2800,
  "carbs": 350,
  "protein": 140,
  "fat": 80,
  "meal_plan": [
    {"meal": "Breakfast", "food": "燕麦粥、香蕉、水煮蛋"},
    {"meal": "Lunch", "food": "糙米、烤鸡胸肉、西兰花"},
    {"meal": "Dinner", "food": "红薯、三文鱼、混合蔬菜"}
  ]
}

Tool：

Calculator

内部逻辑：

调用 Calculator Tool 计算 BMI（身体质量指数）、BMR（基础代谢率）。

根据每日训练强度计算 TDEE（每日总能量消耗）。

根据“减脂”或“增肌”的目标，计算每日所需的 Carbohydrate（碳水，g）、Protein（蛋白质，g）、Fat（脂肪，g） 的具体克数。

---

## 3.3 Skill Coach

职责：
- 根据球员当前能力短板，制定针对性的周/月训练计划，涵盖技术、体能、战术意识。

例如：

- Sprint
- Agility
- Defensing
- Driblling
- Awareness
- Ball Control
- Passing
- Shooting
- Recovery
  
等等

**训练维度（细化）**：
- **技术 (Technique)**：Shooting（射门）、Passing（传球）、Dribbling（盘带）、Ball Control（控球）、First Touch（停球）。
- **体能 (Physical)**：Sprint（速度）、Agility（敏捷）、Stamina（耐力）、Core Strength（核心力量）。
- **战术 (Tactical)**：Awareness（空间意识）、Positioning（位置感）、Defensing（防守选位）、Movement off the ball（无球跑动）。

**Tool: Football Knowledge RAG 实现细节**：
- Agent 不通过自身模型记忆生成训练动作，而是：
  - 根据当前目标（如“提升射门”），在系统中生成搜索词（如 "UEFA shooting drills for wingers"）。
  - **调用 Tool 2：RAG 检索工具**。
  - RAG 工具读取 `knowledge/football` 下的官方 PDF 资料，返回 3-5 条具体、有步骤的足球训练动作描述。
  - Coach Agent 吸收这些训练动作后，结合当前球员的能力值，生成包含具体训练频次（如每周 3 次）的训练日程表。

---

## 3.4 Performance Analyst

职责：
- 利用历史数据（JSON 文件）评估球员的长远发展趋势，发现隐性短板和伤病风险。

**逻辑流程**：
1. **读取数据**：加载 `memory/training_history.json`（过去 1 个月的训练记录）和 `memory/match_history.json`（过往比赛数据）。
2. **对比分析**：对比 `player.json` 中当前的能力值与历史记录。
3. **趋势推演**：识别出某项指标是否增长过快（如 Sprint 从 70 涨到 85），并判断这种短期暴涨对其他指标（如 Stamina）的负面影响。
4. **伤病风险评估**：结合 `player.json` 中的 `injury` 历史，分析当前训练量是否超出身体负荷。
5. **输出建议（通过 Manager 发给 Coach）**：给出下一步应该减少/停止的训练项目，以及需要加强的短板项目。

**输出示例**：
"发现 Sprint 能力值 3 周内提升了 15 点，远超同期增长。建议下一周**暂停 Sprint 训练**，将精力转向 **Endurance（耐力）** 和 **Core（核心力量）** 训练，以预防拉伤。"

## 3.5 Career Agent

职责：
- 模拟足球经纪人和职业规划顾问，从长期发展的角度提供职业路径和商业价值建议。

**具体业务逻辑**：
1. **数据评估**：读取 `player.json` 的 `overall` 评分和 `age`。利用 `Career Knowledge`（或联网搜索）判断当前所处的年龄阶段和竞技水平对应的联赛级别。
2. **模拟路径（无需真实球队）**：例如，当前 `overall=72, age=20`，经纪人会建议：
   `大学校队 (总体评价：72)` -> `中冠联赛 (磨练期，预计1年)` -> `中乙联赛 (发展期，预计2年)` -> `J3联赛 (成名期)`。
3. **商业与合同建议**：根据其能力值和曝光度，模拟当前的“市场身价”和“潜在转会费”。
4. **输入给 Document Agent**：输出一份结构化的“球员职业发展与转会策略建议书”。

**输出格式**：
建议书包含 **【短期目标】**、**【长期发展路径】**、**【潜在风险（如：当前是否面临伤病影响身价）】** 三个部分。

## 3.6 Document Agent

职责：
扮演“球队文书”与“媒体公关”的双重角色。负责将其他 Agent 的专业建议转化为可读性强、正式且符合公众传播需求的文档。

**输入源与分类逻辑**：
- 系统赋予此 Agent 一个清晰的**分支判断逻辑**：根据 Manager 传递的指令关键词，决定当前的输出模式。

**模式 A：内部行政文书**
- **触发条件**：输入带有 "训练计划"、"营养"、"报告" 等词。
- **职责**：将 Coach, Nutrition, Analyst 的原始结构化数据（JSON/文本），打包为 Markdown 格式的 **周训练计划**、**月总结报告**、**比赛复盘** 或 **个人行动指南**。
- **输出格式**：纯文本 Markdown 排版，包含项目符号和分段，可直接阅读。

**模式 B：外部媒体公关**
- **触发条件**：输入带有 "转会传闻"、"新闻稿"、"社媒"、"公关" 等词。
- **职责**：
  - **新闻稿**：生成正式、严谨的官方通稿，包含标题、主体内容、公关口径。
  - **社交媒体动态**：生成 1-3 条生动、适合传播的微博/Instagram 文案（带有表情符号和话题标签）。
  - **回应转会传闻**：生成一套得体的澄清声明，涵盖“不实传闻”、“目前无确切进展”、“感谢球迷”三个层级的措辞。
- **输出格式**：文本，但在开头明确标注 **【内部报告】** 或 **【对外发布稿】**，以防 Agent 混淆内部机密与对外信息。

**协作关系**：
- 不对业务数据进行决策，仅作为纯语言处理节点。
- 完成输出后，将结果通过 Manager 返回给用户。

# 4. Memory Design

必须包含：

## Short Memory

Conversation Buffer

例如：

最近5轮聊天。

供Agent理解当前上下文。

---

## Long Memory

Player Profile

保存：

```

player.json

```

例如：

```json
{
    "name":"Alex",

    "age":20,

    "height":173,

    "weight":58,

    "position":"RW",

    "overall":72,

    "speed":82,

    "shooting":71,

    "passing":73,

    "dribbling":80,

    "stamina":75,

    "injury":"None"
}
```

长期保存。

Agent启动时自动读取。

训练结束后自动更新。

---

另外保存：

```

training_history.json

match_history.json

career_history.json

```

---

# 5. Tool Design

至少实现四个工具。

---

## Tool 1

Calculator

用于：

- BMI
- BMR
- TDEE
- Calories
- Protein

---

## Tool 2

Football Knowledge RAG

建立：

knowledge/

例如：

```

knowledge/

football/

nutrition/

injury/

career/

```

资料来源：

- FIFA Training Centre
- FIFA Grassroots
- UEFA Coaching
- Sports Nutrition

使用：

LangChain Retriever

Chroma

或

FAISS

---

## Tool 3

Search Tool

联网搜索：

- 最新训练建议
- 足球规则
- 运动医学

可以调用 Tavily Search。

---

## Tool 4

Player Database

实际上就是：

player.json

Agent通过Tool读取。

例如：

ReadPlayerProfile()

UpdatePlayerProfile()

---

# 6. Planning

Manager 使用 ReAct。

例如：

Thought

↓

Action

↓

Observation

↓

Action

↓

Observation

↓

Final Answer

不要固定调用工具。

应该由LLM决定：

是否调用工具。

调用哪个工具。

调用顺序。

---

# 7. Knowledge Base

建议建立：

```

knowledge/

football/

nutrition/

injury/

career/

```

来源：

官方公开资料：

- FIFA Training Centre

- FIFA Grassroots

- UEFA Coaching

- FIFA Talent Development

无需大量数据。

几十页PDF即可。

---

# 8. Project Structure

```

FootballAI/

│

├── app.py

├── graph.py

├── config.py

│

├── agents/

│ ├── manager.py

│ ├── nutrition.py

│ ├── coach.py

│ ├── analyst.py

│ ├── career.py

│ └── document.py

│

├── tools/

│ ├── calculator.py

│ ├── rag.py

│ ├── search.py

│ └── database.py

│

├── memory/

│ ├── player.json

│ ├── training_history.json

│ ├── match_history.json

│ └── career_history.json

│

├── knowledge/

│ ├── football/

│ ├── nutrition/

│ ├── injury/

│ └── career/

│

├── prompts/

│

├── utils/

│

└── README.md

```

---

# 9. LangGraph Workflow

```
User

↓

Manager

↓

Planning

↓

Nutrition

↓

Coach

↓

Analyst

↓

Career

↓

Document

↓

Manager

↓

Final Answer
```

**动态路由规则（`add_conditional_edges` 实现逻辑）：**
- Manager 节点在完成计划后，生成一个 `next_agents` 队列（例如：`["Nutrition", "Coach"]`）。
- Manager 不会一次性调用所有 Agent。图会根据这个队列**顺序**执行节点。
- 在执行完最后一个指定的 Agent 后，会自动跳转回 Manager 进行 **Aggregation（汇总）**，最后再调用 Document 输出。
- 如果不包含某个节点，Manager 直接将其跳过，或者给无数据的 Agent 传入一个 `skip=True` 的状态变量（避免 Agent 在缺少数据时产生幻觉）。这非常符合课程要求的“**不要固定调用工具**”（不要固定顺序）。

---

# 10. Development Priority

第一阶段：

- 完成LangGraph流程
- Manager调度
- Agent通信

第二阶段：

- Calculator Tool
- Player Database
- Memory

第三阶段：

- RAG
- Search Tool

第四阶段：

- Prompt优化
- UI优化
- Demo测试

---

# 11. Success Criteria

项目应满足课程要求：

✅ 至少 5 个 Agent

✅ LangGraph Workflow

✅ ReAct Planning

✅ Short Memory

✅ Long Memory

✅ 至少 2 种 Tool（推荐实现 4 种）

✅ Agent 间存在协作关系

✅ 模拟真实足球运动员职业成长流程

最终效果应更接近一个"AI 足球运动员团队"，而不是多个独立聊天机器人。