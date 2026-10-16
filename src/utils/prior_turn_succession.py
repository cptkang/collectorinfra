"""직전 턴 대상 승계 판정 — 서버 식별 필터·지시어 판정 · 생략형 후속 턴(plans/120 PL-1) 보조 판정.

plans/166 N2(D-337)로 `src/utils/query_gen_common.py`에서 옮겼다(동작 불변 Move-only).
진입 함수 `elliptical_succession_filter`는 위치 표면어(`LOCATION_HINT_TERMS`·`term_in_text`)를
읽는데, 그 값 사본은 위치 표면어 단일 정의 가드(`tests/test_semantic_routing/
test_registry_config.py`의 허용 파일 목록)가 `query_gen_common.py`에 묶어 두므로 원 모듈에
남는다 — 이 모듈의 보조 판정을 호출한다. 옮긴 이름은 `query_gen_common`이 모두 재노출한다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

# 서버 식별자로 인정할 filter_conditions field (종전 canonical: process_query._HOST_FIELDS —
# 존 게이트가 routing 계층에서도 필요해 utils로 내림, process_query가 re-export).
HOST_IDENTIFIER_FIELDS: tuple[str, ...] = (
    "hostname", "host_name", "server_name", "name", "host", "device_name",
    "서버명", "서버이름", "장비명", "호스트명", "서버", "장비",
)
# 지시어(demonstrative) 접두/명사 — 실제 서버명이 아니라 직전 대상을 가리키는 표현.
DEMONSTRATIVE_PREFIXES: tuple[str, ...] = (
    "해당", "그", "이", "저", "위", "방금", "앞서", "직전", "이전",
)
DEMONSTRATIVE_NOUNS: tuple[str, ...] = (
    "서버", "장비", "호스트명", "호스트", "인스턴스", "노드", "머신", "시스템",
)


def is_demonstrative_identifier(value: object) -> bool:
    """서버 식별자 값이 실제 이름이 아니라 지시어/플레이스홀더인지 판정한다.

    (process_query._is_demonstrative_value의 단일 출처 이동분 — 동작 동일.)
    """
    if value is None:
        return True
    v = str(value).strip()
    if not v:
        return True
    compact = v.lower().replace(" ", "").replace("_", "").replace("-", "")
    if ("previous" in compact or compact.startswith("prev")) and (
        "server" in compact or "host" in compact
    ):
        return True
    body = v
    for noun in DEMONSTRATIVE_NOUNS:
        if body.endswith(noun):
            body = body[: -len(noun)].strip()
            break
    return body in DEMONSTRATIVE_PREFIXES


def looks_like_process_rows(rows: list[dict[Any, Any]] | None) -> bool:
    """결과 행이 프로세스 조회 결과인지 판별한다 (엔티티·조사 대상 오수집 방지용).

    프로세스 조회 결과 행은 `pid`를 갖는다(process_query._process_to_dict:
    {name, pid, user, cpu_pct, mem_pct, rss, args}). `name`은 프로세스명, `pid`는 서버가
    아니므로 이런 행에서 서버 식별 엔티티를 수집하면 안 된다. 서버 조회 결과 행에는 `pid`가 없다.

    (context_resolver._looks_like_process_rows의 단일 출처 이동분 — 동작 동일.
    utils로 내린 이유: 조사 대상 해소(prior_targets)가 application 계층을 import할 수 없다.)

    Args:
        rows: 결과 행 목록

    Returns:
        첫 행이 `pid` 키를 가지면 True(프로세스 행으로 간주)
    """
    if not rows:
        return False
    first = rows[0]
    if not isinstance(first, dict):
        return False
    keys = {str(k).lower() for k in first.keys()}
    return "pid" in keys


def refers_to_demonstrative_server(text: str) -> bool:
    """질의가 지시어("해당/그/위 … 서버")로 특정 서버를 가리키는지 판정한다.

    "전체/모든/모두" 전역 조회 신호가 있으면 False(서버 스코프 강제 금지).
    지시어 접두 + (선택 공백) + 서버 명사 인접 구문만 인정 — 단순 부분매칭은 단일 문자
    접두("이"/"그"/"위")가 "이상"/"그래서" 등에 오탐하므로 금지.
    (subagents._refers_to_specific_server의 단일 출처 이동분 — 동작 동일.)
    """
    if not text:
        return False
    if any(k in text for k in ("전체", "모든", "모두")):
        return False
    for p in DEMONSTRATIVE_PREFIXES:
        for n in DEMONSTRATIVE_NOUNS:
            if f"{p}{n}" in text or f"{p} {n}" in text:
                return True
    return False


def has_host_identifier_filter(parsed_requirements: dict[Any, Any] | None) -> bool:
    """이번 턴 filter_conditions에 실제 서버 식별자(지시어 제외)가 있는지 판정한다.

    존 역질문 비발동 조건 ⓐ(D-143 §4.2 — 서버명 지목 질의는 존이 결과에 영향 없음)의
    결정적 판정. 라우트 레벨 표면어 게이트는 이 정보가 없어 판정할 수 없었다(후속2 동기).
    """
    parsed = parsed_requirements or {}
    for cond in parsed.get("filter_conditions") or []:
        if not isinstance(cond, dict):
            continue
        if str(cond.get("field", "")).lower() in HOST_IDENTIFIER_FIELDS:
            value = cond.get("value")
            if value and not is_demonstrative_identifier(value):
                return True
    return False


# ── 생략형 후속 턴 승계 (plans/120 PL-1 · G-4 (a)) ────────────────────────────────
# 지시어("해당·그 서버") 없이 첨가 표지만 있는 후속 턴("네 그럼 메모리도 보여줘")은 종전에
# 승계되지 않아 전 서버를 조회했다(run 20260923-140539 G-01 t3). 직전 턴이 서버 1대로 좁혀져
# 있었을 때만 그 서버를 잇는다. 지시어 없는 턴에 엔티티 표본을 넣으면 대량 후속이 표본으로
# 축소되므로(2026-08-04 실측) 아래 조건을 **전부** 충족할 때만 발동하고, 응답에 고지를 싣는다.

#: parsed_requirements에 승계 사실을 남기는 키 — output_generator가 이 값으로 고지를 붙인다.
#: 2단은 `_build_output_state`가 parsed_requirements를 넘기므로 같은 키가 그대로 닿는다.
ELLIPTICAL_SUCCESSION_KEY = "elliptical_succession"
#: 담화 표지 — 앞 턴을 이어받는 말(낱말 단위 일치).
ELLIPTICAL_DISCOURSE_MARKERS: tuple[str, ...] = ("그럼", "그러면")
#: '도'로 끝나지만 첨가 조사가 아닌 명사 — 한자어 '-도(度)' 계열 등(오탐 방지).
_DO_ENDING_NOUNS: tuple[str, ...] = (
    "온도", "속도", "정도", "빈도", "용도", "한도", "각도", "강도", "밀도", "습도", "농도",
    "고도", "경도", "위도", "지도", "제도", "시도", "진도", "척도", "태도",
    "심각도", "중요도", "난이도", "만족도", "신뢰도", "정확도", "유사도", "포화도",
    "혼잡도", "가용도", "민감도", "활용도", "완성도", "기여도",
)
#: '도'로 끝나는 비첨가 형태 — 연결어미("-어도·-아도·-라도")와 부정 극어("아무도").
_DO_ENDING_NON_ADDITIVE: tuple[str, ...] = (
    "어도", "아도", "여도", "해도", "봐도", "줘도", "돼도", "되도", "워도", "져도",
    "라도", "래도", "아무도", "누구도", "어디도", "하나도", "조금도",
)
#: filter_conditions에서 새 식별자로 보는 IP 계열 field(서버 식별 field 밖).
_IP_IDENTIFIER_FIELDS: tuple[str, ...] = ("ip", "ip_address", "ipaddress")
#: hostname 종류 field — 나머지 서버 식별 field는 서버명 종류다(`prior_targets`와 같은 구분).
_HOSTNAME_KIND_FIELDS: frozenset[str] = frozenset({"hostname", "host_name", "호스트명"})
#: 원문의 새 식별자 표면형 — IPv4 · 구분자가 든 영문 이름(`app-01`) · 영문+숫자(`db01`).
#: 파서가 식별 필터로 못 뽑았을 때 직전 서버로 덮어쓰지 않기 위한 보강이다.
_NEW_IDENTIFIER_TOKEN = re.compile(
    r"(?<![0-9A-Za-z])(?:\d{1,3}(?:\.\d{1,3}){3}"
    r"|[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9][A-Za-z0-9_-]*"
    r"|[A-Za-z]+\d[A-Za-z0-9]*)"
)
_WORD_TOKEN = re.compile(r"[0-9A-Za-z가-힣]+")
#: 서버 집합을 고르거나 나누는 낱말(접두 일치) — 순위·목록·나머지 등. `top`은 소문자로 비교한다.
_SET_SELECTION_PREFIXES: tuple[str, ...] = (
    "상위", "하위", "top", "순위", "정렬", "목록", "리스트", "나머지", "다른",
)
_SET_SELECTION_WORDS: frozenset[str] = frozenset({"각", "각각"})
#: '~별' 앞말 — 이 앞말이면 한 서버 안의 시간 구간이라 집합 분할이 아니다("일별도 보여줘").
_TIME_BUCKET_STEMS: frozenset[str] = frozenset({
    "일", "일자", "날짜", "요일", "주", "월", "년", "연", "연도", "분기", "시간", "분",
})
_BY_SUFFIX = re.compile(r"(.+?)별")
#: 한 서버 안의 흐름으로 읽히는 집계(파서 `aggregation` 값). 나머지(top_n·group_by 등)는
#: 서버 집합에 대한 연산이다.
_PER_ENTITY_AGGREGATIONS: frozenset[str] = frozenset({"time_series"})


def selects_server_set(text: str, parsed: Mapping[str, Any]) -> bool:
    """이번 턴이 서버 집합을 스스로 지목하거나 고르는가 — 그러면 생략형이 아니다(ⓕ·ⓖ).

    ⓕ 서버 계열 명사(`DEMONSTRATIVE_NOUNS`)가 원문에 있다 — 생략형은 대상 명사 자체가 빠진
       턴이다("그럼 메모리도 보여줘"). "다른 서버도"·"WAS 서버도"는 새 집합을 지목한다.
    ⓖ 선택·집계 신호 — 식별 필드 밖 `filter_conditions`(임계·OS 등) · `limit` · 시계열 밖
       `aggregation` · 원문의 순위·집합어(상위·목록·나머지·~별·각 등).
    """
    if any(noun in text for noun in DEMONSTRATIVE_NOUNS):
        return True
    if parsed.get("limit") is not None:
        return True
    aggregation = parsed.get("aggregation")
    if aggregation and str(aggregation).strip().lower() not in _PER_ENTITY_AGGREGATIONS:
        return True
    for cond in parsed.get("filter_conditions") or []:
        if not isinstance(cond, dict):
            return True
        field = str(cond.get("field", "")).lower()
        if field in HOST_IDENTIFIER_FIELDS or field in _IP_IDENTIFIER_FIELDS:
            continue  # 식별 필터는 ⓑ가 본다(지시어 값은 선택이 아니다)
        return True
    for token in _WORD_TOKEN.findall(text):
        if token.lower().startswith(_SET_SELECTION_PREFIXES) or token in _SET_SELECTION_WORDS:
            return True
        by = _BY_SUFFIX.search(token)
        if by and by.group(1) not in _TIME_BUCKET_STEMS:
            return True
    return False


def has_additive_marker(text: str) -> bool:
    """첨가 표지 — 담화 표지("그럼"·"그러면") 또는 낱말에 붙은 첨가 조사 '도'("메모리도")."""
    for token in _WORD_TOKEN.findall(text):
        if token in ELLIPTICAL_DISCOURSE_MARKERS:
            return True
        if len(token) < 2 or not token.endswith("도"):
            continue
        if token[:-1].isdigit():  # "30도" — 수치 단위
            continue
        if token.endswith(_DO_ENDING_NOUNS) or token.endswith(_DO_ENDING_NON_ADDITIVE):
            continue
        return True
    return False


def has_new_identifier(text: str, parsed: Mapping[str, Any]) -> bool:
    """이번 턴이 새 대상을 지목했는가 — 서버·IP 식별 필터 또는 원문의 식별자 표면형."""
    if has_host_identifier_filter(dict(parsed)):
        return True
    for cond in parsed.get("filter_conditions") or []:
        if (
            isinstance(cond, dict)
            and str(cond.get("field", "")).lower() in _IP_IDENTIFIER_FIELDS
            and cond.get("value")
        ):
            return True
    return bool(_NEW_IDENTIFIER_TOKEN.search(text))


def single_previous_entity(
    conversation_context: Mapping[str, Any] | None,
) -> tuple[str, str] | None:
    """직전 턴이 서버 1대로 좁혀졌으면 (주입 field, 값), 아니면 None.

    `previous_entities_complete`(context_resolver)는 엔티티가 직전 턴 결과 **전체**를 담는다는
    표지다 — sticky 승계분이거나 행 상한 표본이면 발동하지 않는다. 해소 규칙은 지시어 경로
    (`prior_targets.resolve_targets` ③)와 같다 — 같은 종류의 값이 둘이면 서로 다른 서버이고,
    hostname·서버명 한 쌍은 한 서버의 두 표기다. 주입 field는 해소된 식별자 종류를 따른다 —
    hostname이 있으면 hostname, 서버명뿐이면 name(D-148 · D-046).
    """
    ctx = conversation_context or {}
    if not ctx.get("previous_entities_complete"):
        return None
    hostnames: list[str] = []
    names: list[str] = []
    for entity in ctx.get("previous_entities") or []:
        if not isinstance(entity, Mapping):
            continue
        field = str(entity.get("field", "")).strip().lower()
        if field not in HOST_IDENTIFIER_FIELDS:
            continue
        value = entity.get("value")
        if is_demonstrative_identifier(value):
            continue
        bucket = hostnames if field in _HOSTNAME_KIND_FIELDS else names
        text = str(value).strip()
        if text not in bucket:
            bucket.append(text)
    if len(hostnames) > 1 or len(names) > 1 or not (hostnames or names):
        return None
    return ("hostname", hostnames[0]) if hostnames else ("name", names[0])


def render_elliptical_succession_note(marker: Any) -> str:
    """생략형 승계 고지(침묵 승계 금지 · plans/120 PL-1). 승계가 없으면 빈 문자열."""
    if not isinstance(marker, Mapping) or not marker.get("value"):
        return ""
    return (
        f"**[직전 서버 기준]** 직전 서버 `{marker['value']}` 기준으로 조회했습니다 — "
        "전체 서버는 '전체 서버 …'로 다시 요청하세요."
    )


# 하위호환 별칭(D-337 ③ — plans/166 N2) — `query_gen_common`이 옛 이름으로 import한다.
# 다음 계획에서 `grep -rw <이름>` 0 확인 후 제거한다.
_selects_server_set = selects_server_set
_has_additive_marker = has_additive_marker
_has_new_identifier = has_new_identifier
_single_previous_entity = single_previous_entity
