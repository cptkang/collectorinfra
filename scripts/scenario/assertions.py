"""L1·L2·LR 단언 평가기 (plans/94 §3.3 · §3.8).

**결정적이다. LLM을 쓰지 않는다**(D-035 · G-5). 자연어 응답의 "정답"은 기계가 판정할 수
없으므로, 판정할 수 없는 것은 판정하지 않고 `manual`로 남긴다 - 합격으로 세지 않는 것이
핵심이다. 조용히 합격으로 세면 리포트가 커버리지를 부풀린다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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


@dataclass
class Observation:
    """턴 1회 실행의 관측치. 클라이언트가 채우고 평가기가 읽는다."""

    http_status: int = 0
    query_id: Optional[str] = None
    status: str = "unknown"          # completed | clarification | error | awaiting_approval
    response: str = ""
    executed_sql: Optional[str] = None
    row_count: Optional[int] = None
    has_file: bool = False
    file_name: Optional[str] = None
    clarification: Optional[dict] = None
    form_fill_clarification: Optional[dict] = None
    db_ids: list[str] = field(default_factory=list)
    intent: Optional[str] = None
    processing_time_ms: Optional[float] = None
    wall_ms: float = 0.0
    ttfb_ms: Optional[float] = None
    node_elapsed_ms: dict[str, float] = field(default_factory=dict)
    node_path: list[str] = field(default_factory=list)
    sse_events: list[str] = field(default_factory=list)
    progress_events: list[dict] = field(default_factory=list)
    llm_calls: Optional[int] = None
    tokens: Optional[int] = None
    retries: Optional[int] = None
    column_mapping: dict[str, Any] = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)
    # 무이벤트 구간이 하트비트 간격을 크게 넘겼거나 done 없이 끊긴 경우 True (금지 등급 hang).
    hang: bool = False
    max_event_gap_ms: Optional[float] = None
    error: Optional[str] = None


@dataclass
class Failure:
    """깨진 단언 1건. 분석기의 1차 입력이다(§4.3)."""

    key: str
    expected: Any
    actual: Any

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "expected": self.expected, "actual": self.actual}


@dataclass
class Verdict:
    """턴 1회의 판정."""

    func: str = "pass"               # pass | fail | error | manual | skipped
    perf: str = "n/a"                # pass | fail | n/a
    response_mode: str = "answer"
    forbidden_mode: Optional[str] = None
    mode_evidence: Optional[str] = None
    failures: list[Failure] = field(default_factory=list)
    manual_notes: list[str] = field(default_factory=list)


def _contains_any(text: str, markers: tuple[str, ...]) -> Optional[str]:
    for marker in markers:
        if marker in text:
            return marker
    return None


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
    if not obs.executed_sql:
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


def _check_row_count(spec: Any, actual: Optional[int], failures: list[Failure]) -> None:
    if not isinstance(spec, dict):
        return
    if "eq" in spec and actual != int(spec["eq"]):
        failures.append(Failure("row_count.eq", spec["eq"], actual))
    if "min" in spec and (actual is None or actual < int(spec["min"])):
        failures.append(Failure("row_count.min", spec["min"], actual))
    if "max" in spec and (actual is None or actual > int(spec["max"])):
        failures.append(Failure("row_count.max", spec["max"], actual))


def _check_clarification(spec: Any, obs: Observation, failures: list[Failure]) -> None:
    if not isinstance(spec, dict):
        return
    actual = obs.clarification or obs.form_fill_clarification
    if not actual:
        failures.append(Failure("clarification", spec, None))
        return
    if "kind" in spec and actual.get("kind") != spec["kind"]:
        failures.append(Failure("clarification.kind", spec["kind"], actual.get("kind")))
    if "options_len" in spec:
        options = actual.get("options") or actual.get("fields") or []
        if len(options) != int(spec["options_len"]):
            failures.append(
                Failure("clarification.options_len", spec["options_len"], len(options))
            )


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
        wanted_columns = [str(c) for c in (spec.get("columns") or [])]
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
            failures.append(Failure("file.filled_rows.min", filled["min"], len(data_rows)))


def _check_column_mapping(spec: Any, obs: Observation, failures: list[Failure]) -> None:
    """column_must_not_map - D-200형 회귀를 기계로 잡는다(§3.8).

    "가동률"이 cpu_usage 로 매핑되지 않았는가. 매핑 산출물과 실행 SQL 양쪽을 본다 -
    매핑 산출물이 없는 경로(결정적 조립 등)에서도 SQL에는 칼럼이 남기 때문이다.
    """
    if not isinstance(spec, list):
        return
    mapped = {str(v) for v in obs.column_mapping.values()} if obs.column_mapping else set()
    sql = (obs.executed_sql or "").lower()
    for column in spec:
        name = str(column)
        if name in mapped:
            failures.append(Failure("column_must_not_map", name, "column_mapping 에 존재"))
        elif name and re.search(rf"\b{re.escape(name.lower())}\b", sql):
            failures.append(Failure("column_must_not_map", name, "실행 SQL 에 존재"))


def evaluate_turn(
    scenario: Scenario, turn_index: int, turn: Turn, obs: Observation, group: Group
) -> Verdict:
    """턴 1회를 판정한다. 단언이 없으면 합격이 아니라 `manual`이다."""
    verdict = Verdict()
    failures: list[Failure] = []
    manual: list[str] = []
    expect = turn.expect

    if expect.get("manual_review"):
        manual.append(str(expect["manual_review"]))

    if "status" in expect and obs.status != expect["status"]:
        failures.append(Failure("status", expect["status"], obs.status))
    if "http_status" in expect and obs.http_status != int(expect["http_status"]):
        failures.append(Failure("http_status", expect["http_status"], obs.http_status))
    if "intent" in expect and obs.intent != expect["intent"]:
        failures.append(Failure("intent", expect["intent"], obs.intent))
    if "db_ids" in expect and sorted(obs.db_ids) != sorted(expect["db_ids"]):
        failures.append(Failure("db_ids", expect["db_ids"], obs.db_ids))
    if "has_file" in expect and obs.has_file != bool(expect["has_file"]):
        failures.append(Failure("has_file", expect["has_file"], obs.has_file))

    _check_row_count(expect.get("row_count"), obs.row_count, failures)
    _check_clarification(expect.get("clarification"), obs, failures)

    for needle in expect.get("response_must_contain") or []:
        if str(needle) not in obs.response:
            failures.append(Failure("response_must_contain", needle, "응답에 없음"))
    for needle in expect.get("response_must_not_contain") or []:
        if str(needle) in obs.response:
            failures.append(Failure("response_must_not_contain", needle, "응답에 있음"))

    sql = obs.executed_sql or ""
    for pattern in expect.get("sql_must_match") or []:
        if not re.search(str(pattern), sql):
            failures.append(Failure("sql_must_match", pattern, sql[:200] or None))
    for pattern in expect.get("sql_must_not_match") or []:
        if re.search(str(pattern), sql):
            failures.append(Failure("sql_must_not_match", pattern, sql[:200]))

    _check_column_mapping(expect.get("column_must_not_map"), obs, failures)
    _check_file(expect.get("file"), obs, failures, manual)

    for node in expect.get("node_path") or []:
        if node not in obs.node_path:
            failures.append(Failure("node_path", node, obs.node_path))
    for event in expect.get("sse_events") or []:
        if event not in obs.sse_events:
            failures.append(Failure("sse_events", event, sorted(set(obs.sse_events))))

    budget = expect.get("llm_calls")
    if isinstance(budget, dict) and "max" in budget and obs.llm_calls is not None:
        if obs.llm_calls > int(budget["max"]):
            failures.append(Failure("llm_calls.max", budget["max"], obs.llm_calls))
    budget = expect.get("retries")
    if isinstance(budget, dict) and "max" in budget and obs.retries is not None:
        if obs.retries > int(budget["max"]):
            failures.append(Failure("retries.max", budget["max"], obs.retries))

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

    if obs.error and not failures:
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
