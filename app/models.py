"""核心领域模型，字段与建设 TODO 中的场景上下文保持一致。"""
from datetime import datetime, timezone
from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

class SourceAsset(Base):
    __tablename__ = "source_assets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(200))
    source_uri: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(50))
    license_id: Mapped[str] = mapped_column(String(100))
    allowed_use: Mapped[str] = mapped_column(String(100))
    sensitivity_tier: Mapped[str] = mapped_column(String(50), default="authorized")
    military_scope: Mapped[str] = mapped_column(String(80), default="military_training_readiness")
    operation_phase: Mapped[str] = mapped_column(String(40), default="preparation")
    service_domains: Mapped[list] = mapped_column(JSON, default=list)
    platform_mode: Mapped[str] = mapped_column(String(40), default="not_applicable")
    human_authority: Mapped[str] = mapped_column(String(200))
    raw_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    raw_object_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String(40), default="REGISTERED")
    retention_until: Mapped[str | None] = mapped_column(String(40), nullable=True)
    owner_subject: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(100))
    actor_subject: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class ReviewTask(Base):
    __tablename__ = "review_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(20), default="medium")
    task_type: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class DatasetVersion(Base):
    __tablename__ = "dataset_versions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    purpose: Mapped[str] = mapped_column(String(30))
    manifest_hash: Mapped[str] = mapped_column(String(64))
    taxonomy_version: Mapped[str] = mapped_column(String(80), default="military-scenarios-v1")
    approval_state: Mapped[str] = mapped_column(String(30), default="DRAFT")
    sample_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage_report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ContentObject(Base):
    """标准化内容对象，保留解析版本和源定位。"""
    __tablename__ = "content_objects"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(Integer, index=True)
    modality: Mapped[str] = mapped_column(String(40))
    language: Mapped[str | None] = mapped_column(String(20), nullable=True)
    content_uri: Mapped[str] = mapped_column(String(500))
    normalized_sha256: Mapped[str] = mapped_column(String(64))
    parser_id: Mapped[str] = mapped_column(String(80))
    parser_version: Mapped[str] = mapped_column(String(40))
    parser_confidence: Mapped[float] = mapped_column(default=0.0)
    locator: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="NORMALIZED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class QualityAssessment(Base):
    """质量与政策评分。"""
    __tablename__ = "quality_assessments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(Integer, index=True)
    decision: Mapped[str] = mapped_column(String(30))
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    rule_version: Mapped[str] = mapped_column(String(60), default="quality-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class DataContract(Base):
    """数据契约及其版本化断言。"""
    __tablename__ = "data_contracts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(40))
    lifecycle_state: Mapped[str] = mapped_column(String(20), default="PENDING")
    schema_assertions: Mapped[dict] = mapped_column(JSON, default=dict)
    freshness_assertions: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_assertions: Mapped[dict] = mapped_column(JSON, default=dict)
    failure_policy: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class ContractCheck(Base):
    """数据集执行契约后的不可变结果。"""
    __tablename__ = "contract_checks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(Integer, index=True)
    contract_id: Mapped[int] = mapped_column(Integer, index=True)
    result: Mapped[str] = mapped_column(String(20))
    observed: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class PIIFinding(Base):
    """PII 检测发现项，默认不自动放行高敏结果。"""
    __tablename__ = "pii_findings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(Integer, index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    locator: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(default=0.0)
    detector_version: Mapped[str] = mapped_column(String(50), default="regex-v1")
    action: Mapped[str] = mapped_column(String(30), default="quarantine")
    review_state: Mapped[str] = mapped_column(String(30), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class DedupeMatch(Base):
    """文本/视觉去重命中记录。"""
    __tablename__ = "dedupe_matches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(Integer, index=True)
    matched_content_id: Mapped[int] = mapped_column(Integer)
    layer: Mapped[str] = mapped_column(String(20))
    similarity: Mapped[float] = mapped_column(default=1.0)
    detector_version: Mapped[str] = mapped_column(String(50), default="exact-v1")
    decision: Mapped[str] = mapped_column(String(20), default="ISOLATE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class PackageExport(Base):
    """已批准数据集的不可变导出工件登记。"""
    __tablename__ = "package_exports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[int] = mapped_column(Integer, index=True)
    export_type: Mapped[str] = mapped_column(String(20))
    format: Mapped[str] = mapped_column(String(20))
    artifact_uri: Mapped[str] = mapped_column(String(500))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="EXPORTED")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class EvidenceSpan(Base):
    """可定位的证据片段。"""
    __tablename__ = "evidence_spans"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(Integer, index=True)
    locator: Mapped[dict] = mapped_column(JSON, default=dict)
    text_or_region: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class TrainingSample(Base):
    """证据约束监督样本。"""
    __tablename__ = "training_samples"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_type: Mapped[str] = mapped_column(String(40))
    input_refs: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    target: Mapped[dict] = mapped_column(JSON, default=dict)
    scenario_context: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="CANDIDATE")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class RewardSpec(Base):
    """可回放的 GRPO 奖励规范。"""
    __tablename__ = "reward_specs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    version: Mapped[str] = mapped_column(String(40))
    reward_weights: Mapped[dict] = mapped_column(JSON, default=dict)
    thresholds: Mapped[dict] = mapped_column(JSON, default=dict)
    hard_constraints: Mapped[list] = mapped_column(JSON, default=list)
    rollout_config: Mapped[dict] = mapped_column(JSON, default=dict)
    lifecycle_state: Mapped[str] = mapped_column(String(20), default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class GRPOEpisode(Base):
    """提示、候选组、验证结果和奖励证据。"""
    __tablename__ = "grpo_episodes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prompt: Mapped[str] = mapped_column(Text)
    context_refs: Mapped[list] = mapped_column(JSON, default=list)
    candidate_group: Mapped[list] = mapped_column(JSON, default=list)
    verifier_results: Mapped[list] = mapped_column(JSON, default=list)
    reward_vector: Mapped[dict] = mapped_column(JSON, default=dict)
    scalar_reward: Mapped[float] = mapped_column(default=0.0)
    reward_spec_id: Mapped[int] = mapped_column(Integer)
    scenario_context: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="VERIFIED")
    replay_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class LineageEdge(Base):
    """跨资产、内容、样本、数据集和运行的血缘边。"""
    __tablename__ = "lineage_edges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_type: Mapped[str] = mapped_column(String(60), index=True)
    parent_id: Mapped[str] = mapped_column(String(120), index=True)
    child_type: Mapped[str] = mapped_column(String(60), index=True)
    child_id: Mapped[str] = mapped_column(String(120), index=True)
    relation: Mapped[str] = mapped_column(String(80))
    transform_version: Mapped[str] = mapped_column(String(80), default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

class ProductionTrace(Base):
    """生产反馈脱敏后的评测候选，禁止直接进入训练。"""
    __tablename__ = "production_traces"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_ref: Mapped[str] = mapped_column(String(160), unique=True)
    risk_tier: Mapped[str] = mapped_column(String(20))
    redaction_profile: Mapped[str] = mapped_column(String(80))
    prompt_redacted: Mapped[str] = mapped_column(Text)
    output_redacted: Mapped[str] = mapped_column(Text)
    target_eval_snapshot: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(30), default="EVAL_ONLY")
    approved_for_training: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
