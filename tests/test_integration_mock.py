"""
모의투자 서버 실호출 통합 테스트.

기본적으로 제외됩니다. 실행하려면:

    export KIWOOM_APP_KEY=...        # 모의투자 앱키
    export KIWOOM_SECRET_KEY=...
    export KIWOOM_USE_MOCK=true
    python -m pytest tests/test_integration_mock.py -m mock_integration -v

체결되지 않도록 하한가로 1주만 주문하고 즉시 취소합니다.
"""

import pytest

from config import KiwoomConfig
from kiwoom_account import KiwoomAccount
from kiwoom_api_client import KiwoomAPIClient
from kiwoom_order import KiwoomOrder
from kiwoom_quote import KiwoomQuote
from order_guard import OrderGuard, OrderJournal

pytestmark = pytest.mark.mock_integration

SAMSUNG = "005930"


@pytest.fixture
def live(tmp_path, real_credentials):
    """실제 모의투자 자격증명으로 구성된 모듈 묶음."""
    appkey, secretkey = real_credentials

    config = KiwoomConfig()
    config.appkey = appkey
    config.secretkey = secretkey
    config.use_mock = True  # 통합 테스트는 절대 실거래로 가지 않는다
    config.order_journal_path = str(tmp_path / "orders.jsonl")

    client = KiwoomAPIClient.from_config(config)
    account = KiwoomAccount(client)
    quote = KiwoomQuote(client)
    guard = OrderGuard(config, OrderJournal(config.order_journal_path),
                       price_lookup=quote.get_current_price)
    order = KiwoomOrder(client, guard, account=account)
    return config, client, account, quote, order


def test_authenticate(live):
    _, client, _, _, _ = live
    client.oauth.authenticate()
    assert client.oauth.token
    assert client.oauth.is_token_valid()


def test_deposit_and_balance(live):
    _, _, account, _, _ = live

    deposit = account.get_deposit()
    assert 'orderable_amount' in deposit

    balance = account.get_balance()
    assert 'holdings' in balance
    assert isinstance(balance['holdings'], list)


def test_stock_info(live):
    _, _, _, quote, _ = live

    info = quote.get_stock_info(SAMSUNG)
    assert info['stock_code'].endswith(SAMSUNG)
    assert info['current_price'] > 0
    assert info['upper_limit_price'] > info['lower_limit_price']


def test_buy_then_cancel_roundtrip(live):
    """체결되지 않을 가격으로 1주 매수 -> 미체결 확인 -> 취소."""
    _, _, account, quote, order = live

    info = quote.get_stock_info(SAMSUNG)
    price = max(info['lower_limit_price'], 1)

    placed = order.buy(SAMSUNG, quantity=1, price=price)
    assert placed['status'] == "SUCCESS", placed
    order_no = placed['order_no']
    assert order_no

    try:
        unfilled = account.get_unfilled_orders(stock_code=SAMSUNG)
        assert any(o['order_no'] == order_no for o in unfilled), (
            f"주문번호 {order_no}를 미체결 목록에서 찾지 못했습니다: {unfilled}"
        )
    finally:
        cancelled = order.cancel(order_no, SAMSUNG, quantity=0)
        assert cancelled['status'] == "SUCCESS", cancelled


def test_duplicate_order_is_blocked_live(live):
    """실제 서버 상대로도 중복 주문이 차단되는지 확인."""
    _, _, _, quote, order = live

    info = quote.get_stock_info(SAMSUNG)
    price = max(info['lower_limit_price'], 1)

    first = order.buy(SAMSUNG, quantity=1, price=price)
    assert first['status'] == "SUCCESS", first

    try:
        second = order.buy(SAMSUNG, quantity=1, price=price)
        assert second['status'] == "BLOCKED"
        assert second['reason'] == "DUPLICATE"
    finally:
        order.cancel(first['order_no'], SAMSUNG, quantity=0)
