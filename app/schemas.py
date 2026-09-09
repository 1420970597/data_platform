"""API 请求和响应模型。"""
from datetime import datetime
from pydantic import BaseModel, Field

class AssetCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=200)
    source_uri: str
    source_type: str = "pdf"
    license_id: str
    allowed_use: str
    sensitivity_tier: str = "authorized"
    military_scope: str = "military_training_readiness"
    operation_phase: str = "preparation"
    service_domains: list[str] = Field(min_length=1)
    platform_mode: str = "not_applicable"
    human_authority: str
    raw_sha256: str = Field(min_length=64, max_length=64)
    retention_until: str | None = None
    owner_subject: str

class AssetRead(AssetCreate):
    id: int
    lifecycle_status: str
    created_at: datetime
    model_config = {"from_attributes": True}

class ReviewRead(BaseModel):
    id: int
    asset_id: int
    priority: str
    task_type: str
    status: str
    reviewer: str | None
    notes: str
    created_at: datetime
    model_config = {"from_attributes": True}

class DatasetCreate(BaseModel):
    name: str
    purpose: str = "LORA"
    manifest_hash: str = Field(min_length=64, max_length=64)
    sample_count: int = 0
    coverage_report: dict = {}

class DatasetRead(DatasetCreate):
    id: int
    taxonomy_version: str
    approval_state: str
    created_at: datetime
    model_config = {"from_attributes": True}

class ReviewDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject|quarantine)$")
    notes: str = ""

class ExportRequest(BaseModel):
    export_type: str = Field(pattern="^(LORA|GRPO)$")
    format: str = Field(default="jsonl", pattern="^(jsonl|parquet)$")
    purpose: str

class ContractCreate(BaseModel):
    name: str
    version: str
    lifecycle_state: str = Field(default="PENDING", pattern="^(PENDING|ACTIVE|RETIRED)$")
    schema_assertions: dict = {}
    freshness_assertions: dict = {}
    quality_assertions: dict = {}
    failure_policy: dict = {}

class ContractRead(ContractCreate):
    id: int
    created_at: datetime
    model_config = {"from_attributes": True}

class EvidenceCreate(BaseModel):
    content_id: int
    locator: dict = {}
    text_or_region: str
    confidence: float = Field(default=1.0, ge=0, le=1)

class SampleCreate(BaseModel):
    task_type: str = Field(pattern="^(extract|cite_qa|compare|summarize|correct|abstain)$")
    input_refs: dict = {}
    evidence_refs: list[int] = Field(min_length=1)
    target: dict
    scenario_context: dict

class RewardSpecCreate(BaseModel):
    name: str
    version: str
    reward_weights: dict
    thresholds: dict = {}
    hard_constraints: list[str] = []
    rollout_config: dict = {}
    lifecycle_state: str = Field(default="DRAFT", pattern="^(DRAFT|ACTIVE|RETIRED)$")

class EpisodeCreate(BaseModel):
    prompt: str
    context_refs: list = []
    candidate_group: list[dict] = Field(min_length=2)
    verifier_results: list[dict] = []
    reward_vector: dict
    scalar_reward: float
    reward_spec_id: int
    scenario_context: dict
