"""
국내주식 종목정보 조회.

- ka10001 주식기본정보요청 (POST /api/dostk/stkinfo)
"""

from typing import Any, Dict, Optional

from kiwoom_api_client import PATH_STOCK_INFO, KiwoomAPIClient
from kiwoom_parse import pick, to_float, to_int

TR_STOCK_INFO = "ka10001"


class KiwoomQuote:
    """종목 기본정보 / 현재가 조회."""

    def __init__(self, client: KiwoomAPIClient):
        self.client = client

    def get_stock_info(self, stock_code: str) -> Dict[str, Any]:
        """
        주식기본정보요청 (ka10001).

        Args:
            stock_code: 종목코드. 거래소별 표기 가능 (KRX:005930, NXT:005930_NX,
                SOR:005930_AL)

        Returns:
            현재가, 등락률, 시가/고가/저가, 상/하한가, PER/PBR 등
        """
        response = self.client.call(
            TR_STOCK_INFO, PATH_STOCK_INFO, {"stk_cd": stock_code}
        )
        body = response.body

        info = pick(
            body,
            mapping={
                'stk_cd': 'stock_code',
                'stk_nm': 'stock_name',
                'setl_mm': 'settlement_month',
            },
            numeric={
                'cur_prc': 'current_price',
                'base_pric': 'base_price',
                'pred_pre': 'change',
                'open_pric': 'open_price',
                'high_pric': 'high_price',
                'low_pric': 'low_price',
                'upl_pric': 'upper_limit_price',
                'lst_pric': 'lower_limit_price',
                'trde_qty': 'volume',
                'mac': 'market_cap',
                'fav': 'face_value',
            },
        )
        # 키움은 가격에 등락 부호를 붙여 보낸다 ('-70000' = 70000원, 하락).
        # 가격 자체는 절댓값이어야 하고, 전일대비(change)만 부호를 유지한다.
        for field in ('current_price', 'base_price', 'open_price', 'high_price',
                      'low_price', 'upper_limit_price', 'lower_limit_price'):
            info[field] = abs(info[field])

        info['change_rate'] = to_float(body.get('flu_rt'))
        info['per'] = to_float(body.get('per'))
        info['pbr'] = to_float(body.get('pbr'))
        info['roe'] = to_float(body.get('roe'))
        info['eps'] = to_int(body.get('eps'))
        return info

    def get_current_price(self, stock_code: str) -> Optional[int]:
        """
        현재가만 정수로 반환합니다.

        조회는 됐지만 값이 비어 있으면 None을 반환합니다.
        """
        price = self.get_stock_info(stock_code).get('current_price')
        return price if price else None
