"""
주문 안전장치.

키움 주문 API에는 클라이언트 주문 ID나 멱등성 키 필드가 없습니다
(바디는 dmst_stex_tp / stk_cd / ord_qty / ord_uv / trde_tp / cond_uv 뿐).
따라서 중복 주문은 서버가 아니라 이 계층에서 막아야 합니다.

방어 순서:
  1. 입력 검증        - 형식이 틀린 주문은 API를 호출하지도 않음
  2. 중복 감지        - 최근 N초 내 동일 주문이면 거부 (force로 우회 가능)
  3. 실거래 관문      - 환경변수 + 툴 인자 confirm 이중 확인
  4. 금액 상한       - 1회 주문 금액 제한. 시장가는 현재가로 환산.
"""

import json
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, List, Optional

from kiwoom_errors import KiwoomOrderBlocked

STOCK_CODE_RE = re.compile(r'^\d{6}(_NX|_AL)?$')

VALID_EXCHANGES = ('KRX', 'NXT', 'SOR')

# 스펙(kt10000 trde_tp) 기준 유효한 매매구분
VALID_ORDER_TYPES = (
    '0', '3', '5', '6', '7', '10', '13', '16', '20', '23', '26',
    '28', '29', '30', '31', '61', '62', '81',
)

# 주문단가가 반드시 있어야 하는 지정가 계열
LIMIT_ORDER_TYPES = ('0', '5', '10', '20', '28', '62')

# 실제로 전송된 주문(중복 판정 대상). REJECTED는 미접수 확정이므로 제외한다.
SENT_STATUSES = ('SUCCESS', 'UNKNOWN')


@dataclass(frozen=True)
class OrderIntent:
    """주문 의도. 중복 판정의 단위."""

    action: str          # buy | sell | modify | cancel
    stock_code: str
    quantity: int
    price: int
    order_type: str
    exchange: str
    original_order_no: str = ''

    def fingerprint(self) -> str:
        return '|'.join([
            self.action, self.stock_code, str(self.quantity),
            str(self.price), self.order_type, self.exchange,
            self.original_order_no,
        ])


class OrderJournal:
    """
    주문 시도 기록 (append-only JSONL).

    서버를 재시작해도 중복 감지가 유지되도록 파일에 남깁니다.
    계좌번호나 인증 정보는 기록하지 않습니다.
    """

    #: 중복 조회 시 파일 끝에서 읽어올 최대 줄 수
    TAIL_LINES = 500

    def __init__(self, path: str):
        self.path = path

    def append(self, record: Dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    def recent(self, window_seconds: float,
               now: Optional[float] = None) -> List[Dict[str, Any]]:
        """window_seconds 이내에 기록된 항목을 최신순으로 반환합니다."""
        now = time.time() if now is None else now
        cutoff = now - window_seconds

        records = []
        for line in self._tail_lines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if float(record.get('ts', 0)) >= cutoff:
                records.append(record)

        records.sort(key=lambda r: r.get('ts', 0), reverse=True)
        return records

    def _tail_lines(self) -> List[str]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except OSError:
            return []
        return [line.strip() for line in lines[-self.TAIL_LINES:] if line.strip()]


class OrderGuard:
    """주문 전송 전 검사."""

    def __init__(self, config, journal: OrderJournal,
                 price_lookup: Optional[Callable[[str], Optional[int]]] = None,
                 clock: Callable[[], float] = time.time):
        """
        Args:
            config: KiwoomConfig
            journal: 중복 감지에 사용할 주문 기록
            price_lookup: 종목코드 -> 현재가(원). 시장가 주문의 금액 산정에 사용.
            clock: 현재 시각 함수 (테스트 주입용)
        """
        self.config = config
        self.journal = journal
        self.price_lookup = price_lookup
        self.clock = clock

    # ------------------------------------------------------------------ #

    def check(self, intent: OrderIntent, confirm: bool = False,
              force: bool = False) -> None:
        """
        주문을 전송해도 되는지 검사합니다.

        Raises:
            KiwoomOrderBlocked: 하나라도 걸리면 API를 호출하지 않고 차단
        """
        self._check_input(intent)
        if not force:
            self._check_duplicate(intent)

        # 모의투자에서는 실거래 관문과 금액 상한을 적용하지 않는다.
        if self.config.use_mock:
            return

        self._check_real_trading_allowed()
        self._check_confirmed(confirm)
        self._check_amount(intent)

    # ------------------------------------------------------------------ #

    def _check_input(self, intent: OrderIntent) -> None:
        if not STOCK_CODE_RE.match(intent.stock_code):
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.INVALID_INPUT,
                f"종목코드 형식이 올바르지 않습니다: {intent.stock_code!r} "
                f"(6자리 숫자, 필요 시 _NX/_AL 접미사)",
            )

        if intent.exchange not in VALID_EXCHANGES:
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.INVALID_INPUT,
                f"거래소 구분은 {VALID_EXCHANGES} 중 하나여야 합니다: "
                f"{intent.exchange!r}",
            )

        if intent.action == 'cancel':
            # 취소는 수량 0이 '잔량 전부'를 의미하므로 0을 허용한다.
            if intent.quantity < 0:
                raise KiwoomOrderBlocked(
                    KiwoomOrderBlocked.INVALID_INPUT,
                    f"취소수량은 0 이상이어야 합니다 (0 = 잔량 전부): "
                    f"{intent.quantity}",
                )
        elif intent.quantity < 1:
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.INVALID_INPUT,
                f"주문수량은 1주 이상이어야 합니다: {intent.quantity}",
            )

        if intent.action in ('buy', 'sell'):
            if intent.order_type not in VALID_ORDER_TYPES:
                raise KiwoomOrderBlocked(
                    KiwoomOrderBlocked.INVALID_INPUT,
                    f"매매구분이 올바르지 않습니다: {intent.order_type!r} "
                    f"(유효값: {', '.join(VALID_ORDER_TYPES)})",
                )
            if intent.order_type in LIMIT_ORDER_TYPES and intent.price < 1:
                raise KiwoomOrderBlocked(
                    KiwoomOrderBlocked.INVALID_INPUT,
                    f"지정가 주문(매매구분 {intent.order_type})에는 "
                    f"1원 이상의 주문단가가 필요합니다: {intent.price}",
                )

        if intent.action in ('modify', 'cancel') and not intent.original_order_no:
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.INVALID_INPUT,
                "정정/취소 주문에는 원주문번호가 필요합니다",
            )

        if intent.action == 'modify' and intent.price < 1:
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.INVALID_INPUT,
                f"정정 주문에는 1원 이상의 정정단가가 필요합니다: {intent.price}",
            )

    def _check_duplicate(self, intent: OrderIntent) -> None:
        window = self.config.dedup_window_seconds
        if window <= 0:
            return

        fingerprint = intent.fingerprint()
        now = self.clock()

        for record in self.journal.recent(window, now=now):
            if record.get('status') not in SENT_STATUSES:
                continue
            if record.get('fingerprint') != fingerprint:
                continue

            elapsed = now - float(record.get('ts', now))
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.DUPLICATE,
                f"{elapsed:.0f}초 전에 동일한 주문이 이미 전송되었습니다 "
                f"(주문번호 {record.get('order_no') or '확인필요'}, "
                f"상태 {record.get('status')}). "
                f"중복 체결을 막기 위해 {window}초간 차단합니다. "
                f"의도한 반복 주문이라면 force=true로 다시 호출하세요.",
            )

    def _check_real_trading_allowed(self) -> None:
        if not self.config.allow_real_trading:
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.REAL_TRADING_DISABLED,
                "실거래 주문이 비활성화되어 있습니다. "
                "환경변수 KIWOOM_ALLOW_REAL_TRADING=true 로 설정해야 "
                "실제 주문을 낼 수 있습니다.",
            )

    def _check_confirmed(self, confirm: bool) -> None:
        if not confirm:
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.NOT_CONFIRMED,
                "실거래 주문에는 사용자의 명시적 확인이 필요합니다. "
                "사용자에게 종목/수량/단가를 확인받은 뒤 confirm=true로 "
                "다시 호출하세요.",
            )

    def _check_amount(self, intent: OrderIntent) -> None:
        if intent.action == 'cancel':
            return

        unit_price = intent.price
        if unit_price < 1:
            unit_price = self._lookup_price(intent.stock_code)

        amount = unit_price * intent.quantity
        if amount > self.config.max_order_amount:
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.AMOUNT_LIMIT,
                f"주문금액 {amount:,}원이 1회 한도 "
                f"{self.config.max_order_amount:,}원을 초과합니다. "
                f"수량을 줄이거나 KIWOOM_MAX_ORDER_AMOUNT를 조정하세요.",
            )

    def _lookup_price(self, stock_code: str) -> int:
        """시장가 주문의 금액 산정을 위해 현재가를 조회합니다."""
        if self.price_lookup is None:
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.PRICE_UNKNOWN,
                "단가 없는 주문의 금액을 확인할 수 없어 차단했습니다 "
                "(현재가 조회 수단이 없음).",
            )

        try:
            price = self.price_lookup(stock_code)
        except Exception as exc:  # 조회 실패 시 통과시키지 않는다
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.PRICE_UNKNOWN,
                f"현재가 조회에 실패해 주문금액을 검증할 수 없습니다: {exc}. "
                f"안전을 위해 주문을 차단합니다.",
            ) from exc

        if not price or price < 1:
            raise KiwoomOrderBlocked(
                KiwoomOrderBlocked.PRICE_UNKNOWN,
                f"{stock_code}의 현재가를 확인할 수 없어 주문금액을 "
                f"검증할 수 없습니다. 안전을 위해 주문을 차단합니다.",
            )

        return int(price)

    # ------------------------------------------------------------------ #

    def record(self, intent: OrderIntent, status: str,
               order_no: str = '', error: Optional[str] = None) -> None:
        """주문 시도 결과를 기록합니다."""
        record = {
            'ts': self.clock(),
            'env': 'mock' if self.config.use_mock else 'real',
            'fingerprint': intent.fingerprint(),
            'status': status,
            'order_no': order_no,
            'error': error,
        }
        record.update(asdict(intent))
        self.journal.append(record)
