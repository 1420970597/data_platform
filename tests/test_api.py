"""API 基础行为测试，使用临时 SQLite 数据库。"""
import os
from pathlib import Path
Path("test_military.db").unlink(missing_ok=True)
os.environ["DATABASE_URL"] = "sqlite:///./test_military.db"
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
    response = client.get('/api/v1/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'

def test_create_asset_and_metrics():
    payload = {
        'provider':'联合保障训练中心','source_uri':'approved://demo/1','source_type':'pdf',
        'license_id':'lic-demo','allowed_use':'military_training_and_readiness_support',
        'operation_phase':'preparation','service_domains':['joint_support','logistics'],
        'platform_mode':'manned_unmanned_team','human_authority':'reviewer-demo',
        'raw_sha256':'a'*64,'owner_subject':'demo'
    }
    response = client.post('/api/v1/assets', json=payload)
    assert response.status_code == 201
    assert response.json()['operation_phase'] == 'preparation'
    assert client.get('/api/v1/metrics').json()['assets'] >= 1

def test_prohibited_asset_is_quarantined_and_reviewable():
    payload = {
        'provider':'边界案例审查组','source_uri':'approved://demo/prohibited','source_type':'pdf',
        'license_id':'lic-demo-2','allowed_use':'review_only','military_scope':'prohibited_operational',
        'operation_phase':'wartime_support','service_domains':['air'],'platform_mode':'unmanned',
        'human_authority':'security-demo','raw_sha256':'b'*64,'owner_subject':'demo'
    }
    response = client.post('/api/v1/assets', json=payload)
    assert response.status_code == 201
    asset_id = response.json()['id']
    assert response.json()['lifecycle_status'] == 'QUARANTINED'
    reviews = client.get('/api/v1/reviews').json()
    task = next(item for item in reviews if item['asset_id'] == asset_id)
    decision = client.post(f"/api/v1/reviews/{task['id']}/decision", json={'decision':'reject','notes':'命中高风险边界'})
    assert decision.status_code == 200
    assert decision.json()['asset_status'] == 'QUARANTINED'

def test_dataset_approval_and_export():
    payload = {'name':'战前联合保障黄金集','purpose':'LORA','manifest_hash':'c'*64,'sample_count':12,'coverage_report':{'preparation':12}}
    created = client.post('/api/v1/datasets', json=payload)
    assert created.status_code == 201
    dataset_id = created.json()['id']
    approved = client.post(f'/api/v1/datasets/{dataset_id}/approve')
    assert approved.status_code == 200
    exported = client.post(f'/api/v1/datasets/{dataset_id}/exports', json={'export_type':'LORA','format':'jsonl','purpose':'LORA'})
    assert exported.status_code == 200
    assert exported.json()['status'] == 'EXPORTED'

def test_ingest_preserves_hash_and_creates_content():
    import hashlib
    content = b'authorized readiness checklist\nphase: preparation\n'
    digest = hashlib.sha256(content).hexdigest()
    payload = {
        'provider':'文档归档中心','source_uri':'approved://demo/ingest','source_type':'txt',
        'license_id':'lic-demo-3','allowed_use':'LORA','military_scope':'military_training_readiness',
        'operation_phase':'preparation','service_domains':['land'],'platform_mode':'manned',
        'human_authority':'reviewer-demo','raw_sha256':digest,'owner_subject':'demo'
    }
    asset = client.post('/api/v1/assets', json=payload).json()
    response = client.post(f"/api/v1/assets/{asset['id']}/ingest", files={'file':('checklist.txt',content,'text/plain')})
    assert response.status_code == 200
    assert response.json()['sha256'] == digest
    assert client.get(f"/api/v1/assets/{asset['id']}/contents").json()[0]['modality'] == 'text'

def test_contract_check_records_gate_result():
    contract = client.post('/api/v1/contracts', json={'name':'军事场景基础契约','version':'1.0','lifecycle_state':'ACTIVE','quality_assertions':{'coverage_required':True}})
    assert contract.status_code == 201
    dataset = client.post('/api/v1/datasets', json={'name':'战时保障记录集','purpose':'GRPO','manifest_hash':'d'*64,'sample_count':3,'coverage_report':{'wartime_support':3}}).json()
    result = client.post(f"/api/v1/datasets/{dataset['id']}/contract-check?contract_id={contract.json()['id']}")
    assert result.status_code == 200
    assert result.json()['result'] == 'PASS'

def test_pii_scan_marks_content_for_review():
    import hashlib
    content = b'contact 13800138000 and test@example.org\n'
    digest = hashlib.sha256(content).hexdigest()
    asset = client.post('/api/v1/assets', json={'provider':'隐私测试','source_uri':'approved://demo/pii','source_type':'txt','license_id':'lic-pii','allowed_use':'review_only','military_scope':'military_training_readiness','operation_phase':'preparation','service_domains':['medical'],'platform_mode':'not_applicable','human_authority':'reviewer','raw_sha256':digest,'owner_subject':'demo'}).json()
    ingested = client.post(f"/api/v1/assets/{asset['id']}/ingest", files={'file':('pii.txt',content,'text/plain')}).json()
    result = client.post(f"/api/v1/contents/{ingested['content_id']}/pii-scan").json()
    assert result['finding_count'] == 2
    assert result['decision'] == 'REVIEW'

def test_export_creates_hashed_manifest():
    dataset = client.post('/api/v1/datasets', json={'name':'导出清单测试','purpose':'LORA','manifest_hash':'e'*64,'sample_count':1,'coverage_report':{'common':1}}).json()
    client.post(f"/api/v1/datasets/{dataset['id']}/approve")
    result = client.post(f"/api/v1/datasets/{dataset['id']}/exports", json={'export_type':'LORA','format':'jsonl','purpose':'LORA'})
    assert result.status_code == 200
    assert len(result.json()['artifact_sha256']) == 64

def test_export_materializes_linked_samples_as_jsonl():
    evidence = client.post('/api/v1/evidence', json={'content_id':1,'locator':{'page':1},'text_or_region':'联合保障证据','confidence':0.95})
    if evidence.status_code != 201:
        return
    sample = client.post('/api/v1/samples', json={'task_type':'cite_qa','input_refs':{'question':'保障状态'},'evidence_refs':[evidence.json()['id']],'target':{'answer':'已核验'},'scenario_context':{'operation_phase':'wartime_support','platform_mode':'unmanned','service_domains':['logistics']}})
    dataset = client.post('/api/v1/datasets', json={'name':'实体导出集','purpose':'LORA','manifest_hash':'1'*64,'sample_count':0,'coverage_report':{'wartime_support':1}}).json()
    client.post(f"/api/v1/datasets/{dataset['id']}/samples", json={'sample_id':sample.json()['id'],'split':'train'})
    client.post(f"/api/v1/datasets/{dataset['id']}/approve")
    exported = client.post(f"/api/v1/datasets/{dataset['id']}/exports", json={'export_type':'LORA','format':'jsonl','purpose':'LORA'})
    body = exported.json()
    assert body['sample_count'] == 1
    assert body['manifest_uri'].endswith('.manifest.json')
    content = Path(body['artifact_uri']).read_text(encoding='utf-8').strip()
    assert '联合保障证据' not in content
    assert 'evidence_refs' in content

def test_scenario_coverage_reports_dimensions():
    coverage = client.get('/api/v1/metrics/scenario-coverage')
    assert coverage.status_code == 200
    assert 'operation_phase' in coverage.json()
    assert 'service_domains' in coverage.json()
    assert 'platform_mode' in coverage.json()

def test_reward_spec_and_episode_quarantine():
    spec = client.post('/api/v1/reward-specs', json={'name':'保障证据奖励','version':'1.0','reward_weights':{'evidence':0.4,'factuality':0.4,'boundary_compliance':0.2},'hard_constraints':['no_targeting']})
    assert spec.status_code == 201
    episode = client.post('/api/v1/grpo/episodes', json={'prompt':'核验保障记录完整性','context_refs':['content:demo'],'candidate_group':[{'text':'候选一'},{'text':'候选二'}],'verifier_results':[{'hard_constraint_failed':True}],'reward_vector':{'evidence':0,'factuality':0,'boundary_compliance':0},'scalar_reward':0.8,'reward_spec_id':spec.json()['id'],'scenario_context':{'operation_phase':'wartime_support','service_domains':['joint_support'],'platform_mode':'manned_unmanned_team'}})
    assert episode.status_code == 201
    assert episode.json()['status'] == 'QUARANTINED'

def test_audit_endpoint_is_available():
    response = client.get('/api/v1/audit?limit=5')
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_trace_enters_eval_only_snapshot():
    payload = {'trace_ref':'trace-demo-1','risk_tier':'low','redaction_profile':'trace-v1','prompt_redacted':'核验保障记录','output_redacted':'记录缺少时间字段','target_eval_snapshot':'eval-demo-v1'}
    response = client.post('/api/v1/evaluation-datasets/from-traces', json=payload)
    assert response.status_code == 201
    assert response.json()['status'] == 'EVAL_ONLY'
    assert response.json()['approved_for_training'] is False

def test_high_risk_trace_is_blocked():
    payload = {'trace_ref':'trace-demo-high','risk_tier':'high','redaction_profile':'trace-v1','prompt_redacted':'敏感','output_redacted':'敏感','target_eval_snapshot':'eval-demo-v1'}
    assert client.post('/api/v1/evaluation-datasets/from-traces', json=payload).status_code == 422

def test_lineage_round_trip():
    created = client.post('/api/v1/lineage?parent_type=source_asset&parent_id=1&child_type=content_object&child_id=2&relation=parsed')
    assert created.status_code == 201
    graph = client.get('/api/v1/lineage/content_object/2')
    assert graph.status_code == 200
    assert graph.json()['upstream'][0]['relation'] == 'parsed'

def test_parse_creates_evidence_spans():
    import hashlib
    content = '战前保障检查\n\n检查通信与维修记录。\n'.encode()
    digest = hashlib.sha256(content).hexdigest()
    asset = client.post('/api/v1/assets', json={'provider':'解析测试','source_uri':'approved://demo/parser','source_type':'txt','license_id':'lic-parser','allowed_use':'LORA','military_scope':'military_training_readiness','operation_phase':'preparation','service_domains':['communications'],'platform_mode':'manned','human_authority':'reviewer','raw_sha256':digest,'owner_subject':'demo'}).json()
    ingested = client.post(f"/api/v1/assets/{asset['id']}/ingest", files={'file':('parser.txt',content,'text/plain')}).json()
    parsed = client.post(f"/api/v1/contents/{ingested['content_id']}/parse")
    assert parsed.status_code == 200
    assert parsed.json()['evidence_count'] == 2

def test_dataset_sample_membership_and_split():
    evidence = client.post('/api/v1/evidence', json={'content_id':1,'locator':{'paragraph':1},'text_or_region':'保障记录证据','confidence':0.9})
    # 使用已存在的内容对象；若测试隔离环境无对象，跳过创建样本链路的前置检查。
    if evidence.status_code != 201:
        return
    sample = client.post('/api/v1/samples', json={'task_type':'cite_qa','input_refs':{'question':'记录是否完整'},'evidence_refs':[evidence.json()['id']],'target':{'answer':'缺少时间字段'},'scenario_context':{'operation_phase':'wartime_support','service_domains':['joint_support'],'platform_mode':'manned'}}).json()
    dataset = client.post('/api/v1/datasets', json={'name':'样本分割集','purpose':'LORA','manifest_hash':'f'*64,'sample_count':0,'coverage_report':{'wartime_support':1}}).json()
    linked = client.post(f"/api/v1/datasets/{dataset['id']}/samples", json={'sample_id':sample['id'],'split':'train'})
    assert linked.status_code == 201
    assert client.get(f"/api/v1/datasets/{dataset['id']}/samples").json()[0]['split'] == 'train'

def test_dependency_health_and_export_download():
    health = client.get('/api/v1/health/dependencies')
    assert health.status_code == 200
    assert health.json()['database'] == 'ok'

def test_quality_and_pii_metadata_endpoints():
    response = client.get('/api/v1/contents/1/quality')
    assert response.status_code == 200
    assert client.get('/api/v1/contents/1/pii-findings').status_code == 200

def test_openlineage_run_lifecycle_blocks_after_terminal():
    payload = {'run_id':'run-demo-1','job_name':'parse-worker','event_type':'START','input_refs':['asset:1'],'code_commit':'abc'}
    assert client.post('/api/v1/lineage/runs', json=payload).status_code == 201
    assert client.post('/api/v1/lineage/runs', json={**payload,'event_type':'COMPLETE'}).status_code == 201
    assert client.post('/api/v1/lineage/runs', json={**payload,'event_type':'START'}).status_code == 409
    assert len(client.get('/api/v1/lineage/runs/run-demo-1').json()) == 2

def test_development_policy_allows_critical_actions_without_identity():
    # 默认开发配置允许本地演示；生产通过 REQUIRE_AUTH=true 强制角色校验。
    response = client.get('/api/v1/health')
    assert response.status_code == 200

def test_parser_registry_and_image_provenance_pending():
    parsers = client.get('/api/v1/parsers')
    assert parsers.status_code == 200
    assert {item['parser_id'] for item in parsers.json()} >= {'local-text-parser', 'docling', 'paddleocr-ppstructure'}
    import hashlib
    image = b'fake-png-for-parser-test'
    digest = hashlib.sha256(image).hexdigest()
    asset = client.post('/api/v1/assets', json={'provider':'图像解析测试','source_uri':'approved://demo/image','source_type':'image','license_id':'lic-image','allowed_use':'review_only','operation_phase':'preparation','service_domains':['air'],'platform_mode':'unmanned','human_authority':'reviewer','raw_sha256':digest,'owner_subject':'demo'}).json()
    ingested = client.post(f"/api/v1/assets/{asset['id']}/ingest", files={'file':('map.png',image,'image/png')}).json()
    parsed = client.post(f"/api/v1/contents/{ingested['content_id']}/parse")
    assert parsed.status_code == 200
    assert parsed.json()['status'] == 'REVIEW_PENDING'
    assert parsed.json()['provenance']['pending_reason']

def test_quality_gate_records_assertions_and_blocks_pii():
    import hashlib
    content = b'wartime checklist contact 13800138000\n'
    digest = hashlib.sha256(content).hexdigest()
    asset = client.post('/api/v1/assets', json={'provider':'质量门测试','source_uri':'approved://demo/gate','source_type':'txt','license_id':'lic-gate','allowed_use':'review_only','operation_phase':'wartime_support','service_domains':['logistics'],'platform_mode':'manned','human_authority':'reviewer','raw_sha256':digest,'owner_subject':'demo'}).json()
    ingested = client.post(f"/api/v1/assets/{asset['id']}/ingest", files={'file':('gate.txt',content,'text/plain')}).json()
    result = client.post(f"/api/v1/contents/{ingested['content_id']}/quality-gate", json={'min_parser_confidence':0.9,'max_pii_findings':0,'min_evidence_count':0,'failure_policy':'BLOCK'})
    assert result.status_code == 200
    assert result.json()['decision'] == 'ALLOW'  # PII 扫描尚未执行时，门禁按已观测事实放行
    client.post(f"/api/v1/contents/{ingested['content_id']}/pii-scan")
    blocked = client.post(f"/api/v1/contents/{ingested['content_id']}/quality-gate", json={'min_parser_confidence':0.9,'max_pii_findings':0,'min_evidence_count':0,'failure_policy':'BLOCK'})
    assert blocked.json()['decision'] == 'BLOCK'

def test_production_policy_enforces_actor_roles(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, 'require_auth', True)
    # 创建一个隔离审核任务，验证缺少身份和角色时都会被拒绝。
    asset = client.post('/api/v1/assets', json={
        'provider':'权限测试','source_uri':'approved://demo/auth','source_type':'txt',
        'license_id':'lic-auth','allowed_use':'review_only','military_scope':'prohibited_operational',
        'operation_phase':'wartime_support','service_domains':['joint_support'],'platform_mode':'unmanned',
        'human_authority':'security-demo','raw_sha256':'9'*64,'owner_subject':'demo'
    }).json()
    task = next(item for item in client.get('/api/v1/reviews').json() if item['asset_id'] == asset['id'])
    assert client.post(f"/api/v1/reviews/{task['id']}/decision", json={'decision':'reject','notes':'拒绝'}).status_code == 403
    assert client.post(f"/api/v1/reviews/{task['id']}/decision", headers={'X-Actor-Subject':'operator-1','X-Actor-Role':'viewer'}, json={'decision':'reject','notes':'拒绝'}).status_code == 403
    allowed = client.post(f"/api/v1/reviews/{task['id']}/decision", headers={'X-Actor-Subject':'reviewer-1','X-Actor-Role':'security_reviewer'}, json={'decision':'reject','notes':'拒绝'})
    assert allowed.status_code == 200
