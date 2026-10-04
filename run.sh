#!/usr/bin/env bash
#
# 키움 MCP 서버 실행 스크립트.
#
#   ./run.sh                    MCP 서버 기동 (stdio)
#   ./run.sh example [옵션...]  CLI 예제 (--order, --stock 000660 ...)
#   ./run.sh test               단위 테스트 (네트워크 불필요)
#   ./run.sh test-mock          모의투자 실호출 통합 테스트 (하나씩, 5초 간격)
#   ./run.sh setup              venv 생성 + 의존성 설치
#   ./run.sh env                읽어들인 설정 확인 (키는 마스킹)
#   ./run.sh check              토큰 발급으로 자격증명/연결 확인 (조회만)
#
# .env 는 python-dotenv 로 읽지 않으므로 이 스크립트가 셸 환경변수로 올립니다.
# MCP 클라이언트의 stdio 를 깨지 않도록 안내 메시지는 모두 stderr 로 보냅니다.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PY="$VENV/bin/python"
PY_BOOTSTRAP="${PYTHON:-python3.11}"   # setup 에서 venv 를 만들 인터프리터

log()  { printf '%s\n' "$*" >&2; }
die()  { log "오류: $*"; exit 1; }

# --------------------------------------------------------------------- setup

do_setup() {
    if [[ ! -x "$PY" ]]; then
        command -v "$PY_BOOTSTRAP" >/dev/null 2>&1 \
            || die "$PY_BOOTSTRAP 을 찾을 수 없습니다. PYTHON=python3.12 ./run.sh setup 처럼 지정하세요."
        log "[setup] $VENV 생성 ($PY_BOOTSTRAP)"
        "$PY_BOOTSTRAP" -m venv "$VENV"
    fi
    log "[setup] 의존성 설치 (죽은 extra-index-url 우회를 위해 pip 설정 무시)"
    PIP_CONFIG_FILE=/dev/null "$PY" -m pip install --upgrade pip >&2
    PIP_CONFIG_FILE=/dev/null "$PY" -m pip install -r requirements.txt >&2
    [[ -f .env ]] || { cp .env.example .env; log "[setup] .env 를 만들었습니다. 앱키/시크릿키를 채우세요."; }
    log "[setup] 완료"
}

# ------------------------------------------------------------------ 사전 점검

require_venv() {
    [[ -x "$PY" ]] || die "$VENV 이 없습니다. ./run.sh setup 을 먼저 실행하세요."
    "$PY" - <<'PYEOF' || die "Python 3.10 이상이 필요합니다 (mcp SDK 요구사항)."
import sys
sys.exit(0 if sys.version_info >= (3, 10) else 1)
PYEOF
}

load_env() {
    if [[ -f "$ROOT/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        . "$ROOT/.env"
        set +a
    else
        log "경고: .env 가 없습니다. 환경변수를 직접 설정했는지 확인하세요."
    fi
    [[ -n "${KIWOOM_APP_KEY:-}" && -n "${KIWOOM_SECRET_KEY:-}" ]] \
        || die "KIWOOM_APP_KEY / KIWOOM_SECRET_KEY 가 비어 있습니다 (.env 확인)."
}

# 실거래면 경고. 차단은 앱(KIWOOM_ALLOW_REAL_TRADING + confirm=true)이 담당합니다.
warn_if_real() {
    [[ "${KIWOOM_USE_MOCK:-false}" == "true" ]] && return 0
    log ""
    log "$(printf '!%.0s' {1..68})"
    log "  경고: 실거래 환경(api.kiwoom.com) 설정입니다."
    log "  실주문 허용 : ${KIWOOM_ALLOW_REAL_TRADING:-false}"
    log "  1회 한도    : ${KIWOOM_MAX_ORDER_AMOUNT:-1000000}원"
    log "  모의투자로 바꾸려면 .env 에 KIWOOM_USE_MOCK=true 를 설정하세요."
    log "$(printf '!%.0s' {1..68})"
    log ""
}

# ----------------------------------------------------------------- 서브커맨드

cmd="${1:-server}"
[[ $# -gt 0 ]] && shift || true

case "$cmd" in
    setup)
        do_setup
        ;;

    server)
        require_venv; load_env; warn_if_real
        exec "$PY" "$ROOT/mcp_server.py" "$@"
        ;;

    example)
        require_venv; load_env; warn_if_real
        exec "$PY" "$ROOT/example.py" "$@"
        ;;

    test)
        require_venv
        exec "$PY" -m pytest tests/ -q "$@"
        ;;

    test-mock)
        require_venv; load_env
        [[ "${KIWOOM_USE_MOCK:-false}" == "true" ]] \
            || die "통합 테스트는 모의투자에서만 실행합니다. KIWOOM_USE_MOCK=true 로 설정하세요."
        log "장중(평일 09:00~15:30 KST)이어야 주문이 접수됩니다."
        # live 픽스처가 function 스코프라 테스트마다 토큰을 재발급합니다.
        # 몰아서 돌리면 키움 서버가 429 를 주므로 간격을 두고 하나씩 실행합니다.
        rc=0
        for t in test_deposit_and_balance test_stock_info \
                 test_buy_then_cancel_roundtrip test_duplicate_order_is_blocked_live; do
            log "--- $t"
            "$PY" -m pytest "tests/test_integration_mock.py::$t" -m mock_integration -q || rc=1
            sleep 5
        done
        exit "$rc"
        ;;

    check)
        require_venv; load_env; warn_if_real
        "$PY" - <<'PYEOF' >&2
import sys
from config import KiwoomConfig
from kiwoom_oauth import KiwoomOAuth

cfg = KiwoomConfig()
cfg.validate()
oauth = KiwoomOAuth(cfg.appkey, cfg.secretkey, use_mock=cfg.use_mock,
                    expiry_buffer_seconds=cfg.token_expiry_buffer,
                    timeout=(cfg.connect_timeout, cfg.read_timeout))
print(f"토큰 발급 시도: {oauth.base_url}/oauth2/token")
data = oauth.authenticate()
if oauth.is_token_valid():
    print(f"성공: 만료 {oauth.token_expires} (token_type={oauth.token_type})")
    sys.exit(0)
# 200 응답인데 토큰이 없으면 대개 앱키/시크릿키 문제입니다.
print("실패: 토큰을 받지 못했습니다. 응답 본문:")
print({k: v for k, v in data.items() if k != 'token'})
print("앱키/시크릿키가 유효한지, 모의투자 키를 모의투자 환경에 쓰고 있는지 확인하세요.")
sys.exit(1)
PYEOF
        ;;

    env)
        require_venv; load_env
        mask() { [[ -n "${1:-}" ]] && printf '%.6s...(%d자)' "$1" "${#1}" || printf '(미설정)'; }
        log "환경        : $([[ ${KIWOOM_USE_MOCK:-false} == true ]] && echo '모의투자 mockapi.kiwoom.com' || echo '실거래 api.kiwoom.com')"
        log "APP_KEY     : $(mask "${KIWOOM_APP_KEY:-}")"
        log "SECRET_KEY  : $(mask "${KIWOOM_SECRET_KEY:-}")"
        log "실주문 허용 : ${KIWOOM_ALLOW_REAL_TRADING:-false}"
        log "1회 한도    : ${KIWOOM_MAX_ORDER_AMOUNT:-1000000}원"
        log "중복 차단   : ${KIWOOM_DEDUP_WINDOW_SECONDS:-60}초"
        log "거래소      : ${KIWOOM_DEFAULT_EXCHANGE:-KRX}"
        log "주문 저널   : ${KIWOOM_ORDER_JOURNAL:-.kiwoom_orders.jsonl}"
        log "python      : $("$PY" -V 2>&1)"
        ;;

    -h|--help|help)
        # 파일 상단 주석 블록을 그대로 사용법으로 출력
        sed -n '2,/^[^#]/p' "${BASH_SOURCE[0]}" | sed -n 's/^#\( \|$\)//p' >&2
        ;;

    *)
        die "알 수 없는 명령: $cmd  (./run.sh --help)"
        ;;
esac
