"""담당 업무 어휘 — 순수 판정 (plans/201 W1 · D-354).

**무엇을 하나.** 담당 업무 영역(`server_os` 등)의 어휘 객체와, 그 어휘로 하는 결정적 판정
(알람 자원 유형 → 영역 · 저장 값 검증·정규화 · 폐지 코드 걸러내기)만 둔다. 파일을 읽지 않는다 —
선언 파일(`config/duty_areas.yaml`) 로드는 `src.infrastructure.duty_area_vocabulary` 소관이다.

**담당 업무는 권한이 아니다**(D-354). 무엇도 숨기지 않고 정렬·강조 순서만 바꾸는 중점이라,
인가 판정 모듈은 이 모듈을 읽지 않는다.

**영역 문자열은 코드에 없다.** 코드는 선언 파일이 준 `code`만 다룬다 — 영역 추가는 선언 파일로 한다.

계층: domain — stdlib 전용(`noise_gate`가 W3에서 재사용할 수 있게 · D-139 부기).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class UnknownDutyAreaError(ValueError):
    """어휘에 없는 담당 업무 코드가 들어왔다(호출부가 422로 바꾼다 — 조용히 버리지 않는다)."""

    def __init__(self, unknown: Iterable[str]) -> None:
        self.unknown: tuple[str, ...] = tuple(unknown)
        super().__init__("알 수 없는 담당 업무: " + ", ".join(self.unknown))


@dataclass(frozen=True)
class DutyArea:
    """담당 업무 영역 하나. 값은 전부 선언 파일에서 온다."""

    code: str
    label: str
    description: str = ""
    resource_types: tuple[str, ...] = ()
    alarm_kinds: tuple[str, ...] = ()
    playbook_prefixes: tuple[str, ...] = ()


#: 제니퍼 이벤트 유형 대조 정규화에서 1회 떼는 접두(게이트웨이 `normalize_event_type`과 같은 규칙).
_APM_TYPE_PREFIXES: tuple[str, ...] = ("ERROR_", "WARNING_")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_apm_event_type(value: object) -> str:
    """제니퍼 이벤트 유형을 대조용으로 정규화한다(plans/201 W3 §5.3 · 순수).

    strip → upper → 연속 공백을 `_` 하나로 → `ERROR_`/`WARNING_` 접두 1회 제거. 폐쇄망 원값
    「METRICS EVENT」(공백 포함)도 `METRICS_EVENT`로 맞는다. 프론트(`app.js`
    `normalizeApmEventType`)도 같은 규칙이다 — 두 구현은 같은 픽스처 테스트로 고정한다.
    문자열이 아니거나 빈 값이면 빈 문자열이다.
    """
    if not isinstance(value, str):
        return ""
    text = _WHITESPACE_RUN.sub("_", value.strip().upper())
    for prefix in _APM_TYPE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text


@dataclass(frozen=True)
class CauseHint:
    """원인 단서 한 줄 — 단서 영역 코드와 근거 문구(선언 파일 값)."""

    areas: tuple[str, ...]
    label: str


@dataclass(frozen=True)
class CauseHints:
    """`cause_hints` 섹션의 검증된 값(plans/201 W3 §5.3).

    단서는 **원인 판정이 아니다** — 알람이 이미 싣고 온 정보(제니퍼 이벤트 유형 · 영향 프로세스 종류)로
    담당 영역을 가리키는 표시일 뿐이다.

    - `apm_event_types`: 정규화 유형(`normalize_apm_event_type`) → 단서.
    - `known_unmapped`: 유형만으로 원인 영역을 못 가르는 것으로 **알려진** 정규화 유형(매핑 점검 배지에서 뺀다).
    - `process_kinds`: 미들웨어 시그니처 kind(소문자) → 단서.
    """

    apm_event_types: Mapping[str, CauseHint] = field(default_factory=dict)
    known_unmapped: tuple[str, ...] = ()
    process_kinds: Mapping[str, CauseHint] = field(default_factory=dict)

    def apm_hint(self, event_type: object) -> CauseHint | None:
        """제니퍼 이벤트 유형(원값)의 단서. 미매핑·빈 값은 None."""
        return self.apm_event_types.get(normalize_apm_event_type(event_type))

    def is_known_unmapped(self, event_type: object) -> bool:
        """알려진 미매핑 유형인가(정규화 대조)."""
        return normalize_apm_event_type(event_type) in self.known_unmapped

    def to_meta(self) -> dict[str, Any]:
        """화면 메타(`/auth/me` `duty_alarm_focus.cause_hints`) — JSON 직렬화 가능한 dict."""
        return {
            "apm_event_types": {
                key: {"areas": list(hint.areas), "label": hint.label}
                for key, hint in self.apm_event_types.items()
            },
            "process_kinds": {
                key: {"areas": list(hint.areas), "label": hint.label}
                for key, hint in self.process_kinds.items()
            },
        }


@dataclass(frozen=True)
class DutyAreaVocabulary:
    """담당 업무 어휘 전체.

    `sections`는 `areas` 밖의 최상위 섹션 원문이다 — 뒤 Wave가 섹션(`cause_hints`·
    `known_unmapped` 등)을 더할 때 이 객체 구조를 바꾸지 않고 읽게 둔다.
    """

    areas: tuple[DutyArea, ...] = ()
    sections: Mapping[str, Any] = field(default_factory=dict)
    # (plans/201 W3) `cause_hints` 섹션의 검증된 값 — 원문은 `sections["cause_hints"]`에도 남는다.
    cause_hints: CauseHints = field(default_factory=CauseHints)

    def codes(self) -> tuple[str, ...]:
        """선언 순서의 영역 코드."""
        return tuple(area.code for area in self.areas)

    def options(self) -> list[dict[str, str]]:
        """화면용 영역 메타 `[{code, label}]`(선언 순서)."""
        return [{"code": area.code, "label": area.label} for area in self.areas]

    def areas_for_resource_type(self, resource_type: str | None) -> list[str]:
        """알람 자원 유형(코드값·표시 이름)이 속한 영역 코드 목록(선언 순서 · 대소문자 무시).

        한 유형이 여러 영역에 속할 수 있다. 빈 값·미매핑은 빈 목록이다.
        """
        needle = (resource_type or "").strip().lower()
        if not needle:
            return []
        return [
            area.code
            for area in self.areas
            if any(rt.strip().lower() == needle for rt in area.resource_types)
        ]

    def normalize_codes(self, values: Iterable[str] | None) -> list[str] | None:
        """입력 코드 목록을 검증·정규화한다(저장 직전).

        - 어휘 밖 코드가 하나라도 있으면 `UnknownDutyAreaError`(조용히 버리지 않는다).
        - 앞뒤 공백 제거 · 중복 제거 · 선언 순서 정렬.
        - 빈 목록(또는 None)은 미지정 = None.
        """
        if values is None:
            return None
        cleaned = [str(v).strip() for v in values]
        known = set(self.codes())
        unknown = [v for v in dict.fromkeys(cleaned) if v not in known]
        if unknown:
            raise UnknownDutyAreaError(unknown)
        chosen = set(cleaned)
        result = [code for code in self.codes() if code in chosen]
        return result or None

    def split_known(self, values: Iterable[str] | None) -> tuple[list[str], list[str]]:
        """저장 값을 (어휘에 있는 코드 · 폐지된 코드)로 나눈다(읽을 때 · 선언 순서 정렬)."""
        stored = [str(v) for v in (values or [])]
        known = set(self.codes())
        kept_set = {v for v in stored if v in known}
        kept = [code for code in self.codes() if code in kept_set]
        dropped = [v for v in dict.fromkeys(stored) if v not in known]
        return kept, dropped


def _str_tuple(value: Any) -> tuple[str, ...]:
    """선언 값(목록이 아니면 빈 값)을 문자열 튜플로 바꾼다."""
    if not isinstance(value, list):
        return ()
    return tuple(str(v) for v in value if v is not None and str(v).strip())


def parse_vocabulary(raw: Mapping[str, Any] | None) -> DutyAreaVocabulary:
    """선언 파일 원문(dict)을 어휘 객체로 바꾼다(순수 · 결정적).

    `code`·`label`이 없거나 `code`가 중복인 항목은 건너뛰고 경고 1줄을 남긴다(침묵 금지).
    """
    if not isinstance(raw, Mapping):
        return DutyAreaVocabulary()
    areas: list[DutyArea] = []
    seen: set[str] = set()
    entries = raw.get("areas")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            logger.warning("담당 업무 어휘: 형식이 아닌 항목을 건너뜀 — %r", entry)
            continue
        code = str(entry.get("code") or "").strip()
        label = str(entry.get("label") or "").strip()
        if not code or not label or code in seen:
            logger.warning("담당 업무 어휘: code·label 누락 또는 중복 항목을 건너뜀 — %r", code)
            continue
        seen.add(code)
        areas.append(
            DutyArea(
                code=code,
                label=label,
                description=str(entry.get("description") or ""),
                resource_types=_str_tuple(entry.get("resource_types")),
                alarm_kinds=_str_tuple(entry.get("alarm_kinds")),
                playbook_prefixes=_str_tuple(entry.get("playbook_prefixes")),
            )
        )
    sections = {k: v for k, v in raw.items() if k != "areas"}
    hints = parse_cause_hints(sections.get("cause_hints"), tuple(area.code for area in areas))
    return DutyAreaVocabulary(areas=tuple(areas), sections=sections, cause_hints=hints)


def _hint_areas(value: Any, known: tuple[str, ...], where: str) -> tuple[str, ...]:
    """단서 영역 목록을 검증한다 — 어휘 밖 코드는 경고 후 뺀다(선언 순서 · 중복 제거)."""
    codes = _str_tuple(value)
    unknown = [c for c in codes if c not in known]
    if unknown:
        logger.warning("담당 업무 단서(%s): 어휘 밖 영역 코드를 무시 — %s", where, ", ".join(unknown))
    chosen = set(codes)
    return tuple(code for code in known if code in chosen)


def parse_cause_hints(raw: Any, known_areas: tuple[str, ...]) -> CauseHints:
    """`cause_hints` 섹션 원문을 검증해 단서 표로 바꾼다(순수 · 결정적 · plans/201 W3).

    비정상 항목(매핑이 아님 · 근거 문구 없음 · 영역이 비거나 전부 어휘 밖)은 경고 1줄 후 건너뛴다
    (침묵 금지 · 앱은 계속 뜬다). 유형 키는 정규화해 대조한다 — 같은 정규화 키가 두 번이면 앞의 것.
    매핑된 유형이 `known_unmapped`에도 있으면 매핑을 살리고 미매핑 목록에서 뺀다(경고).
    """
    if raw is None:
        return CauseHints()
    if not isinstance(raw, Mapping):
        logger.warning("담당 업무 단서: cause_hints가 매핑이 아님 — 무시")
        return CauseHints()

    apm: dict[str, CauseHint] = {}
    entries = raw.get("apm_event_types")
    if entries is not None and not isinstance(entries, Mapping):
        logger.warning("담당 업무 단서: apm_event_types가 매핑이 아님 — 무시")
        entries = None
    for name, spec in (entries or {}).items():
        key = normalize_apm_event_type(name)
        if not key or not isinstance(spec, Mapping):
            logger.warning("담당 업무 단서(apm_event_types): 형식이 아닌 항목을 건너뜀 — %r", name)
            continue
        label = str(spec.get("label") or "").strip()
        areas = _hint_areas(spec.get("areas"), known_areas, f"apm_event_types.{key}")
        if not label or not areas or key in apm:
            logger.warning(
                "담당 업무 단서(apm_event_types): 근거 문구·영역 누락 또는 중복 항목을 건너뜀 — %r", key
            )
            continue
        apm[key] = CauseHint(areas=areas, label=label)

    unmapped: list[str] = []
    raw_unmapped = raw.get("known_unmapped")
    if raw_unmapped is not None and not isinstance(raw_unmapped, list):
        logger.warning("담당 업무 단서: known_unmapped가 목록이 아님 — 무시")
        raw_unmapped = None
    for name in raw_unmapped or []:
        key = normalize_apm_event_type(name)
        if not key:
            logger.warning("담당 업무 단서(known_unmapped): 빈 값·비문자열을 건너뜀 — %r", name)
            continue
        if key in apm:
            logger.warning("담당 업무 단서: %s는 apm_event_types에 매핑돼 있어 known_unmapped에서 뺌", key)
            continue
        if key not in unmapped:
            unmapped.append(key)

    kinds: dict[str, CauseHint] = {}
    groups = raw.get("process_kinds")
    if groups is not None and not isinstance(groups, Mapping):
        logger.warning("담당 업무 단서: process_kinds가 매핑이 아님 — 무시")
        groups = None
    for area_code, spec in (groups or {}).items():
        code = str(area_code).strip()
        if code not in known_areas or not isinstance(spec, Mapping):
            logger.warning(
                "담당 업무 단서(process_kinds): 어휘 밖 영역 또는 형식이 아닌 항목을 건너뜀 — %r", area_code
            )
            continue
        label = str(spec.get("label") or "").strip()
        if not label:
            logger.warning("담당 업무 단서(process_kinds.%s): 근거 문구 누락 — 건너뜀", code)
            continue
        for kind in _str_tuple(spec.get("kinds")):
            key = kind.strip().lower()
            if key in kinds:
                prev = kinds[key]
                merged = tuple(c for c in known_areas if c in set(prev.areas) | {code})
                kinds[key] = CauseHint(areas=merged, label=prev.label)
            else:
                kinds[key] = CauseHint(areas=(code,), label=label)

    return CauseHints(apm_event_types=apm, known_unmapped=tuple(unmapped), process_kinds=kinds)



def unmapped_resource_types(
    vocabulary: DutyAreaVocabulary,
    counts: Mapping[str, int],
    *,
    empty_count: int = 0,
    top_n: int = 5,
) -> dict[str, Any]:
    """`resource_type` 값별 건수에서 어느 영역에도 안 맞는 유형을 고른다(매핑 점검 · 순수).

    `counts`는 값별 건수(빈 값 제외)이고, 빈 `resource_type` 수는 `empty_count`로 따로 받는다
    — 레코드 목록을 받지 않는다(호출부가 스트리밍으로 센다). 상위는 건수 내림차순 · 같은 건수는
    이름 오름차순(결정적).

    Returns:
        `{checked, empty_count, unmapped_total, unmapped_distinct, top: [{resource_type, count}]}`
    """
    unmapped = {
        rt: int(n) for rt, n in counts.items() if not vocabulary.areas_for_resource_type(rt)
    }
    ranked = sorted(unmapped.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "checked": sum(int(n) for n in counts.values()),
        "empty_count": int(empty_count),
        "unmapped_total": sum(unmapped.values()),
        "unmapped_distinct": len(unmapped),
        "top": [{"resource_type": rt, "count": n} for rt, n in ranked[: max(0, top_n)]],
    }


def unmapped_apm_event_types(
    vocabulary: DutyAreaVocabulary,
    counts: Mapping[str, int],
    *,
    empty_count: int = 0,
    top_n: int = 5,
) -> dict[str, Any]:
    """제니퍼 이벤트 유형 값별 건수에서 단서 미매핑 유형을 고른다(매핑 점검 · 순수 · plans/201 W3).

    값은 정규화(`normalize_apm_event_type`)해 합친다 — 「METRICS EVENT」와 「METRICS_EVENT」는 한 유형이다.
    `cause_hints.known_unmapped`(알려진 미매핑)는 `unmapped_*`·`top`에서 빼고 `known_unmapped`로
    따로 접는다 — 배지(`unmapped_distinct`)는 조치할 것만 센다. 정렬은 건수 내림차순 · 이름 오름차순.

    Returns:
        `{checked, empty_count, unmapped_total, unmapped_distinct, top: [{event_type, count}],
        known_unmapped_total, known_unmapped: [{event_type, count}]}`
    """
    hints = vocabulary.cause_hints
    merged: dict[str, int] = {}
    for value, n in counts.items():
        key = normalize_apm_event_type(value)
        if key:
            merged[key] = merged.get(key, 0) + int(n)
    unmapped = {k: n for k, n in merged.items() if k not in hints.apm_event_types}
    known = {k: n for k, n in unmapped.items() if k in hints.known_unmapped}
    action = {k: n for k, n in unmapped.items() if k not in known}

    def _ranked(items: Mapping[str, int]) -> list[dict[str, Any]]:
        ordered = sorted(items.items(), key=lambda kv: (-kv[1], kv[0]))
        return [{"event_type": k, "count": n} for k, n in ordered]

    return {
        "checked": sum(int(n) for n in counts.values()),
        "empty_count": int(empty_count),
        "unmapped_total": sum(action.values()),
        "unmapped_distinct": len(action),
        "top": _ranked(action)[: max(0, top_n)],
        "known_unmapped_total": sum(known.values()),
        "known_unmapped": _ranked(known),
    }
