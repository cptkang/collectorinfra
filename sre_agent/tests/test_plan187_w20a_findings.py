"""plans/187 W20a — 「조사 흐름」 결정적 문장(결과 한 줄 · 조회 · 왜 · 건너뜀) 단위 검증.

- `ACTION_TITLES` 전 액션 + `apm.active_sql`(계약 §3 모양) 문형 커버리지
- 표 밖 수치 0 — 문장 속 숫자가 모두 그 단계 표·data 값의 반올림(개수·순위·행 수 제외)
- 결손·실패 단계는 사유 문장 · 현재값 꼬리 · 알람 대비 10분 초과 주의
- `TOOL_PHRASES` 사전 밖 도구는 문장 없음 · `when` 원소 문장 · 건너뜀 사유(조건식 원문 없음)
- 실행 기록 새 키(`finding`·`because`·`phrase`)의 가림·상한 · `step_skip` kind
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from sre_agent.application.playbook_steps import ACTION_TITLES, StepResult
from sre_agent.domain.activity_log import CLIP_MARK, KINDS, ActivityLimits, ActivityLog
from sre_agent.domain.playbook import parse_playbook
from sre_agent.domain.playbook_expr import compile_expr
from sre_agent.domain.step_findings import (
    BUDGET_SKIP,
    REALTIME_DRIFT_NOTE,
    TOOL_PHRASES,
    WHEN_SKIP_FALLBACK,
    WHEN_UNKNOWN,
    default_because,
    fmt_number,
    skip_because,
    summarize,
    tool_phrase,
    when_because,
)
from sre_agent.infrastructure.activity_masking import mask_activity_text
from test_plan187_w2_runner import SCENARIOS, FakeCaller, _load, _run

# ── 표 밖 수치 검사 ─────────────────────────────────────────────────────────

_NUM = re.compile(r"(?<![A-Za-z_\d.])(\d+(?:\.\d+)?)(?![\d.])(\s*(?:건|개|행|위))?")


def _leaves(value: Any, out: list[float]) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        out.append(float(value))
    elif isinstance(value, str):
        out.extend(float(x) for x in re.findall(r"\d+(?:\.\d+)?", value))
    elif isinstance(value, dict):
        for v in value.values():
            _leaves(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _leaves(v, out)


def _row_count(section: dict) -> int:
    data = section.get("data") or {}
    counts = [len((section.get("table") or {}).get("rows") or [])]
    for v in data.values() if isinstance(data, dict) else []:
        if isinstance(v, (list, dict)):
            counts.append(len(v))
    return max(counts)


def outside_numbers(sentence: str, section: dict) -> list[str]:
    """문장 속 수치 중 표·data 값의 반올림이 아닌 것(개수·순위·행 수는 행 수 이하 정수면 허용)."""
    values: list[float] = []
    _leaves(section.get("table"), values)
    _leaves(section.get("data"), values)
    bad = []
    for m in _NUM.finditer(sentence):
        token, suffix = m.group(1), m.group(2)
        x = float(token)
        if suffix and x.is_integer() and x <= _row_count(section):
            continue
        digits = len(token.split(".")[1]) if "." in token else 0
        if not any(round(v, digits) == x for v in values):
            bad.append(token)
    return bad


# ── 액션별 섹션 표본 ─────────────────────────────────────────────────────────


def _fixture_sections() -> dict[str, dict]:
    """10 픽스처 실 실행에서 액션별 ok 섹션 1건씩(실 데이터 모양)."""
    out: dict[str, dict] = {}
    for name in SCENARIOS:
        fx = _load(name)
        run = _run(fx, FakeCaller(fx, strict=False))
        for s in run.sections:
            if s.status == "ok" and s.action not in out:
                out[s.action] = s.to_dict()
    return out


def _simple(rows: list[dict], cols: list[str]) -> dict:
    return {"columns": cols, "rows": [[r.get(c) for c in cols] for r in rows]}


ACTIVE_SQL_ROWS = [
    {"instance": "A1", "application": "/acct/inquiry", "txid": "t1", "cpu_ms": 4210,
     "elapsed_ms": 9120.4, "sql": "SELECT * FROM ACCT WHERE ID = ? AND TYPE = ?"},
    {"instance": "A1", "application": "/acct/list", "txid": "t2", "cpu_ms": 1200,
     "elapsed_ms": 3000, "sql": "SELECT 1 FROM DUAL"},
]


def _handmade() -> dict[str, dict]:
    host_was = {"host": "wasdemo01", "instances": [
        {"instance": "A1", "stats": {"tps": 9.8, "p95_ms": 6200}, "verdict": "degraded"},
        {"instance": "A2", "stats": {"tps": 9.6, "p95_ms": 900.25}, "verdict": "normal"},
    ]}
    active = [
        {"instance_name": "A1", "application": "/acct/inquiry", "elapsed_ms": 9120,
         "cpu_ms": 4210, "sql_count": 37, "running_mode": "SQL", "running_text": "x"},
        {"instance_name": "A1", "application": "/acct/list", "elapsed_ms": 15000.5,
         "cpu_ms": 200, "running_mode": "RUN", "running_text": "y"},
    ]
    sql_cols = ["instance", "application", "cpu_ms", "elapsed_ms", "sql"]
    err = [{"error_type": "SQLException", "count": 12}, {"error_type": "Timeout", "count": 3}]
    return {
        "scope.host_to_was": {
            "status": "ok", "verdict": "degraded",
            "table": {"columns": ["instance", "tps", "p95_ms", "verdict"],
                      "rows": [["A1", 9.8, 6200, "degraded"], ["A2", 9.6, 900.25, "normal"]]},
            "data": host_was, "badges": [], "missing": [], "reason": None,
        },
        "apm.active_services": {
            "status": "ok", "verdict": None,
            "table": _simple(active, ["application", "elapsed_ms", "cpu_ms", "running_mode",
                                      "running_text"]),
            "data": {"rows": active, "summary": None},
            "badges": ["현재값 · 알람 후 3분"], "missing": [], "reason": None,
        },
        "apm.active_sql": {
            "status": "ok", "verdict": None,
            "table": _simple(ACTIVE_SQL_ROWS, sql_cols),
            "data": {"rows": ACTIVE_SQL_ROWS, "queried_at": "2026-10-22T14:20:00"},
            "badges": ["현재값 · 알람 후 25분"], "missing": [], "reason": None,
        },
        "apm.events_errors": {
            "status": "ok", "verdict": None, "table": _simple(err, ["error_type", "count"]),
            "data": {"errors_by_type": err, "rows": []}, "badges": [], "missing": [],
            "reason": None,
        },
        "host.resource_status": {
            "status": "ok", "verdict": None,
            "table": _simple([{"name": "wasdemo01", "status": "up"}], ["name", "status"]),
            "data": {"rows": [{"name": "wasdemo01", "status": "up"}]}, "badges": [],
            "missing": [], "reason": None,
        },
        "host.topology": {
            "status": "ok", "verdict": None, "table": {"columns": ["name", "status"], "rows": []},
            "data": {"rows": []}, "badges": [], "missing": [], "reason": None,
        },
        "host.alarm_history": {
            "status": "ok", "verdict": None,
            "table": _simple([{"alarm_time": "2026-10-20T07:00:00", "severity": 3,
                               "alarm_name": "CPU"}], ["alarm_time", "severity", "alarm_name"]),
            "data": {"rows": [{"alarm_time": "2026-10-20T07:00:00", "severity": 3,
                               "alarm_name": "CPU"}]},
            "badges": [], "missing": [], "reason": None,
        },
    }


@pytest.fixture(scope="module")
def sections() -> dict[str, dict]:
    return {**_fixture_sections(), **_handmade()}


def test_every_action_has_a_finding_without_outside_numbers(sections: dict[str, dict]) -> None:
    actions = [a for a in ACTION_TITLES if a != "synthesize"]
    assert "apm.active_sql" in actions
    for action in actions:
        if action == "dpm.db_health":
            continue  # 기본 레지스트리는 미연동(skipped) — 결손 문장 검사에서 본다
        sec = sections[action]
        text = summarize(action, sec, {})
        assert text, action
        assert outside_numbers(text, sec) == [], (action, text)


def test_synthesize_has_no_finding() -> None:
    assert summarize("synthesize", {"status": "pending_synthesis"}) is None


def test_precise_patterns_read_table_values(sections: dict[str, dict]) -> None:
    s = summarize("apm.active_sql", sections["apm.active_sql"])
    assert s.startswith("실행 중 SQL 2건 — 1위 A1 /acct/inquiry CPU 4210ms · 경과 9120.4ms: SELECT")
    assert "현재값" in s and REALTIME_DRIFT_NOTE in s  # 알람 후 25분 > 10분
    s = summarize("apm.active_services", sections["apm.active_services"])
    assert "CPU 시간 1위 A1 /acct/inquiry 4210ms" in s and "SQL 37건" not in s  # 표 밖 열 제외
    assert "경과 1위 A1 /acct/list 15000.5ms" in s
    assert s.endswith("· 현재값") and REALTIME_DRIFT_NOTE not in s  # 3분 — 주의 없음
    s = summarize("scope.host_to_was", sections["scope.host_to_was"])
    assert s == "WAS 인스턴스 2개 중 이상 1개(A1) — A1 p95 6200ms"
    s = summarize("host.topology", sections["host.topology"])
    assert s == "판정 없음 · 결과 0건"


def test_runtime_finding_cites_cpu_owner_reading(sections: dict[str, dict]) -> None:
    sec = sections["apm.runtime_and_pool"]
    s = summarize("apm.runtime_and_pool", sec, {"cpu_owner": "was_logic"})
    assert "CPU 주인 = WAS 처리(부하 증가 없음)(R-E)" in s


def test_no_was_and_missing_and_skipped_are_reason_sentences() -> None:
    assert summarize("scope.host_to_was", {"status": "ok", "verdict": "no_was", "data": {}}) == (
        "이 호스트에 WAS 인스턴스 없음"
    )
    miss = {"status": "missing", "reason": "apm_slow_transactions: timeout", "missing": []}
    assert summarize("apm.slow_tx_breakdown", miss) == "수집 결손 — apm_slow_transactions: timeout"
    assert summarize("host.process_top", {"status": "missing", "missing": ["x_tool"]}) == (
        "수집 결손 — x_tool 없음"
    )
    assert summarize("dpm.db_health", {"status": "skipped", "reason": "not_connected"}) == (
        "생략 — not_connected"
    )


def test_fmt_number_only_rounds() -> None:
    assert fmt_number(43) == "43"
    assert fmt_number(43.25) == "43.2"
    assert fmt_number(43.0) == "43"
    assert fmt_number(0.0123) == "0.012"
    assert fmt_number(True) is None and fmt_number("7") is None


# ── 조회 문장 ───────────────────────────────────────────────────────────────


def test_tool_phrases_take_target_window_count_only() -> None:
    args = {"hostname": "SAE0011", "top_n": 10, "sort": "cpu"}
    p = tool_phrase("polestar_process_snapshot", args)
    assert p == "폴스타 · SAE0011 프로세스 목록(CPU 순 · 상위 10) · 현재값"
    p = tool_phrase("apm_app_health", {"instance_name": "A1",
                                       "reference_time": "2026-10-22T14:10:00",
                                       "lookback_minutes": 40})
    assert p == "제니퍼 · A1 응답·처리량·오류(13:30~14:10)"
    p = tool_phrase("apm_active_detail", {"txid": "t1", "source_id": 3})
    assert p == "제니퍼 · 실행 중 요청의 SQL 문 · 현재값"


def test_tool_outside_dictionary_has_no_phrase() -> None:
    assert "bash" not in TOOL_PHRASES
    assert tool_phrase("bash", {"command": "ls"}) is None
    assert tool_phrase("kubectl_describe", {}) is None


# ── 왜 · 건너뜀 ─────────────────────────────────────────────────────────────

TITLES = {"S0r": "호스트의 WAS 인스턴스 영향", "S2": "JVM 런타임 · DB 커넥션 풀",
          "S3a": "액티브 서비스"}


def test_when_because_cites_true_atoms_and_finding() -> None:
    expr = compile_expr("S0r.verdict == 'degraded' and cpu_owner in ['was_load', 'was_logic']")
    ctx = {"S0r": {"verdict": "degraded", "status": "ok"}, "cpu_owner": "was_load"}
    text = when_because(expr, ctx, titles=TITLES, findings={"S2": "A1 프로세스 CPU 최대 74%"},
                        tail="지금 무엇을 처리 중인지 확인", reading_steps={"cpu_owner": ["S2"]})
    assert text.startswith(
        "호스트의 WAS 인스턴스 영향에서 이상 판정 · CPU 주인 = WAS 처리(부하 증가)"
    )
    assert "JVM 런타임 · DB 커넥션 풀: A1 프로세스 CPU 최대 74%" in text
    assert text.endswith("→ 지금 무엇을 처리 중인지 확인")
    assert "==" not in text and "'" not in text  # 조건식 원문 없음
    unknown = when_because(expr, {}, titles=TITLES, findings={}, tail="x")
    assert unknown.endswith("확인을 위해 실행") and "x" not in unknown  # 작성자 문장은 참일 때만


def test_when_because_unknown_names_condition_and_reason_without_author_why() -> None:
    expr = compile_expr("S2.status == 'ok' and cpu_owner in ['was_load', 'was_logic']")
    ctx = {"S2": {"status": "ok"}, "cpu_owner": None}
    text = when_because(expr, ctx, titles=TITLES, findings={"S2": "A1 최대 88%"},
                        tail="CPU를 WAS가 쓰고 있어 본다",
                        reading_reasons={"cpu_owner": "TPS 결손 — 부하/로직 구분 불가"})
    assert text == (
        "「CPU 주인 = WAS 처리(부하 증가) 또는 WAS 처리(부하 증가 없음)」인지 판정할 수 없어"
        "(사유: TPS 결손 — 부하/로직 구분 불가) 확인을 위해 실행"
    )
    # 스텝 원소 — 의도된 생략(건너뜀)과 결손을 구분
    expr = compile_expr("S1.verdict == 'degraded'")
    skipped = when_because(expr, {}, titles={"S1": "WAS 골든 시그널"}, findings={}, tail="x")
    assert "(사유: WAS 골든 시그널을 건너뜀)" in skipped and "결손" not in skipped
    missing = when_because(expr, {"S1": {"status": "missing", "verdict": None}},
                           titles={"S1": "WAS 골든 시그널"}, findings={}, tail="x")
    assert "(사유: WAS 골든 시그널 결과 결손)" in missing
    assert WHEN_UNKNOWN == "실행 조건을 판정할 수 없어 확인을 위해 실행"


def test_usage_trend_shows_percent_unit_for_utilization_kinds() -> None:
    sec = {"status": "ok", "verdict": "degraded", "table": {"columns": [], "rows": []},
           "data": {"findings": {"cpu": {"peak_value": 96.0, "baseline_mean": 43.0,
                                         "is_anomalous": True},
                                 "disk_io": {"peak_value": 12.0, "is_anomalous": False}}}}
    text = summarize("host.usage_trend", sec, {})
    assert "CPU 이상(최대 96% · 평소 평균 43%)" in text
    assert "디스크 I/O 평소 범위(최대 12)" in text  # 단위 미상 — 붙이지 않음


def test_when_because_drops_duplicate_status_atom_and_ne_symbol() -> None:
    expr = compile_expr("S0r.status == 'ok' and S0r.verdict != 'no_was'")
    text = when_because(expr, {"S0r": {"status": "ok", "verdict": "normal"}}, titles=TITLES,
                        findings={}, tail="t")
    assert text == "이 호스트에 WAS 인스턴스가 있음 → t"
    assert "≠" not in when_because(compile_expr("S0r.verdict != 'odd'"),
                                    {"S0r": {"verdict": "normal"}}, titles=TITLES,
                                    findings={}, tail="t")


def test_skip_because_states_actual_value_without_expression() -> None:
    expr = compile_expr("S0r.verdict == 'degraded'")
    text = skip_because(expr, {"S0r": {"verdict": "normal"}}, TITLES)
    assert text == "호스트의 WAS 인스턴스 영향 결과가 정상이라 생략"
    expr = compile_expr("cpu_owner in ['was_load', 'was_logic']")
    assert skip_because(expr, {"cpu_owner": "other_process"}, TITLES) == (
        "CPU 주인 = WAS 밖 프로세스라 생략"
    )
    expr = compile_expr("S3a.status == 'ok'")
    assert skip_because(expr, {"S3a": {"status": "missing"}}, TITLES) == (
        "액티브 서비스 결과가 결손이라 생략"
    )
    assert skip_because(expr, {"S3a": {"status": "skipped"}}, TITLES) == (
        "액티브 서비스를 건너뛰어 생략"
    )
    expr = compile_expr("S0r.verdict == 'degraded' or S2.pool == 'exhausted'")
    assert skip_because(expr, {}, TITLES) == WHEN_SKIP_FALLBACK
    assert BUDGET_SKIP == "시간 예산 소진으로 생략"


def test_default_because_prefers_why_then_focus() -> None:
    assert default_because("작성자 문장", "초점", "제목") == "작성자 문장"
    assert default_because(None, "인스턴스별 CPU", "제목") == "인스턴스별 CPU를 확인"
    assert default_because(None, None, "프로세스 CPU 상위") == "프로세스 CPU 상위를 확인"
    assert default_because(None, "느린 거래 분해", "x") == "느린 거래 분해를 확인"
    assert default_because(None, "힙 추세", "x") == "힙 추세를 확인"
    assert default_because(None, "응답 시간", "x") == "응답 시간을 확인"


def test_narrate_why_is_optional_schema_key() -> None:
    base = {
        "id": "was.why", "version": 1, "match": {"resource_type": "apm.Instance"},
        "runbook": {"meaning": "m", "impact": "i", "owner": "o", "last_reviewed": "2026-10-17"},
    }
    pb = parse_playbook({**base, "steps": [
        {"id": "S1", "action": "apm.golden_signals",
         "narrate": {"focus": "p95", "why": "알람 인스턴스의 응답부터 본다"}},
        {"id": "S2", "action": "apm.runtime_and_pool", "narrate": {"focus": "힙"}},
    ]})
    assert pb.steps[0].narrate.why == "알람 인스턴스의 응답부터 본다"
    assert pb.steps[1].narrate.why is None


# ── 실행 기록 새 키 ─────────────────────────────────────────────────────────


def test_new_activity_keys_are_masked_clipped_and_optional() -> None:
    log = ActivityLog(masker=mask_activity_text, limits=ActivityLimits(max_label_bytes=64))
    assert "step_skip" in KINDS
    log.append("step_end", engine="playbook", label="S1", finding="--password=hunter2 결과")
    log.append("step_start", engine="playbook", label="S1", because="mysql -uroot -pSecret99 왜")
    log.append("tool_start", engine="playbook", label="t", phrase="token: abcdef123456 조회")
    log.append("step_skip", engine="playbook", label="S3 x", status="skipped", because="가" * 200)
    log.append("tool_end", engine="playbook", label="t")
    ev = log.since(0)["events"]
    assert "hunter2" not in ev[0]["finding"]
    assert "Secret99" not in ev[1]["because"]
    assert "abcdef123456" not in ev[2]["phrase"]
    assert ev[3]["because"].endswith(CLIP_MARK) and len(ev[3]["because"].encode()) <= 64
    assert {"finding", "because", "phrase"}.isdisjoint(ev[4])


def test_step_result_dict_shape_feeds_summarize() -> None:
    res = StepResult(status="ok", table={"columns": ["a"], "rows": [[1.25]]})
    assert summarize("host.resource_status", res.to_dict()) == "판정 없음 · 표 1행 · 첫 행 a 1.2"
