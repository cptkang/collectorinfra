"""plans/195 W1 — 조사 브리핑 → 질의응답 질의(결정적 템플릿) 단위 테스트.

픽스처는 실 브리핑 모양이다 — 호스트형은 `build/manual_capture/app-capture-diag` 조사 레코드
(host.cpu)의 `playbook`에 사건창·연결 키를 더한 것, WAS형은
`tests/fixtures/plan187_w5b/latency_sql.json`.
"""

from __future__ import annotations

import ast
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.domain import investigation_requery as rq
from src.domain.investigation_requery import (
    REQUERY_CATALOG,
    RequeryTemplate,
    build_requery,
    window_phrase,
)
from src.domain.time_expr import interpret_detail

ROOT = Path(__file__).resolve().parents[1]
MASK = "***MASKED***"


def _sec(step: str, action: str, title: str, status: str = "ok", **extra: Any) -> dict[str, Any]:
    return {"step": step, "action": action, "title": title, "status": status, **extra}


def _host_playbook() -> dict[str, Any]:
    """host.cpu 실 레코드 모양(가린 사용자 뷰) + 사건창·확정 연결."""
    return {
        "id": "host.cpu",
        "version": 2,
        "matched_by": "symptom",
        "scope": {
            "alarm_instance": None,
            "host": "svr-was-01",
            "polestar_source": "polestar",
            "polestar_server": "svr-was-01",
            "polestar_reason": None,
            "alarm_name": "CPU 사용률",
            "targets": [],
            "polestar_link": "linked",
            "incident_window": {
                "start": "2026-10-26T19:10:00", "end": "2026-10-26T19:40:00", "minutes": 30,
            },
        },
        "sections": [
            _sec("S4", "host.usage_trend", "호스트 자원 시간대별 추이",
                 tools=["polestar_metric_trend"]),
            _sec("S6", "host.process_top", "프로세스 CPU 상위", badges=["현재값 · 알람 후 2분"]),
            _sec("S0r", "scope.host_to_was", "호스트의 WAS 인스턴스 영향"),
            _sec("S1", "apm.golden_signals", "WAS 골든 시그널", "skipped",
                 because="호스트의 WAS 인스턴스 영향 결과가 정상이라 생략"),
            _sec("S2", "apm.runtime_and_pool", "JVM 런타임 · DB 커넥션 풀"),
            _sec("S3a", "apm.active_services", "액티브 서비스"),
            _sec("S3b", "apm.active_sql", "실행 중 SQL",
                 table={"columns": ["sql"], "rows": [["SELECT * FROM ORDER_ITEM WHERE ID = ?"]]}),
            _sec("S5", "xsource.related_alarms", "전후 연관 알람",
                 axis_status={"polestar": "ok", "apm": "ok"}),
            _sec("S7", "change.recent", "최근 변경 대조",
                 axis_status={"polestar": "ok", "apm": "missing"}),
            _sec("S9", "synthesize", "종합", "pending_synthesis"),
        ],
    }


def _was_playbook() -> dict[str, Any]:
    raw = json.loads((ROOT / "tests/fixtures/plan187_w5b/latency_sql.json").read_text("utf-8"))
    return copy.deepcopy(raw["briefing"]["playbook"])


WIN = "2026년 10월 19일 오전 9시 30분부터 오전 10시 10분까지"
CHG = "2026년 10월 18일 오전 10시 10분부터 2026년 10월 19일 오전 10시 10분까지"


# ── 기본 산출 ────────────────────────────────────────────────────────────────


def test_was_playbook_prompts() -> None:
    out = build_requery(_was_playbook(), mask_marker=MASK)
    got = [(o["step"], o["prompt"], o["source_ref"], o["realtime"]) for o in out]
    expected = []
    for inst in ("A1", "A2", "B1"):
        expected.append(
            ("S1", f"{inst} {WIN} 에러율이랑 p95 응답시간 알려줘", "APM-HEALTH-colloquial", False)
        )
    for inst in ("A1", "A2", "B1"):
        expected.append(("S2", f"{inst} {WIN} 힙 메모리 추세 보여줘", "APM-RUNTIME-heap", False))
    for inst in ("A1", "A2", "B1"):
        expected.append(
            ("S2", f"{inst} DB 커넥션 풀 지금 얼마나 쓰고 있어?", "APM-POOL-inst", True)
        )
    for inst in ("A1", "A2", "B1"):
        expected.append(("S3", f"{inst} {WIN} 느린 거래 보여줘", "APM-SLOWTX-10m", False))
    for inst in ("A1", "A2", "B1"):
        expected.append(
            ("S7", f"{inst} {CHG} 소스 변경 감지 이력 보여줘", "APM-CHANGES-default", False)
        )
    assert got == expected
    assert {o["path"] for o in out} == {"apm_query"}
    assert {o["axis"] for o in out} == {"apm"}
    assert all("polestar_source" not in o for o in out)
    assert set(out[0]) == {"step", "title", "prompt", "path", "source_ref", "realtime", "axis"}
    assert out[0]["title"] == "WAS 골든 시그널"


def test_host_playbook_prompts() -> None:
    out = build_requery(_host_playbook(), mask_marker=MASK)
    win = "2026년 10월 26일 오후 7시 10분부터 오후 7시 40분까지"
    assert [(o["step"], o["prompt"], o["realtime"]) for o in out] == [
        ("S0r", "svr-was-01 서버에 올라가 있는 WAS 인스턴스가 뭐야?", False),
        ("S2", f"svr-was-01 {win} GC 시간이랑 스레드 수 추세 보여줘", False),
        ("S2", "svr-was-01 커넥션 풀이랑 실행 모드별 서비스 개수 보여줘", True),
        ("S3a", "svr-was-01 실행 중인 서비스 오래 걸린 순으로 보여줘", True),
    ]


def test_same_input_same_output() -> None:
    pb = _was_playbook()
    first = build_requery(pb, mask_marker=MASK)
    assert build_requery(copy.deepcopy(pb), mask_marker=MASK) == first
    assert build_requery(pb, mask_marker=MASK) == first
    assert pb == _was_playbook()  # 입력을 바꾸지 않는다


# ── 생략 규칙 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("playbook", [None, {}, {"scope": {}}, {"sections": []}, "x"])
def test_no_playbook_zero(playbook: Any) -> None:
    assert build_requery(playbook, mask_marker=MASK) == []


def test_holmes_only_briefing_zero() -> None:
    """HolmesGPT 단독 조사 — 브리핑에 플레이북 섹션이 없으면 0건(Q7)."""
    briefing = {"summary": "요약", "limitations": []}
    assert build_requery(briefing.get("playbook"), mask_marker=MASK) == []


@pytest.mark.parametrize("value", ["", "   ", None, MASK, f"svr-{MASK}"])
def test_empty_or_masked_host_skipped(value: Any) -> None:
    pb = _host_playbook()
    pb["scope"]["host"] = value
    assert build_requery(pb, mask_marker=MASK) == []


def test_masked_instance_dropped_only() -> None:
    pb = _was_playbook()
    pb["scope"]["targets"][1]["instance"] = MASK
    pb["scope"]["targets"][2]["instance"] = ""
    prompts = [o["prompt"] for o in build_requery(pb, mask_marker=MASK)]
    assert prompts and all(p.startswith("A1 ") for p in prompts)
    assert not any(MASK in p for p in prompts)


def test_instance_targets_capped_and_deduped() -> None:
    pb = _was_playbook()
    pb["scope"]["targets"] = [{"instance": n} for n in ("A1", "A1", "A2", "A3", "A4", "A5")]
    out = build_requery(pb, mask_marker=MASK)
    s1 = [o["prompt"].split(" ")[0] for o in out if o["step"] == "S1"]
    assert s1 == ["A1", "A2", "A3"]


def test_no_targets_falls_back_to_host() -> None:
    pb = _was_playbook()
    pb["scope"]["targets"] = []
    s1 = [o for o in build_requery(pb, mask_marker=MASK) if o["step"] == "S1"]
    assert [o["prompt"] for o in s1] == [f"wasdemo01 WAS {WIN} 에러율이랑 p95 응답시간 알려줘"]
    assert s1[0]["source_ref"] == "APM-HEALTH-host-30m"


@pytest.mark.parametrize("status", ["skipped", "missing"])
def test_skipped_missing_sections_zero(status: str) -> None:
    pb = _was_playbook()
    for s in pb["sections"]:
        s["status"] = status
    assert build_requery(pb, mask_marker=MASK) == []


def test_mixed_axis_status_used() -> None:
    """혼합 단계는 축별 상태로 판단한다 — 제니퍼 축 결손이면 변경 질의 0."""
    pb = _was_playbook()
    s7 = next(s for s in pb["sections"] if s["step"] == "S7")
    s7["axis_status"] = {"apm": "missing", "polestar": "ok"}
    assert not [o for o in build_requery(pb, mask_marker=MASK) if o["step"] == "S7"]
    s7["axis_status"] = {"apm": "ok", "polestar": "skipped"}
    assert len([o for o in build_requery(pb, mask_marker=MASK) if o["step"] == "S7"]) == 3


def test_reused_section_same_template() -> None:
    pb = _was_playbook()
    base = build_requery(pb, mask_marker=MASK)
    for s in pb["sections"]:
        s["reused_from"] = "inv-parent-1"
    assert build_requery(pb, mask_marker=MASK) == base


def test_reason_fields_not_read() -> None:
    pb = _was_playbook()
    base = build_requery(pb, mask_marker=MASK)
    for s in pb["sections"]:
        s["reason"] = "그 서버 조회 실패"
        s["reason_detail"] = "운영자 상세 apm_slow_transactions(instance_name=A1)"
    assert build_requery(pb, mask_marker=MASK) == base


@pytest.mark.parametrize("window", [None, {}, {"start": "x", "end": "y"},
                                    {"start": "2026-10-19T10:10:00", "end": "2026-10-19T09:30:00"},
                                    {"start": MASK, "end": MASK}])
def test_bad_window_keeps_only_timeless(window: Any) -> None:
    pb = _was_playbook()
    if window is None:
        pb["scope"].pop("incident_window")
    else:
        pb["scope"]["incident_window"] = window
    out = build_requery(pb, mask_marker=MASK)
    assert [o["source_ref"] for o in out] == ["APM-POOL-inst"] * 3


# ── 출처 없는 action 부재 고정 (D-192 선례) ───────────────────────────────────

ABSENT_ACTIONS = (
    "host.usage_trend",
    "host.process_top",
    "host.alarm_history",
    "xsource.related_alarms",
    "host.resource_status",
    "host.topology",
    "apm.active_sql",
    "dpm.db_health",
    "scope.resolve_was_targets",
    "synthesize",
)


@pytest.mark.parametrize("action", ABSENT_ACTIONS)
def test_unsourced_action_zero(action: str) -> None:
    assert not [t for t in REQUERY_CATALOG if t.action == action]
    for scope_src in (_host_playbook(), _was_playbook()):
        pb = {"scope": scope_src["scope"], "sections": [_sec("SX", action, "제목")]}
        assert build_requery(pb, mask_marker=MASK) == []


def test_polestar_axis_absent_in_catalog() -> None:
    """폴스타 축 질의는 검증 출처(실 DB 실행 검증 골드)가 아직 없다 — 0건(W0 잔여)."""
    assert not [t for t in REQUERY_CATALOG if t.axis == "polestar"]
    assert not [t for t in REQUERY_CATALOG if t.action == "change.recent" and t.axis != "apm"]


# ── 폴스타 축 계약(plans/196) — 카탈로그 항목을 주입해 확인 ───────────────────


@pytest.fixture
def polestar_entry(monkeypatch: pytest.MonkeyPatch) -> RequeryTemplate:
    tpl = RequeryTemplate("xsource.related_alarms", "polestar", "server", "incident",
                          "alarm_query", "", "test-only", False,
                          "{target} 서버의 {window} 알람 이력")
    monkeypatch.setattr(rq, "REQUERY_CATALOG", (tpl,))
    return tpl


def test_polestar_entry_uses_polestar_server(polestar_entry: RequeryTemplate) -> None:
    pb = _host_playbook()
    pb["scope"]["host"] = "jennifer-host"
    out = build_requery(pb, mask_marker=MASK)
    assert [o["prompt"] for o in out] == [
        "svr-was-01 서버의 2026년 10월 26일 오후 7시 10분부터 오후 7시 40분까지 알람 이력"
    ]
    assert out[0]["axis"] == "polestar" and out[0]["polestar_source"] == "polestar"


@pytest.mark.parametrize("link", ["absent", "unresolved"])
def test_polestar_link_skip(polestar_entry: RequeryTemplate, link: str) -> None:
    pb = _host_playbook()
    pb["scope"]["polestar_link"] = link
    assert build_requery(pb, mask_marker=MASK) == []


@pytest.mark.parametrize("server", ["", None, MASK])
def test_polestar_server_empty_skip(polestar_entry: RequeryTemplate, server: Any) -> None:
    pb = _host_playbook()
    pb["scope"]["polestar_server"] = server
    assert build_requery(pb, mask_marker=MASK) == []


@pytest.mark.parametrize("status", ["skipped", "missing"])
def test_polestar_axis_status_skip(polestar_entry: RequeryTemplate, status: str) -> None:
    pb = _host_playbook()
    s5 = next(s for s in pb["sections"] if s["step"] == "S5")
    s5["axis_status"] = {"polestar": status, "apm": "ok"}
    assert build_requery(pb, mask_marker=MASK) == []


# ── 문구 규칙 ────────────────────────────────────────────────────────────────

DEMONSTRATIVES = (
    "그 서버", "이 서버", "해당", "그 인스턴스", "이 인스턴스", "위 ", "앞의", "방금", "저 ",
)
RELATIVE = ("최근", "지난", "오늘", "어제", "분 전", "시간 전")


def _all_prompts() -> list[str]:
    return [o["prompt"] for pb in (_host_playbook(), _was_playbook())
            for o in build_requery(pb, mask_marker=MASK)]


def test_no_demonstratives_or_relative_time() -> None:
    prompts = _all_prompts()
    assert prompts
    for p in prompts:
        assert not any(w in p for w in DEMONSTRATIVES), p
        assert not any(w in p for w in RELATIVE), p


def test_no_tool_or_view_names() -> None:
    """벤치 린트와 같은 규칙 — 도구명·보기명·영문 인자 원문이 질의에 섞이지 않는다."""
    for p in _all_prompts():
        assert "apm_" not in p and "apm." not in p and "polestar_" not in p, p
        assert "SELECT" not in p and "instance_name" not in p, p
    for t in REQUERY_CATALOG:
        assert "apm_" not in t.template and "apm." not in t.template


@pytest.mark.parametrize("hour", range(24))
def test_window_phrase_round_trips_minute_exact(hour: int) -> None:
    """질의응답 시간 해석기(D-333)가 문구를 사건창 그대로(분 정밀) 되읽는다 — 되묻기 없음."""
    now = datetime(2026, 10, 28, 9, 0)
    for minute in (0, 10, 45):
        start = datetime(2026, 10, 26, hour, minute)
        end = start + timedelta(minutes=30)
        phrase = window_phrase(start, end)
        got = interpret_detail(f"A1 {phrase} 오류 기록 보여줘", now, subject="event")
        assert got.clarify is None, phrase
        res = got.resolution
        assert res is not None, phrase
        got_range = (res.start.replace(tzinfo=None), res.end.replace(tzinfo=None))
        assert got_range == (start, end), phrase


def test_window_phrase_cross_date() -> None:
    start, end = datetime(2026, 10, 26, 23, 50), datetime(2026, 10, 27, 0, 20)
    phrase = window_phrase(start, end)
    assert phrase == "2026년 10월 26일 오후 11시 50분부터 2026년 10월 27일 0시 20분까지"
    res = interpret_detail(phrase, datetime(2026, 10, 28), subject="event").resolution
    assert res is not None
    assert (res.start.replace(tzinfo=None), res.end.replace(tzinfo=None)) == (start, end)


def test_aware_window_converted_to_kst() -> None:
    pb = _was_playbook()
    pb["scope"]["incident_window"] = {"start": "2026-10-19T00:30:00+00:00",
                                      "end": "2026-10-19T01:10:00+00:00"}
    out = build_requery(pb, mask_marker=MASK)
    assert out[0]["prompt"] == f"A1 {WIN} 에러율이랑 p95 응답시간 알려줘"


def test_realtime_flags() -> None:
    rt = {t.view for t in REQUERY_CATALOG if t.realtime}
    assert rt == {"apm.pool", "apm.active"}
    assert all(t.window == "none" for t in REQUERY_CATALOG if t.realtime)


# ── 검증 출처 실재 ───────────────────────────────────────────────────────────


def _bench() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load((ROOT / "testdata/apm_bench/scenarios.yaml").read_text("utf-8"))
    return {item["id"]: item for item in data}


@pytest.mark.parametrize("tpl", REQUERY_CATALOG, ids=lambda t: f"{t.action}:{t.target}:{t.view}")
def test_source_ref_exists_in_bench(tpl: RequeryTemplate) -> None:
    """카탈로그의 출처 id가 벤치에 있고, 첫 턴의 기대 처리기·보기·대상 종류가 템플릿과 같다."""
    item = _bench().get(tpl.source_ref)
    assert item is not None, tpl.source_ref
    expect = item["turns"][0]["expect"]
    assert tpl.path in expect["agents"]
    assert tpl.view in expect["views"]
    assert str(expect["target"]).split(":")[0] == tpl.target
    assert not expect.get("refusal") and not expect.get("clarify")
    if tpl.realtime:
        assert expect["window"] == "current"


def test_module_is_stdlib_only() -> None:
    tree = ast.parse((ROOT / "src/domain/investigation_requery.py").read_text("utf-8"))
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert all(not (m or "").startswith("src") for m in mods)
    assert mods <= {"__future__", "collections.abc", "dataclasses", "datetime", "typing"}
