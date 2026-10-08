"""plans/144 W4 — app_impact 정밀화(사건 저장소 우선 · 사후 승격) · 제니퍼 정상 강등 shadow ·
조사 트리거 사건당 1회.

    A. 도메인 순수 함수 — 창 · 앱 영향 모양 · 사후 승격 대상
    B. 게이트 노드 — 사건 저장소 판정 = 게이트웨이 판정 · 사건에 없으면 게이트웨이 · 현행 비트 동일
    C. 워커 — 사건 저장소 앱 영향(annotate·enforce만)
    D. 사후 승격(G-6) — 1회 · 창·해소·PAGE·사건 종료·플래그 off
    E. 사후 승격 통보 경로(notifier)
    F. 정상 강등 shadow(G-5 (a)) — 표지만 · 판정 불변
    G. 조사 트리거 사건당 1회(§4.5)
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.investigation_trigger import investigation_trigger_node
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.application.server_identity import is_apm_source
from noise_gate.domain.alarm import AlarmAnalysisResult
from noise_gate.domain.cross_source import (
    SOURCE_JENNIFER,
    SOURCE_POLESTAR,
    Episode,
    EpisodeAlarm,
    EpisodeMember,
    apm_alarms_in_window,
    app_impact_from_alarms,
    late_promotion_targets,
)
from noise_gate.domain.notification_policy import (
    STAGE_MATRIX,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    NotificationDecision,
    decide_notification,
)
from noise_gate.infrastructure.apm_gateway_client import ApmGatewayClientError
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests.test_plan87_apm_consumer import (
    _ctx,
    _FakeApmClient,
    _fatal_rows,
    _gate_config,
    _gate_state,
    _SpySreClient,
    _SpyStore,
    _trigger_cfg,
    apm_event,
    polestar_event,
)
from noise_gate.tests.test_plan144_cross_source import (
    SHIPPED_RULES,
    _Clock,
    _jennifer_rec,
    _polestar_rec,
    _Redis,
)

# ═════════════════════════════════════════════════════════════════════════════
# 공통
# ═════════════════════════════════════════════════════════════════════════════

T0 = 1_791_330_000.0


def _ea(alarm_id: str, *, source: str = SOURCE_JENNIFER, at: float = T0, level: str = "fatal",
        name: str = "ERROR_T0", severity: int = 3, was=("was_heap_pressure",)) -> EpisodeAlarm:
    return EpisodeAlarm(
        alarm_id=alarm_id, source=source, zone="gongjon", host_key="xsapp01",
        host_key_strength="strong", db_id="jennifer_common" if source == SOURCE_JENNIFER else "pg",
        occurred_at=at, severity=severity, alarm_name=name,
        was_kinds=tuple(was) if source == SOURCE_JENNIFER else (),
        level=level if source == SOURCE_JENNIFER else "",
    )


def _episode(*members: EpisodeMember) -> Episode:
    return Episode(id="ep-1", zone="gongjon", host_key="xsapp01", opened_at=T0, last_seen=T0,
                   members=list(members))


def _jrec(alarm_id: str, *, at: str, level: str = "fatal", name: str = "ERROR_OUTOFMEMORY",
          severity: int = 3, host: str = "xsapp01") -> dict:
    rec = _jennifer_rec(alarm_id, severity=severity, at=at, host=host)
    rec["alarmName"] = name
    rec["apm"] = {**rec["apm"], "level": level, "event_type": name, "event_type_norm": name}
    return rec


_POLICY_CFG = SimpleNamespace(
    suppress_max_severity=2, importance_value_map={"2": "보통", "3": "높음"},
    resolved_to_dashboard=False,
)
_APM_CTX = {"importance_id": "3", "maintenance": None, "noti_policy": "notify",
            "source": "apm_policy"}


def _pctx(importance: str = "2") -> dict:
    return {"importance_id": importance, "maintenance": False, "noti_policy": None,
            "source": "fixture"}


class _LateGraph:
    """정책 순수 함수로 판정하고 폴스타 알람에는 분석 결과를 붙여 돌려주는 가짜 그래프."""

    def __init__(self) -> None:
        self.states: list[dict] = []
        self.decisions: list[NotificationDecision] = []
        self.importance: dict[str, str] = {}  # alarm_id → 폴스타 중요도 코드

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        ev = state["alarm_event"]
        ctx = _APM_CTX if is_apm_source(ev) else _pctx(self.importance.get(ev.alarm_id, "2"))
        decision = decide_notification(
            ev, None, None, ctx, _POLICY_CFG,
            self_heal=state["self_heal"], cross_source=state.get("cross_source"),
        )
        self.decisions.append(decision)
        analysis = AlarmAnalysisResult(
            alarm_event=ev, severity_label="경고", summary="s", probable_cause="c",
            recommended_action="a", notification_channels=["workb"],
        )
        return {**state, "notification_decision": decision, "analysis_result": analysis}


class _LateStore:
    def __init__(self) -> None:
        self.late: list[dict] = []

    def record_late_promotion(self, **kw):
        self.late.append(kw)

    def record_resolution(self, **kw):
        pass

    def record_recurrence(self, **kw):
        pass


def _w4_worker(mode: str = "shadow", *, late: bool = True, impact: bool = True):
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True, repeat_interval_seconds=14400, suppress_max_severity=2,
            self_heal_window_seconds=300, cross_source_mode=mode,
            cross_source_rules_path=str(SHIPPED_RULES), episode_idle_seconds=900,
            app_impact_enabled=impact, app_impact_window_minutes=10,
            app_impact_late_promotion_enabled=late,
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )
    w = AlarmWorker(cfg)
    g = _LateGraph()
    w._graph = g
    w._episodes = w._build_episode_tracker()
    w._decision_store = _LateStore()
    return w, g


async def _feed(w: AlarmWorker, rec: dict, n: int = 1) -> None:
    fields = {b"data": json.dumps(rec, ensure_ascii=False).encode("utf-8")}
    await w._process(_Redis(), "alarm:raw", "g", f"{n}-1".encode(), fields, {})


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    """워커 도착 시계(time.time)를 고정한다 — W3 테스트와 같은 시계."""
    c = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


@pytest.fixture
def sent(monkeypatch) -> list[tuple]:
    """워커가 부르는 사후 승격 통보를 가로챈다(실 발송 없음)."""
    calls: list[tuple] = []

    async def _spy(result, decision, promotion, cfg, incident_publisher=None):
        calls.append((result, decision, promotion))
        return {"incident": False, "workb": True}

    monkeypatch.setattr(worker_mod, "send_late_promotion", _spy)
    return calls


# ═════════════════════════════════════════════════════════════════════════════
# A. 도메인
# ═════════════════════════════════════════════════════════════════════════════


class TestDomain:
    def test_window_is_past_only_and_apm_only(self):
        ep = _episode(
            EpisodeMember(_ea("J-old", at=T0 - 601), 0),
            EpisodeMember(_ea("J-in", at=T0 - 600), 0),
            EpisodeMember(_ea("J-future", at=T0 + 1), 0),
            EpisodeMember(_ea("P-1", source=SOURCE_POLESTAR, at=T0), 0),
        )
        assert [a.alarm_id for a in apm_alarms_in_window(ep, T0, 600)] == ["J-in"]

    def test_app_impact_shape_counts_fatal_and_critical_only(self):
        got = app_impact_from_alarms([
            _ea("J-1", name="ERROR_B"), _ea("J-2", level="critical", name="ERROR_A"),
            _ea("J-3", level="warning", name="WARNING_X", was=("was_error_burst",)),
        ])
        assert got == {
            "source": "episode", "fatal_events": 2, "event_types": ["ERROR_A", "ERROR_B"],
            "was_signals": ["was_error_burst", "was_heap_pressure"],
        }
        assert app_impact_from_alarms([_ea("J-1", level="warning")]) is None
        assert app_impact_from_alarms([]) is None

    @pytest.mark.parametrize(
        "member,expected",
        [
            (EpisodeMember(_ea("P", source=SOURCE_POLESTAR, severity=2), 0, tier="ticket"), True),
            (EpisodeMember(_ea("P", source=SOURCE_POLESTAR, severity=1), 0, tier="dashboard"),
             True),
            (EpisodeMember(_ea("P", source=SOURCE_POLESTAR, severity=2), 0, tier="page"), False),
            (EpisodeMember(_ea("P", source=SOURCE_POLESTAR, severity=2), 0, tier="suppress"),
             False),
            (EpisodeMember(_ea("P", source=SOURCE_POLESTAR, severity=3), 0, tier="ticket"), False),
            (EpisodeMember(_ea("P", source=SOURCE_POLESTAR, severity=2), 0, tier="ticket",
                           resolved=True), False),
            (EpisodeMember(_ea("P", source=SOURCE_POLESTAR, severity=2), 0, tier="ticket",
                           late_promoted=True), False),
            (EpisodeMember(_ea("P", source=SOURCE_POLESTAR, severity=2, at=T0 - 601), 0,
                           tier="ticket"), False),
            (EpisodeMember(_ea("J-x", severity=2), 0, tier="ticket"), False),  # APM 멤버
        ],
    )
    def test_late_promotion_targets(self, member, expected):
        ep = _episode(member)
        assert bool(late_promotion_targets(ep, _ea("J-1"), 600)) is expected

    def test_non_fatal_trigger_has_no_targets(self):
        ep = _episode(EpisodeMember(_ea("P", source=SOURCE_POLESTAR, severity=2), 0, tier="ticket"))
        assert late_promotion_targets(ep, _ea("J-1", level="warning"), 600) == []

    def test_to_dict_carries_w4_fields(self):
        ep = _episode(EpisodeMember(_ea("P", source=SOURCE_POLESTAR), 0, late_promoted=True))
        ep.apm_investigation_id = "inv-9"
        d = ep.to_dict()
        assert d["apm_investigation_id"] == "inv-9" and d["members"][0]["late_promoted"] is True


# ═════════════════════════════════════════════════════════════════════════════
# B. 게이트 노드 — 사건 저장소 우선
# ═════════════════════════════════════════════════════════════════════════════


def _episode_impact(n: int = 2) -> dict:
    alarms = [
        _ea(f"J-{i}", name=f"ERROR_T{i}", was=("was_error_burst", "was_heap_pressure"))
        for i in range(n)
    ]
    return {"app_impact": app_impact_from_alarms(alarms), "window_events": n,
            "window_minutes": 10}


class TestGateEpisodeStore:
    async def test_episode_decision_equals_gateway_decision(self):
        ev = polestar_event(severity=1)
        mcp_client, mcp_store = _FakeApmClient(_fatal_rows(2)), _SpyStore()
        via_mcp = await notification_gate_node(_gate_state(ev), _gate_config(mcp_client, mcp_store))
        ep_client, ep_store = _FakeApmClient(_fatal_rows(2)), _SpyStore()
        state = {**_gate_state(ev), "episode_app_impact": _episode_impact(2)}
        via_ep = await notification_gate_node(state, _gate_config(ep_client, ep_store))

        assert ep_client.calls == [] and len(mcp_client.calls) == 1  # 사건 저장소면 호출 없음
        a, b = via_mcp["notification_decision"], via_ep["notification_decision"]
        assert (a.tier, a.reason, a.priority, a.stage) == (b.tier, b.reason, b.priority, b.stage)
        assert a.tier == TIER_PAGE
        ea, eb = mcp_store.records[0]["stage_evidence"], ep_store.records[0]["stage_evidence"]
        for key in ("app_impact_fatal_events", "app_impact_event_types",
                    "app_impact_was_signals"):
            assert ea[key] == eb[key]
        assert ea["app_impact_source"] == "jennifer" and eb["app_impact_source"] == "episode"
        assert via_ep["noise_context"]["app_impact"]["source"] == "episode"

    async def test_episode_without_fatal_falls_back_to_gateway(self):
        client = _FakeApmClient(_fatal_rows(1))
        impact = {"app_impact": None, "window_events": 1, "window_minutes": 10}
        out = await notification_gate_node(
            {**_gate_state(polestar_event(severity=1)), "episode_app_impact": impact},
            _gate_config(client),
        )
        assert len(client.calls) == 1 and client.calls[0]["level"] == "fatal"
        assert out["notification_decision"].tier == TIER_PAGE

    async def test_no_episode_key_is_current_call(self):
        """shadow·off(키 없음)는 현행 게이트웨이 호출 그대로 — 인자까지 같다."""
        client = _FakeApmClient(_fatal_rows(2))
        await notification_gate_node(_gate_state(polestar_event(severity=1)), _gate_config(client))
        assert client.calls == [{
            "hostname": "was-app-01", "reference_time": "2026-09-29T10:15:00",
            "lookback_minutes": 10, "level": "fatal", "investigation_id": "P-1",
            "source_ids": ["common"],
        }]

    async def test_episode_value_never_promotes_page_or_suppress(self):
        client = _FakeApmClient(_fatal_rows(1))
        for ctx, tier in ((_ctx("3"), TIER_PAGE), (_ctx("2", maintenance=True), TIER_SUPPRESS)):
            out = await notification_gate_node(
                {**_gate_state(polestar_event(severity=2), ctx),
                 "episode_app_impact": _episode_impact(1)},
                _gate_config(client),
            )
            assert out["notification_decision"].tier == tier and "noise_context" not in out
        assert client.calls == []


# ═════════════════════════════════════════════════════════════════════════════
# C. 워커 — 사건 저장소 앱 영향
# ═════════════════════════════════════════════════════════════════════════════


class TestWorkerEpisodeImpact:
    @pytest.mark.parametrize("mode", ["annotate", "enforce"])
    async def test_annotate_enforce_carry_episode_impact(self, clock, sent, mode):
        w, g = _w4_worker(mode, late=False)
        await _feed(w, _jrec("J-1", at="20261007090000"))
        clock.t += 60
        await _feed(w, _polestar_rec("P-1", at="20261007090100"), 2)
        impact = g.states[1]["episode_app_impact"]
        assert impact["window_events"] == 1 and impact["window_minutes"] == 10
        assert impact["app_impact"]["fatal_events"] == 1
        assert impact["app_impact"]["event_types"] == ["ERROR_OUTOFMEMORY"]
        assert "episode_app_impact" not in g.states[0]  # 제니퍼 알람에는 싣지 않는다

    @pytest.mark.parametrize("mode", ["off", "shadow"])
    async def test_shadow_and_off_have_no_key(self, clock, mode):
        w, g = _w4_worker(mode, late=False)
        await _feed(w, _jrec("J-1", at="20261007090000"))
        await _feed(w, _polestar_rec("P-1", at="20261007090100"), 2)
        assert all("episode_app_impact" not in s for s in g.states)

    async def test_app_impact_off_has_no_key(self, clock):
        w, g = _w4_worker("enforce", late=False, impact=False)
        await _feed(w, _jrec("J-1", at="20261007090000"))
        await _feed(w, _polestar_rec("P-1", at="20261007090100"), 2)
        assert all("episode_app_impact" not in s for s in g.states)

    async def test_event_outside_window_is_counted_out(self, clock):
        w, g = _w4_worker("annotate", late=False)
        await _feed(w, _jrec("J-1", at="20261007084000"))  # 21분 전
        await _feed(w, _polestar_rec("P-1", at="20261007090100"), 2)
        impact = g.states[1]["episode_app_impact"]
        assert impact["window_events"] == 0 and impact["app_impact"] is None


# ═════════════════════════════════════════════════════════════════════════════
# D. 사후 승격(G-6)
# ═════════════════════════════════════════════════════════════════════════════


class TestLatePromotion:
    async def test_ticket_is_renotified_once(self, clock, sent):
        w, g = _w4_worker("annotate")
        await _feed(w, _polestar_rec("P-1"))
        assert g.decisions[0].tier == TIER_TICKET and g.decisions[0].stage == STAGE_MATRIX
        clock.t += 60
        await _feed(w, _jrec("J-1", at="20261007090100"), 2)
        assert len(sent) == 1
        result, decision, promotion = sent[0]
        assert result.alarm_event.alarm_id == "P-1" and decision.tier == TIER_TICKET
        ep = w._episodes.episodes()[0]
        assert promotion == {
            "episode_id": ep.id, "trigger_alarm_id": "J-1",
            "trigger_event_type": "ERROR_OUTOFMEMORY", "trigger_level": "fatal",
            "from_tier": TIER_TICKET,
            "reason": f"사후 승격: 같은 서버 제니퍼 ERROR_OUTOFMEMORY (사건 {ep.id})",
        }
        assert ep.member("P-1").late_promoted is True and ep.member("P-1").tier == TIER_TICKET
        rec = w._decision_store.late[0]
        assert rec["alarm_id"] == "P-1" and rec["episode_id"] == ep.id
        assert rec["from_tier"] == TIER_TICKET and rec["sent"] == {"incident": False, "workb": True}
        # 두 번째 심각 이벤트(다른 유형 — 지문 dedup 통과)에는 다시 보내지 않는다
        clock.t += 30
        await _feed(w, _jrec("J-2", at="20261007090130", name="ERROR_JVM_DOWN"), 3)
        assert len(g.states) == 3 and len(sent) == 1
        assert w._late_promotion == {}

    async def test_dashboard_is_renotified(self, clock, sent):
        w, g = _w4_worker("enforce")
        await _feed(w, _polestar_rec("P-1", severity=1))
        assert g.decisions[0].tier == TIER_DASHBOARD
        await _feed(w, _jrec("J-1", at="20261007090100"), 2)
        assert [s[2]["from_tier"] for s in sent] == [TIER_DASHBOARD]

    async def test_out_of_window_is_not_target(self, clock, sent):
        w, _ = _w4_worker("annotate")
        await _feed(w, _polestar_rec("P-1"))
        clock.t += 60
        await _feed(w, _jrec("J-1", at="20261007091100"), 2)  # 11분 뒤 발생
        assert sent == []

    async def test_resolved_member_is_not_target(self, clock, sent):
        w, _ = _w4_worker("annotate")
        await _feed(w, _polestar_rec("P-1"))
        await _feed(w, _polestar_rec("P-2", name="메모리 사용률"), 2)
        clock.t += 10
        await _feed(w, _polestar_rec("P-1c", severity=0), 3)  # P-1 해소
        await _feed(w, _jrec("J-1", at="20261007090100"), 4)
        assert [s[0].alarm_event.alarm_id for s in sent] == ["P-2"]

    async def test_page_and_non_fatal_are_not_targets(self, clock, sent):
        w, g = _w4_worker("annotate")
        g.importance["P-1"] = "3"  # 심각도2 × 높음 → PAGE
        await _feed(w, _polestar_rec("P-1"))
        assert g.decisions[0].tier == TIER_PAGE
        await _feed(w, _polestar_rec("P-2", name="메모리 사용률"), 2)
        await _feed(w, _jrec("J-w", at="20261007090100", level="warning", name="WARNING_X",
                             severity=2), 3)
        assert sent == []  # PAGE는 대상 아님 · 경고 레벨은 트리거 아님
        await _feed(w, _jrec("J-1", at="20261007090100"), 4)
        assert [s[0].alarm_event.alarm_id for s in sent] == ["P-2"]

    async def test_closed_episode_has_no_promotion(self, clock, sent):
        w, _ = _w4_worker("annotate")
        await _feed(w, _polestar_rec("P-1"))
        clock.t += 901  # idle 종료
        await _feed(w, _jrec("J-1", at="20261007090100"), 2)
        assert sent == [] and w._late_promotion == {}

    @pytest.mark.parametrize(
        "mode,late,impact",
        [("annotate", False, True), ("annotate", True, False), ("off", True, True),
         ("shadow", True, True)],  # shadow = 판정·통보 불변 — 플래그가 켜져도 재통보 없음
    )
    async def test_flags_off_no_promotion(self, clock, sent, mode, late, impact):
        w, _ = _w4_worker(mode, late=late, impact=impact)
        await _feed(w, _polestar_rec("P-1"))
        await _feed(w, _jrec("J-1", at="20261007090100"), 2)
        assert sent == [] and w._late_promotion == {}

    async def test_send_failure_is_logged_and_still_once(self, clock, monkeypatch, caplog):
        calls = []

        async def _boom(*a, **k):
            calls.append(a)
            raise RuntimeError("down")

        monkeypatch.setattr(worker_mod, "send_late_promotion", _boom)
        w, _ = _w4_worker("annotate")
        await _feed(w, _polestar_rec("P-1"))
        await _feed(w, _jrec("J-1", at="20261007090100"), 2)
        await _feed(w, _jrec("J-2", at="20261007090110", name="ERROR_JVM_DOWN"), 3)
        assert len(calls) == 1
        assert w._decision_store.late[0]["sent"] == {}
        assert any("사후 승격 통보 실패" in r.getMessage() for r in caplog.records)

    async def test_per_trigger_cap_defers_rest_to_next_trigger(self, clock, sent, monkeypatch,
                                                               caplog):
        monkeypatch.setattr(worker_mod, "_LATE_PROMOTION_PER_TRIGGER_MAX", 2)
        w, _ = _w4_worker("annotate")
        for i in range(3):
            await _feed(w, _polestar_rec(f"P-{i}", name=f"CPU 사용률 {i}"), i + 1)
        await _feed(w, _jrec("J-1", at="20261007090100"), 4)
        assert [s[0].alarm_event.alarm_id for s in sent] == ["P-0", "P-1"]
        ep = w._episodes.episodes()[0]
        assert ep.member("P-2").late_promoted is False and "P-2" in w._late_promotion
        assert any("트리거당 상한" in r.getMessage() for r in caplog.records)
        # 같은 사건의 다음 심각 이벤트가 남은 대상을 보낸다(알람당 1회는 그대로)
        await _feed(w, _jrec("J-2", at="20261007090110", name="ERROR_JVM_DOWN"), 5)
        assert [s[0].alarm_event.alarm_id for s in sent] == ["P-0", "P-1", "P-2"]
        assert w._late_promotion == {}

    async def test_candidate_cap_evicts_oldest(self, clock, monkeypatch, caplog):
        monkeypatch.setattr(worker_mod, "_LATE_PROMOTION_MAX", 2)
        w, _ = _w4_worker("annotate")
        for i in range(3):
            clock.t += 1
            await _feed(w, _polestar_rec(f"P-{i}", host=f"h{i}"), i + 1)
        assert sorted(w._late_promotion) == ["P-1", "P-2"]
        assert any("상한" in r.getMessage() for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════
# E. 사후 승격 통보 경로
# ═════════════════════════════════════════════════════════════════════════════


class _Publisher:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def publish(self, payload):
        self.payloads.append(payload)


class TestSendLatePromotion:
    async def test_publishes_page_incident_and_workb(self, monkeypatch):
        posts: list[dict] = []

        class _Client:
            def __init__(self, timeout=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json, headers):
                posts.append(json)
                return SimpleNamespace(raise_for_status=lambda: None)

        monkeypatch.setattr(notifier_mod.httpx, "AsyncClient", _Client)
        ev = polestar_event(severity=2, alarm_name="CPU 사용률")
        result = AlarmAnalysisResult(
            alarm_event=ev, severity_label="경고", summary="s", probable_cause="c",
            recommended_action="a", notification_channels=["workb"],
        )
        decision = NotificationDecision(TIER_TICKET, "매트릭스", 222, {}, "fp", STAGE_MATRIX,
                                        {"episode_id": "ep-1"})
        promotion = {"episode_id": "ep-1", "trigger_alarm_id": "J-1",
                     "reason": notifier_mod.late_promotion_reason("ERROR_X", "ep-1")}
        cfg = SimpleNamespace(workb=SimpleNamespace(
            base_url="http://workb", system_div="S", send_id="u", alias="[a]",
            bearer_token="t", timeout_seconds=1, get_user_ids=lambda sev: "u1",
        ))
        pub = _Publisher()
        got = await notifier_mod.send_late_promotion(result, decision, promotion, cfg, pub)
        assert got == {"incident": True, "workb": True}
        inc = pub.payloads[0]
        assert inc["tier"] == TIER_PAGE and inc["episode_id"] == "ep-1"
        assert inc["late_promotion"]["trigger_alarm_id"] == "J-1"
        assert posts[0]["msgTitle"].startswith("[사후 승격][경고]")
        assert "사후 승격: 같은 서버 제니퍼 ERROR_X (사건 ep-1)" in posts[0]["msgBody"]
        assert decision.tier == TIER_TICKET and "late_promotion" not in decision.evidence

    async def test_unconfigured_workb_is_graceful(self):
        ev = polestar_event(severity=2)
        result = AlarmAnalysisResult(
            alarm_event=ev, severity_label="경고", summary="s", probable_cause="c",
            recommended_action="a", notification_channels=["workb"],
        )
        decision = NotificationDecision(TIER_TICKET, "r", 1, {}, "fp", STAGE_MATRIX)
        cfg = SimpleNamespace(workb=SimpleNamespace(base_url=""))
        got = await notifier_mod.send_late_promotion(result, decision, {"reason": "x"}, cfg)
        assert got == {"incident": False, "workb": False}

    def test_incident_payload_without_promotion_has_no_key(self):
        ev = polestar_event(severity=2)
        result = AlarmAnalysisResult(
            alarm_event=ev, severity_label="경고", summary="s", probable_cause="c",
            recommended_action="a", notification_channels=["workb"],
        )
        decision = NotificationDecision(TIER_PAGE, "r", 1, {}, "fp", STAGE_MATRIX)
        assert "late_promotion" not in notifier_mod._incident_open_payload(result, decision)

    def test_decision_store_record(self, tmp_path):
        store = DecisionStore(path=tmp_path / "d.jsonl", enabled=True)
        store.record_late_promotion(alarm_id="P-1", episode_id="ep-1", from_tier="ticket",
                                    reason="r", sent={"workb": True})
        rec = json.loads((tmp_path / "d.jsonl").read_text(encoding="utf-8"))
        assert rec["type"] == "late_promotion" and rec["to_tier"] == TIER_PAGE
        assert store.aggregate()["total"] == 0  # type 레코드는 집계 제외


# ═════════════════════════════════════════════════════════════════════════════
# F. 정상 강등 shadow
# ═════════════════════════════════════════════════════════════════════════════


def _cpu(**kw):
    base = dict(severity=2, alarm_name="CPU 사용률", resource_type="server.Server",
                resource_name="cpu0")
    base.update(kw)
    return polestar_event(**base)


_EMPTY = {"rows": [], "source": "jennifer"}


class TestHealthyShadow:
    @pytest.mark.parametrize("importance,tier", [("2", TIER_TICKET), ("3", TIER_PAGE)])
    async def test_marker_recorded_and_decision_unchanged(self, importance, tier):
        ev = _cpu()
        base_client, base_store = _FakeApmClient(_EMPTY), _SpyStore()
        base = await notification_gate_node(
            _gate_state(ev, _ctx(importance)), _gate_config(base_client, base_store)
        )
        client, store = _FakeApmClient(_EMPTY), _SpyStore()
        out = await notification_gate_node(
            _gate_state(ev, _ctx(importance)),
            _gate_config(client, store, apm_healthy_demotion_shadow=True),
        )
        a, b = base["notification_decision"], out["notification_decision"]
        assert (a.tier, a.reason, a.priority, a.stage, a.evidence) == (
            b.tier, b.reason, b.priority, b.stage, b.evidence
        )
        assert b.tier == tier
        assert store.records[0]["stage_evidence"]["apm_healthy_shadow"] == {
            "would_demote": True, "window_minutes": 10, "source": "mcp",
        }
        assert "apm_healthy_shadow" not in base_store.records[0]["stage_evidence"]
        assert client.calls[0]["level"] == "normal"
        # 호출 증가는 shadow on에서 PAGE 판정일 때만(현행은 PAGE면 묻지 않는다)
        assert len(base_client.calls) == (1 if tier == TIER_TICKET else 0)

    async def test_events_in_window_no_marker(self):
        warn = {"rows": [{"level": "warning", "event_type": "WARNING_X"}], "source": "jennifer"}
        store = _SpyStore()
        out = await notification_gate_node(
            _gate_state(_cpu(), _ctx("2")),
            _gate_config(_FakeApmClient(warn), store, apm_healthy_demotion_shadow=True),
        )
        assert out["notification_decision"].tier == TIER_TICKET
        assert "apm_healthy_shadow" not in store.records[0]["stage_evidence"]

    async def test_fatal_still_promotes_with_normal_level_query(self):
        client, store = _FakeApmClient(_fatal_rows(2)), _SpyStore()
        out = await notification_gate_node(
            _gate_state(_cpu(), _ctx("2")),
            _gate_config(client, store, apm_healthy_demotion_shadow=True),
        )
        d = out["notification_decision"]
        assert d.tier == TIER_PAGE and "APM fatal 2건" in d.reason
        assert "apm_healthy_shadow" not in store.records[0]["stage_evidence"]

    @pytest.mark.parametrize(
        "client",
        [
            _FakeApmClient(error=ApmGatewayClientError("timeout")),
            _FakeApmClient(unreachable="연결 불가"),
            _FakeApmClient({"error": "source_unavailable", "reason": "x"}),
        ],
    )
    async def test_gateway_failure_no_marker(self, client):
        store = _SpyStore()
        out = await notification_gate_node(
            _gate_state(_cpu(), _ctx("3")),
            _gate_config(client, store, apm_healthy_demotion_shadow=True),
        )
        ev = store.records[0]["stage_evidence"]
        assert out["notification_decision"].tier == TIER_PAGE
        assert "apm_healthy_shadow" not in ev and ev["app_impact_error"]

    async def test_missing_client_no_marker(self):
        store = _SpyStore()
        await notification_gate_node(
            _gate_state(_cpu(), _ctx("3")),
            _gate_config(None, store, apm_healthy_demotion_shadow=True),
        )
        assert "apm_healthy_shadow" not in store.records[0]["stage_evidence"]

    @pytest.mark.parametrize(
        "event",
        [
            polestar_event(severity=2),  # disk
            _cpu(severity=1),  # 경고 아님
            apm_event(severity=2, alarm_name="WARNING_CPU"),  # APM 알람
        ],
    )
    async def test_non_candidates_have_no_marker(self, event):
        client, store = _FakeApmClient(_EMPTY), _SpyStore()
        await notification_gate_node(
            _gate_state(event, _ctx("3")),
            _gate_config(client, store, apm_healthy_demotion_shadow=True),
        )
        assert "apm_healthy_shadow" not in store.records[0]["stage_evidence"]
        assert all(c["level"] == "fatal" for c in client.calls)

    async def test_off_has_no_key_and_current_call(self):
        client, store = _FakeApmClient(_EMPTY), _SpyStore()
        await notification_gate_node(
            _gate_state(_cpu(), _ctx("2")), _gate_config(client, store)
        )
        assert "apm_healthy_shadow" not in store.records[0]["stage_evidence"]
        assert client.calls[0]["level"] == "fatal"

    async def test_needs_app_impact_enabled(self):
        client, store = _FakeApmClient(_EMPTY), _SpyStore()
        await notification_gate_node(
            _gate_state(_cpu(), _ctx("3")),
            _gate_config(client, store, apm_healthy_demotion_shadow=True,
                         app_impact_enabled=False),
        )
        assert client.calls == []
        assert "apm_healthy_shadow" not in store.records[0]["stage_evidence"]

    async def test_episode_events_short_circuit(self):
        client, store = _FakeApmClient(_EMPTY), _SpyStore()
        impact = {"app_impact": None, "window_events": 1, "window_minutes": 10}
        await notification_gate_node(
            {**_gate_state(_cpu(), _ctx("3")), "episode_app_impact": impact},
            _gate_config(client, store, apm_healthy_demotion_shadow=True),
        )
        assert client.calls == []  # 사건에 이미 APM 이벤트 — 정상 아님, 조회 불필요
        assert "apm_healthy_shadow" not in store.records[0]["stage_evidence"]


# ═════════════════════════════════════════════════════════════════════════════
# G. 조사 트리거 사건당 1회
# ═════════════════════════════════════════════════════════════════════════════


def _page() -> NotificationDecision:
    return NotificationDecision(TIER_PAGE, "심각도3", 330, {}, "fp")


class TestInvestigationNode:
    async def test_existing_episode_investigation_skips_apm(self):
        store, client = _SpyStore(), _SpySreClient()
        state = {
            "alarm_event": apm_event(), "notification_decision": _page(),
            "cross_source": {"episode_id": "ep-1"},
            "episode_investigation": {"episode_id": "ep-1", "investigation_id": "inv-0"},
        }
        out = await investigation_trigger_node(state, _trigger_cfg(store, client))
        assert out == {} and client.payloads == []
        assert store.investigations == [{
            "alarm_id": state["alarm_event"].alarm_id, "fingerprint": "fp",
            "investigation_id": "inv-0", "status": "episode_existing", "verdict": None,
            "episode_id": "ep-1",
        }]

    async def test_payload_and_audit_carry_episode_id(self):
        store, client = _SpyStore(), _SpySreClient()
        state = {"alarm_event": apm_event(), "notification_decision": _page(),
                 "cross_source": {"episode_id": "ep-1"}}
        await investigation_trigger_node(state, _trigger_cfg(store, client))
        assert client.payloads[0]["meta"]["episode_id"] == "ep-1"
        assert store.investigations[0]["episode_id"] == "ep-1"

    async def test_off_is_bit_identical(self):
        store, client = _SpyStore(), _SpySreClient()
        state = {"alarm_event": apm_event(), "notification_decision": _page()}
        await investigation_trigger_node(state, _trigger_cfg(store, client))
        assert "episode_id" not in client.payloads[0]["meta"]
        assert "episode_id" not in store.investigations[0]

    async def test_polestar_alarm_is_not_skipped(self):
        store, client = _SpyStore(), _SpySreClient()
        state = {
            "alarm_event": polestar_event(severity=3), "notification_decision": _page(),
            "cross_source": {"episode_id": "ep-1"},
            "episode_investigation": {"episode_id": "ep-1", "investigation_id": "inv-0"},
        }
        await investigation_trigger_node(state, _trigger_cfg(store, client))
        assert len(client.payloads) == 1


class _TriggerGraph(_LateGraph):
    """판정 뒤 실제 조사 트리거 노드를 부르는 가짜 그래프."""

    def __init__(self, store, client) -> None:
        super().__init__()
        self.cfg = _trigger_cfg(store, client)

    async def ainvoke(self, state, config=None):
        out = await super().ainvoke(state, config)
        return {**out, **await investigation_trigger_node(out, self.cfg)}


class TestWorkerInvestigationOnce:
    async def _run(self, mode: str):
        store, client = _SpyStore(), _SpySreClient()
        w, _ = _w4_worker(mode, late=False)
        g = _TriggerGraph(store, client)
        w._graph = g
        await _feed(w, _jrec("J-1", at="20261007090000"))
        await _feed(w, _jrec("J-2", at="20261007090030", name="ERROR_JVM_DOWN"), 2)
        return w, g, store, client

    async def test_same_episode_submits_once(self, clock):
        w, g, store, client = await self._run("shadow")
        ep = w._episodes.episodes()[0]
        assert len(client.payloads) == 1 and client.payloads[0]["meta"]["episode_id"] == ep.id
        assert ep.apm_investigation_id == "inv-1"
        assert g.states[1]["episode_investigation"] == {"episode_id": ep.id,
                                                        "investigation_id": "inv-1"}
        assert [i["status"] for i in store.investigations] == ["done", "episode_existing"]
        assert store.investigations[1]["investigation_id"] == "inv-1"
        assert all(i["episode_id"] == ep.id for i in store.investigations)

    async def test_off_submits_each(self, clock):
        _, g, store, client = await self._run("off")
        assert len(client.payloads) == 2
        assert all("episode_id" not in p["meta"] for p in client.payloads)
        assert all("episode_id" not in i for i in store.investigations)
        assert all("episode_investigation" not in s for s in g.states)
