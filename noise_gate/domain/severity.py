"""폴스타 심각도 정규화 — 워커·API 공용 단일 출처 (D-184).

설계 전제(Plan 46 §6.1)는 폴스타 템플릿 변수 `${severity}`가 정수(0=해소·1=주의·2=경고·
3=심각)로 렌더링된다는 것이었으나, 폐쇄망 실측(2026-08-25 · Redis `alarm:raw` 적재값)에서
**한글 라벨**(`해제`/`주의`/`경고`/`심각`)로 도착함이 확인됐다. `int("해제")`가 워커에서
ValueError를 내고 `except → ACK`로 폐기되어 UI·통보 어느 쪽에도 도달하지 못했다.

이 모듈은 정수·정수 문자열·한글/영문 라벨을 **결정적**으로 0~3 정수로 정규화한다.
LLM·휴리스틱 없음. 미지 값은 예외(`SeverityParseError`)로 드러내거나(`parse_severity`),
호출자가 원하면 보수적 폴백값과 사유를 함께 돌려준다(`coerce_severity`) — 침묵 드롭 금지.

계층: domain(순수 함수, 표준 라이브러리만).
"""

from __future__ import annotations

from typing import Any, Optional

SEVERITY_CLEAR = 0
SEVERITY_ATTENTION = 1
SEVERITY_WARNING = 2
SEVERITY_CRITICAL = 3

#: 미지 값 폴백 — 비-해소(드롭·오해소 방지) 쪽으로 보수적. E7-c `_E7C_CONSERVATIVE_SEVERITY`와 동일.
CONSERVATIVE_SEVERITY = SEVERITY_WARNING

#: 0~3 정수 → 표시 라벨(기존 코드 관례 `해소` 유지 — 폴스타 원문 `해제`도 0으로 수용한다).
SEVERITY_LABELS: dict[int, str] = {
    SEVERITY_CLEAR: "해소",
    SEVERITY_ATTENTION: "주의",
    SEVERITY_WARNING: "경고",
    SEVERITY_CRITICAL: "심각",
}

#: 라벨 → 정수. 키는 소문자·공백 제거 후 비교한다. 폴스타 조건식 어휘(TROUBLE/ATTENTION/CLEAR)와
#: 국문 UI 어휘(해제/주의/경고/심각)를 함께 수용한다. 범위를 넓히면 오매핑 위험이 커지므로
#: 폴스타에서 실측된 어휘와 그 직역만 둔다.
_LABEL_TO_SEVERITY: dict[str, int] = {
    # 0 — 해소
    "해제": SEVERITY_CLEAR,
    "해소": SEVERITY_CLEAR,
    "정상": SEVERITY_CLEAR,
    "clear": SEVERITY_CLEAR,
    "cleared": SEVERITY_CLEAR,
    "normal": SEVERITY_CLEAR,
    "resolved": SEVERITY_CLEAR,
    # 1 — 주의
    "주의": SEVERITY_ATTENTION,
    "attention": SEVERITY_ATTENTION,
    "minor": SEVERITY_ATTENTION,
    # 2 — 경고
    "경고": SEVERITY_WARNING,
    "warning": SEVERITY_WARNING,
    "warn": SEVERITY_WARNING,
    "major": SEVERITY_WARNING,
    # 3 — 심각
    "심각": SEVERITY_CRITICAL,
    "critical": SEVERITY_CRITICAL,
    "trouble": SEVERITY_CRITICAL,
    "fatal": SEVERITY_CRITICAL,
}

_VALID_RANGE = range(SEVERITY_CLEAR, SEVERITY_CRITICAL + 1)


class SeverityParseError(ValueError):
    """심각도 값을 0~3 정수로 정규화할 수 없을 때."""


def parse_severity(raw: Any) -> int:
    """심각도 원시값을 0~3 정수로 정규화한다.

    수용: 정수(0~3), 정수형 float(2.0), 정수 문자열("2", " 3 "), 라벨(한글/영문, 대소문자·
    공백 무시). bool·None·범위 밖 정수·미지 문자열은 `SeverityParseError`.

    Raises:
        SeverityParseError: 정규화 불가(ValueError 하위 — 기존 `int()` 호출부의 except 절과 호환).
    """
    if raw is None or isinstance(raw, bool):
        raise SeverityParseError(f"severity 값 부재/비정상 타입: {raw!r}")

    if isinstance(raw, int):
        return _check_range(raw, raw)

    if isinstance(raw, float):
        if raw.is_integer():
            return _check_range(int(raw), raw)
        raise SeverityParseError(f"severity 비정수 실수: {raw!r}")

    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise SeverityParseError("severity 빈 문자열")
        key = text.replace(" ", "").lower()
        if key in _LABEL_TO_SEVERITY:
            return _LABEL_TO_SEVERITY[key]
        try:
            return _check_range(int(text), raw)
        except ValueError:
            pass
        try:
            as_float = float(text)
        except ValueError:
            raise SeverityParseError(f"severity 미지 라벨: {raw!r}") from None
        if as_float.is_integer():
            return _check_range(int(as_float), raw)
        raise SeverityParseError(f"severity 비정수 실수 문자열: {raw!r}")

    raise SeverityParseError(f"severity 지원하지 않는 타입 {type(raw).__name__}: {raw!r}")


def coerce_severity(
    raw: Any, *, fallback: int = CONSERVATIVE_SEVERITY
) -> tuple[int, Optional[str]]:
    """예외 없이 정규화한다 — 실패 시 `(fallback, 사유)`, 성공 시 `(값, None)`.

    호출자는 사유가 None이 아니면 반드시 구조화 로그를 남겨 폴백을 가시화해야 한다
    (침묵 폴백 금지 원칙).
    """
    try:
        return parse_severity(raw), None
    except SeverityParseError as exc:
        return fallback, str(exc)


def _check_range(value: int, raw: Any) -> int:
    if value in _VALID_RANGE:
        return value
    raise SeverityParseError(f"severity 범위(0~3) 밖: {raw!r}")
