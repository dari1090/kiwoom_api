"""
Kiwoom API 예외 정의.

주문 처리에서 가장 중요한 구분은 "주문이 접수되지 않은 것이 확실한가"입니다.
- KiwoomAPIError      : 서버가 요청을 받고 거부함 -> 미접수 확정. 재주문 안전.
- KiwoomOrderUnknown  : 응답을 받지 못함 -> 접수 여부 불명. 재주문 금지.
- KiwoomOrderBlocked  : 클라이언트가 전송 전에 차단함 -> 미접수 확정.
"""


class KiwoomError(Exception):
    """모든 키움 관련 예외의 최상위 클래스."""


class KiwoomAPIError(KiwoomError):
    """API가 return_code != 0 으로 응답한 경우 (요청은 도달, 처리는 거부)."""

    def __init__(self, return_code, return_msg: str, api_id: str):
        self.return_code = return_code
        self.return_msg = return_msg
        self.api_id = api_id
        super().__init__(f"[{api_id}] ({return_code}) {return_msg}")


class KiwoomHTTPError(KiwoomError):
    """4xx 등 HTTP 레벨 오류. 주문이 접수되지 않았음이 확실한 경우에만 사용."""

    def __init__(self, status_code: int, api_id: str, detail: str = ""):
        self.status_code = status_code
        self.api_id = api_id
        super().__init__(f"[{api_id}] HTTP {status_code} {detail}".strip())


class KiwoomOrderUnknownState(KiwoomError):
    """
    주문 요청 후 응답을 확인하지 못한 상태.

    타임아웃, 연결 끊김, 5xx 응답이 여기에 해당합니다. 서버에는 주문이
    접수됐을 수 있으므로 절대 재전송하면 안 됩니다.
    """

    def __init__(self, api_id: str, cause: str):
        self.api_id = api_id
        self.cause = cause
        super().__init__(
            f"[{api_id}] 주문 응답을 확인하지 못했습니다 (접수 여부 불명): {cause}"
        )


class KiwoomOrderBlocked(KiwoomError):
    """
    안전장치가 주문을 전송 전에 차단함.

    reason 코드로 차단 사유를 구분합니다.
    """

    INVALID_INPUT = "INVALID_INPUT"
    DUPLICATE = "DUPLICATE"
    REAL_TRADING_DISABLED = "REAL_TRADING_DISABLED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    AMOUNT_LIMIT = "AMOUNT_LIMIT"
    PRICE_UNKNOWN = "PRICE_UNKNOWN"

    def __init__(self, reason: str, message: str):
        self.reason = reason
        super().__init__(message)
