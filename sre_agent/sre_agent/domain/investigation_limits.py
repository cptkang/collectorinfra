"""조사 한계 판정 — APM 사건 판정 · APM 증거 한계 · 정체 가드 (plans/87 J3 · SPEC-apm-sre-agent §3.3·§3.6 · D-035).

**순수 도메인 모듈**(stdlib만). 입력은 트리거 페이로드·게이트웨이 반환 dict(`severity_signatures.apm_payloads`)와
`(도구명, 인자 표기)` 쌍이다 — LLM 서술은 입력이 아니다. 결과는 브리핑 `[한계]`에 싣는 문자열이다.
배선은 `apm_guidance_enabled`일 때만 dispatcher가 한다(끄면 브리핑 비트 동일). APM 사건 판정(R-16 기준)은
지침(`investigation_guidance`)과 dispatcher가 함께 쓰므로 여기 한 곳에 둔다(application 간 직접 의존 회피).

정체 가드(P15)는 **사후 판정**이다 — holmes ReAct 루프를 중간에 끊는 공개 훅이 없어 조사가 끝난 뒤
도구 호출 목록으로 판정한다. 반복 호출이 소진한 step은 되돌리지 못한다(상한은 `max_steps`·전체 타임아웃).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

#: 게이트웨이 이벤트의 resourceType(소문자 비교 — SPEC-apm-gateway §5). R-16 kind 선판정의 단일 기준.
APM_RESOURCE_TYPE = "apm.instance"


def is_apm_resource_type(resource_type: object) -> bool:
    """`resourceType == "apm.Instance"`(대소문자·앞뒤 공백 무시)."""
    return str(resource_type or "").strip().lower() == APM_RESOURCE_TYPE


def is_apm_trigger(payload: object) -> bool:
    """WAS(APM) 사건 트리거 — `event.resourceType`이 apm.Instance 또는 `meta.hints.solution == "apm"`."""
    if not isinstance(payload, dict):
        return False
    event = payload.get("event")
    if isinstance(event, dict) and is_apm_resource_type(event.get("resourceType")):
        return True
    meta = payload.get("meta")
    hints = meta.get("hints") if isinstance(meta, dict) else None
    return isinstance(hints, dict) and hints.get("solution") == "apm"


#: 게이트웨이 오류 코드 중 "APM 미가용 → 폴스타 MCP 도구로 대체"로 다루는 것(SPEC-apm-gateway §3.2).
APM_UNAVAILABLE_CODES: tuple[str, ...] = ("source_unavailable", "instance_unresolved", "not_configured")

#: 정합 신뢰도 중 한계로 적는 값("medium 이하" — 게이트웨이 어휘 high·medium·none + 방어적 low).
_WEAK_CONFIDENCE: frozenset[str] = frozenset({"medium", "low", "none"})

#: 정체 가드 임계 — 같은 도구·같은 인자 호출 횟수.
STALL_REPEAT_THRESHOLD = 3

APM_NOT_CONFIGURED_LIMIT = "APM 미가용 — APM_MCP_URL 미설정: 게이트웨이 도구 없이 폴스타 MCP 도구로 대체"
APM_NOT_CALLED_LIMIT = (
    "APM 증거 없음 — WAS(APM) 사건인데 apm_* 도구 호출 0건(게이트웨이 미가용으로 도구가 등록되지 않았을 수 있음): "
    "폴스타 MCP 도구 증거로만 판단"
)


def _strip_limit_prefix(text: str) -> str:
    text = text.strip()
    return text[len("[한계]"):].strip() if text.startswith("[한계]") else text


def apm_limitations(
    payloads: Sequence[dict],
    *,
    apm_configured: bool,
    apm_incident: bool = False,
    apm_called: bool = False,
) -> list[str]:
    """게이트웨이 반환에서 브리핑 한계 문구를 뽑는다(순서 보존 · 중복 제거).

    ① 게이트웨이 미설정 ①′ 설정됐는데 apm 사건에서 apm_* 호출 0건(헬스체크 실패로 도구 미등록 등 —
    오류 반환조차 없어 ②로는 잡히지 않는다) ② 미가용 오류(`APM_UNAVAILABLE_CODES`) → 폴스타 MCP 도구 대체 사유
    ③ 인스턴스 정합 신뢰도 medium 이하 ④ 도구 `limits`(1분 창 상한 도달 등 — 게이트웨이 문구 그대로).
    """
    out: list[str] = []
    if not apm_configured:
        out.append(APM_NOT_CONFIGURED_LIMIT)
    elif apm_incident and not apm_called:
        out.append(APM_NOT_CALLED_LIMIT)
    for p in payloads:
        tool = str(p.get("tool") or "apm_*")
        code = p.get("error")
        if code in APM_UNAVAILABLE_CODES:
            reason = str(p.get("reason") or "").strip()
            out.append(
                f"APM 미가용({tool}: {code}{' — ' + reason if reason else ''}) — 폴스타 MCP 도구로 대체"
            )
        resolution = p.get("instance_resolution")
        if isinstance(resolution, dict):
            conf = str(resolution.get("confidence") or "").strip().lower()
            if conf in _WEAK_CONFIDENCE:
                why = str(resolution.get("reason") or "").strip()
                out.append(
                    f"APM 인스턴스 정합 신뢰도 {conf}{'(' + why + ')' if why else ''} — "
                    f"{tool} 결과의 인스턴스 귀속이 불확실"
                )
        for limit in p.get("limits") or []:
            text = _strip_limit_prefix(str(limit))
            if text:
                out.append(f"APM {tool}: {text}")
    return list(dict.fromkeys(out))


def repeated_calls(
    calls: Iterable[tuple[str, str]], threshold: int = STALL_REPEAT_THRESHOLD
) -> list[tuple[str, str, int]]:
    """같은 `(도구명, 인자 표기)`가 `threshold`회 이상인 것을 처음 본 순서대로 돌려준다."""
    counts = Counter(calls)
    return [(name, args, n) for (name, args), n in counts.items() if n >= threshold]


def stall_limitation(tool_name: str, args: str, count: int) -> str:
    """정체 가드 한계 문구 한 줄(인자 표기는 120자에서 자른다)."""
    shown = args if len(args) <= 120 else args[:120] + "…"
    return (
        f"조사 정체 — 같은 도구·같은 인자 호출 {count}회({tool_name} {shown}) → 조사 미결"
        "(사후 판정 · 반복 호출이 쓴 step은 이미 소진됨)"
    )


__all__ = [
    "APM_RESOURCE_TYPE",
    "is_apm_resource_type",
    "is_apm_trigger",
    "APM_UNAVAILABLE_CODES",
    "APM_NOT_CONFIGURED_LIMIT",
    "APM_NOT_CALLED_LIMIT",
    "STALL_REPEAT_THRESHOLD",
    "apm_limitations",
    "repeated_calls",
    "stall_limitation",
]
