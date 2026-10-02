"""plans/87 J8 · D-287 ② — 조사 지침의 제니퍼 소스(`source_id`) 전달 (LLM 0회).

다중 소스에서는 인스턴스·트랜잭션 식별이 (소스, 도메인, 인스턴스)다 — 지침이 `profile_ref`의
`source_id`까지 넘기라고 말하고, 트리거 힌트 줄에 `source_id=…`를 싣는다.
`apm_guidance_enabled` off면 조립 문자열이 종전과 바이트 동일하다(APM 조각이 아예 붙지 않는다).
"""

from __future__ import annotations

from types import SimpleNamespace

from sre_agent.application.investigation_guidance import (
    APM_FOCUS_NOTE_TEMPLATE,
    apm_playbook_note,
    build_guidance,
)
from sre_agent.settings import AgentSettings

_HINTS = {"solution": "apm", "source_id": "bank", "instance_id": 1001, "domain_id": 1000,
          "event_type": "ERROR_OUTOFMEMORY", "txid": "7142093822391"}
_APM_EVENT = {"event": {"resourceType": "apm.Instance", "alarmName": "ERROR_OUTOFMEMORY",
                        "serverName": "was-01", "hostname": "was-01", "severity": 3},
              "meta": {"hints": _HINTS}}


def _settings(**kw) -> AgentSettings:
    base = {"model": "test/model", "api_key": None, "gemini_api_key": None}
    return AgentSettings(_env_file=None, **{**base, **kw})


def _job(**kw) -> SimpleNamespace:
    base = {"kind": "alarm", "question": None, "payload": None, "reference_time": None,
            "lookback_minutes": None}
    base.update(kw)
    return SimpleNamespace(**base)


def test_focus_note_passes_source_id_from_profile_ref() -> None:
    expected = "profile_ref의 source_id·domain_id·txid·time_ms를 그대로 넘긴다"
    assert expected in APM_FOCUS_NOTE_TEMPLATE


def test_hint_line_carries_source_id_in_fixed_order() -> None:
    note = apm_playbook_note(_HINTS)
    assert note.endswith(
        "- 트리거 힌트: event_type=ERROR_OUTOFMEMORY · source_id=bank · instance_id=1001 · "
        "domain_id=1000 · txid=7142093822391"
    )


def test_hint_line_without_source_id_is_unchanged() -> None:
    """v4 트리거(source_id None)는 종전 힌트 줄 그대로다."""
    hints = {**_HINTS, "source_id": None}
    assert apm_playbook_note(hints).endswith(
        "- 트리거 힌트: event_type=ERROR_OUTOFMEMORY · instance_id=1001 · domain_id=1000 · "
        "txid=7142093822391"
    )


def test_guidance_on_includes_source_id() -> None:
    g = build_guidance(_settings(apm_guidance_enabled=True, apm_mcp_url="http://gw/sse"),
                       _job(payload=_APM_EVENT, investigation_id="inv-1"))
    assert "source_id=bank" in g and "profile_ref의 source_id" in g


def test_guidance_off_is_byte_identical_and_has_no_source_id() -> None:
    off = build_guidance(_settings(), _job(payload=_APM_EVENT))
    assert off == build_guidance(_settings(), _job())
    assert off == build_guidance(_settings(apm_guidance_enabled=False, apm_mcp_url="http://gw/sse"),
                                 _job(payload=_APM_EVENT))
    assert "source_id" not in off
