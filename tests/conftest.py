import json as jsonlib
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kiwoom_api_client  # noqa: E402
from config import KiwoomConfig  # noqa: E402
from kiwoom_api_client import KiwoomAPIClient  # noqa: E402
from order_guard import OrderJournal  # noqa: E402

MOCK_BASE = "https://mockapi.kiwoom.com"
PROD_BASE = "https://api.kiwoom.com"

#: clean_env가 환경변수를 지우기 전의 원본. 통합 테스트가 실제 키를 읽는 데 쓴다.
ORIGINAL_ENV = dict(os.environ)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, text=None):
        self.status_code = status_code
        self._payload = {} if payload is None else payload
        self.headers = headers or {}
        self.text = text if text is not None else jsonlib.dumps(self._payload)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHTTP:
    """requests.post 를 대신하는 큐 기반 페이크."""

    def __init__(self):
        self.calls = []
        self._queue = []

    def add(self, payload=None, status_code=200, headers=None, exc=None,
            text=None):
        """응답을 하나 큐에 넣습니다. exc를 주면 그 예외를 던집니다."""
        self._queue.append((exc, FakeResponse(status_code, payload, headers, text)))
        return self

    def add_error(self, exc):
        return self.add(exc=exc)

    def __call__(self, url, json=None, headers=None, timeout=None, **kwargs):
        self.calls.append({
            'url': url, 'json': json, 'headers': headers or {},
            'timeout': timeout,
        })
        if not self._queue:
            raise AssertionError(
                f"예상치 못한 추가 호출입니다 (총 {len(self.calls)}회): {url}"
            )
        exc, response = self._queue.pop(0)
        if exc is not None:
            raise exc
        return response

    @property
    def call_count(self):
        return len(self.calls)

    def last(self):
        return self.calls[-1]


@pytest.fixture
def http(monkeypatch):
    """kiwoom_api_client 가 쓰는 requests.post 를 가로챕니다."""
    fake = FakeHTTP()
    monkeypatch.setattr(kiwoom_api_client.requests, "post", fake)
    return fake


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "mock_integration: 실제 모의투자 서버를 호출하는 통합 테스트 "
        "(기본 제외, 실행하려면 -m mock_integration)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("-m"):
        return
    skip = pytest.mark.skip(
        reason="통합 테스트는 기본 제외됩니다. -m mock_integration 으로 실행하세요."
    )
    for item in items:
        if "mock_integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """테스트가 실제 환경변수에 영향받지 않도록 격리합니다."""
    for key in list(os.environ):
        if key.startswith("KIWOOM_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KIWOOM_APP_KEY", "test-app-key")
    monkeypatch.setenv("KIWOOM_SECRET_KEY", "test-secret-key")


@pytest.fixture
def config(clean_env):
    cfg = KiwoomConfig()
    cfg.use_mock = True
    cfg.retry_delay = 0.0
    return cfg


@pytest.fixture
def journal(tmp_path):
    return OrderJournal(str(tmp_path / "orders.jsonl"))


@pytest.fixture
def client(config):
    """토큰이 이미 발급된 상태의 클라이언트."""
    api = KiwoomAPIClient.from_config(config)
    api.oauth.token = "TEST-TOKEN"
    api.oauth.token_type = "Bearer"
    api.oauth.token_expires = datetime.now() + timedelta(hours=6)
    return api


@pytest.fixture
def real_credentials():
    """통합 테스트용 실제 모의투자 자격증명. 없으면 테스트를 건너뜁니다."""
    appkey = ORIGINAL_ENV.get("KIWOOM_APP_KEY", "")
    secretkey = ORIGINAL_ENV.get("KIWOOM_SECRET_KEY", "")
    if not appkey or not secretkey:
        pytest.skip(
            "모의투자 앱키가 없어 건너뜁니다 "
            "(KIWOOM_APP_KEY / KIWOOM_SECRET_KEY 환경변수 필요)"
        )
    return appkey, secretkey


@pytest.fixture
def real_client(config):
    config.use_mock = False
    api = KiwoomAPIClient.from_config(config)
    api.oauth.token = "TEST-TOKEN"
    api.oauth.token_type = "Bearer"
    api.oauth.token_expires = datetime.now() + timedelta(hours=6)
    return api
