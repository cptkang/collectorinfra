"""plans/198 W8 I-11 — 사건 단위 조사(같은 서버 알람 묶음 · 증상 선착 보완).

    A. 도메인 — `episode_investigation_due(cause_confirmed=…)` 기본 비트 동일 · 사건당 1회
    B. 같은 서버 묶음 — 진행 중 조사 합류(잡 미발주 · 연결 행 · 감사 `incident_joined` · 카운터) ·
       다른 호스트 · 사건 창 밖 · 종결된 조사는 별도 발주
    C. 증상 선착 — 원인 도착 시 종합 조사 1회 · 이미 있으면 미발주
    D. 비트 동일 — 플래그 off · 수동 전용(조사 트리거 off) on

하네스는 plans/153 W4 테스트의 워커·가짜 그래프·픽스처(폴스타 CPU PAGE → 270초 뒤 같은 호스트
제니퍼 큐잉)를 그대로 쓴다.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest
from prometheus_client import REGISTRY

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.application import alarm_worker as worker_mod
from noise_gate.domain.cross_source import EpisodeTracker, episode_investigation_due
from noise_gate.domain.notification_policy import TIER_PAGE
from noise_gate.tests.test_plan144_cross_source import _Clock
from noise_gate.tests.test_plan153_w4_episode_investigation import (
    _RULE,
    P_ID,
    _alarm,
    _drain,
    _feed,
    _lines,
    _worker,
)

J_ID = "jennifer:p150q0000000001"
P2_ID = "P150-MEM-1"


def _count(outcome: str) -> float:
    return REGISTRY.get_sample_value(
        "noise_gate_incident_investigations_total", {"outcome": outcome}
    ) or 0.0


def _p2(**kw: Any) -> dict:
    """같은 호스트의 다른 폴스타 알람(지문이 다르게 이름·자원을 바꾼다)."""
    p = _lines()[0]["payload"]
    return {**p, "alarmId": P2_ID, "alarmTime": "20261007090100",
            "alarmName": "메모리 사용률", "resourceName": "MEM", **kw}


def _setup(tmp_path: Path, *, incident: bool, trigger: bool = True, **kw: Any):
    """plans/153 하네스 워커에 사건 단위 플래그·조사 트리거 값을 워커·노드 양쪽에 넣는다.

    후속 모드(`followup=True`)면 노드도 submit만 한다 — 가짜 그래프엔 notifier가 없어 조사는
    저장소에 `submitted`로 남는다(「진행 중」). 종결은 `_finish`로 후속 태스크 대신 기록한다.
    """
    t = _worker(tmp_path, **kw)
    for gate in (t.w._config.noise_gate, t.g.node_cfg["configurable"]["app_config"].noise_gate):
        gate.investigation_incident_unit_enabled = incident
        gate.investigation_trigger_enabled = trigger
        gate.investigation_followup_enabled = bool(kw.get("followup"))
    return t


def _finish(t, investigation_id: str, alarm_id: str) -> None:
    """후속 태스크가 남기는 종결 행을 대신 쓴다."""
    t.inv.record(investigation_id=investigation_id, alarm_ids=[alarm_id], status="done",
                 source="followup")


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


@pytest.fixture
def held(monkeypatch) -> asyncio.Event:
    """후속 poll을 이벤트가 열릴 때까지 붙든다 — 조사가 「진행 중」인 구간을 결정적으로 만든다."""
    gate = asyncio.Event()

    async def _poll(investigation_id, gate_cfg):
        await gate.wait()
        return {"summary": f"brief {investigation_id}"}, "done", None, None

    async def _send(*_a, **_kw):
        return None

    monkeypatch.setattr(notifier_mod, "_poll_until_terminal", _poll)
    monkeypatch.setattr(notifier_mod, "_send_workb_followup", _send)
    return gate


async def _release(gate: asyncio.Event) -> None:
    gate.set()
    await _drain()


def _audit_statuses(t) -> list[tuple[str, Any]]:
    return [(i["status"], i["investigation_id"]) for i in t.audit.investigations]


def _store_rows(t) -> list[dict]:
    if not t.inv.path.exists():
        return []
    rows = [json.loads(x) for x in t.inv.path.read_text("utf-8").splitlines()]
    return [{k: v for k, v in r.items() if k != "ts"} for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# A. 도메인
# ═════════════════════════════════════════════════════════════════════════════


class TestDueSymptomFirst:
    def _symptom_first_ep(self):
        tracker = EpisodeTracker([_RULE], mode="shadow", idle_seconds=900)
        from noise_gate.domain.cross_source import SOURCE_JENNIFER, SOURCE_POLESTAR

        sig = tracker.observe(_alarm("J", SOURCE_JENNIFER, 1100.0), 1100.0)
        tracker.record_tier(sig["episode_id"], "J", TIER_PAGE)
        cause = tracker.observe(_alarm("P", SOURCE_POLESTAR, 1000.0), 1110.0)
        return tracker.get(sig["episode_id"]), cause

    def test_symptom_first_has_no_link_but_related_effects(self):
        ep, cause = self._symptom_first_ep()
        assert ep.links == [] and cause["related_effects"] == ["J"]
        assert episode_investigation_due(ep) is False  # 기본(종전) — 나가지 않는다
        assert episode_investigation_due(ep, cause_confirmed=True) is True

    def test_attempted_is_not_due_even_if_confirmed(self):
        ep, _ = self._symptom_first_ep()
        ep.episode_investigation_status = "submitted"
        assert episode_investigation_due(ep, cause_confirmed=True) is False


# ═════════════════════════════════════════════════════════════════════════════
# B. 같은 서버 묶음
# ═════════════════════════════════════════════════════════════════════════════


class TestSameServerJoin:
    async def test_in_progress_joins_without_new_job(self, tmp_path, clock, held):
        t = _setup(tmp_path, incident=True, followup=True, episode_ctx=False)
        before = _count("joined")
        await _feed(t.w, _lines()[0]["payload"], 1)
        await _feed(t.w, _p2(), 2)
        # 두 번째 알람은 잡을 내지 않는다 — 진행 중인 inv-1에 합류
        assert len(t.client.payloads) == 1
        ep = t.w._episodes.episodes()[0]
        assert ep.incident_investigation_id == "inv-1"
        assert t.g.states[1]["episode_investigation"] == {
            "episode_id": ep.id, "investigation_id": "inv-1", "joined": True}
        assert ("incident_joined", "inv-1") in _audit_statuses(t)
        assert _count("joined") - before == 1
        _finish(t, "inv-1", P_ID)
        rec = t.inv.get_by_investigation("inv-1")
        assert rec["status"] == "done"  # 연결 행이 종결 상태를 덮지 않는다
        assert rec["alarm_ids"] == [P_ID, P2_ID]
        assert t.inv.get_by_alarm(P2_ID)["investigation_id"] == "inv-1"

    async def test_other_host_submits_separately(self, tmp_path, clock, held):
        t = _setup(tmp_path, incident=True, followup=True, episode_ctx=False)
        await _feed(t.w, _lines()[0]["payload"], 1)
        await _feed(t.w, _p2(serverName="xsapp02", hostname="xsapp02"), 2)
        assert len(t.client.payloads) == 2
        assert "incident_joined" not in {s for s, _ in _audit_statuses(t)}
        await _release(held)

    async def test_outside_episode_window_submits_separately(self, tmp_path, clock, held):
        t = _setup(tmp_path, incident=True, followup=True, episode_ctx=False)
        await _feed(t.w, _lines()[0]["payload"], 1)
        clock.t += 901  # 사건 창(episode_idle_seconds=900) 밖 — 사건이 닫히고 새 사건
        await _feed(t.w, _p2(), 2)
        assert len(t.client.payloads) == 2
        assert "incident_joined" not in {s for s, _ in _audit_statuses(t)}
        await _release(held)

    async def test_join_after_dispatch_gates_is_not_counted(self, tmp_path, clock, held):
        """(교정) 발주 게이트에 걸릴 알람은 합류로 세지 않는다 — 호스트 미해소 APM 알람."""
        t = _setup(tmp_path, incident=True, followup=True, episode_ctx=False)
        await _feed(t.w, _lines()[0]["payload"], 1)
        before = _count("joined")
        first = t.g.states[0]
        ev = dataclasses.replace(first["alarm_event"], alarm_id="APM-NOHOST",
                                 resource_type="apm.Instance", hostname="")
        t.g.force_tier = TIER_PAGE
        await t.g.ainvoke({**first, "alarm_event": ev, "episode_investigation": {
            "episode_id": "ep", "investigation_id": "inv-1", "joined": True}})
        assert _count("joined") == before
        assert ("target_unresolved", None) in _audit_statuses(t)
        assert "incident_joined" not in {s for s, _ in _audit_statuses(t)}
        assert len(t.client.payloads) == 1
        await _release(held)

    async def test_finished_investigation_is_not_joined(self, tmp_path, clock, held):
        t = _setup(tmp_path, incident=True, followup=True, episode_ctx=False)
        await _feed(t.w, _lines()[0]["payload"], 1)
        _finish(t, "inv-1", P_ID)
        await _feed(t.w, _p2(), 2)
        assert len(t.client.payloads) == 2
        # 새로 낸 조사가 다음 합류 기준이 된다
        assert t.w._episodes.episodes()[0].incident_investigation_id == "inv-2"


# ═════════════════════════════════════════════════════════════════════════════
# C. 증상 선착
# ═════════════════════════════════════════════════════════════════════════════


class TestSymptomFirst:
    async def _symptom_first(self, tmp_path, *, incident: bool):
        t = _setup(tmp_path, incident=incident, force_tier=TIER_PAGE)
        lines = _lines()
        await _feed(t.w, lines[1]["payload"], 1)  # 증상(제니퍼) 먼저
        await _feed(t.w, lines[0]["payload"], 2)  # 원인(폴스타) 나중
        return t

    async def test_episode_investigation_once_on_cause_arrival(self, tmp_path, clock, held):
        before = _count("symptom_first")
        t = await self._symptom_first(tmp_path, incident=True)
        # 인라인 모드 — 제니퍼 단독 조사는 이미 끝나 합류 없이 폴스타 단독, 그 뒤 종합 1회
        assert t.client.kinds() == [None, None, "episode"]
        ep = t.w._episodes.episodes()[0]
        assert ep.links == [] and ep.episode_investigation_id == "inv-3"
        episode_payload = t.client.payloads[2]
        assert episode_payload["event"]["alarmId"] == P_ID  # 대표 = 원인
        assert {m["alarm_id"] for m in episode_payload["meta"]["episode"]["members"]} == {
            P_ID, J_ID}
        assert _count("symptom_first") - before == 1
        # 이미 있으면 미발주 — 같은 사건의 원인 알람이 더 와도 종합 조사는 1회
        await _feed(t.w, _p2(alarmName="CPU 사용률(코어)", resourceName="CPU"), 3)
        assert t.client.kinds().count("episode") == 1
        assert _count("symptom_first") - before == 1
        await _release(held)

    async def test_join_then_symptom_first_is_not_counted_as_saved(self, tmp_path, clock, held):
        """(교정) 원인 알람이 증상의 진행 중 단독 조사에 합류해도, 같은 처리에서 종합 조사가 나가면
        순 절감 0이라 `joined`로 세지 않는다(`symptom_first`만 1)."""
        before = (_count("joined"), _count("symptom_first"))
        t = _setup(tmp_path, incident=True, force_tier=TIER_PAGE, followup=True)
        lines = _lines()
        await _feed(t.w, lines[1]["payload"], 1)  # 증상(제니퍼) 먼저 — 조사 진행 중
        await _feed(t.w, lines[0]["payload"], 2)  # 원인(폴스타) — 합류 + 종합 조사
        joined = t.g.states[1]["episode_investigation"]
        assert joined["joined"] is True and joined["count"] is False
        assert ("incident_joined", "inv-1") in _audit_statuses(t)
        assert t.client.kinds().count("episode") == 1
        assert (_count("joined"), _count("symptom_first")) == (before[0], before[1] + 1)
        await _release(held)

    async def test_flag_off_keeps_missing_episode_investigation(self, tmp_path, clock, held):
        t = await self._symptom_first(tmp_path, incident=False)
        assert t.client.kinds() == [None, None]  # 종전 — 증상 선착이면 종합 조사가 없다
        assert t.w._episodes.episodes()[0].episode_investigation_status == ""
        await _release(held)


# ═════════════════════════════════════════════════════════════════════════════
# D. 비트 동일
# ═════════════════════════════════════════════════════════════════════════════


async def _run_scenario(tmp_path: Path, *, incident: bool, trigger: bool, held_gate):
    t = _setup(tmp_path, incident=incident, trigger=trigger, followup=True)
    lines = _lines()
    await _feed(t.w, lines[0]["payload"], 1)
    await _feed(t.w, _p2(), 2)
    await _feed(t.w, lines[1]["payload"], 3)
    await _release(held_gate)
    return t


def _observable(t) -> dict[str, Any]:
    return {
        "payloads": t.client.payloads,
        "audit": t.audit.investigations,
        "store": _store_rows(t),
        "states": [sorted(s) for s in t.g.states],
        "episode_investigation": [s.get("episode_investigation") for s in t.g.states],
    }


class TestBitIdentical:
    async def test_flag_off_matches_unset_flag(self, tmp_path, clock, held):
        before = _count("joined")
        off = await _run_scenario(tmp_path / "off", incident=False, trigger=True,
                                  held_gate=held)
        # 플래그 없음(종전 설정 객체) — 같은 시나리오
        base = _worker(tmp_path / "base", followup=True)
        base.g.node_cfg["configurable"]["app_config"].noise_gate.investigation_followup_enabled = (
            True
        )
        lines = _lines()
        await _feed(base.w, lines[0]["payload"], 1)
        await _feed(base.w, _p2(), 2)
        await _feed(base.w, lines[1]["payload"], 3)
        await _drain()
        assert _observable(off) == _observable(base)
        # 종전대로 같은 서버 두 폴스타 알람이 각자 발주 · 합류 없음
        assert [p["event"]["alarmId"] for p in off.client.payloads][:2] == [P_ID, P2_ID]
        assert off.w._episodes.episodes()[0].incident_investigation_id == ""
        assert _count("joined") == before

    async def test_manual_only_is_bit_identical(self, tmp_path, clock, held):
        """수동 전용(D-347 — 조사 트리거 off)에선 플래그 on이어도 off와 같다(자동 발주 0)."""
        before = (_count("joined"), _count("symptom_first"))
        on = await _run_scenario(tmp_path / "on", incident=True, trigger=False, held_gate=held)
        off = await _run_scenario(tmp_path / "off", incident=False, trigger=False,
                                  held_gate=held)
        assert _observable(on) == _observable(off)
        assert on.client.payloads == []
        assert (_count("joined"), _count("symptom_first")) == before
