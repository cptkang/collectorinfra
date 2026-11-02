"""plans/201 W3 — 알람 SSE 담당 강조 키(`apm_event_type`·`top_process_kinds`) · 결정 기록 중첩 집계.

- 플래그 유효 off(`DUTY_AREA_ENABLED` AND `DUTY_ALARM_FOCUS_ENABLED`가 아님)면 티어 SSE·PAGE(incident open)
  payload가 W3 이전과 **바이트 동일**하다(키 집합·순서·값).
- on이면 값 있을 때만 두 키가 붙고, 티어 SSE와 PAGE가 대칭이다. 새 키에는 kind 이름·유형 원값만 —
  프로세스 이름·인자 원문·서버명·IP는 없다.
- 프로세스 종류 판정은 본체가 주입한 콜백이 한다(`noise_gate`는 판정 모듈을 import하지 않는다 · D-139 부기).
- `DecisionStore.count_recent_values`가 점 경로를 받는다(하위호환).
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.application.nodes.alarm_notifier import (
    _incident_open_payload,
    _tier_sse_payload,
    alarm_notifier_node,
    duty_alarm_focus_on,
    duty_focus_sse_keys,
)
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent, ProcessInfo, ProcessSnapshot
from noise_gate.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_TICKET,
    NotificationDecision,
)
from noise_gate.infrastructure.decision_store import DecisionStore

REF = datetime(2026, 10, 30, 9, 0, 0)

# a289818f 시점 `_tier_sse_payload` 키 순서(조사 id·사건 줄 없는 기본형) — off는 이것과 같아야 한다
_BASE_TIER_KEYS = [
    "type", "alarm_id", "severity", "severity_label", "alarm_name", "db_id", "server_name",
    "hostname", "ip_address", "resource_type", "resource_name", "alarm_status", "summary",
    "probable_cause", "recommended_action", "pattern_type", "is_routine", "pattern_analysis",
    "pre_classification", "tier", "tier_reason", "stage", "server_identity", "alarm_time",
    "received_at",
]
_BASE_OPEN_KEYS = [
    "type", "fingerprint", "priority", "ts", "alarm_id", "severity", "severity_label",
    "alarm_name", "db_id", "server_name", "hostname", "ip_address", "resource_type",
    "resource_name", "alarm_status", "summary", "probable_cause", "recommended_action",
    "pattern_type", "is_routine", "pattern_analysis", "pre_classification", "tier",
    "server_identity", "alarm_time", "received_at",
]


def _result(*, apm_type: str | None = "BAD_RESPONSE_TIME_SQL") -> AlarmAnalysisResult:
    raw: dict = {"resourceType": "x"}
    if apm_type is not None:
        raw["apm"] = {"event_type_norm": apm_type, "instance": "inst-01"}
    event = AlarmEvent(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h-1", ip_address="10.0.0.1",
        resource_ancestry="/Servers/svr/Cpus", alarm_id="A-1", severity=2, alarm_status="NOT_ACK",
        resource_type="CPU", resource_name="svr-1-CPU", alarm_name="CPU 임계",
        alarm_time=REF, conditions="", condition_log="", is_clear=False, raw_payload=raw,
    )
    return AlarmAnalysisResult(
        alarm_event=event, severity_label="경고", summary="s", probable_cause="c",
        recommended_action="a", notification_channels=[],
    )


def _decision(tier: str) -> NotificationDecision:
    return NotificationDecision(tier=tier, reason="테스트", priority=300,
                                signals={"severity": 2}, fingerprint="fp")


def _proc(name: str, args: str) -> ProcessInfo:
    return ProcessInfo(name=name, pid=1, ppid=0, user="svc", p100cpu=9.0, pcpu=9.0,
                       pmem=1.0, rss=0, args=args)


def _snapshot() -> ProcessSnapshot:
    return ProcessSnapshot(
        alarm_kind="cpu", captured_at=REF, total_count=7, source_host="h-1",
        top=[
            _proc("java", "/opt/java/bin/java -Dcatalina.home=/opt/t org.apache.catalina.startup.Bootstrap"),
            _proc("db2sysc", "db2sysc 0"),
            _proc("java", "/opt/java/bin/java -Dcatalina.home=/opt/u org.apache.catalina.startup.Bootstrap"),
            _proc("sshd", "/usr/sbin/sshd -D"),
        ],
    )


def _fake_classifier(name: str, args: str) -> str | None:
    if "catalina" in args:
        return "tomcat"
    if name == "db2sysc":
        return "db2"
    return None


def _cfg(*, enabled: bool, focus: bool):
    duty = SimpleNamespace(enabled=enabled, alarm_focus_enabled=focus,
                           alarm_focus_effective=enabled and focus)
    return SimpleNamespace(workb=SimpleNamespace(), alarm=SimpleNamespace(), duty_area=duty)


class _Bus:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def publish(self, payload: dict) -> None:
        self.payloads.append(payload)


def _config(cfg, bus: _Bus, *, classifier=_fake_classifier) -> dict:
    configurable = {"app_config": cfg, "alarm_bus": bus, "incident_publisher": bus}
    if classifier is not None:
        configurable["process_kind_classifier"] = classifier
    return {"configurable": configurable}


def _state(tier: str, kinds: list | None = None) -> dict:
    state = {"analysis_result": _result(), "notification_decision": _decision(tier),
             "process_snapshot": _snapshot()}
    if kinds is not None:
        state["top_process_kinds"] = kinds      # enricher가 판정기 주입 시에만 싣는 상태 키
    return state


@pytest.fixture(autouse=True)
def _no_send(monkeypatch):
    async def fake(*_a, **_k):
        return None

    monkeypatch.setattr(notifier_mod, "_send_workb", fake)
    monkeypatch.setattr(notifier_mod, "_send_webhook", fake)


# ─── 플래그 판정 ──────────────────────────────────────────────────────────────


def test_focus_flag_requires_both_and_exact_true() -> None:
    from unittest.mock import MagicMock

    assert duty_alarm_focus_on(_cfg(enabled=True, focus=True)) is True
    assert duty_alarm_focus_on(_cfg(enabled=True, focus=False)) is False
    assert duty_alarm_focus_on(_cfg(enabled=False, focus=True)) is False
    assert duty_alarm_focus_on(SimpleNamespace()) is False
    assert duty_alarm_focus_on(MagicMock()) is False     # 테스트 대역이 켜진 것으로 읽히지 않는다


def test_real_config_property() -> None:
    from src.config.services import DutyAreaConfig

    assert DutyAreaConfig(_env_file=None).alarm_focus_effective is False
    assert DutyAreaConfig(_env_file=None, enabled=True).alarm_focus_effective is False
    assert DutyAreaConfig(_env_file=None, alarm_focus_enabled=True).alarm_focus_effective is False
    on = DutyAreaConfig(_env_file=None, enabled=True, alarm_focus_enabled=True)
    assert on.alarm_focus_effective is True


def test_env_key_name_is_exact(monkeypatch) -> None:
    from src.config.services import DutyAreaConfig

    monkeypatch.setenv("DUTY_AREA_ENABLED", "true")
    monkeypatch.setenv("DUTY_ALARM_FOCUS_ENABLED", "true")
    assert DutyAreaConfig(_env_file=None).alarm_focus_effective is True
    monkeypatch.delenv("DUTY_ALARM_FOCUS_ENABLED")
    monkeypatch.setenv("DUTY_AREA_ALARM_FOCUS_ENABLED", "true")   # 접두 붙은 이름은 받지 않는다
    assert DutyAreaConfig(_env_file=None).alarm_focus_effective is False


# ─── off = 바이트 동일 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("cfg", [
    _cfg(enabled=False, focus=False), _cfg(enabled=True, focus=False),
    _cfg(enabled=False, focus=True), SimpleNamespace(workb=SimpleNamespace(), alarm=SimpleNamespace()),
])
@pytest.mark.parametrize("tier", [TIER_DASHBOARD, TIER_TICKET])
async def test_off_tier_payload_identical(cfg, tier) -> None:
    bus = _Bus()
    state = _state(tier)
    await alarm_notifier_node(state, _config(cfg, bus))
    assert len(bus.payloads) == 1
    sent = bus.payloads[0]
    assert list(sent) == _BASE_TIER_KEYS
    expected = _tier_sse_payload(state["analysis_result"], state["notification_decision"])
    assert json.dumps(sent, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)


async def test_off_page_payload_identical() -> None:
    bus = _Bus()
    state = _state(TIER_PAGE)
    await alarm_notifier_node(state, _config(_cfg(enabled=True, focus=False), bus))
    assert len(bus.payloads) == 1
    sent = dict(bus.payloads[0])
    assert list(sent) == _BASE_OPEN_KEYS
    expected = _incident_open_payload(state["analysis_result"], state["notification_decision"])
    sent.pop("ts"), expected.pop("ts")
    assert sent == expected


async def test_off_ignores_state_kinds() -> None:
    """플래그 유효 off면 상태에 kind가 있어도(이론상) SSE에 싣지 않는다."""
    bus = _Bus()
    await alarm_notifier_node(_state(TIER_DASHBOARD, ["tomcat"]), _config(_cfg(enabled=True, focus=False), bus))
    assert list(bus.payloads[0]) == _BASE_TIER_KEYS


# ─── on = 값 있을 때만 키 · 대칭 ───────────────────────────────────────────────


async def test_on_tier_and_page_symmetric_keys() -> None:
    cfg = _cfg(enabled=True, focus=True)
    tier_bus, page_bus = _Bus(), _Bus()
    kinds = ["tomcat", "db2", "tomcat", 3, ""]           # 상태 키(enricher 판정) — 고유·문자열만 옮긴다
    await alarm_notifier_node(_state(TIER_DASHBOARD, kinds), _config(cfg, tier_bus))
    await alarm_notifier_node(_state(TIER_PAGE, kinds), _config(cfg, page_bus))
    tier_payload, page_payload = tier_bus.payloads[0], page_bus.payloads[0]
    for payload, base in ((tier_payload, _BASE_TIER_KEYS), (page_payload, _BASE_OPEN_KEYS)):
        assert list(payload)[: len(base)] == base          # 기존 키 순서 그대로 · 새 키는 뒤
        assert payload["apm_event_type"] == "BAD_RESPONSE_TIME_SQL"
        # 고유 · 순서 유지
        assert payload["top_process_kinds"] == ["tomcat", "db2"]
        assert set(payload) - set(base) == {"apm_event_type", "top_process_kinds"}



# ─── 사후 승격 incident open(send_late_promotion) 대칭 ────────────────────────


async def test_late_promotion_off_payload_identical() -> None:
    result, decision, bus = _result(), _decision(TIER_TICKET), _Bus()
    await notifier_mod.send_late_promotion(result, decision, {"reason": "r"}, _cfg(enabled=True, focus=False), bus)
    sent = dict(bus.payloads[0])
    assert "apm_event_type" not in sent and "top_process_kinds" not in sent
    import dataclasses

    promoted = dataclasses.replace(decision, tier=TIER_PAGE, reason="r",
                                   evidence={"late_promotion": {"reason": "r"}})
    expected = _incident_open_payload(result, promoted)
    sent.pop("ts"), expected.pop("ts")
    assert sent == expected


async def test_late_promotion_on_carries_apm_event_type_only() -> None:
    bus = _Bus()
    await notifier_mod.send_late_promotion(_result(), _decision(TIER_TICKET), {"reason": "r"},
                                           _cfg(enabled=True, focus=True), bus)
    sent = bus.payloads[0]
    assert sent["tier"] == TIER_PAGE and sent["apm_event_type"] == "BAD_RESPONSE_TIME_SQL"
    assert "top_process_kinds" not in sent        # 사후 승격 시점엔 원 판정 스냅샷이 없다
    bus2 = _Bus()
    await notifier_mod.send_late_promotion(_result(apm_type=None), _decision(TIER_TICKET), {"reason": "r"},
                                           _cfg(enabled=True, focus=True), bus2)
    assert "apm_event_type" not in bus2.payloads[0]

def test_new_keys_carry_no_names_args_or_addresses() -> None:
    keys = duty_focus_sse_keys(_result(), ["tomcat", "db2"])
    blob = json.dumps(keys, ensure_ascii=False)
    for secret in ("srv-1", "h-1", "10.0.0.1", "java", "catalina", "db2sysc", "inst-01", "sshd"):
        assert secret not in blob, secret


def test_apm_event_type_is_raw_string() -> None:
    """원값 문자열 그대로(앞뒤 공백만 뗌) — 대조 정규화는 소비처가 한다."""
    keys = duty_focus_sse_keys(_result(apm_type="  METRICS EVENT "))
    assert keys == {"apm_event_type": "METRICS EVENT"}
    assert duty_focus_sse_keys(_result(apm_type="")) == {}
    assert duty_focus_sse_keys(_result(apm_type=None)) == {}
    long_type = "X" * 500
    assert len(duty_focus_sse_keys(_result(apm_type=long_type))["apm_event_type"]) == 64


def test_top_process_kinds_capped_and_strings_only() -> None:
    keys = duty_focus_sse_keys(_result(apm_type=None), [f"k{i}" for i in range(9)])
    assert keys["top_process_kinds"] == ["k0", "k1", "k2", "k3", "k4"]
    assert duty_focus_sse_keys(_result(apm_type=None), None) == {}
    assert duty_focus_sse_keys(_result(apm_type=None), []) == {}
    assert duty_focus_sse_keys(_result(apm_type=None), "tomcat") == {}       # 목록이 아니면 무시
    assert duty_focus_sse_keys(_result(apm_type=None), [None, 1, ""]) == {}


def test_notifier_does_not_reclassify_masked_snapshot() -> None:
    """notifier는 마스킹 스냅샷으로 다시 판정하지 않는다 — 상태 키가 없으면 kind 키도 없다."""
    import inspect

    assert "process_kind_classifier" not in inspect.getsource(notifier_mod)
    assert list(inspect.signature(duty_focus_sse_keys).parameters) == ["result", "top_process_kinds"]


async def test_on_without_values_adds_nothing() -> None:
    bus = _Bus()
    state = {"analysis_result": _result(apm_type=None),
             "notification_decision": _decision(TIER_DASHBOARD), "process_snapshot": _snapshot()}
    await alarm_notifier_node(state, _config(_cfg(enabled=True, focus=True), bus))
    assert list(bus.payloads[0]) == _BASE_TIER_KEYS


# ─── 경계 · 배선 ──────────────────────────────────────────────────────────────

_ROOT = Path(__file__).resolve().parents[2]


def test_noise_gate_does_not_import_middleware_module() -> None:
    """D-139 부기 — `src.domain.middleware`(pydantic·yaml)는 noise_gate가 import하지 않는다."""
    for p in (_ROOT / "noise_gate").rglob("*.py"):
        if "/tests/" in p.as_posix():
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("src.domain.middleware"), p
                if node.module == "src.domain":
                    assert all(a.name != "middleware" for a in node.names), p
            elif isinstance(node, ast.Import):
                assert all(not a.name.startswith("src.domain.middleware") for a in node.names), p


def test_worker_configurable_key_only_when_injected() -> None:
    from noise_gate.application.alarm_worker import AlarmWorker

    plain = AlarmWorker(SimpleNamespace())
    assert plain._process_kind_classifier is None
    injected = AlarmWorker(SimpleNamespace(), process_kind_classifier=_fake_classifier)
    assert injected._process_kind_classifier is _fake_classifier
    src = (_ROOT / "noise_gate/application/alarm_worker.py").read_text(encoding="utf-8")
    assert '{"process_kind_classifier": self._process_kind_classifier}' in src
    assert "if self._process_kind_classifier is not None else {}" in src


def test_server_injects_classifier_only_when_focus_on(monkeypatch) -> None:
    import asyncio

    import noise_gate.application.alarm_worker as worker_mod
    from src.api import server

    seen: list[dict] = []

    class _FakeWorker:
        def __init__(self, config, **kwargs) -> None:
            seen.append(kwargs)

        async def run(self) -> None:
            return None

    monkeypatch.setattr(worker_mod, "AlarmWorker", _FakeWorker)

    async def go(focus: bool) -> None:
        cfg = SimpleNamespace(
            alarm=SimpleNamespace(enabled=True, redis_stream_key="s"),
            duty_area=SimpleNamespace(alarm_focus_effective=focus),
        )
        task = server._start_alarm_worker(cfg)
        await task

    asyncio.run(go(False))
    asyncio.run(go(True))
    from src.domain.middleware import classify_process_kind

    assert seen[0] == {}
    assert seen[1] == {"process_kind_classifier": classify_process_kind}


# ─── 결정 기록 점 경로 집계 ───────────────────────────────────────────────────


def test_count_recent_values_dotted_path(tmp_path) -> None:
    path = tmp_path / "d.jsonl"
    now = datetime.now(UTC)
    recent = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(days=30)).isoformat()
    rows = [
        {"ts": recent, "resource_type": "CPU",
         "stage_evidence": {"apm_policy": {"event_type_norm": "BAD_RESPONSE_TIME_SQL"}}},
        {"ts": recent, "resource_type": "CPU",
         "stage_evidence": {"apm_policy": {"event_type_norm": " METRICS EVENT "}}},
        {"ts": recent, "resource_type": "apm.Instance", "stage_evidence": {"apm_policy": "x"}},
        {"ts": recent, "resource_type": "apm.Instance"},
        {"ts": old, "stage_evidence": {"apm_policy": {"event_type_norm": "OLD"}}},
        {"ts": recent, "type": "recurrence",
         "stage_evidence": {"apm_policy": {"event_type_norm": "SKIP"}}},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    store = DecisionStore(str(path), True)
    counts, empty = store.count_recent_values(7 * 86400, "stage_evidence.apm_policy.event_type_norm")
    assert counts == {"BAD_RESPONSE_TIME_SQL": 1, "METRICS EVENT": 1}
    assert empty == 2
    # 점 없는 이름은 종전과 같다(최상위 필드)
    top, top_empty = store.count_recent_values(7 * 86400, "resource_type")
    assert top == {"CPU": 2, "apm.Instance": 2} and top_empty == 0
