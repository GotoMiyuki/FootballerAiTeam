"""RAG 评估数据集。

每条样本：(query, expected)
- query: 检索查询（中文或英文）
- expected: 预期命中的来源文件名子串列表（命中任一即视为相关）

expected 中的子串对应 knowledge/ 目录下的实际文件名，用于计算 hit_rate/recall/MRR/nDCG。
"""

EVAL_DATASET = [
    {
        "query": "边锋速度训练方法",
        "expected": ["FIFA Youth Football Training Manual", "Fifa-Coaching-Manual", "国际足球科技动态"],
    },
    {
        "query": "射门技术训练 shooting drills",
        "expected": ["FIFA Youth Football Training Manual", "Fifa-Coaching-Manual"],
    },
    {
        "query": "赛前碳水加载策略",
        "expected": ["ISSN_Nutrient_Timing", "Portuguese_FF_Consensus_Nutrition", "4R_Nutrition_Strategies"],
    },
    {
        "query": "运动补液 电解质 钠补充",
        "expected": ["ACSM_Exercise_Fluid_Replacement"],
    },
    {
        "query": "腘绳肌拉伤预防",
        "expected": ["coach-4.4.5", "NSCA"],
    },
    {
        "query": "急性慢性负荷比 ACWR 监测",
        "expected": ["NSCA_Match_Load", "coach-4.4.5"],
    },
    {
        "query": "青年球员长期发展 LTAD 模型",
        "expected": ["CFA_Youth_Training_Outline", "深入剖析长期运动员发展"],
    },
    {
        "query": "守门员专项训练方法",
        "expected": ["FIFA_Futsal_Goalkeeper_Manual", "FIFA Youth Football Training Manual"],
    },
    {
        "query": "五人制足球 体能训练",
        "expected": ["FIFA_Futsal_Fitness_Manual"],
    },
    {
        "query": "反应速度与敏捷性训练",
        "expected": ["反应速度", "敏捷性训练", "Acceleration and Deceleration Mechanics"],
    },
    {
        "query": "赛后肌肉恢复与营养",
        "expected": ["Portuguese_FF_Consensus_Nutrition", "4R_Nutrition_Strategies"],
    },
    {
        "query": "臀肌激活与热身",
        "expected": ["activation-of-the-gluteus-maximus"],
    },
    {
        "query": "有氧耐力训练策略",
        "expected": ["Aerobic Endurance Training Strategies"],
    },
    {
        "query": "足球比赛负荷监测 match load",
        "expected": ["NSCA_Match_Load_Soccer"],
    },
    {
        "query": "球员体能评估 fitness evaluation",
        "expected": ["NSCA_Fitness_Evaluation_Soccer"],
    },
]
