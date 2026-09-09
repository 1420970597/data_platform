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
