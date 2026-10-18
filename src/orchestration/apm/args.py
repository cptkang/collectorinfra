"""보기 표 렌더 조각(창·조건·대상 문구)과 선택 조건(`view_args`) 형식 검증.

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from src.routing.registry import ViewArgSpec, ViewSpec

#: 문자열 조건 값 형식(SPEC §6.3 — 원천 허용값 미공개라 임의 enum 으로 줄이지 않는다). 첫 글자는
#: 게이트웨이(`_IDENT` — 영문 시작)와 같다(W2 검증 B4 — 본체가 통과시킨 값이 게이트웨이에서 보기
#: 전체를 실패시키지 않게). 길이 64는 게이트웨이 상한(정렬 기준·지표 128 · 오류 유형 64) 안쪽이다.
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
#: 계정 ID 형식(plans/134 W7 — 게이트웨이 경로 변수 `account`와 같은 형식 · SPEC §2.4).
_ACCOUNT = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
#: 대문자 토큰(plans/134 W7 — 게이트웨이 경로 변수 `token`과 같은 형식 · 오류 유형 이름).
_TOKEN = re.compile(r"^[A-Z0-9_]{1,64}$")
#: `opaque`(형식 미공개 값 — GUID 등) 길이 상한 — 게이트웨이 검증(1~256자)과 같다(plans/134 W5).
_OPAQUE_MAX = 256
#: `text` 조건에서 거부하는 문자 범주 — 제어(Cc: C0·DEL·C1) · 서식 제어(Cf: 방향 제어·폭 없는
#: 문자 등) · 줄·문단 구분(Zl U+2028 · Zp U+2029)(W2 검증 B5 · SPEC §6.1 「제어 문자 없음」).
_TEXT_REJECT_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


_WINDOW_TEXT = {"range": "기간 지정 가능", "current": "현재값", "hourly": "시 단위 통계",
                "none": "", "compare": "두 기간 비교 — 기간은 질문에서"}


def _arg_text(arg: ViewArgSpec) -> str:
    form = {
        "int": "정수" + (f" {arg.min} 이상" if arg.min is not None else ""),
        "bool": "true/false",
        "enum": "|".join(arg.choices),
        "str": "이름",
        "str_list": "이름 목록",
        "catalog": f"{arg.catalog or ''} 지표 이름".strip(),
        "text": "문자열",
        "opaque": "문자열(공백 없이 그대로)",
        "account": "계정 ID",
        "token": "대문자 이름",
    }.get(arg.type, arg.type)
    if arg.type == "str_list" and arg.catalog:
        form = f"이름 목록({arg.catalog} 지표)"
    text = f"`{arg.name}` = {form}" + ("(필수)" if arg.required else "")
    return f"{text} — {arg.label}" if arg.label else text


#: 이름 보기(`target: named`)의 대상 이름 종류(도구 인자 → 보기 표 문구).
_NAMED_NOUN = {"service": "서비스", "business": "업무"}


def _target_text(view: ViewSpec) -> str:
    """보기 표의 대상 설명 — 종전 보기(대상 표현 없음)는 종전 문구 그대로다."""
    if view.required_input:
        return "대상 서버 필요"
    if view.target == "optional":
        return "서버를 말하면 그 서버 · 없으면 전체"
    if view.target == "reference":
        return "앞 결과의 행을 가리킴(ref)"
    if view.target == "named":
        noun = _NAMED_NOUN.get(view.target_arg, view.target_arg)
        return f"{noun} 이름을 말하면(targets) 그 {noun} · 없으면 전체"
    return "대상 없이 전체 목록"


# ── 선택 조건(view_args — plans/134 M-3 · SPEC §6.3) ─────────────────────────

def _coerce_int(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """`int` 조건 — ASCII 숫자 문자열·정수형 float을 정수로, `min` 미만·bool은 거부."""
    if isinstance(value, bool):
        return False, None
    text = value.strip() if isinstance(value, str) else ""
    if text and text.isascii() and text.isdigit():  # 「²」·전각 숫자는 거부(W1 검증 L-2)
        value = int(text)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if not isinstance(value, int) or (spec.min is not None and value < spec.min):
        return False, None
    return True, value


def _coerce_bool(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """`bool` 조건 — bool 그대로 · 문자열은 true/false(대소문자 무시)만."""
    if isinstance(value, bool):
        return True, value
    text = value.strip().lower() if isinstance(value, str) else ""
    return (True, text == "true") if text in ("true", "false") else (False, None)


def _coerce_enum(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """`enum` 조건 — 대소문자 무시로 선택지에 맞추고 선언 표기로 돌려준다."""
    text = value.strip().lower() if isinstance(value, str) else ""
    lookup = {c.lower(): c for c in spec.choices}
    return (True, lookup[text]) if text in lookup else (False, None)


def _coerce_ident(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """`str`·`catalog` 조건 — 식별자 형식(`_IDENT`)만."""
    text = value.strip() if isinstance(value, str) else ""
    return (True, text) if _IDENT.match(text) else (False, None)


def _coerce_text(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """`text` 조건 — 200자 이하 · 제어·서식 문자 거부."""
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > 200 or any(
            unicodedata.category(c) in _TEXT_REJECT_CATEGORIES for c in text):
        return False, None
    return True, text


def _coerce_str_list(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """`str_list` 조건 — 스칼라는 1개 목록으로 · 모든 항목이 식별자여야 한다."""
    items = value if isinstance(value, list) else [value]
    clean = [v.strip() for v in items if isinstance(v, str)]
    if not clean or len(clean) != len(items) or not all(_IDENT.match(c) for c in clean):
        return False, None
    return True, clean


def _coerce_opaque(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """`opaque` 조건 — `_OPAQUE_MAX`자 이하 · 공백·제어·서식 문자 거부."""
    text = value.strip() if isinstance(value, str) else ""
    if not text or len(text) > _OPAQUE_MAX or any(
            c.isspace() or unicodedata.category(c) in _TEXT_REJECT_CATEGORIES for c in text):
        return False, None
    return True, text


def _coerce_account(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """`account` 조건 — 계정 형식(`_ACCOUNT`) · `..` 거부."""
    text = value.strip() if isinstance(value, str) else ""
    return (True, text) if _ACCOUNT.match(text) and ".." not in text else (False, None)


def _coerce_token(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """`token` 조건 — 대소문자만 맞춘다(뜻을 바꾸지 않는다) — 게이트웨이 경로 변수 형식."""
    text = value.strip().upper() if isinstance(value, str) else ""
    return (True, text) if _TOKEN.match(text) else (False, None)


#: 조건 형식(`ViewArgSpec.type`) → 검증·정규화 함수. `str`·`catalog`는 같은 함수를 쓴다.
_COERCERS = {
    "int": _coerce_int,
    "bool": _coerce_bool,
    "enum": _coerce_enum,
    "str": _coerce_ident,
    "catalog": _coerce_ident,
    "text": _coerce_text,
    "str_list": _coerce_str_list,
    "opaque": _coerce_opaque,
    "account": _coerce_account,
    "token": _coerce_token,
}


def _coerce_arg(spec: ViewArgSpec, value: Any) -> tuple[bool, Any]:
    """조건 값 1개를 선언 형식으로 검증·정규화한다 — (통과 여부, 값). 모르는 형식은 거부."""
    coerce = _COERCERS.get(spec.type) if isinstance(spec.type, str) else None
    return coerce(spec, value) if coerce is not None else (False, None)


def validate_view_args(view: ViewSpec, raw: Any) -> tuple[dict[str, Any], list[str]]:
    """분해 `view_args[보기]` → (도구 인자, 버린 조건 설명).

    코드가 `ViewArgSpec`으로 형·범위·선택지를 검증한다. 모르는 이름·값은 버리고 그 사실을 돌려준다 —
    호출부가 `apm_unresolved_condition`으로 알린다(다른 조회로 성공 처리하지 않는다 · G-10).
    값이 `null`인 조건은 「미지정」이라 버린 조건으로 세지 않는다.
    """
    if raw is None or raw == {}:
        return {}, []
    if not isinstance(raw, dict):
        return {}, [f"조건 형식이 아님({type(raw).__name__})"]
    specs = {a.name: a for a in view.args}
    out: dict[str, Any] = {}
    rejected: list[str] = []
    for name, value in raw.items():
        if value is None:  # 미지정 — 버린 조건이 아니다(무고지 · W1 검증 H-2)
            continue
        spec = specs.get(str(name))
        if spec is None:
            rejected.append(f"{str(name)[:40]}(이 보기에 없는 조건)")
            continue
        ok, clean = _coerce_arg(spec, value)
        if not ok:
            rejected.append(f"{spec.name}={str(value)[:40]!r}")
            continue
        out[spec.tool_arg or spec.name] = clean
    return out, rejected


def _missing_required(view: ViewSpec, args: dict[str, Any], raw: Any) -> list[str]:
    """검증 뒤에도 없는 필수 조건 — `이름(없음)` 또는 `이름='값'(형식 밖)`."""
    given = raw if isinstance(raw, dict) else {}
    out: list[str] = []
    for spec in view.args:
        if not spec.required or (spec.tool_arg or spec.name) in args:
            continue
        value = given.get(spec.name)
        out.append(f"{spec.name}(없음)" if value is None
                   else f"{spec.name}={str(value)[:40]!r}(형식 밖)")
    return out
