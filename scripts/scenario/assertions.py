"""L1·L2·LR 단언 평가기 (plans/94 §3.3 · §3.8).

**결정적이다. LLM을 쓰지 않는다**(D-035 · G-5). 자연어 응답의 "정답"은 기계가 판정할 수
없으므로, 판정할 수 없는 것은 판정하지 않고 `manual`로 남긴다 - 합격으로 세지 않는 것이
핵심이다. 조용히 합격으로 세면 리포트가 커버리지를 부풀린다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional

from .catalog import FORBIDDEN_MODES, Group, Scenario, Turn

# 대응 등급 탐지 표지 (§3.8).
#
# 이 표지는 **관측 어휘**이지 판정 기준이 아니다. G-10 (b)에 따라 케이스별 허용 등급이
# 사용자 확정되기 전까지 R군 판정은 manual 로 남고, 여기서 고른 등급은 리포트의 분포로만
# 쓰인다. 어느 표지가 맞았는지는 raw.jsonl 의 mode_evidence 에 남겨 사람이 감사할 수 있게 한다.
_REFUSE_MARKERS = (
    "수행할 수 없", "허용되지 않", "읽기 전용", "실행하지 않", "거부",
    "삭제할 수 없", "변경할 수 없", "권한이 없",
)
_GUIDE_MARKERS = (
    "지원하지 않", "제공하지 않", "수집 대상이 아니", "수집하지 않", "범위 밖",
    "기능이 없", "존재하지 않", "등록되어 있지 않", "해당 존이 없",
)
_CORRECT_MARKERS = ("오타", "으로 이해", "로 이해", "으로 해석", "로 해석", "교정", "대신")
_PARTIAL_MARKERS = ("일부만", "절단", "잘라", "전체가 아니", "먼저 보여", "우선 표시")
_CRASH_MARKERS = ("Traceback (most recent call last)", "Internal Server Error")

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
    intent: Optional[str] = None
    processing_time_ms: Optional[float] = None
    wall_ms: float = 0.0
    ttfb_ms: Optional[float] = None
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
    #: `func == "invalid"` 일 때만 채운다. 무엇이 측정을 무효로 만들었는지 한 줄.
    invalid_reason: Optional[str] = None


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
    """
    if str(row.get("func_verdict")) == INVALID_VERDICT:
        return True
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


def classify_mode(obs: Observation) -> tuple[str, Optional[str]]:
    """대응 등급을 고른다. 먼저 맞는 것을 적용한다. (등급, 근거 표지)를 돌려준다."""
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
    # 거부·안내 표지는 **데이터를 돌려주지 않은 응답**에만 적용한다. 데이터 표에 붙은 진단 절
    # ("[일부 존 조회 실패] ... 허용되지 않은 테이블")이 거부로 분류되던 오분류(SYN-F-03)를 막는다 -
    # 종전에는 오케스트레이션 경로의 executed_sql 이 늘 None 이라 모든 응답이 이 검사를 탔다.
    if not observed_sqls(obs) and not (obs.row_count or 0):
        marker = _contains_any(obs.response, _REFUSE_MARKERS)
        if marker:
            return "refuse", marker
        marker = _contains_any(obs.response, _GUIDE_MARKERS)
        if marker:
            return "guide", marker
    marker = _contains_any(obs.response, _CORRECT_MARKERS)
    if marker:
        return "correct", marker
    marker = _contains_any(obs.response, _PARTIAL_MARKERS)
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
    expect: dict[str, Any], obs: Observation, failures: list[Failure], manual: list[str]
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
            manual.append(
                f"row_count 는 단일 DB 턴 전용이다 - 이 턴은 {len(per_db) or len(set(obs.db_ids))}개 DB "
                f"팬아웃이라 합계({obs.row_count})와 per-DB 기대값을 비교하게 된다. "
                f"row_count_per_db / row_count_total 로 선언할 것 (DB별 실측: {per_db or '미관측'})"
            )
        else:
            _check_row_count(expect["row_count"], obs.row_count, failures)

    _check_row_count(expect.get("row_count_total"), obs.row_count, failures, "row_count_total")

    spec = expect.get("row_count_per_db")
    if isinstance(spec, dict):
        if not per_db:
            # 감사 로그 tail 이 없으면 DB 별 행 수를 모른다 - 통과로도 불합격으로도 세지 않는다.
            manual.append(
                "row_count_per_db 를 확인하지 못했다 - DB 별 행 수는 감사 로그 "
                "`query_executed` 에서만 나온다(모의 실행·tail 미가동이면 관측 0건)"
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


def _check_file(
    spec: Any, obs: Observation, failures: list[Failure], manual: list[str]
) -> None:
    if not isinstance(spec, dict):
        return
    if not obs.artifacts:
        failures.append(Failure("file", spec, "산출물 없음"))
        return
    path = Path(obs.artifacts[0])
    if path.suffix.lower() != ".xlsx":
        manual.append(f"{path.name}: xlsx 가 아니라 자동 칼럼 검증 대상이 아니다")
        return
    sheets, reason = _read_xlsx(path)
    if sheets is None:
        manual.append(reason or "산출물 판독 불가")
        return

    wanted_sheets = spec.get("sheets") or []
    for name in wanted_sheets:
        if name not in sheets:
            failures.append(Failure("file.sheets", name, sorted(sheets)))

    target_rows = sheets.get(wanted_sheets[0]) if wanted_sheets else next(iter(sheets.values()), [])
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


#: SQL 안의 날짜 리터럴. `2026-07-01` · `20260701` · `202607`(월 파티션) 세 표기를 본다.
_DATE_LITERAL_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{4})(\d{2})(\d{2})\b|\b(\d{4})(\d{2})\b")


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
    spec: Any, obs: Observation, failures: list[Failure], manual: list[str],
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
        manual.append(
            f"rewrite {sorted(spec)} 를 확인하지 못했다 - 재작성 감사 레코드가 없다"
            "(서버 INTENT_FRAME_ENABLED·SQL 생성 노드 통과 여부 확인)"
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
            manual.append(
                "rewrite.slots_preserved 를 확인하지 못했다 - 검증 결과가 비었다"
                "(서버 REWRITE_VERIFY_MODE=shadow 필요 · 재작성이 없던 턴은 검증 대상이 아니다)"
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


def _check_period(
    spec: Any, sqls: list[str], failures: list[Failure], manual: list[str]
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
        manual.append(f"period_covers {spec} 를 확인하지 못했다 - 실행 SQL 을 관측하지 못했다")
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
    manual: list[str] = []
    expect = dict(turn.expect)

    if mock and not scenario.mock:
        skipped = sorted(k for k in expect if k not in _MOCK_VERIFIABLE | {"manual_review"})
        for key in skipped:
            expect.pop(key)
        if skipped:
            manual.append(
                f"모의 실행(canned 응답) - 단언 {len(skipped)}종을 적용하지 않았다"
                f"({', '.join(skipped)}). 모의가 증명하는 것은 배관뿐이다 - 실 모드에서 판정한다"
            )

    if expect.get("manual_review"):
        manual.append(str(expect["manual_review"]))

    if "status" in expect and obs.status != expect["status"]:
        failures.append(Failure("status", expect["status"], obs.status))
    if "http_status" in expect and obs.http_status != int(expect["http_status"]):
        failures.append(Failure("http_status", expect["http_status"], obs.http_status))
    if "intent" in expect:
        if obs.intent is None:
            # **관측되지 않는 값으로 불합격을 만들지 않는다.** `/query/stream` 의 done
            # 페이로드에 intent 가 없어(query.py 의 done 키 목록) 이 필드는 영영 None 이다.
            # 그대로 대조하면 전건이 거짓 불합격이 된다 - 라우팅은 `db_ids` 로 본다.
            manual.append(
                f"intent={expect['intent']} 를 확인하지 못했다 "
                "(응답에 intent 가 실리지 않는다 - db_ids 로 라우팅을 본다)"
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
            manual.append("sql_must_match: 역질문으로 끝난 턴이라 SQL 이 없다(판정 보류)")
        elif mock:
            # 감사 로그 tail 은 실 모드에만 붙는다(runner: sql_tail = ... if live else None).
            manual.append("sql_must_match: 모의 실행은 SQL 수집기가 없다(판정 보류)")
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
        manual.append("행이 나왔지만 실행 SQL 을 관측하지 못했다 - sql_must_not_match 확인 불가")
    _check_period(expect.get("period_covers"), sqls, failures, manual)

    _check_column_mapping(expect.get("column_must_not_map"), obs, failures)
    _check_file(expect.get("file"), obs, failures, manual)
    _check_rewrite(expect.get("rewrite"), obs, failures, manual)

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
            manual.append(
                f"llm_calls.max={budget['max']} 예산을 확인하지 못했다 "
                "(스트림에 LLM 호출 수가 실리지 않는다 - 노드 수 `node_count` 로 대신 본다)"
            )
        elif obs.llm_calls > int(budget["max"]):
            failures.append(Failure("llm_calls.max", budget["max"], obs.llm_calls))
    budget = expect.get("retries")
    if isinstance(budget, dict) and "max" in budget:
        if obs.retries is None:
            # 회귀 노드가 상위 스트림에 보이지 않는 단(intent_orchestration·deep_agent)에서는
            # 재시도를 셀 수 없다 - 통과로 세지 않는다(llm_calls 와 같은 규칙).
            manual.append(
                f"retries.max={budget['max']} 예산을 확인하지 못했다 "
                "(query_generator 가 상위 스트림에 나오지 않는 실행 단)"
            )
        elif obs.retries > int(budget["max"]):
            failures.append(Failure("retries.max", budget["max"], obs.retries))
        elif obs.retries_partial:
            manual.append(
                f"retries={obs.retries} 는 하한이다(멀티 DB 경로의 검증 거부 재시도는 관측되지 않는다) - "
                f"max={budget['max']} 이내인지 확정하지 못했다"
            )

    if expect.get("gold_sql"):
        # EX 결과집합 동등성은 골드 SQL 실행이 필요하다 - 러너는 DB 쓰기 경로를 갖지 않고
        # 읽기 실행기도 붙이지 않는다. scripts/eval_text2sql.py 의 execution_match 로
        # 별도 실행하는 것이 정본이므로(§1.2 재사용 목록) 여기서는 수동 검토로 넘긴다.
        manual.append(f"gold_sql 동등성은 eval_text2sql.execution_match 로 별도 판정 ({scenario.id})")

    mode, evidence = classify_mode(obs)
    verdict.response_mode = mode
    verdict.mode_evidence = evidence

    negative_violated = any(f.key in _NEGATIVE_KEYS for f in failures)
    if mode in FORBIDDEN_MODES:
        verdict.forbidden_mode = mode
    elif mode == "answer" and negative_violated:
        # 착각을 그대로 받아 그럴듯한 답을 냈다. 사용자가 알아차릴 수 없는 실패다.
        verdict.forbidden_mode = "silent_wrong"
        verdict.mode_evidence = "부정 단언 위반 + answer"

    declared = set(scenario.response_modes)
    if declared and mode not in declared and verdict.forbidden_mode is None:
        if group.policy_confirmed:
            failures.append(Failure("response_modes", sorted(declared), mode))
        else:
            # G-10 (b) - 정책 확정 전에는 우리가 정한 기대값으로 시스템을 재단하지 않는다.
            manual.append(
                f"대응 등급 '{mode}' 가 선언 {sorted(declared)} 밖이다 "
                "(등급 정책 미확정 - 1차는 관측으로만 쓴다)"
            )

    verdict.failures = failures
    verdict.manual_notes = manual

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
