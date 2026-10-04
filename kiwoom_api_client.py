"""
키움 REST API 전송 계층.

키움 REST API는 모든 요청이 POST이고, 어떤 API를 호출할지는 URL이 아니라
`api-id` 헤더로 지정합니다. 연속조회는 요청/응답 헤더의 cont-yn / next-key로
주고받습니다.

주문 API(kt10000~kt10003)는 재시도하지 않습니다. 응답을 받지 못했다는 것이
주문이 접수되지 않았다는 뜻은 아니기 때문에, 재전송하면 중복 체결이 발생할
수 있습니다. 대신 KiwoomOrderUnknownState를 던져 호출자가 대조하도록 합니다.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

from kiwoom_errors import (
    KiwoomAPIError,
    KiwoomHTTPError,
    KiwoomOrderUnknownState,
)
from kiwoom_oauth import KiwoomOAuth

# 주문 계열 API. 절대 재시도하지 않는다.
ORDER_API_IDS = frozenset({"kt10000", "kt10001", "kt10002", "kt10003"})

PATH_ORDER = "/api/dostk/ordr"
PATH_ACCOUNT = "/api/dostk/acnt"
PATH_STOCK_INFO = "/api/dostk/stkinfo"


@dataclass
class KiwoomResponse:
    """키움 API 단일 응답."""

    api_id: str
    body: Dict[str, Any] = field(default_factory=dict)
    cont_yn: str = "N"
    next_key: str = ""

    @property
    def return_code(self) -> int:
        code = self.body.get("return_code", 0)
        try:
            return int(code)
        except (TypeError, ValueError):
            return -1

    @property
    def return_msg(self) -> str:
        return self.body.get("return_msg", "")

    @property
    def has_more(self) -> bool:
        return self.cont_yn == "Y" and bool(self.next_key)


class KiwoomAPIClient:
    """api-id 기반 키움 REST API 클라이언트."""

    def __init__(self, appkey: str, secretkey: str, use_mock: bool = False,
                 max_retries: int = 3, retry_delay: float = 1.0,
                 connect_timeout: float = 5.0, read_timeout: float = 30.0,
                 token_expiry_buffer: int = 60):
        """
        Args:
            appkey: 앱키
            secretkey: 시크릿키
            use_mock: 모의투자 환경 사용 여부
            max_retries: 조회 API의 최대 시도 횟수 (주문 API에는 적용되지 않음)
            retry_delay: 재시도 간 지연 (초). 시도마다 선형 증가.
            connect_timeout: 연결 타임아웃 (초)
            read_timeout: 응답 대기 타임아웃 (초)
            token_expiry_buffer: 토큰 만료 몇 초 전부터 미리 갱신할지
        """
        self.oauth = KiwoomOAuth(
            appkey, secretkey, use_mock,
            expiry_buffer_seconds=token_expiry_buffer,
            timeout=(connect_timeout, read_timeout),
        )
        self.use_mock = use_mock
        self.base_url = self.oauth.base_url
        self.max_retries = max(1, max_retries)
        self.retry_delay = retry_delay
        self.timeout: Tuple[float, float] = (connect_timeout, read_timeout)

    @classmethod
    def from_config(cls, config) -> "KiwoomAPIClient":
        """KiwoomConfig로부터 클라이언트를 생성합니다."""
        config.validate()
        return cls(
            appkey=config.appkey,
            secretkey=config.secretkey,
            use_mock=config.use_mock,
            max_retries=config.max_retries,
            retry_delay=config.retry_delay,
            connect_timeout=config.connect_timeout,
            read_timeout=config.read_timeout,
            token_expiry_buffer=config.token_expiry_buffer,
        )

    # ------------------------------------------------------------------ #
    # 단일 호출
    # ------------------------------------------------------------------ #

    def call(self, api_id: str, path: str,
             body: Optional[Dict[str, Any]] = None,
             cont_yn: Optional[str] = None,
             next_key: Optional[str] = None) -> KiwoomResponse:
        """
        키움 API를 1회 호출합니다.

        Args:
            api_id: TR 코드 (예: 'kt10000')
            path: URL 경로 (예: '/api/dostk/ordr')
            body: 요청 바디
            cont_yn: 연속조회 여부 ('Y'/'N')
            next_key: 연속조회 키

        Returns:
            KiwoomResponse

        Raises:
            KiwoomAPIError: return_code != 0 (요청 도달, 서버가 거부)
            KiwoomHTTPError: 4xx 응답 (요청 미처리 확정)
            KiwoomOrderUnknownState: 주문 API에서 응답 확인 실패 (접수 여부 불명)
            requests.RequestException: 조회 API에서 재시도 소진
        """
        is_order = api_id in ORDER_API_IDS
        attempts = 1 if is_order else self.max_retries

        # 주문 도중 토큰 만료로 실패하지 않도록 호출 전에 먼저 확보한다.
        self.oauth.refresh_token_if_needed()

        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "api-id": api_id,
        }
        headers.update(self.oauth.get_auth_header())
        if cont_yn:
            headers["cont-yn"] = cont_yn
        if next_key:
            headers["next-key"] = next_key

        last_exception: Optional[Exception] = None

        for attempt in range(attempts):
            try:
                response = requests.post(
                    url, json=body or {}, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                # 응답을 받지 못함. 주문이면 접수 여부를 알 수 없다.
                if is_order:
                    raise KiwoomOrderUnknownState(api_id, repr(exc)) from exc
                last_exception = exc
                if attempt < attempts - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise

            status = response.status_code

            if status >= 500:
                # 서버 오류. 주문은 접수됐을 수도 있다.
                if is_order:
                    raise KiwoomOrderUnknownState(
                        api_id, f"HTTP {status}: {response.text[:200]}"
                    )
                last_exception = requests.HTTPError(
                    f"HTTP {status}", response=response
                )
                if attempt < attempts - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise last_exception

            if status >= 400:
                # 4xx는 요청 자체가 거부된 것이므로 접수되지 않았음이 확실하다.
                raise KiwoomHTTPError(status, api_id, response.text[:300])

            return self._build_response(api_id, response)

        # 이론상 도달하지 않음
        raise last_exception or RuntimeError(f"[{api_id}] 호출 실패")

    def _build_response(self, api_id: str, response) -> KiwoomResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise KiwoomHTTPError(
                response.status_code, api_id, f"JSON 파싱 실패: {response.text[:200]}"
            ) from exc

        result = KiwoomResponse(
            api_id=api_id,
            body=payload if isinstance(payload, dict) else {"data": payload},
            cont_yn=response.headers.get("cont-yn", "N"),
            next_key=response.headers.get("next-key", ""),
        )

        if result.return_code != 0:
            raise KiwoomAPIError(result.return_code, result.return_msg, api_id)

        return result

    # ------------------------------------------------------------------ #
    # 연속조회
    # ------------------------------------------------------------------ #

    def call_paged(self, api_id: str, path: str,
                   body: Optional[Dict[str, Any]] = None,
                   max_pages: int = 10,
                   page_delay: float = 0.2) -> List[KiwoomResponse]:
        """
        연속조회(cont-yn/next-key)를 따라가며 전체 페이지를 가져옵니다.

        주문 API에는 사용하지 마세요. 조회 전용입니다.

        Args:
            max_pages: 최대 페이지 수. 초과분은 조용히 버리지 않고 로그 대신
                호출자가 페이지 수로 판단할 수 있게 그대로 반환합니다.
            page_delay: 페이지 간 지연 (초). 키움 유량 제한 대응.
        """
        if api_id in ORDER_API_IDS:
            raise ValueError(f"주문 API({api_id})는 연속조회 대상이 아닙니다")

        pages: List[KiwoomResponse] = []
        cont_yn: Optional[str] = None
        next_key: Optional[str] = None

        for page in range(max_pages):
            result = self.call(api_id, path, body, cont_yn=cont_yn, next_key=next_key)
            pages.append(result)

            if not result.has_more:
                break
            if page + 1 >= max_pages:
                break

            cont_yn = result.cont_yn
            next_key = result.next_key
            time.sleep(page_delay)

        return pages
