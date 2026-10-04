"""
국내주식 계좌 조회.

키움 REST API 실제 스펙 기준 (구 OpenAPI+ TR 코드 opw00018 등이 아님):
- ka00001  계좌번호조회          (POST /api/dostk/acnt)
- kt00018  계좌평가잔고내역요청   (POST /api/dostk/acnt)
- kt00001  예수금상세현황요청     (POST /api/dostk/acnt)
- ka10075  미체결요청            (POST /api/dostk/acnt)
- ka10076  체결요청              (POST /api/dostk/acnt)
"""

from typing import Any, Dict, List, Optional

from kiwoom_api_client import PATH_ACCOUNT, KiwoomAPIClient
from kiwoom_parse import pick, to_float, to_int

TR_ACCOUNT_LIST = "ka00001"
TR_BALANCE = "kt00018"
TR_DEPOSIT = "kt00001"
TR_UNFILLED = "ka10075"
TR_FILLED = "ka10076"

#: ka10075 / ka10076 의 거래소구분
STEX_ALL, STEX_KRX, STEX_NXT = "0", "1", "2"


class KiwoomAccount:
    """계좌 잔고 / 예수금 / 체결·미체결 조회."""

    def __init__(self, client: KiwoomAPIClient):
        self.client = client

    # ------------------------------------------------------------------ #
    # 계좌
    # ------------------------------------------------------------------ #

    def get_accounts(self) -> List[str]:
        """계좌번호조회 (ka00001). 계좌번호 목록을 반환합니다."""
        response = self.client.call(TR_ACCOUNT_LIST, PATH_ACCOUNT, {})
        raw = response.body.get('acctNo')

        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if isinstance(raw, str) and raw.strip():
            return [raw.strip()]
        return []

    def get_balance(self, query_type: str = "1",
                    exchange: str = "KRX") -> Dict[str, Any]:
        """
        계좌평가잔고내역요청 (kt00018).

        Args:
            query_type: 조회구분. '1' 합산, '2' 개별
            exchange: 국내거래소구분. 'KRX' 또는 'NXT'

        Returns:
            총매입금액/총평가금액/총평가손익/총수익률/추정예탁자산과 보유종목 목록
        """
        pages = self.client.call_paged(
            TR_BALANCE, PATH_ACCOUNT,
            {"qry_tp": query_type, "dmst_stex_tp": exchange},
        )
        first = pages[0].body

        summary = {
            'total_purchase_amount': to_int(first.get('tot_pur_amt')),
            'total_evaluation_amount': to_int(first.get('tot_evlt_amt')),
            'total_profit_loss': to_int(first.get('tot_evlt_pl')),
            'total_profit_rate': to_float(first.get('tot_prft_rt')),
            'estimated_deposit_asset': to_int(first.get('prsm_dpst_aset_amt')),
            'total_loan_amount': to_int(first.get('tot_loan_amt')),
        }

        holdings: List[Dict[str, Any]] = []
        for page in pages:
            for row in page.body.get('acnt_evlt_remn_indv_tot') or []:
                holdings.append(_parse_holding(row))

        summary['holdings_count'] = len(holdings)
        summary['holdings'] = holdings
        return summary

    def get_holdings(self, exchange: str = "KRX") -> List[Dict[str, Any]]:
        """보유종목 목록만 반환합니다."""
        return self.get_balance(exchange=exchange)['holdings']

    def get_deposit(self, query_type: str = "2") -> Dict[str, Any]:
        """
        예수금상세현황요청 (kt00001).

        Args:
            query_type: 조회구분. '2' 일반조회, '3' 추정조회

        Returns:
            예수금, 주문가능금액, 출금가능금액, 현금미수금 등
        """
        response = self.client.call(
            TR_DEPOSIT, PATH_ACCOUNT, {"qry_tp": query_type}
        )
        body = response.body

        return {
            'deposit': to_int(body.get('entr')),
            'orderable_amount': to_int(body.get('ord_alow_amt')),
            'withdrawable_amount': to_int(body.get('pymn_alow_amt')),
            'substitute_amount': to_int(body.get('repl_amt')),
            'cash_receivable': to_int(body.get('ch_uncla')),
            'stock_margin_cash': to_int(body.get('profa_ch')),
            'd1_estimated_deposit': to_int(body.get('d1_entra')),
        }

    # ------------------------------------------------------------------ #
    # 주문 내역
    # ------------------------------------------------------------------ #

    def get_unfilled_orders(self, stock_code: Optional[str] = None,
                            trade_type: str = "0",
                            exchange_type: str = STEX_ALL) -> List[Dict[str, Any]]:
        """
        미체결요청 (ka10075).

        Args:
            stock_code: 종목코드 6자리. None이면 전체 종목
            trade_type: 매매구분. '0' 전체, '1' 매도, '2' 매수
            exchange_type: 거래소구분. '0' 통합, '1' KRX, '2' NXT
        """
        body = {
            "all_stk_tp": "1" if stock_code else "0",
            "trde_tp": trade_type,
            "stk_cd": stock_code or "",
            "stex_tp": exchange_type,
        }
        pages = self.client.call_paged(TR_UNFILLED, PATH_ACCOUNT, body)

        orders: List[Dict[str, Any]] = []
        for page in pages:
            for row in page.body.get('oso') or []:
                orders.append(_parse_unfilled(row))
        return orders

    def get_filled_orders(self, stock_code: Optional[str] = None,
                          sell_type: str = "0",
                          order_no: str = "",
                          exchange_type: str = STEX_ALL) -> List[Dict[str, Any]]:
        """
        체결요청 (ka10076).

        Args:
            stock_code: 종목코드 6자리. None이면 전체 종목
            sell_type: 매도수구분. '0' 전체, '1' 매도, '2' 매수
            order_no: 이 주문번호보다 과거에 체결된 내역을 조회
            exchange_type: 거래소구분. '0' 통합, '1' KRX, '2' NXT
        """
        body = {
            "stk_cd": stock_code or "",
            "qry_tp": "1" if stock_code else "0",
            "sell_tp": sell_type,
            "ord_no": order_no,
            "stex_tp": exchange_type,
        }
        pages = self.client.call_paged(TR_FILLED, PATH_ACCOUNT, body)

        fills: List[Dict[str, Any]] = []
        for page in pages:
            for row in page.body.get('cntr') or []:
                fills.append(_parse_filled(row))
        return fills


# ---------------------------------------------------------------------- #
# 응답 정규화
# ---------------------------------------------------------------------- #

def _parse_holding(row: Dict[str, Any]) -> Dict[str, Any]:
    """kt00018 의 보유종목 한 건."""
    result = pick(
        row,
        mapping={'stk_cd': 'stock_code', 'stk_nm': 'stock_name'},
        numeric={
            'rmnd_qty': 'quantity',
            'trde_able_qty': 'sellable_quantity',
            'pur_pric': 'purchase_price',
            'cur_prc': 'current_price',
            'pur_amt': 'purchase_amount',
            'evlt_amt': 'evaluation_amount',
            'evltv_prft': 'profit_loss',
        },
    )
    result['profit_rate'] = to_float(row.get('prft_rt'))
    # 가격 필드는 부호 없이 절댓값으로 정리한다
    result['current_price'] = abs(result['current_price'])
    result['purchase_price'] = abs(result['purchase_price'])
    # 종목코드에 붙는 시장 접두어(A005930)를 제거한다
    result['stock_code'] = _strip_market_prefix(result['stock_code'])
    return result


def _parse_unfilled(row: Dict[str, Any]) -> Dict[str, Any]:
    """ka10075 의 미체결 한 건."""
    result = pick(
        row,
        mapping={
            'ord_no': 'order_no',
            'stk_cd': 'stock_code',
            'stk_nm': 'stock_name',
            'ord_stt': 'order_status',
            'io_tp_nm': 'order_side',
            'trde_tp': 'order_type_name',
            'orig_ord_no': 'original_order_no',
            'tm': 'time',
            'stex_tp_txt': 'exchange',
        },
        numeric={
            'ord_qty': 'order_quantity',
            'ord_pric': 'order_price',
            'oso_qty': 'unfilled_quantity',
            'cntr_qty': 'filled_quantity',
            'cntr_pric': 'filled_price',
            'cur_prc': 'current_price',
        },
    )
    result['current_price'] = abs(result['current_price'])
    result['stock_code'] = _strip_market_prefix(result['stock_code'])
    return result


def _parse_filled(row: Dict[str, Any]) -> Dict[str, Any]:
    """ka10076 의 체결 한 건."""
    result = pick(
        row,
        mapping={
            'ord_no': 'order_no',
            'stk_cd': 'stock_code',
            'stk_nm': 'stock_name',
            'ord_stt': 'order_status',
            'io_tp_nm': 'order_side',
            'trde_tp': 'order_type_name',
            'orig_ord_no': 'original_order_no',
            'ord_tm': 'order_time',
            'stex_tp_txt': 'exchange',
        },
        numeric={
            'ord_qty': 'order_quantity',
            'ord_pric': 'order_price',
            'cntr_qty': 'filled_quantity',
            'cntr_pric': 'filled_price',
            'oso_qty': 'unfilled_quantity',
            'tdy_trde_cmsn': 'commission',
            'tdy_trde_tax': 'tax',
        },
    )
    result['stock_code'] = _strip_market_prefix(result['stock_code'])
    return result


def _strip_market_prefix(stock_code: Any) -> str:
    """'A005930' -> '005930'. 키움은 잔고 응답에 시장 접두어를 붙여 보낸다."""
    code = (stock_code or '')
    if not isinstance(code, str):
        code = str(code)
    code = code.strip()
    if len(code) == 7 and code[0].isalpha() and code[1:].isdigit():
        return code[1:]
    return code
