"""
키움 응답 값 파싱 헬퍼.

키움은 숫자를 문자열로 돌려주며 부호와 선행 0이 붙는 경우가 많습니다.
예: '+70000', '-001500', '000000012345', '' (빈 값)
"""

from typing import Any, Dict, Optional


def to_int(value: Any, default: int = 0) -> int:
    """'+000123' 같은 키움 숫자 문자열을 int로 변환합니다."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    text = str(value).strip().replace(',', '')
    if not text:
        return default

    sign = -1 if text.startswith('-') else 1
    text = text.lstrip('+-')
    if not text:
        return default

    # 소수점이 붙어 오는 경우 정수부만 취한다
    if '.' in text:
        text = text.split('.', 1)[0]

    if not text.isdigit():
        return default
    return sign * int(text)


def to_float(value: Any, default: float = 0.0) -> float:
    """'-3.25' 같은 키움 실수 문자열을 float로 변환합니다."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(',', '')
    if not text:
        return default

    try:
        return float(text)
    except ValueError:
        return default


def pick(source: Dict[str, Any], mapping: Dict[str, str],
         numeric: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    키움 축약 필드를 읽기 쉬운 이름으로 옮깁니다.

    Args:
        source: 키움 응답 dict
        mapping: {키움필드: 결과필드} - 문자열 그대로 옮김
        numeric: {키움필드: 결과필드} - 'int' 또는 'float'로 변환.
            값은 '결과필드:타입' 형식이 아니라 결과필드명이며,
            타입은 to_int를 기본으로 하고 필드명이 '_rate'로 끝나면 float를 씁니다.
    """
    result: Dict[str, Any] = {}
    for src, dst in mapping.items():
        result[dst] = (source.get(src) or '').strip() if isinstance(
            source.get(src), str
        ) else source.get(src)

    for src, dst in (numeric or {}).items():
        if dst.endswith('_rate'):
            result[dst] = to_float(source.get(src))
        else:
            result[dst] = to_int(source.get(src))

    return result
