"""주문 실행 테스트: 요청 바디 스펙 일치, 3상태 분류, 대조."""

import pytest
import requests

from kiwoom_account import KiwoomAccount
from kiwoom_order import KiwoomOrder
from order_guard import OrderGuard, OrderJournal


@pytest.fixture
def order(client, config, journal):
    guard = OrderGuard(config, journal, price_lookup=lambda code: 70000)
    return KiwoomOrder(client, guard, account=KiwoomAccount(client))


# ---------------------------------------------------------------------- #
# 요청 바디가 공식 스펙과 일치하는지
# ---------------------------------------------------------------------- #

def test_buy_sends_spec_compliant_body(order, http):
    http.add({"return_code": 0, "return_msg": "KRX 매수주문이 완료되었습니다.",
              "ord_no": "0539055", "dmst_stex_tp": "KRX"})

    result = order.buy("005930", quantity=10, price=70000)

    call = http.last()
    assert call['headers']['api-id'] == "kt10000"
    assert call['url'].endswith("/api/dostk/ordr")
    assert call['json'] == {
        "dmst_stex_tp": "KRX",
        "stk_cd": "005930",
        "ord_qty": "10",
        "ord_uv": "70000",
        "trde_tp": "0",
        "cond_uv": "",
    }
    assert result['status'] == "SUCCESS"
    assert result['order_no'] == "0539055"


def test_sell_uses_kt10001(order, http):
    http.add({"return_code": 0, "ord_no": "0539056"})

    order.sell("005930", quantity=5, price=71000)

    assert http.last()['headers']['api-id'] == "kt10001"


def test_market_order_sends_empty_price(order, http):
    http.add({"return_code": 0, "ord_no": "0539057"})

    order.buy("005930", quantity=1, price=0, order_type="3")

    assert http.last()['json']['ord_uv'] == ""
    assert http.last()['json']['trde_tp'] == "3"


def test_modify_sends_spec_compliant_body(order, http):
    http.add({"return_code": 0, "ord_no": "0539058",
              "base_orig_ord_no": "0539055", "mdfy_qty": "5"})

    result = order.modify("0539055", "005930", quantity=5, price=69000)

    assert http.last()['headers']['api-id'] == "kt10002"
    assert http.last()['json'] == {
        "dmst_stex_tp": "KRX",
        "orig_ord_no": "0539055",
        "stk_cd": "005930",
        "mdfy_qty": "5",
        "mdfy_uv": "69000",
        "mdfy_cond_uv": "",
    }
    assert result['status'] == "SUCCESS"


def test_cancel_sends_spec_compliant_body(order, http):
    http.add({"return_code": 0, "ord_no": "0539059",
              "base_orig_ord_no": "0539055", "cncl_qty": "0"})

    order.cancel("0539055", "005930", quantity=0)

    assert http.last()['headers']['api-id'] == "kt10003"
    assert http.last()['json'] == {
        "dmst_stex_tp": "KRX",
        "orig_ord_no": "0539055",
        "stk_cd": "005930",
        "cncl_qty": "0",
    }


# ---------------------------------------------------------------------- #
# 결과 3상태 분류
# ---------------------------------------------------------------------- #

def test_server_rejection_is_rejected_not_raised(order, http):
    http.add({"return_code": 3, "return_msg": "주문가능금액이 부족합니다"})

    result = order.buy("005930", quantity=10, price=70000)

    assert result['status'] == "REJECTED"
    assert result['order_sent'] is False
    assert "주문가능금액" in result['message']


def test_blocked_order_never_reaches_network(order, http):
    result = order.buy("00593", quantity=10, price=70000)

    assert result['status'] == "BLOCKED"
    assert result['order_sent'] is False
    assert http.call_count == 0


def test_timeout_produces_unknown_with_reconciliation(order, http):
    http.add_error(requests.Timeout("read timeout"))
    # 대조: 미체결 조회 -> 체결 조회
    http.add({"return_code": 0, "oso": [{
        "ord_no": "0539060", "stk_cd": "A005930", "stk_nm": "삼성전자",
        "ord_qty": "10", "ord_pric": "70000", "oso_qty": "10",
        "ord_stt": "접수", "io_tp_nm": "+매수",
    }]})
    http.add({"return_code": 0, "cntr": []})

    result = order.buy("005930", quantity=10, price=70000)

    assert result['status'] == "UNKNOWN"
    assert result['order_sent'] is None
    assert result['do_not_retry'] is True
    assert result['reconciliation']['checked'] is True
    assert result['reconciliation']['matched_count'] == 1
    assert result['reconciliation']['matched_orders'][0]['order_no'] == "0539060"


def test_unknown_reports_when_no_matching_order_found(order, http):
    http.add_error(requests.ConnectionError("연결 끊김"))
    http.add({"return_code": 0, "oso": []})
    http.add({"return_code": 0, "cntr": []})

    result = order.buy("005930", quantity=10, price=70000)

    assert result['status'] == "UNKNOWN"
    assert result['reconciliation']['matched_count'] == 0
    assert "확인되지 않았습니다" in result['reconciliation']['interpretation']


def test_unknown_reports_reconciliation_failure(order, http):
    http.add_error(requests.Timeout("read timeout"))
    for _ in range(3):  # 미체결 조회도 실패 (조회는 재시도함)
        http.add_error(requests.ConnectionError("계속 실패"))

    result = order.buy("005930", quantity=10, price=70000)

    assert result['status'] == "UNKNOWN"
    assert result['reconciliation']['checked'] is False


def test_order_is_not_retransmitted_on_timeout(order, http):
    """가장 중요한 보장: 타임아웃 시 주문 API는 한 번만 전송된다."""
    http.add_error(requests.Timeout("read timeout"))
    http.add({"return_code": 0, "oso": []})
    http.add({"return_code": 0, "cntr": []})

    order.buy("005930", quantity=10, price=70000)

    order_calls = [c for c in http.calls
                   if c['headers'].get('api-id') == "kt10000"]
    assert len(order_calls) == 1


# ---------------------------------------------------------------------- #
# 중복 방지 (end-to-end)
# ---------------------------------------------------------------------- #

def test_repeated_identical_order_is_blocked(order, http):
    http.add({"return_code": 0, "ord_no": "0539055"})

    first = order.buy("005930", quantity=10, price=70000)
    second = order.buy("005930", quantity=10, price=70000)

    assert first['status'] == "SUCCESS"
    assert second['status'] == "BLOCKED"
    assert second['reason'] == "DUPLICATE"
    # 두 번째 주문은 네트워크로 나가지 않았다
    assert http.call_count == 1


def test_force_allows_intentional_repeat(order, http):
    http.add({"return_code": 0, "ord_no": "0539055"})
    http.add({"return_code": 0, "ord_no": "0539056"})

    order.buy("005930", quantity=10, price=70000)
    second = order.buy("005930", quantity=10, price=70000, force=True)

    assert second['status'] == "SUCCESS"
    assert http.call_count == 2


def test_rejected_order_can_be_retried_immediately(order, http):
    http.add({"return_code": 3, "return_msg": "주문가능금액이 부족합니다"})
    http.add({"return_code": 0, "ord_no": "0539055"})

    first = order.buy("005930", quantity=10, price=70000)
    second = order.buy("005930", quantity=10, price=70000)

    assert first['status'] == "REJECTED"
    assert second['status'] == "SUCCESS"


def test_unknown_order_blocks_identical_retry(order, http):
    http.add_error(requests.Timeout("read timeout"))
    http.add({"return_code": 0, "oso": []})
    http.add({"return_code": 0, "cntr": []})

    first = order.buy("005930", quantity=10, price=70000)
    second = order.buy("005930", quantity=10, price=70000)

    assert first['status'] == "UNKNOWN"
    assert second['status'] == "BLOCKED"
    assert second['reason'] == "DUPLICATE"


# ---------------------------------------------------------------------- #
# 저널
# ---------------------------------------------------------------------- #

def test_journal_records_every_attempt(order, http, journal):
    http.add({"return_code": 0, "ord_no": "0539055"})

    order.buy("005930", quantity=10, price=70000)
    order.buy("00593", quantity=10, price=70000)  # BLOCKED

    records = journal.recent(3600)
    statuses = {r['status'] for r in records}
    assert statuses == {"SUCCESS", "BLOCKED"}
    assert all('acnt_no' not in r for r in records)
