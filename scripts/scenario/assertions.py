"""L1·L2·LR 단언 평가기 (plans/94 §3.3 · §3.8).

**결정적이다. LLM을 쓰지 않는다**(D-035 · G-5). 자연어 응답의 "정답"은 기계가 판정할 수
없으므로, 판정할 수 없는 것은 판정하지 않고 `manual`로 남긴다 - 합격으로 세지 않는 것이
핵심이다. 조용히 합격으로 세면 리포트가 커버리지를 부풀린다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from . import REPO_ROOT
from .catalog import (
    DISCLOSURE_KIND_GRADES,
    FORBIDDEN_MODES,
    FORM_MEMORY_PANEL_STATES,
    PLAN_ANY_AGENT,
    STREAM_KEYS,
    Group,
    Scenario,
    Turn,
)

# 대응 등급 탐지 표지 (§3.8).
#
# 이 표지는 **관측 어휘**다. 등급은 먼저 제품의 구조 필드 `disclosures[].kind`(plans/123 V-1)로
# 정하고, kind 가 없거나(과거 run) 등급을 정하지 않는 kind 뿐이면 표지어로 고른다. 어느 표지가
# 맞았는지는 raw.jsonl 의 mode_evidence 에 남겨 사람이 감사할 수 있게 한다.
_REFUSE_MARKERS = (
    "수행할 수 없", "허용되지 않", "읽기 전용", "실행하지 않", "거부",
    "삭제할 수 없", "변경할 수 없", "권한이 없",
)
# plans/123 V-3 - 「지원 범위에 포함되지 않」·「제공하고 있지 않」·「제공되지 않」(run
# 20260923-103638 에서 guide 응답 7턴이 이 표지 누락으로 answer 로 분류됐다).
_GUIDE_MARKERS = (
    "지원하지 않", "제공하지 않", "수집 대상이 아니", "수집하지 않", "범위 밖",
    "기능이 없", "존재하지 않", "등록되어 있지 않", "해당 존이 없",
    "지원 범위에 포함되지 않", "제공하고 있지 않", "제공되지 않",
)
_CORRECT_MARKERS = ("오타", "으로 이해", "로 이해", "으로 해석", "로 해석", "교정", "대신")
_PARTIAL_MARKERS = ("일부만", "절단", "잘라", "전체가 아니", "먼저 보여", "우선 표시")
# plans/123 V-3 `empty_template` - 0건 응답의 템플릿 문구(제품 빈 결과 문구 · 완화 제안 · 「확인되지
# 않습니다」류 LLM 서술). 대상 없음·미래·충돌을 짚는 안내(`guide`)가 먼저 판정된다.
_EMPTY_TEMPLATE_MARKERS = (
    "데이터가 없습니다", "데이터가 없어", "데이터가 없는", "결과가 없습니다", "조회된 데이터가 없",
    "조회 결과가 없", "확인되지 않습니다", "임계값 낮추기", "임계값을 낮추거나", "조건을 완화",
)
_CRASH_MARKERS = ("Traceback (most recent call last)", "Internal Server Error")

#: 고지 kind 등급의 우선순위(plans/123 V-1) - 한 턴에 여러 kind 가 있으면 앞선 등급이 턴의 등급이다.
_GRADE_PRECEDENCE: tuple[str, ...] = ("refuse", "error", "guide", "partial", "correct")
#: 범위·상한 고지 kind - 응답의 **대응**이 아니라 **조회 범위**를 알린다. 응답 본문의 거절·안내·빈
#: 결과 표지보다 뒤에 본다: 상한에 닿은 「수집하지 않는 지표」 안내 응답을 `partial` 로 덮으면 안
#: 된다(R4-05 형). `scope_narrowed`(사용자·러너 자동 응답이 고른 범위 - D-216 ②)는 등급을 정하지
#: 않는다(`neutral`).
CONTEXT_KINDS: frozenset[str] = frozenset({"row_limit_reached", "scope_partial"})
#: 상한 고지만으로 정해진 `partial` - `answer` 를 허용한 시나리오에서는 `answer` 동치다(123 V-1 ·
#: 상한 도달 대조군 R2-07C·R3-08C). 상한을 밝힌 정상 조회를 「정상 응답이 아니다」로 떨어뜨리지
#: 않는다.
LIMIT_ONLY_PARTIAL_EVIDENCE = "kind:row_limit_reached"

# 부정 단언 - 위반이 곧 silent_wrong 후보다(§3.8).
_NEGATIVE_KEYS = frozenset({"sql_must_not_match", "response_must_not_contain", "column_must_not_map"})

# 모의 실행에서 **적용하지 않는** 단언.
#
# 모의 서버는 시나리오에 `mock:` 블록이 없으면 canned 응답(`SELECT hostname FROM
# cmm_resource LIMIT 5`)을 돌려준다. 여기에 실제 SQL 구조 단언을 걸면 전건이 불합격이
# 되는데, 그것은 시스템이 아니라 **모의 서버를 판정한 결과**다. 모의 실행의 목적은 배관
# 검증이므로(server.verify_profile 의 "무과금 배관 검증") 내용 단언은 사유와 함께
# 보류로 넘긴다.
#
# **`mock:` 블록이 있으면 적용한다.** 작성자가 응답 내용을 직접 정했다는 뜻이고, 그때의
# 단언은 "SQL·행수가 끝까지 전달되는가"라는 배관을 실제로 검증한다(F-01 선례).
# 상태·역질문·SSE 는 어느 경우에도 `mock:` 블록이 정하므로 그대로 판정한다.
# 모의 실행에서 **판정할 수 있는** 단언. 이 셋만 남기고 나머지는 보류한다.
#
# 처음에는 "내용 단언만" 보류했는데 그것으로 부족했다(2026-09-14 모의 실행 실측:
# F-03·F-04 는 `status: clarification` 에서, H-01 은 `has_file: true` 에서 불합격이었다).
# `mock:` 블록이 없으면 canned 응답이 **상태·역질문·산출물까지 전부** 정하므로, 남겨 둔
# 키도 결국 모의 서버를 판정한다. 모의가 실제로 증명하는 것은 배관뿐이다 -
# 요청이 나갔고, SSE 가 흘렀고, 노드를 밟았고, 200 이 돌아왔다.
_MOCK_VERIFIABLE = frozenset({"sse_events", "node_path", "http_status"})


@dataclass
class Observation:
    """턴 1회 실행의 관측치. 클라이언트가 채우고 평가기가 읽는다."""

    http_status: int = 0
    query_id: Optional[str] = None
    status: str = "unknown"          # completed | clarification | error | awaiting_approval
    response: str = ""
    executed_sql: Optional[str] = None
    # 이 턴에 실제로 실행된 SQL 전부. 오케스트레이션·멀티 DB 경로는 done 에 SQL 이 실리지 않아
    # (`executed_sql`=None · run 20260914-154940 93턴 전부) 러너가 서버 감사 로그 `query_executed`
    # 에서 thread_id 로 모은다. SQL 단언은 이 목록을 SQL 별로 본다(`observed_sqls`).
    executed_sqls: list[str] = field(default_factory=list)
    row_count: Optional[int] = None
    #: DB 별 반환 행 수(감사 로그 `query_executed` 의 `source_name`·`row_count`).
    #: **`row_count` 는 멀티 DB 팬아웃의 합계다** - D-02 는 100×3=300 이었는데 단언은
    #: per-DB `max: 100` 이었다. 각 DB 는 정확히 100행을 냈는데 판정 축이 틀렸다(Y-4).
    row_counts_by_db: dict[str, int] = field(default_factory=dict)
    has_file: bool = False
    file_name: Optional[str] = None
    clarification: Optional[dict] = None
    form_fill_clarification: Optional[dict] = None
    # 저장 값 패널(D-187). 삭제 턴의 signature 를 여기서 얻는다(I-06).
    form_memory_panel: Optional[dict] = None
    db_ids: list[str] = field(default_factory=list)
    # `db_ids` 의 출처(plans/120 V-1) - `scope`(done `db_scope`) | `executed`(감사 로그 실행 DB)
    # | None(둘 다 없음).
    db_ids_source: Optional[str] = None
    intent: Optional[str] = None
    processing_time_ms: Optional[float] = None
    wall_ms: float = 0.0
    # 요청 송신 → **첫 `node_start` 수신**(ms). 서버가 그래프를 시작했다는 신호일 뿐 답변이 아니다
    # (run 20260923-103638 p50 27ms). 답변 체감 지연은 아래 `ttft_ms` 로 읽는다(plans/119 H-1).
    ttfb_ms: Optional[float] = None
    # 요청 송신 → **첫 `token` 이벤트(내용 있음) 수신**(ms · plans/119 H-1). `ttfb_ms` 와
    # 기준 시각이 같다. 토큰 없이 done 만 온 턴(역질문·비스트림)은 None 이다 - 0 으로 채우지
    # 않는다.
    ttft_ms: Optional[float] = None
    # 서버 단계 타임라인(plans/119 T-0) - 스트림 `done`·`error` 페이로드의 `timeline` 객체 그대로.
    # 옛 서버는 싣지 않는다(None).
    timeline: Optional[dict] = None
    # 2단 계획 요약(plans/121 TP-0.1) — 스트림 `done.plan_summary` 그대로(코드·개수만). 없으면 None.
    plan_summary: dict[str, Any] | None = None
    # 노드별 **누적** 실행 시간. 재계획 루프로 같은 노드가 여러 번 돌면 회차를 합친다.
    node_elapsed_ms: dict[str, float] = field(default_factory=dict)
    node_calls: dict[str, int] = field(default_factory=dict)  # 노드별 완료 횟수(루프 회차)
    node_path: list[str] = field(default_factory=list)
    sse_events: list[str] = field(default_factory=list)
    progress_events: list[dict] = field(default_factory=list)
    llm_calls: Optional[int] = None   # 스트림에 없다 - 구조적 측정 불가(client 주석 참조)
    tokens: Optional[int] = None      # 동일
    retries: Optional[int] = None     # 재생성 회차 실측(스트림 진행 이벤트 + 감사 로그 retry_attempt)
    # True 면 retries 는 **하한**이다 - 멀티 DB 경로는 검증 거부 재시도가 스트림·감사 로그에 안 보인다.
    retries_partial: bool = False
    node_count: Optional[int] = None  # 실행 노드 수 - 비용 대리 지표
    column_mapping: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    # 무이벤트 구간이 하트비트 간격을 크게 넘겼거나 done 없이 끊긴 경우 True (금지 등급 hang).
    hang: bool = False
    max_event_gap_ms: Optional[float] = None
    error: Optional[str] = None
    # 401/403 을 받아 재로그인 후 1회 재시도했다(T-a). 재시도가 성공했어도 남긴다 -
    # "8시간 run 에서 토큰이 언제 죽었는가"는 재시도가 삼키면 관측되지 않는다.
    auth_retried: bool = False
    # O-e(plans/94 §19.3): 서버가 남긴 재작성 감사 레코드(plans/107 §4.9). 1순위는 완료 done
    # 페이로드, 없으면 감사 로그(`rewrite_trace` 이벤트). 서버가 INTENT_FRAME_ENABLED 가
    # 아니면 빈다.
    rewrite_traces: list[dict] = field(default_factory=list)
    # 턴 송신 시각(plans/122 H-2) — KST ISO 8601(초 단위). 상대 기간·오라클 자리표의 앵커다.
    anchor_at: Optional[str] = None
    # 순차 의존 경과 노트(plans/122 H-5 · plans/121 TP-11.8) — 스트림 `done.dependency_notes` 그대로.
    dependency_notes: list[dict[str, Any]] = field(default_factory=list)
    # 결과 행(plans/122 H-1 · G-4) — 러너가 `/query/{id}/download-csv` 로 받는다. None = 수집하지 않았다.
    # {"status": "ok"|"empty"|"unavailable", "columns": [...], "rows": [{열: 값}], "total_rows": int,
    #  "truncated": bool, "reason": str|None}. 행 원문은 raw.jsonl 에 싣지 않는다(판정 결과만).
    result: Optional[dict[str, Any]] = None
    # 오라클 실행 결과(plans/122 O-1·O-2·O-4) — 러너가 `oracle.run_oracle` 로 채운다.
    # None = 실행하지 않았다(모의 실행 · 환경 보류 · `source: fixture` · 옛 run). 형태:
    #   {"id": 오라클 id, "targets": [db_id…],
    #    "pre": run_oracle 결과(phase="pre") | None,    # snapshot=pre_post — 송신 직전
    #    "post": run_oracle 결과(phase="post") | None}  # 턴 완료·결과 행 수집 뒤
    # run_oracle 결과 = {"status": "ok"|"unavailable", "reason", "rows_by_db": {db: [행]},
    #   "elapsed_ms", "phase", "limit_by_db"}. `rows_by_db` 는 마스킹된 행 원문이다
    #   - raw.jsonl 에는 요약만 싣는다(G-4).
    # 대상 DB 가 없어 돌리지 않았으면 post 는 status=unavailable·사유만 있는 같은 모양이다.
    oracle: Optional[dict[str, Any]] = None
    # 응답 고지(plans/123 W-8 · V-1) - 스트림 `done.disclosures`·`QueryResponse.disclosures`
    # (`{kind, text, source}` 목록). **None = 수집하지 않았다**(옛 러너·과거 run 행) · [] = 수집했고
    # 고지 없음. 불변식 활성(V-4)은 이 칸을 수집한 run(= W-8 이후 러너 · run R5~)에서만 판정에
    # 들어간다.
    disclosures: Optional[list[dict[str, Any]]] = None
    # 감사 로그 실행 SQL 항목(plans/123 V-4 `limit_disclosed`) - `{sql, source, row_count, success,
    # retry_attempt}`. `executed_sqls` 는 SQL 문자열만 남긴다 - SQL 별 행 수는 여기서 읽는다.
    sql_entries: list[dict[str, Any]] = field(default_factory=list)
    # 존 선택(plans/123 V-2) - `{selected: [db_id], offered: [db_id] | None, source: auto|turn}`.
    # 러너 자동 응답(D-216 ②) 또는 턴의 `selected_db_ids` 로 조회 범위를 좁힌 턴. 없으면 None.
    zone_selection: Optional[dict[str, Any]] = None
    # 응답 본문이 원시 로그 상한(4,000자)에서 잘렸다 - 재판정 복원 행만 참이다(실 run 은 전문이
    # 있다).
    response_truncated: bool = False


@dataclass
class Failure:
    """깨진 단언 1건. 분석기의 1차 입력이다(§4.3)."""

    key: str
    expected: Any
    actual: Any

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "expected": self.expected, "actual": self.actual}


#: 러너 자신의 인증 실패로 끝난 턴의 판정값(T-c · D-218).
#:
#: **기능 판정이 아니다.** 측정이 성립하지 않은 턴이므로 판정표·실패 분류·대안 수립의
#: 분모에서 빠지고, 리포트는 건수·구간을 별도 절에 명시한다. `fail` 로 세면 `http_status`
#: 단언의 기대 200 vs 실제 401 이 **기능 불합격**으로 집계되고(run 20260915-131903 에서
#: 31건), 대조군도 함께 401 이라 「과잉 거부 의심」 26건이 전건 허위로 올라갔다.
INVALID_VERDICT = "invalid"

#: 러너 인증 실패로 보는 HTTP 상태.
AUTH_FAILURE_STATUSES = frozenset({401, 403})

#: 보류(`manual`) 사유의 출처 어휘(plans/122 J-3). 앞일수록 대표 출처(`manual_source`)로 먼저 뽑힌다.
#: - `env_mismatch`: 실행 환경이 시나리오 선언과 달라 러너가 데이터 의존 단언을 보류했다(D-216 ③)
#: - `policy`: 대응 등급 정책 미확정(`policy_confirmed: false`)
#: - `catalog`: 카탈로그 `manual_review` 문구
#: - `oracle_unavailable`: 오라클을 실행·비교하지 못했다(plans/122 O-2 — 불합격 아님)
#: - `fanout`: 단일 DB 전용 단언이 멀티 DB 팬아웃 턴에 걸렸다(Y-4)
#: - `invariant`: 활성 불변식(plans/123 V-4)을 판정하지 못했다(응답 본문 절단 등 - 불합격 아님)
#: - `unobservable`: 관측 수단이 없어 확인하지 못했다(스트림 미탑재·모의 실행·역질문 등)
MANUAL_SOURCES: tuple[str, ...] = (
    "env_mismatch", "policy", "catalog", "oracle_unavailable", "fanout", "invariant",
    "unobservable",
)


#: 판정기 지문(`judge_digest`)이 덮는 소스 — 턴 판정(`evaluate_turn`) · 오라클
#: 비교(`evaluate_oracle`) · 불변식(`invariants.evaluate_invariants` - plans/123 V-4).
JUDGE_SOURCES: tuple[str, ...] = ("assertions.py", "oracle.py", "invariants.py")


def judge_digest() -> str:
    """판정기 코드 지문 — 판정 소스 바이트의 sha256 앞 16자(plans/122 J-1 ⑤ 보강 · 123 교차 검토).

    판정 계약은 (리포트 정의 버전, 카탈로그 지문)만 봐서 **판정기 코드 변경**(예: run 뒤 들어온
    plans/120 V-1 `db_ids` 폴백 — 20260923 run 재판정 차이 14행)을 가르지 못했다. 파일 바이트를
    그대로 해시하므로 주석만 고쳐도 달라진다 — 그래서 지문이 달라도 비교를 **막지 않고** 주의 줄만
    싣는다(같은 판정기로 보려면 J-4 재판정). 버전 상수를 손으로 올리는 방식은 잊으면 조용히 틀린다.
    """
    here = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for name in JUDGE_SOURCES:
        digest.update(name.encode("utf-8") + b"\0")
        path = here / name
        digest.update(path.read_bytes() if path.exists() else b"")
    return digest.hexdigest()[:16]


def primary_manual_source(sources: Iterable[str]) -> Optional[str]:
    """보류 출처 목록의 대표값 — `MANUAL_SOURCES` 순서로 가장 앞선 것. 없으면 None."""
    present = set(sources)
    return next((source for source in MANUAL_SOURCES if source in present), None)


#: 러너 `_hold_for_env` 가 `manual_review` 에 쓰는 환경 불일치 보류 문구의 머리(D-216 ③).
#: 보류 출처(plans/122 J-3)를 가르는 데만 쓴다 — 러너 문구가 바뀌면
#: `tests/test_scenario/test_plan122_judge_sources.py` 가 깨진다.
ENV_MISMATCH_NOTE_PREFIX = "환경 불일치 - 시나리오 env="


@dataclass
class _Holds:
    """보류 사유와 그 출처(plans/122 J-3). 사유 문구는 종전과 바이트 동일하게 쌓는다."""

    notes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def add(self, note: str, *sources: str) -> None:
        """사유 1건을 쌓고 출처를 첫 등장 순서로(중복 없이) 적는다.

        출처는 `MANUAL_SOURCES` 어휘뿐이다.
        """
        if not sources or any(source not in MANUAL_SOURCES for source in sources):
            raise ValueError(f"보류 출처는 MANUAL_SOURCES 어휘여야 한다 - {sources!r}")
        self.notes.append(note)
        for source in sources:
            if source not in self.sources:
                self.sources.append(source)

    def __bool__(self) -> bool:
        return bool(self.notes)


def _review_sources(review: str) -> tuple[str, ...]:
    """`manual_review` 문구의 출처 — 카탈로그 문구 · 러너 환경 불일치 보류 · 둘을 이은 것.

    러너는 원 문구가 있으면 `"{원문} / {환경 불일치 …}"`, 없으면 환경 문구만 넣는다
    (`_hold_for_env`).
    """
    at = review.find(ENV_MISMATCH_NOTE_PREFIX)
    if at < 0:
        return ("catalog",)
    return ("env_mismatch",) if at == 0 else ("catalog", "env_mismatch")


@dataclass
class Verdict:
    """턴 1회의 판정."""

    func: str = "pass"               # pass | fail | error | manual | invalid | skipped
    perf: str = "n/a"                # pass | fail | n/a
    response_mode: str = "answer"
    forbidden_mode: Optional[str] = None
    mode_evidence: Optional[str] = None
    failures: list[Failure] = field(default_factory=list)
    manual_notes: list[str] = field(default_factory=list)
    #: 보류 사유의 출처(plans/122 J-3) — `MANUAL_SOURCES` 어휘 · 첫 등장 순서 · 중복 없음.
    manual_sources: list[str] = field(default_factory=list)
    #: `func == "invalid"` 일 때만 채운다. 무엇이 측정을 무효로 만들었는지 한 줄.
    invalid_reason: Optional[str] = None
    #: 불변식 위반(plans/123 V-4 트리아지 칸) - `{name, active, detail}`. `active` 인 것만 판정에
    #: 들어간다.
    invariant_violations: list[dict[str, Any]] = field(default_factory=list)


def _contains_any(text: str, markers: tuple[str, ...]) -> Optional[str]:
    for marker in markers:
        if marker in text:
            return marker
    return None


def observed_sqls(obs: Observation) -> list[str]:
    """이 턴에 관측된 SQL 전부. 감사 로그 수집분이 있으면 그것, 없으면 done 페이로드 1건."""
    if obs.executed_sqls:
        return list(obs.executed_sqls)
    return [obs.executed_sql] if obs.executed_sql else []


def resolve_db_ids(
    scope_ids: Iterable[Any], executed_sources: Iterable[Any]
) -> tuple[list[str], Optional[str]]:
    """`db_ids` 단언이 대조할 DB 집합과 그 출처 `(목록, "scope"|"executed"|None)` (plans/120 V-1).

    스트림 `done.db_scope` 는 **마지막 노드의 델타**로 만들어진다(`src/api/routes/query.py` 스트림
    `on_chain_end`). 3단 `output_generator` 델타에는 `active_db_id`·`target_databases` 가 없어 run
    20260923-140539 의 3단 111행이 전부 `db_ids=[]` 였고, `db_ids` 불합격 12턴은 실제 조회 DB 가
    기대값과 전부 같았다. 스코프가 비면 **SQL 이 실제로 나간 DB**(감사 로그 `source_name`)로 본다 -
    실패한 실행도 센다. 라우팅 단언은 "어디로 보냈나"를 보고, 행 수는 `row_counts_by_db` 가
    따로 본다. 스코프가 있으면 스코프가 이긴다. 제품 교정(S-1) 뒤에도 옛 run 재판정에 쓰므로 남긴다.
    """
    scope = [str(db_id) for db_id in scope_ids if db_id]
    if scope:
        return scope, "scope"
    executed = sorted({str(source) for source in executed_sources if source})
    return (executed, "executed") if executed else ([], None)


def row_db_ids(row: dict[str, Any]) -> tuple[list[str], Optional[str]]:
    """`raw.jsonl` 행 1개에 `resolve_db_ids` 를 적용한다 - 옛 run 재판정용(plans/120 V-1).

    `db_ids_source` 칸이 있는 행은 이미 폴백을 거쳤으므로 그대로 둔다. 실행 DB 는 러너가 싣는
    `executed_sqls[].source`(감사 로그 `query_executed` 의 `source_name`)다.
    """
    if row.get("db_ids_source"):
        return [str(db_id) for db_id in row.get("db_ids") or []], str(row["db_ids_source"])
    entries = row.get("executed_sqls") or []
    return resolve_db_ids(
        row.get("db_ids") or [],
        [entry.get("source") for entry in entries if isinstance(entry, dict)],
    )


def sql_body(sql: str) -> str:
    """SQL 단언이 매칭할 본문 — **주석(`--`·`/* */`)을 뺀다**(plans/114 M-6).

    생성 규칙이 SQL 머리에 `-- 설명` 주석을 강제한다. 원문에 매칭하면 A-01 의
    `sql_must_not_match: (?i)여의도` 가 주석 `-- 여의도 개발 서버들의 …` 에 걸리고(run
    20260922-112010), 반대로 주석 속 테이블명이 `sql_must_match` 를 거짓 통과시킨다.
    리터럴(`WHERE loc = '여의도'`)은 남긴다 — 그것이 부정 단언이 잡아야 할 것이다.
    주석 판정은 제품 검증기와 같은 함수를 쓴다(사본을 두면 한쪽만 낡는다).
    """
    from src.sql_validation import strip_sql_comments

    return strip_sql_comments(sql)


#: 전송 계층 인증 실패를 **원시 로그 문자열에서** 알아보는 표지.
#: `client._http_error` 가 `http 401 ...` / `http 403 ...` 형태로 적는다.
_AUTH_ERROR_RE = re.compile(r"\bhttp\s+(401|403)\b", re.IGNORECASE)


def is_auth_failure_error(error: Optional[str]) -> bool:
    """`obs.error`(=`raw.jsonl` 의 `error`)가 러너 인증 실패인가.

    **`func_verdict` 가 아니라 오류 문자열을 본다.** T-c 이전에 적재된 run 은 401 턴을
    `error`/`fail` 로 기록했으므로(run 20260915-131903 의 103턴), 판정값만 보면 그 run 은
    영영 무효로 식별되지 않는다 - 재개(X-1)가 바로 그 턴들을 건너뛴다.
    """
    return bool(error) and bool(_AUTH_ERROR_RE.search(str(error)))


def row_is_invalid(row: dict[str, Any]) -> bool:
    """`raw.jsonl` 의 행 1개가 **측정이 성립하지 않은 턴**인가(T-c).

    두 가지를 함께 본다:
      - `func_verdict == "invalid"` — T-c 이후에 적재된 행.
      - `error` 가 `http 401`/`http 403` — **T-c 이전에 적재된 행**. run 20260915-131903 의
        103턴이 여기 해당한다. 판정값만 보면 그 run 은 영영 무효로 식별되지 않아 재개가
        바로 그 턴들을 건너뛴다(X-1 이 풀려는 문제 자체다).

    오류 문구 추정은 **`invalid_reason` 칸이 없는 행(T-c 이전)에만** 쓴다(plans/122 J-4 보강).
    T-c 이후 러너는 그 칸을 늘 싣고 러너 인증 실패를 `invalid` 로 적는다 — 그 행의 `http 401` 은
    401 을 **기대한** 가드 턴(H-6 `auth: none` · J-08)의 판정된 관측이라 무효가 아니다. 문구로
    추정하면 그 턴이 재판정·리포트·재개에서 무효로 빠진다.
    """
    if str(row.get("func_verdict")) == INVALID_VERDICT:
        return True
    if "invalid_reason" in row:
        return False
    return is_auth_failure_error(row.get("error"))


def is_runner_auth_failure(obs: Observation, expect: Optional[dict[str, Any]] = None) -> bool:
    """이 턴이 **러너 자신의** 인증 실패로 끝났는가(T-c).

    401/403 을 **기대하는** 가드 시나리오는 제외한다 - 기대한 오류는 측정이 성립한 것이다.
    (2026-09-16 실측: 현 카탈로그에 401/403 을 기대하는 턴은 없다. R2 의 가드는 400·422 다.)
    """
    if expect and "http_status" in expect:
        try:
            if int(expect["http_status"]) in AUTH_FAILURE_STATUSES:
                return False
        except (TypeError, ValueError):
            pass
    if obs.http_status in AUTH_FAILURE_STATUSES:
        return True
    return obs.http_status == 0 and is_auth_failure_error(obs.error)


def disclosure_kinds(obs: Observation) -> list[str]:
    """이 턴 응답 고지의 kind(등장 순서 · 중복 없음). 수집하지 않았으면 빈 목록."""
    kinds: list[str] = []
    for item in obs.disclosures or []:
        kind = str(item.get("kind") or "") if isinstance(item, dict) else ""
        if kind and kind not in kinds:
            kinds.append(kind)
    return kinds


def _graded_kind(kinds: Iterable[str]) -> Optional[tuple[str, str]]:
    """kind 들 중 우선순위(`_GRADE_PRECEDENCE`)가 가장 앞선 (등급, kind).

    등급을 정하는 kind 가 없으면 None.
    """
    best: Optional[tuple[int, str, str]] = None
    for kind in kinds:
        grade = DISCLOSURE_KIND_GRADES.get(kind)
        if grade not in _GRADE_PRECEDENCE:
            continue            # neutral · auxiliary · 미매핑(표지어 폴백)
        rank = _GRADE_PRECEDENCE.index(grade)
        if best is None or rank < best[0]:
            best = (rank, grade, kind)
    return (best[1], best[2]) if best else None


def _norm_text(text: str) -> str:
    """본문 대조용 정규화 - 강조 표지를 걷고 공백을 한 칸으로 접는다.

    제품 `result_aggregator._norm_text` 와 같은 규칙이다.
    """
    return " ".join(str(text).replace("**", "").split())


def undisclosed_text(obs: Observation) -> str:
    """표지어를 볼 본문 - 등급을 아는 고지(kind 매핑 있음)의 문구를 걷어낸다(plans/123 V-1).

    고지는 kind 로 이미 등급에 반영된다. 문구가 본문에 남아 표지어에 한 번 더 걸리면 kind 와
    다른 등급이 난다(상한 고지의 「절단」→ partial · 좁힌 범위의 「전체가 아니라」→ partial ·
    생성기 메모 인용문의 「대신」→ correct — D-279 주의 ①). 매핑 없는 kind 의 문구는 남긴다
    (표지어 폴백).
    """
    text = _norm_text(obs.response)
    for item in obs.disclosures or []:
        if not isinstance(item, dict) or str(item.get("kind") or "") not in DISCLOSURE_KIND_GRADES:
            continue
        piece = _norm_text(item.get("text") or "")
        if piece:
            text = text.replace(piece, " ")
    return text


def _zero_rows(obs: Observation) -> bool:
    """돌려준 데이터 행이 0이다 - SQL·행이 없는 응답이거나, 행 수를 **관측했고** 0이다.

    SQL 은 있는데 행 수를 관측하지 못한 턴(합계 None · DB별 행 수 없음)은 0행으로 보지 않는다
    - 모르는 것이다.
    """
    per_db = obs.row_counts_by_db
    if not observed_sqls(obs) and not (obs.row_count or 0):
        return True
    observed = obs.row_count == 0 or (obs.row_count is None and bool(per_db))
    return observed and not any((count or 0) for count in per_db.values())


def classify_mode(obs: Observation) -> tuple[str, Optional[str]]:
    """대응 등급을 고른다. 먼저 맞는 것을 적용한다. (등급, 근거 표지)를 돌려준다.

    순서(plans/123 V-1·V-3): 구조(crash·hang·clarify·error) → **응답 고지 kind**(범위·상한
    kind 제외 · 우선순위 refuse > error > guide > partial > correct) → 거절·안내 표지(데이터 없는
    응답 · **0행이면 SQL 이 있어도 안내 표지**) → `empty_template`(0행 + 빈 결과 템플릿) →
    범위·상한 kind(`CONTEXT_KINDS`) → 교정·부분 표지 → answer. 표지어는 kind 로 등급이 정해진
    고지 문구를 걷어낸 본문에서 본다(`undisclosed_text`). 근거가 kind 이면 `mode_evidence` 는
    `kind:<kind>` 다.
    """
    if obs.http_status >= 500:
        return "crash", f"http_status={obs.http_status}"
    crash_marker = _contains_any(obs.response, _CRASH_MARKERS)
    if crash_marker:
        return "crash", crash_marker
    if obs.hang:
        return "hang", f"max_event_gap_ms={obs.max_event_gap_ms}"
    if obs.status == "clarification" or obs.clarification or obs.form_fill_clarification:
        return "clarify", "clarification"
    if obs.status == "error" or obs.http_status >= 400:
        return "error", f"status={obs.status} http={obs.http_status}"
    kinds = disclosure_kinds(obs)
    graded = _graded_kind(kind for kind in kinds if kind not in CONTEXT_KINDS)
    if graded:
        return graded[0], f"kind:{graded[1]}"
    text = undisclosed_text(obs)
    zero_rows = _zero_rows(obs)
    # 거부·안내 표지는 **데이터를 돌려주지 않은 응답**에만 적용한다. 데이터 표에 붙은 진단 절
    # ("[일부 존 조회 실패] ... 허용되지 않은 테이블")이 거부로 분류되던 오분류(SYN-F-03)를 막는다 -
    # 종전에는 오케스트레이션 경로의 executed_sql 이 늘 None 이라 모든 응답이 이 검사를 탔다.
    if not observed_sqls(obs) and not (obs.row_count or 0):
        marker = _contains_any(text, _REFUSE_MARKERS)
        if marker:
            return "refuse", marker
    # plans/123 V-3 - 0행 응답은 SQL 이 있어도 안내 표지를 본다(「nonexistent-01 은 존재하지
    # 않습니다」를 조회 뒤에 말한 응답이 answer 로 분류됐다). 행이 있는 응답은 종전대로 보지
    # 않는다(SYN-F-03).
    if zero_rows:
        marker = _contains_any(text, _GUIDE_MARKERS)
        if marker:
            return "guide", marker
        marker = _contains_any(text, _EMPTY_TEMPLATE_MARKERS)
        if marker:
            return "empty_template", marker
    context = _graded_kind(kind for kind in kinds if kind in CONTEXT_KINDS)
    if context:
        return context[0], f"kind:{context[1]}"
    marker = _contains_any(text, _CORRECT_MARKERS)
    if marker:
        return "correct", marker
    marker = _contains_any(text, _PARTIAL_MARKERS)
    if marker:
        return "partial", marker
    return "answer", None


def _check_row_count(
    spec: Any, actual: Optional[int], failures: list[Failure], key: str = "row_count"
) -> None:
    if not isinstance(spec, dict):
        return
    if "eq" in spec and actual != int(spec["eq"]):
        failures.append(Failure(f"{key}.eq", spec["eq"], actual))
    if "min" in spec and (actual is None or actual < int(spec["min"])):
        failures.append(Failure(f"{key}.min", spec["min"], actual))
    if "max" in spec and (actual is None or actual > int(spec["max"])):
        failures.append(Failure(f"{key}.max", spec["max"], actual))


def _check_row_count_axes(
    expect: dict[str, Any], obs: Observation, failures: list[Failure], manual: _Holds
) -> None:
    """행 수 판정 3축 (Y-4).

    | 키 | 본다 | 언제 쓰나 |
    |---|---|---|
    | `row_count` | 응답의 합계 | **단일 DB 턴에서만** |
    | `row_count_total` | 응답의 합계 | 팬아웃 합계를 재고 싶을 때 |
    | `row_count_per_db` | **DB 별** 행 수 | 팬아웃 턴의 기본 축 |

    D-02("100건 조회")는 b0 100 + cm_gp 100 + cm_yd 100 = 300 을 냈고 **각 DB 는 정확히
    100행**이었는데, 단언이 `row_count.max: 100` 이라 불합격으로 세어졌다. 판정 축이 틀렸다.
    그래서 멀티 DB 턴에서 `row_count` 는 **불합격이 아니라 보류**다 - 틀린 축으로 재단하지 않는다.
    """
    per_db = obs.row_counts_by_db
    multi_db = len(per_db) > 1 or len(set(obs.db_ids)) > 1

    if "row_count" in expect:
        if multi_db:
            manual.add(
                f"row_count 는 단일 DB 턴 전용이다 - 이 턴은 {len(per_db) or len(set(obs.db_ids))}개 DB "
                f"팬아웃이라 합계({obs.row_count})와 per-DB 기대값을 비교하게 된다. "
                f"row_count_per_db / row_count_total 로 선언할 것 "
                f"(DB별 실측: {per_db or '미관측'})",
                "fanout",
            )
        else:
            _check_row_count(expect["row_count"], obs.row_count, failures)

    _check_row_count(expect.get("row_count_total"), obs.row_count, failures, "row_count_total")

    spec = expect.get("row_count_per_db")
    if isinstance(spec, dict):
        if not per_db:
            # 감사 로그 tail 이 없으면 DB 별 행 수를 모른다 - 통과로도 불합격으로도 세지 않는다.
            manual.add(
                "row_count_per_db 를 확인하지 못했다 - DB 별 행 수는 감사 로그 "
                "`query_executed` 에서만 나온다(모의 실행·tail 미가동이면 관측 0건)",
                "unobservable",
            )
        else:
            for db_id, count in sorted(per_db.items()):
                offenders: list[Failure] = []
                _check_row_count(spec, count, offenders, "row_count_per_db")
                for failure in offenders:
                    failures.append(Failure(failure.key, failure.expected, f"{db_id}={count}"))


def option_labels(clarification: dict[str, Any]) -> list[str]:
    """선택지의 표시 이름. 존 선택은 `options`, 폼필은 `fields` 다."""
    labels: list[str] = []
    for item in clarification.get("options") or clarification.get("fields") or []:
        if isinstance(item, dict):
            picked = next(
                (str(item[k]) for k in ("name", "label", "value", "db_id", "id") if item.get(k)),
                None,
            )
            labels.append(picked if picked is not None else str(item))
        elif item is not None:
            labels.append(str(item))
    return labels


def _option_haystack(clarification: dict[str, Any]) -> set[str]:
    """선택지가 담은 모든 스칼라 값. `options_contains` 는 이것과 대조한다."""
    values: set[str] = set()
    for item in clarification.get("options") or clarification.get("fields") or []:
        if isinstance(item, dict):
            values.update(str(v) for v in item.values() if isinstance(v, (str, int, float)))
        elif item is not None:
            values.add(str(item))
    return values


def _check_clarification(spec: Any, obs: Observation, failures: list[Failure]) -> None:
    if not isinstance(spec, dict):
        return
    actual = obs.clarification or obs.form_fill_clarification
    if not actual:
        failures.append(Failure("clarification", spec, None))
        return
    if "kind" in spec and actual.get("kind") != spec["kind"]:
        failures.append(Failure("clarification.kind", spec["kind"], actual.get("kind")))
    labels = option_labels(actual)
    if "options_len" in spec and len(labels) != int(spec["options_len"]):
        # **깨지기 쉬운 단언이다.** 열이 하나 늘면 제품이 옳아도 불합격이 된다 -
        # I-01~I-06 6턴 + 후속 13턴 skip = 19턴이 정확히 이것으로 무효가 됐다(Y-1).
        # 선택지의 "개수"가 계약인 경우에만 쓰고, 그 밖에는 options_contains 를 쓴다.
        failures.append(Failure("clarification.options_len", spec["options_len"], len(labels)))
    if "options_contains" in spec:
        haystack = _option_haystack(actual)
        for needle in spec["options_contains"] or []:
            if str(needle) not in haystack:
                failures.append(Failure("clarification.options_contains", needle, labels))


def _read_xlsx(path: Path) -> tuple[Optional[dict[str, list[list[Any]]]], Optional[str]]:
    """산출 xlsx를 시트별 전 행으로 읽는다. 미리보기 일부가 아니라 전 칼럼을 본다(V7)."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return None, "openpyxl 미설치 - 산출물 단언은 수동 검토로 남긴다"
    try:
        book = load_workbook(path, data_only=True)
    except Exception as exc:  # 파일이 깨진 것도 판정 재료다
        return None, f"xlsx 열기 실패: {type(exc).__name__}: {exc}"
    sheets = {
        name: [list(row) for row in book[name].iter_rows(values_only=True)]
        for name in book.sheetnames
    }
    return sheets, None


def _empty_by_column(
    target_rows: list[list[Any]], header: list[str], columns: list[str]
) -> dict[str, str]:
    """열별 공란 수 `"2338/2338"`. 어느 열이 산출물을 깎았는지 한 눈에 보이게 한다(Y-3)."""
    total = max(0, len(target_rows) - 1)
    counts: dict[str, str] = {}
    for column in columns:
        if column not in header:
            counts[column] = f"헤더에 없음/{total}"
            continue
        index = header.index(column)
        empty = sum(
            1 for row in target_rows[1:]
            if index >= len(row) or row[index] in (None, "")
        )
        counts[column] = f"{empty}/{total}"
    return counts


#: 2단 머리글의 상위·하위 이름 구분자(plans/122 H-4 `file.header_rows`) — `상위/하위`.
HEADER_JOIN = "/"


def _header_rows_of(spec: dict[str, Any]) -> list[int]:
    """`file.header_row: N`(1-based) · `file.header_rows: [N, …]` → 머리글 행 번호 목록.

    선언이 없으면 [1] 이다.
    """
    if spec.get("header_rows"):
        return [int(n) for n in spec["header_rows"]]
    return [int(spec.get("header_row") or 1)]


def _header_text(value: Any) -> str:
    """머리글 칸 문구 — 공백(줄바꿈 포함)을 한 칸으로 접고 양끝을 뗀다."""
    return " ".join(_cell(value).split())


def _merged_ranges(path: Path, sheet_name: str | None) -> tuple[list[Any] | None, str | None]:
    """시트의 병합 범위 목록(openpyxl `CellRange`). 읽지 못하면 (None, 사유)."""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return None, "openpyxl 미설치"
    try:
        book = load_workbook(path, data_only=True)
    except Exception as exc:  # `_read_xlsx` 와 같은 파일이라 여기까지 오면 드물다
        return None, f"xlsx 열기 실패: {type(exc).__name__}: {exc}"
    sheet = book[sheet_name] if sheet_name in book.sheetnames else book.worksheets[0]
    return list(sheet.merged_cells.ranges), None


def _header_view(
    path: Path, sheet_name: str | None, rows: list[list[Any]] | None, spec: dict[str, Any],
) -> tuple[list[list[Any]] | None, dict[str, Any] | None]:
    """(머리글 1행 + 데이터 행, 문제) — `file.header_row`·`header_rows`(plans/122 H-4).

    머리글 이름 규칙(열마다):
      1. 머리글 행을 위에서 아래로 읽는다. 빈 칸이 병합 범위 안이면 그 범위 좌상단 값을 쓴다
         (가로 병합 `E5:J5` 는 E~J 가 같은 상위 이름을 갖고 · 세로 병합 `B5:B6` 은 6행도
         5행 값이다).
      2. 칸 문구는 공백(줄바꿈 포함)을 한 칸으로 접는다 —
         `설치장소\\n(주센터, …)` → `설치장소 (주센터, …)`.
      3. 빈 값과 바로 위와 같은 값(세로 병합)을 빼고 `상위/하위` 로 잇는다(`HEADER_JOIN`).
         하위가 없으면 상위만, 상위가 없으면 하위만이다 — `월중평균사용률(최근 6개월간)/M` ·
         `제조사(모델명)` · `처리능력/(TPMC)` · `서버위치/설치장소 (주센터, 재해복구센터 등)`.
      4. 이름이 비는 열은 빈 문자열로 자리만 지킨다(열 번호 정렬 유지).
    데이터 행은 마지막 머리글 행 다음부터다. `header_row: N` 은 `header_rows: [N]` 과 같다.
    """
    if rows is None:
        return None, {"reason": "대상 시트가 없다"}
    numbers = _header_rows_of(spec)
    if max(numbers) > len(rows):
        return None, {"reason": "머리글 행이 시트 행 수를 넘는다", "sheet_rows": len(rows)}
    ranges, problem = _merged_ranges(path, sheet_name)
    if ranges is None:
        return None, {"reason": problem}
    anchors: dict[tuple[int, int], Any] = {}
    for cells in ranges:
        top_left = (_at(rows[cells.min_row - 1], cells.min_col - 1)
                    if cells.min_row <= len(rows) else None)
        for row_no in range(cells.min_row, cells.max_row + 1):
            if row_no in numbers:
                for col_no in range(cells.min_col, cells.max_col + 1):
                    anchors[(row_no, col_no)] = top_left
    width = max((len(rows[n - 1]) for n in numbers), default=0)
    header: list[Any] = []
    for col_no in range(1, width + 1):
        parts: list[str] = []
        for row_no in numbers:
            value = _at(rows[row_no - 1], col_no - 1)
            if _cell(value) == "":
                value = anchors.get((row_no, col_no))
            text = _header_text(value)
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        header.append(HEADER_JOIN.join(parts))
    return [header, *rows[max(numbers):]], None


def _check_file(
    spec: Any, obs: Observation, failures: list[Failure], manual: _Holds,
    upload: str | None = None,
) -> None:
    if not isinstance(spec, dict):
        return
    if not obs.artifacts:
        failures.append(Failure("file", spec, "산출물 없음"))
        return
    path = Path(obs.artifacts[0])
    if isinstance(spec.get("docx"), dict):
        # plans/122 H-3 - docx 선언은 docx 산출물만 본다
        # (xlsx 하위 키와 함께 쓰지 않는다 · 로더 검사).
        if path.suffix.lower() != ".docx":
            failures.append(Failure("file.docx", "docx 산출물", path.name))
        else:
            _check_docx(spec["docx"], path, upload, failures, manual)
        return
    if path.suffix.lower() != ".xlsx":
        manual.add(f"{path.name}: xlsx 가 아니라 자동 칼럼 검증 대상이 아니다", "unobservable")
        return
    sheets, reason = _read_xlsx(path)
    if sheets is None:
        manual.add(reason or "산출물 판독 불가", "unobservable")
        return

    wanted_sheets = spec.get("sheets") or []
    for name in wanted_sheets:
        if name not in sheets:
            failures.append(Failure("file.sheets", name, sorted(sheets)))

    target_rows = sheets.get(wanted_sheets[0]) if wanted_sheets else next(iter(sheets.values()), [])
    if "header_row" in spec or "header_rows" in spec:
        # plans/122 H-4 — 머리글이 첫 행이 아닌 양식(제목 행 · 2단 머리글).
        # 선언하지 않으면 이 분기를 타지 않는다(종전 판정과 바이트 동일).
        sheet_name = wanted_sheets[0] if wanted_sheets else next(iter(sheets), None)
        view, problem = _header_view(path, sheet_name, target_rows, spec)
        if view is None:
            # 선언한 시트가 없으면 `file.sheets` 가 이미 불합격이다
            # - 머리글 불합격을 겹쳐 싣지 않는다.
            if target_rows is not None:
                failures.append(Failure("file.header_rows",
                                        spec.get("header_rows") or spec.get("header_row"), problem))
            if spec.get("style_preserved"):
                _check_xlsx_style(path, upload, failures, manual)
            return
        target_rows = view
    # 선언한 시트가 없으면 `sheets.get` 이 None 이다 — `file.sheets` 는 위에서 이미 불합격이고, 빈 표로
    # 이어 판정한다(종전에는 `filled_rows` 선언 시 None 인덱싱 예외로 판정 전체가 죽었다 · 122 h-judge 발견).
    target_rows = target_rows or []
    header = [str(c) for c in (target_rows[0] if target_rows else []) if c is not None]
    for column in spec.get("columns") or []:
        if column not in header:
            failures.append(Failure("file.columns", column, header))

    filled = spec.get("filled_rows")
    if isinstance(filled, dict) and "min" in filled:
        # 채워져야 하는 열. `filled_columns` 가 있으면 그것, 없으면 선언한 `columns` 전부에서
        # `optional_columns`(공란이 정답인 자유 서술 열)를 뺀다.
        #
        # **`optional_columns` 는 기본값이 없다** - 선언하지 않으면 종전과 비트 동일하게 동작한다.
        # '비고' 같은 열을 공란 정답으로 볼지 `[미작성 항목]` 안내 대상으로 볼지는 사람 확정
        # 사항이라(G-4 · plans/96 §8) 카탈로그에 미리 적어 두지 않는다 - 여기서는 **표현 수단만**
        # 마련한다.
        optional = {str(c) for c in (spec.get("optional_columns") or [])}
        declared = [str(c) for c in (spec.get("filled_columns") or spec.get("columns") or [])]
        wanted_columns = [c for c in declared if c not in optional]
        if wanted_columns:
            # **전 칼럼을 본다**(V7). 선언한 칼럼 중 하나라도 비어 있으면 그 행은 채워진 것이
            # 아니다 - "아무 칸이나 차 있으면 통과"로 세면 일부만 채운 산출물이 합격한다.
            # 미리보기 일부가 아니라 실제 산출 파일의 전 칼럼 확인(Known Mistakes).
            indexes = [header.index(c) for c in wanted_columns if c in header]
            data_rows = [
                row for row in target_rows[1:]
                if indexes and all(
                    i < len(row) and row[i] not in (None, "") for i in indexes
                )
            ]
        else:
            data_rows = [r for r in target_rows[1:] if any(c not in (None, "") for c in r)]
        if len(data_rows) < int(filled["min"]):
            # Y-3: **어느 열이 몇 행 비었는지**를 싣는다. 「기대 1 실제 0」은 *빈 파일*로
            # 읽히는데 H-04 의 실체는 2338행 중 5열이 2337행 채워지고 '비고' 한 열만
            # 전 행 공란인 정상 산출물이었다. 열 단위 수치가 없으면 산출물을 직접
            # 열어보기 전에는 진단이 불가능하다.
            failures.append(Failure(
                "file.filled_rows.min", filled["min"],
                {
                    "filled_rows": len(data_rows),
                    "data_rows": max(0, len(target_rows) - 1),
                    "empty_by_column": _empty_by_column(target_rows, header, wanted_columns),
                    "optional_columns": sorted(optional) or None,
                },
            ))

    # plans/122 H-4 - 새 하위 키는 선언했을 때만 본다(없으면 종전 판정과 바이트 동일).
    _check_xlsx_values(spec, target_rows, failures)
    if spec.get("style_preserved"):
        _check_xlsx_style(path, upload, failures, manual)


def _at(row: list[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _check_xlsx_values(
    spec: dict[str, Any], target_rows: list[list[Any]] | None, failures: list[Failure],
) -> None:
    """xlsx 값 단언(plans/122 H-4) — value_range · unique_by · columns_differ · empty_columns ·
    column_equals. 머리글은 빈 칸도 자리를 지키게 읽고(열 번호 정렬), 데이터 행은 빈 행을 뺀다.
    불합격 상세에는 개수와 예시 값 최대 3개만 싣는다(G-4). 선언한 시트가 없으면(`file.sheets`
    불합격) 빈 표로 본다. 첫 행은 `file.header_row(s)` 선언이 있으면 `_header_view` 가 만든
    머리글이다."""
    target_rows = target_rows or []
    header = [_cell(c) for c in (target_rows[0] if target_rows else [])]
    rows = [list(r) for r in target_rows[1:] if any(c not in (None, "") for c in r)]
    failures.extend(_value_range_failures("file", spec.get("value_range") or {}, header, rows, {}))
    if spec.get("unique_by"):
        failure = _unique_by_failure("file", spec["unique_by"], header, rows, {})
        if failure:
            failures.append(failure)
    for pair in spec.get("columns_differ") or []:
        # 두 열이 **전 행에서 같으면**(사본) 불합격이다. 행 단위로 다름을 요구하면 name=hostname 인
        # 정상 서버에서 오탐한다(122 §9.1 H-04) - 잘못된 매핑(D-148)은 열 전체를 사본으로 만든다.
        if pair[0] not in header or pair[1] not in header:
            failures.append(Failure("file.columns_differ", pair,
                                    {"missing": "헤더에 없음", "header": header}))
            continue
        left, right = header.index(pair[0]), header.index(pair[1])
        compared = [(_cell(_at(r, left)), _cell(_at(r, right))) for r in rows
                    if _cell(_at(r, left)) and _cell(_at(r, right))]
        equal = sum(1 for a, b in compared if a == b)
        if not compared or equal == len(compared):
            failures.append(Failure("file.columns_differ", pair,
                                    {"compared_rows": len(compared), "equal_rows": equal}))
    for column in spec.get("empty_columns") or []:
        # 공란이 **정답**인 열 - 채워지면 불합격이다(`optional_columns` 는 공란을 허용할 뿐이다).
        if column not in header:
            failures.append(Failure("file.empty_columns", column,
                                    {"missing": "헤더에 없음", "header": header}))
            continue
        index = header.index(column)
        filled = sum(1 for r in rows if _cell(_at(r, index)))
        if filled:
            failures.append(Failure("file.empty_columns", column, {
                "filled_rows": filled,
                "empty_by_column": _empty_by_column(target_rows, header, [column]),
            }))
    for column, value in (spec.get("column_equals") or {}).items():
        if column not in header:
            failures.append(Failure("file.column_equals", {column: value},
                                    {"missing": "헤더에 없음", "header": header}))
            continue
        index = header.index(column)
        others = [_cell(_at(r, index)) for r in rows if _cell(_at(r, index)) != _cell(value)]
        if others or not rows:
            failures.append(Failure("file.column_equals", {column: value}, {
                "rows": len(rows), "mismatched_rows": len(others),
                "examples": list(dict.fromkeys(others))[:_EXAMPLE_LIMIT],
            }))


def _upload_path(upload: str | None) -> Path | None:
    """업로드 원본 경로(저장소 루트 기준 상대 경로 허용). 없으면 None."""
    if not upload:
        return None
    path = Path(upload)
    path = path if path.is_absolute() else REPO_ROOT / path
    return path if path.exists() else None


def _column_letter(index: int) -> str:
    """1 → A · 27 → AA (openpyxl `get_column_letter` 와 같다)."""
    letters = ""
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters


def _custom_widths(sheet: Any) -> dict[str, float]:
    """시트의 사용자 지정 열 너비 {열 문자: 너비}. 범위(min~max)로 묶인 차원은 열마다 편다."""
    widths: dict[str, float] = {}
    for key, dim in sheet.column_dimensions.items():
        if not dim.customWidth or not dim.width:
            continue
        if not dim.min:
            widths[str(key)] = float(dim.width)
            continue
        for index in range(dim.min, (dim.max or dim.min) + 1):
            widths[_column_letter(index)] = float(dim.width)
    return widths


def _check_xlsx_style(
    path: Path, upload: str | None, failures: list[Failure], manual: _Holds,
) -> None:
    """`file.style_preserved`(H-4) — 업로드 원본의 사용자 지정 열 너비·병합 셀이 그대로인가."""
    original = _upload_path(upload)
    if original is None or original.suffix.lower() != ".xlsx":
        manual.add("file.style_preserved 를 확인하지 못했다 - 대조할 업로드 원본 xlsx 가 없다",
                   "unobservable")
        return
    try:
        from openpyxl import load_workbook
    except ImportError:
        manual.add("openpyxl 미설치 - file.style_preserved 는 수동 검토로 남긴다", "unobservable")
        return
    try:
        before, after = load_workbook(original), load_workbook(path)
    except Exception as exc:  # 깨진 파일은 판독 불가로 남긴다(`_read_xlsx` 와 같다)
        manual.add("file.style_preserved 를 확인하지 못했다 - 열기 실패: "
                   f"{type(exc).__name__}: {exc}", "unobservable")
        return
    missing_sheets = [name for name in before.sheetnames if name not in after.sheetnames]
    widths: dict[str, list[float | None]] = {}
    merged_missing: list[str] = []
    for name in before.sheetnames:
        if name not in after.sheetnames:
            continue
        have = _custom_widths(after[name])
        for letter, width in _custom_widths(before[name]).items():
            if have.get(letter) is None or abs(float(have[letter]) - width) > 0.01:
                widths[f"{name}!{letter}"] = [width, have.get(letter)]
        kept = {str(r) for r in after[name].merged_cells.ranges}
        merged = sorted(str(x) for x in before[name].merged_cells.ranges)
        merged_missing += [f"{name}!{r}" for r in merged if r not in kept]
    if missing_sheets or widths or merged_missing:
        failures.append(Failure("file.style_preserved", True, {
            "sheets_missing": missing_sheets, "widths_changed": widths,
            "merged_missing": merged_missing,
        }))


#: docx 자리 표시(`{{…}}`) - 양식 파서·작성기(`src/document/word_parser.py`
#: `_PLACEHOLDER_PATTERN`)와 같은 식.
_PLACEHOLDER_RE = re.compile(r"\{\{.+?\}\}")


def _docx_texts(doc: Any) -> list[str]:
    """본문·표(중첩 포함)·자기 정의가 있는 머리글/바닥글의 문단 문구."""
    texts = [p.text for p in doc.paragraphs]

    def walk(tables: Any) -> None:
        for table in tables:
            for row in table.rows:
                for cell in row.cells:
                    texts.extend(p.text for p in cell.paragraphs)
                    walk(cell.tables)

    walk(doc.tables)
    for section in doc.sections:
        for part in (section.header, section.footer):
            if not part.is_linked_to_previous:
                texts.extend(p.text for p in part.paragraphs)
                walk(part.tables)
    return texts


def _docx_style_ids(doc: Any) -> tuple[list[Any], list[tuple[Any, list[list[list[Any]]]]]]:
    """(본문 문단 스타일 ID 목록, [(표 스타일 ID, 행별 셀별 문단 스타일 ID)])."""
    def sid(style: Any) -> Any:
        return getattr(style, "style_id", None)

    paragraphs = [sid(p.style) for p in doc.paragraphs]
    tables = [
        (sid(t.style),
         [[[sid(p.style) for p in cell.paragraphs] for cell in row.cells] for row in t.rows])
        for t in doc.tables
    ]
    return paragraphs, tables


def _check_docx(
    spec: dict[str, Any], path: Path, upload: str | None,
    failures: list[Failure], manual: _Holds,
) -> None:
    """`.docx` 산출 단언(plans/122 H-3) — 자리 표시 잔존 · 표 행 수·첫 행 · 원본 대비 스타일 ID."""
    try:
        from docx import Document
    except ImportError:
        manual.add("python-docx 미설치 - docx 단언은 수동 검토로 남긴다(`document` extra)",
                   "unobservable")
        return
    try:
        doc = Document(str(path))
    except Exception as exc:  # 깨진 파일은 판독 불가로 남긴다(`_read_xlsx` 와 같다)
        manual.add(f"docx 열기 실패: {type(exc).__name__}: {exc}", "unobservable")
        return

    if spec.get("no_placeholders"):
        left = [m for text in _docx_texts(doc) for m in _PLACEHOLDER_RE.findall(text)]
        if left:
            failures.append(Failure("file.docx.no_placeholders", 0, {
                "remaining": len(left), "examples": list(dict.fromkeys(left))[:_EXAMPLE_LIMIT],
            }))

    for item in spec.get("tables") or []:
        index = int(item["index"])
        if index >= len(doc.tables):
            failures.append(Failure("file.docx.tables", item, {"tables": len(doc.tables)}))
            continue
        table = doc.tables[index]
        if "min_rows" in item and len(table.rows) < int(item["min_rows"]):
            failures.append(Failure("file.docx.tables.min_rows", item["min_rows"],
                                    {"index": index, "rows": len(table.rows)}))
        if "first_row" in item:
            wanted = [str(text).strip() for text in item["first_row"]]
            actual = [cell.text.strip() for cell in table.rows[0].cells] if table.rows else []
            if actual != wanted:
                wrong = [{"cell": i, "actual": a}
                         for i, (a, w) in enumerate(zip(actual, wanted)) if a != w]
                failures.append(Failure("file.docx.tables.first_row", wanted, {
                    "index": index, "cells": len(actual), "mismatched": wrong[:_EXAMPLE_LIMIT],
                }))

    if spec.get("styles_preserved"):
        original = _upload_path(upload)
        if original is None or original.suffix.lower() != ".docx":
            manual.add("file.docx.styles_preserved 를 확인하지 못했다"
                       " - 대조할 업로드 원본 docx 가 없다", "unobservable")
            return
        try:
            before = _docx_style_ids(Document(str(original)))
        except Exception as exc:
            manual.add(f"file.docx.styles_preserved 를 확인하지 못했다 - 원본 열기 실패: "
                       f"{type(exc).__name__}: {exc}", "unobservable")
            return
        detail = _docx_style_diff(before, _docx_style_ids(doc))
        if detail:
            failures.append(Failure("file.docx.styles_preserved", True, detail))


def _docx_style_diff(
    before: tuple[list[Any], list[tuple[Any, list[list[list[Any]]]]]],
    after: tuple[list[Any], list[tuple[Any, list[list[list[Any]]]]]],
) -> dict[str, Any]:
    """원본 대비 스타일 ID 차이. 채움은 본문 문단을 늘리지 않고, 표에 더한 행은 원본 첫 데이터 행
    (없으면 머리글 행)의 스타일을 복제해야 한다 - 그 행과 대조한다."""
    detail: dict[str, Any] = {}
    (before_pars, before_tables), (after_pars, after_tables) = before, after
    if before_pars != after_pars:
        changed = sum(1 for a, b in zip(before_pars, after_pars) if a != b)
        detail["paragraphs"] = {"original": len(before_pars), "output": len(after_pars),
                                "changed": changed + abs(len(before_pars) - len(after_pars))}
    tables: dict[str, Any] = {}
    for index, (table_style, rows) in enumerate(before_tables):
        if index >= len(after_tables):
            tables[str(index)] = "산출물에 없음"
            continue
        out_style, out_rows = after_tables[index]
        reference = rows[1] if len(rows) > 1 else (rows[0] if rows else [])
        mismatched = [r for r, row in enumerate(out_rows)
                      if row != (rows[r] if r < len(rows) else reference)]
        entry: dict[str, Any] = {}
        if out_style != table_style:
            entry["table_style"] = [table_style, out_style]
        if mismatched:
            entry.update(rows_mismatched=len(mismatched), first_mismatch_row=mismatched[0])
        if entry:
            tables[str(index)] = entry
    if tables:
        detail["tables"] = tables
    return detail


#: SQL 안의 날짜 리터럴. `2026-07-01` · `20260701` · `202607`(월 파티션) 세 표기를 본다.
#: (plans/122 T-9) 시간 통계 `stat_date` 리터럴 `2026070110`(YYYYMMDDHH · 10자리)은 그 날짜로
#: 읽는다 — 종전에는 어느 갈래에도 걸리지 않아 리터럴 없음으로 샜다. `TIMESTAMP '… 10:00:00'`
#: 의 시각은 종전대로 날짜 부분만 읽는다.
_DATE_LITERAL_RE = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{4})(\d{2})(\d{2})\b|\b(\d{4})(\d{2})\b"
    r"|\b(\d{4})(\d{2})(\d{2})(\d{2})\b"
)


def sql_period_bounds(sqls: list[str]) -> tuple[Optional[date], Optional[date]]:
    """SQL 이 실제로 건드린 기간의 (최소 경계, 최대 경계). 날짜가 없으면 (None, None).

    월 파티션 리터럴(`'202607'`)은 **경계 두 개**로 편다 - 그 달을 요구한 것은 7/1 부터
    8/1 까지를 요구한 것이다. 표기가 아니라 의미를 본다.
    """
    bounds: list[date] = []
    for sql in sqls:
        for match in _DATE_LITERAL_RE.finditer(sql):
            try:
                if match.group(1):
                    bounds.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
                elif match.group(4):
                    bounds.append(date(int(match.group(4)), int(match.group(5)), int(match.group(6))))
                elif match.group(9):
                    if int(match.group(12)) > 23:
                        continue  # 시가 아니면 날짜·시 리터럴이 아니다(식별자 등)
                    bounds.append(date(int(match.group(9)), int(match.group(10)),
                                       int(match.group(11))))
                else:
                    year, month = int(match.group(7)), int(match.group(8))
                    start = date(year, month, 1)
                    bounds.extend([start, _next_month(start)])
            except ValueError:
                # 날짜가 아닌 8자리 숫자(식별자 등)는 경계가 아니다.
                continue
    return (min(bounds), max(bounds)) if bounds else (None, None)


def _next_month(day: date) -> date:
    return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)


#: `rewrite.gate` 의 선언값. `pass_through` = 전 레코드가 게이트 통과(원문 무수정),
#: `rewritten` = 한 레코드 이상이 재작성·병기 대상.
REWRITE_GATE_EXPECTATIONS: frozenset[str] = frozenset({"pass_through", "rewritten"})


def _check_rewrite(
    spec: Any, obs: Observation, failures: list[Failure], manual: _Holds,
) -> None:
    """Y-11 `rewrite.gate` · Y-12 `rewrite.slots_preserved` (plans/94 §19.2 · plans/107).

    **관측하지 못한 것을 판정하지 않는다** — 레코드가 없으면(서버가 INTENT_FRAME_ENABLED 가
    아니거나 SQL 생성 노드를 지나지 않은 턴) 불합격이 아니라 보류다. O-b(`llm_calls`)와 같은
    구조다: 제품이 싣지 않으면 하네스는 모른다.

    - `gate`: 게이트 판정만 본다. 통과 판정일 때 프롬프트 바이트가 불변인 것은 코드
      계약이며 `tests/test_nodes/test_plan107_intent_frame.py`가 고정한다.
    - `slots_preserved`: 검증 결과가 전부 `pass` 여야 한다. 실패 메시지는 채널별 사유를
      싣는다(Y-3 과 같은 이유 — "기대 1 실제 0" 형 메시지는 원인을 못 짚는다).
    """
    if not isinstance(spec, dict) or not spec:
        return
    traces = [t for t in obs.rewrite_traces if isinstance(t, dict)]
    if not traces:
        manual.add(
            f"rewrite {sorted(spec)} 를 확인하지 못했다 - 재작성 감사 레코드가 없다"
            "(서버 INTENT_FRAME_ENABLED·SQL 생성 노드 통과 여부 확인)",
            "unobservable",
        )
        return

    gate = spec.get("gate")
    if gate is not None:
        reasons = [str((t.get("gate") or {}).get("reason")) for t in traces]
        needed = [bool((t.get("gate") or {}).get("needed")) for t in traces]
        if gate not in REWRITE_GATE_EXPECTATIONS:
            failures.append(Failure("rewrite.gate", sorted(REWRITE_GATE_EXPECTATIONS), gate))
        elif gate == "pass_through" and any(needed):
            failures.append(Failure("rewrite.gate", gate, reasons))
        elif gate == "rewritten" and not any(needed):
            failures.append(Failure("rewrite.gate", gate, reasons))

    if spec.get("slots_preserved"):
        verified = [(t.get("consumer"), t.get("verify") or {}) for t in traces]
        if not any(v for _, v in verified):
            manual.add(
                "rewrite.slots_preserved 를 확인하지 못했다 - 검증 결과가 비었다"
                "(서버 REWRITE_VERIFY_MODE=shadow 필요 · 재작성이 없던 턴은 검증 대상이 아니다)",
                "unobservable",
            )
        else:
            broken = {
                f"{consumer}.{channel}": result
                for consumer, results in verified
                for channel, result in results.items()
                if result != "pass"
            }
            if broken:
                slots = traces[0].get("slots") or {}
                failures.append(Failure(
                    "rewrite.slots_preserved", "pass",
                    {"broken": broken, "frame_slots": sorted(slots)},
                ))


#: 그래프 밖 사전 게이트(존 역질문 등)로 끝난 턴의 계획 경로 코드 - 서버가 계획 없이 싣는다
#: (`src/api/routes/query.py` `_pre_gate_plan_summary` · plans/121 TP-0.1).
PRE_GATE_PLAN_PATH = "pre_gate"


def _plan_edges(tasks: list[dict[str, Any]]) -> list[list[str]]:
    """계획 요약의 `input_from` 간선을 [생산 담당, 소비 담당] 쌍으로 편다(등장 순서 · 중복 제거)."""
    agent_of = {task.get("id"): str(task.get("agent")) for task in tasks}
    edges: list[list[str]] = []
    for task in tasks:
        for source in task.get("input_from") or []:
            if source in agent_of:
                edge = [agent_of[source], str(task.get("agent"))]
                if edge not in edges:
                    edges.append(edge)
    return edges


def _check_plan(
    spec: Any, obs: Observation, failures: list[Failure], manual: _Holds,
) -> None:
    """계획 구조 단언 `plan` (plans/121 TP-0.3) — 2단 계획 요약(`done.plan_summary`)을 본다.

    **관측하지 못한 것을 판정하지 않는다**(`rewrite` 와 같은 구조). 계획 요약은 2단만 싣는다 —
    1·3단 실행·옛 서버·모의 실행에는 요약이 없고, 그래프 밖 사전 게이트(존 역질문 등)로 끝난
    턴은 `plan_path=pre_gate` 라 계획이 없다. 이때는 불합격이 아니라 보류이고, 보류 사유에 기대
    구조를 그대로 적어 사람이 대신 볼 것을 남긴다.

    요약은 **이번 턴에 실행된 최종 계획**이다 — 재계획이 더한 task 도 들어 있다. 그래서 하한
    (`min_tasks`·`agents`·`edges`)은 재계획으로 채워져도 통과하고, 첫 분해만 보려면
    `replan_max: 0` 을 함께 선언한다. `agents` 는 개수까지 세는 포함 관계이고 순서를 보지 않는다
    — 독립 task 의 나열 순서는 LLM 이 정한다. 순서가 계약이면 `edges`(input_from 간선)로 쓴다.
    """
    if not isinstance(spec, dict) or not spec:
        return
    wanted = json.dumps(spec, ensure_ascii=False, sort_keys=True)
    summary = obs.plan_summary if isinstance(obs.plan_summary, dict) else None
    if summary is None:
        manual.add(
            f"plan {wanted} 를 확인하지 못했다 - 계획 요약(done.plan_summary)이 없다"
            "(2단 밖 실행 단 · 옛 서버 · 모의 실행)",
            "unobservable",
        )
        return
    path = summary.get("plan_path")
    if path == PRE_GATE_PLAN_PATH:
        manual.add(
            f"plan {wanted} 를 확인하지 못했다 - 사전 게이트에서 끝난 턴이라 계획이 없다"
            f"(plan_path={path})",
            "unobservable",
        )
        return

    tasks = [task for task in summary.get("tasks") or [] if isinstance(task, dict)]
    count = summary.get("task_count")
    if not isinstance(count, int) or isinstance(count, bool):
        count = len(tasks)
    agents = [str(task.get("agent")) for task in tasks]
    shape = {"task_count": count, "agents": agents}
    if "min_tasks" in spec and count < int(spec["min_tasks"]):
        failures.append(Failure("plan.min_tasks", spec["min_tasks"], shape))
    if "max_tasks" in spec and count > int(spec["max_tasks"]):
        failures.append(Failure("plan.max_tasks", spec["max_tasks"], shape))
    if spec.get("agents") and Counter(str(a) for a in spec["agents"]) - Counter(agents):
        failures.append(Failure("plan.agents", spec["agents"], agents))
    edges = _plan_edges(tasks)
    for source, target in (spec.get("edges") or []):
        if not any(
            str(source) in (PLAN_ANY_AGENT, have[0]) and str(target) in (PLAN_ANY_AGENT, have[1])
            for have in edges
        ):
            failures.append(Failure("plan.edges", [source, target], edges))
    allowed = spec.get("plan_path")
    if allowed is not None:
        allowed_paths = [allowed] if isinstance(allowed, str) else [str(p) for p in allowed]
        if path not in allowed_paths:
            failures.append(Failure("plan.plan_path", allowed_paths, path))
    if "replan_max" in spec:
        replans = summary.get("replan_count")
        if not isinstance(replans, int) or isinstance(replans, bool):
            manual.add(
                f"plan.replan_max={spec['replan_max']} 를 확인하지 못했다"
                " - 계획 요약에 재계획 횟수가 없다",
                "unobservable",
            )
        elif replans > int(spec["replan_max"]):
            failures.append(Failure("plan.replan_max", spec["replan_max"], replans))


def _check_period(
    spec: Any, sqls: list[str], failures: list[Failure], manual: _Holds
) -> None:
    """`period_covers: {from, to}` - **표기가 아니라 기간**을 본다(Y-5).

    D-03 은 기대 `(?i)202607` · 실제
    `ctime >= TIMESTAMP '2026-07-01' AND ctime < TIMESTAMP '2026-08-01'` 이었다.
    **기간은 정확한데 표기가 달라서** 불합격이 났다. 같은 함정이 `202601`·`202606`·
    `'20\\d{4}'` 계열 단언 전반에 있다.

    `to` 는 **배타 경계**다(`< to`). 포함 경계 표기(`<= to-1`)도 같은 기간이므로 통과시킨다.
    """
    if not isinstance(spec, dict) or not spec.get("from") or not spec.get("to"):
        return
    try:
        wanted_from = date.fromisoformat(str(spec["from"]))
        wanted_to = date.fromisoformat(str(spec["to"]))
    except ValueError:
        failures.append(Failure("period_covers", spec, "from/to 가 YYYY-MM-DD 가 아니다"))
        return
    if not sqls:
        manual.add(f"period_covers {spec} 를 확인하지 못했다 - 실행 SQL 을 관측하지 못했다",
                   "unobservable")
        return
    low, high = sql_period_bounds(sqls)
    if low is None or high is None:
        failures.append(Failure("period_covers", spec, "SQL 에 날짜 리터럴이 없다"))
        return
    if low > wanted_from or high < wanted_to - timedelta(days=1):
        failures.append(Failure(
            "period_covers", spec,
            {"sql_period": f"{low.isoformat()}~{high.isoformat()}"},
        ))


# --- 상대 기간 `period_covers` (plans/122 H-2) ------------------------------------------

#: 상대 기간 판정이 결과 행(H-1)에서 찾는 기간 열 — 이름 **정확 일치**(대소문자 무시) ·
#: 앞일수록 우선. 근거(run 20260923-103638 · 20260923-140539 실행 SQL 별칭 실측):
#: `alarm_time`(38회 · `a.ctime` 별칭) · `stat_month`(30회 · `TO_DATE(s.stat_date || '01', …)`) ·
#: `stat_date`(8회 · 원 컬럼) · `created_time`(D-06 · `a.ctime` 별칭) · `month`(2회).
#: 원 컬럼 `ctime`(알람 발생 시각)과 계획서 표기 `yyyymm`(§3.3 H-2)을 더했다.
#: 부분 일치는 쓰지 않는다 — `cpu_avg_month`·`months_over_40`(값 열) · `도입일자`(자산 날짜)를
#: 조회 기간으로 오인한다.
RESULT_PERIOD_COLUMNS: tuple[str, ...] = (
    "stat_month", "stat_date", "yyyymm", "month", "ctime", "alarm_time", "created_time",
)

#: DB 현재시각 함수 — `unbounded`(날짜 한정 없음)에서 리터럴 없는 날짜 한정을 잡는다
#: (§10.1 A-01 `CURRENT_DATE - INTERVAL '1 month'` 실측 · §10.3 T-5b 와 같은 목록).
#: PG·DB2 표기를 함께 본다.
_DB_NOW_RE = re.compile(
    r"(?i)\bcurrent[_ ](?:date|timestamp)\b|\bnow\s*\(|\bsysdate\b|\binterval\b"
)
#: 결과 행 기간 값 — 숫자형 `202608`·`20260824`·`2026082410`(시간 통계) ·
#: 날짜형 `2026-08`·`2026-08-24`·`2026-08-24 10:00:00`·`2026-08-24T10:00:00+09:00`.
_ROW_PERIOD_DIGITS = re.compile(r"(\d{4})(\d{2})(?:\d{2}(?:\d{2})?)?")
_ROW_PERIOD_DASHED = re.compile(r"(\d{4})-(\d{2})(?:-\d{2})?(?:[T ]|$)")


def near_month_boundary(day: date) -> bool:
    """앵커 날짜(KST)가 월 경계 ±1일인가 — **말일 · 1일 · 2일**(plans/122 §8 위험표).

    앵커는 러너 시계(턴 송신 시각)이고 시스템은 서버 시계·DB `CURRENT_DATE` 로 기간을
    푼다. 두 시계가 하루 안쪽으로 어긋나도 ① 말일·1일은 서로 **다른 달**을 볼 수 있고
    (지난달·이번 달이 통째로 바뀐다) ② 2일은 「이번 달 = 1일~어제」 창이 [1일, 2일) 과
    빈 창(1일 기준) 사이에서 갈린다. 이 셋은 판정하지 않고 보류한다 — 어긋남을 시스템
    오답으로 세지 않는다.
    """
    return day.day in (1, 2) or (day + timedelta(days=1)).day == 1


#: 일·주·시 단위 상대 기간(plans/122 T-9) — 창이 날짜로 정해진다(`relative_window` 독스트링).
DAY_LEVEL_KINDS: frozenset[str] = frozenset(
    {"last_n_days", "yesterday", "last_week", "this_week", "today", "last_n_hours"}
)
#: 일·시 단위 창의 자정 보류 폭(`near_day_boundary`).
DAY_BOUNDARY_MARGIN = timedelta(minutes=10)


def near_day_boundary(moment: datetime) -> bool:
    """앵커 시각(KST)이 자정 ±`DAY_BOUNDARY_MARGIN` 안인가(plans/122 T-9 — 일·시 단위 창 보류).

    시간 해석 기본 on(D-306)에서 시스템은 요청 수신 시각(KST)으로 기간을 푼다. 남는 어긋남은
    러너·서버 시계 차와 턴 안 재송신(자동응답) 지연이라 「어제」·「지난주」·「최근 N시간」 창은
    자정 앞뒤에서만 다른 날을 볼 수 있다. DB 현재시각 함수로 푼 SQL 은 함수식 부정 단언이
    따로 잡는다.
    """
    since = moment - moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return since < DAY_BOUNDARY_MARGIN or since >= timedelta(days=1) - DAY_BOUNDARY_MARGIN


#: `exact` 판정용 리터럴(plans/122 T-9) — 대시 날짜(시각 선택) · YYYYMMDD(HH) · YYYYMM.
_EXTENT_LITERAL_RE = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?\b"
    r"|\b(\d{4})(\d{2})(\d{2})(\d{2})?\b"
    r"|\b(\d{4})(\d{2})\b"
)
#: 리터럴 바로 앞의 비교 연산자 — `TIMESTAMP '…'`·`DATE '…'`·`TO_DATE('…'` 감싸개를 건너뛴다.
_OP_BEFORE_RE = re.compile(
    r"(<=|>=|<>|!=|<|>|=)\s*(?:(?:timestamp|date)\s*"
    r"|(?:to_date|to_timestamp|timestamp|date|cast)\s*\(\s*)?'?$",
    re.IGNORECASE,
)


def _literal_span(match: re.Match[str]) -> tuple[datetime, datetime] | None:
    """리터럴 1개가 가리키는 구간 `[시작, 끝)` — 시각이 있는 TIMESTAMP 는 한 점(시작 = 끝)."""
    g = match.group
    try:
        if g(1):
            day = datetime(int(g(1)), int(g(2)), int(g(3)))
            if g(4) is None:
                return day, day + timedelta(days=1)
            point = day.replace(hour=int(g(4)), minute=int(g(5)), second=int(g(6) or 0))
            return point, point
        if g(7):
            day = datetime(int(g(7)), int(g(8)), int(g(9)))
            if g(10) is None:
                return day, day + timedelta(days=1)
            hour = day.replace(hour=int(g(10)))
            return hour, hour + timedelta(hours=1)
        month = datetime(int(g(11)), int(g(12)), 1)
        nxt = _next_month(month.date())
        return month, datetime(nxt.year, nxt.month, nxt.day)
    except ValueError:
        return None  # 날짜가 아닌 숫자(식별자 등)는 경계가 아니다


def sql_period_extent(sqls: list[str]) -> tuple[date | None, date | None]:
    """SQL 리터럴이 정하는 조회 구간 `[시작일, 끝일)` — 비교 연산자를 읽는다(plans/122 T-9 `exact`).

    `sql_period_bounds`(「덮는다」 판정)는 연산자를 보지 않아 배타 끝 `< '202609'` 를 9월
    전체로 편다. 정확 창은 그 차이를 가려야 하므로 리터럴 앞 연산자로 끝을 읽는다:
    `<` = 배타 끝(리터럴 시작) · `<=` = 포함 끝(리터럴 끝) · `>` = 리터럴 끝부터 · `>=` = 리터럴
    시작부터 · 그 밖(`=`·BETWEEN·IN·연산자 없음) = 리터럴 구간 전체. 시작은 날짜로 내리고 끝은
    자정으로 올린다(일 단위 판정). 한쪽 경계 리터럴이 없으면 그쪽은 None(열린 구간)이다.
    """
    lowers: list[datetime] = []
    uppers: list[datetime] = []
    for sql in sqls:
        for match in _EXTENT_LITERAL_RE.finditer(sql):
            span = _literal_span(match)
            if span is None:
                continue
            lo, hi = span
            op_match = _OP_BEFORE_RE.search(sql[max(0, match.start() - 48):match.start()])
            op = op_match.group(1) if op_match else None
            if op in ("<", "<="):
                uppers.append(lo if op == "<" else hi)
            elif op in (">", ">="):
                lowers.append(hi if op == ">" else lo)
            else:
                lowers.append(lo)
                uppers.append(hi)
    low = min(lowers).date() if lowers else None
    high: date | None = None
    if uppers:
        top = max(uppers)
        midnight = top.replace(hour=0, minute=0, second=0, microsecond=0)
        high = top.date() if top == midnight else top.date() + timedelta(days=1)
    return low, high


def _period_window(
    spec: dict[str, Any], anchor_at: str | None,
) -> tuple[tuple[date, date] | None, str]:
    """(상대 기간 창 `[시작일, 끝일)`, 보류 사유).

    창은 해석기 `relative_window` 가 정한다(정책 단일 출처 — 자체 월 산술 금지).
    """
    if not anchor_at:
        return None, "턴 앵커(anchor_at)가 없다 - 옛 run 이거나 러너가 기록하지 않았다"
    from src.domain.time_spec import KST, TimeSpecError, relative_window

    try:
        moment = datetime.fromisoformat(str(anchor_at))
    except ValueError:
        return None, f"턴 앵커를 읽지 못했다 - {anchor_at!r}"
    moment = moment.replace(tzinfo=KST) if moment.tzinfo is None else moment.astimezone(KST)
    if near_month_boundary(moment.date()):
        return None, (f"앵커 {moment.date().isoformat()} 가 월 경계 ±1일(말일·1일·2일)"
                      "이다 - 시스템과 러너가 다른 달을 볼 수 있다")
    if str(spec.get("relative")) in DAY_LEVEL_KINDS and near_day_boundary(moment):
        return None, (f"앵커 {moment.isoformat(timespec='minutes')} 가 자정 ±"
                      f"{DAY_BOUNDARY_MARGIN.seconds // 60}분이다 - 일·시 단위 창은 시스템과"
                      " 러너가 다른 날을 볼 수 있다")
    try:
        if "month_span" in spec:
            span = spec["month_span"]
            window = relative_window("month_span", moment, month_from=int(span["from"]),
                                     month_to=int(span["to"]))
        else:
            window = relative_window(str(spec.get("relative")), moment, n=spec.get("n"))
    except (TimeSpecError, KeyError, TypeError, ValueError) as exc:
        return None, f"기간 창을 계산하지 못했다 - {exc}"
    if window[0] >= window[1]:
        return None, f"기간 창이 비었다 - {window[0].isoformat()}"
    return window, ""


def _row_month(value: Any) -> tuple[int, int] | None:
    """결과 행 기간 값 → (연, 월). 읽지 못하면 None."""
    text = _cell(value)
    match = _ROW_PERIOD_DIGITS.fullmatch(text) or _ROW_PERIOD_DASHED.match(text)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    return (year, month) if 1900 <= year <= 2100 and 1 <= month <= 12 else None


def _result_months(result: Any) -> tuple[str | None, list[tuple[int, int]], str]:
    """(기간 열, 행의 (연, 월) 목록, 못 읽은 사유) — 결과 행(H-1)의 조회 기간 표본."""
    if not isinstance(result, dict):
        return None, [], "결과 행을 수집하지 않았다"
    status = result.get("status")
    if status == "empty":
        return None, [], "결과 행이 0건이다"
    if status != "ok":
        return None, [], f"결과 행을 받지 못했다({result.get('reason') or status})"
    folded = {str(column).casefold(): str(column) for column in result.get("columns") or []}
    column = next((folded[name] for name in RESULT_PERIOD_COLUMNS if name in folded), None)
    if column is None:
        return None, [], f"결과 행에 기간 열({', '.join(RESULT_PERIOD_COLUMNS)})이 없다"
    months = [month for row in result.get("rows") or [] if isinstance(row, dict)
              for month in [_row_month(row.get(column))] if month is not None]
    if not months:
        return column, [], f"기간 열 {column} 의 값을 날짜로 읽지 못했다"
    return column, months, ""


def _check_exact_period(
    spec: dict[str, Any], bodies: list[str], window: tuple[date, date], shown: dict[str, Any],
    failures: list[Failure], manual: _Holds,
) -> None:
    """`period_covers.exact`(plans/122 T-9) — SQL 리터럴 구간이 창과 날짜 단위로 같은가."""
    if not bodies:
        manual.add(f"period_covers {spec} 를 확인하지 못했다 - 실행 SQL 을 관측하지 못했다"
                   "(정확 창은 SQL 리터럴로만 판정한다)", "unobservable")
        return
    low, high = sql_period_extent(bodies)
    if (low, high) == window:
        return
    actual: dict[str, Any] = {**shown, "sql_period": (
        "날짜 리터럴 없음" if low is None and high is None
        else f"{low.isoformat() if low else '…'}~{high.isoformat() if high else '…'}")}
    functions = sorted({match.group(0).upper()
                        for body in bodies for match in _DB_NOW_RE.finditer(body)})
    if functions:
        actual["db_time_functions"] = functions
    failures.append(Failure("period_covers", spec, actual))


def _check_relative_period(
    spec: Any, obs: Observation, sqls: list[str], failures: list[Failure], manual: _Holds,
) -> None:
    """`period_covers` 상대 기간·월 범위·날짜 한정 없음(plans/122 H-2).

    절대 기간(`{from, to}`)은 `_check_period` 가 보고 여기서는 아무것도 하지 않는다.

    - 창 = `relative_window(종류, 앵커)` — 앵커는 턴 송신 시각(`obs.anchor_at` · KST).
      앵커가 없거나 월 경계 ±1일(`near_month_boundary`)이면 보류한다.
    - 판정은 `_check_period` 와 같은 「덮는다」 규칙이다 — 실행 SQL(주석 제외) 날짜
      리터럴의 최소·최대 경계가 창을 덮으면 통과(넓게 조회한 것은 통과 · 좁거나 다른
      기간이면 불합격).
    - SQL 에 날짜 리터럴이 없으면(DB 함수식 · SQL 미관측) 결과 행의 기간 열
      (`RESULT_PERIOD_COLUMNS`)로 본다. 결과 행은 **표본**이라 일 단위 끝까지 닿는다는
      보장이 없어(알람은 매일 나지 않는다) 월 단위로 덮는지를 본다. 결과가 잘렸는데
      덮지 못하면 보류, 둘 다 없으면 보류다.
    - `unbounded: true` 는 관측 SQL 전부에 날짜 한정(날짜 리터럴 · DB 현재시각 함수)이
      **없어야** 통과한다. SQL 을 관측하지 못했으면 보류다.
    - `exact: true`(plans/122 T-9 · relative·month_span 전용)는 「덮는다」 대신 **같다**를 본다 —
      실행 SQL 리터럴이 정하는 구간(`sql_period_extent` · 연산자를 읽는다)이 창과 날짜 단위로
      같아야 통과한다(「어제 알람 → 9월 전체」 같은 과대 확장이 불합격). 리터럴이 없으면 결과
      행으로 가지 않고 불합격이다(정확 창은 리터럴로만 판정 · §10.3 ④). 시 단위 창은 날짜로
      내려가므로(`relative_window`) 시 정확성은 입도 단언이 본다. 키가 없으면 종전 판정 그대로다.
    - 일·시 단위 창(`DAY_LEVEL_KINDS`)은 앵커가 자정 ±`DAY_BOUNDARY_MARGIN` 이면 보류한다.

    불합격의 기대값은 선언 그대로 싣는다(재판정기가 기대값으로 카탈로그 변경을 가린다) —
    창·앵커·관측 기간은 실제값 쪽에 싣는다.
    """
    if not isinstance(spec, dict) or not (
        spec.get("unbounded") is True or "relative" in spec or "month_span" in spec
    ):
        return
    bodies = [sql_body(sql) for sql in sqls]
    if spec.get("unbounded") is True:
        if not sqls:
            manual.add(f"period_covers {spec} 를 확인하지 못했다 - 실행 SQL 을 관측하지 못했다",
                       "unobservable")
            return
        low, high = sql_period_bounds(bodies)
        functions = sorted({match.group(0).upper()
                            for body in bodies for match in _DB_NOW_RE.finditer(body)})
        if low is not None or functions:
            actual: dict[str, Any] = {}
            if low is not None and high is not None:
                actual["sql_period"] = f"{low.isoformat()}~{high.isoformat()}"
            if functions:
                actual["db_time_functions"] = functions
            failures.append(Failure("period_covers", spec, actual))
        return
    window, reason = _period_window(spec, obs.anchor_at)
    if window is None:
        manual.add(f"period_covers {spec} 를 확인하지 못했다 - {reason}", "unobservable")
        return
    start, end = window
    shown: dict[str, Any] = {"window": f"{start.isoformat()}~{end.isoformat()}",
                             "anchor_at": obs.anchor_at}
    if spec.get("exact") is True:
        _check_exact_period(spec, bodies, window, shown, failures, manual)
        return
    low, high = sql_period_bounds(bodies)
    if low is not None and high is not None:
        if low > start or high < end - timedelta(days=1):
            failures.append(Failure("period_covers", spec, {
                **shown, "sql_period": f"{low.isoformat()}~{high.isoformat()}"}))
        return
    column, months, why = _result_months(obs.result)
    if not months:
        head = "실행 SQL 에 날짜 리터럴이 없고" if sqls else "실행 SQL 을 관측하지 못했고"
        manual.add(f"period_covers {spec} 를 확인하지 못했다 - {head} {why}", "unobservable")
        return
    last = end - timedelta(days=1)
    first_month, last_month = min(months), max(months)
    if first_month <= (start.year, start.month) and last_month >= (last.year, last.month):
        return
    observed = (f"{first_month[0]:04d}-{first_month[1]:02d}"
                f"~{last_month[0]:04d}-{last_month[1]:02d}")
    if isinstance(obs.result, dict) and obs.result.get("truncated"):
        manual.add(f"period_covers {spec} 를 확인하지 못했다 - 결과 행이 잘려 기간 열"
                   f" {column} 의 표본({observed})이 창 {shown['window']} 을 덮는지"
                   " 알 수 없다", "unobservable")
        return
    failures.append(Failure("period_covers", spec, {
        **shown, "result_period": observed, "column": column}))


def _check_oracle(spec: Any, obs: Observation, failures: list[Failure], manual: _Holds) -> None:
    """오라클 단언 `oracle`(plans/122 O-2) — 비교는 `oracle.evaluate_oracle` 에 맡긴다.

    `obs.oracle`(러너가 채운다 · 형태는 `Observation.oracle` 주석)의 post·pre 결과와
    결과 행(`obs.result`)·감사 DB 별 행 수(`obs.row_counts_by_db`)를 넘긴다.
    `source: fixture` 는 판정기가 정답표를 직접 읽는다(DB 호출 0 · 옛 run 재판정에도 쓴다).

    pass → 무표시 · fail → `Failure("oracle", 선언, 차이 상세)` · hold → 보류
    (`oracle_unavailable` — 오라클 실패·결과 미수집은 불합격이 아니다 · §9.2 판정 계약).
    보류 문구는 `oracle` 로 시작한다(재판정기 `rejudge._NOT_COLLECTED_NOTES` 가 과거 run 의
    미수집 보류로 가른다).
    """
    from .oracle import evaluate_oracle

    record = obs.oracle if isinstance(obs.oracle, dict) else {}
    verdict, detail = evaluate_oracle(
        spec, record.get("post"), obs.result, pre=record.get("pre"),
        row_counts_by_db=obs.row_counts_by_db,
    )
    if verdict == "pass":
        return
    if verdict == "fail":
        failures.append(Failure("oracle", spec, detail))
        return
    oracle_id = spec.get("id") if isinstance(spec, dict) else None
    reason = detail.get("reason") if isinstance(detail, dict) else detail
    manual.add(f"oracle {oracle_id} 를 확인하지 못했다 - {reason or '사유 없음'}",
               "oracle_unavailable")


def _check_column_mapping(spec: Any, obs: Observation, failures: list[Failure]) -> None:
    """column_must_not_map - D-200형 회귀를 기계로 잡는다(§3.8).

    "가동률"이 cpu_usage 로 매핑되지 않았는가. 매핑 산출물과 실행 SQL 양쪽을 본다 -
    매핑 산출물이 없는 경로(결정적 조립 등)에서도 SQL에는 칼럼이 남기 때문이다.
    """
    if not isinstance(spec, list):
        return
    mapped = {str(v) for v in obs.column_mapping.values()} if obs.column_mapping else set()
    sqls = [sql.lower() for sql in observed_sqls(obs)]
    for column in spec:
        name = str(column)
        if name in mapped:
            failures.append(Failure("column_must_not_map", name, "column_mapping 에 존재"))
        elif name and any(re.search(rf"\b{re.escape(name.lower())}\b", sql) for sql in sqls):
            failures.append(Failure("column_must_not_map", name, "실행 SQL 에 존재"))


def _ended_in_question(obs: Observation) -> bool:
    """역질문으로 끝난 턴 — 아직 조회 단계가 아니다(Y-9 와 같은 판별)."""
    return (obs.status == "clarification" or bool(obs.clarification)
            or bool(obs.form_fill_clarification))


def _check_observed_facts(
    expect: dict[str, Any], obs: Observation, sqls: list[str],
    failures: list[Failure], manual: _Holds, *, mock: bool,
) -> None:
    """이미 관측되는 값에 붙는 단언(plans/122 H-5). 관측하지 못한 것은 불합격이 아니라 보류다."""
    wanted = expect.get("sql_executed")
    if isinstance(wanted, bool):
        # `sql_must_match`(Y-9)와 같은 규칙 — **못 본 것**과 **안 만든 것**을 가른다.
        if sqls:
            if not wanted:
                failures.append(Failure("sql_executed", False, sqls[0][:200]))
        elif _ended_in_question(obs):
            manual.add("sql_executed: 역질문으로 끝난 턴이라 SQL 이 없다(판정 보류)",
                       "unobservable")
        elif mock:
            manual.add("sql_executed: 모의 실행은 SQL 수집기가 없다(판정 보류)", "unobservable")
        elif wanted:
            failures.append(Failure("sql_executed", True, None))
        elif (obs.row_count or 0) > 0:
            # 데이터는 나왔는데 SQL 을 못 봤다 - 「SQL 없음」을 통과로 세지 않는다
            # (부정 단언 가드와 대칭).
            manual.add("sql_executed: 행이 나왔지만 실행 SQL 을 관측하지 못했다(판정 보류)",
                       "unobservable")

    forbidden_nodes = expect.get("node_path_must_not") or []
    if forbidden_nodes:
        if not obs.node_path:
            # 노드 경로가 비면 「안 밟았다」가 아니라 「못 봤다」다(비스트림 경로 · 스트림 단절).
            manual.add(
                f"node_path_must_not {list(forbidden_nodes)} 를 확인하지 못했다"
                " - 노드 경로를 관측하지 못했다",
                "unobservable",
            )
        else:
            for node in forbidden_nodes:
                if node in obs.node_path:
                    failures.append(Failure("node_path_must_not", node, obs.node_path))

    allowed = expect.get("status_any")
    if allowed and obs.status not in [str(status) for status in allowed]:
        failures.append(Failure("status_any", list(allowed), obs.status))

    panel = expect.get("form_memory_panel")
    if panel in FORM_MEMORY_PANEL_STATES:
        present = isinstance(obs.form_memory_panel, dict) and bool(obs.form_memory_panel)
        if present != (panel == "present"):
            failures.append(Failure("form_memory_panel", panel, "present" if present else "absent"))

    stream = expect.get("stream")
    if isinstance(stream, dict):
        for key in sorted(STREAM_KEYS & set(stream)):
            bound = stream[key]
            if not isinstance(bound, dict) or "max" not in bound:
                continue
            actual = getattr(obs, key)
            if actual is None:
                # 토큰 없이 done 만 온 턴(역질문·비스트림)·옛 서버는 값이 없다 - 0 으로 보지 않는다.
                manual.add(
                    f"stream.{key}.max={bound['max']} 를 확인하지 못했다"
                    f" - 스트림에서 {key} 가 관측되지 않았다",
                    "unobservable",
                )
            elif actual > float(bound["max"]):
                failures.append(Failure(f"stream.{key}.max", bound["max"], actual))

    kinds = expect.get("dependency_notes_contains") or []
    if kinds:
        summary = obs.plan_summary if isinstance(obs.plan_summary, dict) else None
        if summary is None or summary.get("plan_path") == PRE_GATE_PLAN_PATH:
            # 노트는 2단 계획이 실행될 때만 생긴다 - 계획 요약이 없으면(1·3단 · 옛 서버 · 모의) 또는
            # 사전 게이트로 끝났으면 「노트 없음」이 아니라 「볼 수 없음」이다
            # (`plan` 단언과 같은 계약).
            manual.add(
                f"dependency_notes_contains {list(kinds)} 를 확인하지 못했다"
                " - 계획 요약(done.plan_summary)이 없거나 사전 게이트에서 끝난 턴이다"
                "(2단 밖 실행 단 · 옛 서버 · 모의 실행)",
                "unobservable",
            )
        else:
            have = sorted({str(note.get("kind")) for note in obs.dependency_notes
                           if isinstance(note, dict)})
            for kind in kinds:
                if kind not in have:
                    failures.append(Failure("dependency_notes_contains", kind, have))

    wanted_kinds = expect.get("disclosures_contains") or []
    if wanted_kinds:
        # plans/123 V-1 - 응답 고지 구조 필드(`done.disclosures`)의 kind. 고지는 조회 결과에 붙는다
        # - 역질문 턴에는 없고(제품 계약), 수집하지 않은 행(옛 러너·과거 run)은 「고지 없음」이
        # 아니라 「볼 수 없음」이다.
        if obs.disclosures is None:
            manual.add(
                f"disclosures_contains {list(wanted_kinds)} 를 확인하지 못했다"
                " - 응답 고지(done.disclosures)를 수집하지 않은 run 이다(W-8 이전 러너 · 과거 run)",
                "unobservable",
            )
        elif _ended_in_question(obs):
            manual.add(
                f"disclosures_contains {list(wanted_kinds)} 를 확인하지 못했다"
                " - 역질문으로 끝난 턴이라 조회 고지가 없다",
                "unobservable",
            )
        else:
            have = disclosure_kinds(obs)
            for kind in wanted_kinds:
                if kind not in have:
                    failures.append(Failure("disclosures_contains", kind, have))


# --- 표 형태 값 판정 (결과 행 H-1 · xlsx H-4 공용) --------------------------------------------
#: 불합격 상세에 싣는 예시 값 수 상한 — 행 원문을 싣지 않는다(G-4 · PII 노출면 · plans/110 94·§4.4).
_EXAMPLE_LIMIT = 3


def _cell(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _resolve_column(ref: Any, header: list[str]) -> str | None:
    """열 참조(이름 또는 별칭 목록) → 머리글에 있는 첫 이름. 없으면 None."""
    names = ref if isinstance(ref, list) else [ref]
    return next((str(name) for name in names if str(name) in header), None)


def _as_number(value: Any) -> tuple[str, float | None]:
    """("empty"|"number"|"invalid", 값). 문자열은 공백을 전부 뺀 뒤 float 로 읽는다."""
    if isinstance(value, bool):
        return "invalid", None
    if isinstance(value, (int, float)):
        return "number", float(value)
    text = "".join(_cell(value).split())
    if not text:
        return "empty", None
    try:
        return "number", float(text)
    except ValueError:
        return "invalid", None


def _value_range_items(ranges: Any) -> list[tuple[Any, Any, Any]]:
    """`value_range` 선언 → [(열 참조, [하한, 상한], 불합격 기대값)].

    두 형식을 받는다(plans/122 H-1·H-4):
      - 종전 `{열: [하한, 상한]}` — 열 이름 하나. 기대값·상세는 종전과 바이트 동일하다.
      - 별칭 목록 `[{columns: [별칭…], range: [하한, 상한]}]` — run 마다 갈리는 열 이름
        (B-12 CPU 평균 `cpu_avg_utilization`·`cpu_avg_percent`·`cpu_avg`·`cpu_avg_usage`)을
        한 단언으로 본다.
        **머리글에 먼저 나오는 별칭이 아니라 목록에서 먼저 적은 별칭**이 이긴다(`_resolve_column`).
    """
    if isinstance(ranges, dict):
        return [(column, bounds, {column: bounds}) for column, bounds in ranges.items()]
    return [(item.get("columns"), item.get("range"), item)
            for item in ranges or [] if isinstance(item, dict)]


def _value_range_failures(
    prefix: str, ranges: dict[str, Any] | list[dict[str, Any]], header: list[str],
    rows: list[list[Any]], extra: dict[str, Any],
) -> list[Failure]:
    """열 값이 [하한, 상한](양 끝 포함) 안인가.

    빈 칸은 세지 않되, 숫자가 하나도 없으면 불합격이다. 별칭 목록이 머리글에 하나도 없으면
    불합격 상세에 별칭과 머리글을 싣는다.
    """
    out: list[Failure] = []
    for ref, bounds, expected in _value_range_items(ranges):
        low, high = float(bounds[0]), float(bounds[1])
        if isinstance(ref, list):
            resolved = _resolve_column(ref, header)
            if resolved is None:
                out.append(Failure(f"{prefix}.value_range", expected,
                                   {"columns": ref, "missing": "헤더에 없음", "header": header,
                                    **extra}))
                continue
            column = resolved
        else:
            column = ref
        if column not in header:
            out.append(Failure(f"{prefix}.value_range", expected,
                               {"column": column, "missing": "헤더에 없음", "header": header,
                                **extra}))
            continue
        index = header.index(column)
        checked = 0
        offenders: list[str] = []
        invalid = 0
        for row in rows:
            kind, number = _as_number(row[index] if index < len(row) else None)
            if kind == "empty":
                continue
            if kind == "invalid":
                invalid += 1
                offenders.append(_cell(row[index]))
                continue
            checked += 1
            if number is not None and not (low <= number <= high):
                offenders.append(_cell(row[index]))
        if offenders or not checked:
            out.append(Failure(f"{prefix}.value_range", expected, {
                "column": column, "checked": checked, "non_numeric": invalid,
                "out_of_range": len(offenders) - invalid, "examples": offenders[:_EXAMPLE_LIMIT],
                **extra,
            }))
    return out


def _unique_by_failure(
    prefix: str, refs: list[Any], header: list[str], rows: list[list[Any]], extra: dict[str, Any],
) -> Failure | None:
    """열 조합이 행마다 유일한가(예: 서버당 1행 · 서버×월 1행)."""
    columns = [_resolve_column(ref, header) for ref in refs]
    if any(column is None for column in columns):
        return Failure(f"{prefix}.unique_by", refs,
                       {"missing": "헤더에 없음", "header": header, **extra})
    indexes = [header.index(str(column)) for column in columns]
    seen: dict[tuple[str, ...], int] = {}
    for row in rows:
        key = tuple(_cell(row[i] if i < len(row) else None) for i in indexes)
        seen[key] = seen.get(key, 0) + 1
    duplicated = [key for key, count in seen.items() if count > 1]
    if not duplicated:
        return None
    return Failure(f"{prefix}.unique_by", refs, {
        "columns": columns, "rows": len(rows), "duplicate_keys": len(duplicated),
        "duplicate_rows": sum(seen[key] for key in duplicated),
        "examples": [list(key) for key in duplicated[:_EXAMPLE_LIMIT]], **extra,
    })


def _check_db_row_sum(
    result: dict[str, Any], obs: Observation, failures: list[Failure], manual: _Holds,
) -> None:
    """`result.matches_db_row_sum`(plans/123 CT-6 · E-04) - 결과 행 수 = 감사 로그 DB별 행 수 합.

    「각각」 질의의 병합 결과가 DB별 조회 행을 잃었는지 본다(run 20260923-103638 E-04: 2,442행
    대 1,690 + 759). DB별 행 수는 **마지막 성공 실행**의 값이다(`runner._apply_sql_audit`).
    관측하지 못하면 보류다.
    """
    per_db = obs.row_counts_by_db
    total = result.get("total_rows")
    if not per_db or not isinstance(total, int) or isinstance(total, bool):
        manual.add("result.matches_db_row_sum 을 확인하지 못했다 - DB별 행 수(감사 로그)"
                   " 또는 결과 행 수가 없다", "unobservable")
        return
    expected = sum(int(count or 0) for count in per_db.values())
    if total != expected:
        failures.append(Failure("result.matches_db_row_sum", expected,
                                {"total_rows": total, "row_counts_by_db": dict(per_db)}))


def _check_result(spec: Any, obs: Observation, failures: list[Failure], manual: _Holds) -> None:
    """결과 행 단언 `result` (plans/122 H-1) — 러너가 받은 `download-csv` 행을 본다.

    관측하지 못하면(수집 안 함 · 받지 못함) 불합격이 아니라 보류다. 빈 결과는 `allow_empty` 가
    아니면 선언한 하위 단언이 전부 불합격이다. 절단된 결과는 받은 행만 판정하고 그 사실을 상세에
    싣는다. 불합격 상세에는 **개수와 예시 값 최대 3개만** 싣는다 - 행 원문은 싣지 않는다(G-4).
    """
    if not isinstance(spec, dict) or not spec:
        return
    result = obs.result if isinstance(obs.result, dict) else None
    if result is None:
        manual.add("result 를 확인하지 못했다 - 결과 행 미수집(러너가 download-csv 를 받지 않았다)",
                   "unobservable")
        return
    status = result.get("status")
    if status == "unavailable" or status not in ("ok", "empty"):
        manual.add(f"result 를 확인하지 못했다 - 결과 행을 받지 못했다"
                   f"(status={status} · {result.get('reason') or '사유 없음'})", "unobservable")
        return
    header = [str(column) for column in result.get("columns") or []]
    rows = [[row.get(column) for column in header]
            for row in result.get("rows") or [] if isinstance(row, dict)]
    if spec.get("matches_db_row_sum"):
        _check_db_row_sum(result, obs, failures, manual)
    checks = [key for key in ("columns", "filled_columns", "value_range", "unique_by")
              if key in spec]
    if status == "empty" or not rows:
        # 행 합 단언만 있으면 0행도 그 단언이 판정한다(DB별 합도 0 이면 일치).
        if not spec.get("allow_empty") and (checks or not spec.get("matches_db_row_sum")):
            for key in checks or ["allow_empty"]:
                failures.append(Failure(f"result.{key}", spec.get(key, False), "결과 행 0건"))
        return
    extra: dict[str, Any] = (
        {"truncated": True, "judged_rows": len(rows), "total_rows": result.get("total_rows")}
        if result.get("truncated") else {}
    )
    for ref in spec.get("columns") or []:
        if _resolve_column(ref, header) is None:
            failures.append(Failure("result.columns", ref, header))
    for ref in spec.get("filled_columns") or []:
        column = _resolve_column(ref, header)
        if column is None:
            failures.append(Failure("result.filled_columns", ref,
                                    {"missing": "헤더에 없음", "header": header, **extra}))
            continue
        index = header.index(column)
        empty = sum(1 for row in rows if not _cell(row[index] if index < len(row) else None))
        if empty:
            failures.append(Failure("result.filled_columns", ref,
                                    {"column": column, "empty_rows": empty, "rows": len(rows),
                                     **extra}))
    failures.extend(
        _value_range_failures("result", spec.get("value_range") or {}, header, rows, extra)
    )
    if spec.get("unique_by"):
        failure = _unique_by_failure("result", spec["unique_by"], header, rows, extra)
        if failure:
            failures.append(failure)


def evaluate_turn(
    scenario: Scenario,
    turn_index: int,
    turn: Turn,
    obs: Observation,
    group: Group,
    *,
    mock: bool = False,
) -> Verdict:
    """턴 1회를 판정한다. 단언이 없으면 합격이 아니라 `manual`이다.

    `mock=True` 면 내용 단언(`_CONTENT_KEYS`)을 적용하지 않고 보류로 남긴다 -
    모의 서버의 canned 응답을 시스템의 답으로 판정하면 전건이 거짓 불합격이 된다.
    """
    verdict = Verdict()
    failures: list[Failure] = []
    # 보류 사유 + 출처(plans/122 J-3). 사유 문구는 종전과 바이트 동일하다.
    manual = _Holds()
    expect = dict(turn.expect)

    if mock and not scenario.mock:
        skipped = sorted(k for k in expect if k not in _MOCK_VERIFIABLE | {"manual_review"})
        for key in skipped:
            expect.pop(key)
        if skipped:
            manual.add(
                f"모의 실행(canned 응답) - 단언 {len(skipped)}종을 적용하지 않았다"
                f"({', '.join(skipped)}). 모의가 증명하는 것은 배관뿐이다 - 실 모드에서 판정한다",
                "unobservable",
            )

    if expect.get("manual_review"):
        review = str(expect["manual_review"])
        manual.add(review, *_review_sources(review))

    if "status" in expect and obs.status != expect["status"]:
        failures.append(Failure("status", expect["status"], obs.status))
    if "http_status" in expect and obs.http_status != int(expect["http_status"]):
        failures.append(Failure("http_status", expect["http_status"], obs.http_status))
    if "intent" in expect:
        if obs.intent is None:
            # **관측되지 않는 값으로 불합격을 만들지 않는다.** `/query/stream` 의 done
            # 페이로드에 intent 가 없어(query.py 의 done 키 목록) 이 필드는 영영 None 이다.
            # 그대로 대조하면 전건이 거짓 불합격이 된다 - 라우팅은 `db_ids` 로 본다.
            manual.add(
                f"intent={expect['intent']} 를 확인하지 못했다 "
                "(응답에 intent 가 실리지 않는다 - db_ids 로 라우팅을 본다)",
                "unobservable",
            )
        elif obs.intent != expect["intent"]:
            failures.append(Failure("intent", expect["intent"], obs.intent))
    if "db_ids" in expect and sorted(obs.db_ids) != sorted(expect["db_ids"]):
        failures.append(Failure("db_ids", expect["db_ids"], obs.db_ids))
    if "has_file" in expect and obs.has_file != bool(expect["has_file"]):
        failures.append(Failure("has_file", expect["has_file"], obs.has_file))

    _check_row_count_axes(expect, obs, failures, manual)
    _check_clarification(expect.get("clarification"), obs, failures)

    for needle in expect.get("response_must_contain") or []:
        if str(needle) not in obs.response:
            failures.append(Failure("response_must_contain", needle, "응답에 없음"))
    # plans/120 U-4: 선택지 중 하나라도 성립하면 통과. 선택지가 목록이면 그 문구가 **전부** 있어야
    # 성립한다 - H-06 은 `[미작성 항목]` 또는 3열 각각의 `공란 유지` 적용 내역을 받는다
    # (D-151 역질문 × D-216 자동응답).
    options = expect.get("response_must_contain_any") or []
    if options and not any(
        all(str(needle) in obs.response
            for needle in (option if isinstance(option, list) else [option]))
        for option in options
    ):
        failures.append(Failure("response_must_contain_any", options, "응답에 없음"))
    for needle in expect.get("response_must_not_contain") or []:
        if str(needle) in obs.response:
            failures.append(Failure("response_must_not_contain", needle, "응답에 있음"))

    # SQL 별로 판정한다 - 이어붙여 검색하면 두 SQL 경계를 넘는 거짓 일치가 난다.
    # 멀티 DB 는 존마다, 재계획은 라운드마다 SQL 이 따로 나간다(SYN-A-01: 한 턴 12건).
    sqls = observed_sqls(obs)
    must_match = expect.get("sql_must_match") or []
    if must_match and not sqls:
        # Y-9: **못 본 것**과 **안 만든 것**을 가른다(plans/94 §16).
        # 둘을 같은 불합격으로 세면 수집 실패가 "SQL 미생성" 제품 결함으로 읽힌다
        # (plans/96 P-13 → plans/98 J-5 로 내려간 사례). 부정 단언 쪽 가드와 대칭이다.
        if obs.status == "clarification" or obs.clarification or obs.form_fill_clarification:
            # 역질문은 아직 조회 단계가 아니다 - 사용자가 답해야 SQL 이 나온다.
            manual.add("sql_must_match: 역질문으로 끝난 턴이라 SQL 이 없다(판정 보류)",
                       "unobservable")
        elif mock:
            # 감사 로그 tail 은 실 모드에만 붙는다(runner: sql_tail = ... if live else None).
            manual.add("sql_must_match: 모의 실행은 SQL 수집기가 없다(판정 보류)", "unobservable")
        else:
            # 실 모드에서 완료됐는데 SQL 0건이면 그것은 관측이다 - 불합격으로 센다.
            for pattern in must_match:
                failures.append(Failure("sql_must_match", pattern, None))
    else:
        bodies = [sql_body(sql) for sql in sqls]
        for pattern in must_match:
            if not any(re.search(str(pattern), body) for body in bodies):
                failures.append(Failure("sql_must_match", pattern, (sqls[0][:200] if sqls else None)))
    for pattern in expect.get("sql_must_not_match") or []:
        hit = next((sql for sql in sqls if re.search(str(pattern), sql_body(sql))), None)
        if hit is not None:
            failures.append(Failure("sql_must_not_match", pattern, hit[:200]))
    if expect.get("sql_must_not_match") and not sqls and (obs.row_count or 0) > 0:
        # 데이터는 나왔는데 SQL 을 하나도 보지 못했다 - 부정 단언을 통과로 세지 않는다.
        manual.add("행이 나왔지만 실행 SQL 을 관측하지 못했다 - sql_must_not_match 확인 불가",
                   "unobservable")
    _check_period(expect.get("period_covers"), sqls, failures, manual)
    # plans/122 H-2 - 상대 기간·월 범위·날짜 한정 없음. 절대 기간 선언에는 아무것도 하지 않는다.
    _check_relative_period(expect.get("period_covers"), obs, sqls, failures, manual)

    _check_column_mapping(expect.get("column_must_not_map"), obs, failures)
    _check_file(expect.get("file"), obs, failures, manual, scenario.upload)
    _check_rewrite(expect.get("rewrite"), obs, failures, manual)
    _check_plan(expect.get("plan"), obs, failures, manual)
    # plans/122 H-1 · H-5 - 선언하지 않은 턴은 아무것도 하지 않는다(판정 바이트 불변).
    _check_result(expect.get("result"), obs, failures, manual)
    if "oracle" in expect:
        # plans/122 O-2 - 오라클 정답과 비교(선언하지 않은 턴은 판정 바이트 불변).
        _check_oracle(expect["oracle"], obs, failures, manual)
    _check_observed_facts(expect, obs, sqls, failures, manual, mock=mock)

    for node in expect.get("node_path") or []:
        if node not in obs.node_path:
            failures.append(Failure("node_path", node, obs.node_path))
    for event in expect.get("sse_events") or []:
        if event not in obs.sse_events:
            failures.append(Failure("sse_events", event, sorted(set(obs.sse_events))))

    budget = expect.get("llm_calls")
    if isinstance(budget, dict) and "max" in budget:
        if obs.llm_calls is None:
            # **확인 못 한 예산을 통과로 세지 않는다.** `done` 페이로드에 LLM 호출 수가
            # 없어 이 단언은 영영 발화하지 않는다 - 조용히 건너뛰면 "예산을 지켰다"로 읽힌다.
            manual.add(
                f"llm_calls.max={budget['max']} 예산을 확인하지 못했다 "
                "(스트림에 LLM 호출 수가 실리지 않는다 - 노드 수 `node_count` 로 대신 본다)",
                "unobservable",
            )
        elif obs.llm_calls > int(budget["max"]):
            failures.append(Failure("llm_calls.max", budget["max"], obs.llm_calls))
    budget = expect.get("retries")
    if isinstance(budget, dict) and "max" in budget:
        if obs.retries is None:
            # 회귀 노드가 상위 스트림에 보이지 않는 단(intent_orchestration·deep_agent)에서는
            # 재시도를 셀 수 없다 - 통과로 세지 않는다(llm_calls 와 같은 규칙).
            manual.add(
                f"retries.max={budget['max']} 예산을 확인하지 못했다 "
                "(query_generator 가 상위 스트림에 나오지 않는 실행 단)",
                "unobservable",
            )
        elif obs.retries > int(budget["max"]):
            failures.append(Failure("retries.max", budget["max"], obs.retries))
        elif obs.retries_partial:
            manual.add(
                f"retries={obs.retries} 는 하한이다(멀티 DB 경로의 검증 거부 재시도는 관측되지 않는다) - "
                f"max={budget['max']} 이내인지 확정하지 못했다",
                "unobservable",
            )

    if expect.get("gold_sql"):
        # EX 결과집합 동등성은 골드 SQL 실행이 필요하다 - 러너는 DB 쓰기 경로를 갖지 않고
        # 읽기 실행기도 붙이지 않는다. scripts/eval_text2sql.py 의 execution_match 로
        # 별도 실행하는 것이 정본이므로(§1.2 재사용 목록) 여기서는 수동 검토로 넘긴다.
        manual.add(f"gold_sql 동등성은 eval_text2sql.execution_match 로 별도 판정 ({scenario.id})",
                   "unobservable")

    mode, evidence = classify_mode(obs)
    verdict.response_mode = mode
    verdict.mode_evidence = evidence

    # plans/123 V-4 - 불변식은 모든 턴에 계산해 트리아지 칸에 싣고, 군 헤더가 활성으로 선언했고 그
    # run 이 응답 고지를 수집했을 때만(= W-8 이후 러너 · run R5~) 판정에 넣는다.
    from .invariants import evaluate_invariants

    verdict.invariant_violations = evaluate_invariants(
        scenario, turn_index, turn, obs, group, mode=mode, mock=mock, manual=manual,
    )
    active = [v for v in verdict.invariant_violations if v["active"]]
    for violation in active:
        failures.append(Failure(f"invariant.{violation['name']}", "위반 없음", violation["detail"]))

    negative_violated = any(f.key in _NEGATIVE_KEYS for f in failures)
    if mode in FORBIDDEN_MODES:
        verdict.forbidden_mode = mode
    elif mode == "answer" and negative_violated:
        # 착각을 그대로 받아 그럴듯한 답을 냈다. 사용자가 알아차릴 수 없는 실패다.
        verdict.forbidden_mode = "silent_wrong"
        verdict.mode_evidence = "부정 단언 위반 + answer"
    elif mode in ("answer", "empty_template") and active:
        # plans/123 V-5(120·V-7 보류 해제) - 활성 불변식 위반 + 정상 응답·빈 결과 템플릿. 상한
        # 도달·한 존 조회·대상 없음을 말하지 않고 그럴듯하게 끝낸 응답이다.
        verdict.forbidden_mode = "silent_wrong"
        verdict.mode_evidence = (f"불변식 위반({', '.join(v['name'] for v in active)}) + {mode}")

    declared = set(scenario.response_modes)
    if (mode == "partial" and evidence == LIMIT_ONLY_PARTIAL_EVIDENCE and "answer" in declared):
        declared.add("partial")          # 123 V-1 - 상한 고지만의 partial 은 answer 동치
    if declared and mode not in declared and verdict.forbidden_mode is None:
        if group.policy_confirmed:
            failures.append(Failure("response_modes", sorted(declared), mode))
        else:
            # G-10 (b) - 정책 확정 전에는 우리가 정한 기대값으로 시스템을 재단하지 않는다.
            manual.add(
                f"대응 등급 '{mode}' 가 선언 {sorted(declared)} 밖이다 "
                "(등급 정책 미확정 - 1차는 관측으로만 쓴다)",
                "policy",
            )

    verdict.failures = failures
    verdict.manual_notes = manual.notes
    verdict.manual_sources = manual.sources

    # 기대한 오류는 오류 판정이 아니다. 클라이언트가 HTTP 4xx 를 obs.error 로 옮기므로
    # (2026-09-14 — 401 이 manual 로 새던 것을 막은 변경) 400 을 **기대하는** 가드 시나리오
    # (R2-08)까지 error 로 떨어졌다. 기대값과 일치한 오류는 단언이 판정한다.
    expected_error = (
        ("http_status" in expect and obs.http_status >= 400
         and obs.http_status == int(expect["http_status"]))
        or (expect.get("status") == "error" and obs.status == "error")
    )
    if is_runner_auth_failure(obs, expect):
        # T-c: **전송 계층 실패를 기능 판정에 넣지 않는다.** 단언은 전부 401 의 그림자다 -
        # 기대 200 vs 실제 401 을 기능 불합격으로 세면 판정표·실패 분류·대안 수립이 통째로
        # 거짓이 된다(run 20260915-131903: 불합격 31건 · 「과잉 거부 의심」 26건 전건 허위).
        # 원본 사유는 행의 `error` 에 그대로 남는다 - 지우는 것은 **판정**뿐이다.
        verdict.func = INVALID_VERDICT
        verdict.failures = []
        verdict.manual_notes = []
        verdict.manual_sources = []
        verdict.invariant_violations = []
        verdict.invalid_reason = (
            f"러너 인증 실패 - {obs.error or f'http {obs.http_status}'}"
            + ("  (재로그인 후 1회 재시도했으나 다시 거부됐다)" if obs.auth_retried else "")
        )
        verdict.perf = "n/a"
        return verdict

    if obs.error and not failures and not expected_error:
        verdict.func = "error"
    elif verdict.forbidden_mode:
        verdict.func = "fail"
    elif failures:
        verdict.func = "fail"
    elif manual:
        # 기계 단언이 통과했어도 **옮기지 않은 기대값이 남아 있으면 합격으로 세지 않는다**.
        # "옮긴 만큼만 자동 판정 대상이 된다"(§3.4) 를 집계에서 지키는 지점이다 -
        # 여기서 pass 로 세면 단언 미작성 시나리오가 커버리지를 부풀린다.
        verdict.func = "manual"
    else:
        verdict.func = "pass"

    verdict.perf = _perf_verdict(scenario, turn_index, obs, group)
    return verdict


def _perf_verdict(
    scenario: Scenario, turn_index: int, obs: Observation, group: Group
) -> str:
    applies = scenario.perf.get("applies_to_turn")
    if applies is not None and int(applies) != turn_index:
        return "n/a"
    target = scenario.target_ms or group.latency_target_ms
    if not target:
        return "n/a"
    if obs.processing_time_ms is None:
        return "n/a"
    return "pass" if obs.processing_time_ms <= target else "fail"
