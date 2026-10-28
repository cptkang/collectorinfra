"""plans/198 W6 · I-7 — 토폴로지 1홉 이웃 · 외부 원인 후보 (결정적 · 가짜 MCP만 · D-350 ④).

고정: 연결 출처 필터(추정·선택 대기 연결 제외) · 이웃 상한 · 선행 판정으로 외부 원인 후보
생성/미생성 · 이웃 알람 배치 1회 상한 · 지침 블록 on/off ·
플래그 off 비트 동일(배치·결과·브리핑·지침).
"""

import json
from types import SimpleNamespace

from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.evidence_prefetch import (
    TOOL_ALARMS,
    TOOL_METRIC,
    TOOL_TOPOLOGY,
    EvidenceScope,
    build_calls,
    prefetch_and_correlate,
    scope_from_job,
)
from sre_agent.application.investigation_guidance import (
    NEIGHBOR_SCOPE_RULE,
    build_guidance,
    neighbor_note,
)
from sre_agent.domain.correlation import AlarmPoint
from sre_agent.domain.severity_signatures import ImportanceVerdict
from sre_agent.domain.topology_neighbors import (
    ORIGIN_LINK,
    ORIGIN_TOPOLOGY,
    Neighbor,
    link_neighbor,
    neighbor_finding,
    select_neighbors,
    topology_neighbors,
)
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings

REF = "2026-10-12T10:15:00"
TARGET = "was01"


def _settings(**kw) -> AgentSettings:
    kw.setdefault("_env_file", None)
    kw.setdefault("model", "test/model")
    kw.setdefault("gemini_api_key", None)
    return AgentSettings(**kw)


def _alarms(*rows: tuple[str, str]) -> str:
    return json.dumps(
        {
            "rows": [
                {"alarm_time": t, "severity": 2, "alarm_name": name, "resource_name": ""}
                for t, name in rows
            ],
            "row_count": len(rows),
        }
    )


def _empty_metric() -> str:
    return json.dumps({"rows": [], "window": {}})


def _topology(*names: str, hop: int = 1) -> str:
    return json.dumps(
        {
            "rows": [
                {
                    "resource_id": i,
                    "resource_name": n,
                    "avail_status": 1,
                    "hop": hop,
                    "direction": "ancestor",
                }
                for i, n in enumerate(names)
            ],
            "row_count": len(names),
        }
    )


class _FakeMcp:
    """가짜 폴스타 MCP 배치 — 서버별 알람 응답 · 토폴로지 응답 · 배치 호출 기록."""

    def __init__(self, alarms: dict[str, str], topology: str | None = None) -> None:
        self.alarms = alarms
        self.topology = topology
        self.batches: list[list[tuple[str, dict]]] = []

    def __call__(self, calls):
        self.batches.append(list(calls))
        out = []
        for name, args in calls:
            if name == TOOL_ALARMS:
                out.append(self.alarms.get(args["server_name"], _alarms()))
            elif name == TOOL_TOPOLOGY:
                out.append(self.topology or _topology())
            else:
                out.append(_empty_metric())
        return out

    @property
    def calls(self) -> list[tuple[str, dict]]:
        return [c for b in self.batches for c in b]


def _scope(**kw) -> EvidenceScope:
    base = {
        "source": "polestar",
        "server_name": TARGET,
        "reference_time": REF,
        "lookback_minutes": 60,
    }
    return EvidenceScope(**{**base, **kw})


def _link(**kw) -> dict:
    return {
        "status": "linked",
        "db_id": "polestar_cm_gp",
        "server_name": "host-a",
        "basis": "previous_confirmed",
        "by": "op",
        "at": "2026-10-01",
        **kw,
    }


def _apm_job(link: dict | None, *, episode_db: str | None = None) -> SimpleNamespace:
    meta: dict = {"hints": {"solution": "apm"}}
    if link is not None:
        meta["hints"]["polestar_link"] = link
    if episode_db:
        meta["episode"] = {"infra_db_ids": [episode_db]}
    payload = {
        "event": {"dbId": "jennifer", "serverName": TARGET, "resourceType": "apm.Instance"},
        "meta": meta,
    }
    return SimpleNamespace(reference_time=REF, lookback_minutes=60, payload=payload)


def _polestar_job() -> SimpleNamespace:
    return SimpleNamespace(
        reference_time=REF,
        lookback_minutes=60,
        payload={"event": {"dbId": "polestar", "serverName": TARGET}},
    )


# ── 연결 출처 필터 ──────────────────────────────────────────────────────


def test_link_neighbor_accepts_only_previous_confirmed():
    assert link_neighbor(_link()) == Neighbor("host-a", "polestar_cm_gp", ORIGIN_LINK)
    assert link_neighbor(_link(basis="user_selected")) is None  # 확정 대기 — 미확정
    assert link_neighbor(_link(basis="")) is None
    assert link_neighbor({"status": "unresolved"}) is None  # 선택 대기
    assert link_neighbor({"status": "absent", "by": "op"}) is None  # 미등록
    assert link_neighbor(_link(server_name="")) is None
    assert link_neighbor(None) is None


def test_topology_neighbors_one_hop_only():
    rows = [
        {"resource_name": "db01", "hop": 1},
        {"resource_name": "far", "hop": 2},
        {"name": "peer", "relation": "peer"},  # hop 없음(DB2 폴백형) — 1홉으로 본다
        {"resource_name": "", "hop": 1},
        {"resource_name": "bad", "hop": "x"},
    ]
    assert [n.name for n in topology_neighbors(rows, "polestar")] == ["db01", "peer"]
    assert all(n.origin == ORIGIN_TOPOLOGY for n in topology_neighbors(rows, "polestar"))


def test_scope_from_job_ignores_unconfirmed_link_and_flag_off():
    assert scope_from_job(_apm_job(_link()), cross_source=True).link_neighbor is None  # off
    on = scope_from_job(_apm_job(_link()), neighbors_max=5)
    assert on.link_neighbor == Neighbor("host-a", "polestar_cm_gp", ORIGIN_LINK)
    for link in (_link(basis="user_selected"), {"status": "unresolved"}, {"status": "absent"}):
        assert scope_from_job(_apm_job(link), neighbors_max=5).link_neighbor is None
    # 폴스타 알람에는 연결 힌트가 없다(APM 전용)
    assert scope_from_job(_polestar_job(), neighbors_max=5).link_neighbor is None


# ── 선택 · 상한 ─────────────────────────────────────────────────────────


def test_select_neighbors_dedup_excludes_target_and_caps():
    cands = [Neighbor(f"n{i}", "polestar", ORIGIN_TOPOLOGY) for i in range(7)]
    cands += [
        Neighbor("WAS01", "polestar", ORIGIN_TOPOLOGY),
        Neighbor("n1", "polestar", ORIGIN_TOPOLOGY),
        Neighbor("host-a", "gp", ORIGIN_LINK),
    ]
    picked, dropped = select_neighbors(TARGET, cands, 5)
    assert [n.name for n in picked] == ["host-a", "n0", "n1", "n2", "n3"]  # 확정 연결 먼저
    assert dropped == 3


def test_neighbor_cap_limits_alarm_calls():
    mcp = _FakeMcp({}, topology=_topology(*(f"n{i}" for i in range(8))))
    r = prefetch_and_correlate(_scope(neighbors_max=3), mcp)
    assert len(mcp.batches) == 2  # 첫 배치 + 이웃 배치 1회
    assert [a["server_name"] for _, a in mcp.batches[1]] == ["n0", "n1", "n2"]
    nb = r.to_dict()["neighbors"]
    assert nb["dropped"] == 5 and nb["limit"] == 3 and nb["tool_calls"] == 1 + 3
    assert any("상한 3대 밖" in n for n in r.notes)


# ── 선행 판정 · 외부 원인 후보 ──────────────────────────────────────────


def test_leading_neighbor_becomes_external_cause_candidate():
    mcp = _FakeMcp(
        {
            TARGET: _alarms(("2026-10-12T10:15:00", "응답시간 임계 초과")),
            "db01": _alarms(("2026-10-12T09:52:00", "DB 세션 락 대기 증가")),
            "was02": _alarms(("2026-10-12T10:13:00", "응답시간 임계 초과")),
            "web01": _alarms(("2026-10-12T10:20:00", "HTTP 5xx")),
        },
        topology=_topology("was02", "db01", "web01", "quiet"),
    )
    d = prefetch_and_correlate(_scope(neighbors_max=5), mcp).to_dict()
    nb = d["neighbors"]
    by = {h["name"]: h for h in nb["hosts"]}
    assert by["db01"]["leads"] and by["was02"]["leads"]
    assert by["web01"]["leads"] is False and by["quiet"]["alarm_count"] == 0
    cands = nb["external_cause_candidates"]
    assert [(c["rank"], c["host"], c["lead_min"]) for c in cands] == [
        (1, "db01", 23),
        (2, "was02", 2),
    ]
    assert "상관 ≠ 인과" in cands[0]["basis"]
    # 이웃 알람은 연관 서버 알람으로 타임라인·선행 노트에 들어간다(correlate 규칙 재사용)
    assert any("[db01]" in t["detail"] for t in d["timeline"])
    assert any("연관 서버 db01" in n for n in d["notes"])
    assert d["leading_signal"] is None and d["alarm_summary"]["count"] == 1  # 대표 요약 불변


def test_no_candidate_when_neighbors_follow():
    mcp = _FakeMcp(
        {
            TARGET: _alarms(("2026-10-12T10:00:00", "CPU 임계")),
            "db01": _alarms(("2026-10-12T10:05:00", "락 대기")),
        },
        topology=_topology("db01"),
    )
    nb = prefetch_and_correlate(_scope(neighbors_max=5), mcp).to_dict()["neighbors"]
    assert nb["hosts"][0]["leads"] is False and nb["external_cause_candidates"] == []


def test_neighbor_alarm_error_is_not_zero():
    mcp = _FakeMcp({"db01": json.dumps({"error": "timeout"})}, topology=_topology("db01"))
    r = prefetch_and_correlate(_scope(neighbors_max=5), mcp)
    h = r.to_dict()["neighbors"]["hosts"][0]
    assert h["leads"] is None and h["alarm_count"] is None and h["error"] == "timeout"
    assert any("이웃 db01 알람 결손" in n for n in r.notes)


def test_neighbor_finding_ties_sorted_by_name():
    pts = {n: [AlarmPoint("2026-10-12T10:00:00", 2, "x")] for n in ("b", "a")}
    nbs = [Neighbor("b", "s", ORIGIN_TOPOLOGY), Neighbor("a", "s", ORIGIN_TOPOLOGY)]
    f = neighbor_finding(REF, 0, nbs, pts, limit=5)
    assert [c["host"] for c in f["external_cause_candidates"]] == ["a", "b"]


# ── 1회 조회 상한 · 재조회 없음 ────────────────────────────────────────


def test_single_neighbor_batch_and_topology_one_hop():
    mcp = _FakeMcp({}, topology=_topology("db01", "was02"))
    prefetch_and_correlate(_scope(neighbors_max=5), mcp)
    topo = [c for c in mcp.calls if c[0] == TOOL_TOPOLOGY]
    assert topo == [(TOOL_TOPOLOGY, {"source": "polestar", "server_name": TARGET, "max_hops": 1})]
    assert mcp.batches[0][-1][0] == TOOL_TOPOLOGY  # 첫 배치 맨 끝
    assert len(mcp.batches) == 2 and all(c[0] == TOOL_ALARMS for c in mcp.batches[1])


def test_related_server_alarms_not_refetched():
    mcp = _FakeMcp(
        {"db01": _alarms(("2026-10-12T09:52:00", "락 대기"))}, topology=_topology("db01")
    )
    r = prefetch_and_correlate(_scope(neighbors_max=5, related_servers=("db01",)), mcp)
    assert len(mcp.batches) == 1  # 이웃 배치 없음
    nb = r.to_dict()["neighbors"]
    assert nb["tool_calls"] == 1 and nb["external_cause_candidates"][0]["host"] == "db01"


def test_no_neighbors_no_second_batch():
    mcp = _FakeMcp({}, topology=_topology())
    nb = prefetch_and_correlate(_scope(neighbors_max=5), mcp).to_dict()["neighbors"]
    assert len(mcp.batches) == 1 and nb["hosts"] == [] and nb["tool_calls"] == 1


def test_topology_error_noted():
    mcp = _FakeMcp({}, topology=json.dumps({"error": "알 수 없는 소스"}))
    r = prefetch_and_correlate(_scope(neighbors_max=5), mcp)
    assert len(mcp.batches) == 1 and any("토폴로지 이웃 결손" in n for n in r.notes)


def test_skip_infra_uses_confirmed_link_source_only():
    scope = scope_from_job(_apm_job(_link()), neighbors_max=5)
    assert scope.skip_infra
    mcp = _FakeMcp({"host-a": _alarms(("2026-10-12T10:10:00", "CPU 임계"))})
    d = prefetch_and_correlate(scope, mcp).to_dict()
    assert mcp.calls == [
        (
            TOOL_ALARMS,
            {
                "source": "polestar_cm_gp",
                "server_name": "host-a",
                "reference_time": REF,
                "lookback_minutes": 60,
            },
        )
    ]
    cand = d["neighbors"]["external_cause_candidates"][0]
    assert cand["host"] == "host-a" and cand["origin"] == ORIGIN_LINK and cand["lead_min"] == 5


def test_session_failure_skips_neighbor_batch():
    def _fail(calls):
        return [json.dumps({"error": "MCP 세션 실패: down"})] * len(calls)

    calls_seen: list = []

    def _rec(calls):
        calls_seen.append(calls)
        return _fail(calls)

    d = prefetch_and_correlate(_scope(neighbors_max=5), _rec).to_dict()
    assert len(calls_seen) == 1 and d["neighbors"]["hosts"] == []


# ── 플래그 off 비트 동일 ───────────────────────────────────────────────


def test_flag_off_bit_identical_batch_and_result():
    mcp = _FakeMcp({TARGET: _alarms(("2026-10-12T10:15:00", "x"))})
    off = _scope()
    calls = build_calls(off)
    assert [c[0] for c in calls] == [TOOL_ALARMS] + [TOOL_METRIC] * 4
    d = prefetch_and_correlate(off, mcp).to_dict()
    assert "neighbors" not in d and len(mcp.batches) == 1
    assert scope_from_job(_polestar_job()) == scope_from_job(_polestar_job(), neighbors_max=0)


def test_default_settings_off_and_prefetch_wiring(monkeypatch):
    s = _settings()
    assert s.evidence_topology_neighbors_enabled is False and s.evidence_topology_neighbors_max == 5
    seen: dict = {}

    def _scope_spy(job, **kw):
        seen.update(kw)
        return None

    monkeypatch.setattr(mcp_service, "scope_from_job", _scope_spy)
    monkeypatch.setattr(mcp_service, "make_batch_caller", lambda settings: object())
    mcp_service._default_prefetch_fn(_settings(evidence_correlation_enabled=True))(_polestar_job())
    assert seen["neighbors_max"] == 0
    mcp_service._default_prefetch_fn(
        _settings(
            evidence_correlation_enabled=True,
            evidence_topology_neighbors_enabled=True,
            evidence_topology_neighbors_max=3,
        )
    )(_polestar_job())
    assert seen["neighbors_max"] == 3


# ── 브리핑 가산 · 지침 블록 ─────────────────────────────────────────────


def _corr_with_neighbors() -> dict:
    mcp = _FakeMcp(
        {
            TARGET: _alarms(("2026-10-12T10:15:00", "응답시간")),
            "db01": _alarms(("2026-10-12T09:52:00", "락 대기")),
        },
        topology=_topology("db01", "quiet"),
    )
    return prefetch_and_correlate(_scope(neighbors_max=5), mcp).to_dict()


def _verdict() -> ImportanceVerdict:
    return ImportanceVerdict(level="경고", confidence="none", escalate=False)


def test_briefing_adds_keys_only_and_keeps_hypotheses():
    corr = _corr_with_neighbors()
    plain = {k: v for k, v in corr.items() if k != "neighbors"}
    with_nb = build_briefing(answer="원인 미상", verdict=_verdict(), correlation=corr)
    without = build_briefing(answer="원인 미상", verdict=_verdict(), correlation=plain)
    assert set(with_nb) - set(without) == {"neighbors", "external_cause_candidates"}
    assert with_nb["root_cause_hypotheses"] == without["root_cause_hypotheses"]
    assert with_nb["external_cause_candidates"][0]["host"] == "db01"
    assert (
        with_nb["neighbors"]["tool_calls"] == 3
        and "external_cause_candidates" not in with_nb["neighbors"]
    )


def _gjob(corr: dict) -> SimpleNamespace:
    return SimpleNamespace(
        reference_time=REF,
        lookback_minutes=60,
        correlation=corr,
        payload={"event": {"dbId": "polestar", "serverName": TARGET}},
        kind="alarm",
    )


def test_guidance_block_on_off():
    corr = _corr_with_neighbors()
    on = build_guidance(_settings(evidence_topology_neighbors_enabled=True), _gjob(corr))
    off = build_guidance(_settings(), _gjob(corr))
    note = neighbor_note(corr)
    assert note and note in on and note not in off
    assert NEIGHBOR_SCOPE_RULE in on and NEIGHBOR_SCOPE_RULE not in off
    assert "db01 (폴스타 토폴로지) · 첫 알람 T-23m · 대상보다 선행" in note
    assert "quiet (폴스타 토폴로지) · 구간 알람 0건" in note
    assert "외부 원인 후보 1:" in note
    # off: 이웃 키가 있어도 없어도 지침 문자열이 같다
    plain = {k: v for k, v in corr.items() if k != "neighbors"}
    assert off == build_guidance(_settings(), _gjob(plain))


def test_guidance_no_block_without_neighbors():
    corr = _corr_with_neighbors()
    corr["neighbors"] = {**corr["neighbors"], "hosts": []}
    assert neighbor_note(corr) is None and neighbor_note(None) is None
    on = build_guidance(_settings(evidence_topology_neighbors_enabled=True), _gjob(corr))
    assert NEIGHBOR_SCOPE_RULE not in on
