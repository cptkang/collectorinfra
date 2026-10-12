"""plans/150 W0 — flags-off 비트 동일 기준선(판정 레코드 · 조사 페이로드) + 사건·브리핑 픽스처.

이후 Wave(W1 판정 레코드 `analysis` · W4 `meta.episode`/`meta.cross_source`)가 플래그를 더해도
**기본값(off)에서는** 아래 산출이 지금과 한 글자도 달라지면 안 된다(plans/150 §3 ①·⑥).

    ① 판정 레코드 JSONL 한 줄 — `notification_gate_node`가 실 `DecisionStore`에 쓴 레코드 전체
       (`ts` 제외 — 기록 시각이라 비결정). 설정은 실 `NoiseGateConfig` 기본값(.env·NOISE_* 차단)이라
       새 필드의 **기본값**이 off인지까지 고정한다. 분석 결과는 summary·probable_cause·
       recommended_action을 채운 실 `AlarmAnalysisResult`다 — off에서는 레코드에 실리지 않아야 한다.
    ② 조사 트리거 페이로드 — `build_trigger_payload` 출력(폴스타·제니퍼 × episode_id 유무).

`test_decision_snapshot.py`(판정 6-튜플)·`test_plan60_flags_off_regression.py`(meta 일부)와
겹치지 않게, 여기서는 **직렬화된 산출 전체**를 골든으로 박는다.

골든 재생성은 **산출을 의도적으로 바꾼 경우에만** 한다(그때는 결정 기록이 먼저다):
    python -m noise_gate.tests.test_plan150_w0_baseline --write
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.investigation_briefing import BRIEFING_CONTRACT_KEYS, render_briefing_lines
from noise_gate.domain.investigation_payload import CONTRACT_VERSION, build_trigger_payload
from noise_gate.domain.notification_policy import NotificationDecision
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.infrastructure.polestar_noise_context import _unavailable
from noise_gate.scripts import replay_noise as rn

TESTDATA = Path(__file__).resolve().parents[1] / "testdata" / "plan150"
INCIDENT = TESTDATA / "cross_source_incident.jsonl"
BRIEFING_MOCK = TESTDATA / "briefing_mock.json"
DECISION_GOLDEN = TESTDATA / "decision_records.golden.json"
PAYLOAD_GOLDEN = TESTDATA / "trigger_payloads.golden.json"

# 판정 레코드 비교에서 빼는 키 — 기록 시각(store.record가 now로 채운다).
_NONDETERMINISTIC_KEYS = frozenset({"ts"})

# 고정 사건 id — 실 추적기의 id는 naive 발생 시각의 로컬 epoch 해시라 기계 TZ마다 다르다.
EPISODE_ID = "ep-p150fixture"


# ═════════════════════════════════════════════════════════════════════════════
# 공통 입력
# ═════════════════════════════════════════════════════════════════════════════


def _incident_lines() -> list[dict[str, Any]]:
    return rn.load_jsonl(str(INCIDENT))


def _polestar_event() -> AlarmEvent:
    """크로스소스 사건의 원인 — 폴스타 CPU 경고(중요도 높음 → PAGE)."""
    return rn.event_from_payload(_incident_lines()[0]["payload"])


def _jennifer_event() -> AlarmEvent:
    """크로스소스 사건의 증상 — 270초 뒤 같은 호스트 제니퍼 SERVICE_QUEUING(strong 매칭)."""
    return rn.event_from_payload(_incident_lines()[1]["payload"])


def _analysis(event: AlarmEvent, *, error: str | None = None) -> AlarmAnalysisResult:
    """LLM 분석 결과 — 서술 3필드를 채운다(W1 플래그 off에서는 판정 레코드에 실리지 않아야 한다)."""
    return AlarmAnalysisResult(
        alarm_event=event,
        severity_label="경고",
        summary="xsapp01 CPU 사용률 98%로 임계 초과",
        probable_cause="배치 작업 또는 트래픽 급증으로 인한 CPU 포화 추정",
        recommended_action="상위 CPU 프로세스 확인 후 부하 분산 검토",
        notification_channels=["workb"],
        error=error,
    )


def _shadow_signal() -> dict[str, Any]:
    """워커 사건 추적기 신호(shadow · 결합 조건 충족) — 픽스처 사건의 실측 신호에서 id만 고정."""
    return {
        "episode_id": EPISODE_ID, "mode": "shadow", "host_key": "xsapp01",
        "rule_id": "host_resource_to_was_latency", "cause_alarm_id": "P150-CPU-1",
        "lag_seconds": 270.0, "action": "demote", "enforce": False, "missing": [], "applied": False,
    }


def _gate_config() -> SimpleNamespace:
    """실 `NoiseGateConfig` 기본값 + 게이트 on + 중요도 매핑만 명시(.env 미적재)."""
    from src.config import NoiseGateConfig

    return SimpleNamespace(noise_gate=NoiseGateConfig(
        _env_file=None,
        enable_noise_gate=True,
        importance_value_map_csv="1=낮음,2=보통,3=높음",
    ))


def _gate_cases() -> dict[str, dict[str, Any]]:
    """케이스 ID → 게이트 노드 state. ID는 골든 키라 바꾸지 않는다."""
    p_ev, j_ev = _polestar_event(), _jennifer_event()
    lines = _incident_lines()
    return {
        # 크로스소스 원인 — 매트릭스 PAGE(분석 서술 있음).
        "polestar_cpu_page": {
            "alarm_event": p_ev, "analysis_result": _analysis(p_ev), "history_stats": None,
            "noise_context": dict(lines[0]["noise_ctx"]), "error": None,
        },
        # 크로스소스 증상 — 제니퍼 수집 실패(현행 공급자) + shadow 사건 신호.
        "jennifer_queuing_shadow": {
            "alarm_event": j_ev, "analysis_result": _analysis(j_ev), "history_stats": None,
            "noise_context": _unavailable(), "error": None, "cross_source": _shadow_signal(),
        },
        # 억제 — 유지보수 중 주의 알람(condition_log 기록 경로).
        "polestar_maintenance_suppress": {
            "alarm_event": rn.event_from_payload({**lines[0]["payload"], "severity": 1,
                                                 "alarmId": "P150-MNT-1"}),
            "analysis_result": _analysis(p_ev), "history_stats": None,
            "noise_context": {**lines[0]["noise_ctx"], "maintenance": True}, "error": None,
        },
        # AI 분석 실패 — 원문 알람으로 판단(`stage_evidence.analysis` 영문 키 경로 · W1 라벨 대상).
        "polestar_analysis_failed": {
            "alarm_event": rn.event_from_payload({**lines[0]["payload"], "alarmId": "P150-ERR-1"}),
            "analysis_result": _analysis(p_ev, error="LLM 호출 타임아웃"), "history_stats": None,
            "noise_context": dict(lines[0]["noise_ctx"]), "error": None,
        },
    }


async def _record_one(tmp_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    store = DecisionStore(str(tmp_dir / "decisions.jsonl"))
    await notification_gate_node(state, {"configurable": {
        "app_config": _gate_config(), "decision_store": store,
    }})
    lines = store.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, "게이트 노드가 판정 레코드를 정확히 1줄 써야 한다"
    return json.loads(lines[0])


def _strip(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if k not in _NONDETERMINISTIC_KEYS}


def _decision(
    tier: str, reason: str, fingerprint: str, signals: dict[str, Any]
) -> NotificationDecision:
    return NotificationDecision(tier=tier, reason=reason, priority=3, signals=signals,
                                fingerprint=fingerprint, stage="matrix")


def _payload_cases() -> dict[str, dict[str, Any]]:
    """케이스 ID → build_trigger_payload 출력. ID는 골든 키라 바꾸지 않는다."""
    p_dec = _decision("page", "심각도 2 × 중요도 높음 → 즉시 통보", "fp-p150-cpu",
                      {"severity": 2, "importance": "높음", "maintenance": False})
    j_dec = _decision("page", "수집 실패 — 원문 알람으로 판단", "fp-p150-queuing",
                      {"severity": 2, "collection_failed": True})
    return {
        "polestar_no_episode": build_trigger_payload(_polestar_event(), p_dec),
        "polestar_with_episode": build_trigger_payload(
            _polestar_event(), p_dec, episode_id=EPISODE_ID
        ),
        "jennifer_no_episode": build_trigger_payload(_jennifer_event(), j_dec),
        "jennifer_with_episode": build_trigger_payload(
            _jennifer_event(), j_dec, episode_id=EPISODE_ID
        ),
    }


@pytest.fixture(autouse=True)
def _no_noise_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """프로세스 환경의 NOISE_* 를 지워 기본값 판정이 셸 환경에 흔들리지 않게 한다."""
    for key in [k for k in os.environ if k.startswith("NOISE_")]:
        monkeypatch.delenv(key, raising=False)


# ═════════════════════════════════════════════════════════════════════════════
# ① 판정 레코드 JSONL
# ═════════════════════════════════════════════════════════════════════════════


class TestDecisionRecordBaseline:
    @pytest.mark.parametrize("case", sorted(_gate_cases()))
    async def test_record_matches_golden(self, tmp_path: Path, case: str):
        golden = json.loads(DECISION_GOLDEN.read_text(encoding="utf-8"))
        rec = await _record_one(tmp_path, _gate_cases()[case])
        datetime.fromisoformat(rec["ts"])  # 기록 시각 키는 있고 ISO여야 한다(값은 비교 제외)
        assert _strip(rec) == golden[case]
        # 키 순서까지 같아야 JSONL 바이트가 같다.
        assert list(_strip(rec)) == list(golden[case])

    async def test_analysis_text_absent_by_default(self, tmp_path: Path):
        # W1(`NOISE_DECISION_ANALYSIS_TEXT_ENABLED`) 전 — 분석 서술이 있어도 레코드에 싣지 않는다.
        rec = await _record_one(tmp_path, _gate_cases()["polestar_cpu_page"])
        assert "analysis" not in rec
        assert "CPU 포화 추정" not in json.dumps(rec, ensure_ascii=False)

    def test_golden_covers_every_case(self):
        golden = json.loads(DECISION_GOLDEN.read_text(encoding="utf-8"))
        assert set(golden) == set(_gate_cases())


# ═════════════════════════════════════════════════════════════════════════════
# ② 조사 트리거 페이로드
# ═════════════════════════════════════════════════════════════════════════════

_EVENT_KEYS = {
    "dbId", "serverName", "hostname", "alarmId", "severity", "alarmName", "resourceType",
    "resourceName", "alarmTime", "conditions", "conditionLog",
}
_META_BASE = {"recurrence", "cluster", "root_resource", "source"}


class TestTriggerPayloadBaseline:
    @pytest.mark.parametrize("case", sorted(_payload_cases()))
    def test_payload_matches_golden(self, case: str):
        golden = json.loads(PAYLOAD_GOLDEN.read_text(encoding="utf-8"))
        assert _payload_cases()[case] == golden[case]

    @pytest.mark.parametrize("case", sorted(_payload_cases()))
    def test_contract_shape(self, case: str):
        payload = _payload_cases()[case]
        assert CONTRACT_VERSION == "1" and payload["contract_version"] == "1"
        assert list(payload) == ["contract_version", "event", "decision", "meta"]
        assert set(payload["event"]) == _EVENT_KEYS
        assert set(payload["decision"]) == {"tier", "reason", "fingerprint", "signals"}
        expected_meta = set(_META_BASE)
        if case.startswith("jennifer"):
            expected_meta.add("hints")
        if case.endswith("with_episode"):
            expected_meta.add("episode_id")
        # W4 가산 키(`episode`·`cross_source`)는 기본 off에서 없어야 한다.
        assert set(payload["meta"]) == expected_meta

    def test_apm_event_db_id_is_jennifer_source(self):
        # R-4 전제 — 제니퍼 트리거의 event.dbId는 폴스타 소스가 아니라 제니퍼 소스 id다.
        assert _payload_cases()["jennifer_no_episode"]["event"]["dbId"] == "jennifer_common"

    def test_golden_covers_every_case(self):
        golden = json.loads(PAYLOAD_GOLDEN.read_text(encoding="utf-8"))
        assert set(golden) == set(_payload_cases())


# ═════════════════════════════════════════════════════════════════════════════
# 픽스처 자체 검증 — 이후 Wave가 기대는 모양
# ═════════════════════════════════════════════════════════════════════════════


class TestCrossSourceIncidentFixture:
    def test_shadow_replay_links_cpu_page_to_queuing(self):
        rows, skipped = rn.replay(_incident_lines(), tracker=rn.build_tracker("shadow"))
        assert skipped == 0 and len(rows) == 2
        cause, effect = rows
        assert (cause["source"], cause["tier"], cause["stage"]) == ("polestar", "page", "matrix")
        assert effect["source"] == "jennifer"
        cs = effect["cross_source"]
        assert cs["episode_id"] == cause["cross_source"]["episode_id"]
        assert cs["episode_id"].startswith("ep-")
        assert cs["rule_id"] == "host_resource_to_was_latency"
        assert cs["cause_alarm_id"] == "P150-CPU-1"
        assert cs["lag_seconds"] == 270.0
        assert cs["missing"] == []  # 같은 존·호스트 키 strong · 창 안 · 원인 PAGE

    def test_fixed_signal_mirrors_replay(self):
        rows, _ = rn.replay(_incident_lines(), tracker=rn.build_tracker("shadow"))
        actual = dict(rows[1]["cross_source"])
        actual["episode_id"] = EPISODE_ID
        assert actual == _shadow_signal()


class TestBriefingMockFixture:
    def test_keys_equal_contract(self):
        briefing = json.loads(BRIEFING_MOCK.read_text(encoding="utf-8"))
        assert set(briefing) == BRIEFING_CONTRACT_KEYS

    def test_renders_all_elements_in_order(self):
        briefing = json.loads(BRIEFING_MOCK.read_text(encoding="utf-8"))
        labels = [label for label, _ in render_briefing_lines(briefing)]
        assert labels == ["중요도", "요약", "타임라인", "병목", "원인", "원인 가설", "권고", "한계"]


# ═════════════════════════════════════════════════════════════════════════════
# 골든 재생성 (의도적 변경 시에만)
# ═════════════════════════════════════════════════════════════════════════════


def _write_goldens() -> None:
    import tempfile

    for key in [k for k in os.environ if k.startswith("NOISE_")]:
        os.environ.pop(key)
    records: dict[str, Any] = {}
    for case, state in sorted(_gate_cases().items()):
        with tempfile.TemporaryDirectory() as tmp:
            records[case] = _strip(asyncio.run(_record_one(Path(tmp), state)))
    DECISION_GOLDEN.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    payloads = dict(sorted(_payload_cases().items()))
    PAYLOAD_GOLDEN.write_text(
        json.dumps(payloads, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"골든 갱신: {DECISION_GOLDEN} · {PAYLOAD_GOLDEN}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write_goldens()
    else:
        print(__doc__)
