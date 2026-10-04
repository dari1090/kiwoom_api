"""안전장치 테스트: 입력 검증, 중복 차단, 실거래 관문, 금액 상한."""

import pytest

from kiwoom_errors import KiwoomOrderBlocked
from order_guard import OrderGuard, OrderIntent


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_intent(**overrides):
    base = dict(action='buy', stock_code='005930', quantity=1, price=70000,
                order_type='0', exchange='KRX')
    base.update(overrides)
    return OrderIntent(**base)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def guard(config, journal, clock):
    return OrderGuard(config, journal, price_lookup=lambda code: 70000,
                      clock=clock)


# ---------------------------------------------------------------------- #
# 입력 검증
# ---------------------------------------------------------------------- #

@pytest.mark.parametrize("code", ["00593", "0059300", "ABCDEF", "", "005930_XX"])
def test_invalid_stock_code_blocked(guard, code):
    with pytest.raises(KiwoomOrderBlocked) as exc:
        guard.check(make_intent(stock_code=code))
    assert exc.value.reason == KiwoomOrderBlocked.INVALID_INPUT


@pytest.mark.parametrize("code", ["005930", "005930_NX", "005930_AL"])
def test_valid_stock_codes_pass(guard, code):
    guard.check(make_intent(stock_code=code))


@pytest.mark.parametrize("quantity", [0, -1])
def test_non_positive_quantity_blocked(guard, quantity):
    with pytest.raises(KiwoomOrderBlocked) as exc:
        guard.check(make_intent(quantity=quantity))
    assert exc.value.reason == KiwoomOrderBlocked.INVALID_INPUT


def test_cancel_allows_zero_quantity(guard):
    """취소는 수량 0이 '잔량 전부'를 의미하므로 허용된다."""
    guard.check(make_intent(action='cancel', quantity=0, price=0,
                            order_type='', original_order_no='0001234'))


def test_limit_order_requires_price(guard):
    with pytest.raises(KiwoomOrderBlocked, match="주문단가"):
        guard.check(make_intent(order_type='0', price=0))


def test_market_order_does_not_require_price(guard):
    guard.check(make_intent(order_type='3', price=0))


def test_invalid_order_type_blocked(guard):
    with pytest.raises(KiwoomOrderBlocked, match="매매구분"):
        guard.check(make_intent(order_type='99'))


def test_invalid_exchange_blocked(guard):
    with pytest.raises(KiwoomOrderBlocked, match="거래소"):
        guard.check(make_intent(exchange='NASDAQ'))


def test_modify_requires_original_order_no(guard):
    with pytest.raises(KiwoomOrderBlocked, match="원주문번호"):
        guard.check(make_intent(action='modify', order_type='',
                                original_order_no=''))


# ---------------------------------------------------------------------- #
# 중복 차단
# ---------------------------------------------------------------------- #

def test_duplicate_order_within_window_blocked(guard, clock):
    intent = make_intent()
    guard.check(intent)
    guard.record(intent, 'SUCCESS', order_no='0001234')

    clock.advance(30)
    with pytest.raises(KiwoomOrderBlocked) as exc:
        guard.check(intent)

    assert exc.value.reason == KiwoomOrderBlocked.DUPLICATE
    assert "0001234" in str(exc.value)


def test_duplicate_allowed_after_window(guard, clock):
    intent = make_intent()
    guard.record(intent, 'SUCCESS', order_no='0001234')

    clock.advance(61)
    guard.check(intent)


def test_force_bypasses_duplicate_check(guard, clock):
    intent = make_intent()
    guard.record(intent, 'SUCCESS', order_no='0001234')

    clock.advance(5)
    guard.check(intent, force=True)


def test_different_quantity_is_not_duplicate(guard, clock):
    guard.record(make_intent(quantity=1), 'SUCCESS', order_no='0001234')

    clock.advance(5)
    guard.check(make_intent(quantity=2))


def test_rejected_order_is_not_treated_as_duplicate(guard, clock):
    """서버가 거부한 주문은 미접수 확정이므로 다시 시도할 수 있어야 한다."""
    intent = make_intent()
    guard.record(intent, 'REJECTED', error='주문가능금액 부족')

    clock.advance(5)
    guard.check(intent)


def test_unknown_order_is_treated_as_duplicate(guard, clock):
    """접수 여부 불명인 주문은 중복으로 간주해 재전송을 막는다."""
    intent = make_intent()
    guard.record(intent, 'UNKNOWN', error='timeout')

    clock.advance(5)
    with pytest.raises(KiwoomOrderBlocked) as exc:
        guard.check(intent)
    assert exc.value.reason == KiwoomOrderBlocked.DUPLICATE


def test_dedup_survives_new_guard_instance(config, journal, clock):
    """저널을 통해 프로세스 재시작 후에도 중복 감지가 유지된다."""
    intent = make_intent()
    first = OrderGuard(config, journal, clock=clock)
    first.record(intent, 'SUCCESS', order_no='0001234')

    clock.advance(10)
    second = OrderGuard(config, journal, clock=clock)
    with pytest.raises(KiwoomOrderBlocked):
        second.check(intent)


# ---------------------------------------------------------------------- #
# 실거래 관문 (모의투자에서는 적용되지 않음)
# ---------------------------------------------------------------------- #

def test_mock_environment_skips_real_trading_gates(guard):
    """모의투자는 confirm 없이도 통과한다."""
    guard.check(make_intent(), confirm=False)


def test_real_trading_blocked_without_env_flag(config, journal, clock):
    config.use_mock = False
    config.allow_real_trading = False
    guard = OrderGuard(config, journal, clock=clock)

    with pytest.raises(KiwoomOrderBlocked) as exc:
        guard.check(make_intent(), confirm=True)

    assert exc.value.reason == KiwoomOrderBlocked.REAL_TRADING_DISABLED


def test_real_trading_blocked_without_confirm(config, journal, clock):
    config.use_mock = False
    config.allow_real_trading = True
    guard = OrderGuard(config, journal, clock=clock)

    with pytest.raises(KiwoomOrderBlocked) as exc:
        guard.check(make_intent(), confirm=False)

    assert exc.value.reason == KiwoomOrderBlocked.NOT_CONFIRMED


def test_real_trading_passes_with_both_gates(config, journal, clock):
    config.use_mock = False
    config.allow_real_trading = True
    guard = OrderGuard(config, journal, clock=clock)

    guard.check(make_intent(quantity=1, price=70000), confirm=True)


# ---------------------------------------------------------------------- #
# 금액 상한
# ---------------------------------------------------------------------- #

def test_amount_limit_blocks_large_order(config, journal, clock):
    config.use_mock = False
    config.allow_real_trading = True
    config.max_order_amount = 1_000_000
    guard = OrderGuard(config, journal, clock=clock)

    with pytest.raises(KiwoomOrderBlocked) as exc:
        guard.check(make_intent(quantity=100, price=70000), confirm=True)

    assert exc.value.reason == KiwoomOrderBlocked.AMOUNT_LIMIT


def test_market_order_amount_uses_current_price(config, journal, clock):
    config.use_mock = False
    config.allow_real_trading = True
    config.max_order_amount = 100_000
    guard = OrderGuard(config, journal, price_lookup=lambda code: 70000,
                       clock=clock)

    with pytest.raises(KiwoomOrderBlocked) as exc:
        guard.check(make_intent(order_type='3', price=0, quantity=2),
                    confirm=True)

    assert exc.value.reason == KiwoomOrderBlocked.AMOUNT_LIMIT


def test_market_order_blocked_when_price_lookup_fails(config, journal, clock):
    """현재가를 못 가져오면 통과가 아니라 차단이어야 한다."""
    config.use_mock = False
    config.allow_real_trading = True

    def broken_lookup(code):
        raise RuntimeError("시세 서버 응답 없음")

    guard = OrderGuard(config, journal, price_lookup=broken_lookup, clock=clock)

    with pytest.raises(KiwoomOrderBlocked) as exc:
        guard.check(make_intent(order_type='3', price=0), confirm=True)

    assert exc.value.reason == KiwoomOrderBlocked.PRICE_UNKNOWN


def test_market_order_blocked_when_price_is_empty(config, journal, clock):
    config.use_mock = False
    config.allow_real_trading = True
    guard = OrderGuard(config, journal, price_lookup=lambda code: None,
                       clock=clock)

    with pytest.raises(KiwoomOrderBlocked) as exc:
        guard.check(make_intent(order_type='3', price=0), confirm=True)

    assert exc.value.reason == KiwoomOrderBlocked.PRICE_UNKNOWN


def test_cancel_skips_amount_check(config, journal, clock):
    config.use_mock = False
    config.allow_real_trading = True
    config.max_order_amount = 1
    guard = OrderGuard(config, journal, clock=clock)

    guard.check(
        make_intent(action='cancel', quantity=0, price=0, order_type='',
                    original_order_no='0001234'),
        confirm=True,
    )
