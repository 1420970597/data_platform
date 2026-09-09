# 军事领域数据制备平台

军事领域训练数据制备系统，覆盖战前准备、战时保障、战后复盘、多军兵种/专业条线以及有人、无人和有人/无人协同资料治理。系统提供来源资产登记、军事场景标签、审核队列、数据集草稿、审计事件和军事风格态势看板。

当前分支完成基础可运行骨架，后续按 [建设 TODO](docs/军事领域数据制备系统-建设TODO.md) 逐阶段实现解析、质量、数据契约、LoRA/GRPO 导出、撤回和运营能力。

## 快速启动

```bash
docker compose up -d --build
# 浏览器访问 http://localhost:8000
```

宿主机端口可配置：

```bash
APP_PORT=18000 docker compose up -d --build
```

API 文档：`/docs`；健康检查：`/api/v1/health`。

## 本地开发

```bash
pip install -r requirements.txt
python -m pytest -q
python -m compileall -q app
```

开发约束：中文注释、所有写操作记录审计、训练数据必须带军事场景上下文，未经授权或命中高风险规则的资料进入隔离状态。
