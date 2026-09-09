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
