"""plans/153 W4 — 조사 페이로드 사건 스냅샷(`meta.episode`·`meta.cross_source`) ·
사건당 1회 종합 조사.

    A. 도메인 — 스냅샷 모양(W5 수신 계약 키 전부 · `effect_alarm_id` · 최근 순 · 상한) · 신호 불변 ·
       종합 조사 조건
    B. 페이로드 — 플래그 off 비트 동일(W0 골든) · on 가산 키 · 종합 조사 페이로드
    C. 워커 — 종합 조사 정확히 1회 · `episode_existing` 연결 · 저장소 `alarm_ids` 다중 ·
       통보 멤버 0 · 한쪽 소스 · 플래그 off · 실패 재시도 없음 · workb 후속 1건
    D. 리플레이 하네스 — 종합 조사 발주 수

픽스처: `noise_gate/testdata/plan153/cross_source_incident.jsonl`(폴스타 CPU PAGE → 270초 뒤 같은
호스트 제니퍼 큐잉 · `host_resource_to_was_latency` · missing 없음 — W0 작성).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.investigation_trigger import investigation_trigger_node
from noise_gate.application.server_identity import is_apm_source
from noise_gate.domain.alarm import AlarmAnalysisResult
from noise_gate.domain.cross_source import (
    MAX_LINKS_PER_EPISODE,
    SNAPSHOT_MAX_MEMBERS,
    SOURCE_JENNIFER,
    SOURCE_POLESTAR,
    CrossSourceRule,
    EpisodeAlarm,
    EpisodeTracker,
    SideSpec,
    episode_investigation_due,
    episode_snapshot,
)
from noise_gate.domain.investigation_payload import (
    CONTRACT_VERSION,
    build_episode_trigger_payload,
)
from noise_gate.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    NotificationDecision,
    decide_notification,
)
from noise_gate.infrastructure.investigation_store import InvestigationStore
from noise_gate.scripts import replay_noise as rn
from noise_gate.tests.test_plan144_cross_source import SHIPPED_RULES, _Clock, _Redis

TESTDATA = Path(__file__).resolve().parents[1] / "testdata" / "plan153"
INCIDENT = TESTDATA / "cross_source_incident.jsonl"
PAYLOAD_GOLDEN = TESTDATA / "trigger_payloads.golden.json"
EPISODE_ID = "ep-p150fixture"  # W0 골든과 같은 고정 id

P_ID = "P150-CPU-1"
J_ID = "jennifer:p150q0000000001"

# W5 수신 계약(plans/153 §4.4 · sre_agent `cross_source_episode`)이 읽는 키.
EPISODE_KEYS = {"episode_id", "zone", "host_key", "members", "links", "rules", "infra_db_ids"}
MEMBER_KEYS = {"alarm_id", "source", "db_id", "alarm_name", "kind", "was_kinds", "level",
               "severity", "occurred_at", "tier", "resolved"}
LINK_KEYS = {"rule_id", "cause_alarm_id", "effect_alarm_id", "lag_seconds", "action", "missing"}


def _lines() -> list[dict[str, Any]]:
    return rn.load_jsonl(str(INCIDENT))


def _fixture_tracker() -> tuple[EpisodeTracker, str]:
    """픽스처 두 알람을 실 추적기·규칙 표로 묶는다 → (추적기, 사건 id). 원인은 PAGE로 기록."""
    tracker = rn.build_tracker("shadow")
    episode_id = ""
    for line, tier in zip(_lines(), (TIER_PAGE, TIER_PAGE)):
        ev = rn.event_from_payload(line["payload"])
        signal = rn._cross_source_signal(tracker, ev)
        assert signal is not None
        episode_id = signal["episode_id"]
        tracker.record_tier(episode_id, ev.alarm_id, tier)
    return tracker, episode_id


# ═════════════════════════════════════════════════════════════════════════════
# A. 도메인
# ═════════════════════════════════════════════════════════════════════════════

_RULE = CrossSourceRule(
    id="r-cpu-was", cause=SideSpec(SOURCE_POLESTAR, kinds=frozenset({"cpu"})),
    effect=SideSpec(SOURCE_JENNIFER, was_kinds=frozenset({"*"})),
    cause_before_seconds=600, cause_after_seconds=60,
)
_UNUSED_RULE = CrossSourceRule(
    id="r-unused", cause=SideSpec(SOURCE_POLESTAR, kinds=frozenset({"disk"})),
    effect=SideSpec(SOURCE_JENNIFER, alarm_names=frozenset({"X"})),
    cause_before_seconds=60, cause_after_seconds=0,
)


def _alarm(alarm_id: str, source: str, at: float, **kw: Any) -> EpisodeAlarm:
    return EpisodeAlarm(
        alarm_id=alarm_id, source=source, zone="gongjon", host_key="xsapp01",
        host_key_strength="strong", db_id="pg" if source == SOURCE_POLESTAR else "jennifer_c",
        occurred_at=at, severity=2, kind="cpu" if source == SOURCE_POLESTAR else "",
        was_kinds=("was_service_queuing",) if source == SOURCE_JENNIFER else (),
        alarm_name=kw.pop("name", alarm_id), level=kw.pop("level", ""), **kw,
    )


class TestSnapshotShape:
    def test_fixture_snapshot_matches_contract(self):
        tracker, episode_id = _fixture_tracker()
        snap = episode_snapshot(tracker.get(episode_id), tracker.rules)
        assert set(snap) == EPISODE_KEYS
        assert snap["episode_id"] == episode_id and snap["host_key"] == "xsapp01"
        assert snap["zone"]
        # 최근 순 — 나중에 온 제니퍼 증상이 앞
        assert [m["alarm_id"] for m in snap["members"]] == [J_ID, P_ID]
        assert all(set(m) == MEMBER_KEYS for m in snap["members"])
        j, p = snap["members"]
        assert (j["source"], p["source"]) == ("jennifer", "polestar")
        assert j["was_kinds"] == ["was_service_queuing"] and j["level"] == "warning"
        assert j["alarm_name"] == "SERVICE_QUEUING"
        assert p["kind"] == "cpu" and p["alarm_name"] == "CPU 사용률" and p["level"] == ""
        assert isinstance(p["occurred_at"], float) and p["tier"] == TIER_PAGE
        assert p["resolved"] is False
        assert snap["links"] == [{
            "rule_id": "host_resource_to_was_latency", "cause_alarm_id": P_ID,
            "effect_alarm_id": J_ID, "lag_seconds": 270.0, "action": "demote", "missing": [],
        }]
        assert [r["id"] for r in snap["rules"]] == ["host_resource_to_was_latency"]
        rule = snap["rules"][0]
        assert set(rule) == {"id", "cause", "effect", "window"}
        assert rule["cause"]["source"] == "polestar" and rule["effect"]["source"] == "jennifer"
        assert set(rule["window"]) == {"cause_before_seconds", "cause_after_seconds"}
        assert snap["infra_db_ids"] == ["polestar_cm_gp"]
        json.dumps(snap)  # JSON 직렬화 가능

    def test_only_linked_rules_and_selector_shape(self):
        tracker = EpisodeTracker([_RULE, _UNUSED_RULE], mode="shadow", idle_seconds=900)
        sig = tracker.observe(_alarm("P", SOURCE_POLESTAR, 1000.0), 1000.0)
        tracker.record_tier(sig["episode_id"], "P", TIER_PAGE)
        tracker.observe(_alarm("J", SOURCE_JENNIFER, 1100.0), 1100.0)
        snap = episode_snapshot(tracker.get(sig["episode_id"]), tracker.rules)
        assert snap["rules"] == [{
            "id": "r-cpu-was",
            "cause": {"source": "polestar", "kinds": ["cpu"]},
            "effect": {"source": "jennifer", "was_kinds": ["*"]},
            "window": {"cause_before_seconds": 600, "cause_after_seconds": 60},
        }]

    def test_member_and_link_caps_keep_most_recent(self):
        tracker = EpisodeTracker([_RULE], mode="shadow", idle_seconds=10_000)
        sig = tracker.observe(_alarm("P", SOURCE_POLESTAR, 1000.0), 1000.0)
        tracker.record_tier(sig["episode_id"], "P", TIER_PAGE)
        for i in range(60):
            tracker.observe(_alarm(f"J-{i:02d}", SOURCE_JENNIFER, 1001.0 + i), 1001.0 + i)
        ep = tracker.get(sig["episode_id"])
        assert len(ep.links) == MAX_LINKS_PER_EPISODE == 50  # 사건 누적 상한
        snap = episode_snapshot(ep, tracker.rules)
        assert len(snap["members"]) == SNAPSHOT_MAX_MEMBERS == 20
        assert [m["alarm_id"] for m in snap["members"][:2]] == ["J-59", "J-58"]
        assert len(snap["links"]) == 50
        assert snap["links"][0]["effect_alarm_id"] == "J-59"
        assert snap["links"][-1]["effect_alarm_id"] == "J-10"  # 오래된 10건은 정리됨

    def test_signal_and_link_dict_have_no_effect_alarm_id(self):
        """판정 근거(신호·`CrossSourceLink.to_dict`)는 그대로 — 판정 레코드 비트 동일."""
        tracker, episode_id = _fixture_tracker()
        ep = tracker.get(episode_id)
        assert "effect_alarm_id" not in ep.links[0].to_dict()
        tracker2 = rn.build_tracker("shadow")
        signals = [rn._cross_source_signal(tracker2, rn.event_from_payload(x["payload"]))
                   for x in _lines()]
        assert all("effect_alarm_id" not in s for s in signals)

    def test_episode_to_dict_members_carry_new_fields(self):
        tracker, episode_id = _fixture_tracker()
        members = tracker.get(episode_id).to_dict()["members"]
        assert all(MEMBER_KEYS <= set(m) and "late_promoted" in m for m in members)


class TestDue:
    def _ep(self, *, cause_tier: str | None = TIER_PAGE, effect: bool = True):
        tracker = EpisodeTracker([_RULE], mode="shadow", idle_seconds=900)
        sig = tracker.observe(_alarm("P", SOURCE_POLESTAR, 1000.0), 1000.0)
        tracker.record_tier(sig["episode_id"], "P", cause_tier)
        if effect:
            tracker.observe(_alarm("J", SOURCE_JENNIFER, 1100.0), 1100.0)
            tracker.record_tier(sig["episode_id"], "J", TIER_DASHBOARD)
        return tracker.get(sig["episode_id"])

    def test_due_when_all_conditions_hold(self):
        assert episode_investigation_due(self._ep()) is True

    def test_single_source_is_not_due(self):
        assert episode_investigation_due(self._ep(effect=False)) is False

    def test_no_notified_member_is_not_due(self):
        ep = self._ep(cause_tier=TIER_DASHBOARD)
        assert ep.links and episode_investigation_due(ep) is False

    def test_no_link_is_not_due(self):
        ep = self._ep()
        ep.links.clear()
        assert episode_investigation_due(ep) is False

    def test_attempted_is_not_due(self):
        ep = self._ep()
        ep.episode_investigation_status = "down"
        assert episode_investigation_due(ep) is False


# ═════════════════════════════════════════════════════════════════════════════
# B. 페이로드
# ═════════════════════════════════════════════════════════════════════════════


class _Client:
    """조사 서비스 대역 — 제출마다 새 id. `fail_kinds`의 investigation_kind 제출은 예외."""

    def __init__(self, *, fail_kinds: tuple[str, ...] = ()) -> None:
        self.payloads: list[dict] = []
        self.fail_kinds = fail_kinds

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def submit(self, payload: dict) -> dict:
        if payload["meta"].get("investigation_kind") in self.fail_kinds:
            raise ConnectionError("down")
        self.payloads.append(payload)
        return {"investigation_id": f"inv-{len(self.payloads)}", "status": "accepted"}

    async def poll(self, inv_id: str) -> dict:
        return {"status": "done", "briefing": {"summary": f"brief {inv_id}"}, "verdict": None}

    def kinds(self) -> list[str | None]:
        return [p["meta"].get("investigation_kind") for p in self.payloads]


class _Audit:
    def __init__(self) -> None:
        self.investigations: list[dict] = []

    def record_investigation(self, **kw: Any) -> None:
        self.investigations.append(kw)

    def record_late_promotion(self, **kw: Any) -> None:
        pass

    def record_resolution(self, **kw: Any) -> None:
        pass

    def record_recurrence(self, **kw: Any) -> None:
        pass


def _gate(episode_ctx: bool, **kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = dict(
        investigation_trigger_enabled=True, investigation_trigger_min_tier="PAGE",
        investigation_poll_interval_seconds=0.0, investigation_total_timeout_seconds=5.0,
        investigation_episode_context_enabled=episode_ctx,
    )
    return SimpleNamespace(**{**base, **kw})


def _node_cfg(episode_ctx: bool, client: _Client, audit: Any = None, inv_store: Any = None):
    return {"configurable": {
        "app_config": SimpleNamespace(
            noise_gate=_gate(episode_ctx),
            host_authz=SimpleNamespace(mode="admin_only"),
            composite=SimpleNamespace(
                prior_targets_enabled=False, availability_precheck_enabled=False
            ),
        ),
        "sre_agent_client": client, "decision_store": audit, "investigation_store": inv_store,
    }}


def _golden() -> dict[str, Any]:
    return json.loads(PAYLOAD_GOLDEN.read_text(encoding="utf-8"))


def _j_decision(evidence: dict | None = None) -> NotificationDecision:
    """W0 골든의 제니퍼 판정 + 결합 판정 근거(evidence.cross_source)."""
    return NotificationDecision(
        tier="page", reason="수집 실패 — 원문 알람으로 판단", priority=3,
        signals={"severity": 2, "collection_failed": True}, fingerprint="fp-p150-queuing",
        stage="matrix", evidence=evidence or {},
    )


_CS_EVIDENCE = {"cross_source": {
    "mode": "shadow", "host_key": "xsapp01", "rule_id": "host_resource_to_was_latency",
    "cause_alarm_id": P_ID, "lag_seconds": 270.0, "action": "demote", "enforce": False,
    "missing": [], "applied": False,
}, "episode_id": EPISODE_ID}


def _snapshot_state() -> dict[str, Any]:
    tracker, episode_id = _fixture_tracker()
    snap = episode_snapshot(tracker.get(episode_id), tracker.rules)
    snap["episode_id"] = EPISODE_ID
    snap["members"][0]["tier"] = None  # 워커는 판정 전에 스냅샷을 만든다 — 이 알람 티어는 빈 값
    return {
        "alarm_event": rn.event_from_payload(_lines()[1]["payload"]),
        "notification_decision": _j_decision(_CS_EVIDENCE),
        "cross_source": {"episode_id": EPISODE_ID},
        "episode_snapshot": snap,
    }


class TestTriggerPayload:
    async def test_flag_off_is_bit_identical_to_w0_golden(self):
        client = _Client()
        await investigation_trigger_node(_snapshot_state(), _node_cfg(False, client))
        assert client.payloads == [_golden()["jennifer_with_episode"]]

    async def test_flag_on_without_snapshot_is_bit_identical(self):
        client = _Client()
        state = _snapshot_state()
        del state["episode_snapshot"]  # 사건 추적기 없음(cross_source_mode off)
        await investigation_trigger_node(state, _node_cfg(True, client))
        assert client.payloads == [_golden()["jennifer_with_episode"]]

    async def test_flag_on_adds_episode_and_cross_source(self):
        client = _Client()
        state = _snapshot_state()
        await investigation_trigger_node(state, _node_cfg(True, client))
        payload = client.payloads[0]
        assert payload["contract_version"] == CONTRACT_VERSION == "1"
        meta = payload["meta"]
        assert meta["cross_source"] == _CS_EVIDENCE["cross_source"]
        assert "investigation_kind" not in meta  # 단독 조사
        episode = meta["episode"]
        assert set(episode) == EPISODE_KEYS and episode["episode_id"] == EPISODE_ID
        assert episode["members"][0]["alarm_id"] == J_ID
        assert episode["members"][0]["tier"] == "page"  # 제출 시점 판정 티어를 채운다
        assert state["episode_snapshot"]["members"][0]["tier"] is None  # 원본 불변
        # 가산 키를 빼면 골든과 같다(계약 가산만)
        stripped = {**payload, "meta": {k: v for k, v in meta.items()
                                        if k not in ("episode", "cross_source")}}
        assert stripped == _golden()["jennifer_with_episode"]

    def test_episode_payload_shape(self):
        state = _snapshot_state()
        p_event = rn.event_from_payload(_lines()[0]["payload"])
        decision = dataclasses.replace(_j_decision(), fingerprint="fp-cpu")
        payload = build_episode_trigger_payload(p_event, decision, state["episode_snapshot"])
        assert payload["contract_version"] == "1"
        assert payload["event"]["alarmId"] == P_ID
        assert payload["decision"]["fingerprint"] == f"episode:{EPISODE_ID}"
        meta = payload["meta"]
        assert meta["investigation_kind"] == "episode" and meta["episode_id"] == EPISODE_ID
        assert meta["episode"] == state["episode_snapshot"]
        assert "cross_source" not in meta  # 대표 알람에 결합 판정 근거가 없으면 키 없음


# ═════════════════════════════════════════════════════════════════════════════
# C. 워커 — 종합 조사
# ═════════════════════════════════════════════════════════════════════════════

_POLICY_CFG = SimpleNamespace(
    suppress_max_severity=2, importance_value_map={"1": "낮음", "2": "보통", "3": "높음"},
    resolved_to_dashboard=False,
)
_APM_CTX = {"importance_id": "3", "maintenance": None, "noti_policy": "notify",
            "source": "apm_policy"}


class _Graph:
    """정책 순수 함수로 판정하고 실제 조사 트리거 노드를 부르는 가짜 그래프."""

    def __init__(self, node_cfg: dict, *, force_tier: str | None = None,
                 workb_sent: bool = False) -> None:
        self.node_cfg = node_cfg
        self.force_tier = force_tier
        self.workb_sent = workb_sent
        self.states: list[dict] = []

    async def ainvoke(self, state: dict, config: Any = None) -> dict:
        self.states.append(state)
        ev = state["alarm_event"]
        ctx = _APM_CTX if is_apm_source(ev) else {
            "importance_id": "3", "maintenance": False, "noti_policy": None, "source": "fx"}
        decision = decide_notification(ev, None, None, ctx, _POLICY_CFG,
                                       cross_source=state.get("cross_source"))
        if self.force_tier:
            decision = dataclasses.replace(decision, tier=self.force_tier)
        analysis = AlarmAnalysisResult(
            alarm_event=ev, severity_label="경고", summary="s", probable_cause="c",
            recommended_action="a", notification_channels=["workb"],
            notifications_sent={"workb": True} if self.workb_sent else {},
        )
        out = {**state, "notification_decision": decision, "analysis_result": analysis}
        return {**out, **await investigation_trigger_node(out, self.node_cfg)}


def _worker(tmp_path: Path, *, episode_ctx: bool = True, mode: str = "shadow",
            force_tier: str | None = None, workb_sent: bool = False,
            followup: bool = False, client: _Client | None = None):
    client = client or _Client()
    audit = _Audit()
    inv_store = InvestigationStore(str(tmp_path / "inv.jsonl"))
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True, repeat_interval_seconds=14400, suppress_max_severity=2,
            dedup_severity_rise_bypass=False,
            self_heal_window_seconds=300, cross_source_mode=mode,
            cross_source_rules_path=str(SHIPPED_RULES), episode_idle_seconds=900,
            app_impact_enabled=False, app_impact_late_promotion_enabled=False,
            investigation_trigger_enabled=True, investigation_episode_context_enabled=episode_ctx,
            investigation_followup_enabled=followup, investigation_followup_timeout_seconds=5.0,
            investigation_followup_max_inflight=8, investigation_poll_interval_seconds=0.0,
            fault_escalation_enabled=False,
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
        host_authz=SimpleNamespace(mode="admin_only"),
        composite=SimpleNamespace(prior_targets_enabled=False, availability_precheck_enabled=False),
        workb=SimpleNamespace(),
    )
    w = AlarmWorker(cfg)
    g = _Graph(_node_cfg(episode_ctx, client, audit, inv_store),
               force_tier=force_tier, workb_sent=workb_sent)
    w._graph = g
    w._episodes = w._build_episode_tracker()
    w._decision_store = audit
    w._investigation_store = inv_store
    w._sre_agent_client = client
    return SimpleNamespace(w=w, g=g, client=client, audit=audit, inv=inv_store)


async def _feed(w: AlarmWorker, payload: dict, n: int) -> None:
    fields = {b"data": json.dumps(payload, ensure_ascii=False).encode("utf-8")}
    await w._process(_Redis(), "alarm:raw", "g", f"{n}-1".encode(), fields, {})


def _later_jennifer(alarm_id: str, at: str) -> dict:
    """같은 호스트의 다른 유형 제니퍼 이벤트(지문 dedup에 걸리지 않게 유형을 바꾼다)."""
    p = json.loads(json.dumps(_lines()[1]["payload"]))
    name = "SERVICE_EXCEPTION"
    p.update(alarmId=alarm_id, alarmTime=at, alarmName=name)
    p["apm"].update(idempotency_key=alarm_id, event_type=name, event_type_norm=name)
    return p


async def _drain() -> None:
    while notifier_mod._FOLLOWUP_TASKS:
        await asyncio.gather(*list(notifier_mod._FOLLOWUP_TASKS))


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


@pytest.fixture
def followup(monkeypatch) -> list[tuple]:
    """후속 poll(자체 클라이언트)·workb 후속 발송을 가로챈다(실 호출 없음)."""
    sent: list[tuple] = []

    async def _poll(investigation_id, gate_cfg):
        return {"summary": f"brief {investigation_id}"}, "done", None, None

    async def _send(workb_cfg, result, briefing, escalation, l3=None, **kw):
        # (plans/153 W6) 종합 조사 후속만 `episode_note` 키워드를 받는다 — 단독 조사는 종전 호출.
        sent.append((result.alarm_event.alarm_id, briefing, *kw.values()))

    monkeypatch.setattr(notifier_mod, "_poll_until_terminal", _poll)
    monkeypatch.setattr(notifier_mod, "_send_workb_followup", _send)
    return sent


class TestWorkerEpisodeInvestigation:
    async def test_exactly_once_and_jennifer_followups_link(self, tmp_path, clock, followup):
        t = _worker(tmp_path)
        lines = _lines()
        await _feed(t.w, lines[0]["payload"], 1)
        await _feed(t.w, lines[1]["payload"], 2)
        await _feed(t.w, _later_jennifer("jennifer:p150q0000000002", "20261007090500"), 3)
        await _drain()
        # 폴스타 단독 · 제니퍼 단독 · 종합 1회 — 세 번째(제니퍼 후속)는 새로 내지 않는다
        assert t.client.kinds() == [None, None, "episode"]
        ep = t.w._episodes.episodes()[0]
        episode_payload = t.client.payloads[2]
        assert ep.episode_investigation_id == "inv-3"
        assert episode_payload["event"]["alarmId"] == P_ID  # 대표 = 먼저 쓰인 원인
        assert episode_payload["decision"]["fingerprint"] == f"episode:{ep.id}"
        snap = episode_payload["meta"]["episode"]
        assert [m["alarm_id"] for m in snap["members"]] == [J_ID, P_ID]
        assert snap["links"][0]["effect_alarm_id"] == J_ID
        # 단독 조사도 제출 시점 스냅샷을 싣는다 — 폴스타 때는 멤버 1, 제니퍼 때는 2
        assert len(t.client.payloads[0]["meta"]["episode"]["members"]) == 1
        assert len(t.client.payloads[1]["meta"]["episode"]["members"]) == 2
        assert t.client.payloads[1]["meta"]["cross_source"]["cause_alarm_id"] == P_ID
        # 제니퍼 후속 알람은 종합 조사 id로 연결
        assert t.g.states[2]["episode_investigation"] == {
            "episode_id": ep.id, "investigation_id": "inv-3"}
        link = [i for i in t.audit.investigations if i["status"] == "episode_existing"]
        assert [i["investigation_id"] for i in link] == ["inv-3"]
        # 저장소 — 종합 조사 1건에 멤버 전부(대표가 첫 원소) + 연결된 후속 알람
        rec = t.inv.get_by_investigation("inv-3")
        assert rec["kind"] == "episode" and rec["episode_id"] == ep.id
        assert rec["status"] == "done"  # 후속 poll이 남긴 종결 상태(연결 행이 덮지 않음)
        assert rec["alarm_ids"] == [P_ID, J_ID, "jennifer:p150q0000000002"]
        assert t.inv.get_by_alarm(J_ID)["investigation_id"] == "inv-3"
        # 워크비 후속 — 플래그 off(인라인 모드)면 보내지 않는다(저장만)
        assert followup == []

    async def test_workb_followup_once_for_representative(self, tmp_path, clock, followup):
        t = _worker(tmp_path, followup=True, workb_sent=True)
        lines = _lines()
        await _feed(t.w, lines[0]["payload"], 1)
        await _feed(t.w, lines[1]["payload"], 2)
        await _drain()
        assert t.client.kinds().count("episode") == 1
        episode_inv = t.w._episodes.episodes()[0].episode_investigation_id
        ep_id = t.w._episodes.episodes()[0].id
        assert followup == [(P_ID, {"summary": f"brief {episode_inv}"},
                             f"같은 사건(폴스타×제니퍼) 종합 조사 — 사건 {ep_id} · 멤버 알람 2건")]

    async def test_no_notified_member_no_episode_investigation(self, tmp_path, clock, followup):
        t = _worker(tmp_path, force_tier=TIER_DASHBOARD)
        lines = _lines()
        await _feed(t.w, lines[0]["payload"], 1)
        await _feed(t.w, lines[1]["payload"], 2)
        ep = t.w._episodes.episodes()[0]
        assert ep.links  # 결합 링크(원인 티어 미달 → link)는 있다
        assert t.client.payloads == [] and ep.episode_investigation_status == ""

    async def test_single_source_no_episode_investigation(self, tmp_path, clock, followup):
        t = _worker(tmp_path)
        p = _lines()[0]["payload"]
        await _feed(t.w, p, 1)
        await _feed(t.w, {**p, "alarmId": "P150-MEM-1", "alarmTime": "20261007090100",
                          "alarmName": "메모리 사용률", "resourceName": "MEM"}, 2)
        assert "episode" not in t.client.kinds() and len(t.client.payloads) == 2

    async def test_flag_off_no_snapshot_and_no_episode_investigation(
        self, tmp_path, clock, followup
    ):
        t = _worker(tmp_path, episode_ctx=False)
        lines = _lines()
        await _feed(t.w, lines[0]["payload"], 1)
        await _feed(t.w, lines[1]["payload"], 2)
        assert t.client.kinds() == [None, None]
        assert all(not ({"episode", "cross_source", "investigation_kind"} & set(p["meta"]))
                   for p in t.client.payloads)
        assert all("episode_snapshot" not in s for s in t.g.states)
        assert t.w._episode_ctx == {}

    async def test_cross_source_off_has_no_tracker_no_snapshot(self, tmp_path, clock, followup):
        t = _worker(tmp_path, mode="off")
        lines = _lines()
        await _feed(t.w, lines[0]["payload"], 1)
        await _feed(t.w, lines[1]["payload"], 2)
        assert t.client.kinds() == [None, None]
        assert all("episode" not in p["meta"] for p in t.client.payloads)

    async def test_submit_failure_is_recorded_and_not_retried(self, tmp_path, clock, followup):
        t = _worker(tmp_path, client=_Client(fail_kinds=("episode",)))
        lines = _lines()
        await _feed(t.w, lines[0]["payload"], 1)
        await _feed(t.w, lines[1]["payload"], 2)
        await _feed(t.w, _later_jennifer("jennifer:p150q0000000003", "20261007090510"), 3)
        ep = t.w._episodes.episodes()[0]
        assert ep.episode_investigation_status == "down" and ep.episode_investigation_id == ""
        failed = [i for i in t.audit.investigations
                  if i.get("fingerprint") == f"episode:{ep.id}"]
        assert [i["status"] for i in failed] == ["down"]  # 1회만 시도
        failed_rows = [json.loads(x) for x in t.inv.path.read_text("utf-8").splitlines()
                       if json.loads(x)["kind"] == "episode"]
        assert [(r["status"], r["alarm_ids"][0]) for r in failed_rows] == [("failed", P_ID)]
        # (plans/153 W6) 실패한 종합 조사가 멤버의 단독 조사를 가리지 않는다
        rec = t.inv.get_by_alarm(P_ID)
        assert rec["kind"] == "alarm" and rec["investigation_id"] == "inv-1"
        # 실패한 종합 조사에는 묶지 않는다 — 종전(D-317 ⑦) 사건의 기존 단독 조사 id로 연결
        assert t.g.states[2]["episode_investigation"] == {
            "episode_id": ep.id, "investigation_id": "inv-2"}
        assert t.client.kinds() == [None, None]

    async def test_later_rejected_episode_investigation_is_not_linked(
        self, tmp_path, clock, monkeypatch
    ):
        """submit은 받았지만 조사 서비스가 뒤에 거부 — 후속 제니퍼 알람은 종전 경로(없으면 제출)."""
        async def _poll(investigation_id, gate_cfg):
            return None, "rejected", "같은 호스트 조사 진행 중", None

        monkeypatch.setattr(notifier_mod, "_poll_until_terminal", _poll)
        t = _worker(tmp_path)
        lines = _lines()
        await _feed(t.w, lines[0]["payload"], 1)
        await _feed(t.w, lines[1]["payload"], 2)
        await _drain()
        ep = t.w._episodes.episodes()[0]
        assert ep.episode_investigation_id == "inv-3"
        assert t.inv.get_by_investigation("inv-3")["status"] == "rejected"
        ep.apm_investigation_id = ""  # 기존 단독 조사가 없는 경우 — 새로 제출해야 한다
        await _feed(t.w, _later_jennifer("jennifer:p150q0000000004", "20261007090520"), 3)
        assert "episode_investigation" not in t.g.states[2]
        assert t.client.kinds() == [None, None, "episode", None]  # 사건당 1회 시도는 그대로


# ═════════════════════════════════════════════════════════════════════════════
# D. 리플레이 하네스 — 종합 조사 발주 수
# ═════════════════════════════════════════════════════════════════════════════


class TestReplayCount:
    def test_fixture_counts_one_episode_investigation(self):
        rows, _ = rn.replay(_lines(), tracker=rn.build_tracker("shadow"))
        assert [r.get("episode_investigation", False) for r in rows] == [False, True]
        assert rn.summarize(rows)["cross_source"]["episode_investigations"] == 1

    def test_off_has_no_count(self):
        rows, _ = rn.replay(_lines())
        assert "cross_source" not in rn.summarize(rows)
        assert all("episode_investigation" not in r for r in rows)
