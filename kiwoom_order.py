"""
국내주식 주문.

- kt10000 주식 매수주문 (POST /api/dostk/ordr)
- kt10001 주식 매도주문
- kt10002 주식 정정주문
- kt10003 주식 취소주문

모든 메서드는 예외를 던지지 않고 아래 3가지 상태 중 하나를 담은 dict를
반환합니다. 호출자(특히 LLM)가 "재주문해도 되는가"를 명확히 판단할 수
있어야 하기 때문입니다.

  SUCCESS  - 접수 확정. 주문번호 있음.
  REJECTED - 미접수 확정. 원인 있음. 고쳐서 다시 주문해도 안전.
  UNKNOWN  - 접수 여부 불명. 절대 재주문 금지. 미체결/체결 대조 결과 동봉.
"""

import threading
from typing import Any, Dict, List, Optional

from kiwoom_account import KiwoomAccount
from kiwoom_api_client import PATH_ORDER, KiwoomAPIClient
from kiwoom_errors import (
    KiwoomAPIError,
    KiwoomHTTPError,
    KiwoomOrderBlocked,
    KiwoomOrderUnknownState,
)
from order_guard import OrderGuard, OrderIntent

TR_BUY = "kt10000"
TR_SELL = "kt10001"
TR_MODIFY = "kt10002"
TR_CANCEL = "kt10003"

STATUS_SUCCESS = "SUCCESS"
STATUS_REJECTED = "REJECTED"
STATUS_BLOCKED = "BLOCKED"
STATUS_UNKNOWN = "UNKNOWN"

ORDER_TYPE_LIMIT = "0"    # 보통(지정가)
ORDER_TYPE_MARKET = "3"   # 시장가

_UNKNOWN_ADVICE = (
    "주문이 접수됐는지 확인되지 않았습니다. 절대로 같은 주문을 다시 내지 "
    "마세요. 아래 reconciliation(미체결/체결 대조) 결과를 사용자에게 보여주고, "
    "확실하지 않으면 사용자가 직접 HTS/MTS에서 확인하도록 안내하세요."
)


class KiwoomOrder:
    """주문 실행. 안전장치를 통과한 요청만 전송합니다."""

    def __init__(self, client: KiwoomAPIClient, guard: OrderGuard,
                 account: Optional[KiwoomAccount] = None):
        """
        Args:
            client: API 클라이언트
            guard: 주문 안전장치
            account: 주문 상태 불명 시 대조에 사용할 계좌 조회. None이면 대조 생략.
        """
        self.client = client
        self.guard = guard
        self.account = account
        # 동시 주문이 서로 끼어들어 중복 감지를 우회하지 못하게 직렬화한다.
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 공개 API
    # ------------------------------------------------------------------ #

    def buy(self, stock_code: str, quantity: int, price: int = 0,
            order_type: str = ORDER_TYPE_LIMIT, exchange: str = "KRX",
            condition_price: int = 0, confirm: bool = False,
            force: bool = False) -> Dict[str, Any]:
        """주식 매수주문 (kt10000)."""
        intent = OrderIntent(
            action='buy', stock_code=stock_code, quantity=quantity,
            price=price, order_type=order_type, exchange=exchange,
        )
        body = {
            "dmst_stex_tp": exchange,
            "stk_cd": stock_code,
            "ord_qty": str(quantity),
            "ord_uv": str(price) if price else "",
            "trde_tp": order_type,
            "cond_uv": str(condition_price) if condition_price else "",
        }
        return self._execute(TR_BUY, body, intent, confirm, force)

    def sell(self, stock_code: str, quantity: int, price: int = 0,
             order_type: str = ORDER_TYPE_LIMIT, exchange: str = "KRX",
             condition_price: int = 0, confirm: bool = False,
             force: bool = False) -> Dict[str, Any]:
        """주식 매도주문 (kt10001)."""
        intent = OrderIntent(
            action='sell', stock_code=stock_code, quantity=quantity,
            price=price, order_type=order_type, exchange=exchange,
        )
        body = {
            "dmst_stex_tp": exchange,
            "stk_cd": stock_code,
            "ord_qty": str(quantity),
            "ord_uv": str(price) if price else "",
            "trde_tp": order_type,
            "cond_uv": str(condition_price) if condition_price else "",
        }
        return self._execute(TR_SELL, body, intent, confirm, force)

    def modify(self, original_order_no: str, stock_code: str, quantity: int,
               price: int, exchange: str = "KRX", condition_price: int = 0,
               confirm: bool = False, force: bool = False) -> Dict[str, Any]:
        """
        주식 정정주문 (kt10002).

        Args:
            quantity: 정정수량. 0이면 잔량 전부 정정.
        """
        intent = OrderIntent(
            action='modify', stock_code=stock_code, quantity=quantity,
            price=price, order_type='', exchange=exchange,
            original_order_no=original_order_no,
        )
        body = {
            "dmst_stex_tp": exchange,
            "orig_ord_no": original_order_no,
            "stk_cd": stock_code,
            "mdfy_qty": str(quantity),
            "mdfy_uv": str(price),
            "mdfy_cond_uv": str(condition_price) if condition_price else "",
        }
        return self._execute(TR_MODIFY, body, intent, confirm, force)

    def cancel(self, original_order_no: str, stock_code: str,
               quantity: int = 0, exchange: str = "KRX",
               confirm: bool = False, force: bool = False) -> Dict[str, Any]:
        """
        주식 취소주문 (kt10003).

        Args:
            quantity: 취소수량. 0이면 잔량 전부 취소.
        """
        intent = OrderIntent(
            action='cancel', stock_code=stock_code, quantity=quantity,
            price=0, order_type='', exchange=exchange,
            original_order_no=original_order_no,
        )
        body = {
            "dmst_stex_tp": exchange,
            "orig_ord_no": original_order_no,
            "stk_cd": stock_code,
            "cncl_qty": str(quantity),
        }
        return self._execute(TR_CANCEL, body, intent, confirm, force)

    # ------------------------------------------------------------------ #
    # 실행
    # ------------------------------------------------------------------ #

    def _execute(self, api_id: str, body: Dict[str, Any], intent: OrderIntent,
                 confirm: bool, force: bool) -> Dict[str, Any]:
        with self._lock:
            try:
                self.guard.check(intent, confirm=confirm, force=force)
            except KiwoomOrderBlocked as exc:
                self.guard.record(intent, STATUS_BLOCKED, error=str(exc))
                return {
                    'status': STATUS_BLOCKED,
                    'reason': exc.reason,
                    'message': str(exc),
                    'order_sent': False,
                    'request': _describe(intent),
                }

            try:
                response = self.client.call(api_id, PATH_ORDER, body)
            except KiwoomOrderUnknownState as exc:
                self.guard.record(intent, STATUS_UNKNOWN, error=str(exc))
                return self._handle_unknown(intent, exc)
            except (KiwoomAPIError, KiwoomHTTPError) as exc:
                # 서버가 요청을 받고 거부한 것이므로 주문은 접수되지 않았다.
                self.guard.record(intent, STATUS_REJECTED, error=str(exc))
                return {
                    'status': STATUS_REJECTED,
                    'message': str(exc),
                    'return_code': getattr(exc, 'return_code', None),
                    'order_sent': False,
                    'request': _describe(intent),
                }

            order_no = (response.body.get('ord_no') or '').strip()
            self.guard.record(intent, STATUS_SUCCESS, order_no=order_no)

            result = {
                'status': STATUS_SUCCESS,
                'order_no': order_no,
                'message': response.return_msg or '주문이 접수되었습니다.',
                'order_sent': True,
                'environment': 'mock' if self.client.use_mock else 'real',
                'request': _describe(intent),
            }
            for key in ('base_orig_ord_no', 'mdfy_qty', 'cncl_qty',
                        'dmst_stex_tp'):
                if key in response.body:
                    result[key] = response.body[key]
            return result

    def _handle_unknown(self, intent: OrderIntent,
                        exc: KiwoomOrderUnknownState) -> Dict[str, Any]:
        """
        응답을 확인하지 못한 주문을 미체결/체결 내역과 대조합니다.

        재전송이 아니라 조회이므로 안전합니다.
        """
        result: Dict[str, Any] = {
            'status': STATUS_UNKNOWN,
            'message': str(exc),
            'order_sent': None,
            'do_not_retry': True,
            'advice': _UNKNOWN_ADVICE,
            'environment': 'mock' if self.client.use_mock else 'real',
            'request': _describe(intent),
        }

        if self.account is None:
            result['reconciliation'] = {
                'checked': False,
                'reason': '계좌 조회 수단이 없어 대조하지 못했습니다.',
            }
            return result

        try:
            unfilled = self.account.get_unfilled_orders(
                stock_code=intent.stock_code
            )
            filled = self.account.get_filled_orders(
                stock_code=intent.stock_code
            )
        except Exception as lookup_error:
            result['reconciliation'] = {
                'checked': False,
                'reason': f'대조 조회에 실패했습니다: {lookup_error}',
            }
            return result

        matches = _match_orders(unfilled + filled, intent)
        result['reconciliation'] = {
            'checked': True,
            'matched_count': len(matches),
            'matched_orders': matches,
            'unfilled_orders': unfilled,
            'filled_orders': filled,
            'interpretation': (
                f'동일 종목·수량 주문 {len(matches)}건이 확인되었습니다. '
                f'방금 낸 주문이 접수됐을 가능성이 높습니다.'
                if matches else
                '동일 종목·수량 주문이 확인되지 않았습니다. 다만 반영 지연일 '
                '수 있으므로 잠시 후 미체결 조회로 다시 확인하세요.'
            ),
        }
        return result


# ---------------------------------------------------------------------- #

def _describe(intent: OrderIntent) -> Dict[str, Any]:
    return {
        'action': intent.action,
        'stock_code': intent.stock_code,
        'quantity': intent.quantity,
        'price': intent.price,
        'order_type': intent.order_type,
        'exchange': intent.exchange,
        'original_order_no': intent.original_order_no,
    }


def _match_orders(orders: List[Dict[str, Any]],
                  intent: OrderIntent) -> List[Dict[str, Any]]:
    """대조 대상 주문 중 종목·수량이 일치하는 건을 추립니다."""
    matches = []
    for order in orders:
        if order.get('stock_code') != intent.stock_code:
            continue
        if intent.quantity and order.get('order_quantity') != intent.quantity:
            continue
        matches.append(order)
    return matches
