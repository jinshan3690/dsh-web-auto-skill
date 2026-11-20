"""HTTP 入参安全校验：协议白名单 + 内网地址拒绝"""
import pytest

from webskill import security


@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(security, "ALLOW_PRIVATE_NETWORK", False)
    monkeypatch.setattr(security, "ALLOW_FILE_URL", False)


@pytest.fixture
def loose(monkeypatch):
    monkeypatch.setattr(security, "ALLOW_PRIVATE_NETWORK", True)
    monkeypatch.setattr(security, "ALLOW_FILE_URL", True)


class TestScheme:
    def test_http_ok(self, strict):
        url = "https://example.com/form"
        assert security.validate_page_url(url) == url

    def test_file_blocked(self, strict):
        with pytest.raises(ValueError, match="file://"):
            security.validate_page_url("file:///etc/passwd")

    def test_file_allowed_when_enabled(self, loose):
        url = "file:///tmp/a.html"
        assert security.validate_page_url(url) == url

    def test_other_scheme_blocked(self, strict):
        with pytest.raises(ValueError, match="协议"):
            security.validate_page_url("ftp://example.com/a")


class TestPrivateNetwork:
    def test_localhost_blocked(self, strict):
        with pytest.raises(ValueError, match="环回"):
            security.validate_page_url("http://localhost:8000/x")

    def test_loopback_ip_blocked(self, strict):
        with pytest.raises(ValueError, match="内网|保留"):
            security.validate_page_url("http://127.0.0.1:8000/x")

    def test_rfc1918_blocked(self, strict):
        for ip in ("10.0.0.5", "172.16.3.4", "192.168.1.1"):
            with pytest.raises(ValueError):
                security.validate_page_url(f"http://{ip}/oa")

    def test_metadata_service_blocked(self, strict):
        with pytest.raises(ValueError):
            security.validate_page_url("http://169.254.169.254/latest/meta-data/")

    def test_private_allowed_when_enabled(self, loose):
        url = "http://127.0.0.1:8000/x"
        assert security.validate_page_url(url) == url

    def test_missing_host(self, strict):
        with pytest.raises(ValueError):
            security.validate_page_url("http:///nohost")
