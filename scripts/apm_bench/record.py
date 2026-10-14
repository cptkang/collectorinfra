"""턴 관측 레코드 계약 (plans/152 §3.4·§3.8·§3.10 · D-323).

드라이버(`driver`)가 턴마다 `TurnRecord`를 만들고, 판정(`judge`)이 `Verdict`를 붙이고,
위생(`hygiene`)이 치환·관문을 거쳐 `trace.jsonl` 한 줄로 쓴다. 개선 루프(`loop`)의 `--compare`·
`--replay`는 반출된 `trace.jsonl`을 이 모양으로 다시 읽는다.

**이 파일은 모듈 사이의 계약이다** — 칸을 바꾸려면 팀 리드에게 보고하고 같이 고친다.
값은 JSON 직렬화 가능한 것(문자열·수·불린·리스트·dict·None)만 담는다.

치환 전(드라이버 출력)에는 운영 이름 원값이 들어 있다 — 디스크에 쓰기 전 반드시 `hygiene`을 거친다.
"""

from __future__ import annotations

from typing import Any, TypedDict

#: 레코드 형식 버전 — 칸을 바꾸면 올린다.
RECORD_VERSION = 1

#: 게이트웨이 오류 코드 사본(`apm_gateway/apm_gateway/domain/errors.py` `ERROR_CODES` — 벤치는
#: 게이트웨이를 import 하지 않는다 · D-274). 정본과 같은지는 테스트가 파일 텍스트로 대조한다.
#: **벤치 안 유일한 사본이다**(plans/164 W2a) — `judge`·`audit_digest`·`safe_summary`는 여기서
#: import 한다.
GATEWAY_ERROR_CODES: frozenset[str] = frozenset({
    "not_configured", "invalid_argument", "instance_unresolved", "profile_ref_mismatch",
    "source_unavailable", "contract_violation", "apm_quota_exceeded", "apm_api_error",
    "rate_limited", "job_not_found", "job_not_ready",
})
#: 대상 해석 실패 코드(`GATEWAY_ERROR_CODES` 원소 — 판정기가 호출 단계를 가를 때 쓴다 ·
#: plans/164 §4.3-3).
INSTANCE_UNRESOLVED = "instance_unresolved"
#: 처리기가 직접 붙이는 실패 코드(`src/orchestration/apm_query.py` — 배치 결과에 대상 항목 없음).
HANDLER_ERROR_CODES: frozenset[str] = frozenset({"batch_missing"})
#: `view_failures[].code`가 받는 오류 코드(밖이면 None — 사유 원문의 이름 조각을 코드로 받지
#: 않는다).
ERROR_CODES: frozenset[str] = GATEWAY_ERROR_CODES | HANDLER_ERROR_CODES


class PlanTask(TypedDict, total=False):
    """분해가 낸 APM task 하나(관측 지점 ② 원출력 · ① 디스패치 task)."""

    task_id: str
    agent: str
    views: list[str]
    view_args: dict[str, Any]
    targets: list[Any]
    sources: list[str]
    areas: list[str]


class SanitizeChange(TypedDict, total=False):
    """정제(`sanitize_task_views` 등)가 바꾸거나 버린 항목."""

    task_id: str
    field: str  # views · view_args · targets · sources · agent
    before: Any
    after: Any
    reason: str


class Reselect(TypedDict, total=False):
    """보기 재선택(`_select_views`) — 관측 지점 ③ + 처리기 `meta.selection`."""

    happened: bool
    candidates: list[str]  # 입력 보기 후보
    parsed_views: list[str]  # LLM 원출력(정제 전)
    parsed_view_args: dict[str, Any]
    result_views: list[str]  # 정제 뒤 최종
    reason: str | None


class TargetText(TypedDict, total=False):
    """대상 해석 문자열 하나."""

    text: str
    kind: str  # instance · host · business · service · …
    resolved: bool
    candidates: list[str]  # 게이트웨이 후보 이름
    selected: list[str]
    searched: int  # 해석 근거 검색 수(처리기 `targets.texts[].searched` 건수 — 원문 미보관)
    failures: int  # 해석 실패 수(`targets.texts[].failures` 건수 — 원문은 이름이 섞인 자유 문장)


class Call(TypedDict, total=False):
    """게이트웨이 도구 호출 하나(처리기 provenance + 게이트웨이 감사 줄 대조)."""

    tool: str
    view: str | None  # provenance 보기 id
    hostname: str | None  # provenance 대상(치환 전 원값)
    reference: Any  # 앞 턴 행 지목(참조 보기)
    args: dict[str, Any]
    window: Any  # 봉투 window 그대로 — {start,end,minutes} · 비교는 {current,baseline}
    rows: int | None
    error: str | None  # 게이트웨이 오류 유형(QUOTA_EXCEEDED · timeout · apm_api_error …)
    elapsed_ms: int | None
    api_calls: int | None
    sources: list[str]


class Observed(TypedDict, total=False):
    """턴 하나에서 본 것 — 판정의 입력."""

    agents: list[str]  # plan_summary.tasks[].agent (정제 뒤)
    plan_raw: list[PlanTask]  # 정제 전 APM task(관측 지점 ②) — 없으면 []
    plan_final: list[PlanTask]  # 디스패치된 APM task(관측 지점 ①의 task)
    sanitize_changes: list[SanitizeChange]
    reselect: Reselect | None
    targets: list[TargetText]
    untargeted: bool  # 서버 미지정 → 상위 N 경로 진입
    time_resolution: dict[str, Any] | None  # done.time_resolution 그대로
    calls: list[Call]
    apm_source_scope: Any
    apm_source_clarification: Any
    answer_lines: list[str]  # 처리기 결과의 answer_lines(관측 지점 ①) — 응답 본문 파싱값이 아님
    disclosures: list[dict[str, Any]]  # {kind,text,source}
    # source · required_arg · time(시간 해석 되묻기) · zone(존 사전 게이트)
    #   · 그 밖 사전 게이트 원값 · None
    clarify_kind: str | None
    clarify_text: str | None
    refused: bool
    response: str
    response_len: int
    degraded_reason: str | None
    node_elapsed_ms: dict[str, Any]
    elapsed_ms: int | None
    apm_called: bool  # APM 도구 호출이 하나라도 관측됐는가(D-219 ③ 관측 0 판정)
    # 처리기 보기 호출 실패 `{view, code}`(code = 실패 사유의 오류 코드 · 코드 모양이 아니면 None) —
    # 처리기 결과 캡처가 있을 때만 둔다. 판정이 오류 감사 줄의 호출 단계를 가른다(plans/164 §4.3-3)
    view_failures: list[dict[str, Any]]
    error: str | None  # 턴 자체 실패(HTTP·SSE·타임아웃)


class Verdict(TypedDict, total=False):
    """판정 결과(§3.4 5축 · §3.5 분류)."""

    axes: dict[str, str]  # source·views·target·window·args → pass · fail · na
    axis_detail: dict[str, Any]  # 축별 근거(누락·초과 수 · 기대/관측)
    turn_pass: bool  # 5축 전부 통과(na 제외)
    expected_clarify: bool  # 되묻기가 정답인 턴(별도 집계)
    expected_refusal: bool
    categories: list[str]  # 분류 13종 중 해당 전부(첫째가 대표)
    fewshot_fixable: bool | None
    # 지목 위치 — "F1:apm.sql_stats:example#2" · "F1:apm.sql_stats:args.sort_by" · "F2" …
    locus: list[str]
    fewshot_copy: dict[str, Any] | None  # {view, example_index, args}
    # 판정 안 함 사유(e2_skip · aborted:<사유>) — 드라이버가 판정 전에 미리 적는다
    skipped: str | None
    unjudged: str | None  # 판정 불가 사유(turn_error:… · no_observation · placeholder_missing:…)


class TurnRecord(TypedDict, total=False):
    """`trace.jsonl` 한 줄."""

    v: int  # RECORD_VERSION
    scenario_id: str
    turn: int  # 0부터
    split: str
    covers: list[str]
    heavy: bool
    was_touch: bool
    sensitive: bool
    prompt_template: str  # 자리표 원문
    prompt: str  # 채운 프롬프트(치환 전 원값 — 반출본에서는 치환본)
    placeholders: dict[str, str]  # 자리표 → 값(반출본에서는 치환 값)
    expect: dict[str, Any]
    thread_id: str
    sent_at: str  # 턴 송신 시각 KST ISO(창 판정 앵커)
    observed: Observed
    verdict: Verdict
    probe: list[dict[str, Any]]  # P층 대조
    evidence: dict[str, Any]  # 근거 묶음(§3.8) — 실패 턴만 전체, 통과 턴은 요약
    # 진단 보조(드라이버) — plan_summary · source_auto_answer · llm_kinds · capture_kinds ·
    #   inherited_hosts(대상 승계 턴 — 앞 턴 인스턴스의 호스트 · 판정이 읽는다)
    extra: dict[str, Any]


__all__ = [
    "RECORD_VERSION",
    "ERROR_CODES",
    "GATEWAY_ERROR_CODES",
    "HANDLER_ERROR_CODES",
    "INSTANCE_UNRESOLVED",
    "PlanTask",
    "SanitizeChange",
    "Reselect",
    "TargetText",
    "Call",
    "Observed",
    "Verdict",
    "TurnRecord",
]
