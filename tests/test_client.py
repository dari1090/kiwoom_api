"""전송 계층 테스트: 헤더 구성, 오류 분류, 재시도 정책, 연속조회."""

import pytest
import requests

from kiwoom_api_client import PATH_ACCOUNT, PATH_ORDER
from kiwoom_errors import KiwoomAPIError, KiwoomHTTPError, KiwoomOrderUnknownState


def test_call_sends_api_id_and_bearer_token(client, http):
    http.add({"return_code": 0, "return_msg": "정상", "ord_no": "0001234"})

    client.call("kt10000", PATH_ORDER, {"stk_cd": "005930"})

    call = http.last()
    assert call['url'] == "https://mockapi.kiwoom.com/api/dostk/ordr"
    assert call['headers']['api-id'] == "kt10000"
    assert call['headers']['Authorization'] == "Bearer TEST-TOKEN"
    assert call['headers']['Content-Type'] == "application/json;charset=UTF-8"
    assert call['json'] == {"stk_cd": "005930"}


def test_call_uses_split_connect_read_timeout(client, http):
    http.add({"return_code": 0})

    client.call("ka10001", PATH_ACCOUNT, {})

    assert http.last()['timeout'] == (5.0, 30.0)


def test_continuation_headers_are_forwarded(client, http):
    http.add({"return_code": 0})

    client.call("ka10075", PATH_ACCOUNT, {}, cont_yn="Y", next_key="KEY-1")

    headers = http.last()['headers']
    assert headers['cont-yn'] == "Y"
    assert headers['next-key'] == "KEY-1"


def test_nonzero_return_code_raises_api_error(client, http):
    http.add({"return_code": 3, "return_msg": "주문가능금액이 부족합니다"})

    with pytest.raises(KiwoomAPIError) as exc_info:
        client.call("kt10000", PATH_ORDER, {})

    assert exc_info.value.return_code == 3
    assert "주문가능금액" in exc_info.value.return_msg
    assert exc_info.value.api_id == "kt10000"


def test_4xx_raises_http_error(client, http):
    http.add(status_code=401, text="unauthorized")

    with pytest.raises(KiwoomHTTPError) as exc_info:
        client.call("ka10001", PATH_ACCOUNT, {})

    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------- #
# 재시도 정책 — 중복 주문 방지의 핵심
# ---------------------------------------------------------------------- #

@pytest.mark.parametrize("api_id", ["kt10000", "kt10001", "kt10002", "kt10003"])
def test_order_apis_are_never_retried_on_network_error(client, http, api_id):
    """주문은 네트워크 오류가 나도 재전송하지 않는다."""
    http.add_error(requests.ConnectionError("연결 끊김"))

    with pytest.raises(KiwoomOrderUnknownState):
        client.call(api_id, PATH_ORDER, {})

    assert http.call_count == 1


@pytest.mark.parametrize("api_id", ["kt10000", "kt10001", "kt10002", "kt10003"])
def test_order_apis_are_never_retried_on_timeout(client, http, api_id):
    http.add_error(requests.Timeout("read timeout"))

    with pytest.raises(KiwoomOrderUnknownState):
        client.call(api_id, PATH_ORDER, {})

    assert http.call_count == 1


def test_order_5xx_is_unknown_not_retried(client, http):
    """5xx는 주문이 접수됐을 수도 있으므로 UNKNOWN으로 처리하고 재시도하지 않는다."""
    http.add(status_code=502, text="bad gateway")

    with pytest.raises(KiwoomOrderUnknownState):
        client.call("kt10000", PATH_ORDER, {})

    assert http.call_count == 1


def test_query_apis_are_retried(client, http):
    """조회 API는 재시도한다."""
    http.add_error(requests.ConnectionError("일시적 오류"))
    http.add_error(requests.ConnectionError("일시적 오류"))
    http.add({"return_code": 0, "entr": "1000000"})

    result = client.call("kt00001", PATH_ACCOUNT, {})

    assert http.call_count == 3
    assert result.body['entr'] == "1000000"


def test_query_api_gives_up_after_max_retries(client, http):
    for _ in range(3):
        http.add_error(requests.ConnectionError("계속 실패"))

    with pytest.raises(requests.ConnectionError):
        client.call("kt00001", PATH_ACCOUNT, {})

    assert http.call_count == 3


# ---------------------------------------------------------------------- #
# 연속조회
# ---------------------------------------------------------------------- #

def test_call_paged_follows_next_key(client, http):
    http.add({"return_code": 0, "oso": [{"ord_no": "1"}]},
             headers={"cont-yn": "Y", "next-key": "K1"})
    http.add({"return_code": 0, "oso": [{"ord_no": "2"}]},
             headers={"cont-yn": "Y", "next-key": "K2"})
    http.add({"return_code": 0, "oso": [{"ord_no": "3"}]},
             headers={"cont-yn": "N", "next-key": ""})

    pages = client.call_paged("ka10075", PATH_ACCOUNT, {}, page_delay=0)

    assert len(pages) == 3
    assert http.calls[1]['headers']['next-key'] == "K1"
    assert http.calls[2]['headers']['next-key'] == "K2"


def test_call_paged_respects_max_pages(client, http):
    for _ in range(5):
        http.add({"return_code": 0, "oso": []},
                 headers={"cont-yn": "Y", "next-key": "K"})

    pages = client.call_paged("ka10075", PATH_ACCOUNT, {}, max_pages=2,
                              page_delay=0)

    assert len(pages) == 2
    assert http.call_count == 2


def test_call_paged_rejects_order_apis(client):
    with pytest.raises(ValueError, match="주문 API"):
        client.call_paged("kt10000", PATH_ORDER, {})
