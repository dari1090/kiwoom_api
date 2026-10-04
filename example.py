"""
키움 REST API 모듈 사용 예제.

환경변수를 설정한 뒤 실행하세요:
    export KIWOOM_APP_KEY=...
    export KIWOOM_SECRET_KEY=...
    export KIWOOM_USE_MOCK=true
    python example.py

조회만 수행하는 것이 기본이며, 주문 예제는 --order 옵션을 줘야 실행됩니다
(모의투자 환경에서만).
"""

import argparse
import sys

from config import KiwoomConfig
from kiwoom_account import KiwoomAccount
from kiwoom_api_client import KiwoomAPIClient
from kiwoom_errors import KiwoomError
from kiwoom_order import KiwoomOrder
from kiwoom_quote import KiwoomQuote
from order_guard import OrderGuard, OrderJournal

SAMSUNG = "005930"


def build(config: KiwoomConfig):
    client = KiwoomAPIClient.from_config(config)
    account = KiwoomAccount(client)
    quote = KiwoomQuote(client)
    guard = OrderGuard(
        config,
        journal=OrderJournal(config.order_journal_path),
        price_lookup=quote.get_current_price,
    )
    order = KiwoomOrder(client, guard, account=account)
    return client, account, quote, order


def show_deposit(account: KiwoomAccount):
    print("\n=== 예수금 (kt00001) ===")
    deposit = account.get_deposit()
    print(f"  예수금        : {deposit['deposit']:,}원")
    print(f"  주문가능금액  : {deposit['orderable_amount']:,}원")
    print(f"  출금가능금액  : {deposit['withdrawable_amount']:,}원")


def show_balance(account: KiwoomAccount):
    print("\n=== 계좌평가잔고 (kt00018) ===")
    balance = account.get_balance()
    print(f"  총매입금액    : {balance['total_purchase_amount']:,}원")
    print(f"  총평가금액    : {balance['total_evaluation_amount']:,}원")
    print(f"  총평가손익    : {balance['total_profit_loss']:,}원")
    print(f"  총수익률      : {balance['total_profit_rate']:.2f}%")

    if not balance['holdings']:
        print("  보유종목 없음")
        return

    print(f"\n  {'종목코드':<10}{'종목명':<14}{'수량':>8}{'매입가':>12}"
          f"{'현재가':>12}{'평가손익':>14}{'수익률':>9}")
    print("  " + "-" * 80)
    for h in balance['holdings']:
        print(f"  {h['stock_code']:<10}{h['stock_name']:<14}"
              f"{h['quantity']:>8,}{h['purchase_price']:>12,}"
              f"{h['current_price']:>12,}{h['profit_loss']:>14,}"
              f"{h['profit_rate']:>8.2f}%")


def show_stock(quote: KiwoomQuote, stock_code: str):
    print(f"\n=== 종목정보 {stock_code} (ka10001) ===")
    info = quote.get_stock_info(stock_code)
    print(f"  {info['stock_name']} ({info['stock_code']})")
    print(f"  현재가        : {info['current_price']:,}원 "
          f"({info['change']:+,}, {info['change_rate']:+.2f}%)")
    print(f"  시가/고가/저가: {info['open_price']:,} / "
          f"{info['high_price']:,} / {info['low_price']:,}")
    print(f"  상한가/하한가 : {info['upper_limit_price']:,} / "
          f"{info['lower_limit_price']:,}")
    print(f"  PER / PBR     : {info['per']} / {info['pbr']}")


def show_orders(account: KiwoomAccount):
    print("\n=== 미체결 (ka10075) ===")
    unfilled = account.get_unfilled_orders()
    if not unfilled:
        print("  미체결 주문 없음")
    for order in unfilled:
        print(f"  주문번호 {order['order_no']} {order['stock_name']} "
              f"{order['order_side']} {order['order_quantity']}주 "
              f"@ {order['order_price']:,}원 "
              f"(미체결 {order['unfilled_quantity']}주, {order['order_status']})")


def demo_order(order: KiwoomOrder, quote: KiwoomQuote, stock_code: str):
    """모의투자에서 1주 지정가 매수 -> 미체결 확인 -> 취소."""
    print("\n=== 주문 데모 (모의투자) ===")

    info = quote.get_stock_info(stock_code)
    # 체결되지 않도록 하한가 근처로 낮게 건다
    price = max(info['lower_limit_price'], 1)
    print(f"  {info['stock_name']} 1주를 {price:,}원에 매수 주문합니다 "
          f"(현재가 {info['current_price']:,}원, 체결되지 않을 가격)")

    result = order.buy(stock_code, quantity=1, price=price)
    print(f"  결과: {result['status']} - {result.get('message', '')}")

    if result['status'] != "SUCCESS":
        return

    order_no = result['order_no']
    print(f"  주문번호: {order_no}")

    print("\n  같은 주문을 한 번 더 시도합니다 (중복 차단 확인)")
    duplicate = order.buy(stock_code, quantity=1, price=price)
    print(f"  결과: {duplicate['status']} - {duplicate.get('message', '')}")

    print("\n  주문을 취소합니다")
    cancelled = order.cancel(order_no, stock_code, quantity=0)
    print(f"  결과: {cancelled['status']} - {cancelled.get('message', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="키움 REST API 예제")
    parser.add_argument("--stock", default=SAMSUNG, help="조회할 종목코드")
    parser.add_argument("--order", action="store_true",
                        help="주문 데모까지 실행 (모의투자에서만)")
    args = parser.parse_args()

    config = KiwoomConfig()
    try:
        config.validate()
    except ValueError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        print("KIWOOM_APP_KEY / KIWOOM_SECRET_KEY 를 설정하세요.",
              file=sys.stderr)
        return 1

    env = "모의투자" if config.use_mock else "실거래"
    print(f"환경: {env} ({'mockapi' if config.use_mock else 'api'}.kiwoom.com)")

    client, account, quote, order = build(config)

    try:
        show_deposit(account)
        show_balance(account)
        show_stock(quote, args.stock)
        show_orders(account)

        if args.order:
            if not config.use_mock:
                print("\n주문 데모는 모의투자 환경에서만 실행됩니다.",
                      file=sys.stderr)
                return 1
            demo_order(order, quote, args.stock)

    except KiwoomError as exc:
        print(f"\nAPI 오류: {exc}", file=sys.stderr)
        return 1

    print("\n완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
