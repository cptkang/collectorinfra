"""응답 고지(disclosure) — kind 어휘 정본과 턴 단위 조립 규칙 (plans/123 W-8·W-9).

**무엇인가.** 응답이 「전부를 본 결과」가 아닐 때(상한 도달 · 좁힌 범위 · 등록되지 않은 존 ·
단위 의심 · 생성기 고백 · 조건 변경 · 조회 실패) 코드가 결정적으로 붙이는 한 줄 고지다.
고지는 **문구와 함께 kind(코드)로도** 낸다 — 응답 계약의 구조 필드 `disclosures[]`
(`{kind, text, source}`)로 네 진입점(비스트림·스트림 × 텍스트·파일)에 같은 모양으로 싣는다.
문구만 있으면 하네스가 표지어로 추정해야 하고, 문구를 고칠 때마다 판정이 흔들린다(123 §2.5).

**왜 별도 필드인가.** 순차 경과 채널(`dependency_notes`)은 `detail`이 있으면 「순차 처리 경과」
블록으로 **렌더**되고 없으면 응답에서 **탈락**한다 — 관측 전용 kind를 실을 수 없다(123 원칙 ⑥).

**이 모듈이 정본인 것**
- 123 고지 kind 어휘(아래 상수)와 kind 표(`KIND_TABLE` — 응답 등급 · 의무 여부 · 범위).
- 다른 계획의 kind(121 `NOTE_*` · 122 `TimeResolution.notes`)는 **등급 중립**으로 같은 표에 올린다
  (123 V-1). 원 상수는 각 모듈에 두고, 이 표가 그 값을 빠짐없이 덮는지는 drift 테스트가 고정한다
  (`tests/test_domain/test_disclosure.py`) — domain은 utils를 import할 수 없어 문자열로 둔다.
- 턴 단위 고지의 우선순위·상한(W-9): 의무 고지는 모두 본문에, 그 밖은 **본문 최대 3줄**이고
  나머지는 구조 필드로만 남긴다(123 RK-5 — 고지 누적으로 응답이 장황해지는 것을 막는다).
- 134 APM 고지 kind(SPEC-apm-question-coverage §7.5)와 선택 칸 `ref`(`{"apm_job_id": …}` — 화면이
  작업 카드를 그리는 참조 · 134 W0-B). `make`·`dedupe`가 `ref`를 보존한다.
- 122 조회 기간 고지 kind(`QUERY_PERIOD` · plans/122 T-8)와 그 문구(`query_period_text`) — 해석
  결과(`TimeResolution`)를 사람이 읽는 한 줄로 바꾼다. 'YYYYMM'을 노출하지 않는다.

계층: domain — 순수 · I/O·LLM·전역 상태 0. `src.domain.time_spec`(domain)만 더 의존한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, NotRequired, TypedDict

from src.domain import time_spec as ts
from src.domain.time_spec import Subject, TimeResolution

# ── 123 고지 kind (결과가 전부가 아님을 알리는 것) ─────────────────────────────

#: 조회 결과가 시스템 행 상한(LIMIT)에 닿았다 — 이후 행이 잘렸을 수 있다(W-1).
#: 121 `truncation`(선행 결과 스코프 상한 100)과는 다른 사실이다.
ROW_LIMIT_REACHED = "row_limit_reached"
#: 사용자가 범위를 좁혀 일부 존만 조회했다(W-2 · D-176 후속4 기록의 응답 쪽).
SCOPE_NARROWED = "scope_narrowed"
#: 「전체」를 요청했지만 조회한 존이 조회 가능 존의 일부다(W-5 강화 규칙).
SCOPE_PARTIAL = "scope_partial"
#: 원문이 등록되지 않은 존을 지목해 선택 존으로 바꿔 조회했다(W-4).
UNREGISTERED_ZONE = "unregistered_zone"
#: 사용자 값의 단위가 지표에 비해 이상하다 — 조회는 요청 그대로 했다(S-1 · D-264 ⑤ 불변).
UNIT_SUSPECT = "unit_suspect"
#: SQL 생성기가 주석으로 조건을 무시·생략했다고 스스로 적었다 — 원문 인용(S-8).
GENERATOR_NOTE = "generator_note"
#: 파서가 뽑은 조건이 최종 실행 SQL에 같은 뜻으로 들어가지 않았다(S-11 1차).
CONDITION_CHANGED = "condition_changed"
#: 0건 조회의 대상 식별자가 확인한 DB에 등록돼 있지 않다(일부 DB에만 있음 포함 · S-4a).
ENTITY_NOT_FOUND = "entity_not_found"

# ── 실패 계열 (W-6) — `regen_stop.reason`(plans/119 Q-3 계약) 재사용 + SQL 차단 ──────

FAIL_VALIDATION_BUDGET = "validation_budget"
FAIL_NON_SQL = "non_sql"
FAIL_DEADLINE = "deadline"
#: 생성 SQL이 읽기 전용 가드(`sql_guard` — DML/DDL)에 막혀 실행하지 않았다(D-003).
SQL_BLOCKED = "sql_blocked"
#: `regen_stop`이 없는 조회 실패(실행 오류 · 전 DB 실패 등).
QUERY_FAILED = "query_failed"

# ── 섀도 전용 (S-2 · S-6) — 응답을 바꾸지 않고 판정만 로그에 남긴다(123·G-8 (c)) ────────

BLANK_INPUT = "blank_input"
SQL_INPUT = "sql_input"
WRITE_REQUEST = "write_request"
PROMPT_INJECTION = "prompt_injection"
CREDENTIAL_REQUEST = "credential_request"
CONDITION_CONFLICT = "condition_conflict"

#: 122 해석기 kind 재사용(123 W-8 이름 정리) — S-5 착수 때 소비한다. 원 상수는
#: `src.domain.time_spec.NOTE_FUTURE_PERIOD`(같은 값 — drift 테스트가 고정).
FUTURE_PERIOD = "future_period"

# ── 134 APM 고지 kind (SPEC-apm-question-coverage §7.5 — 각 Wave가 소비처를 붙인다) ──────────

#: 오래 걸리는 조회를 작업으로 접수했다 — 데이터 답이 아니다(W0-B · `ref` = 작업 참조).
APM_JOB_ACCEPTED = "apm_job_accepted"
#: 화면·CSV는 앞 N행이고 전체 M행은 결과 파일이다(W0-B · `ref` = 작업 참조).
APM_FULL_RESULT_FILE = "apm_full_result_file"
#: 일부 소스·도메인·조각 조회가 실패했다(게이트웨이 봉투 `partial`).
APM_PARTIAL_SOURCES = "apm_partial_sources"
#: 기간을 말했지만 현재값만 있는 보기다.
APM_CURRENT_ONLY = "apm_current_only"
#: 시 단위 통계라 요청 구간보다 넓은 정시 경계로 집계했다.
APM_HOURLY_RESOLUTION = "apm_hourly_resolution"
#: 변경 감지 시각이며 배포 확정이 아니다(W2).
APM_CHANGE_DETECTION = "apm_change_detection"
#: 개인정보·자격증명을 가렸다(G-11 미결).
APM_MASKED_FIELDS = "apm_masked_fields"
#: 해석하지 못한 조건·기능이 있다 — 다른 조회로 대신하지 않았다.
APM_UNRESOLVED_CONDITION = "apm_unresolved_condition"
#: 고지 항목의 선택 칸 `ref`에서 작업 카드가 읽는 키(게이트웨이 작업 ID).
REF_APM_JOB_ID = "apm_job_id"

# ── 122 조회 기간 고지 (plans/122 T-8 · §10.3 「고지」 · D-309) ──────────────────────────────

#: 이번 조회의 실행 SQL이 실제로 쓴 기간 해석을 알린다 — 출처가 기본값(기간 미지정)·모델 해석이면
#: 그 사실도 적는다(침묵 폴백 금지). 등급은 정하지 않고(neutral) 본문에서 생략하지 않는다(의무).
QUERY_PERIOD = "query_period"
#: 조회 기간 고지의 머리표 — 본문에는 `[안내]` 대신 이 머리표로 싣는다(§10.3.1 문구 그대로).
QUERY_PERIOD_HEAD = "[조회 기간]"

Grade = Literal["partial", "correct", "guide", "refuse", "error", "auxiliary", "neutral"]
Scope = Literal["task", "turn", "shadow", "foreign"]


class Disclosure(TypedDict):
    """응답 계약의 고지 한 건 — `done.disclosures[]`·`QueryResponse.disclosures`의 원소."""

    kind: str
    text: str
    #: 어디서 생긴 사실인가 — `"turn"`(턴 전체) 또는 `"task:<task_id>"`·`"task"`(조회 한 건).
    source: str
    #: 선택 — 화면이 따라갈 참조(예 `{"apm_job_id": "<32 hex>"}` — 134 W0-B 작업 카드).
    ref: NotRequired[dict[str, str]]


@dataclass(frozen=True)
class KindSpec:
    """kind 표의 한 행 (123 W-8 — 재계획 의미 · 응답 등급 · 렌더 위치 · 매뉴얼 절을 한 표로)."""

    kind: str
    #: 하네스 응답 등급(123 V-1 kind → 등급 표 초안 · `neutral`은 등급을 정하지 않는다).
    grade: Grade
    #: 본문에서 절대 생략하지 않는 고지(원칙 ⑫ — 122·A 문구 단언 대상 · 결과 신뢰에 직결).
    mandatory: bool
    #: 발생 범위 — task(조회 한 건) · turn(턴 한 번) · shadow(로그만) · foreign(다른 계획 kind).
    scope: Scope
    #: 본문 렌더 우선순위(작을수록 먼저). 의무 고지는 순위와 무관하게 모두 싣는다.
    priority: int = 50


#: kind 표 — 정본(123 W-8). 다른 계획 kind는 `foreign` · `neutral`로 등재만 한다(123 V-1).
KIND_TABLE: dict[str, KindSpec] = {
    spec.kind: spec
    for spec in (
        # 123 — task 단위
        KindSpec(ROW_LIMIT_REACHED, "partial", True, "task", 10),
        KindSpec(FAIL_VALIDATION_BUDGET, "error", True, "task", 5),
        KindSpec(FAIL_NON_SQL, "error", True, "task", 5),
        KindSpec(FAIL_DEADLINE, "error", True, "task", 5),
        KindSpec(SQL_BLOCKED, "refuse", True, "task", 5),
        KindSpec(QUERY_FAILED, "error", True, "task", 5),
        KindSpec(CONDITION_CHANGED, "correct", False, "task", 20),
        KindSpec(ENTITY_NOT_FOUND, "guide", False, "task", 15),
        KindSpec(GENERATOR_NOTE, "auxiliary", False, "task", 40),
        # 123 — 턴 단위
        # 좁힌 범위는 사용자(또는 러너 자동 응답 — D-216 ②)가 고른 범위다 — 응답의 대응 등급을
        # 정하지 않는다(123 V-1 확정). 「전체」 요청을 일부 존으로 답한 것은 `scope_partial`이
        # partial.
        KindSpec(SCOPE_NARROWED, "neutral", True, "turn", 10),
        KindSpec(SCOPE_PARTIAL, "partial", True, "turn", 10),
        KindSpec(UNREGISTERED_ZONE, "correct", False, "turn", 20),
        KindSpec(UNIT_SUSPECT, "correct", False, "turn", 30),
        # 122 T-8 — 조회 한 건(task)의 실행 SQL이 쓴 기간. 기본값·모델 해석도 본문에 반드시 나와야
        # 하므로 의무 고지다(W-9 「본문 최대 3줄」 상한에 밀리지 않는다 · 1단 합성이 떨어뜨리면
        # 집계기가 되살린다). 응답의 대응 등급은 정하지 않는다.
        KindSpec(QUERY_PERIOD, "neutral", True, "task", 25),
        # 123 — 섀도(응답 불변 · S-2·S-6 · run R5′ 뒤 기본 on 판정)
        KindSpec(BLANK_INPUT, "guide", False, "shadow"),
        KindSpec(SQL_INPUT, "refuse", False, "shadow"),
        KindSpec(WRITE_REQUEST, "refuse", False, "shadow"),
        KindSpec(PROMPT_INJECTION, "refuse", False, "shadow"),
        KindSpec(CREDENTIAL_REQUEST, "refuse", False, "shadow"),
        KindSpec(CONDITION_CONFLICT, "guide", False, "shadow"),
        # 121 `NOTE_*`(src/utils/prior_dependency.py) — 등급 중립(123 V-1)
        *(
            KindSpec(k, "neutral", False, "foreign")
            for k in (
                "gate", "trace", "truncation", "postcheck", "sufficiency", "scope_db",
                "decompose", "ownership", "routing_fallback", "bridge", "probe",
                "source_unavailable", "structure_missing", "descriptions_missing",
            )
        ),
        # 122 `TimeResolution.notes`(src/domain/time_spec.py) — 등급 중립(123 V-1)
        *(
            KindSpec(k, "neutral", False, "foreign")
            for k in (
                "default_period", "current_month_excluded", "empty_range",
                "period_in_progress", FUTURE_PERIOD, "display_grain_unaligned",
                "year_inferred", "multiple_periods",
                # 알람 경로 예외(`subject="event"` · D-291)
                "event_no_default_period", "event_to_now",
            )
        ),
        # 134 APM(SPEC-apm-question-coverage §7.5) — 등급·의무·범위는 SPEC 표 그대로
        KindSpec(APM_JOB_ACCEPTED, "partial", True, "task", 5),
        KindSpec(APM_FULL_RESULT_FILE, "neutral", True, "task", 10),
        KindSpec(APM_PARTIAL_SOURCES, "partial", True, "task", 10),
        KindSpec(APM_UNRESOLVED_CONDITION, "guide", True, "task", 15),
        KindSpec(APM_CURRENT_ONLY, "neutral", False, "task", 30),
        KindSpec(APM_HOURLY_RESOLUTION, "neutral", False, "task", 30),
        KindSpec(APM_CHANGE_DETECTION, "neutral", False, "task", 30),
        KindSpec(APM_MASKED_FIELDS, "neutral", False, "task", 40),
    )
}

#: 턴 단위 고지 중 의무가 아닌 것의 본문 최대 줄 수(123 W-9 · RK-5).
TURN_BODY_MAX_OPTIONAL_LINES = 3

#: 실패 kind — 집계기가 0행 실패를 「데이터 없음」 문구가 아니라 실패 안내로 끝낸다(W-6).
FAILURE_KINDS: frozenset[str] = frozenset(
    {FAIL_VALIDATION_BUDGET, FAIL_NON_SQL, FAIL_DEADLINE, SQL_BLOCKED, QUERY_FAILED}
)

_NOT_EMPTY_TAIL = "데이터가 없다는 뜻이 아닙니다."


def failure_text(kind: str, *, subject: str, detail: str | None = None) -> str:
    """조회 실패 안내 문구 — **단일 출처**(plans/123 W-6 · 121 TP-5.1 종결 사유와 공유).

    실패를 「조건에 해당하는 데이터가 없습니다」로 바꾸면 사용자는 대상이 없다고 믿는다(침묵 강등).
    문구에는 하네스 등급 표지(`scripts/scenario/assertions.py` `_CORRECT_MARKERS`의 「대신」 등)를
    우연히 넣지 않는다 — 등급은 kind가 정한다(123 V-1). SQL 차단만 거부 문구다(D-003 읽기 전용).
    """
    subj = " ".join(str(subject or "").split())[:60] or "요청하신"
    why = " ".join(str(detail or "").split())[:150]
    tail = f" (마지막 사유: {why})" if why else ""
    if kind == SQL_BLOCKED:
        return (
            "삭제·수정 같은 데이터 변경 요청은 수행할 수 없습니다(읽기 전용). "
            "조회하려는 내용을 말씀해 주시면 다시 조회하겠습니다."
        )
    if kind == FAIL_VALIDATION_BUDGET:
        return (
            f"「{subj}」 조회는 SQL을 재시도 한도까지 다시 만들었지만 검증을 통과하지 못해 "
            f"결과를 얻지 못했습니다. {_NOT_EMPTY_TAIL}{tail}"
        )
    if kind == FAIL_DEADLINE:
        return (
            f"「{subj}」 조회는 응답 시간 상한 안에 끝나지 않아 결과를 얻지 못했습니다. "
            f"{_NOT_EMPTY_TAIL}{tail}"
        )
    if kind == FAIL_NON_SQL:
        return f"「{subj}」 조회는 요청을 SQL로 옮기지 못해 결과를 얻지 못했습니다.{tail}"
    return f"「{subj}」 조회 중 오류가 발생해 결과를 얻지 못했습니다. {_NOT_EMPTY_TAIL}{tail}"


def _clean_ref(ref: Any) -> dict[str, str] | None:
    """`ref` 칸 — 문자열 키·값만 남긴다(비면 None)."""
    if not isinstance(ref, Mapping):
        return None
    clean = {str(k): str(v) for k, v in ref.items() if k and v is not None and str(v)}
    return clean or None


def make(
    kind: str, text: str, *, source: str = "turn", ref: Mapping[str, Any] | None = None
) -> Disclosure:
    """고지 한 건을 만든다 — kind는 표에 있어야 한다(오타가 조용히 새 kind가 되지 않게).

    `ref`(선택)는 화면이 따라갈 참조다(134 W0-B 작업 카드 — `{"apm_job_id": …}`).
    """
    if kind not in KIND_TABLE:
        raise ValueError(f"등록되지 않은 disclosure kind: {kind!r}")
    item: Disclosure = {"kind": kind, "text": " ".join(str(text).split()), "source": source}
    clean = _clean_ref(ref)
    if clean:
        item["ref"] = clean
    return item


def dedupe(items: Iterable[Mapping[str, Any] | None]) -> list[Disclosure]:
    """(kind, text, ref) 기준으로 중복을 지운다 — 처음 나온 순서를 지킨다.

    형식이 틀린 항목은 버린다. 선택 칸 `ref`는 보존한다(없는 항목은 종전과 같은 세 칸 그대로).
    """
    seen: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
    out: list[Disclosure] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or "")
        text = str(item.get("text") or "")
        if not kind or not text:
            continue
        ref = _clean_ref(item.get("ref"))
        key = (kind, text, tuple(sorted(ref.items())) if ref else ())
        if key in seen:
            continue
        seen.add(key)
        kept: Disclosure = {"kind": kind, "text": text, "source": str(item.get("source") or "turn")}
        if ref:
            kept["ref"] = ref
        out.append(kept)
    return out


def body_lines_for_turn(items: Iterable[Mapping[str, Any]]) -> list[Disclosure]:
    """턴 단위 고지 중 **본문에 렌더할 것**만 우선순위대로 고른다(W-9).

    - 의무 고지(`KindSpec.mandatory`)는 모두 싣는다 — 원칙 ⑫(다른 계획의 의무 고지 · 122·A 문구
      단언 대상)를 깎지 않는다.
    - 그 밖은 우선순위 순으로 최대 `TURN_BODY_MAX_OPTIONAL_LINES`줄. 넘는 것은 구조 필드
      (`disclosures`)에만 남는다 — 호출부가 전체 목록을 따로 싣는다.
    - 섀도 kind는 본문에 싣지 않는다(응답 불변).
    """
    ranked = sorted(
        (d for d in dedupe(items) if KIND_TABLE.get(d["kind"], _UNKNOWN).scope != "shadow"),
        key=lambda d: KIND_TABLE.get(d["kind"], _UNKNOWN).priority,
    )
    body: list[Disclosure] = []
    optional = 0
    for d in ranked:
        spec = KIND_TABLE.get(d["kind"], _UNKNOWN)
        if spec.mandatory:
            body.append(d)
        elif optional < TURN_BODY_MAX_OPTIONAL_LINES:
            body.append(d)
            optional += 1
    return body


#: 문구가 자기 머리표를 가진 kind — 본문에 `[안내]`를 덧붙이지 않고 문구 그대로 싣는다.
_SELF_HEADED_KINDS: frozenset[str] = frozenset({QUERY_PERIOD})


def render_line(item: Mapping[str, Any]) -> str:
    """고지 한 건의 본문 줄 — `[안내] …`(머리표를 가진 kind는 문구 그대로 · `[조회 기간] …`)."""
    text = str(item.get("text") or "")
    return text if item.get("kind") in _SELF_HEADED_KINDS else f"[안내] {text}"


def render_lines(items: Iterable[Mapping[str, Any]]) -> str:
    """고지 목록을 본문 꼬리 블록으로 렌더한다 — 한 줄에 하나(`render_line`). 없으면 빈 문자열."""
    lines = [render_line(d) for d in dedupe(items)]
    return "\n".join(lines)


# ── 조회 기간 문구 (plans/122 T-8) ──────────────────────────────────────────────

_GRAIN_LABELS: dict[str, str] = {"hour": "시간 단위", "day": "일 단위", "month": "월 단위"}
#: 해석기 고지 코드(`TimeResolution.notes`) → 문구. 표에 없는 코드(기본값 표지 등)는 머리 문구가
#: 이미 말하거나 싣지 않는다.
_PERIOD_NOTE_PHRASES: dict[str, str] = {
    ts.NOTE_CURRENT_MONTH_EXCLUDED: "이번 달 제외",
    ts.NOTE_EMPTY_RANGE: "완결된 구간이 아직 없습니다",
    ts.NOTE_EVENT_TO_NOW: "현재 시각까지",
    ts.NOTE_PERIOD_IN_PROGRESS: "진행 중 기간",
    ts.NOTE_MULTIPLE_PERIODS: "여러 기간을 모두 포함",
    ts.NOTE_YEAR_INFERRED: "연도 미지정 — 가장 최근 연도로 해석",
    ts.NOTE_DISPLAY_GRAIN_UNALIGNED: "요청한 집계 단위를 기간 경계에 맞춰 조정",
    ts.NOTE_FUTURE_PERIOD: "아직 오지 않은 기간 포함",
}
_LLM_SOURCE_PHRASE = "모델 해석 — 다르면 날짜를 직접 적어 주세요"
#: 원문 스팬 에코 상한(자) — LLM 슬롯 스팬은 길이 제한이 없다(리뷰 m-7).
SPAN_MAX_CHARS = 40


def _span_text(span: str) -> str:
    """고지에 싣는 원문 스팬 — 공백·개행을 한 칸으로 접고 `SPAN_MAX_CHARS`자에서 말줄임한다.

    개행이 남으면 `[조회 기간]` 줄이 쪼개져 줄 단위 중복 제거(집계기)가 어긋난다(리뷰 m-7).
    """
    text = " ".join(str(span or "").split())
    return text if len(text) <= SPAN_MAX_CHARS else text[: SPAN_MAX_CHARS - 1] + "…"


def _period_range_label(res: TimeResolution) -> str:
    """기간 표기(`TimeResolution.label()`). 빈 구간은 날짜만 — 고지 문구가 따로 말한다."""
    if res.is_empty and res.end is not None:
        return f"{res.end:%Y-%m-%d %H:%M}" if res.grain == "hour" else f"{res.end:%Y-%m-%d}"
    start, end = res.start, res.end
    if (
        start is not None and end is not None and end - start == timedelta(days=1)
        and start == start.replace(hour=0, minute=0, second=0, microsecond=0)
    ):
        return f"{start:%Y-%m-%d}"  # 하루(「어제」·「9월 15일」)는 날짜 하나로
    return res.label()


def period_label(res: TimeResolution, *, subject: Subject = "metric") -> str:
    """사람이 읽는 기간 + 집계 단위(`2026-08-30 ~ 2026-09-28 · 일 단위`) — 'YYYYMM' 노출 없음.

    사건(`subject="event"` — 알람)은 통계 집계 단위가 없어 기간만 적는다. 응답 프롬프트의 기준 정보
    (D-186)와 고지 문구가 같은 표기를 쓴다.
    """
    label = _period_range_label(res)
    if subject == "metric" and not res.unbounded:
        return f"{label} · {_GRAIN_LABELS[res.grain]}"
    return label


def query_period_text(res: TimeResolution, *, subject: Subject = "metric") -> str:
    """조회 기간 고지 한 줄 — `[조회 기간] 2026-08-30 ~ 2026-09-28 · 일 단위 (「최근 30일」)`.

    plans/122 §10.3 「고지」·§10.3.1.

    - 기간 미지정(source=default): `[조회 기간] 기간 미지정 — 지난달 기준(2026-08-01 ~ 2026-08-31)
      · 월 단위`
    - 모델 해석(source=llm): 끝에 「모델 해석 — 다르면 날짜를 직접 적어 주세요」
    - 해석기 고지 코드는 문구로 덧붙인다(「이번 달 제외」·「현재 시각까지」 등)
    """
    if res.source == "default":
        if res.unbounded:
            head = "기간 미지정 — 전체 보관 기간(기간 조건 없음)"
        else:
            head = f"기간 미지정 — 지난달 기준({_period_range_label(res)})"
        if subject == "metric" and not res.unbounded:
            head = f"{head} · {_GRAIN_LABELS[res.grain]}"
    else:
        head = period_label(res, subject=subject)
        span = _span_text(res.span)
        if span:
            head = f"{head} (「{span}」)"
    parts = [head]
    parts.extend(_PERIOD_NOTE_PHRASES[n] for n in res.notes if n in _PERIOD_NOTE_PHRASES)
    if res.source == "llm":
        parts.append(_LLM_SOURCE_PHRASE)
    return f"{QUERY_PERIOD_HEAD} " + " · ".join(parts)


def period_years(res: TimeResolution) -> list[str]:
    """기간이 걸친 연도(시작 · 끝을 포함하는 마지막 시각) — 응답 연도 사후 가드(D-186)의 기준."""
    points = [res.start] if res.start is not None else []
    if res.end is not None:
        points.append(res.end - timedelta(microseconds=1) if not res.is_empty else res.end)
    return sorted({f"{p.year:04d}" for p in points})


_UNKNOWN = KindSpec("", "neutral", False, "foreign", 90)
