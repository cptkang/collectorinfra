"""plans/150 W0 · R-4 — APM(제니퍼) 트리거 잡의 증거 사전수집 소스 결함 확정.

plans/150 §2.2 추론: 사전수집(`evidence_prefetch.scope_from_job`)이 `event.dbId`를 그대로
폴스타 도구의 `source`로 넘긴다. 제니퍼 트리거면 dbId가 `jennifer`·`jennifer_<source_id>`
(apm_gateway `alarm_db_id`)라 mcp_server `_resolve_engine`이 「알 수 없는 소스」 ValueError →
도구가 `{error}`를 돌려주고, 사전수집 5축(알람 1 + 지표 4)이 전부 「결손」으로 브리핑 한계에 실린다.

이 결함은 신규 플래그(`CROSS_SOURCE_INVESTIGATION_ENABLED`)와 **무관**하다 — 기존 플래그
`EVIDENCE_CORRELATION_ENABLED`(기본 off)만 켜면 현행 경로에서 재현된다
(아래 설정에 신규 필드 없음).

- W0에서는 `TestR4DefectReproduction`이 결함(제니퍼 dbId → 폴스타 source · 5축 전부
  결손)을 고정했고, W5 교정(`evidence_prefetch.scope_from_job` — `is_apm_trigger`면
  제니퍼 dbId를 쓰지 않는다)과 함께 지웠다.
  그 클래스의 대조군(폴스타 트리거 불변)은 아래로 옮겼다.
- `TestR4ExpectedBehavior`: plans/150 §4.6 기대 동작(W5에서 xfail 해제).
- mcp_server 쪽 「알 수 없는 소스 → {error}」는 경계(D-118 · 양방향 import 0) 때문에 여기서
  import하지 않는다 — `mcp_server/tests/test_plan150_r4_unknown_source.py`가 확인한다. 여기서는
  그 응답 모양을 가짜 배치 호출기로 재현한다.

픽스처: `tests/fixtures/plan150/apm_trigger_payloads.json`(noise_gate `build_trigger_payload`
실 출력 사본 — noise_gate 파일을 읽지 않는다).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sre_agent.application import evidence_prefetch
from sre_agent.application.investigation_jobs import JobStore
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plan150"
PAYLOADS = json.loads((FIXTURES / "apm_trigger_payloads.json").read_text(encoding="utf-8"))

# mcp_server 등록 소스(폴스타 계열만 — 제니퍼 소스는 mcp_server 설정에 존재하지 않는다).
ACTIVE_SOURCES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
INFRA_SOURCE_UNKNOWN_NOTE = "인프라 소스 미상 — 폴스타 사전수집 생략"


def _settings(**extra: object) -> AgentSettings:
    """현행 사전수집 경로를 켠 설정(.env 미적재)."""
    return AgentSettings(
        _env_file=None, model="test/model", api_key=None, gemini_api_key=None,
        service_bearer_token=None, evidence_correlation_enabled=True, **extra,
    )


class _FakeMcpBatch:
    """mcp_server 폴스타 도구의 응답 모양을 흉내 내는 배치 호출기(호출 인자를 기록한다)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, calls):  # noqa: ANN001, ANN204 — BatchCaller
        self.calls.extend(calls)
        out = []
        for _name, args in calls:
            source = str(args.get("source"))
            if source not in ACTIVE_SOURCES:
                # mcp_server/polestar_tools.py `_resolve_engine` ValueError → `_err(str(e))`.
                error = f"알 수 없는 소스: {source} (활성: {ACTIVE_SOURCES})"
                out.append(json.dumps({"error": error}, ensure_ascii=False))
            else:
                out.append(json.dumps({"rows": [], "row_count": 0, "window": {}}))
        return out


def _job(payload: dict, tmp_path: Path):  # noqa: ANN202 — InvestigationJob
    """실 JobStore로 제출해 기준시각(사건 좌표계)이 채워진 잡을 받는다(executor는 잡만 포획)."""
    captured: list = []
    store = JobStore(_settings(), executor=captured.append, audit_path=tmp_path / "audit.jsonl")
    assert store.submit(payload)["status"] == "accepted"
    (job,) = captured
    assert job.reference_time  # alarmTime → ISO
    return job


def _apm_payload(db_id: str, *, episode: dict | None = None) -> dict:
    payload = copy.deepcopy(PAYLOADS["jennifer_common_no_episode"])
    payload["event"]["dbId"] = db_id
    payload["decision"]["fingerprint"] = f"fp-r4-{db_id}"
    if episode is not None:
        payload["meta"]["episode"] = episode
    return payload


def _prefetch(monkeypatch: pytest.MonkeyPatch, settings: AgentSettings) -> tuple:
    fake = _FakeMcpBatch()
    monkeypatch.setattr(mcp_service, "make_batch_caller", lambda _s: fake)
    fn = mcp_service._default_prefetch_fn(settings)
    assert fn is not None
    return fn, fake


class TestFixtureShape:
    def test_payload_is_apm_trigger(self):
        payload = PAYLOADS["jennifer_common_with_episode"]
        assert payload["contract_version"] == "1"
        assert payload["event"]["dbId"] == "jennifer_common"
        assert payload["event"]["resourceType"] == "apm.Instance"
        assert payload["meta"]["hints"]["solution"] == "apm"
        assert payload["meta"]["episode_id"] == "ep-p150fixture"

    def test_note_literal_matches_module(self):
        assert evidence_prefetch.INFRA_SOURCE_UNKNOWN_NOTE == INFRA_SOURCE_UNKNOWN_NOTE


class TestR4ExpectedBehavior:
    """plans/150 §4.6 사전수집 소스 — W5 교정."""

    @pytest.mark.parametrize("db_id", ["jennifer", "jennifer_common"])
    def test_apm_trigger_without_infra_source_skips_polestar(self, monkeypatch, tmp_path, db_id):
        # 신규 플래그 없이(현행 설정) — 플래그 밖 결함 교정(R-4).
        fn, fake = _prefetch(monkeypatch, _settings())
        result = fn(_job(_apm_payload(db_id), tmp_path))
        assert not any(str(args.get("source", "")).startswith("jennifer") for _, args in fake.calls)
        assert result is not None
        assert any(INFRA_SOURCE_UNKNOWN_NOTE in n for n in result["notes"])

    def test_apm_trigger_uses_episode_infra_db_id(self, monkeypatch, tmp_path):
        episode = {"episode_id": "ep-p150fixture", "infra_db_ids": ["polestar_cm_gp"]}
        fn, fake = _prefetch(monkeypatch, _settings(cross_source_investigation_enabled=True))
        result = fn(_job(_apm_payload("jennifer_common", episode=episode), tmp_path))
        assert fake.calls
        assert {args["source"] for _, args in fake.calls} == {"polestar_cm_gp"}
        assert not any("알 수 없는 소스" in n for n in result["notes"])

    def test_apm_trigger_episode_infra_db_id_ignored_when_flag_off(self, monkeypatch, tmp_path):
        # 플래그 off면 meta.episode를 무시한다 — 폴스타 축 생략(R-4 교정만 적용).
        episode = {"episode_id": "ep-p150fixture", "infra_db_ids": ["polestar_cm_gp"]}
        fn, fake = _prefetch(monkeypatch, _settings())
        result = fn(_job(_apm_payload("jennifer_common", episode=episode), tmp_path))
        assert fake.calls == []
        assert INFRA_SOURCE_UNKNOWN_NOTE in result["notes"]

    def test_infra_trigger_unaffected(self, monkeypatch, tmp_path):
        # 대조 — 폴스타 트리거는 dbId가 그대로 유효한 소스다
        # (교정 뒤에도 유지 · W0 결함 클래스에서 옮김).
        payload = _apm_payload("polestar_cm_gp")
        payload["event"]["resourceType"] = "server.Server"
        payload["meta"].pop("hints")
        fn, fake = _prefetch(monkeypatch, _settings())
        result = fn(_job(payload, tmp_path))
        assert {args["source"] for _, args in fake.calls} == {"polestar_cm_gp"}
        assert not any("결손" in n for n in result["notes"])
