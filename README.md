# 军事领域数据制备平台

军事领域训练数据制备系统，覆盖战前准备、战时保障、战后复盘、多军兵种/专业条线以及有人、无人和有人/无人协同资料治理。系统提供来源资产登记、军事场景标签、审核队列、数据集草稿、审计事件和军事风格态势看板。

当前版本已提供可运行闭环：资产登记与撤回、原件哈希校验、文本/多模态解析器契约、PII 与去重、可配置质量门、证据样本、数据契约、数据集分割与 JSONL 导出、GRPO episode、评测隔离、血缘和 OpenLineage 生命周期。后续增强项仍以 [建设 TODO](docs/军事领域数据制备系统-建设TODO.md) 为准。

## 快速启动

```bash
docker compose up -d --build
# 浏览器访问 http://localhost:8000
```

宿主机端口可配置：

```bash
APP_PORT=18000 docker compose up -d --build
```

生产模式启用身份和角色校验：

```bash
APP_ENV=production REQUIRE_AUTH=true SECRET_KEY='请替换为随机密钥' APP_PORT=18000 docker compose up -d --build
```

受保护的写接口使用 `X-Actor-Subject` 和 `X-Actor-Role` 请求头；角色包括 `data_admin`、`security_reviewer`、`reviewer`、`data_steward` 和 `training_engineer`。生产部署应通过组织 SSO 或 API 网关注入这些声明，不应由浏览器直接伪造。

API 文档：`/docs`；健康检查：`/api/v1/health`，依赖检查：`/api/v1/health/dependencies`。

导出 JSONL 工件和 manifest 可通过 `/api/v1/exports/{export_id}/download` 下载；生产 trace 只能进入评测快照。解析器能力可通过 `/api/v1/parsers` 查询，质量门通过 `/api/v1/contents/{content_id}/quality-gate` 执行。

## 本地开发

```bash
pip install -r requirements.txt
python -m pytest -q
python -m compileall -q app
```

开发约束：中文注释、所有写操作记录审计、训练数据必须带军事场景上下文，未经授权或命中高风险规则的资料进入隔离状态。
