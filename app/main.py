"""军事领域数据制备平台 API 与前端入口。"""
from pathlib import Path
import hashlib
import re
import shutil
import json
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from .db import Base, engine, get_db
from .config import settings
from .models import AuditEvent, ContentObject, ContractCheck, DataContract, DatasetVersion, DatasetSample, DedupeMatch, EvidenceSpan, GRPOEpisode, LineageEdge, LineageRun, PackageExport, PIIFinding, ProductionTrace, QualityAssessment, RewardSpec, ReviewTask, SourceAsset, TrainingSample, TrainingRun
from .schemas import AssetCreate, AssetRead, ContractCreate, ContractRead, DatasetCreate, DatasetRead, EpisodeCreate, EvidenceCreate, ExportRequest, ReviewDecision, ReviewRead, RewardSpecCreate, SampleCreate, TraceCreate, TraceRead, DatasetSampleCreate, LineageRunCreate, QualityGateRequest, TrainingRunCreate
from .parsers import select_parser

Base.metadata.create_all(bind=engine)
app = FastAPI(title="军事领域数据制备平台", version="0.1.0")
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

def actor(request: Request, required_roles: set[str] | None = None) -> str:
    """读取操作者声明；生产模式下校验角色，开发模式允许本地调试。"""
    subject = request.headers.get("X-Actor-Subject", "")
    role = request.headers.get("X-Actor-Role", "")
    if settings.require_auth and (not subject or (required_roles and role not in required_roles)):
        raise HTTPException(403, "缺少有效操作者或角色声明")
    return subject or "development-user"


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
        "eval_only_traces": db.scalar(select(func.count(ProductionTrace.id)).where(ProductionTrace.status == "EVAL_ONLY")) or 0,
        "audit_events": db.scalar(select(func.count(AuditEvent.id))) or 0,
    }

@app.get("/api/v1/assets", response_model=list[AssetRead])
def list_assets(db: Session = Depends(get_db)):
    return list(db.scalars(select(SourceAsset).order_by(SourceAsset.created_at.desc())).all())

@app.post("/api/v1/assets", response_model=AssetRead, status_code=201)
def create_asset(payload: AssetCreate, request: Request, db: Session = Depends(get_db)):
    """登记资产；禁止类别在服务端直接隔离。"""
    subject = actor(request)
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
    db.add(AuditEvent(action="asset.registered", entity_type="source_asset", entity_id=str(asset.id), actor_subject=subject, purpose=payload.allowed_use, reason="高风险输入自动隔离" if blocked else "来源登记"))
    if blocked:
        db.add(ReviewTask(asset_id=asset.id, priority="high", task_type="policy_boundary", notes="命中禁止类别，等待合规审核"))
    db.commit(); db.refresh(asset)
    return asset

@app.get("/api/v1/reviews", response_model=list[ReviewRead])
def list_reviews(db: Session = Depends(get_db)):
    return list(db.scalars(select(ReviewTask).order_by(ReviewTask.created_at.desc())).all())

@app.get("/api/v1/reviews/pending", response_model=list[ReviewRead])
def list_pending_reviews(db: Session = Depends(get_db)):
    """审核工作台只返回未完成任务。"""
    return list(db.scalars(select(ReviewTask).where(ReviewTask.status == "PENDING").order_by(ReviewTask.priority.desc(), ReviewTask.created_at.asc())).all())

@app.get("/api/v1/reviews/{review_id}")
def review_detail(review_id: int, db: Session = Depends(get_db)):
    """返回审核任务及其资产场景标签，供审核工作台展示。"""
    task = db.get(ReviewTask, review_id)
    if not task:
        raise HTTPException(404, "审核任务不存在")
    asset = db.get(SourceAsset, task.asset_id)
    events = db.scalars(select(AuditEvent).where(AuditEvent.entity_type == "review_task", AuditEvent.entity_id == str(review_id)).order_by(AuditEvent.created_at.desc())).all()
    asset_view = None if asset is None else {"id": asset.id, "provider": asset.provider, "source_uri": asset.source_uri, "source_type": asset.source_type, "license_id": asset.license_id, "allowed_use": asset.allowed_use, "sensitivity_tier": asset.sensitivity_tier, "military_scope": asset.military_scope, "operation_phase": asset.operation_phase, "service_domains": asset.service_domains, "platform_mode": asset.platform_mode, "lifecycle_status": asset.lifecycle_status}
    return {"review": {"id": task.id, "status": task.status, "priority": task.priority, "task_type": task.task_type, "reviewer": task.reviewer, "notes": task.notes, "created_at": task.created_at}, "asset": asset_view, "history": [{"action": e.action, "actor_subject": e.actor_subject, "reason": e.reason, "created_at": e.created_at} for e in events]}

@app.post("/api/v1/reviews/{review_id}/complete")
def complete_review(review_id: int, request: Request, db: Session = Depends(get_db)):
    subject = actor(request, {"reviewer", "security_reviewer"})
    task = db.get(ReviewTask, review_id)
    if not task: raise HTTPException(404, "审核任务不存在")
    task.status = "COMPLETED"; task.reviewer = subject
    db.add(AuditEvent(action="review.completed", entity_type="review_task", entity_id=str(review_id), actor_subject=task.reviewer, purpose="review", reason="人工审核完成"))
    db.commit(); return {"status": task.status, "review_id": review_id}

@app.get("/api/v1/datasets", response_model=list[DatasetRead])
def list_datasets(db: Session = Depends(get_db)):
    return list(db.scalars(select(DatasetVersion).order_by(DatasetVersion.created_at.desc())).all())

@app.post("/api/v1/datasets", response_model=DatasetRead, status_code=201)
def create_dataset(payload: DatasetCreate, request: Request, db: Session = Depends(get_db)):
    subject = actor(request)
    dataset = DatasetVersion(**payload.model_dump())
    db.add(dataset); db.flush()
    db.add(AuditEvent(action="dataset.created", entity_type="dataset_version", entity_id=str(dataset.id), actor_subject=subject, purpose=payload.purpose, reason="创建数据集草稿"))
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
    subject = actor(request, {"data_admin", "security_reviewer"})
    asset = db.get(SourceAsset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    asset.lifecycle_status = "WITHDRAWN"
    actor_name = subject
    db.add(AuditEvent(action="asset.withdrawn", entity_type="source_asset", entity_id=str(asset_id), actor_subject=actor_name, purpose="withdrawal", reason="人工撤回"))
    db.commit()
    return {"asset_id": asset_id, "lifecycle_status": asset.lifecycle_status}

@app.post("/api/v1/reviews/{review_id}/decision")
def decide_review(review_id: int, payload: ReviewDecision, request: Request, db: Session = Depends(get_db)):
    """完成审核并同步资产状态。"""
    subject = actor(request, {"reviewer", "security_reviewer"})
    task = db.get(ReviewTask, review_id)
    if not task:
        raise HTTPException(404, "审核任务不存在")
    task.status = "COMPLETED"
    task.reviewer = subject
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
    actor(request, {"data_steward", "security_reviewer"})
    dataset = db.get(DatasetVersion, dataset_id)
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    failed_contract = db.scalar(select(func.count(ContractCheck.id)).where(ContractCheck.dataset_id == dataset_id, ContractCheck.result == "FAIL"))
    if failed_contract:
        raise HTTPException(409, "数据集存在失败的数据契约检查")
    if dataset.approval_state not in {"DRAFT", "REVIEWED"}:
        raise HTTPException(409, "数据集状态不允许批准")
    dataset.approval_state = "APPROVED"
    db.add(AuditEvent(action="dataset.approved", entity_type="dataset_version", entity_id=str(dataset_id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose=dataset.purpose, reason="发布审批"))
    db.commit(); db.refresh(dataset)
    return dataset

@app.post("/api/v1/datasets/{dataset_id}/exports")
def export_dataset(dataset_id: int, payload: ExportRequest, request: Request, db: Session = Depends(get_db)):
    """为已批准数据集生成可训练 JSONL 和不可变 manifest。"""
    subject = actor(request, {"training_engineer", "data_steward"})
    dataset = db.get(DatasetVersion, dataset_id)
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    if dataset.approval_state != "APPROVED":
        raise HTTPException(409, "数据集尚未批准")
    if payload.purpose != dataset.purpose:
        raise HTTPException(403, "用途声明与数据集用途不匹配")
    export_dir = Path(settings.data_dir) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    if payload.format == "parquet":
        raise HTTPException(422, "当前运行时未安装 parquet 编码器，请使用 jsonl")
    import json
    links = db.scalars(select(DatasetSample).where(DatasetSample.dataset_id == dataset_id).order_by(DatasetSample.id)).all()
    samples = []
    for link in links:
        sample = db.get(TrainingSample, link.sample_id)
        if not sample:
            continue
        # 导出样本只包含结构化字段和证据 ID，不暴露原始资产正文。
        samples.append({
            "sample_id": sample.id,
            "split": link.split,
            "task_type": sample.task_type,
            "input_refs": sample.input_refs,
            "evidence_refs": sample.evidence_refs,
            "target": sample.target,
            "scenario_context": sample.scenario_context,
        })
    serialized = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in samples)
    data_artifact = export_dir / f"dataset-{dataset_id}-{payload.export_type.lower()}.jsonl"
    data_artifact.write_text(serialized, encoding="utf-8")
    materialized_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    manifest = {
        "dataset_id": dataset.id, "name": dataset.name, "purpose": dataset.purpose,
        "export_type": payload.export_type, "format": payload.format,
        "manifest_hash": dataset.manifest_hash, "taxonomy_version": dataset.taxonomy_version,
        "sample_count": len(samples), "declared_sample_count": dataset.sample_count,
        "materialized_sha256": materialized_hash, "coverage_report": dataset.coverage_report,
        "splits": {split: sum(1 for row in samples if row["split"] == split) for split in {row["split"] for row in samples}},
    }
    manifest_artifact = export_dir / f"dataset-{dataset_id}-{payload.export_type.lower()}.manifest.json"
    manifest_artifact.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # PackageExport 保持历史语义：下载地址返回 manifest；数据文件通过 data_artifact_uri 提供。
    artifact_hash = hashlib.sha256(manifest_artifact.read_bytes()).hexdigest()
    data_artifact_hash = hashlib.sha256(data_artifact.read_bytes()).hexdigest()
    export = PackageExport(dataset_id=dataset_id, export_type=payload.export_type, format=payload.format, artifact_uri=str(manifest_artifact), artifact_sha256=artifact_hash)
    db.add(export)
    db.add(AuditEvent(action="dataset.exported", entity_type="dataset_version", entity_id=str(dataset_id), actor_subject=subject, purpose=payload.purpose, reason=payload.export_type + ":" + payload.format + f":{len(samples)} samples"))
    db.commit()
    return {"dataset_id": dataset_id, "export_id": export.id, "export_type": payload.export_type, "format": payload.format, "manifest_hash": dataset.manifest_hash, "materialized_sha256": materialized_hash, "artifact_sha256": artifact_hash, "artifact_uri": str(manifest_artifact), "manifest_uri": str(manifest_artifact), "data_artifact_uri": str(data_artifact), "data_artifact_sha256": data_artifact_hash, "sample_count": len(samples), "status": "EXPORTED"}


@app.post("/api/v1/assets/{asset_id}/ingest")
def ingest_asset(asset_id: int, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """保存原件并生成最小内容对象；重型 OCR 由后续 Worker 处理。"""
    asset = db.get(SourceAsset, asset_id)
    if not asset:
        raise HTTPException(404, "资产不存在")
    if asset.lifecycle_status in {"QUARANTINED", "WITHDRAWN"}:
        raise HTTPException(409, "资产处于隔离或撤回状态，不能导入")
    subject = actor(request)
    base = Path(settings.data_dir) / "assets"
    base.mkdir(parents=True, exist_ok=True)
    target = base / str(asset_id)
    target.mkdir(parents=True, exist_ok=True)
    # 仅保留文件名，阻断上传请求中的目录穿越片段。
    safe_name = Path(file.filename or "upload.bin").name
    path = target / safe_name
    with path.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != asset.raw_sha256:
        path.unlink(missing_ok=True)
        raise HTTPException(422, "上传原件哈希与登记值不一致")
    asset.raw_object_uri = str(path)
    asset.lifecycle_status = "INGESTED"
    suffix = path.suffix.lower()
    modality = "text" if suffix in {".txt", ".md", ".csv", ".json", ".html"} else ("image" if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"} else "document")
    content = ContentObject(asset_id=asset_id, modality=modality, content_uri=str(path), normalized_sha256=digest, parser_id="ingest-pass-through", parser_version="1.0", parser_confidence=1.0, locator={"filename": file.filename}, status="NORMALIZED")
    db.add(content); db.flush()
    decision = "ALLOW" if asset.sensitivity_tier != "prohibited" else "BLOCK"
    db.add(QualityAssessment(content_id=content.id, decision=decision, scores={"integrity": 1.0, "hash_match": 1.0}, reason="原件哈希校验通过" if decision == "ALLOW" else "敏感等级阻断"))
    db.add(AuditEvent(action="asset.ingested", entity_type="source_asset", entity_id=str(asset_id), actor_subject=subject, purpose=asset.allowed_use, reason="原件保存与哈希校验"))
    db.commit()
    return {"asset_id": asset_id, "content_id": content.id, "sha256": digest, "status": asset.lifecycle_status, "modality": modality}

@app.get("/api/v1/assets/{asset_id}/contents")
def list_contents(asset_id: int, db: Session = Depends(get_db)):
    """列出资产的标准化内容对象和质量结论。"""
    rows = db.scalars(select(ContentObject).where(ContentObject.asset_id == asset_id).order_by(ContentObject.created_at.desc())).all()
    return [{"id": row.id, "asset_id": row.asset_id, "modality": row.modality, "parser_version": row.parser_version, "confidence": row.parser_confidence, "status": row.status} for row in rows]

@app.get("/api/v1/parsers")
def list_parsers():
    """公开当前解析器插件能力，便于调度器选择 Docling 或 OCR Worker。"""
    from .parsers import PARSER_PLUGINS
    samples = {"text": ".txt", "document": ".pdf", "image": ".png"}
    return [{"parser_id": plugin.parser_id, "version": plugin.parser_version,
             "supported_modalities": [modality for modality, suffix in samples.items() if plugin.supports(modality, suffix)]}
            for plugin in PARSER_PLUGINS]


@app.get("/api/v1/contracts", response_model=list[ContractRead])
def list_contracts(db: Session = Depends(get_db)):
    """列出数据契约版本。"""
    return list(db.scalars(select(DataContract).order_by(DataContract.created_at.desc())).all())

@app.post("/api/v1/contracts", response_model=ContractRead, status_code=201)
def create_contract(payload: ContractCreate, request: Request, db: Session = Depends(get_db)):
    """创建契约；ACTIVE 契约才能阻断数据集发布。"""
    subject = actor(request)
    contract = DataContract(**payload.model_dump())
    db.add(contract); db.flush()
    db.add(AuditEvent(action="contract.created", entity_type="data_contract", entity_id=str(contract.id), actor_subject=subject, purpose="governance", reason=payload.name + ":" + payload.version))
    db.commit(); db.refresh(contract); return contract

@app.post("/api/v1/datasets/{dataset_id}/contract-check")
def contract_check(dataset_id: int, contract_id: int, request: Request, db: Session = Depends(get_db)):
    """执行基础 schema/freshness/quality 断言并保存结果。"""
    subject = actor(request)
    dataset = db.get(DatasetVersion, dataset_id); contract = db.get(DataContract, contract_id)
    if not dataset or not contract: raise HTTPException(404, "数据集或契约不存在")
    observed = {"manifest_hash_length": len(dataset.manifest_hash or ""), "sample_count": dataset.sample_count, "coverage_fields": len(dataset.coverage_report or {})}
    passed = observed["manifest_hash_length"] == 64 and observed["sample_count"] > 0 and observed["coverage_fields"] > 0
    result = "PASS" if passed else "FAIL"
    db.add(ContractCheck(dataset_id=dataset_id, contract_id=contract_id, result=result, observed=observed))
    db.add(AuditEvent(action="dataset.contract_checked", entity_type="dataset_version", entity_id=str(dataset_id), actor_subject=subject, purpose=dataset.purpose, reason=contract.name + ":" + result))
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


@app.post("/api/v1/contents/{content_id}/semantic-dedupe")
def semantic_dedupe(content_id: int, request: Request, db: Session = Depends(get_db)):
    """用归一化 token Jaccard 召回文本近重复，算法可替换为向量索引。"""
    content = db.get(ContentObject, content_id)
    if not content:
        raise HTTPException(404, "内容对象不存在")
    path = Path(content.content_uri)
    text = path.read_text(errors="ignore") if path.exists() and content.modality == "text" else ""
    tokens = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", text.lower()))
    matches = []
    candidates = db.scalars(select(ContentObject).where(ContentObject.modality == "text", ContentObject.id != content_id)).all()
    for candidate in candidates:
        candidate_path = Path(candidate.content_uri)
        candidate_text = candidate_path.read_text(errors="ignore") if candidate_path.exists() else ""
        candidate_tokens = set(re.findall(r"[\w\u4e00-\u9fff]{2,}", candidate_text.lower()))
        union = tokens | candidate_tokens
        similarity = len(tokens & candidate_tokens) / len(union) if union else 0.0
        if similarity >= 0.8:
            db.add(DedupeMatch(content_id=content_id, matched_content_id=candidate.id, layer="semantic", similarity=similarity, detector_version="jaccard-v1", decision="ISOLATE"))
            matches.append({"matched_content_id": candidate.id, "similarity": round(similarity, 4), "layer": "semantic"})
    if matches:
        content.status = "QUARANTINED"
    db.add(AuditEvent(action="dedupe.semantic_completed", entity_type="content_object", entity_id=str(content_id), actor_subject=request.headers.get("X-Actor-Subject", "system"), purpose="quality", reason=f"语义近重复命中 {len(matches)} 项"))
    db.commit()
    return {"content_id": content_id, "detector_version": "jaccard-v1", "matches": matches, "decision": "ISOLATE" if matches else "ALLOW"}

@app.get("/api/v1/metrics/scenario-coverage")
def scenario_coverage(db: Session = Depends(get_db)):
    """按阶段、军兵种和平台形态统计已登记资产覆盖。"""
    assets = db.scalars(select(SourceAsset)).all()
    result = {"total": len(assets), "operation_phase": {}, "service_domains": {}, "platform_mode": {}}
    for asset in assets:
        result["operation_phase"][asset.operation_phase] = result["operation_phase"].get(asset.operation_phase, 0) + 1
        for domain in asset.service_domains or []:
            result["service_domains"][domain] = result["service_domains"].get(domain, 0) + 1
        result["platform_mode"][asset.platform_mode] = result["platform_mode"].get(asset.platform_mode, 0) + 1
    return result


@app.get("/api/v1/audit")
def list_audit(limit: int = 100, db: Session = Depends(get_db)):
    """返回最近审计事件，不包含原始正文。"""
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(min(limit, 500))).all()
    return [{"id": r.id, "action": r.action, "entity_type": r.entity_type, "entity_id": r.entity_id, "actor_subject": r.actor_subject, "purpose": r.purpose, "reason": r.reason, "created_at": r.created_at} for r in rows]

@app.post("/api/v1/evidence", status_code=201)
def create_evidence(payload: EvidenceCreate, request: Request, db: Session = Depends(get_db)):
    """创建可定位证据片段，供监督样本引用。"""
    subject = actor(request)
    if not db.get(ContentObject, payload.content_id): raise HTTPException(404, "内容对象不存在")
    evidence = EvidenceSpan(**payload.model_dump())
    db.add(evidence); db.flush()
    db.add(AuditEvent(action="evidence.created", entity_type="evidence_span", entity_id=str(evidence.id), actor_subject=subject, purpose="curation", reason="创建证据片段"))
    db.commit(); return {"id": evidence.id, "content_id": evidence.content_id, "locator": evidence.locator, "confidence": evidence.confidence}

@app.get("/api/v1/evidence")
def list_evidence(content_id: int | None = None, db: Session = Depends(get_db)):
    """查询证据片段元数据，支持按内容对象过滤。"""
    query = select(EvidenceSpan).order_by(EvidenceSpan.id)
    if content_id is not None:
        query = query.where(EvidenceSpan.content_id == content_id)
    rows = db.scalars(query).all()
    return [{"id": row.id, "content_id": row.content_id, "locator": row.locator, "text_or_region": row.text_or_region, "confidence": row.confidence, "created_at": row.created_at} for row in rows]

@app.post("/api/v1/samples", status_code=201)
def create_sample(payload: SampleCreate, request: Request, db: Session = Depends(get_db)):
    """创建证据约束监督样本；禁止无证据样本进入候选。"""
    subject = actor(request)
    evidence = db.scalars(select(EvidenceSpan).where(EvidenceSpan.id.in_(payload.evidence_refs))).all()
    if len(evidence) != len(set(payload.evidence_refs)): raise HTTPException(422, "证据片段不存在或重复")
    sample = TrainingSample(**payload.model_dump())
    db.add(sample); db.flush()
    db.add(AuditEvent(action="sample.built", entity_type="training_sample", entity_id=str(sample.id), actor_subject=subject, purpose="curation", reason=payload.task_type))
    db.commit(); return {"id": sample.id, "task_type": sample.task_type, "status": sample.status, "evidence_refs": sample.evidence_refs}

@app.post("/api/v1/reward-specs", status_code=201)
def create_reward_spec(payload: RewardSpecCreate, request: Request, db: Session = Depends(get_db)):
    """创建版本化奖励规范，硬约束由平台保留。"""
    required = {"evidence", "factuality", "boundary_compliance"}
    if not required.issubset(payload.reward_weights): raise HTTPException(422, "奖励权重至少包含 evidence、factuality、boundary_compliance")
    spec = RewardSpec(**payload.model_dump())
    db.add(spec); db.flush()
    db.add(AuditEvent(action="reward_spec.created", entity_type="reward_spec", entity_id=str(spec.id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose="rl_post_training", reason=payload.name + ":" + payload.version))
    db.commit(); return {"id": spec.id, "name": spec.name, "version": spec.version, "lifecycle_state": spec.lifecycle_state}

@app.post("/api/v1/grpo/episodes", status_code=201)
def create_episode(payload: EpisodeCreate, request: Request, db: Session = Depends(get_db)):
    """保存可回放 GRPO episode；硬约束失败的候选只能隔离。"""
    spec = db.get(RewardSpec, payload.reward_spec_id)
    if not spec: raise HTTPException(404, "RewardSpec 不存在")
    if len(payload.candidate_group) < 2: raise HTTPException(422, "候选组至少包含两个候选")
    import json
    replay_payload = payload.model_dump()
    replay_hash = hashlib.sha256(json.dumps(replay_payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    episode = GRPOEpisode(**payload.model_dump(), replay_hash=replay_hash)
    if any(item.get("hard_constraint_failed") for item in payload.verifier_results): episode.status = "QUARANTINED"; episode.scalar_reward = 0.0
    db.add(episode); db.flush()
    db.add(AuditEvent(action="reward.replayed", entity_type="grpo_episode", entity_id=str(episode.id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose="rl_post_training", reason=episode.status))
    db.commit(); return {"id": episode.id, "status": episode.status, "replay_hash": replay_hash, "scalar_reward": episode.scalar_reward}


@app.get("/api/v1/traces", response_model=list[TraceRead])
def list_traces(db: Session = Depends(get_db)):
    """只列出已脱敏的生产 trace 评测候选。"""
    return list(db.scalars(select(ProductionTrace).order_by(ProductionTrace.created_at.desc())).all())

@app.post("/api/v1/evaluation-datasets/from-traces", response_model=TraceRead, status_code=201)
def create_trace_eval(payload: TraceCreate, request: Request, db: Session = Depends(get_db)):
    """生产 trace 进入独立评测快照，默认永不直接进入训练。"""
    if payload.risk_tier in {"high", "critical"}:
        raise HTTPException(422, "高风险 trace 不能进入评测快照")
    if db.scalar(select(ProductionTrace).where(ProductionTrace.trace_ref == payload.trace_ref)):
        raise HTTPException(409, "trace 已登记")
    trace = ProductionTrace(**payload.model_dump(), status="EVAL_ONLY", approved_for_training=False)
    db.add(trace); db.flush()
    db.add(AuditEvent(action="trace.eval_snapshot_created", entity_type="production_trace", entity_id=str(trace.id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose="evaluation", reason=payload.redaction_profile))
    db.commit(); db.refresh(trace); return trace

@app.post("/api/v1/lineage", status_code=201)
def create_lineage(parent_type: str, parent_id: str, child_type: str, child_id: str, relation: str, request: Request, db: Session = Depends(get_db)):
    """写入最小血缘边；正文和测试标签不进入事件。"""
    edge = LineageEdge(parent_type=parent_type, parent_id=parent_id, child_type=child_type, child_id=child_id, relation=relation)
    db.add(edge)
    db.add(AuditEvent(action="lineage.edge_created", entity_type=child_type, entity_id=child_id, actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose="lineage", reason=relation))
    db.commit(); return {"id": edge.id, "parent": {"type": parent_type, "id": parent_id}, "child": {"type": child_type, "id": child_id}, "relation": relation}

@app.get("/api/v1/lineage/{entity_type}/{entity_id}")
def get_lineage(entity_type: str, entity_id: str, db: Session = Depends(get_db)):
    """查询实体上下游血缘；runs 路径兼容生命周期查询。"""
    if entity_type == "runs":
        rows = db.scalars(select(LineageRun).where(LineageRun.run_id == entity_id).order_by(LineageRun.id)).all()
        return [{"run_id": r.run_id, "job_name": r.job_name, "event_type": r.event_type, "input_refs": r.input_refs, "output_refs": r.output_refs, "config_sha256": r.config_sha256, "code_commit": r.code_commit, "created_at": r.created_at} for r in rows]
    upstream = db.scalars(select(LineageEdge).where(LineageEdge.child_type == entity_type, LineageEdge.child_id == entity_id)).all()
    downstream = db.scalars(select(LineageEdge).where(LineageEdge.parent_type == entity_type, LineageEdge.parent_id == entity_id)).all()
    pack=lambda e: {"id": e.id, "parent_type": e.parent_type, "parent_id": e.parent_id, "child_type": e.child_type, "child_id": e.child_id, "relation": e.relation}
    return {"entity_type": entity_type, "entity_id": entity_id, "upstream": [pack(e) for e in upstream], "downstream": [pack(e) for e in downstream]}

@app.post("/api/v1/contents/{content_id}/parse")
def parse_content(content_id: int, request: Request, db: Session = Depends(get_db)):
    """运行注册的解析器插件，生成段落证据和多模态 provenance。"""
    content = db.get(ContentObject, content_id)
    if not content:
        raise HTTPException(404, "内容对象不存在")
    path = Path(content.content_uri)
    if not path.exists():
        raise HTTPException(422, "原件文件不存在")
    parser = select_parser(path, content.modality)
    if parser is None:
        raise HTTPException(422, "没有兼容当前内容模态的解析器插件")
    result = parser.parse(path, content.modality)
    evidence_count = 0
    for paragraph in result.paragraphs:
        db.add(EvidenceSpan(content_id=content_id, locator={k: v for k, v in paragraph.items() if k != "text" and k != "confidence"}, text_or_region=paragraph["text"], confidence=paragraph.get("confidence", result.confidence)))
        evidence_count += 1
    content.parser_id = result.parser_id
    content.parser_version = result.parser_version
    content.parser_confidence = result.confidence
    content.locator = {**(content.locator or {}), "provenance": result.provenance}
    content.status = result.status
    db.add(QualityAssessment(content_id=content_id, decision="ALLOW" if result.status == "PARSED" else "REVIEW", scores={"paragraph_count": evidence_count, "parser_confidence": result.confidence, **{k: v for k, v in result.provenance.items() if isinstance(v, (int, float))}}, reason="解析器完成" if result.status == "PARSED" else result.provenance.get("pending_reason", "等待异步解析器")))
    db.add(AuditEvent(action="asset.parsed", entity_type="content_object", entity_id=str(content_id), actor_subject=request.headers.get("X-Actor-Subject", "system"), purpose="normalization", reason=f"生成 {evidence_count} 个证据片段"))
    db.commit()
    return {"content_id": content_id, "status": content.status, "parser_id": content.parser_id, "parser_version": content.parser_version, "parser_confidence": content.parser_confidence, "provenance": result.provenance, "evidence_count": evidence_count}


@app.post("/api/v1/datasets/{dataset_id}/samples", status_code=201)
def add_dataset_sample(dataset_id: int, payload: DatasetSampleCreate, request: Request, db: Session = Depends(get_db)):
    """把已创建的监督样本加入数据版本，并明确训练/评测分割。"""
    dataset = db.get(DatasetVersion, dataset_id); sample = db.get(TrainingSample, payload.sample_id)
    if not dataset or not sample: raise HTTPException(404, "数据集或样本不存在")
    if dataset.approval_state not in {"DRAFT", "REVIEWED"}: raise HTTPException(409, "数据集已冻结，不能追加样本")
    if db.scalar(select(DatasetSample).where(DatasetSample.dataset_id == dataset_id, DatasetSample.sample_id == payload.sample_id)):
        raise HTTPException(409, "样本已加入该数据集")
    link = DatasetSample(dataset_id=dataset_id, sample_id=payload.sample_id, split=payload.split)
    db.add(link); dataset.sample_count = (dataset.sample_count or 0) + 1
    db.add(AuditEvent(action="dataset.sample_added", entity_type="dataset_version", entity_id=str(dataset_id), actor_subject=request.headers.get("X-Actor-Subject", "unknown"), purpose=dataset.purpose, reason=f"sample={payload.sample_id},split={payload.split}"))
    db.commit(); return {"dataset_id": dataset_id, "sample_id": payload.sample_id, "split": payload.split, "sample_count": dataset.sample_count}

@app.get("/api/v1/datasets/{dataset_id}/samples")
def list_dataset_samples(dataset_id: int, db: Session = Depends(get_db)):
    """查看数据集样本分割清单。"""
    if not db.get(DatasetVersion, dataset_id): raise HTTPException(404, "数据集不存在")
    rows = db.scalars(select(DatasetSample).where(DatasetSample.dataset_id == dataset_id).order_by(DatasetSample.id)).all()
    return [{"id": row.id, "sample_id": row.sample_id, "split": row.split} for row in rows]


@app.get("/api/v1/exports/{export_id}/download")
def download_export(export_id: int, db: Session = Depends(get_db)):
    """下载已经生成的 manifest；路径来自数据库且必须位于数据目录。"""
    export = db.get(PackageExport, export_id)
    if not export: raise HTTPException(404, "导出工件不存在")
    path = Path(export.artifact_uri).resolve()
    export_root = (Path(settings.data_dir) / "exports").resolve()
    if export_root not in path.parents or not path.is_file(): raise HTTPException(404, "导出文件不可用")
    return FileResponse(path, media_type="application/json", filename=path.name)

@app.get("/api/v1/health/dependencies")
def dependency_health(db: Session = Depends(get_db)):
    """检查数据库和数据目录等运行依赖。"""
    try:
        db.execute(select(func.count(SourceAsset.id)))
        db_status = "ok"
    except Exception:
        db_status = "error"
    data_path = Path(settings.data_dir)
    try:
        data_path.mkdir(parents=True, exist_ok=True)
        data_status = "ok"
    except OSError:
        data_status = "error"
    return {"status": "ok" if db_status == data_status == "ok" else "degraded", "database": db_status, "data_dir": data_status}


@app.get("/api/v1/contents/{content_id}/quality")
def content_quality(content_id: int, db: Session = Depends(get_db)):
    """返回内容对象的质量和政策检查历史。"""
    if not db.get(ContentObject, content_id):
        raise HTTPException(404, "内容对象不存在")
    rows = db.scalars(select(QualityAssessment).where(QualityAssessment.content_id == content_id).order_by(QualityAssessment.created_at.desc())).all()
    return [{"id": row.id, "decision": row.decision, "scores": row.scores, "reason": row.reason, "rule_version": row.rule_version, "created_at": row.created_at} for row in rows]

@app.post("/api/v1/contents/{content_id}/quality-gate")
def run_quality_gate(content_id: int, payload: QualityGateRequest, request: Request, db: Session = Depends(get_db)):
    """执行可配置质量门并记录每条断言，失败按策略隔离或转人工复核。"""
    content = db.get(ContentObject, content_id)
    if not content:
        raise HTTPException(404, "内容对象不存在")
    evidence = db.scalars(select(EvidenceSpan).where(EvidenceSpan.content_id == content_id)).all()
    pii_count = db.scalar(select(func.count(PIIFinding.id)).where(PIIFinding.content_id == content_id)) or 0
    checks = {
        "parser_confidence": {"observed": content.parser_confidence, "required": payload.min_parser_confidence, "passed": content.parser_confidence >= payload.min_parser_confidence},
        "pii_findings": {"observed": pii_count, "required_max": payload.max_pii_findings, "passed": pii_count <= payload.max_pii_findings},
        "evidence_count": {"observed": len(evidence), "required": payload.min_evidence_count, "passed": len(evidence) >= payload.min_evidence_count},
        "evidence_confidence": {"observed": min((item.confidence for item in evidence), default=0.0), "required": payload.min_evidence_confidence, "passed": min((item.confidence for item in evidence), default=0.0) >= payload.min_evidence_confidence},
    }
    if payload.allowed_modalities:
        checks["modality"] = {"observed": content.modality, "allowed": payload.allowed_modalities, "passed": content.modality in payload.allowed_modalities}
    passed = all(item["passed"] for item in checks.values())
    decision = "ALLOW" if passed else payload.failure_policy
    content.status = "NORMALIZED" if decision == "ALLOW" else ("QUARANTINED" if decision == "BLOCK" else "REVIEW_PENDING")
    db.add(QualityAssessment(content_id=content_id, decision=decision, scores=checks, reason="全部质量断言通过" if passed else "存在未通过的质量断言", rule_version="quality-gate-v1"))
    db.add(AuditEvent(action="quality.gate_evaluated", entity_type="content_object", entity_id=str(content_id), actor_subject=request.headers.get("X-Actor-Subject", "system"), purpose="quality", reason=f"{decision}:{len([c for c in checks.values() if not c['passed']])} failures"))
    db.commit()
    return {"content_id": content_id, "decision": decision, "passed": passed, "checks": checks, "status": content.status}

@app.get("/api/v1/contents/{content_id}/pii-findings")
def content_pii_findings(content_id: int, db: Session = Depends(get_db)):
    """返回 PII 发现项元数据，不返回原文内容。"""
    if not db.get(ContentObject, content_id):
        raise HTTPException(404, "内容对象不存在")
    rows = db.scalars(select(PIIFinding).where(PIIFinding.content_id == content_id).order_by(PIIFinding.created_at.desc())).all()
    return [{"id": row.id, "entity_type": row.entity_type, "locator": row.locator, "confidence": row.confidence, "action": row.action, "review_state": row.review_state, "detector_version": row.detector_version} for row in rows]


@app.post("/api/v1/lineage/runs", status_code=201)
def create_lineage_run(payload: LineageRunCreate, request: Request, db: Session = Depends(get_db)):
    """记录 OpenLineage 生命周期事件，并禁止终态后继续写入。"""
    terminal = {"COMPLETE", "FAIL", "ABORT"}
    previous = db.scalars(select(LineageRun).where(LineageRun.run_id == payload.run_id).order_by(LineageRun.id.desc())).first()
    if previous and previous.event_type in terminal:
        raise HTTPException(409, "该 run 已进入终态")
    event = LineageRun(**payload.model_dump())
    db.add(event)
    db.add(AuditEvent(action="lineage.run_event", entity_type="lineage_run", entity_id=payload.run_id, actor_subject=request.headers.get("X-Actor-Subject", "system"), purpose="lineage", reason=payload.event_type + ":" + payload.job_name))
    db.commit()
    return {"run_id": event.run_id, "job_name": event.job_name, "event_type": event.event_type, "created_at": event.created_at}

@app.get("/api/v1/lineage/runs/{run_id}")
def get_lineage_run(run_id: str, db: Session = Depends(get_db)):
    """查询同一 run 的完整生命周期事件。"""
    rows = db.scalars(select(LineageRun).where(LineageRun.run_id == run_id).order_by(LineageRun.id)).all()
    return [{"run_id": r.run_id, "job_name": r.job_name, "event_type": r.event_type, "input_refs": r.input_refs, "output_refs": r.output_refs, "config_sha256": r.config_sha256, "code_commit": r.code_commit, "created_at": r.created_at} for r in rows]

@app.get("/api/v1/lineage/runs/{run_id}/openlineage")
def get_openlineage_events(run_id: str, db: Session = Depends(get_db)):
    """将简化运行记录转换为 OpenLineage 1.x 事件信封。"""
    rows = db.scalars(select(LineageRun).where(LineageRun.run_id == run_id).order_by(LineageRun.id)).all()
    return [{
        "eventType": row.event_type,
        "eventTime": row.created_at.isoformat(),
        "run": {"runId": row.run_id},
        "job": {"namespace": "military-data-platform", "name": row.job_name},
        "inputs": [{"namespace": "military-data-platform", "name": ref} for ref in (row.input_refs or [])],
        "outputs": [{"namespace": "military-data-platform", "name": ref} for ref in (row.output_refs or [])],
        "producer": "https://github.com/1420970597/data_platform",
        "facets": {"codeVersion": {"_producer": "military-data-platform", "_schemaURL": "https://openlineage.io/spec/facets/1-0-0/CodeVersionRunFacet.json", "version": row.code_commit} if row.code_commit else {}, "config": {"sha256": row.config_sha256} if row.config_sha256 else {}},
    } for row in rows]

@app.post("/api/v1/training/runs", status_code=201)
def register_training_run(payload: TrainingRunCreate, request: Request, db: Session = Depends(get_db)):
    """登记训练运行；只有已批准数据集可绑定训练任务。"""
    subject = actor(request, {"training_engineer", "data_steward"})
    dataset = db.get(DatasetVersion, payload.dataset_id)
    if not dataset:
        raise HTTPException(404, "数据集不存在")
    if dataset.approval_state != "APPROVED":
        raise HTTPException(409, "训练运行只能使用已批准数据集")
    if db.scalar(select(TrainingRun).where(TrainingRun.run_id == payload.run_id)):
        raise HTTPException(409, "训练运行 ID 已登记")
    run = TrainingRun(**payload.model_dump())
    db.add(run)
    db.add(AuditEvent(action="training.run_registered", entity_type="training_run", entity_id=payload.run_id, actor_subject=subject, purpose="training", reason=f"{payload.training_type}:{payload.status}"))
    db.commit(); db.refresh(run)
    return {"id": run.id, "run_id": run.run_id, "dataset_id": run.dataset_id, "training_type": run.training_type, "status": run.status, "created_at": run.created_at}

@app.get("/api/v1/training/runs")
def list_training_runs(dataset_id: int | None = None, db: Session = Depends(get_db)):
    """查询训练运行元数据，不返回模型权重内容。"""
    query = select(TrainingRun).order_by(TrainingRun.created_at.desc())
    if dataset_id is not None:
        query = query.where(TrainingRun.dataset_id == dataset_id)
    rows = db.scalars(query).all()
    return [{"id": r.id, "run_id": r.run_id, "dataset_id": r.dataset_id, "training_type": r.training_type, "code_commit": r.code_commit, "image_digest": r.image_digest, "parameters": r.parameters, "resources": r.resources, "output_artifacts": r.output_artifacts, "status": r.status, "created_at": r.created_at} for r in rows]
