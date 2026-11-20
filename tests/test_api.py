"""API 层边界：这些是「暴露出去就出事」的闸口，必须有测试守着

全部走 TestClient，不真开浏览器 —— 校验都在进浏览器之前。
"""
import pytest
from fastapi.testclient import TestClient

from web_auto_skill.web import main as web


@pytest.fixture
def client(monkeypatch):
    """每个用例独立 token，避免受真实环境变量影响"""
    monkeypatch.setattr(web, "API_TOKEN", "test-token")
    monkeypatch.setattr("web_auto_skill.security.ALLOW_FILE_URL", False)
    monkeypatch.setattr("web_auto_skill.security.ALLOW_PRIVATE_NETWORK", False)
    with TestClient(web.app) as c:
        yield c


AUTH = {"Authorization": "Bearer test-token"}


class TestAuth:
    def test_health_needs_no_token(self, client):
        assert client.get("/health").status_code == 200

    def test_business_api_requires_token(self, client):
        assert client.get("/skills").status_code == 401

    def test_wrong_token_rejected(self, client):
        r = client.get("/skills", headers={"Authorization": "Bearer nope"})
        assert r.status_code == 401

    def test_correct_token_passes(self, client):
        assert client.get("/skills", headers=AUTH).status_code == 200

    def test_health_does_not_leak_base_url(self, client):
        assert "base_url" not in client.get("/health").json()


class TestUrlGuards:
    def test_file_url_rejected(self, client):
        r = client.post("/task/run", headers=AUTH,
                        json={"page_key": "file:///etc/passwd", "command": "x"})
        assert r.status_code == 400

    def test_private_ip_rejected(self, client):
        r = client.post("/task/run", headers=AUTH,
                        json={"page_key": "http://10.1.2.3/x", "command": "x"})
        assert r.status_code == 400

    def test_import_rejects_bad_page_key(self, client):
        """导入的 page_key 会被 /inspect 拿去访问，不能绕过入口校验"""
        r = client.post("/skill/import", headers=AUTH, json={"skill": {
            "page_key": "file:///etc/passwd", "contract_fingerprint": "fp", "steps": [],
        }})
        assert r.status_code == 400

    def test_empty_request_rejected(self, client):
        r = client.post("/task/run", headers=AUTH, json={"page_key": "https://example.com"})
        assert r.status_code == 400


class TestRequestDefaults:
    def test_headless_defaults_true(self):
        """服务器多半没有图形界面，默认有头会直接启动失败"""
        assert web.RunRequest.model_fields["headless"].default is True
        assert web.ExecuteRequest.model_fields["headless"].default is True
