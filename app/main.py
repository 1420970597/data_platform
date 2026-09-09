"""军事领域数据制备平台 API 与前端入口。"""
from pathlib import Path
import hashlib
import re
import shutil
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .db import Base, engine, get_db
from .config import settings
from .models import AuditEvent, ContentObject, ContractCheck, DataContract, DatasetVersion, DedupeMatch, PackageExport, PIIFinding, QualityAssessment, ReviewTask, SourceAsset
from .schemas import AssetCreate, AssetRead, ContractCreate, ContractRead, DatasetCreate, DatasetRead, ReviewDecision, ReviewRead, ExportRequest

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
    allowed_phases = {"preparation", "wartime_support", "post_event_review", "common"}
    allowed_modes = {"manned", "unmanned", "manned_unmanned_team", "not_applicable"}
    if payload.operation_phase not in allowed_phases:
        raise HTTPException(422, "不支持的军事阶段标签")
    if payload.platform_mode not in allowed_modes:
        raise HTTPException(422, "不支持的平台形态标签")
    duplicate = db.scalar(select(SourceAsset).where(SourceAsset.raw_sha256 == payload.raw_sha256))
    if duplicate:
        raise HTTPException(409, "原件哈希已登记")
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


@app.get("/api/v1/assets/{asset_id}", response_model=AssetRead)
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    """按 ID 获取资产，不返回原始正文。"""
    asset = db.get(SourceAsset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    return asset

@app.post("/api/v1/assets/{asset_id}/withdraw")
def withdraw_asset(asset_id: int, request: Request, db: Session = Depends(get_db)):
    """冻结资产后续导出，并写入撤回审计。"""
    asset = db.get(SourceAsset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    asset.lifecycle_status = "WITHDRAWN"
    actor = request.headers.get("X-Actor-Subject", "unknown")
    db.add(AuditEvent(action="asset.withdrawn", entity_type="source_asset", entity_id=str(asset_id), actor_subject=actor, purpose="withdrawal", reason="人工撤回"))
    db.commit()
    return {"asset_id": asset_id, "lifecycle_status": asset.lifecycle_status}

@app.post("/api/v1/reviews/{review_id}/decision")
def decide_review(review_id: int, payload: ReviewDecision, request: Request, db: Session = Depends(get_db)):
    """完成审核并同步资产状态。"""
    task = db.get(ReviewTask, review_id)
    if not task:
        raise HTTPException(404, "审核任务不存在")
    task.status = "COMPLETED"
    task.reviewer = request.headers.get("X-Actor-Subject", "reviewer")
    task.notes = payload.notes
    asset = db.get(SourceAsset, task.asset_id)
    if asset:
        asset.lifecycle_status = {"approve": "CANDIDATE", "reject": "QUARANTINED", "quarantine": "QUARANTINED"}[payload.decision]
    db.add(AuditEvent(action="review.decided", entity_type="review_task", entity_id=str(review_id), actor_subject=task.reviewer, purpose="review", reason=payload.decision + ":" + payload.notes))
    db.commit()
    return {"review_id": review_id, "decision": payload.decision, "asset_status": asset.lifecycle_status if asset else None}

@app.post("/api/v1/datasets/{dataset_id}/approve", response_model=DatasetRead)
def approve_dataset(dataset_id: int, request: Request, db: Session = Depends(get_db)):
    """批准数据集草稿；生产环境应在此处接入完整契约与污染门禁。"""
    dataset = db.get(DatasetVersion, dataset_id)
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    if dataset.approval_state not in {"DRAFT", "REVIEWED"}:
        raise HTTPException(409, "数据集状态不允许批准")
    dataset.approval_state = "APPROVED"
    db.add(AuditEvent(action="dataset.approved", entity_type="dataset_version", entity_id=str(dataset_id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose=dataset.purpose, reason="发布审批"))
    db.commit(); db.refresh(dataset)
    return dataset

@app.post("/api/v1/datasets/{dataset_id}/exports")
def export_dataset(dataset_id: int, payload: ExportRequest, request: Request, db: Session = Depends(get_db)):
    """为已批准数据集生成不可变 manifest 工件，不暴露原始资产。"""
    dataset = db.get(DatasetVersion, dataset_id)
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    if dataset.approval_state != "APPROVED":
        raise HTTPException(409, "数据集尚未批准")
    if payload.purpose != dataset.purpose:
        raise HTTPException(403, "用途声明与数据集用途不匹配")
    export_dir = Path(settings.data_dir) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dataset_id": dataset.id, "name": dataset.name, "purpose": dataset.purpose,
        "export_type": payload.export_type, "format": payload.format,
        "manifest_hash": dataset.manifest_hash, "taxonomy_version": dataset.taxonomy_version,
        "sample_count": dataset.sample_count, "coverage_report": dataset.coverage_report,
    }
    artifact = export_dir / f"dataset-{dataset_id}-{payload.export_type.lower()}.manifest.json"
    artifact.write_text(__import__("json").dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    export = PackageExport(dataset_id=dataset_id, export_type=payload.export_type, format=payload.format, artifact_uri=str(artifact), artifact_sha256=artifact_hash)
    db.add(export)
    db.add(AuditEvent(action="dataset.exported", entity_type="dataset_version", entity_id=str(dataset_id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose=payload.purpose, reason=payload.export_type + ":" + payload.format))
    db.commit()
    return {"dataset_id": dataset_id, "export_id": export.id, "export_type": payload.export_type, "format": payload.format, "manifest_hash": dataset.manifest_hash, "artifact_sha256": artifact_hash, "artifact_uri": str(artifact), "status": "EXPORTED"}


@app.post("/api/v1/assets/{asset_id}/ingest")
def ingest_asset(asset_id: int, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """保存原件并生成最小内容对象；重型 OCR 由后续 Worker 处理。"""
    asset = db.get(SourceAsset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    if asset.lifecycle_status in {"QUARANTINED", "WITHDRAWN"}:
        raise HTTPException(409, "资产处于隔离或撤回状态，不能导入")
    base = Path(settings.data_dir) / "assets"
    base.mkdir(parents=True, exist_ok=True)
    target = base / str(asset_id)
    target.mkdir(parents=True, exist_ok=True)
    path = target / (file.filename or "upload.bin")
    with path.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != asset.raw_sha256:
        path.unlink(missing_ok=True)
        raise HTTPException(422, "上传原件哈希与登记值不一致")
    asset.raw_object_uri = str(path)
    asset.lifecycle_status = "INGESTED"
    suffix = path.suffix.lower()
    modality = "text" if suffix in {".txt", ".md", ".csv", ".json", ".html"} else "document"
    content = ContentObject(asset_id=asset_id, modality=modality, content_uri=str(path), normalized_sha256=digest, parser_id="ingest-pass-through", parser_version="1.0", parser_confidence=1.0, locator={"filename": file.filename}, status="NORMALIZED")
    db.add(content); db.flush()
    decision = "ALLOW" if asset.sensitivity_tier != "prohibited" else "BLOCK"
    db.add(QualityAssessment(content_id=content.id, decision=decision, scores={"integrity": 1.0, "hash_match": 1.0}, reason="原件哈希校验通过" if decision == "ALLOW" else "敏感等级阻断"))
    db.add(AuditEvent(action="asset.ingested", entity_type="source_asset", entity_id=str(asset_id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose=asset.allowed_use, reason="原件保存与哈希校验"))
    db.commit()
    return {"asset_id": asset_id, "content_id": content.id, "sha256": digest, "status": asset.lifecycle_status, "modality": modality}

@app.get("/api/v1/assets/{asset_id}/contents")
def list_contents(asset_id: int, db: Session = Depends(get_db)):
    """列出资产的标准化内容对象和质量结论。"""
    rows = db.scalars(select(ContentObject).where(ContentObject.asset_id == asset_id).order_by(ContentObject.created_at.desc())).all()
    return [{"id": row.id, "asset_id": row.asset_id, "modality": row.modality, "parser_version": row.parser_version, "confidence": row.parser_confidence, "status": row.status} for row in rows]


@app.get("/api/v1/contracts", response_model=list[ContractRead])
def list_contracts(db: Session = Depends(get_db)):
    """列出数据契约版本。"""
    return list(db.scalars(select(DataContract).order_by(DataContract.created_at.desc())).all())

@app.post("/api/v1/contracts", response_model=ContractRead, status_code=201)
def create_contract(payload: ContractCreate, request: Request, db: Session = Depends(get_db)):
    """创建契约；ACTIVE 契约才能阻断数据集发布。"""
    contract = DataContract(**payload.model_dump())
    db.add(contract); db.flush()
    db.add(AuditEvent(action="contract.created", entity_type="data_contract", entity_id=str(contract.id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose="governance", reason=payload.name + ":" + payload.version))
    db.commit(); db.refresh(contract); return contract

@app.post("/api/v1/datasets/{dataset_id}/contract-check")
def contract_check(dataset_id: int, contract_id: int, request: Request, db: Session = Depends(get_db)):
    """执行基础 schema/freshness/quality 断言并保存结果。"""
    dataset = db.get(DatasetVersion, dataset_id); contract = db.get(DataContract, contract_id)
    if not dataset or not contract: raise HTTPException(404, "数据集或契约不存在")
    observed = {"manifest_hash_length": len(dataset.manifest_hash or ""), "sample_count": dataset.sample_count, "coverage_fields": len(dataset.coverage_report or {})}
    passed = observed["manifest_hash_length"] == 64 and observed["sample_count"] > 0 and observed["coverage_fields"] > 0
    result = "PASS" if passed else "FAIL"
    db.add(ContractCheck(dataset_id=dataset_id, contract_id=contract_id, result=result, observed=observed))
    db.add(AuditEvent(action="dataset.contract_checked", entity_type="dataset_version", entity_id=str(dataset_id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose=dataset.purpose, reason=contract.name + ":" + result))
    db.commit()
    return {"dataset_id": dataset_id, "contract_id": contract_id, "result": result, "observed": observed}


@app.post("/api/v1/contents/{content_id}/pii-scan")
def scan_pii(content_id: int, request: Request, db: Session = Depends(get_db)):
    """对文本内容执行最小化正则扫描；真实环境需替换为经黄金集校准的检测器。"""
    content = db.get(ContentObject, content_id)
    if not content:
        raise HTTPException(404, "内容对象不存在")
    path = Path(content.content_uri)
    text = path.read_text(errors="ignore") if path.exists() and content.modality == "text" else ""
    patterns = {"email": r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "phone": r"(?<!\d)1[3-9]\d{9}(?!\d)", "id_number": r"(?<!\d)\d{17}[0-9Xx](?!\d)"}
    findings = []
    for entity_type, pattern in patterns.items():
        for match in re.finditer(pattern, text):
            finding = PIIFinding(content_id=content_id, entity_type=entity_type, locator={"start": match.start(), "end": match.end()}, confidence=0.98, action="typed_placeholder", review_state="PENDING")
            db.add(finding); findings.append({"entity_type": entity_type, "start": match.start(), "end": match.end()})
    if findings:
        content.status = "REVIEW_PENDING"
        db.add(QualityAssessment(content_id=content_id, decision="REVIEW", scores={"pii_findings": len(findings)}, reason="检测到 PII，等待人工复核"))
    db.add(AuditEvent(action="pii.detected", entity_type="content_object", entity_id=str(content_id), actor_subject=request.headers.get("X-Actor-Subject", "system"), purpose="privacy", reason=f"发现 {len(findings)} 项"))
    db.commit()
    return {"content_id": content_id, "finding_count": len(findings), "findings": findings, "decision": "REVIEW" if findings else "ALLOW"}

@app.post("/api/v1/contents/{content_id}/dedupe")
def dedupe_content(content_id: int, request: Request, db: Session = Depends(get_db)):
    """执行精确哈希去重，命中后隔离当前内容对象。"""
    content = db.get(ContentObject, content_id)
    if not content:
        raise HTTPException(404, "内容对象不存在")
    matches = db.scalars(select(ContentObject).where(ContentObject.normalized_sha256 == content.normalized_sha256, ContentObject.id != content_id)).all()
    result = []
    for match in matches:
        db.add(DedupeMatch(content_id=content_id, matched_content_id=match.id, layer="exact", similarity=1.0, decision="ISOLATE"))
        result.append({"matched_content_id": match.id, "layer": "exact", "similarity": 1.0})
    if result:
        content.status = "QUARANTINED"
        db.add(QualityAssessment(content_id=content_id, decision="BLOCK", scores={"exact_duplicate": 1.0}, reason="命中精确重复"))
    db.add(AuditEvent(action="dedupe.completed", entity_type="content_object", entity_id=str(content_id), actor_subject=request.headers.get("X-Actor-Subject", "system"), purpose="quality", reason=f"精确命中 {len(result)} 项"))
    db.commit()
    return {"content_id": content_id, "matches": result, "decision": "ISOLATE" if result else "ALLOW"}
