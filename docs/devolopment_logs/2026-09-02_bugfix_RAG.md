# FootballAI Career Agent 开发日志 · 第 1 期 / v1.1
*日志日期：2026-09-02 | 编写人：Claude Code*

---

## 1. 上下文快照（必填）
- **分支 / Tag**：`main`
- **依赖变动**：是
  - `torch`: 未安装 -> `2.13.0+cpu`（官方 CPU 源）
  - `FlagEmbedding`: 未安装 -> `1.4.2`（bge 系列加载）
  - `modelscope`: 未安装 -> `1.39.1`（国内模型下载）
  - `langchain-chroma`: 未安装 -> `1.1.0`
  - `pypdf`: 未安装 -> `6.16.2`（PDF 解析，最终采用）
  - `pymupdf`: 未安装 -> `1.28.2`（仅兼容性测试，最终未使用）
- **关联文档/Issue**：`plan.md`、`task.md`、`FootballAI Career Agent 架构升级任务书.md`

---

## 2. 本期摘要

本次改造聚焦 RAG 检索质量全链路。核心是修复三处硬伤：DeepSeek 不提供 embedding 接口导致索引无法构建、PDF 因 Git LFS 指针未拉取而全部加载失败、GBK 控制台打印含 ™ 文件名崩溃。随后实现本地化两阶段检索（bge-m3 embedding + bge-reranker-v2-m3 精排），配合分类型 splitter，并收敛工具分组、删除硬编码搜索回退、打通 citations 来源传递。最后新增评估模块，量化验证 rerank 使 MRR 提升 19.5%、nDCG@5 提升 15.9%，hit_rate@5 达 73.3%。

---

## 3. 核心内容详解

### 3.1 架构与设计决策

- **Embedding 选型**：原 `_get_embedding_model()` 使用 OpenAI `text-embedding-3-small`，但 `OPENAI_BASE_URL` 指向 DeepSeek，而 DeepSeek 不提供 embedding 接口（实测 404）。决策：改用本地多语言模型 `bge-m3`（1024 维），经 ModelScope 下载到 `models/`，无 API 依赖、可离线。
- **两阶段检索**：放弃单一 Top-5 向量检索，改为「过度召回 20 → BGE 交叉编码精排 5」。设计上保留三重降级路径（未启用/文档不足/模型异常时回退为原始相似度 top-5），保证与旧行为兼容。
- **分类型 splitter**：`knowledge/` 语料中 `.txt` 以中文为主、`.pdf` 以英文为主，共用一套分隔符会切坏语义。决策：按来源扩展名分派不同分块器，`.txt` 优先中文标点、`.pdf` 优先英文句点，chunk_size 500→900 减少上下文截断。
- **模型分发策略**：本地目录优先（`RERANK_MODEL_DIR` / `EMBEDDING_MODEL_DIR`），HF 联网兜底，国内走 ModelScope 下载。
- **citations 数据流**：`retrieve_docs()` 返回结构化 `Document`（保留 `source`/`category` 元数据）；`FootballKnowledgeRAG` 在精排后记录引用到模块级；Coach 输出 `references` 字段并回写 `state.citations`，`AgentState` 新增 `citations` 字段（`merge_lists` reducer）。
- **工具分组收敛**：`tools/__init__.py` 的 `COACH_TOOLS` 等原为死代码，与实际 Agent 内联工具不一致。决策：统一为单一事实源，各 Agent 引用分组。

### 3.2 关键代码实现详解

**改动点 A：本地 Embedding 懒加载包装（`tools/rag.py`）**
- **意图**：替代对 DeepSeek 的 OpenAI embedding 调用（404），改为本地 bge-m3。
- **关键代码**：
  ```python
  # ❌ 之前：调用 DeepSeek 上的 text-embedding-3-small -> openai.NotFoundError 404
  # ✅ 现在：
  class _LocalEmbeddings(Embeddings):
      def embed_query(self, text):
          vectors = self._ensure_model().encode([text], max_length=512)
          return vectors[0].tolist()
  ```

**改动点 B：两阶段检索 + 引用记录（`tools/rag.py`）**
- **意图**：过度召回后用 BGE reranker 精排，并记录结构化引用。
- **关键代码**：
  ```python
  docs = retrieve_docs(query, k=config.RAG_RETRIEVAL_K)   # 召回 20
  docs = rerank_documents(query, docs, config.RAG_TOP_K)  # 精排 5
  _set_last_citations(docs)                               # 记录引用
  ```

**改动点 C：GBK 编码崩溃修复（`app.py`）**
- **意图**：Windows GBK 控制台无法编码 ™ 等字符导致打印崩溃。
- **关键代码**：
  ```python
  for _stream in (sys.stdout, sys.stderr):
      try:
          _stream.reconfigure(encoding="utf-8", errors="replace")
      except Exception:
          pass
  ```

---

## 4. 调试踩坑时间线

| 轮次 | 我的操作 / 触发条件 (Action) | 系统报错 / 观察结果 (Observation) | 最终修正决策 (Decision) |
| :--- | :--- | :--- | :--- |
| 1 | 安装 CPU 版 torch | 下载断流 `IncompleteRead(7.4MB/94MB)` | 加 `--retries 10 --timeout 300` 重试成功 |
| 2 | 清华镜像安装 FlagEmbedding / modelscope / langchain-chroma | `from versions: none`（镜像未同步这些包） | 分别换默认 PyPI / 阿里云镜像 |
| 3 | 用 hf-mirror 下载 reranker 模型 | `/resolve` 请求 308 重定向回 `huggingface.co`，大文件仍走被墙的 LFS CDN | 改 ModelScope 本地下载 |
| 4 | FlagReranker 设 `use_fp16=True` | CPU-only torch 不支持 fp16 | 改 `use_fp16=False` |
| 5 | 构建索引 | 全部 PDF `Stream has ended unexpectedly` / `as type pdf` | 排查发现文件是 Git LFS 指针（`version https://git-lfs...`），`git lfs pull` 后 pypdf 正常加载 28/28 |
| 6 | 打印含 ™ 文件名 | `UnicodeEncodeError: 'gbk' codec can't encode '™'` | app.py 顶部重配置 stdout/stderr 为 UTF-8 |
| 7 | 构建索引 | `ModuleNotFoundError: langchain_chroma` / `pypdf` | 补装缺失依赖 |
| 8 | 最小 embedding 测试 | DeepSeek 返回 404（无 embedding 模型） | 弃用 OpenAI embedding，换本地 bge-m3 |

---

## 5. 下一步计划

- **明确待办（Todo）**：
  1. 更新 `实验报告.md`，把 rerank 评估指标（hit_rate/recall/MRR/nDCG）写入「评测指标展示及分析」章节
  2. 完整跑一遍 `python app.py`，验证多 Agent 端到端 + citations references 落地
  3. 考虑为检索结果做 MMR 去重，缓解同一来源重复出现的现象
- **❗️ 阻塞项 / 待确认疑点（Blockers）**：
  - 评估集 4 条 MISS 的预期来源本身篇幅短、在索引中占比低（如 `ACSM_Exercise_Fluid_Replacement` 仅 1 页），数据集标注可能偏严，待确认是否调整
  - `pymupdf` 已安装但最终未使用，是否移除该依赖待确认

---

## 6. 开发随笔 

> 因为一开始这个协作系统只是我的人工智能课程的大作业，所以有很多地方的细节都没做好。这个 RAG 的问题也是最近我在重看的时候发现的，在之前的开发中把注意力都放在调整 prompt 和 agent 具体的流程上了。
> 
> 现在开发都专门挑梁文谷时间了，没办法，之前薅学校的羊毛报销 token，全部都充到 DeepSeek 了，还开了发票。早知道应该也拿来买一些其他模型的。

---
