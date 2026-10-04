"""계좌/시세 조회 테스트: api-id, 요청 바디, 응답 정규화."""

import pytest

from kiwoom_account import KiwoomAccount
from kiwoom_parse import to_float, to_int
from kiwoom_quote import KiwoomQuote


@pytest.fixture
def account(client):
    return KiwoomAccount(client)


@pytest.fixture
def quote(client):
    return KiwoomQuote(client)


# ---------------------------------------------------------------------- #
# 파싱 헬퍼
# ---------------------------------------------------------------------- #

@pytest.mark.parametrize("raw,expected", [
    ("+000070000", 70000),
    ("-000001500", -1500),
    ("000000012345", 12345),
    ("1,234", 1234),
    ("", 0),
    (None, 0),
    ("12.75", 12),
    ("abc", 0),
])
def test_to_int(raw, expected):
    assert to_int(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("-3.25", -3.25),
    ("+1.50", 1.5),
    ("", 0.0),
    ("N/A", 0.0),
])
def test_to_float(raw, expected):
    assert to_float(raw) == expected


# ---------------------------------------------------------------------- #
# 잔고
# ---------------------------------------------------------------------- #

def test_get_balance_request_and_parsing(account, http):
    http.add({
        "return_code": 0,
        "tot_pur_amt": "000000010000000",
        "tot_evlt_amt": "000000010500000",
        "tot_evlt_pl": "000000000500000",
        "tot_prft_rt": "5.00",
        "prsm_dpst_aset_amt": "000000012000000",
        "acnt_evlt_remn_indv_tot": [{
            "stk_cd": "A005930",
            "stk_nm": "삼성전자",
            "rmnd_qty": "000000000010",
            "trde_able_qty": "000000000010",
            "pur_pric": "000000070000",
            "cur_prc": "-000000071000",
            "pur_amt": "000000700000",
            "evlt_amt": "000000710000",
            "evltv_prft": "000000010000",
            "prft_rt": "1.43",
        }],
    })

    result = account.get_balance()

    call = http.last()
    assert call['headers']['api-id'] == "kt00018"
    assert call['url'].endswith("/api/dostk/acnt")
    assert call['json'] == {"qry_tp": "1", "dmst_stex_tp": "KRX"}

    assert result['total_purchase_amount'] == 10_000_000
    assert result['total_profit_rate'] == 5.0
    assert result['holdings_count'] == 1

    holding = result['holdings'][0]
    assert holding['stock_code'] == "005930"   # A 접두어 제거
    assert holding['stock_name'] == "삼성전자"
    assert holding['quantity'] == 10
    assert holding['sellable_quantity'] == 10
    assert holding['current_price'] == 71000   # 부호 제거
    assert holding['profit_rate'] == 1.43


def test_get_balance_merges_continuation_pages(account, http):
    http.add({"return_code": 0, "acnt_evlt_remn_indv_tot": [
        {"stk_cd": "A005930", "rmnd_qty": "1"}]},
        headers={"cont-yn": "Y", "next-key": "K1"})
    http.add({"return_code": 0, "acnt_evlt_remn_indv_tot": [
        {"stk_cd": "A000660", "rmnd_qty": "2"}]},
        headers={"cont-yn": "N"})

    result = account.get_balance()

    assert result['holdings_count'] == 2
    assert [h['stock_code'] for h in result['holdings']] == ["005930", "000660"]


# ---------------------------------------------------------------------- #
# 예수금
# ---------------------------------------------------------------------- #

def test_get_deposit(account, http):
    http.add({
        "return_code": 0,
        "entr": "000000005000000",
        "ord_alow_amt": "000000004800000",
        "pymn_alow_amt": "000000004500000",
        "ch_uncla": "000000000000000",
    })

    result = account.get_deposit()

    assert http.last()['headers']['api-id'] == "kt00001"
    assert http.last()['json'] == {"qry_tp": "2"}
    assert result['deposit'] == 5_000_000
    assert result['orderable_amount'] == 4_800_000
    assert result['withdrawable_amount'] == 4_500_000


# ---------------------------------------------------------------------- #
# 미체결 / 체결
# ---------------------------------------------------------------------- #

def test_get_unfilled_orders_all_stocks(account, http):
    http.add({"return_code": 0, "oso": []})

    account.get_unfilled_orders()

    assert http.last()['headers']['api-id'] == "ka10075"
    assert http.last()['json'] == {
        "all_stk_tp": "0", "trde_tp": "0", "stk_cd": "", "stex_tp": "0",
    }


def test_get_unfilled_orders_single_stock(account, http):
    http.add({"return_code": 0, "oso": [{
        "ord_no": "0539055", "stk_cd": "A005930", "stk_nm": "삼성전자",
        "ord_qty": "10", "ord_pric": "70000", "oso_qty": "4",
        "cntr_qty": "6", "ord_stt": "접수", "io_tp_nm": "+매수",
        "cur_prc": "-71000",
    }]})

    orders = account.get_unfilled_orders(stock_code="005930")

    assert http.last()['json']['all_stk_tp'] == "1"
    assert http.last()['json']['stk_cd'] == "005930"

    order = orders[0]
    assert order['order_no'] == "0539055"
    assert order['stock_code'] == "005930"
    assert order['order_quantity'] == 10
    assert order['unfilled_quantity'] == 4
    assert order['filled_quantity'] == 6
    assert order['current_price'] == 71000


def test_get_filled_orders(account, http):
    http.add({"return_code": 0, "cntr": [{
        "ord_no": "0539055", "stk_cd": "A005930", "stk_nm": "삼성전자",
        "ord_qty": "10", "cntr_qty": "10", "cntr_pric": "70000",
        "ord_stt": "체결", "io_tp_nm": "+매수", "tdy_trde_cmsn": "105",
        "tdy_trde_tax": "0",
    }]})

    fills = account.get_filled_orders(stock_code="005930")

    assert http.last()['headers']['api-id'] == "ka10076"
    assert http.last()['json']['qry_tp'] == "1"
    assert fills[0]['filled_quantity'] == 10
    assert fills[0]['commission'] == 105


# ---------------------------------------------------------------------- #
# 계좌번호 / 종목정보
# ---------------------------------------------------------------------- #

def test_get_accounts_handles_list_and_string(account, http):
    http.add({"return_code": 0, "acctNo": ["1234567890", "0987654321"]})
    assert account.get_accounts() == ["1234567890", "0987654321"]

    http.add({"return_code": 0, "acctNo": "1234567890"})
    assert account.get_accounts() == ["1234567890"]


def test_get_stock_info(quote, http):
    http.add({
        "return_code": 0,
        "stk_cd": "005930", "stk_nm": "삼성전자",
        "cur_prc": "-71000", "base_pric": "72000",
        "open_pric": "+72500", "high_pric": "+72800", "low_pric": "-70900",
        "upl_pric": "+93600", "lst_pric": "-50400",
        "pred_pre": "-1000", "flu_rt": "-1.39",
        "trde_qty": "12345678", "per": "13.5", "pbr": "1.2",
    })

    info = quote.get_stock_info("005930")

    assert http.last()['headers']['api-id'] == "ka10001"
    assert http.last()['url'].endswith("/api/dostk/stkinfo")
    assert http.last()['json'] == {"stk_cd": "005930"}

    assert info['stock_name'] == "삼성전자"
    assert info['current_price'] == 71000       # 부호 제거
    assert info['low_price'] == 70900
    assert info['upper_limit_price'] == 93600
    assert info['change'] == -1000              # 전일대비는 부호 유지
    assert info['change_rate'] == -1.39
    assert info['per'] == 13.5


def test_get_current_price(quote, http):
    http.add({"return_code": 0, "cur_prc": "-71000"})
    assert quote.get_current_price("005930") == 71000

    http.add({"return_code": 0, "cur_prc": ""})
    assert quote.get_current_price("005930") is None
