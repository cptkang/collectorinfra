"""plans/153 검증(verifier) — 조사 페이로드 `meta.episode` 생산자 고정 + 워커 메모리 상한.

1. 실 워커(W6 종단 하네스 재사용)에 `cross_source_incident.jsonl`을 넣어 조사 서비스 대역에 제출된
   페이로드 3건이 `sre_agent/tests/fixtures/plan153/noise_gate_episode_payloads.json`과 같은지 본다.
   수신 측(sre_agent)은 같은 JSON 파일을 `sre_agent/tests/test_plan153_verify_roundtrip.py`에서
   해석한다 — 두 패키지 import를 섞지 않고 파일 하나로 계약을 맞물린다(D-118).
   `occurred_at`(epoch 초 · 생산 머신 로컬 시각 규약)만 시간대 중립으로 따로 비교한다.
2. `_episode_ctx`(종합 조사 대표 후보) 상한 `_EPISODE_CTX_MAX` — 넘치면 닫힌 사건 몫부터 버린다.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.test_api.test_plan153_w6_cross_source_e2e import (  # noqa: F401 — 픽스처 재사용
    _bind_poll,
    _run_incident,
    _worker,
    clock,
    workb,
)

from noise_gate.application import alarm_worker as worker_mod

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "sre_agent" / "tests" / "fixtures" / "plan153" / "noise_gate_episode_payloads.json"
_KEYS = ("polestar_cpu_single", "jennifer_queuing_individual", "episode_composite")

# 픽스처 alarmTime(로컬 naive) — 생산자는 이 시각을 로컬 시각으로 epoch 변환한다.
_CPU_AT = datetime(2026, 10, 7, 9, 0, 0).timestamp()
_QUEUING_AT = datetime(2026, 10, 7, 9, 4, 30).timestamp()


def _strip_occurred(payload: dict[str, Any]) -> tuple[dict[str, Any], list[float]]:
    out = copy.deepcopy(payload)
    times: list[float] = []
    for m in (out.get("meta", {}).get("episode") or {}).get("members") or []:
        times.append(m.pop("occurred_at"))
    return out, times


@pytest.mark.usefixtures("clock", "workb")
class TestProducerMatchesSharedFixture:
    async def test_real_payloads_equal_fixture(self, tmp_path, monkeypatch):
        t = _worker(tmp_path)
        _bind_poll(monkeypatch, t.service)
        await _run_incident(t)
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

        assert len(t.service.payloads) == 3
        for key, produced in zip(_KEYS, t.service.payloads, strict=True):
            got, got_times = _strip_occurred(json.loads(json.dumps(produced, default=str)))
            want, _ = _strip_occurred(fixture[key])
            assert got == want, key
            # occurred_at — epoch 초(float) · 최근 순(제니퍼 → 폴스타)
            assert all(isinstance(x, float) for x in got_times)
            assert got_times == sorted(got_times, reverse=True)
        _, comp_times = _strip_occurred(t.service.payloads[2])
        assert comp_times == [_QUEUING_AT, _CPU_AT]

    async def test_composite_contract_shape(self, tmp_path, monkeypatch):
        """수신 측이 기대하는 키·타입 — 링크 끝점이 멤버에 있고 규칙 id가 링크와 맞는다."""
        t = _worker(tmp_path)
        _bind_poll(monkeypatch, t.service)
        await _run_incident(t)
        comp = t.service.payloads[2]
        assert comp["contract_version"] == "1"
        meta = comp["meta"]
        assert meta["investigation_kind"] == "episode"
        assert comp["decision"]["fingerprint"] == "episode:" + meta["episode"]["episode_id"]
        assert meta["episode_id"] == meta["episode"]["episode_id"]
        ep = meta["episode"]
        assert set(ep) == {"episode_id", "zone", "host_key", "members", "links", "rules",
                           "infra_db_ids"}
        member_keys = {"alarm_id", "source", "db_id", "alarm_name", "kind", "was_kinds", "level",
                       "severity", "occurred_at", "tier", "resolved"}
        assert all(set(m) == member_keys for m in ep["members"])
        ids = {m["alarm_id"] for m in ep["members"]}
        for lk in ep["links"]:
            assert set(lk) == {"rule_id", "cause_alarm_id", "effect_alarm_id", "lag_seconds",
                               "action", "missing"}
            assert lk["cause_alarm_id"] in ids and lk["effect_alarm_id"] in ids
        assert {r["id"] for r in ep["rules"]} == {lk["rule_id"] for lk in ep["links"]}
        assert ep["infra_db_ids"] == ["polestar_cm_gp"]


class TestEpisodeCtxBound:
    def _worker(self, open_ids: set[str]) -> worker_mod.AlarmWorker:
        w = object.__new__(worker_mod.AlarmWorker)
        w._episode_ctx = {}
        w._episodes = SimpleNamespace(get=lambda eid: object() if eid in open_ids else None)
        return w

    def _result(self) -> dict[str, Any]:
        return {"notification_decision": object(), "analysis_result": object()}

    def test_overflow_evicts_closed_episode_first_then_oldest(self, monkeypatch):
        monkeypatch.setattr(worker_mod, "_EPISODE_CTX_MAX", 4)
        w = self._worker(open_ids={"ep-open"})
        for i in range(2):
            ev = SimpleNamespace(alarm_id=f"closed-{i}")
            w._remember_episode_ctx({"episode_id": "ep-closed"}, ev, self._result(), float(i))
        for i in range(2):
            ev = SimpleNamespace(alarm_id=f"open-{i}")
            w._remember_episode_ctx({"episode_id": "ep-open"}, ev, self._result(), 10.0 + i)
        assert len(w._episode_ctx) == 4
        ev = SimpleNamespace(alarm_id="open-2")
        w._remember_episode_ctx({"episode_id": "ep-open"}, ev, self._result(), 20.0)
        # 닫힌 사건 몫 2건이 먼저 빠진다(가장 오래된 열린 사건 몫은 남는다)
        assert sorted(w._episode_ctx) == ["open-0", "open-1", "open-2"]

    def test_never_exceeds_cap_when_all_open(self, monkeypatch):
        monkeypatch.setattr(worker_mod, "_EPISODE_CTX_MAX", 3)
        w = self._worker(open_ids={"ep"})
        for i in range(10):
            ev = SimpleNamespace(alarm_id=f"a{i}")
            w._remember_episode_ctx({"episode_id": "ep"}, ev, self._result(), float(i))
            assert len(w._episode_ctx) <= 3
        assert sorted(w._episode_ctx) == ["a7", "a8", "a9"]

    def test_missing_analysis_or_decision_is_not_kept(self):
        w = self._worker(open_ids={"ep"})
        ev = SimpleNamespace(alarm_id="x")
        w._remember_episode_ctx({"episode_id": "ep"}, ev, {"notification_decision": None}, 0.0)
        w._remember_episode_ctx({"episode_id": "ep"}, ev, None, 0.0)
        assert w._episode_ctx == {}
