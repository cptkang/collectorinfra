"""data_query 처리기의 DB 대상 정책 (plans/166 O4 · 종전 정의처 `subagents`).

분류(`subagents.classify_dbs`)가 낸 후보 DB 목록을 이번 턴 신호로 다듬는 결정적 판정을 모은다.

- 위치 힌트 고정(`_apply_turn_hint_pinning`) · 직전 턴 DB 승계(`_apply_db_succession`) ·
  존 선택 반영(`_apply_selection_to_zone_groups`) · 대상 정규화(`normalize_targets`)
- 존 역질문 후단 게이트(`_zone_clarification_or_none_task`) · 지시어 서버 주입
- 명시 소스 안내 · 사용률 소스 가드(D-308 ⑨) · 비활성 소스 노트

호출부(`subagents.run_data_query_pipeline`)와 조기 판정(`_zone_clarification_before_classify`)은
테스트가 `subagents`에서 바꿔 끼우는 이름(`_zone_clarification_or_none_task` 등)을 부르므로
`subagents`에 남는다(D-337 ②). 여기 이름은 `subagents`가 모두 재노출한다.
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from typing import Any

from src.config import AppConfig
from src.domain.knowledge_assets import mentions_utilization, utilization_kind
from src.orchestration.db_access import authorize_targets
from src.orchestration.process_query import (
    _is_demonstrative_value,
    resolve_investigation_targets,
)
from src.routing.capability_ownership import _corrected_note
from src.routing.db_authz import authorized_db_ids
from src.routing.location_hints import (
    inactive_hinted_sources,
    pin_targets_to_hints,
    strip_location_terms,
)
from src.routing.registry import get_registry
from src.routing.source_hints import (
    KIND_DB,
    KIND_NON_DB,
    SourceMention,
    is_mention_active,
    mention_in_text,
    mentions_for_task,
    resolve_source_mentions,
    source_notice_text,
    utilization_notice_text,
)
from src.utils.prior_dependency import NOTE_SOURCE_UNAVAILABLE
from src.utils.query_gen_common import (
    HOST_IDENTIFIER_FIELDS as _HOST_FIELDS,  # 종전 경유처 process_query._HOST_FIELDS와 같은 객체
)
from src.utils.query_gen_common import (
    ZONE_CLARIFY_OPTIONS,
    build_zone_clarification,
    has_host_identifier_filter,
    refers_to_demonstrative_server,
    term_in_text,
)
from src.utils.task_result_keys import DEGRADED_KEY

# 로그 이름은 이동 전 그대로 둔다(로그 필터·수집 규칙 불변 — plans/166 Move-only).
logger = logging.getLogger("src.orchestration.subagents")

# 이번 턴 질의에서 "새 위치/DB 신호"로 인정할 키워드 (M2 — DB 승계 차단 조건).
# 사용자가 이번 턴에 아래 신호를 명시하면 직전 DB를 승계하지 않고 분류 결과를 따른다.
# 정본은 `config/db_registry.yaml`(locations + environment_terms + 제품/DB signal_terms)
# — Plan 67 R2, 사본 금지. D-004 경계: 의도 분류가 아니라 승계 차단 판정에만 쓴다.
_LOCATION_DB_SIGNALS = get_registry().new_db_signal_terms()


def _has_new_location_db_signal(text: str) -> bool:
    """이번 턴 질의에 새 위치/DB 신호가 있는지 표면 검사한다 (M2 — 승계 차단 조건).

    사용자가 이번 턴에 위치(김포/여의도 등)·DB명(polestar 등)·환경을 명시하면
    직전 DB를 승계하지 않고 이번 분류 결과를 따라야 한다(리스크 표: 새 의도를 덮어쓰지 않음).

    Args:
        text: 이번 턴 sub_query(또는 user_query)

    Returns:
        새 위치/DB 신호가 있으면 True
    """
    if not text:
        return False
    return any(term_in_text(sig.lower(), text.lower()) for sig in _LOCATION_DB_SIGNALS)


#: 존 선택이 결과를 바꾸는 agent (G-3 확정 2026-09-16).
#: 존 그룹별로 다른 DB 를 조회하는 SQL 계열만 넣는다 — 목록을 넓히기 전에 그 agent 가
#: `targets` 로 폴스타 존을 받는지 먼저 확인할 것(게이트 후단이 그 조건을 다시 검사한다).
_ZONE_SCOPED_AGENTS: frozenset[str] = frozenset({"data_query", "alarm_query"})


def _zone_clarification_or_none_task(
    task: dict[str, Any],
    isolated: dict[str, Any],
    targets: list[dict[str, Any]],
    *,
    db_pinned: bool,
    db_succeeded: bool,
    app_config: AppConfig,
) -> dict[str, Any] | None:
    """존 역질문 후단 게이트 판정 (D-143 후속2 — 텍스트 경로 파이프라인 내 결정적 게이트).

    라우트 pre-gate(표면어 "서버"+전량 조회 필수)가 놓치는 형태 — 위치어 없는 존 단위
    조회("OS 버전 확인하시오" 등)가 LLM 임의 팬아웃(전 존/임의 존)으로 흐르는 것을,
    파싱·분류가 끝난 시점의 실제 신호로 판정해 존 선택 역질문으로 전환한다.
    §4.2 비발동 목록을 결정적 조건으로 옮긴 것: 서버명 지목·승계 가능·존 무관 질의는
    비발동, 비대화 채널(zone_clarification_allowed=False)은 항상 비발동.

    Args:
        task: 현재 TaskSpec
        isolated: 격리 입력(zone_clarification_allowed/parsed_requirements 포함)
        targets: 분류·핀·승계가 끝난 대상 DB 목록
        db_pinned: 이번 턴 위치 힌트 결정적 고정 여부
        db_succeeded: 직전 턴 DB 승계 여부
        app_config: 앱 설정

    Returns:
        발동 시 clarification 페이로드, 비발동이면 None
    """
    # 채널 게이트: 대화형 텍스트 라우트만 허용(§4.3-3 — 배치·평가·API 직접 호출 보호)
    if not isolated.get("zone_clarification_allowed"):
        return None
    # 존 스코프가 결과를 바꾸는 agent 만 (G-3 확정 2026-09-16 — 종전 data_query 전용에서 확대).
    #
    # 알람 조회도 존 단위로 데이터가 갈리므로 "어느 존이냐"가 결과를 바꾼다. 종전에는
    # `alarm_query` 가 게이트를 건너뛰어 LLM 임의 팬아웃(3개 존 전체)으로 흘렀다 —
    # run 20260915-131903 실측: 유효 280턴 중 66턴(24%)이 역질문 없이 3-DB 팬아웃이고
    # 팬아웃 턴 p50 131.5초 vs 단일 DB 59.9초(2.2배)다.
    # **열거로 좁게 유지한다** — process_query(폴스타 REST)·direct_response 등은 SQL 존
    # 스코프 개념이 달라 같은 역질문이 성립하지 않는다.
    if task.get("agent", "data_query") not in _ZONE_SCOPED_AGENTS:
        return None
    # **복합 계획 제외는 풀었다**(G-3). 종전 주석의 우려 두 가지는 아래처럼 처리한다:
    #   ① "중간 task 역질문은 UX 어색" → 한 턴의 task 들은 같은 원문·같은 존 신호를 보므로
    #      **같은 페이로드**를 만든다. `result_aggregator` 가 그중 하나만 취해 턴당 1회로
    #      묶고 나머지 task 결과를 버린다(`_zone_clarification_from_tasks`).
    #   ② "전역 판정 부정확" → 역질문 턴은 `target_db_ids` 를 남기지 않아(아래 반환부)
    #      `_collect_db_promotion` 이 빈 dict 를 돌려주므로 임의 분류 결과가 다음 턴
    #      승계로 새지 않는다. 복합 경로도 db 승격을 붙이지 않는다.
    # 위치 힌트 고정·승계가 발동했으면 존은 이미 결정적(§4.2 승계 우선)
    if db_pinned or db_succeeded:
        return None
    # 첫 턴 한정: 직전 턴 DB가 있으면 승계 계열이 처리(분류가 직전 DB 1개로 수렴해
    # db_succeeded=False로 남는 경우 포함 — previous_db_ids 존재 자체를 비발동 조건으로)
    ctx = isolated.get("conversation_context") or {}
    if ctx.get("previous_db_ids"):
        return None
    # 이번 턴 원문·sub_query에 위치/DB 신호가 있으면 비발동(D-065 결정적 보강이 처리)
    original_query = isolated.get("original_user_query") or isolated.get("user_query", "")
    if _has_new_location_db_signal(original_query) or _has_new_location_db_signal(
        task.get("sub_query", "")
    ):
        return None
    parsed = isolated.get("parsed_requirements") or {}
    # 서버명 지목 질의는 존이 결과에 영향 없음(§4.2 ⓐ) — hostname으로 어차피 특정됨
    if has_host_identifier_filter(parsed):
        return None
    # 조회 대상 필드가 파싱되지 않은 질의(잡담성 오분류 등)는 비발동 — 과잉 역질문 방지
    if not parsed.get("query_targets"):
        return None
    # 대상이 전부 폴스타 존일 때만(비폴스타 대상은 존 선택이 무의미)
    polestar_ids = app_config.get_polestar_db_ids() or set()
    target_ids = [t.get("db_id") for t in targets if t.get("db_id")]
    if not target_ids or not all(d in polestar_ids for d in target_ids):
        return None
    # 선택지는 사용자 조회 권한(D-232)으로 거른다 — 3단 라우터·라우트 사전 게이트와 같은 규칙
    # (plans/116 §10.3). 빈 목록을 넘기면 build_zone_clarification이 "제한 없음"으로 읽으므로
    # 권한 내 존이 없으면 묻지 않는다(뒤이은 인가 필터가 사유를 알린다).
    zone_ids = authorized_db_ids(
        app_config.multi_db.get_active_db_ids() or [o["db_id"] for o in ZONE_CLARIFY_OPTIONS],
        isolated.get("allowed_db_ids"), isolated.get("user_role"),
    )
    if not zone_ids:
        return None
    payload = build_zone_clarification(
        zone_ids, original_query,
        # 존 그룹 상호배타(D-143 후속3) — 라우트 pre-gate와 동일 UI 규칙
        group_exclusive=bool(
            app_config.multi_db.zone_group_exclusive
        ),
    )
    if payload:
        logger.info(
            "존 역질문 후단 게이트 발동(D-143 후속2): 위치어·서버 식별·승계 신호 없음 — "
            "LLM 임의 팬아웃(%s) 대신 존 선택 요청", target_ids,
        )
    return payload


def _zone_clarification_result(zone_q: dict[str, Any]) -> dict[str, Any]:
    """존 역질문 task 결과 — 조기 판정·후단 게이트가 같은 모양을 낸다."""
    return {
        "final_response": zone_q["question"],
        "zone_clarification": zone_q,
        "source": [],
        # target_db_ids를 의도적으로 남기지 않는다 — result_aggregator의 DB 승격
        # (_collect_db_promotion)이 임의 분류 결과를 previous_db_ids로 체크포인터에
        # 남겨 재개·후속 턴 승계를 오염시키는 것을 차단(요청 스코프 원칙).
    }


def _refers_to_specific_server(text: str) -> bool:
    """질의가 지시어로 특정 서버 하나를 가리키는지 판정한다 (전체 조회 방지 게이트).

    "전체/모든/모두" 같은 전역 조회 신호가 있으면 False(서버 스코프 강제 금지).
    "해당/그/이/저/위/직전 … 서버/장비/노드/…" 같은 지시어 서버 참조가 있으면 True.

    Args:
        text: 판정 대상 질의 텍스트(보통 original_query)

    Returns:
        지시어로 특정 서버를 지목하면 True
    """
    # 구현은 utils.query_gen_common으로 이동(D-153 후속1 단일 출처 — intent_planner
    # 맥락 주입 게이트와 공유) — 동작 동일.
    return refers_to_demonstrative_server(text)


def _inject_demonstrative_hostname(isolated: dict[str, Any]) -> dict[str, Any]:
    """지시어("해당 서버") data/alarm 조회에 직전 서버 hostname을 filter_conditions로 주입한다.

    query_generator는 parsed_requirements(original_query + filter_conditions)로 SQL을
    생성하며 intent_planner가 확장한 sub_query(state.user_query)는 프롬프트에 사용하지 않는다.
    따라서 지시어 후속 질의는 filter_conditions가 비어(D-055) 서버 필터 없이 전체 조회
    (전체 서버/전체 알람)로 빠진다. concrete "webdb01 서버 알람"과 동일하게 filter에 hostname을
    채워, 이미 검증된 filter_conditions.hostname 경로로 서버 필터를 강제한다(D-056 후속).

    주입 조건(모두 충족):
    - 이번 턴 filter_conditions에 서버 식별 필터가 없음(concrete 질의면 그대로 둠).
    - original_query가 지시어로 특정 서버를 가리킴(전체 조회 신호 없음).
    - 직전 턴 서버(previous_entities)에서 지시어가 아닌 실제 서버가 **정확히 1대** 해소됨.
      여러 대(「그 서버들」)면 첫 1대로 좁히지 않고 주입을 생략한다 — 노드의 지시어 스코프
      (`prompt_blocks.demonstrative_entity_scope`)가 전체를 HAVING으로 승계한다(plans/116 §10.3).

    필드는 해소된 식별자 종류를 따른다 — hostname이 있으면 hostname, 서버명뿐이면 name.
    서버명을 hostname 필드로 붙이면 name≠hostname 서버(D-046)가 0건이 된다.

    Args:
        isolated: subagent 격리 입력(parsed_requirements/conversation_context 포함)

    Returns:
        (필요 시) hostname 필터가 추가된 parsed_requirements 사본, 아니면 원본 참조
    """
    parsed = isolated.get("parsed_requirements") or {}
    filters = parsed.get("filter_conditions") or []
    if any(
        isinstance(c, dict) and str(c.get("field", "")).lower() in _HOST_FIELDS
        for c in filters
    ):
        return parsed  # 이미 서버 식별 필터 있음(concrete 질의)
    if not _refers_to_specific_server(str(parsed.get("original_query", ""))):
        return parsed  # 특정 서버 지목 아님(전체 조회 등) — 스코프 강제 금지
    resolution = resolve_investigation_targets(isolated)
    if not resolution.resolved:
        return parsed  # 직전 실제 서버 미해소
    if len(resolution.targets) > 1:
        logger.info(
            "지시어 대상 %d대 — 단일 필터 주입 생략(노드 지시어 스코프가 승계)",
            len(resolution.targets),
        )
        return parsed
    target = resolution.targets[0]
    field, value = (
        ("hostname", target.hostname) if target.hostname
        else ("name", target.server_name) if target.server_name
        else ("hostname", target.ip)
    )
    if not value or _is_demonstrative_value(value):
        return parsed
    new_parsed = dict(parsed)
    new_parsed["filter_conditions"] = [
        *filters,
        {"field": field, "op": "=", "value": value},
    ]
    logger.info(
        "data_query 지시어 서버 해소: %s=%s 를 filter_conditions에 주입(전체 조회 방지)",
        field, value,
    )
    return new_parsed


# 핀 대체 target의 정제 질의 — 3단 라우터(plans/113 F-1)가 같은 규칙을 쓰도록 routing 계층으로
# 옮겼다(사본 금지). 기존 이름은 소비처·테스트를 위해 유지한다.
_strip_location_terms = strip_location_terms


def _apply_turn_hint_pinning(
    targets: list[dict[str, Any]],
    isolated: dict[str, Any],
    sub_query: str,
    active_db_ids: list[str],
    *,
    zone_group_exclusive: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """이번 턴 원문 위치 힌트(target_db_hints)로 대상 DB 집합을 결정적으로 고정한다.

    DB 선택 우선순위 "① 이번 턴 명시 위치 > ② db_ids 고정 > ③ 승계 > ④ fan-out"에서
    ①의 판정이 종래 LLM 산출물(sub_query→classify_dbs)에 의존해, LLM이 직전 턴 위치를
    병합해 sub_query를 오염시키면("은행존 알람"→"김포 은행 공동존…") 오라우팅됐다
    (2026-07-16 실측). input_parser가 이번 턴 **원문**에서 결정적으로 추출한
    parsed_requirements.target_db_hints를 폼필과 동일한 해소 로직(resolve_priority_db_ids,
    D-065 계열)으로 DB 집합에 고정한다. classify_dbs 결과는 sub_query_context(위치 제거
    정제 질의) 재사용을 위해 유지하고 **DB 집합 결정권만** 이 함수가 갖는다.

    판정은 3단 라우터와 **같은 함수**(`routing.location_hints.pin_targets_to_hints` — plans/113
    F-2 · D-066 대칭)다. 존 그룹이 없는 DB의 분류 결과는 보존한다(종전 전량 탈락 교정).

    복합 계획(is_composite)은 종전에 고정을 통째로 껐다(전역 힌트가 task별 위치를 덮으므로 —
    그 결과 task마다 LLM 분류가 DB를 골라 "공동존"이 김포/여의도로 갈라졌다, plans/113 A-3).
    이제는 **task 단위로 고정**한다 — task 질의가 원문 힌트 중 일부만 가리키면 그 부분집합,
    가리키지 않으면 원문 힌트 전체다(`task_hint_scope`). 원문에 없는 위치어는 task 질의에
    있어도 무시한다(위 2026-07-16 방어 유지). 단일 task(1단 도구 호출 포함)는 종전대로 원문 힌트
    전체다 — 재작성 질의가 위치어 하나를 빠뜨리면 조용히 좁혀지므로(누락) 과포함 쪽을 택한다.

    Args:
        targets: classify_dbs가 반환한 후보 목록
        isolated: subagent 격리 입력(parsed_requirements/is_composite 포함)
        sub_query: 이번 task 질의(합성 target의 정제 질의 원료 · 복합 계획의 task 범위 근거)
        active_db_ids: 활성 DB 목록
        zone_group_exclusive: 존 그룹 상호배타 설정(호출부가 설정에서 넘긴다 · 기본 False는
            종전 동작 — 두 존 그룹 해소도 고정)

    Returns:
        (적용된 targets, 핀 적용 여부)
    """
    parsed = isolated.get("parsed_requirements") or {}
    hints = parsed.get("target_db_hints") or []
    return pin_targets_to_hints(
        targets, hints if isinstance(hints, list) else [], active_db_ids,
        fill_query=sub_query,
        task_query=sub_query if isolated.get("is_composite") else None,
        zone_group_exclusive=zone_group_exclusive,
    )


def _multiple_systems_active(app_config: AppConfig) -> bool:
    """활성 DB의 소유 시스템(`registry.system_of` · None 제외)이 둘 이상인가(§12.6 ①)."""
    return len(get_registry().systems_of(app_config.multi_db.get_active_db_ids())) >= 2


def _apply_selection_to_zone_groups(
    targets: list[dict[str, Any]], selected_db_ids: list[str], sub_query: str,
) -> list[dict[str, Any]]:
    """분류 대상 중 존 그룹 DB만 사용자가 고른 존으로 바꾼다(plans/121 TP-1.2).

    존 없는 시스템 DB(자산 DB 등)는 그대로 둔다 — 복원된 교차 시스템 task가 폴스타 존으로 끌려가지
    않게 한다. 대상에 존 그룹 DB가 없으면 입력 그대로다.
    """
    reg = get_registry()
    kept = [t for t in targets if reg.zone_group_of(str(t.get("db_id") or "")) is None]
    if len(kept) == len(targets):
        return targets
    return normalize_targets(selected_db_ids, sub_query) + kept


#: 명시 소스 안내 단락의 사유 코드(plans/132 · 재계획이 종결로 본다 — N-3).
REASON_SOURCE_INACTIVE = "source_inactive"
REASON_SOURCE_UNSUPPORTED = "source_unsupported"
#: 사용률을 갖지 않은 소스에 사용률을 물어 조회하지 않은 결과의 사유 코드(D-308 ⑨ · plans/132).
REASON_SOURCE_NOT_CANONICAL = "source_not_canonical"
#: 사용률의 답변 영역 코드(레지스트리 `capabilities`) — 이 영역 소유 시스템이 사용률 정본이다.
_UTILIZATION_CAPABILITY = "server_usage"


def _explicit_source_notice(
    sub_query: str, isolated: dict[str, Any], app_config: AppConfig,
) -> dict[str, Any] | None:
    """사용자가 이름으로 지목한 소스를 이 SQL 처리기가 조회할 수 없으면 안내 결과(plans/132 G-1).

    task 질의에 이름이 남은 지목만 본다. 그중 **활성 DB 시스템**이 하나라도 있으면 None(종전 경로 —
    비활성 지목은 아래 TP-1.11a 노트가 단다). 없으면 조회하지 않고 안내만 한다(「안내만」 — 다른
    소스로 대신 답하지 않는다). 2단은 계획 출구(`conditional_agents.apply_explicit_sources`)가 먼저
    바꾸므로 여기는 1단(부가 경로 — 비SQL 처리기 비노출 · G-5)·재계획 후속의 가드다. 지목한 비DB
    시스템이 활성인데 SQL 처리기로 왔으면 「이 조회 경로에서 처리하지 않음」으로 안내한다.
    """
    hints = (isolated.get("parsed_requirements") or {}).get("target_db_hints")
    mentions = resolve_source_mentions(hints if isinstance(hints, list) else [])
    scoped = [m for m in mentions if mention_in_text(m, sub_query)]
    if not scoped:
        return None
    from src.orchestration.conditional_agents import active_conditional_systems  # 지연 — 순환 방지

    systems = active_conditional_systems(app_config)
    active_db_ids = app_config.multi_db.get_active_db_ids()
    if any(
        m.kind == KIND_DB
        and is_mention_active(m, active_db_ids=active_db_ids, active_non_db=systems)
        for m in scoped
    ):
        return None
    unsupported = any(m.kind == KIND_NON_DB and m.system in systems for m in scoped)
    reason = REASON_SOURCE_UNSUPPORTED if unsupported else REASON_SOURCE_INACTIVE
    logger.info("data_query 명시 소스 안내 단락(plans/132 G-1): 지목=%s 사유=%s",
                [m.system for m in scoped], reason)
    return {
        "final_response": source_notice_text(
            [m.hint for m in scoped], unsupported_path=unsupported),
        DEGRADED_KEY: reason,
        "query_results": [],
    }


def _utilization_source_guard(
    task: dict[str, Any], sub_query: str, isolated: dict[str, Any],
    targets: list[dict[str, Any]], app_config: AppConfig,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    """사용률 task 대상에서 사용률 정본이 아닌 DB 시스템을 결정적으로 뺀다(D-308 ⑨ · plans/132).

    플래그를 두지 않는다 — D-308 ⑨ 확정 규칙의 구현이고, 사용률 비소유 DB가 없는 배포(관측
    DB 전용)는 판정이 늘 불성립이라 종전과 바이트 동일하다. 표 선별 LLM이 비소유 DB의 수집
    이력 표로 사용률을 지어내는 실패(ITAM-114)를 LLM 출력과 무관하게 막는다.

    판정 텍스트는 task 질의다(복합 계획의 사용률 아닌 task는 건드리지 않는다). 비소유 = 선언된
    소유 시스템(`system_of`)이 있고 사용률 영역(`server_usage`) 소유자가 아닌 DB — 선언 없는 DB는
    판정 밖이다.

    - 비소유 시스템을 사용자가 이름으로 지목했고(task 귀속 지목) 소유 시스템은 지목하지 않았으면
      조회하지 않고 안내만 한다(다른 소스로 대신 답하지 않는다 — G-1). 대상 목록과 무관하게
      판정한다 — 상류(소유 제한 등)가 지목된 DB를 먼저 뺐어도 안내만이다.
    - 그 밖(분류·승계가 끌어온 경우)은 비소유 DB만 뺀다. 다 빠지면 안내만 한다. 교정 노트는
      인가 뒤 확정 대상 기준으로 호출부가 만든다(`_utilization_correction_note` — D-264).
    - 사용률 소유 시스템이 레지스트리에 없거나 활성 소유 DB가 없으면 아무것도 하지 않는다(로그만).

    Returns:
        (대상 목록, 안내 결과 또는 None, 뺀 비소유 대상 목록)
    """
    if not mentions_utilization(sub_query):
        return targets, None, []
    reg = get_registry()
    owners = set(reg.capability_owners(_UTILIZATION_CAPABILITY))

    def _non_owner_system(system: str | None) -> bool:
        return system is not None and system not in owners

    non_owner = [t for t in targets if _non_owner_system(reg.system_of(str(t.get("db_id") or "")))]
    single = not isolated.get("is_composite")
    hints = (isolated.get("parsed_requirements") or {}).get("target_db_hints")
    mentions = [m for m in mentions_for_task(
        resolve_source_mentions(hints if isinstance(hints, list) else []), sub_query,
        single_task=single,
    ) if m.kind == KIND_DB]
    named = [m for m in mentions
             if any(_non_owner_system(reg.system_of(d)) for d in m.db_ids)]
    if not non_owner and not named:
        return targets, None, []
    active_owner = [
        d for d in app_config.multi_db.get_active_db_ids() if reg.system_of(d) in owners
    ]
    if not active_owner:
        logger.info("사용률 소스 가드 미적용(D-308): 활성 소유 DB 없음 (task=%s, targets=%s)",
                    task.get("task_id"), [t.get("db_id") for t in targets])
        return targets, None, []
    kept = [t for t in targets if t not in non_owner]
    if (named and not _owner_named(mentions, owners, isolated, sub_query, single)) or not kept:
        logger.info("사용률 소스 가드 — 안내만(D-308): task=%s 지목=%s 대상=%s",
                    task.get("task_id"), [m.system for m in named],
                    [t.get("db_id") for t in targets])
        return [], {
            "final_response": utilization_notice_text(
                [m.hint for m in named], kind=utilization_kind(sub_query)),
            DEGRADED_KEY: REASON_SOURCE_NOT_CANONICAL,
            "query_results": [],
        }, []
    if non_owner:
        logger.info("사용률 소스 가드 — 비소유 DB 제외(D-308): task=%s %s → %s",
                    task.get("task_id"), [t.get("db_id") for t in non_owner],
                    [t.get("db_id") for t in kept])
    return kept, None, non_owner


def _owner_named(
    mentions: list[SourceMention], owners: set[str], isolated: dict[str, Any],
    sub_query: str, single: bool,
) -> bool:
    """사용률 소유 시스템도 지목했는가 — 힌트 지목 ∪ 원문의 제품·DB 신호 표면어 스캔.

    소유 시스템의 제품명은 입력 파서의 결정적 소스 보강 대상이 아니라 힌트에 없을 수 있다. 스캔
    재료는 `db_scope.names_only_zoneless_sources`와 같은 레지스트리 접근자다(리터럴 0). 단일 task는
    원문, 복합 계획은 task 질의를 본다(지목 귀속 규칙과 같다).
    """
    if any(m.system in owners for m in mentions):
        return True
    reg = get_registry()
    text = (str(isolated.get("original_user_query") or "") or sub_query) if single else sub_query
    signals = [t for t in (*reg.product_terms(), *reg.db_signal_terms()) if term_in_text(t, text)]
    return any(m.kind == KIND_DB and m.system in owners for m in resolve_source_mentions(signals))


def _utilization_correction_note(
    task: dict[str, Any], removed: list[dict[str, Any]], targets: list[dict[str, Any]],
    isolated: dict[str, Any], app_config: AppConfig,
) -> dict[str, Any] | None:
    """사용률 가드가 뺀 비소유 DB의 교정 노트 — 인가 뒤 확정 대상 기준(D-264 ② · TP-1.6 선례).

    뺀 DB 중 인가 밖 DB는 이름을 싣지 않는다(어차피 조회되지 않았다). 남는 것이 없으면 None.
    """
    reg = get_registry()
    shown = authorize_targets(removed, isolated) or []
    if not shown or not targets:
        return None
    owners = reg.capability_owners(_UTILIZATION_CAPABILITY)
    owner_system = next(
        (reg.system_of(d) for d in app_config.multi_db.get_active_db_ids()
         if reg.system_of(d) in owners),
        None,
    ) or owners[0]
    return _corrected_note(
        reg, _UTILIZATION_CAPABILITY, owner_system,
        [str(t.get("db_id") or "") for t in shown],
        [str(t.get("db_id") or "") for t in targets],
        task.get("task_id"),
    )


def _source_unavailable_notes(
    isolated: dict[str, Any], active_db_ids: list[str],
    active_non_db: Collection[str] = (),
) -> list[dict[str, Any]]:
    """원문 힌트가 등록·비활성 소스만 가리키면 사유 노트를 만든다(plans/121 TP-1.11a · N-11).

    대상 집합은 바꾸지 않는다 — 분류가 고른 활성 DB로 답하되, 요청한 소스를 조회하지 않았다는
    사실을 숨기지 않는다. 문구에는 사용자가 쓴 표현만 싣는다(레지스트리 표시명 비노출 — D-264).
    턴 단위 사실이라 `task_id`를 두지 않는다 — 여러 task에서 같은 노트가 나와도 집계기가
    한 번만 싣는다. 비활성 **비DB 시스템**(제니퍼·문서·Prometheus — plans/132 N-1)을 함께 지목한
    힌트도 같은 노트를 단다(활성 소스 지목과 섞여 조회가 진행되는 턴).
    """
    hints = (isolated.get("parsed_requirements") or {}).get("target_db_hints") or []
    clean = hints if isinstance(hints, list) else []
    inactive = list(inactive_hinted_sources(clean, active_db_ids))
    for mention in resolve_source_mentions(clean):
        if (
            mention.kind == KIND_NON_DB and mention.system not in active_non_db
            and mention.hint not in inactive
        ):
            inactive.append(mention.hint)
    return [
        {
            "kind": NOTE_SOURCE_UNAVAILABLE,
            "task_id": None,
            "reason": "source_inactive",
            "detail": (
                f"요청하신 「{hint}」 데이터 소스는 현재 활성화되어 있지 않아 조회하지 않았습니다."
            ),
        }
        for hint in inactive
    ]


def _apply_db_succession(
    targets: list[dict[str, Any]],
    sub_query: str,
    conversation_context: dict[str, Any] | None,
    active_db_ids: list[str],
    *,
    inherit_all: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """이전 턴 DB를 우선 후보로 승계한다 (M2, Plan 50 §3.2).

    `inherit_all`(D-206 — 존 동시 조회 개방 시): 직전 DB 집합 **전체**를 승계한다. 은행존+공동존을
    함께 고른 스레드가 다음 턴에 첫 DB 하나로 좁혀지면 "선택된 폴스타로 계속"이 거짓이 된다.
    상호배타(플래그 on)에서는 종전대로 첫 DB 1개 — 팬아웃 결과의 b0+gp 조합이 한 run에 섞이지 않게.

    우선순위: ① 이번 턴 명시 위치/DB > ② mapped_db_ids(호출 전 db_ids 고정) >
    ③ previous_db_ids(멀티턴 승계) > ④ 전체 후보 fan-out.

    본 함수는 ①(이번 턴 신호)이 없고 직전 DB가 있을 때만 승계를 적용한다.
    승계 조건:
      - 이번 턴 sub_query에 새 위치/DB 신호가 **없음**.
      - conversation_context.previous_db_ids 가 **활성 DB 중 1개 이상** 존재.
    승계 시 직전 DB를 단일 우선 후보로 좁혀 fan-out(다중 폴스타 오선택)을 방지한다.

    Args:
        targets: classify_dbs가 반환한 target_databases 목록
        sub_query: 이번 턴 SQL 생성 입력 질의
        conversation_context: context_resolver 보존 맥락(없으면 첫 턴)
        active_db_ids: 활성 DB 목록(승계 후보 유효성 검사용)

    Returns:
        (적용된 targets, 승계 적용 여부)
    """
    if not conversation_context:
        return targets, False
    prev_db_ids = conversation_context.get("previous_db_ids") or []
    if not prev_db_ids:
        return targets, False

    # ① 이번 턴에 새 위치/DB 신호가 있으면 승계하지 않는다(사용자 새 의도 최우선).
    if _has_new_location_db_signal(sub_query):
        return targets, False

    # 직전 DB 중 현재 활성인 것만 후보로 인정.
    valid_prev = [d for d in prev_db_ids if not active_db_ids or d in active_db_ids]
    if not valid_prev:
        return targets, False

    # 이미 분류 결과가 직전 DB 1개로 수렴했으면 승계 불필요
    # (상호배타 규칙 — 개방이면 집합 비교로 판정).
    current_ids = [t.get("db_id") for t in targets]
    if not inherit_all and len(targets) == 1 and current_ids[0] in valid_prev:
        return targets, False
    succeed_ids = list(valid_prev) if inherit_all else [valid_prev[0]]
    # 분류 결과가 승계 집합과 이미 같으면(순서 무관) 승계 불필요.
    if inherit_all and set(current_ids) == set(succeed_ids):
        return targets, False

    # 승계: 직전 DB(상호배타면 우선순위 1개, 개방이면 전체)를 우선 후보로 사용.
    succeeded = [{
        "db_id": db_id,
        "relevance_score": 1.0,
        "sub_query_context": sub_query,
        "user_specified": False,
        "reason": f"이전 턴 DB 승계(멀티턴, previous_db_ids={valid_prev})",
    } for db_id in succeed_ids]
    logger.info(
        "data_query DB 승계 적용: %s (분류 결과 %s → 직전 DB 우선, 전체 승계=%s)",
        succeed_ids, current_ids, inherit_all,
    )
    return succeeded, True


def normalize_targets(targets: list[Any], sub_query: str) -> list[dict[str, Any]]:
    """db_ids(문자열 리스트) 또는 target dict 리스트를 target dict 형태로 정규화한다.

    Args:
        targets: 문자열 db_id 리스트 또는 target_databases dict 리스트
        sub_query: sub_query_context 기본값

    Returns:
        target_databases 형태 dict 리스트
    """
    normalized: list[dict[str, Any]] = []
    # 정제 질의(`sub_query_context` — 단일 DB SQL 생성 입력)는 위치어를 걷어낸다
    # (plans/123 W-5 코드분).
    # DB는 이미 정해졌다(선택·계획 고정). 위치어가 남으면 생성기·요약이 「김포」를 데이터 조건으로
    # 읽어 「구분할 수 있는 컬럼이 없다」고 답하거나 WHERE로 누출한다(run `20260923-103638` R2-09C).
    # `sub_query` 자체는 바꾸지 않는다(121 TP-11.2 · 113 F-2 위치어 보존 계약).
    context = strip_location_terms(sub_query) or sub_query
    for t in targets:
        if isinstance(t, str):
            normalized.append({
                "db_id": t,
                "relevance_score": 1.0,
                "sub_query_context": context,
                "user_specified": False,
                "reason": "필드 매핑 결과에서 식별된 DB",
            })
        elif isinstance(t, dict):
            normalized.append(t)
    return normalized
