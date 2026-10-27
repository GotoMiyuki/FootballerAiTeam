# 仓库协作约定

文档管理遵循[文档管理规范](docs/guides/文档管理规范.md)，开发日志使用[开发交付日志模板](docs/templates/开发交付日志模板.md)。用户对本轮目标和范围的明确指示优先。

- 每个有明确目标的完整交付批次维护一份日志，覆盖实际提交范围、设计取舍、真实问题、验证和遗留项；同一批补充push不机械拆分多份日志。
- 行为变化同步更新现行guide；版本交付更新CHANGELOG，功能/里程碑验收和专项实验按需形成completed/report，互相链接。
- 区分实际版本与目标版本、已提交与已推送/发布、本轮验证与历史引用。文档补齐不代表已完成新版本发布或额外模型/实机验收。
- 历史审计失败、分项交接、原始证据和preserved-source正文保留原时点。新日志单独创建，不改写旧结论或向保留正文追加记录。
- 交付前运行`python tools/check_docs.py`及`git -c core.whitespace=trailing-space,space-before-tab,cr-at-eol diff --check`；按实际代码影响选择业务检查，文档改动记录文档检查范围。
- 文档发布运行`python tools/check_docs.py --portable`和`python tools/check_version.py`；本机引用仅按docs/local_references.json精确清单单列，不能自动忽略普通失效链接。实际CI结果与私有材料验证分别记录。
- 保留用户已有修改，实际球员/游戏数据、密钥与本地运行材料不随文档操作自动纳入提交。
