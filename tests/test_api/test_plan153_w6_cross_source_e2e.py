"""plans/153 W6 — 크로스소스 종합 조사 가짜 LLM 종단.

폴스타 CPU → 제니퍼 큐잉 → 종합 조사 → 웹 조회.

픽스처 `noise_gate/testdata/plan153/cross_source_incident.jsonl`(폴스타 CPU PAGE → 270초 뒤 같은
호스트 제니퍼 큐잉)을 실제 워커(`AlarmWorker._process`)에 넣는다. 그래프는 분석(LLM)만 가짜로
채우고 **실제** 판정 노드(`notification_gate_node` — 판정 감사 기록 포함)·조사 트리거 노드·
후속 poll 경로(`_spawn_investigation_followup` · 종합 조사 후속)를 부른다. 조사 서비스는 띄우지
않는다 — 제출을 받아 id를 매기고, poll에 종합 조사면 `cross_source` 블록을 담은 브리핑으로 done을
돌려주는 대역이다(sre_agent import 0 · D-118).

    A. 플래그 on(게이트·트리거·제니퍼 정책·cross_source shadow·스냅샷·후속) — 종합 조사 1회 ·
       저장소 `alarm_ids` 두 멤버 · by-alarm API · 관제 `decisions/{id}`의 `investigation` 블록 ·
       workb 후속 표지
    B. shadow는 판정 티어를 바꾸지 않는다(cross_source off 실행과 티어 동일)
    C. 플래그 전부 off — 조사 제출 0 · 조사 저장소 파일 0
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import noise_gate.application.nodes.alarm_notifier as notifier_mod
import noise_gate.infrastructure.sre_agent_client as sre_client_mod
from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.investigation_trigger import investigation_trigger_node
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.application.server_identity import is_apm_source
from noise_gate.domain.alarm import AlarmAnalysisResult
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.infrastructure.investigation_store import InvestigationStore
from noise_gate.scripts import replay_noise as rn
from noise_gate.tests.test_plan144_cross_source import SHIPPED_RULES, _Clock, _Redis
from tests.test_api.test_noise_dashboard_api import _make_client, _make_user_client
from tests.test_api.test_plan153_w2_investigation_api import ADMIN, _alarm_client, _config

TESTDATA = Path(__file__).resolve().parents[2] / "noise_gate" / "testdata" / "plan153"
INCIDENT = TESTDATA / "cross_source_incident.jsonl"
BRIEFING_MOCK = TESTDATA / "briefing_mock.json"

P_ID = "P150-CPU-1"
J_ID = "jennifer:p150q0000000001"
EPISODE_NOTE_HEAD = "같은 사건(폴스타×제니퍼) 종합 조사"

# 제니퍼 알람의 노이즈 컨텍스트 — 실제 그래프에서는 enricher가 제니퍼 정책으로 채운다.
_APM_CTX = {"importance_id": "3", "maintenance": None, "noti_policy": "notify",
            "source": "apm_policy"}


def _lines() -> list[dict[str, Any]]:
    return rn.load_jsonl(str(INCIDENT))


def _mock_briefing() -> dict[str, Any]:
    return json.loads(BRIEFING_MOCK.read_text(encoding="utf-8"))


class _SreService:
    """조사 서비스 대역 — submit은 id를 매기고 poll은 done. 종합 조사면 `cross_source`를 더한다.

    `cross_source` 블록은 W5 sre_agent `_cross_source_block`과 같은 모양을 제출 스냅샷에서 만든다
    (도구 2회 호출 · 양쪽 인용 뒷받침).
    """

    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.episodes: dict[str, dict[str, Any]] = {}

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        inv = f"inv-{len(self.payloads)}"
        if payload["meta"].get("investigation_kind") == "episode":
            self.episodes[inv] = payload["meta"]["episode"]
        return {"investigation_id": inv, "status": "accepted"}

    async def poll(self, inv: str) -> dict[str, Any]:
        briefing = _mock_briefing()
        snap = self.episodes.get(inv)
        if snap is not None:
            names = {m["alarm_id"]: m["alarm_name"] for m in snap["members"]}
            sources: dict[str, int] = {}
            for m in snap["members"]:
                sources[m["source"]] = sources.get(m["source"], 0) + 1
            briefing["cross_source"] = {
                "episode_id": snap["episode_id"], "host_key": snap["host_key"],
                "sources": sources, "apm_tools_connected": True, "apm_tools_called": 2,
                "links": [
                    {"rule_id": lk["rule_id"], "cause": names[lk["cause_alarm_id"]],
                     "effect": names[lk["effect_alarm_id"]], "lag_seconds": lk["lag_seconds"],
                     "supported_by_tools": True}
                    for lk in snap["links"]
                ],
            }
        return {"status": "done", "briefing": briefing, "verdict": None}

    def kinds(self) -> list[str | None]:
        return [p["meta"].get("investigation_kind") for p in self.payloads]


class _Graph:
    """분석(LLM)만 가짜 — 실제 판정 노드·조사 트리거 노드·후속 스폰(notifier 경로)을 부른다."""

    def __init__(self, node_cfg: dict[str, Any]) -> None:
        self.node_cfg = node_cfg

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        ev = state["alarm_event"]
        ctx = _APM_CTX if is_apm_source(ev) else dict(_lines()[0]["noise_ctx"])
        analysis = AlarmAnalysisResult(
            alarm_event=ev, severity_label="경고", summary="가짜 분석", probable_cause="c",
            recommended_action="a", notification_channels=["workb"],
            notifications_sent={"workb": True},
        )
        out = {**state, "analysis_result": analysis, "noise_context": ctx}
        out.update(await notification_gate_node(out, self.node_cfg))
        out.update(await investigation_trigger_node(out, self.node_cfg))
        gate_cfg = self.node_cfg["configurable"]["app_config"].noise_gate
        notifier_mod._spawn_investigation_followup(
            out.get("investigation_pending"), analysis,
            self.node_cfg["configurable"]["app_config"], gate_cfg, self.node_cfg,
        )
        return out


def _gate(tmp_path: Path, *, on: bool, mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        enable_noise_gate=True, repeat_interval_seconds=14400, suppress_max_severity=2,
        dedup_severity_rise_bypass=False,
        importance_value_map={"1": "낮음", "2": "보통", "3": "높음"},
        resolved_to_dashboard=False, self_heal_window_seconds=300,
        cross_source_mode=mode, cross_source_rules_path=str(SHIPPED_RULES),
        episode_idle_seconds=900, app_impact_enabled=False,
        app_impact_late_promotion_enabled=False, apm_noise_policy_enabled=on,
        investigation_trigger_enabled=on, investigation_trigger_min_tier="PAGE",
        investigation_episode_context_enabled=on, investigation_followup_enabled=on,
        investigation_followup_timeout_seconds=5.0, investigation_followup_max_inflight=8,
        investigation_poll_interval_seconds=0.0, investigation_total_timeout_seconds=5.0,
        fault_escalation_enabled=False,
        investigation_store_path=str(tmp_path / "inv.jsonl"),
    )


def _worker(tmp_path: Path, *, on: bool = True, mode: str = "shadow"):
    service = _SreService()
    decisions = DecisionStore(str(tmp_path / "decisions.jsonl"))
    inv_store = InvestigationStore(str(tmp_path / "inv.jsonl"))
    cfg = SimpleNamespace(
        noise_gate=_gate(tmp_path, on=on, mode=mode),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
        host_authz=SimpleNamespace(mode="admin_only"),
        composite=SimpleNamespace(prior_targets_enabled=False, availability_precheck_enabled=False),
        workb=SimpleNamespace(),
    )
    node_cfg = {"configurable": {
        "app_config": cfg, "sre_agent_client": service, "decision_store": decisions,
        "investigation_store": inv_store,
    }}
    w = AlarmWorker(cfg)
    w._graph = _Graph(node_cfg)
    w._episodes = w._build_episode_tracker()
    w._decision_store = decisions
    w._investigation_store = inv_store
    w._sre_agent_client = service
    return SimpleNamespace(w=w, service=service, decisions=decisions, inv=inv_store)


async def _run_incident(t: SimpleNamespace) -> None:
    for n, line in enumerate(_lines(), start=1):
        fields = {b"data": json.dumps(line["payload"], ensure_ascii=False).encode("utf-8")}
        await t.w._process(_Redis(), "alarm:raw", "g", f"{n}-1".encode(), fields, {})
    while notifier_mod._FOLLOWUP_TASKS:
        await asyncio.gather(*list(notifier_mod._FOLLOWUP_TASKS))


def _tiers(tmp_path: Path) -> dict[str, str]:
    path = tmp_path / "decisions.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    return {r["alarm_id"]: r["tier"] for r in rows if "type" not in r}


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


@pytest.fixture
def workb(monkeypatch) -> list[dict[str, Any]]:
    """workb 후속 발송을 가로챈다(실 호출 없음) — 종합 조사 후속만 `episode_note`를 받는다."""
    sent: list[dict[str, Any]] = []

    async def _send(workb_cfg, result, briefing, escalation, l3=None, **kw):  # noqa: ANN001
        sent.append({"alarm_id": result.alarm_event.alarm_id, "briefing": briefing, **kw})

    monkeypatch.setattr(notifier_mod, "_send_workb_followup", _send)
    return sent


def _bind_poll(monkeypatch, service: _SreService) -> None:
    """후속 poll(자체 클라이언트 팩토리)이 같은 조사 서비스 대역을 쓰게 한다."""
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", lambda gate_cfg: service)


class TestCrossSourceEndToEnd:
    async def test_episode_investigation_reaches_web(self, tmp_path, clock, workb, monkeypatch):
        t = _worker(tmp_path)
        _bind_poll(monkeypatch, t.service)
        await _run_incident(t)

        # 폴스타 단독 · 제니퍼 단독 · 종합 1회
        assert t.service.kinds() == [None, None, "episode"]
        ep = t.w._episodes.episodes()[0]
        assert ep.episode_investigation_id == "inv-3"

        # 조사 저장소 — 종합 조사 1건에 두 멤버(대표 = 원인 폴스타가 첫 원소)
        rec = t.inv.get_by_investigation("inv-3")
        assert rec["kind"] == "episode" and rec["status"] == "done"
        assert rec["alarm_ids"] == [P_ID, J_ID]
        assert rec["cross_source"]["links"][0]["rule_id"] == "host_resource_to_was_latency"
        # 두 멤버 모두 종합 조사를 본다(자기 단독 조사도 done이지만 종결된 종합 조사가 우선)
        assert t.inv.get_by_investigation("inv-2")["status"] == "done"
        assert t.inv.get_by_alarm(J_ID)["investigation_id"] == "inv-3"
        assert t.inv.get_by_alarm(P_ID)["investigation_id"] == "inv-3"

        # workb 후속 — 종합 조사만 표지(대표 알람 스레드) · 단독 조사 후속은 표지 없음
        episode_sent = [s for s in workb if "episode_note" in s]
        assert [s["alarm_id"] for s in episode_sent] == [P_ID]
        assert episode_sent[0]["episode_note"].startswith(EPISODE_NOTE_HEAD)
        singles = sorted(s["alarm_id"] for s in workb if "episode_note" not in s)
        assert singles == sorted([J_ID, P_ID])

        # API — 증상(제니퍼) 알람으로 조회해도 종합 조사 브리핑·「크로스소스」 줄·cross_source
        config = _config(tmp_path)
        body = _alarm_client(config, ADMIN).get(
            f"/api/v1/alarm/investigations/by-alarm/{J_ID}"
        ).json()
        assert body["investigation_id"] == "inv-3" and body["kind"] == "episode"
        assert body["status"] == "done" and body["episode_id"] == ep.id
        cs_lines = [x["text"] for x in body["briefing_lines"] if x["label"] == "크로스소스"]
        assert cs_lines == [
            "사건 호스트 xsapp01 · 폴스타 1건 · 제니퍼 1건\n"
            "APM 도구 연결 · 호출 2회\n"
            "규칙 host_resource_to_was_latency: CPU 사용률 → SERVICE_QUEUING (270초) · "
            "도구 뒷받침: 양쪽"
        ]
        assert all(x["label"] != "cross_source" for x in body["briefing_lines"])
        assert body["cross_source"]["sources"] == {"jennifer": 1, "polestar": 1}
        assert body["briefing"]["cross_source"] == body["cross_source"]

        # 관제 결정 추적 — 운영자·사용자 경로 모두 `investigation` 블록에 같은 종합 조사
        op = _make_client(config).get(f"/api/v1/admin/noise/decisions/{J_ID}").json()
        user = _make_user_client(config).get(f"/api/v1/noise/decisions/{J_ID}").json()
        for decision in (op["decision"], user["decision"]):
            inv = decision["investigation"]
            assert inv["investigation_id"] == "inv-3" and inv["kind"] == "episode"
            assert any(x["label"] == "크로스소스" for x in inv["briefing_lines"])
            assert inv["cross_source"]["episode_id"] == ep.id
        assert "briefing" not in user["decision"]["investigation"]

    async def test_shadow_keeps_tiers(self, tmp_path, clock, workb, monkeypatch):
        on_dir, off_dir = tmp_path / "shadow", tmp_path / "off"
        tiers: dict[str, dict[str, str]] = {}
        for mode, base in (("shadow", on_dir), ("off", off_dir)):
            t = _worker(base, mode=mode)
            _bind_poll(monkeypatch, t.service)
            await _run_incident(t)
            tiers[mode] = _tiers(base)
        assert tiers["shadow"] == tiers["off"] == {P_ID: "page", J_ID: "page"}
        # shadow 결합 판정 근거는 감사에 남되 적용(enforce)되지 않았다
        rows = [json.loads(x) for x in (on_dir / "decisions.jsonl").read_text("utf-8").splitlines()]
        j_row = next(r for r in rows if r.get("alarm_id") == J_ID and "type" not in r)
        cs = j_row["stage_evidence"]["cross_source"]
        assert cs["mode"] == "shadow" and cs["enforce"] is False and cs["applied"] is False

    async def test_flags_off_no_episode_investigation_no_store(
        self, tmp_path, clock, workb, monkeypatch
    ):
        t = _worker(tmp_path, on=False, mode="off")
        _bind_poll(monkeypatch, t.service)
        await _run_incident(t)
        assert t.service.payloads == []
        assert workb == []
        assert not (tmp_path / "inv.jsonl").exists()
        assert _tiers(tmp_path) == {P_ID: "page", J_ID: "page"}
