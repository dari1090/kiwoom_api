"""
키움증권 주식 매수/매도 MCP 서버.

stdio 트랜스포트로 동작합니다. 실행:
    python mcp_server.py

필요한 환경변수는 .env.example 을 참고하세요.
"""

import sys
from typing import Any, Dict, Optional

import requests
from mcp.server.fastmcp import FastMCP

from config import KiwoomConfig
from kiwoom_account import KiwoomAccount
from kiwoom_api_client import KiwoomAPIClient
from kiwoom_errors import KiwoomError
from kiwoom_order import KiwoomOrder
from kiwoom_quote import KiwoomQuote
from order_guard import OrderGuard, OrderJournal

mcp = FastMCP("kiwoom-trading")

_config: Optional[KiwoomConfig] = None
_client: Optional[KiwoomAPIClient] = None
_account: Optional[KiwoomAccount] = None
_quote: Optional[KiwoomQuote] = None
_order: Optional[KiwoomOrder] = None


def _build() -> None:
    """지연 초기화. 첫 툴 호출 시점에 인증 정보를 검증합니다."""
    global _config, _client, _account, _quote, _order

    if _order is not None:
        return

    _config = KiwoomConfig()
    _config.validate()

    _client = KiwoomAPIClient.from_config(_config)
    _account = KiwoomAccount(_client)
    _quote = KiwoomQuote(_client)

    guard = OrderGuard(
        config=_config,
        journal=OrderJournal(_config.order_journal_path),
        price_lookup=_quote.get_current_price,
    )
    _order = KiwoomOrder(_client, guard, account=_account)


def _ensure():
    """초기화 후 (order, account, quote, config) 를 반환합니다."""
    _build()
    return _order, _account, _quote, _config


#: 툴에서 구조화된 오류로 변환할 예외들. 원시 예외 대신 status='ERROR' 를
#: 돌려주어 호출자가 일관되게 해석할 수 있게 합니다.
TOOL_ERRORS = (KiwoomError, ValueError, requests.RequestException)


def _fail(exc: Exception) -> Dict[str, Any]:
    return {'status': 'ERROR', 'message': str(exc),
            'error_type': type(exc).__name__}


# ====================================================================== #
# 주문 툴
# ====================================================================== #

@mcp.tool()
def kiwoom_buy_stock(stock_code: str, quantity: int, price: int = 0,
                     order_type: str = "0", exchange: str = "",
                     confirm: bool = False,
                     force: bool = False) -> Dict[str, Any]:
    """
    국내주식 매수주문을 냅니다.

    실거래 환경에서는 반드시 사용자에게 종목/수량/단가를 말로 확인받은 뒤
    confirm=true 로 호출하세요. 임의로 confirm=true 를 넣지 마세요.

    반환값의 status 를 반드시 확인하세요:
      SUCCESS  - 접수됨. order_no 확인.
      REJECTED - 미접수 확정. 원인을 고쳐 다시 시도해도 안전합니다.
      BLOCKED  - 안전장치가 차단. reason 을 사용자에게 설명하세요.
      UNKNOWN  - 접수 여부 불명. 절대 재주문하지 말고 reconciliation 을 보고하세요.

    Args:
        stock_code: 종목코드 6자리 (예: '005930'). NXT는 '005930_NX', SOR는 '005930_AL'
        quantity: 주문수량 (주). 1 이상
        price: 주문단가 (원). 시장가 주문이면 0
        order_type: 매매구분. '0' 보통(지정가), '3' 시장가, '5' 조건부지정가,
            '6' 최유리지정가, '7' 최우선지정가, '10' 보통(IOC), '13' 시장가(IOC),
            '20' 보통(FOK), '23' 시장가(FOK), '61' 장시작전시간외,
            '62' 시간외단일가, '81' 장마감후시간외
        exchange: 거래소. 'KRX', 'NXT', 'SOR'. 비우면 기본 설정값
        confirm: 실거래 주문 확인. 사용자 동의 없이 true 로 설정 금지
        force: 중복 차단 무시. 의도적인 반복(분할매수) 시에만 사용
    """
    try:
        order, _, _, config = _ensure()
        return order.buy(
            stock_code=stock_code, quantity=quantity, price=price,
            order_type=order_type, exchange=exchange or config.default_exchange,
            confirm=confirm, force=force,
        )
    except TOOL_ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def kiwoom_sell_stock(stock_code: str, quantity: int, price: int = 0,
                      order_type: str = "0", exchange: str = "",
                      confirm: bool = False,
                      force: bool = False) -> Dict[str, Any]:
    """
    국내주식 매도주문을 냅니다.

    실거래 환경에서는 반드시 사용자에게 종목/수량/단가를 말로 확인받은 뒤
    confirm=true 로 호출하세요.

    매도 전에 kiwoom_get_balance 로 보유수량(sellable_quantity)을 확인하는 것을
    권장합니다.

    반환값의 status 는 kiwoom_buy_stock 과 동일하게 해석하세요.

    Args:
        stock_code: 종목코드 6자리
        quantity: 주문수량 (주)
        price: 주문단가 (원). 시장가면 0
        order_type: 매매구분. '0' 지정가, '3' 시장가 등
        exchange: 거래소. 'KRX', 'NXT', 'SOR'
        confirm: 실거래 주문 확인
        force: 중복 차단 무시
    """
    try:
        order, _, _, config = _ensure()
        return order.sell(
            stock_code=stock_code, quantity=quantity, price=price,
            order_type=order_type, exchange=exchange or config.default_exchange,
            confirm=confirm, force=force,
        )
    except TOOL_ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def kiwoom_modify_order(original_order_no: str, stock_code: str,
                        quantity: int, price: int, exchange: str = "",
                        confirm: bool = False,
                        force: bool = False) -> Dict[str, Any]:
    """
    미체결 주문의 수량/단가를 정정합니다.

    원주문번호는 kiwoom_get_unfilled_orders 로 확인하세요.

    Args:
        original_order_no: 원주문번호 (매수/매도 응답의 order_no)
        stock_code: 종목코드 6자리
        quantity: 정정수량. 0이면 잔량 전부 정정
        price: 정정단가 (원). 1 이상 필수
        exchange: 거래소
        confirm: 실거래 확인
        force: 중복 차단 무시
    """
    try:
        order, _, _, config = _ensure()
        return order.modify(
            original_order_no=original_order_no, stock_code=stock_code,
            quantity=quantity, price=price,
            exchange=exchange or config.default_exchange,
            confirm=confirm, force=force,
        )
    except TOOL_ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def kiwoom_cancel_order(original_order_no: str, stock_code: str,
                        quantity: int = 0, exchange: str = "",
                        confirm: bool = False,
                        force: bool = False) -> Dict[str, Any]:
    """
    미체결 주문을 취소합니다.

    Args:
        original_order_no: 원주문번호
        stock_code: 종목코드 6자리
        quantity: 취소수량. 0이면 잔량 전부 취소
        exchange: 거래소
        confirm: 실거래 확인
        force: 중복 차단 무시
    """
    try:
        order, _, _, config = _ensure()
        return order.cancel(
            original_order_no=original_order_no, stock_code=stock_code,
            quantity=quantity, exchange=exchange or config.default_exchange,
            confirm=confirm, force=force,
        )
    except TOOL_ERRORS as exc:
        return _fail(exc)


# ====================================================================== #
# 조회 툴
# ====================================================================== #

@mcp.tool()
def kiwoom_get_balance(exchange: str = "KRX") -> Dict[str, Any]:
    """
    계좌 평가잔고와 보유종목을 조회합니다 (kt00018).

    보유종목마다 quantity(보유수량), sellable_quantity(매도가능수량),
    purchase_price(매입가), current_price(현재가), profit_loss(평가손익),
    profit_rate(수익률%)를 반환합니다.

    Args:
        exchange: 국내거래소구분. 'KRX' 또는 'NXT'
    """
    try:
        _, account, _, _ = _ensure()
        return account.get_balance(exchange=exchange)
    except TOOL_ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def kiwoom_get_deposit() -> Dict[str, Any]:
    """
    예수금과 주문가능금액을 조회합니다 (kt00001).

    매수 주문 전에 orderable_amount(주문가능금액)를 확인하는 데 사용하세요.
    """
    try:
        _, account, _, _ = _ensure()
        return account.get_deposit()
    except TOOL_ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def kiwoom_get_unfilled_orders(stock_code: str = "",
                               trade_type: str = "0") -> Any:
    """
    미체결 주문 목록을 조회합니다 (ka10075).

    정정/취소에 필요한 order_no 를 여기서 확인합니다.

    Args:
        stock_code: 종목코드 6자리. 비우면 전체 종목
        trade_type: 매매구분. '0' 전체, '1' 매도, '2' 매수
    """
    try:
        _, account, _, _ = _ensure()
        return account.get_unfilled_orders(
            stock_code=stock_code or None, trade_type=trade_type
        )
    except TOOL_ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def kiwoom_get_filled_orders(stock_code: str = "",
                             sell_type: str = "0") -> Any:
    """
    체결 내역을 조회합니다 (ka10076).

    Args:
        stock_code: 종목코드 6자리. 비우면 전체 종목
        sell_type: 매도수구분. '0' 전체, '1' 매도, '2' 매수
    """
    try:
        _, account, _, _ = _ensure()
        return account.get_filled_orders(
            stock_code=stock_code or None, sell_type=sell_type
        )
    except TOOL_ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def kiwoom_get_stock_info(stock_code: str) -> Dict[str, Any]:
    """
    종목 기본정보와 현재가를 조회합니다 (ka10001).

    지정가 주문의 단가를 정할 때 current_price, upper_limit_price(상한가),
    lower_limit_price(하한가)를 참고하세요.

    Args:
        stock_code: 종목코드 6자리
    """
    try:
        _, _, quote, _ = _ensure()
        return quote.get_stock_info(stock_code)
    except TOOL_ERRORS as exc:
        return _fail(exc)


@mcp.tool()
def kiwoom_get_trading_status() -> Dict[str, Any]:
    """
    현재 MCP 서버의 거래 환경과 안전장치 설정을 확인합니다.

    주문을 내기 전에 모의투자인지 실거래인지 확인할 때 사용하세요.
    """
    try:
        _, _, _, config = _ensure()
        return {
            'environment': 'mock' if config.use_mock else 'real',
            'base_url': _client.base_url if _client else None,
            'real_trading_allowed': config.allow_real_trading,
            'max_order_amount': config.max_order_amount,
            'dedup_window_seconds': config.dedup_window_seconds,
            'default_exchange': config.default_exchange,
            'order_journal': config.order_journal_path,
        }
    except TOOL_ERRORS as exc:
        return _fail(exc)


# ====================================================================== #

def main() -> None:
    config = KiwoomConfig()
    if config.is_real_trading:
        banner = (
            "\n" + "!" * 68 + "\n"
            "  경고: 실거래 환경(api.kiwoom.com)으로 기동합니다.\n"
            f"  실주문 허용 : {config.allow_real_trading}\n"
            f"  1회 한도    : {config.max_order_amount:,}원\n"
            f"  중복 차단   : {config.dedup_window_seconds}초\n"
            "  모의투자로 바꾸려면 KIWOOM_USE_MOCK=true 를 설정하세요.\n"
            + "!" * 68 + "\n"
        )
    else:
        banner = "[kiwoom-mcp] 모의투자 환경(mockapi.kiwoom.com)으로 기동합니다.\n"

    print(banner, file=sys.stderr, flush=True)
    mcp.run()


if __name__ == "__main__":
    main()
