"""军事领域数据制备平台 API 与前端入口。"""
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .db import Base, engine, get_db
from .models import AuditEvent, DatasetVersion, ReviewTask, SourceAsset
from .schemas import AssetCreate, AssetRead, DatasetCreate, DatasetRead, ReviewRead

Base.metadata.create_all(bind=engine)
app = FastAPI(title="军事领域数据制备平台", version="0.1.0")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(static_dir / "index.html")

@app.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "military-data-platform"}

@app.get("/api/v1/metrics")
def metrics(db: Session = Depends(get_db)):
    """返回首页态势卡片所需的聚合指标。"""
    return {
        "assets": db.scalar(select(func.count(SourceAsset.id))) or 0,
        "pending_reviews": db.scalar(select(func.count(ReviewTask.id)).where(ReviewTask.status == "PENDING")) or 0,
        "datasets": db.scalar(select(func.count(DatasetVersion.id))) or 0,
        "approved_datasets": db.scalar(select(func.count(DatasetVersion.id)).where(DatasetVersion.approval_state == "APPROVED")) or 0,
    }

@app.get("/api/v1/assets", response_model=list[AssetRead])
def list_assets(db: Session = Depends(get_db)):
    return list(db.scalars(select(SourceAsset).order_by(SourceAsset.created_at.desc())).all())

@app.post("/api/v1/assets", response_model=AssetRead, status_code=201)
def create_asset(payload: AssetCreate, request: Request, db: Session = Depends(get_db)):
    """登记资产；禁止类别在服务端直接隔离。"""
    blocked = payload.military_scope == "prohibited_operational" or payload.sensitivity_tier == "prohibited"
    status = "QUARANTINED" if blocked else "REGISTERED"
    asset = SourceAsset(**payload.model_dump(), lifecycle_status=status)
    db.add(asset); db.flush()
    db.add(AuditEvent(action="asset.registered", entity_type="source_asset", entity_id=str(asset.id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose=payload.allowed_use, reason="高风险输入自动隔离" if blocked else "来源登记"))
    if blocked:
        db.add(ReviewTask(asset_id=asset.id, priority="high", task_type="policy_boundary", notes="命中禁止类别，等待合规审核"))
    db.commit(); db.refresh(asset)
    return asset

@app.get("/api/v1/reviews", response_model=list[ReviewRead])
def list_reviews(db: Session = Depends(get_db)):
    return list(db.scalars(select(ReviewTask).order_by(ReviewTask.created_at.desc())).all())

@app.post("/api/v1/reviews/{review_id}/complete")
def complete_review(review_id: int, request: Request, db: Session = Depends(get_db)):
    task = db.get(ReviewTask, review_id)
    if not task: raise HTTPException(404, "审核任务不存在")
    task.status = "COMPLETED"; task.reviewer = request.headers.get("X-Actor-Subject", "reviewer")
    db.add(AuditEvent(action="review.completed", entity_type="review_task", entity_id=str(review_id), actor_subject=task.reviewer, purpose="review", reason="人工审核完成"))
    db.commit(); return {"status": task.status, "review_id": review_id}

@app.get("/api/v1/datasets", response_model=list[DatasetRead])
def list_datasets(db: Session = Depends(get_db)):
    return list(db.scalars(select(DatasetVersion).order_by(DatasetVersion.created_at.desc())).all())

@app.post("/api/v1/datasets", response_model=DatasetRead, status_code=201)
def create_dataset(payload: DatasetCreate, request: Request, db: Session = Depends(get_db)):
    dataset = DatasetVersion(**payload.model_dump())
    db.add(dataset); db.flush()
    db.add(AuditEvent(action="dataset.created", entity_type="dataset_version", entity_id=str(dataset.id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose=payload.purpose, reason="创建数据集草稿"))
    db.commit(); db.refresh(dataset); return dataset
