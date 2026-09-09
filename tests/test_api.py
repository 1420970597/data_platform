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
