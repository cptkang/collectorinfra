"""트리거 페이로드 계약 검증 (Plan 05 §4·§8) — contract_version·필수 필드·선택 필드 수용."""

from sre_agent.application.investigation_jobs import (
    CONTRACT_VERSION,
    JobStore,
    validate_payload,
)
from sre_agent.settings import AgentSettings


def make_settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        model="test/model",
        api_key=None,
        max_steps=3,
        gemini_api_key=None,
        service_bearer_token=None,
    )


def base_payload() -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "event": {"serverName": "web-01", "hostname": "web-01.local", "severity": 2},
        "decision": {"fingerprint": "fp"},
    }


def test_contract_version_constant_is_1():
    assert CONTRACT_VERSION == "1"


def test_valid_payload_accepted():
    ok, reason = validate_payload(base_payload())
    assert ok is True and reason is None


def test_missing_contract_version_rejected():
    p = base_payload()
    p.pop("contract_version")
    ok, reason = validate_payload(p)
    assert ok is False and "contract_version" in reason


def test_unsupported_contract_version_rejected():
    p = base_payload()
    p["contract_version"] = "2"
    ok, reason = validate_payload(p)
    assert ok is False and "contract_version" in reason


def test_missing_required_event_field_rejected():
    for missing in ("serverName", "hostname", "severity"):
        p = base_payload()
        p["event"].pop(missing)
        ok, reason = validate_payload(p)
        assert ok is False, f"{missing} 결측이 거부되지 않음"
        assert missing in reason


def test_empty_required_field_rejected():
    p = base_payload()
    p["event"]["hostname"] = ""
    ok, reason = validate_payload(p)
    assert ok is False and "hostname" in reason


def test_severity_zero_is_valid():
    # severity 0은 빈값이 아니므로 유효(빈문자열/None만 거부).
    p = base_payload()
    p["event"]["severity"] = 0
    ok, _ = validate_payload(p)
    assert ok is True


def test_optional_fields_absence_accepted():
    # decision.signals·meta 등 선택 필드 결측은 거부하지 않는다(스코프만 축소).
    p = {
        "contract_version": CONTRACT_VERSION,
        "event": {"serverName": "web-01", "hostname": "web-01.local", "severity": 3},
    }
    ok, reason = validate_payload(p)
    assert ok is True and reason is None


def test_non_dict_payload_rejected():
    ok, reason = validate_payload("not-a-dict")
    assert ok is False and reason


def test_submit_missing_field_returns_rejected(tmp_path):
    store = JobStore(make_settings(), audit_path=tmp_path / "a.jsonl")
    p = base_payload()
    p["event"].pop("serverName")
    res = store.submit(p)
    assert res["status"] == "rejected"
    assert "serverName" in res["reason"]


def test_submit_unsupported_version_rejected(tmp_path):
    store = JobStore(make_settings(), audit_path=tmp_path / "a.jsonl")
    p = base_payload()
    p["contract_version"] = "99"
    res = store.submit(p)
    assert res["status"] == "rejected"
    assert "contract_version" in res["reason"]


# ── plans/153 §4.4 가산 키(meta.episode · meta.cross_source · meta.investigation_kind) ──


def _episode_payload() -> dict:
    p = base_payload()
    p["meta"] = {
        "episode_id": "ep-1",
        "investigation_kind": "episode",
        "cross_source": {"rule_id": "R1", "cause_alarm_id": "a1", "lag_seconds": 270.0,
                         "action": "link", "missing": []},
        "episode": {
            "episode_id": "ep-1", "zone": "gongjon", "host_key": "web-01",
            "members": [{"alarm_id": "a1", "source": "polestar", "db_id": "db1",
                         "alarm_name": "CPU", "kind": "cpu", "was_kinds": [], "level": "",
                         "severity": 2, "occurred_at": 1791363600.0, "tier": "page",
                         "resolved": False}],
            "links": [], "rules": [], "infra_db_ids": ["db1"],
        },
    }
    return p


def test_additive_episode_meta_keys_accepted_without_version_bump():
    # contract_version은 "1" 그대로 — 수신 측은 meta 추가 키를 허용한다(D-118 · plans/153 §4.4).
    p = _episode_payload()
    assert p["contract_version"] == CONTRACT_VERSION == "1"
    ok, reason = validate_payload(p)
    assert ok is True and reason is None


def test_submit_with_episode_meta_accepted(tmp_path):
    store = JobStore(make_settings(), audit_path=tmp_path / "a.jsonl")
    res = store.submit(_episode_payload())
    assert res["status"] == "accepted"


def test_malformed_episode_meta_does_not_reject():
    # 모양이 틀린 meta.episode도 계약 위반이 아니다(선택 키 — 해석 측이 무시한다).
    p = base_payload()
    p["meta"] = {"episode": "not-a-dict", "investigation_kind": 3}
    ok, _ = validate_payload(p)
    assert ok is True
