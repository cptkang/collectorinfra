"""채팅 증상 질의 라우터 (plans/187 W12 · §5.6.2 ③ · D-341 ㉕㉖ · G-32·G-33) — 순수 함수.

채팅의 장애 진단 질의를 **결정적으로** 두 엔진 중 하나로 보낸다. LLM 분류를 쓰지 않는다(D-035).

1. 대상: 이번 턴에 명시 식별된 서버가 정확히 1건인가(지시어는 식별자가 아니다 — 호출부가
   이번 턴 식별 결과만 넘긴다).
2. 증상 → 유형: 질의 표면어를 플레이북 유형으로 매핑해 후보가 정확히 1건인가.
3. 둘 다 성립 → 플레이북 잡(가상 알람 맥락). 하나라도 불성립 → HolmesGPT(`sre_diagnose`).

## 사전 — sre_agent 정본의 사본

본체는 sre_agent를 import할 수 없다(D-118 · 양방향 import 0). 그래서 사전을 여기에 사본으로 둔다.

- `KIND_KEYWORDS` = sre_agent `investigation_guidance._KIND_KEYWORDS`(`classify_alarm_kind` 어휘).
- `PLAYBOOK_SURFACE_TERMS`·`PLAYBOOK_ALARM_KINDS` = `sre_agent/config/playbooks/*.yaml`의
  `match.surface_terms`·`match.alarm_kind`.
- `VIRTUAL_EVENTS` = 플레이북별 가상 알람의 (resourceType, alarmName). sre_agent의 기존 플레이북
  선택(`alarm_facts_from_payload` → `select_playbook`)이 그 플레이북을 고르는 값이다.

드리프트는 테스트가 잡는다 — 본체 쪽은 sre_agent 파일을 읽어 대조하고
(`tests/test_plan187_w12_symptom_router.py`), sre_agent 쪽은 이 파일의 `VIRTUAL_EVENTS` 리터럴을
읽어 실제 선택 결과를 대조한다(`sre_agent/tests/test_plan187_w12_virtual.py`). 둘 다 import 없이
파일을 읽는다. `VIRTUAL_EVENTS`는 그래서 **리터럴 dict**로 둔다(ast로 읽힌다).

계층: domain — 표준 라이브러리만 쓴다(I/O·LLM·전역 상태 0).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: 경로(엔진) 값 — 카드 머리 표기와 저장 레코드 `subject.route`.
ROUTE_PLAYBOOK = "playbook"
ROUTE_HOLMESGPT = "holmesgpt"

#: 판정 사유 코드(결정적) — 표기 문구는 `REASON_LABELS`.
REASON_MATCHED = "matched"
REASON_TARGET_NONE = "target_none"
REASON_TARGET_MULTI = "target_multi"
REASON_TYPE_NONE = "type_none"
REASON_TYPE_MULTI = "type_multi"
REASON_TYPE_UNRESOLVED = "type_unresolved"

REASON_LABELS: dict[str, str] = {
    REASON_MATCHED: "대상·증상 일치",
    REASON_TARGET_NONE: "대상 미확정",
    REASON_TARGET_MULTI: "대상 복수",
    REASON_TYPE_NONE: "증상 유형 미확정",
    REASON_TYPE_MULTI: "증상 유형 복수",
    # 증상 kind는 하나지만 그 kind의 플레이북이 없거나(로그) 둘 이상이다(디스크 — 용량·I/O).
    REASON_TYPE_UNRESOLVED: "증상 유형 미확정",
}

#: 대화 고지의 kind — `src/api/thread_history.py` `INVESTIGATION_REF_KIND`와 같은 값이다
#: (interface 상수를 domain이 import할 수 없어 문자열로 둔다 — 동등성은 테스트가 고정한다).
INVESTIGATION_REF_KIND = "investigation_ref"

#: sre_agent `classify_alarm_kind` 어휘 사본(순서 그대로).
KIND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cpu", ("cpu",)),
    ("memory", ("memory", "메모리", "mem")),
    ("disk", ("disk", "디스크", "volume", "filesystem", "inode")),
    ("network", ("network", "net", "traffic", "네트워크", "bandwidth")),
    ("process", ("process", "프로세스", "daemon", "service down", "프로세스다운")),
    ("log", ("log", "logmonitor", "로그")),
)

#: 플레이북 YAML `match.surface_terms` 사본(fallback 제외).
PLAYBOOK_SURFACE_TERMS: dict[str, tuple[str, ...]] = {
    "host.cpu": ("CPU", "CPU 사용률", "CPU 과다"),
    "host.disk_io": ("디스크 I/O", "디스크 IO", "디스크 대기"),
    "host.fs": ("디스크 용량", "디스크 사용률", "filesystem"),
    "host.memory": ("메모리", "메모리 사용률", "메모리 부족"),
    "host.network": ("네트워크", "네트워크 단절", "네트워크 장애"),
    "host.process": ("프로세스 다운", "프로세스 중단", "프로세스 종료"),
    "was.active": ("액티브 서비스", "스레드 정체", "서비스 행"),
    "was.error": ("에러율", "오류 급증", "예외 급증"),
    "was.heap": ("힙", "OOM", "가비지 컬렉션", "GC 과다"),
    "was.latency": ("응답 지연", "응답시간", "느려"),
    "was.pool": ("커넥션 풀", "DB 커넥션", "풀 고갈"),
}

#: 플레이북 YAML `match.alarm_kind` 사본(값이 있는 것만).
PLAYBOOK_ALARM_KINDS: dict[str, str] = {
    "host.cpu": "cpu",
    "host.disk_io": "disk",
    "host.fs": "disk",
    "host.memory": "memory",
    "host.network": "network",
    "host.process": "process",
}

#: 플레이북별 가상 알람 (resourceType, alarmName) — sre_agent 선택기가 그 플레이북을 고르는 값.
VIRTUAL_EVENTS: dict[str, tuple[str, str]] = {
    "host.cpu": ("", "CPU 사용률"),
    "host.disk_io": ("", "Disk I/O"),
    "host.fs": ("", "Filesystem 용량"),
    "host.memory": ("", "메모리 사용률"),
    "host.network": ("", "Network"),
    "host.process": ("", "Process Down"),
    "was.active": ("apm.Instance", "SERVICE_QUEUING"),
    "was.error": ("apm.Instance", "HIGH_RATE_FAIL"),
    "was.heap": ("apm.Instance", "JVM_HEAP_MEM_HIGH"),
    "was.latency": ("apm.Instance", "TX_BAD_RESPONSE"),
    "was.pool": ("apm.Instance", "DB_CONNECTION_FAIL"),
}

#: kind 키워드의 제외어 — 키워드가 다른 낱말의 일부로만 나타난 경우를 지운 뒤 대조한다
#: (plans/187 교정 · 「로그인」·「login」의 `로그`/`log`가 log kind로 잡히던 오탐). 사전
#: (`KIND_KEYWORDS` = sre_agent 정본 사본)은 바꾸지 않고 매칭 함수에서만 쓴다(사본 동등성 유지).
KEYWORD_EXCLUSIONS: dict[str, tuple[str, ...]] = {
    "log": ("login", "logon", "logout", "logoff", "blog", "catalog", "dialog", "logic"),
    "로그": ("로그인", "로그아웃", "로그온", "로그오프", "블로그", "카탈로그", "다이얼로그"),
}

#: 진단 요청 표지 — 3단 라우터 프롬프트의 판단 규칙(「원인·진단·왜」가 없는 단순 조회는 진단이
#: 아니다)을 결정적으로 옮긴 것. 2단(LLM 의도 분류 없음)이 조회 질의를 조사로 가로채지 않게 한다.
DIAGNOSIS_CUES: tuple[str, ...] = ("원인", "진단", "왜", "rca")

_SPACE_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """표면어 비교용 — 소문자 · 공백 제거(「응답 지연」 = 「응답지연」)."""
    return _SPACE_RE.sub("", text or "").lower()


def _keyword_hit(lowered: str, word: str) -> bool:
    """소문자 질의에 kind 키워드가 있는가 — 제외어(`KEYWORD_EXCLUSIONS`)를 지운 뒤 부분 일치."""
    text = lowered
    for excluded in KEYWORD_EXCLUSIONS.get(word, ()):
        text = text.replace(excluded, " ")
    return word in text


@dataclass(frozen=True)
class SymptomDictionary:
    """라우터 사전(기본값 = 위 사본)."""

    kind_keywords: tuple[tuple[str, tuple[str, ...]], ...] = KIND_KEYWORDS
    surface_terms: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: dict(PLAYBOOK_SURFACE_TERMS)
    )
    alarm_kinds: Mapping[str, str] = field(default_factory=lambda: dict(PLAYBOOK_ALARM_KINDS))


DEFAULT_DICTIONARY = SymptomDictionary()


@dataclass(frozen=True)
class SymptomRoute:
    """라우팅 결과.

    Attributes:
        route: `playbook` | `holmesgpt`.
        target: 확정 대상 1건(`server_name`·`hostname`·`db_id`) — 대상이 1건일 때만.
        alarm_kind: 증상 kind(`classify_alarm_kind` 어휘) — 정해졌을 때만.
        playbook_hint: 플레이북 id — 플레이북 경로일 때만.
        reason: 판정 사유 코드(`REASON_*`).
        label: 카드 머리 표기(「플레이북 was.latency — 대상·증상 일치」 등).
    """

    route: str
    target: dict[str, str] | None
    alarm_kind: str | None
    playbook_hint: str | None
    reason: str
    label: str

    @property
    def target_label(self) -> str:
        """대상 표시명(hostname → server_name 순) — 없으면 빈 문자열."""
        t = self.target or {}
        return t.get("hostname") or t.get("server_name") or ""


def _clean_target(item: Mapping[str, Any]) -> dict[str, str]:
    """대상 dict에서 식별 키(server_name·hostname·db_id)의 값 있는 것만 문자열로 남긴다."""
    return {
        key: str(item[key]).strip()
        for key in ("server_name", "hostname", "db_id")
        if item.get(key) is not None and str(item[key]).strip()
    }


def _strip_identifiers(query: str, targets: Sequence[Mapping[str, str]]) -> str:
    """질의에서 대상 식별자(서버명·hostname)를 지운다.

    「logsvr01」의 `log`가 증상으로 잡히지 않게 한다.
    """
    text = query or ""
    values = {t.get(k, "") for t in targets for k in ("server_name", "hostname")}
    for value in sorted((v for v in values if v), key=len, reverse=True):
        text = re.sub(re.escape(value), " ", text, flags=re.IGNORECASE)
    return text


def symptom_candidates(query: str, dictionary: SymptomDictionary = DEFAULT_DICTIONARY) -> list[str]:
    """질의 표면어 → 유형 후보(정렬된 목록).

    플레이북 id 또는 `kind:<kind>`(플레이북으로 좁혀지지 않은 kind)다.

    - `surface_terms`가 질의에 있으면 그 플레이북이 후보다(공백·대소문자 무시).
    - `classify_alarm_kind` 키워드가 있으면 그 kind가 후보다(classify와 같은 소문자 부분 문자열 —
      단 「로그인」처럼 키워드가 다른 낱말의 일부인 제외어는 먼저 지운다 · `KEYWORD_EXCLUSIONS`).
      이미 같은 kind의 플레이북이 표면어로 잡혔으면 더하지 않는다. 그 kind의 플레이북이 하나면
      그 플레이북, 없거나 둘 이상이면 `kind:<kind>`다.
    """
    folded = _norm(query)
    lowered = (query or "").lower()
    picked: set[str] = {
        pb for pb, terms in dictionary.surface_terms.items()
        if any(_norm(t) and _norm(t) in folded for t in terms)
    }
    picked_kinds = {dictionary.alarm_kinds.get(pb) for pb in picked}
    for kind, words in dictionary.kind_keywords:
        if not any(_keyword_hit(lowered, w) for w in words) or kind in picked_kinds:
            continue
        playbooks = sorted(pb for pb, k in dictionary.alarm_kinds.items() if k == kind)
        picked.add(playbooks[0] if len(playbooks) == 1 else f"kind:{kind}")
    return sorted(picked)


def has_diagnosis_cue(query: str) -> bool:
    """질의에 진단 요청 표지(`DIAGNOSIS_CUES`)가 있는가 — 소문자 부분 일치."""
    lowered = (query or "").lower()
    return any(cue in lowered for cue in DIAGNOSIS_CUES)


def route_symptom(
    query: str,
    targets: Sequence[Mapping[str, Any]],
    dictionary: SymptomDictionary = DEFAULT_DICTIONARY,
) -> SymptomRoute:
    """증상 질의를 플레이북 또는 HolmesGPT로 보낸다(결정적).

    Args:
        query: 사용자 질의 원문.
        targets: 이번 턴에 명시 식별된 대상 목록(지시어·직전 턴 승계는 넣지 않는다).
        dictionary: 라우터 사전(기본 = sre_agent 정본 사본).

    Returns:
        `SymptomRoute`. 대상 판정이 먼저다 — 대상 불성립이면 유형과 무관하게 그 사유를 쓴다.
    """
    cleaned = [t for t in (_clean_target(item) for item in targets) if t]
    target = cleaned[0] if len(cleaned) == 1 else None
    candidates = symptom_candidates(_strip_identifiers(query, cleaned), dictionary)
    only = candidates[0] if len(candidates) == 1 else None
    alarm_kind: str | None = None
    if only is not None:
        alarm_kind = (
            only[len("kind:"):] if only.startswith("kind:")
            else dictionary.alarm_kinds.get(only)
        )

    if not cleaned:
        reason = REASON_TARGET_NONE
    elif len(cleaned) > 1:
        reason = REASON_TARGET_MULTI
    elif not candidates:
        reason = REASON_TYPE_NONE
    elif len(candidates) > 1:
        reason = REASON_TYPE_MULTI
    elif only is not None and only.startswith("kind:"):
        reason = REASON_TYPE_UNRESOLVED
    else:
        return SymptomRoute(
            route=ROUTE_PLAYBOOK,
            target=target,
            alarm_kind=alarm_kind,
            playbook_hint=only,
            reason=REASON_MATCHED,
            label=f"플레이북 {only} — {REASON_LABELS[REASON_MATCHED]}",
        )
    return SymptomRoute(
        route=ROUTE_HOLMESGPT,
        target=target,
        alarm_kind=alarm_kind,
        playbook_hint=None,
        reason=reason,
        label=f"HolmesGPT — {REASON_LABELS[reason]}",
    )


def virtual_event_fields(playbook_id: str) -> tuple[str, str]:
    """플레이북 id → 가상 알람의 (resourceType, alarmName). 모르는 id면 KeyError."""
    return VIRTUAL_EVENTS[playbook_id]


__all__ = [
    "DEFAULT_DICTIONARY",
    "DIAGNOSIS_CUES",
    "INVESTIGATION_REF_KIND",
    "KEYWORD_EXCLUSIONS",
    "KIND_KEYWORDS",
    "PLAYBOOK_ALARM_KINDS",
    "PLAYBOOK_SURFACE_TERMS",
    "REASON_LABELS",
    "ROUTE_HOLMESGPT",
    "ROUTE_PLAYBOOK",
    "SymptomDictionary",
    "SymptomRoute",
    "VIRTUAL_EVENTS",
    "has_diagnosis_cue",
    "route_symptom",
    "symptom_candidates",
    "virtual_event_fields",
]
