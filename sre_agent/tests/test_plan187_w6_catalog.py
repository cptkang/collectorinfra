"""plans/187 W6 — 카탈로그 나머지 유형 플레이북 YAML · ITSM·DPM 어댑터 슬롯 · S1 임계 인자 (D-341).

실 YAML 디렉토리(`sre_agent/config/playbooks/`)를 그대로 읽어 매칭 표·유형별 종단·런북 커버리지·
대응 문구 경계를 검사한다. 녹화는 W0 골든 픽스처를 재사용하고, 호스트 유형은 그 녹화에 최소
항목을 더해 테스트 안에서 만든다. 실 MCP·LLM·DB 호출 0.
"""

from __future__ import annotations

import copy
import json
import re
import threading
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from sre_agent.application.investigation_guidance import classify_alarm_kind
from sre_agent.application.playbook_runner import (
    alarm_facts_from_payload,
    compute_windows,
    initial_scope,
    run_playbook,
)
from sre_agent.application.playbook_steps import (
    DEFAULT_P95_RATIO,
    NOT_CONNECTED,
    STEP_LIBRARY,
    VERDICT_SOURCE_RULE,
    StepContext,
    golden_signals,
    target_verdict,
)
from sre_agent.domain import source_adapter as sa
from sre_agent.domain.playbook import (
    APM_FALLBACK_ID,
    FALLBACK_ID,
    STEP_ACTIONS,
    AlarmFacts,
    Playbook,
    WindowSpec,
    select_playbook,
)
from sre_agent.infrastructure.playbook_loader import DEFAULT_PLAYBOOK_DIR, load_playbooks

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plan187"
_APM = "apm.Instance"

#: §4.1 유형 표 중 YAML로 둔 것(db.*는 DPM 연동 뒤라 만들지 않는다 — §6 · G-6).
CATALOG_IDS = sorted(
    [
        "was.latency", "was.error", "was.active", "was.heap", "was.pool",
        "was.sql", APM_FALLBACK_ID,  # plans/196 D2
        "host.cpu", "host.memory", "host.fs", "host.disk_io", "host.process", "host.network",
        FALLBACK_ID,
    ]
)  # fmt: skip

#: 유형 13종 × 대표 알람 사실 → 기대 플레이북(W1 표를 실제 YAML로).
TYPE_TABLE: list[tuple[str, AlarmFacts, str]] = [
    ("was.latency", AlarmFacts(_APM, "apm", ("latency", "response_time")), "was.latency"),
    ("was.error", AlarmFacts(_APM, "apm", ("was_error_burst",)), "was.error"),
    ("was.active", AlarmFacts(_APM, "apm", ("was_service_queuing",)), "was.active"),
    ("was.heap", AlarmFacts(_APM, "apm", ("was_heap_pressure",)), "was.heap"),
    ("was.pool", AlarmFacts(_APM, "apm", ("was_db_pool_exhaustion",)), "was.pool"),
    ("was.sql", AlarmFacts(_APM, "apm", ("was_sql_pattern",)), "was.sql"),  # plans/196 D2
    ("was.unmatched", AlarmFacts(_APM, "apm", ()), APM_FALLBACK_ID),  # plans/196 D2 ②
    ("host.cpu", AlarmFacts("server", "cpu", ()), "host.cpu"),
    ("host.memory", AlarmFacts("server", "memory", ()), "host.memory"),
    ("host.fs", AlarmFacts("server", "disk", ("filesystem",)), "host.fs"),
    ("host.disk_io", AlarmFacts("server", "disk", ("disk_io",)), "host.disk_io"),
    ("host.process", AlarmFacts("server", "process", ()), "host.process"),
    ("host.network", AlarmFacts("server", "network", ()), "host.network"),
    ("db.session", AlarmFacts("dpm.Instance", None, ("lock",)), FALLBACK_ID),
    ("fallback", AlarmFacts("server", "log", ()), FALLBACK_ID),
]

#: 게이트웨이 `was_signals` kind(W0 실측 8종 중 유형이 정해진 6종) → 플레이북.
SIGNAL_KINDS: dict[str, str] = {
    "was_error_burst": "was.error",
    "was_service_queuing": "was.active",
    "was_thread_pool_exhaustion": "was.active",
    "was_heap_pressure": "was.heap",
    "was_gc_stall": "was.heap",
    "was_db_pool_exhaustion": "was.pool",
}

#: 호스트 알람 → (알람명, 기대 플레이북) — 실행기의 실제 정규화(`alarm_facts_from_payload`) 경로.
HOST_ALARMS: dict[str, str] = {
    "host.cpu": "CPU 사용률 임계 초과",
    "host.memory": "메모리 사용률 임계 초과",
    "host.fs": "Filesystem 사용률 임계 초과",
    "host.disk_io": "Disk I/O busy 임계 초과",
    "host.process": "프로세스 다운",
    "host.network": "Network 응답 없음",
}


@pytest.fixture(scope="module")
def loaded():
    return load_playbooks(DEFAULT_PLAYBOOK_DIR)


@pytest.fixture(scope="module")
def playbooks(loaded) -> dict[str, Playbook]:
    return {pb.id: pb for pb in loaded.playbooks}


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ── 로드 · 매칭 ─────────────────────────────────────────────────────────────


def test_real_directory_loads_without_rejection(loaded) -> None:
    assert loaded.error is None
    assert loaded.rejected == ()
    assert sorted(pb.id for pb in loaded.playbooks) == CATALOG_IDS


@pytest.mark.parametrize(
    ("type_id", "facts", "expected"), TYPE_TABLE, ids=[t[0] for t in TYPE_TABLE]
)
def test_type_table_with_real_yaml(type_id, facts, expected, playbooks) -> None:
    pb, matched_by = select_playbook(facts, list(playbooks.values()))
    assert pb is not None and pb.id == expected
    assert (matched_by == "fallback") == (expected == FALLBACK_ID)


@pytest.mark.parametrize(("kind", "expected"), sorted(SIGNAL_KINDS.items()))
def test_gateway_signal_kinds_select_was_type(kind, expected, playbooks) -> None:
    pb, matched_by = select_playbook(AlarmFacts(_APM, "apm", (kind,)), list(playbooks.values()))
    assert (pb.id, matched_by) == (expected, "match:resource_type+any_kind")


def _host_payload(alarm_name: str) -> dict:
    return {
        "contract_version": "1",
        "event": {
            "dbId": "polestar",
            "serverName": "wasdemo01",
            "hostname": "wasdemo01",
            "alarmId": "polestar:w6",
            "severity": 2,
            "alarmName": alarm_name,
            "resourceType": "server",
            "resourceName": "wasdemo01",
            "alarmTime": "20261019100000",
        },
        "decision": {"tier": "page"},
        "meta": {},
    }


@pytest.mark.parametrize(("expected", "alarm_name"), sorted(HOST_ALARMS.items()))
def test_host_alarm_runner_facts_select_host_type(expected, alarm_name, playbooks) -> None:
    facts = alarm_facts_from_payload(_host_payload(alarm_name))
    pb, _ = select_playbook(facts, list(playbooks.values()))
    assert pb.id == expected


def test_disk_alarm_without_fs_or_io_words_falls_back(playbooks) -> None:
    facts = alarm_facts_from_payload(_host_payload("Disk 경보"))
    assert facts.alarm_kind == "disk" and facts.kinds == ()
    assert select_playbook(facts, list(playbooks.values()))[0].id == FALLBACK_ID


def test_priorities_unique(playbooks) -> None:
    prios = [pb.match.priority for pb in playbooks.values() if not pb.is_fallback]
    assert len(prios) == len(set(prios))


def test_was_types_require_apm_resource_type(playbooks) -> None:
    for pb in playbooks.values():
        if pb.id == APM_FALLBACK_ID:  # APM 공통 점검 — 자원 유형만(plans/196 D2 ②)
            assert pb.match.resource_type == _APM and not pb.match.any_kind
        elif pb.id.startswith("was."):
            assert pb.match.resource_type == _APM and pb.match.any_kind
        elif pb.id.startswith("host."):
            assert pb.match.alarm_kind and pb.match.resource_type is None


def test_surface_terms_consistent_with_classify_alarm_kind(playbooks) -> None:
    """W12 표면어 — host.*는 `classify_alarm_kind`와 같은 kind, was.*는 OS kind에 걸리지 않는다."""
    seen: dict[str, str] = {}
    for pb in playbooks.values():
        if pb.is_fallback or pb.id == APM_FALLBACK_ID:  # 공통 점검은 증상 질의로 고르지 않는다
            assert pb.match.surface_terms == []
            continue
        assert pb.match.surface_terms, pb.id
        for term in pb.match.surface_terms:
            assert term not in seen, (term, pb.id, seen.get(term))
            seen[term] = pb.id
            kind = classify_alarm_kind(None, term)
            if pb.id.startswith("host."):
                assert kind == pb.match.alarm_kind, (pb.id, term, kind)
            else:
                assert kind is None, (pb.id, term, kind)


def test_steps_follow_catalog_order(playbooks) -> None:
    """§4.1 스텝 순서(보조 액션은 액션 ID로)."""
    expected = {
        "was.error": ["S0", "S1", "S1e", "S3", "S7", "S5", "S9"],
        "was.active": ["S0", "S1", "S1a", "S2", "S3", "S5", "S9"],
        "was.heap": ["S0", "S2", "S1", "S7", "S4", "S9"],
        "was.pool": ["S0", "S2", "S3", "S8", "S1", "S5", "S9"],
        "host.cpu": ["S4", "S6", "S0r", "S1", "S2", "S3a", "S3b", "S5", "S7", "S9"],
        "host.memory": ["S4", "S6", "S0r", "S2", "S5", "S9"],
        "host.fs": ["S4", "S5", "S7", "S9"],
        "host.disk_io": ["S4", "S6", "S5", "S9"],
        "host.process": [
            "S4r", "S4", "S6", "S5", "S5a", "S0r", "S1", "S2", "S3a", "S3b", "S7", "S9",
        ],
        "host.network": ["S4r", "S4t", "S5", "S9"],
    }
    for pid, order in expected.items():
        assert [s.id for s in playbooks[pid].steps] == order, pid
    actions = {s.id: s.action for s in playbooks["was.error"].steps}
    assert actions["S1e"] == "apm.events_errors"
    assert {s.id: s.action for s in playbooks["was.active"].steps}["S1a"] == "apm.active_services"
    net = {s.id: s.action for s in playbooks["host.network"].steps}
    assert (net["S4r"], net["S4t"]) == ("host.resource_status", "host.topology")
    for pb in playbooks.values():
        assert pb.steps[-1].action == "synthesize"
        assert all(
            s.action in STEP_ACTIONS and s.action in STEP_LIBRARY | {"synthesize": None}
            for s in pb.steps
        )


def test_s4_kinds_per_host_type(playbooks) -> None:
    kinds = {
        pid: next(s for s in playbooks[pid].steps if s.id == "S4").args["kinds"]
        for pid in ("host.cpu", "host.memory", "host.fs", "host.disk_io")
    }
    assert kinds == {
        "host.cpu": ["cpu"],
        "host.memory": ["memory"],
        "host.fs": ["filesystem"],
        "host.disk_io": ["disk_io"],
    }
    s6 = next(s for s in playbooks["host.memory"].steps if s.id == "S6")
    assert s6.args["sort"] == "mem"


# ── 런북 커버리지 · 대응 문구 경계 ──────────────────────────────────────────


def test_runbook_coverage_100pct(playbooks) -> None:
    for pb in playbooks.values():
        rb = pb.runbook
        assert rb.meaning and rb.impact and rb.owner and rb.last_reviewed, pb.id
        assert rb.mitigation, pb.id
        assert not rb.is_stale(date(2026, 10, 19)), pb.id
        if not pb.is_fallback:
            assert pb.response_hints, pb.id


#: 명령 리터럴 — `test_remediation.test_no_change_commands_in_catalog` · `test_boundary`
#: `test_no_remediation_execution_path`의 패턴 + 셸 일반형.
_COMMAND_PATTERNS = (
    r"systemctl\s+(restart|stop|start)",
    r"\bkill\s+-9\b",
    r"\brm\s+-rf\b",
    r"dmesg\s+-C",
    r"subprocess\.(run|Popen)",
    r"os\.system",
    r"systemctl restart",
    r"kill -",
    r"\bsudo\b",
    r"\brm\s+-",
    r"`",
)
_IP_LITERAL = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def _yaml_files() -> list[Path]:
    return sorted(DEFAULT_PLAYBOOK_DIR.glob("*.yaml"))


@pytest.mark.parametrize("path", _yaml_files(), ids=lambda p: p.name)
def test_yaml_has_no_command_tool_or_ip_literals(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for pattern in _COMMAND_PATTERNS:
        assert not re.search(pattern, text, re.I), (path.name, pattern)
    # 도구명은 스텝 라이브러리에만(§5.2)
    assert not re.search(r"\b(apm|polestar)_[a-z]", text), path.name
    assert not _IP_LITERAL.search(text), path.name


def test_mitigation_items_complete(playbooks) -> None:
    for pb in playbooks.values():
        for item in pb.runbook.mitigation:
            assert item.action and item.verification and item.rollback, pb.id
            assert item.risk in ("low", "medium", "high")


# ── 유형별 종단(녹화 재사용) ─────────────────────────────────────────────────


class RecordingCaller:
    """녹화 조회 배치 콜러블(README 조회 규칙 — 녹화 args ⊆ 호출 args인 첫 항목)."""

    def __init__(self, recordings: Mapping[str, list[dict]]) -> None:
        self.recordings = recordings
        self.calls: list[tuple[str, dict]] = []
        self.unrecorded: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, calls):
        out = []
        for tool, args in calls:
            resp = next(
                (
                    e["response"]
                    for e in self.recordings.get(tool, [])
                    if all(args.get(k) == v for k, v in e["args"].items())
                ),
                None,
            )
            with self._lock:
                self.calls.append((tool, dict(args)))
                if resp is None:
                    self.unrecorded.append((tool, dict(args)))
            out.append(json.dumps(resp if resp is not None else {"error": "미녹화"}))
        return out


def _recordings(base: str, extra: Mapping[str, list[dict]] | None = None) -> dict:
    rec = copy.deepcopy(_load(base)["tool_responses"])
    for tool, entries in (extra or {}).items():
        rec[tool] = list(entries) + rec.get(tool, [])  # 추가 녹화가 먼저 맞는다
    return rec


_QUERIED = "2026-10-19T10:12:00"
_WAS_EXTRA = {
    "apm_events": [
        {
            "args": {"record": "error"},
            "response": {
                "rows": [],
                "record": "error",
                "errors_by_type": [{"error_type": "SERVICE_EXCEPTION", "count": 12}],
                "source_kind": "apm_api",
            },
        }
    ],
    "apm_active_services": [
        {
            "args": {},
            "response": {
                "rows": [
                    {
                        "application": "/acct/inquiry",
                        "elapsed_ms": 42000,
                        "cpu_ms": 30,
                        "running_mode": "DB",
                        "running_text": "SELECT ...",
                    }
                ],
                "summary": {"total": 1},
                "queried_at": _QUERIED,
                "source_kind": "apm_api",
            },
        }
    ],
}
_HOST_EXTRA = {
    "polestar_resource_status": [
        {"args": {}, "response": {"rows": [{"name": "wasdemo01", "status": "down"}]}}
    ],
    "polestar_topology": [
        {"args": {}, "response": {"rows": [{"name": "wasdemo02", "status": "down"}]}}
    ],
    "polestar_alarm_history": [
        {"args": {}, "response": {"rows": [{"alarm_time": "2026-10-10T03:00:00"}]}}
    ],
    "polestar_process_snapshot": [
        {
            "args": {"sort": "mem"},
            "response": {
                "rows": [{"name": "java", "pid": 100, "pmem": 61.0}],
                "captured_at": _QUERIED,
                "source_kind": "polestar_process_realtime",
            },
        }
    ],
}

#: 유형 → (녹화 기준 픽스처, 페이로드, APM 축 사용 여부).
_WAS_TYPES = ("was.error", "was.active", "was.pool")
_HOST_TYPES = tuple(HOST_ALARMS)


def _run(pb: Playbook, payload: dict, caller: RecordingCaller, *, apm: bool, **kw):
    return run_playbook(
        payload,
        pb,
        matched_by="test",
        polestar=caller,
        apm=caller if apm else None,
        polestar_source="polestar",
        max_workers=1,
        for_each_max=1,
        now=lambda: datetime(2026, 10, 19, 10, 12),
        **kw,
    )


def _assert_sections(run, pb: Playbook) -> None:
    assert run.playbook_id == pb.id
    assert [s.step for s in run.sections] == [s.id for s in pb.steps]
    for s in run.sections:
        if s.status in ("missing", "skipped"):
            assert s.reason, (pb.id, s.step)  # 침묵 금지
    json.dumps(run.to_briefing_dict(), ensure_ascii=False)


@pytest.mark.parametrize("type_id", _WAS_TYPES)
def test_was_type_end_to_end(type_id: str, playbooks) -> None:
    pb = playbooks[type_id]
    caller = RecordingCaller(_recordings("latency_sql", _WAS_EXTRA))
    run = _run(pb, _load("latency_sql")["alarm"], caller, apm=True)
    assert caller.unrecorded == []
    _assert_sections(run, pb)
    status = {s.step: s.status for s in run.sections}
    for sid, st in status.items():
        if sid == "S9":
            assert st == "pending_synthesis"
        elif sid == "S8":
            assert st == "skipped"
        else:
            assert st == "ok", (type_id, sid, run.section(sid).reason)
    if type_id == "was.pool":
        s8 = run.section("S8")
        assert s8.reason == NOT_CONNECTED
        assert s8.data == {"adapter": "dpm", "connected": False}
    if type_id == "was.error":
        assert run.section("S1e").data["errors_by_type"][0]["error_type"] == "SERVICE_EXCEPTION"
    if type_id == "was.active":
        assert any(b.startswith("현재값") for b in run.section("S1a").badges)


@pytest.mark.parametrize("type_id", _HOST_TYPES)
def test_host_type_end_to_end(type_id: str, playbooks) -> None:
    """제니퍼 미구성 호스트 — 역방향 S0r은 생략(결손 아님) · WAS 스텝은 when_false로 skipped."""
    payload = _host_payload(HOST_ALARMS[type_id])
    pb, _ = select_playbook(alarm_facts_from_payload(payload), list(playbooks.values()))
    assert pb.id == type_id
    caller = RecordingCaller(_recordings("latency_sql", _HOST_EXTRA))
    run = _run(pb, payload, caller, apm=False)
    assert caller.unrecorded == []
    assert not any(t.startswith("apm_") for t, _ in caller.calls)
    _assert_sections(run, pb)
    for s in run.sections:
        if s.step == "S9":
            assert s.status == "pending_synthesis"
        elif s.step == "S0r":
            assert s.status == "skipped" and "생략" in s.reason
        elif s.action.startswith("apm."):
            assert s.status == "skipped" and s.reason.startswith("when_false"), s.step
        else:
            assert s.status == "ok", (type_id, s.step, s.reason)
    if "S7" in {s.step for s in run.sections}:
        assert run.section("S7").data["axes"]["itsm"] == {
            "status": "skipped",
            "reason": NOT_CONNECTED,
        }


def test_case4_runs_with_real_heap_yaml(playbooks) -> None:
    """case4(OOM 3시간 주기) — 실제 `was.heap.yaml`로 선택돼 S0→S2→S1→S7→S4→S9."""
    fx = _load("case4_oom_periodic")
    exp = fx["expected"]
    # OUTOFMEMORY 이벤트의 게이트웨이 시그니처 kind(`EVENT_TYPE_SIGNALS` — W0 실측).
    pb, matched_by = select_playbook(
        AlarmFacts(_APM, "apm", ("was_heap_pressure",)), list(playbooks.values())
    )
    assert (pb.id, matched_by) == (exp["playbook_id"], "match:resource_type+any_kind")
    caller = RecordingCaller(fx["tool_responses"])
    run = _run(pb, fx["alarm"], caller, apm=True)
    assert caller.unrecorded == []
    assert [s.step for s in run.sections] == exp["section_order"] == [
        "S0", "S2", "S1", "S7", "S4", "S9"
    ]  # fmt: skip
    for s in run.sections:
        if s.action == "synthesize":
            assert s.status == "pending_synthesis"
        else:
            assert s.status == exp["section_status"][s.step], (s.step, s.reason)
    assert {s.step: s.missing for s in run.sections if s.missing} == exp.get("missing", {})
    realtime = {s.step for s in run.sections if any(b.startswith("현재값") for b in s.badges)}
    assert realtime == set(exp["realtime_steps"])


def test_case4_runner_facts_select_heap(playbooks) -> None:
    facts = alarm_facts_from_payload(_load("case4_oom_periodic")["alarm"])
    assert select_playbook(facts, list(playbooks.values()))[0].id == "was.heap"


# ── 어댑터 슬롯 ─────────────────────────────────────────────────────────────


def test_not_connected_adapter_and_default_registry() -> None:
    for name in (sa.ITSM, sa.DPM, "unknown"):
        adapter = sa.DEFAULT_REGISTRY.get(name)
        assert isinstance(adapter, sa.SourceAdapter)
        assert adapter.name == name and adapter.available() is False
        res = adapter.fetch("S7", {}, {})
        assert res == {
            "status": "skipped",
            "reason": NOT_CONNECTED,
            "data": {"adapter": name, "connected": False},
        }
        assert sa.fetch_or_not_connected(adapter, "S7", {}, {}) == res
    assert sa.NOT_CONNECTED == NOT_CONNECTED


class _Connected:
    def __init__(self, name: str, result: dict | None = None, boom: bool = False) -> None:
        self.name = name
        self.result = result or {"status": "ok", "reason": None, "data": {"rows": [1]}}
        self.boom = boom
        self.seen: list[tuple[str, dict, dict]] = []

    def available(self) -> bool:
        return True

    def fetch(self, step_id: str, scope: Mapping[str, Any], window: Mapping[str, Any]) -> dict:
        self.seen.append((step_id, dict(scope), dict(window)))
        if self.boom:
            raise RuntimeError("연결 거부")
        return self.result


def _ctx(adapters: sa.AdapterRegistry | None = None) -> StepContext:
    payload = _host_payload("CPU 사용률 임계 초과")
    kw: dict[str, Any] = {} if adapters is None else {"adapters": adapters}
    return StepContext(
        payload=payload,
        windows=compute_windows("2026-10-19T10:00:00", WindowSpec()),
        scope=initial_scope(payload, None, "테스트 — 폴스타 미구성"),
        args={},
        targets=[],
        polestar=None,
        apm=None,
        now=lambda: datetime(2026, 10, 19, 10, 12),
        prior={},
        **kw,
    )


def test_s7_s8_default_not_connected() -> None:
    ctx = _ctx()
    s8 = STEP_LIBRARY["dpm.db_health"](ctx)
    assert (s8.status, s8.reason, s8.data) == (
        "skipped",
        NOT_CONNECTED,
        {"adapter": "dpm", "connected": False},
    )
    s7 = STEP_LIBRARY["change.recent"](_ctx())
    assert s7.data["axes"]["itsm"] == {"status": "skipped", "reason": NOT_CONNECTED}


def test_s7_s8_use_injected_adapters() -> None:
    dpm = _Connected(sa.DPM, {"status": "ok", "reason": None, "data": {"blockers": 0}})
    itsm = _Connected(sa.ITSM)
    reg = sa.AdapterRegistry({sa.DPM: dpm, sa.ITSM: itsm})
    s8 = STEP_LIBRARY["dpm.db_health"](_ctx(reg))
    assert (s8.status, s8.data) == ("ok", {"blockers": 0})
    assert dpm.seen[0][0] == "S8" and dpm.seen[0][2]["alarm_time"] == "2026-10-19T10:00:00"
    s7 = STEP_LIBRARY["change.recent"](_ctx(reg))
    assert s7.data["axes"]["itsm"] == {"status": "ok", "reason": None}
    assert itsm.seen[0][0] == "S7" and itsm.seen[0][1]["polestar_server"] == "wasdemo01"


def test_connected_adapter_failure_is_missing_not_silent() -> None:
    reg = sa.AdapterRegistry({sa.DPM: _Connected(sa.DPM, boom=True)})
    s8 = STEP_LIBRARY["dpm.db_health"](_ctx(reg))
    assert s8.status == "missing" and "연결 거부" in s8.reason
    assert sa.AdapterRegistry({}).get(sa.ITSM).available() is False


# ── S1 임계 인자 ────────────────────────────────────────────────────────────


def test_latency_yaml_declares_s1_ratio(playbooks) -> None:
    s1 = next(s for s in playbooks["was.latency"].steps if s.id == "S1")
    assert s1.args["p95_ratio"] == DEFAULT_P95_RATIO == 2.0


def test_target_verdict_ratio_boundary() -> None:
    assert target_verdict([], 200.0, 100.0, 2.0) == ("degraded", VERDICT_SOURCE_RULE)
    assert target_verdict([], 199.9, 100.0, 2.0) == ("normal", VERDICT_SOURCE_RULE)
    assert target_verdict([], 150.0, 100.0, 1.5) == ("degraded", VERDICT_SOURCE_RULE)
    assert target_verdict([], 150.0, 100.0, 3.0) == ("normal", VERDICT_SOURCE_RULE)


def _rule_fixture() -> tuple[dict, str]:
    """사건창 app_health에 was_signals가 없는(규칙 판정) 픽스처와 그 인스턴스."""
    for path in sorted(FIXTURES.glob("*.json")):
        fx = json.loads(path.read_text(encoding="utf-8"))
        ref = fx.get("tool_reference_time")
        for e in fx["tool_responses"].get("apm_app_health", []):
            a = e["args"]
            if a.get("reference_time") == ref and a.get("instance_name") == "A1":
                if not (e["response"].get("was_signals") or []):
                    return fx, "A1"
    pytest.skip("규칙 판정 픽스처 없음")


@pytest.mark.parametrize(("ratio", "expected"), [(0.01, "degraded"), (1000.0, "normal")])
def test_s1_ratio_arg_overrides_rule(ratio: float, expected: str) -> None:
    fx, instance = _rule_fixture()
    alarm_time = fx["alarm_time"]
    payload = fx["alarm"]
    caller = RecordingCaller(fx["tool_responses"])
    ctx = StepContext(
        payload=payload,
        windows=compute_windows(alarm_time, WindowSpec()),
        scope=initial_scope(payload, "polestar", None),
        args={"p95_ratio": ratio},
        targets=[instance],
        polestar=caller,
        apm=caller,
        now=lambda: datetime(2026, 10, 19, 10, 12),
        prior={},
    )
    res = golden_signals(ctx)
    assert res.data["p95_ratio"] == ratio
    assert (res.verdict, res.verdict_source) == (expected, VERDICT_SOURCE_RULE)
