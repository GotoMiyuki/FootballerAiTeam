# FootballAI Career Agent 开发任务清单 (TASK.md)

> 该任务清单专为 AI Agent 协作开发设计。请严格按任务编号依次执行，每完成一个任务，可提交并让 AI 进行下一步任务，不要一次性将所有代码发送给 AI，以保证执行效率和准确性。

---

## 阶段一：环境初始化与项目结构搭建 (4 个任务)

**目标**：完成环境配置，创建符合 `plan.md` 第 8 节要求的项目文件结构。

- [ ] **1. 创建项目根目录与虚拟环境**
  - 在本地创建 `FootballAI/` 文件夹，初始化 git 仓库。
  - 创建 Python 虚拟环境，安装基础依赖：`langgraph`, `langchain-core`, `langchain-openai`, `langchain-community`, `chromadb`, `tavily-python`, `python-dotenv`。
- [ ] **2. 配置 API 密钥与环境变量**
  - 在根目录下创建 `.env` 文件，配置大模型 API Key 和 Base URL（如 `OPENAI_API_KEY` 或 `DASHSCOPE_API_KEY`）。
  - 配置 Tavily 搜索 API Key (如果使用 Tavily)。
- [ ] **3. 创建项目文件夹结构**
  - 根据 `plan.md` 第 8 节，创建 `agents/`, `tools/`, `memory/`, `knowledge/`, `prompts/`, `utils/` 文件夹。
  - 创建 `app.py`, `graph.py`, `config.py` 三个主要入口文件。
- [ ] **4. 编写配置文件 `config.py`**
  - 在 `config.py` 中完成大模型（如 DeepSeek V4 或通义千问）的初始化配置。
  - 从 `.env` 读取 API Key 和基础配置。

## 阶段二：核心状态与基础 Agent 类 (3 个任务)

**目标**：定义多智能体系统的标准状态，构建 Agent 基类。

- [ ] **5. 定义 LangGraph 状态类 (`graph.py`)**
  - 在 `graph.py` 中定义 `AgentState` (TypedDict)，包含 `messages` (List), `current_agent` (str), `player_profile` (Dict), `execution_plan` (List) 等必要状态字段。
- [ ] **6. 实现基础 Agent 抽象类 (`agents/base.py`)**
  - 创建 `agents/base.py`，定义 `BaseAgent` 接口，包含 `name`, `role`, `system_prompt` 属性。
  - 实现基础的 `get_context(messages)` 方法，用于封装短期记忆（最近 5 条对话）。
- [ ] **7. 编写所有 Agent 的 System Prompt**
  - 在 `prompts/` 文件夹下创建各 Agent 的提示词文件（或直接在 Python 中定义多行字符串）。
  - 涵盖 Manager, Nutrition, Skill Coach, Performance Analyst, Career Agent, Document Agent 的基础人设和职责说明（参考 `plan.md` 第 3 节）。

## 阶段三：记忆系统实现 (4 个任务)

**目标**：实现“短期记忆”与“长期记忆”两种机制（课程硬性指标）。

- [ ] **8. 实现短期记忆机制 (Short Memory)**
  - 在 `BaseAgent` 中维护一个滑动窗口列表，用于保存当前对话的最近 5-10 条历史消息，不落地到硬盘。
- [ ] **9. 创建球员档案与历史 JSON 数据**
  - 在 `memory/` 文件夹下创建 `player.json`, `training_history.json`, `match_history.json`, `career_history.json` 四个空文件。
  - 在 `player.json` 中填入一个虚拟球员的基础数据结构（如姓名、年龄、身高、体重、位置、能力值等，参考 `plan.md` 第 4 节）。
- [ ] **10. 实现长时记忆读取工具 (`tools/database.py`)**
  - 编写 Python 函数 `read_player_profile()` 和 `update_player_profile()`，用于从 `memory/player.json` 读取和写入球员数据。
- [ ] **11. 实现长时记忆更新逻辑 (Agent 侧)**
  - 在 Nutrition 和 Skill Coach 等 Agent 产生新的计划后，将更新结果通过工具同步保存到对应的 JSON 历史文件中。

## 阶段四：工具系统实现 (5 个任务)

**目标**：开发 4 种工具，并让 Agent 具备调用工具的能力（课程硬性指标，且超过 2 种）。

- [ ] **12. 实现计算器工具 (`tools/calculator.py`)**
  - 实现 BMI、基础代谢率 (BMR)、每日总能量消耗 (TDEE)、蛋白质/碳水/脂肪推荐量的计算工具函数。
  - 使用 `@tool` 装饰器将此函数包装为 LangChain 工具。
- [ ] **13. 实现球员数据库工具 (`tools/database.py`)**
  - 将第 10 步的读写函数，进一步封装为可被 Agent 调用的 `@tool`，例如 `ReadPlayerProfileTool` 和 `UpdatePlayerProfileTool`。
  - **关键点**：确保 `UpdatePlayerProfile` 支持用户**手动修改 position（位置）**。这是实现“位置转型”动态改变的基础。其他的数据则保持由系统改变的特性。
- [ ] **14. 实现联网搜索工具 (`tools/search.py`)**
  - 使用 Tavily API 或 Serper API 封装一个搜索工具。
  - 该工具可接收具体的搜索词，并返回互联网上的实时资讯（供经理或分析师查看最新足球战术趋势）。
- [ ] **15. 构建知识库 RAG 环境 (`knowledge/`)**
  - 创建 `knowledge/football/`, `knowledge/nutrition/`, `knowledge/injury/`, `knowledge/career/` 文件夹。
  - 从 FIFA Training Centre, UEFA Coaching 等公开渠道下载或收集几份 PDF/Markdown 文档（如基础足球训练手册）放入文件夹中。
- [ ] **16. 实现 RAG 检索工具 (`tools/rag.py`)**
  - 编写代码，利用 `langchain_chroma` 或 `FAISS` 将第 15 步的文档进行 Embedding。
  - 建立检索器，并封装为一个 `@tool`，供 Skill Coach 等 Agent 作为“足球知识库”查询使用。

## 阶段五：LangGraph 流程与图构建 (3 个任务)

**目标**：搭建多智能体系统的核心运行框架。

- [ ] **17. 创建 Manager Agent 的编排逻辑 (`agents/manager.py`)**
  - 实现 `Manager` 类的核心方法，使其具备 ReAct 推理能力。
  - 逻辑：接收用户问题 -> 思考 (Thought) -> 决策调用哪个工具或下发给哪个 Agent -> 执行 Action -> 观察反馈 (Observation) -> 循环或输出最终答案（参考 `plan.md` 第 6 节）。
- [ ] **18. 构建 LangGraph 的图结构 (`graph.py`)**
  - 初始化 `StateGraph(AgentState)`。
  - 将 Manager, Nutrition, Skill Coach, Analyst, Career, Document 添加为图中的节点 (`add_node`)。
- [ ] **19. 实现条件边与路由逻辑**
  - 设置入口点为 `Manager`。
  - 添加条件边 (`add_conditional_edges`)：让 `Manager` 根据用户需求，动态判断接下来应该激活哪个 Agent 节点。如果只问饮食，则路由到 Nutrition，否则路由到 Coach 或全流程。

## 阶段六：Agent 业务逻辑实现 (5 个任务)

**目标**：为每个独立节点实现具体的业务处理函数。

- [ ] **20. 实现 Nutrition Agent 节点逻辑**
  - 调用计算器工具，结合 `player.json` 数据，输出具体的每日热量、蛋白质摄入量和一套示例食谱。
- [ ] **21. 实现 Skill Coach 节点逻辑**
  - 调用 RAG 工具检索“足球训练要点”。
  - 结合球员当前能力值，输出一份带周期的“速度/敏捷/控球/射门/耐力”周训练计划表。
- [ ] **22. 实现 Performance Analyst 节点逻辑**
  - 读取 `training_history.json` 和 `match_history.json`，分析成长趋势。
  - 利用 ReAct 逻辑，指出球员能力短板（例如：速度过快但耐力不足），给出下一阶段的训练建议。
- [ ] **23. 实现 Career Agent 节点逻辑（职业经纪人）**
  - 根据球员当前的综合评分，读取 `career_history.json` 历史发展情况。
  - **动态路径判断逻辑**：不要死板地输出单一路径（如大学->中冠->J3）。Agent 需要根据 `player.json` 中的 `position` 和 `age`，以及 `overall` 评分的高低，**动态生成**多条可选路径：
    - 提供**留洋潜力评估**（如“目前身价50万欧，建议前往荷甲或比甲联赛试训”），这里应该单独设置一个函数，根据球员的个人信息判断留洋的概率，概率越高，用户提出留洋的要求越容易实现。
    - 改位置：如果用户输入“我想改踢中后卫”，Agent 需读取 `UpdatePlayerPosition` 的结果，先根据球员目前的个人信息、能力以及年龄（年龄越大越不容易改位置）判断是否合适改位置，并给出中后卫的训练和职业发展路径。
  - 最终输出为结构化的“职业规划建议书”。
- [ ] **24. 实现 Document Agent 节点逻辑（球队文书与媒体公关）**
  - 设定 `Document Agent` 的双重职责：**对内文书** + **对外公关**。
  - **对内文书功能**：接收 Nutrition, Coach, Analyst, Career Agent 提供的原始数据和结论，汇总输出为：周训练计划排版、月总结报告、比赛复盘、提供给球员的用户友好版报告。
  - **对外公关功能**（额外加分项）：根据 Career Agent 的转会谈判结果或突发事件，输出以下内容：
    - 官方新闻稿（针对正式转会、赛事结果）。
    - 社交媒体动态（简短、吸睛的微博/Instagram 文案）。
    - 转会传闻回应话术（当外界出现不实传闻时，提供专业、得体的官方澄清声明）。
  - 请确保 Agent 在最终输出时，根据用户指令或 Manager 的判断，在答案首行明确标识 **【内部报告】** 或 **【对外发布稿】**。


## 阶段七：整合入口与运行调试 (3 个任务)

- [ ] **25. 编写主入口函数 `app.py`**
  - 构建 `if __name__ == "__main__":` 入口。
  - 初始化状态，设置初始用户问题（如：“我需要为下个月的大学联赛做备赛准备”）。
  - 调用 `graph.compile().stream(initial_state)` 运行整个系统。
- [ ] **26. 设置初始虚拟场景测试**
  - 由于没有真实数据，手动在 JSON 文件里生成一个“虚拟球员的 3 个月历史数据”，用于测试 Performance Analyst 分析趋势的能力。
- [ ] **27. 修复多 Agent 通信中的上下文丢失问题**
  - 测试流程，确保 `Manager` 在调用后续 Agent 时，能将 `player.json` 的上下文以及之前的对话历史，顺利、完整地传递给下一个 Agent。

## 阶段八：Prompt 细化与降本优化 (2 个任务)

- [ ] **28. 优化提示词与系统变量 (Prompt Engineering)**
  - 在 `prompts/` 文件夹中对各个 Agent 进行提示词微调，增加输出格式限制（如要求 JSON 格式输出），减少 LLM 幻觉。
- [ ] **29. 增加暂停/断点机制 (Human in the loop)**
  - 在 Manager 做出决策前或 Agent 节点执行前，添加 LangGraph 的断点 (`interrupt_before`)。用于模拟“人类教练”确认后再执行下一步，这会让实验报告更具含金量。

## 阶段九：实验报告与提交准备 (附录)

- [ ] **30. 生成演示 Demo 截图**
  - 将运行控制台输出的完整流程截图保存。
- [ ] **31. 撰写实验报告**
  - 结合代码和运行截图，按照学号_姓名_实验七命名，按照 PPT 要求整理 PDF 实验报告并提交。

---

**给 AI 辅助开发的建议**：
针对这 30 个任务，推荐使用 **“任务列表驱动法”**。每次只让 AI 做 1-2 个独立的小任务，比如：“*我们先从任务 1 开始。请帮我生成创建项目目录和安装必要依赖的命令行指令*”。AI 能准确理解当前所处的开发阶段，不容易出现代码混乱。