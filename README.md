# 키움증권 주식 매수/매도 MCP 서버

키움 REST API로 국내주식을 주문·조회하는 MCP 서버입니다. Claude 같은 MCP
클라이언트에서 잔고를 확인하고 매수/매도 주문을 낼 수 있습니다.

중복 주문과 오류 처리를 최우선으로 설계했습니다. 아래 "주문 안전장치" 절을
반드시 읽어보세요.

## 설치

**Python 3.10 이상이 필요합니다.** MCP SDK(`mcp`)가 3.10+ 를 요구합니다. 조회·주문
모듈과 단위 테스트만 쓴다면 3.8 에서도 동작하지만, `mcp_server.py` 는 뜨지 않습니다.

```bash
python3.11 -m venv .venv          # 시스템 python 이 3.8 이면 3.11 을 명시하세요
source .venv/bin/activate
pip install -r requirements.txt
```

설정은 `.env.example` 을 복사해 채웁니다.

```bash
cp .env.example .env
```

`.env` 는 **자동으로 읽히지 않습니다.** 이 프로젝트는 `python-dotenv` 를 쓰지 않고
`os.getenv` 로만 설정을 읽으므로, 셸에서 직접 환경변수로 올려야 합니다.

```bash
set -a; . ./.env; set +a
```

MCP 클라이언트로 띄울 때는 아래 `env` 블록이 이 역할을 대신합니다.

### pip 설치가 멈춘다면

`extra-index-url` 에 죽은 인덱스가 걸려 있으면 pip 이 패키지마다 DNS 재시도를
반복하다 사실상 멎습니다. NVIDIA PyIndex 가 남긴 `pypi.ngc.nvidia.com` 이 대표적인
사례입니다(현재 호스트 없음). 설정 파일을 무시하고 설치하면 우회됩니다.

```bash
PIP_CONFIG_FILE=/dev/null pip install -r requirements.txt
```

`--index-url` 만 줘서는 안 됩니다. 그건 `index-url` 만 덮어쓰고 `extra-index-url` 은
그대로 남습니다. 영구적으로 지우려면:

```bash
pip config --user unset global.extra-index-url
pip config --user unset global.trusted-host
```

## MCP 클라이언트 등록

`command` 는 **venv 의 python 절대경로**를 쓰세요. MCP 클라이언트는 셸 초기화 파일을
읽지 않으므로 `python` 만 적으면 3.8 같은 시스템 인터프리터가 잡혀 `mcp` 를 못 찾습니다.

`claude_desktop_config.json` (또는 사용 중인 MCP 클라이언트 설정):

```json
{
  "mcpServers": {
    "kiwoom": {
      "command": "/home/yjbae/stock/kiwoom_api/.venv/bin/python",
      "args": ["/home/yjbae/stock/kiwoom_api/mcp_server.py"],
      "env": {
        "KIWOOM_APP_KEY": "your-app-key",
        "KIWOOM_SECRET_KEY": "your-secret-key",
        "KIWOOM_USE_MOCK": "true"
      }
    }
  }
}
```

Claude Code에서는:

```bash
claude mcp add kiwoom \
  -e KIWOOM_APP_KEY=... -e KIWOOM_SECRET_KEY=... -e KIWOOM_USE_MOCK=true \
  -- /home/yjbae/stock/kiwoom_api/.venv/bin/python \
     /home/yjbae/stock/kiwoom_api/mcp_server.py
```

기동하면 stderr 로 환경 배너가 찍힙니다. 모의투자면 한 줄, 실거래면 `!` 로 둘러싼
경고 블록이 나옵니다. 주문 전에 `kiwoom_get_trading_status` 로 다시 확인하세요.

## 제공 툴

| 툴 | api-id | 설명 |
|---|---|---|
| `kiwoom_buy_stock` | kt10000 | 매수주문 |
| `kiwoom_sell_stock` | kt10001 | 매도주문 |
| `kiwoom_modify_order` | kt10002 | 정정주문 |
| `kiwoom_cancel_order` | kt10003 | 취소주문 |
| `kiwoom_get_balance` | kt00018 | 계좌평가잔고·보유종목 |
| `kiwoom_get_deposit` | kt00001 | 예수금·주문가능금액 |
| `kiwoom_get_unfilled_orders` | ka10075 | 미체결 |
| `kiwoom_get_filled_orders` | ka10076 | 체결 |
| `kiwoom_get_stock_info` | ka10001 | 현재가·종목정보 |
| `kiwoom_get_trading_status` | — | 현재 환경/안전장치 설정 확인 |

## 주문 안전장치

### 결과는 항상 4가지 상태 중 하나

주문 툴은 예외를 던지지 않고 `status` 를 반환합니다. **재주문해도 되는지**를
호출자가 명확히 알 수 있어야 하기 때문입니다.

| status | 의미 | 재주문 |
|---|---|---|
| `SUCCESS` | 접수 확정. `order_no` 있음 | 불필요 |
| `REJECTED` | 서버가 거부. **미접수 확정** | 원인 수정 후 안전 |
| `BLOCKED` | 안전장치가 전송 전 차단 | 조건 충족 후 안전 |
| `UNKNOWN` | 타임아웃 등으로 **접수 여부 불명** | **절대 금지** |

`UNKNOWN` 이면 미체결·체결 내역을 자동으로 조회해 동일 종목·수량 주문이
실제로 들어갔는지 대조한 결과(`reconciliation`)를 함께 반환합니다.

### 중복 주문 차단 (4단 방어)

키움 주문 API에는 클라이언트 주문 ID나 멱등성 키 필드가 없습니다. 따라서
중복 방지는 전적으로 이 서버가 담당합니다.

1. **전송 전 차단** — 입력 형식 검증, 동일 주문 지문(`매수/매도|종목|수량|단가|유형`)이
   최근 60초 내 존재하면 API를 호출하지 않고 거부. `threading.Lock` 으로 동시 주문 직렬화
2. **재시도 금지** — 주문 API(kt10000~kt10003)는 네트워크 오류·타임아웃·5xx에도
   재전송하지 않습니다. 응답을 못 받은 것과 주문이 안 들어간 것은 다릅니다.
   조회 API는 정상적으로 재시도합니다.
3. **상태 대조** — `UNKNOWN` 발생 시 미체결·체결 조회로 실제 접수 여부를 확인
4. **감사 로그** — 모든 시도를 `.kiwoom_orders.jsonl` 에 기록. 서버를 재시작해도
   중복 감지가 유지됩니다. 계좌번호는 기록하지 않습니다.

의도적인 분할매수처럼 같은 주문을 반복해야 하면 `force=true` 로 호출하세요.

### 실거래 이중 관문

실거래(`api.kiwoom.com`)에서 주문이 나가려면 **둘 다** 필요합니다.

1. 환경변수 `KIWOOM_ALLOW_REAL_TRADING=true`
2. 툴 인자 `confirm=true`

여기에 1회 주문 금액 상한(`KIWOOM_MAX_ORDER_AMOUNT`, 기본 100만원)이 더해집니다.
시장가 주문은 단가가 없으므로 현재가(ka10001)로 환산해 검사하며, **현재가 조회에
실패하면 통과가 아니라 차단**합니다.

모의투자에서는 실거래 관문과 금액 상한이 적용되지 않습니다. 입력 검증과 중복
차단은 모의투자에서도 동작합니다.

## 모듈 구조

```
mcp_server.py        MCP 툴 정의 (FastMCP, stdio)
kiwoom_order.py      주문 실행 + 3상태 분류 + 대조        (kt10000~kt10003)
order_guard.py       안전장치 + 주문 저널
kiwoom_account.py    잔고/예수금/미체결/체결              (kt00018, kt00001, ka10075, ka10076)
kiwoom_quote.py      종목정보/현재가                      (ka10001)
kiwoom_api_client.py 전송 계층 (api-id 헤더, 연속조회, 재시도 정책)
kiwoom_oauth.py      토큰 발급/갱신                       (au10001)
kiwoom_parse.py      키움 숫자 문자열 파싱
config.py            환경변수 설정
example.py           CLI 사용 예제
```

키움 REST API는 모든 요청이 `POST` 이고, 호출할 API는 URL이 아니라 **`api-id`
헤더**로 지정합니다. 연속조회는 `cont-yn` / `next-key` 헤더로 주고받습니다.

## 테스트

```bash
# 단위 테스트 (네트워크 불필요, 항상 실행 가능)
python -m pytest tests/ -q

# 모의투자 실호출 통합 테스트 (모의투자 앱키 필요)
set -a; . ./.env; set +a           # KIWOOM_USE_MOCK=true 인지 확인하세요
python -m pytest tests/ -m mock_integration -v
```

통합 테스트는 체결되지 않도록 하한가로 1주만 주문한 뒤 즉시 취소합니다. 주문이
접수되려면 **장중(평일 09:00~15:30 KST)** 이어야 합니다.

### 통합 테스트의 토큰 발급 제한

통합 테스트를 한 번에 몰아서 돌리면 두 번째 테스트부터 이렇게 실패합니다.

```
OAuth authentication failed: 429 Client Error: null for url:
https://mockapi.kiwoom.com/oauth2/token
```

`live` 픽스처가 function 스코프라 테스트마다 클라이언트를 새로 만들고 토큰을
재발급받는데, 키움 서버가 토큰 발급 호출 빈도를 제한하기 때문입니다. 코드 결함이
아니라 픽스처 스코프 문제입니다. 당장은 테스트를 하나씩 간격을 두고 돌리세요.

```bash
for t in test_deposit_and_balance test_stock_info \
         test_buy_then_cancel_roundtrip test_duplicate_order_is_blocked_live; do
    python -m pytest tests/test_integration_mock.py::$t -m mock_integration -q
    sleep 5
done
```

근본적으로 고치려면 `live` 픽스처를 `scope="session"` 으로 올려 토큰을 재사용하면
됩니다. 단, 중복 차단 저널도 함께 공유되므로 주문 지문이 겹치지 않게 해야 합니다.

### 주문 저널이 남습니다

통합 테스트와 CLI 예제는 실제로 주문을 내므로 `KIWOOM_ORDER_JOURNAL` 경로에 기록이
쌓입니다(기본 `.kiwoom_orders.jsonl`, 현재 디렉터리). 같은 종목·수량·단가로 60초
안에 다시 주문하면 **프로세스를 재시작해도** `BLOCKED`/`DUPLICATE` 로 막힙니다.
저널은 재실행 간 중복을 막는 장치이므로 함부로 지우지 말고, 반복 실행이 필요하면
단가를 한 틱씩 바꾸거나 `KIWOOM_ORDER_JOURNAL` 을 임시 경로로 돌리세요.

## CLI 예제

```bash
set -a; . ./.env; set +a           # 또는 export KIWOOM_APP_KEY=... 등을 직접

python example.py                  # 예수금/잔고/종목정보/미체결 조회
python example.py --order          # 주문 데모까지 (모의투자 전용)
python example.py --stock 000660   # 다른 종목 조회
```

`--order` 는 실제로 주문을 냅니다. 하한가로 1주를 걸어 체결되지 않게 한 뒤,
같은 주문을 한 번 더 시도해 중복 차단을 보여주고, 마지막에 취소합니다.

```
환경: 모의투자 (mockapi.kiwoom.com)
=== 주문 데모 (모의투자) ===
  삼성전자 1주를 188,000원에 매수 주문합니다 (현재가 270,000원, 체결되지 않을 가격)
  결과: SUCCESS - 모의투자 매수주문완료
  주문번호: 0096165

  같은 주문을 한 번 더 시도합니다 (중복 차단 확인)
  결과: BLOCKED - 0초 전에 동일한 주문이 이미 전송되었습니다 (주문번호 0096165, ...)

  주문을 취소합니다
  결과: SUCCESS - 모의투자 취소주문완료
```

## 참고

- [키움 REST API 가이드](https://openapi.kiwoom.com/guide/apiguide)
- [키움증권 공식 GitHub](https://github.com/Kiwoom-Securities/Kiwoom-REST-API)
