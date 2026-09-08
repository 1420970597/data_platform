# Controlled Data MVP

本地、仅标准库实现的训练数据治理 MVP。它只处理具备授权的公开、非敏感、非作战化知识文本；不提供行动建议、目标选择、武器、战术或实时情报处理能力。

## 运行

要求 Python 3.10 或更高版本，默认使用 Python 标准库和 SQLite，无第三方依赖。当前回归和 HTTP 烟测均在 SQLite 上完成；代码保留了可选 psycopg DB-API 适配路径，但 PostgreSQL 实例集成不属于本轮已验证范围。

```bash
python -m controlled_data_mvp --host 127.0.0.1 --port 8080 --dsn ./runtime/mvp.sqlite3
```

服务默认绑定回环地址。运行后可访问：

```text
GET  /health
GET  /v1/audit/verify
POST /v1/assets
POST /v1/assets/{asset_id}/pii-findings
POST /v1/contracts
POST /v1/datasets
POST /v1/reward-specs
POST /v1/episodes
POST /v1/episodes/{episode_id}/replay
POST /v1/datasets/{dataset_id}/exports
```

所有写请求的 JSON 对象必须带 `actor` 和允许的 `purpose`：`training_data_preparation`、`quality_review` 或 `evaluation`。

## 本地演示

终端 A 启动服务。终端 B 登记示例资产：

```bash
curl -X POST http://127.0.0.1:8080/v1/assets -H "Content-Type: application/json" --data @examples/public_non_sensitive_asset.json
```

示例仅包含公开、非敏感的文档元数据知识，不包含个人信息或任何作战化内容。

## 策略门禁

- 无授权、未允许的用途、敏感等级或禁止领域词汇会使资产状态为 `BLOCKED`。
- 未解决 PII 发现项会阻断数据集冻结。
- 数据集冻结同时验证 schema、freshness 和质量断言；`ACTIVE` 契约失败时返回 `403 policy_blocked`，`PENDING` 契约只记录观察结果。
- 只有 `FROZEN` 数据集可以导出 `lora` 或 `grpo` manifest。
- 审计事件使用哈希链；`GET /v1/audit/verify` 可验证完整性。
- 导入、冻结和导出写入 OpenLineage 兼容生命周期事件，事件只保存标识、版本与最小元数据。

## 测试

在仓库根目录执行：

```bash
python -m unittest discover -s tests -v
```

测试覆盖：授权阻断、非作战范围阻断、未解决 PII 阻断、数据契约、冻结 LoRA manifest、GRPO RewardSpec 与回放、审计哈希链，以及 START/COMPLETE 血缘事件。
