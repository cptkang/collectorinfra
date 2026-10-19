"""조사 대화의 「추가 조사 요청」 도메인 (plans/187 W10 · D-341 ⑭ · G-21) — 순수 함수.

부모 알람 조사(브리핑)에서 다음 셋을 **결정적으로** 만든다. LLM을 부르지 않는다.

1. 초점 목록 — 코드 카탈로그(`FOCUSES`). 브리핑 플레이북에 해당 단계가 있을 때만 활성이다.
2. 대상·사건창 — 브리핑 `playbook.scope`에서 읽고, 없으면 레코드의 알람 정보(호출부가 넘긴
   값)로 채운다.
3. `prior_findings` — 브리핑 `playbook.sections[]`·판정 칩에서 **화이트리스트 키만** 옮긴
   구조화 요약이다.
   소견(`opinion`)·종합(`synthesis`)·표·배지 같은 서술·자유 텍스트는 싣지 않는다. 값도 공백 없는
   짧은 토큰(enum)·숫자·불리언만 통과시킨다 — 문장이 키 이름을 바꿔 들어와도 걸러진다.

보충 한 줄(`sanitize_note`)은 개행·제어 문자를 공백으로 바꾸고, 길이 상한을 넘으면 거부한다(잘라서
침묵 수용하지 않는다). 마스킹 함수는 호출부가 주입한다(domain은 security 계층을 import하지 않는다).

질문 문장 템플릿은 `src/prompts/investigation_followup.py`에 있다(plans/155 §2.5 배치).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

#: `prior_findings` 계약 버전 — sre_agent가 같은 값을 보고 렌더한다.
PRIOR_FINDINGS_VERSION = 1

#: 저장 레코드·감사의 출처 값(plans/187 W10 — 조사 대화에서 접수).
ORIGIN_INVESTIGATION_CHAT = "investigation_chat"

#: 응답의 고정 표기(D-335 ⑧ · D-341 ⑭ — 결과는 가설이며 원 판정·브리핑을 바꾸지 않는다).
HYPOTHESIS_NOTICE = "가설 · 참고용 — 자동 조사 결과이며 확정 원인이 아닙니다"

#: enum 토큰 — 공백·문장부호 없는 짧은 식별자(서술 차단). 한글 한 단어(예 「심각」)는 허용한다.
_TOKEN_RE = re.compile(r"^[0-9A-Za-z가-힣_.\-/]{1,48}$")
#: 조사 id — 토큰보다 길 수 있다(uuid 등).
_ID_RE = re.compile(r"^[0-9A-Za-z_.:\-]{1,80}$")
#: 개행·탭·기타 제어 문자.
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f  ]+")

MAX_SECTIONS = 20
MAX_TOOLS = 40
MAX_READINGS = 30

#: 섹션에서 옮기는 키(화이트리스트). `readings`·`tools`는 아래에서 따로 검사한다.
SECTION_SCALAR_KEYS: tuple[str, ...] = ("step", "status", "verdict", "verdict_source")


@dataclass(frozen=True)
class FollowupFocus:
    """추가 조사 초점 1종.

    Attributes:
        id: 요청 본문 `focus` 값.
        label: 화면 표시명.
        requires_steps: 이 중 하나라도 부모 플레이북 섹션에 있어야 활성이다. 비면 항상 활성.
    """

    id: str
    label: str
    requires_steps: tuple[str, ...] = ()


#: 초점 카탈로그(순서 = 화면 순서). `cause_recheck`는 플레이북이 없는 브리핑(현행
#: HolmesGPT)에서도 쓸 수 있는 범용 초점이다.
FOCUSES: tuple[FollowupFocus, ...] = (
    FollowupFocus("top_transaction", "1위 트랜잭션 원인", ("S3",)),
    FollowupFocus("db_wait", "DB 측 대기", ("S2", "S3")),
    FollowupFocus("change_impact", "변경 영향", ("S7",)),
    FollowupFocus("same_business_hosts", "같은 업무 다른 서버", ("S0",)),
    FollowupFocus("cause_recheck", "원인 가설 재확인"),
)
_FOCUS_BY_ID: dict[str, FollowupFocus] = {f.id: f for f in FOCUSES}

#: 비활성 사유(응답 `focuses[].reason`).
REASON_STEP_MISSING = "부모 조사 브리핑에 해당 단계가 없습니다"


@dataclass(frozen=True)
class FollowupTarget:
    """추가 조사 대상·사건창(결정적으로 채운 값).

    Attributes:
        hostname: 대상 hostname(없으면 None).
        server_name: 대상 서버명(없으면 None).
        db_id: 대상 DB 식별자(없으면 None).
        reference_time: 사건 기준시각 ISO 8601(없으면 None).
        lookback_minutes: 기준시각 이전 폭(분 · 없으면 None).
        source: 값의 출처 — `briefing_scope` | `alarm_record`.
    """

    hostname: str | None
    server_name: str | None
    db_id: str | None
    reference_time: str | None
    lookback_minutes: int | None
    source: str

    @property
    def label(self) -> str:
        """대상 표시명(호스트 우선 · 없으면 빈 문자열)."""
        return self.hostname or self.server_name or ""

    def as_subject(self) -> dict[str, Any]:
        """저장 레코드 `subject`용 dict(값 있는 키만)."""
        raw = {
            "hostname": self.hostname,
            "server_name": self.server_name,
            "db_id": self.db_id,
            "reference_time": self.reference_time,
            "lookback_minutes": self.lookback_minutes,
            "target_source": self.source,
        }
        return {k: v for k, v in raw.items() if v not in (None, "")}


def _is_token(value: Any) -> bool:
    return isinstance(value, str) and bool(_TOKEN_RE.match(value))


def _iso(value: Any) -> str | None:
    """ISO 8601 문자열이면 그대로(초 해상도 정규화), 아니면 None."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, float) and value.is_integer() and value > 0:
        return int(value)
    return None


def _text(value: Any) -> str | None:
    """식별자 문자열(앞뒤 공백 제거 · 비거나 문자열이 아니면 None)."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def playbook_of(briefing: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """브리핑의 선택 키 `playbook`(dict가 아니면 None — 현행 HolmesGPT 브리핑)."""
    if not isinstance(briefing, Mapping):
        return None
    playbook = briefing.get("playbook")
    return playbook if isinstance(playbook, Mapping) else None


def _sections(playbook: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    raw = (playbook or {}).get("sections")
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, Mapping)]


def present_steps(briefing: Mapping[str, Any] | None) -> set[str]:
    """부모 플레이북에 섹션이 있는 단계 id 집합(`status`가 `skipped`인 것은 제외)."""
    out: set[str] = set()
    for section in _sections(playbook_of(briefing)):
        step = section.get("step")
        if _is_token(step) and section.get("status") != "skipped":
            out.add(str(step))
    return out


def focus_options(briefing: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """초점 목록 — `[{id, label, active, reason?}]`(카탈로그 순)."""
    steps = present_steps(briefing)
    out: list[dict[str, Any]] = []
    for focus in FOCUSES:
        active = not focus.requires_steps or any(s in steps for s in focus.requires_steps)
        item: dict[str, Any] = {"id": focus.id, "label": focus.label, "active": active}
        if not active:
            item["reason"] = REASON_STEP_MISSING
        out.append(item)
    return out


def resolve_focus(
    focus_id: str, briefing: Mapping[str, Any] | None
) -> tuple[FollowupFocus | None, str | None]:
    """요청 초점을 검사한다 — (초점, None) 또는 (None, 거부 사유)."""
    focus = _FOCUS_BY_ID.get(str(focus_id or ""))
    if focus is None:
        return None, f"알 수 없는 조사 초점입니다: {str(focus_id)[:40]!r}"
    option = next(o for o in focus_options(briefing) if o["id"] == focus.id)
    if not option["active"]:
        return None, f"「{focus.label}」 초점은 쓸 수 없습니다 — {REASON_STEP_MISSING}"
    return focus, None


def sanitize_note(
    note: str | None, max_chars: int, mask_fn: Callable[[str], str] | None = None
) -> tuple[str | None, str | None]:
    """보충 한 줄을 정리한다 — (정리된 문장 또는 None, 거부 사유 또는 None).

    개행·제어 문자는 공백으로 바꾸고 연속 공백을 하나로 줄인다. 상한(`max_chars`)을 넘으면
    자르지 않고 거부한다. `mask_fn`은 공백으로 나눈 낱말마다 적용한다(값 전체 일치 패턴이라
    낱말 단위로 대조).
    """
    if note is None:
        return None, None
    text = " ".join(_CONTROL_RE.sub(" ", str(note)).split())
    if not text:
        return None, None
    if len(text) > max_chars:
        return None, f"보충 문장은 {max_chars}자 이내로 입력해 주세요(현재 {len(text)}자)"
    if mask_fn is not None:
        text = " ".join(mask_fn(word) for word in text.split(" "))
    return text, None


def resolve_target(
    briefing: Mapping[str, Any] | None,
    *,
    fallback_hostname: str | None = None,
    fallback_server_name: str | None = None,
    fallback_db_id: str | None = None,
    fallback_reference_time: str | None = None,
) -> FollowupTarget | None:
    """대상·사건창을 정한다 — 브리핑 `playbook.scope` 우선, 없으면 알람 정보(호출부 값).

    `playbook.scope` 계약(plans/187 §5.5 · W4가 채운다): `host`·`polestar_server`·`polestar_source`
    (대상 DB id) · 사건창 `incident_window{end, minutes}`(scope 또는 playbook 최상위).
    대상 호스트·서버명이 모두 없으면 None(호출부가 400으로 거부한다).
    """
    playbook = playbook_of(briefing)
    scope = (playbook or {}).get("scope")
    has_target = isinstance(scope, Mapping) and (
        _text(scope.get("host")) or _text(scope.get("polestar_server"))
    )
    if isinstance(scope, Mapping) and has_target:
        window = scope.get("incident_window")
        if not isinstance(window, Mapping):
            window = (playbook or {}).get("incident_window")
        window = window if isinstance(window, Mapping) else {}
        reference_time = _iso(window.get("end"))
        return FollowupTarget(
            hostname=_text(scope.get("host")),
            server_name=_text(scope.get("polestar_server")),
            db_id=_text(scope.get("polestar_source")) or _text(fallback_db_id),
            reference_time=reference_time,
            lookback_minutes=_positive_int(window.get("minutes")) if reference_time else None,
            source="briefing_scope",
        )
    hostname = _text(fallback_hostname)
    server_name = _text(fallback_server_name)
    if not hostname and not server_name:
        return None
    return FollowupTarget(
        hostname=hostname,
        server_name=server_name,
        db_id=_text(fallback_db_id),
        reference_time=_iso(fallback_reference_time),
        lookback_minutes=None,
        source="alarm_record",
    )


def _clean_readings(raw: Any) -> dict[str, Any]:
    """판독 enum만 남긴다 — 키는 토큰, 값은 토큰·정수·실수·불리언·None(목록·dict·문장은 버린다)."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if len(out) >= MAX_READINGS:
            break
        if not _is_token(key):
            continue
        if value is None or isinstance(value, (bool, int, float)) or _is_token(value):
            out[str(key)] = value
    return out


def _clean_tools(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(t) for t in dict.fromkeys(t for t in raw if _is_token(t))][:MAX_TOOLS]


def _clean_section(section: Mapping[str, Any]) -> dict[str, Any] | None:
    out: dict[str, Any] = {
        key: section[key] for key in SECTION_SCALAR_KEYS if _is_token(section.get(key))
    }
    if "step" not in out:
        return None
    readings = _clean_readings(section.get("readings"))
    if readings:
        out["readings"] = readings
    tools = _clean_tools(section.get("tools"))
    if tools:
        out["tools"] = tools
    return out


def _verdict_chip(rec: Mapping[str, Any]) -> dict[str, str]:
    """판정 칩 — 레코드 verdict(dict)·브리핑 severity의 level·confidence 토큰만."""
    out: dict[str, str] = {}
    verdict = rec.get("verdict")
    briefing = rec.get("briefing")
    severity = briefing.get("severity") if isinstance(briefing, Mapping) else None
    for source in (verdict, severity):
        if not isinstance(source, Mapping):
            continue
        for key in ("level", "confidence"):
            value = source.get(key)
            if key not in out and _is_token(value):
                out[key] = str(value)
    return out


def build_prior_findings(
    parent: Mapping[str, Any], target: FollowupTarget | None = None
) -> dict[str, Any]:
    """부모 조사 레코드 → `prior_findings`(화이트리스트 구조화 요약 · LLM 서술 제외).

    모양(버전 1 — sre_agent `investigation_guidance`가 같은 화이트리스트로 다시 거른다)::

        {version, source: "playbook"|"briefing", parent_investigation_id?, playbook_id?,
         window?{reference_time, lookback_minutes?}, verdict?{level?, confidence?},
         readings?{키: enum},
         sections?[{step, status?, verdict?, verdict_source?, readings?, tools?}],
         tools?[...]}

    플레이북이 없는 브리핑(현행 HolmesGPT)이면 `source="briefing"`이고 사건창·판정 칩만
    싣는다 — 저장소가 사용 도구 목록을 보관하지 않으므로 `tools`는 비어 있다.
    """
    briefing = parent.get("briefing")
    playbook = playbook_of(briefing if isinstance(briefing, Mapping) else None)
    out: dict[str, Any] = {
        "version": PRIOR_FINDINGS_VERSION,
        "source": "playbook" if playbook is not None else "briefing",
    }
    parent_id = parent.get("investigation_id")
    if isinstance(parent_id, str) and _ID_RE.match(parent_id):
        out["parent_investigation_id"] = parent_id
    if playbook is not None and _is_token(playbook.get("id")):
        out["playbook_id"] = str(playbook["id"])
    if target is not None and target.reference_time:
        window: dict[str, Any] = {"reference_time": target.reference_time}
        if target.lookback_minutes:
            window["lookback_minutes"] = target.lookback_minutes
        out["window"] = window
    chip = _verdict_chip(parent)
    if chip:
        out["verdict"] = chip
    if playbook is None:
        return out
    readings = _clean_readings(playbook.get("readings"))
    if readings:
        out["readings"] = readings
    sections = [
        cleaned for cleaned in (_clean_section(s) for s in _sections(playbook)) if cleaned
    ][:MAX_SECTIONS]
    if sections:
        out["sections"] = sections
    tools = _clean_tools([t for s in sections for t in s.get("tools", [])])
    if tools:
        out["tools"] = tools
    return out


__all__ = [
    "FOCUSES",
    "FollowupFocus",
    "FollowupTarget",
    "HYPOTHESIS_NOTICE",
    "ORIGIN_INVESTIGATION_CHAT",
    "PRIOR_FINDINGS_VERSION",
    "REASON_STEP_MISSING",
    "build_prior_findings",
    "focus_options",
    "playbook_of",
    "present_steps",
    "resolve_focus",
    "resolve_target",
    "sanitize_note",
]
