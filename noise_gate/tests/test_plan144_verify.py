"""plans/144 W0·W1·W2 독립 검증 (verifier) — 계획 이탈(지속 조건 재판정) · 보안 · 경계.

  A. 지속 조건 재판정 경로(이탈 1건) — off·폴스타 미진입 · 1회성 ·
     다른 지문/해소/심각도3 무부작용 · sweep
  B. 정책 YAML 로딩 보안 — safe_load · 스키마 위반 · 유형 키 충돌
  C. enricher 공급자 예외 → unavailable(PAGE) + 로그
  D. 경계 — noise_gate 신규·변경 파일이 apm_gateway를 import하지 않는다 ·
     리플레이 하네스 외부 호출 0
  E. host_key 경계값

apm_gateway는 import하지 않는다(D-139). 게이트웨이 `alarm:raw` 레코드는 합성한다.
"""

from __future__ import annotations

import ast
import json
import logging
import socket
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_context_enricher import alarm_context_enricher_node
from noise_gate.domain.alarm import (
    HOST_KEY_NONE,
    HOST_KEY_STRONG,
    AlarmEvent,
    normalize_host_key,
)
from noise_gate.domain.notification_policy import (
    STAGE_SEVERITY3,
    TIER_PAGE,
    compute_fingerprint,
    decide_notification,
)
from noise_gate.infrastructure.apm_noise_context import ApmNoiseContext

REPO = Path(__file__).resolve().parents[2]
REF = datetime(2026, 10, 7, 10, 0, 0)


# ─── 합성 픽스처 ──────────────────────────────────────────────────────────────


def _record(
    alarm_name: str = "JVM_HEAP_MEM_HIGH",
    *,
    severity: int = 2,
    kind: str = "metric",
    instance: str = "was-app-01_8080",
    alarm_id: str = "jennifer:1",
) -> dict:
    return {
        "dbId": "jennifer",
        "source": "jennifer",
        "serverName": "was-app-01",
        "hostname": "was-app-01",
        "ipAddress": "",
        "resourceAncestry": "JENNIFER > dom > " + instance,
        "alarmId": alarm_id,
        "severity": severity,
        "alarmStatus": "",
        "resourceType": "apm.Instance",
        "resourceName": instance,
        "alarmName": alarm_name,
        "alarmTime": "20261007100000",
        "conditions": f"JENNIFER EVENT warning — {alarm_name}",
        "conditionLog": "event",
        "apm": {
            "source": "jennifer",
            "instance_name": instance,
            "event_type": alarm_name,
            "event_kind": kind,
            "match_reason": "host_name",
            "was_signals": [],
        },
    }


def _event_from(rec: dict) -> AlarmEvent:
    return AlarmEvent(
        db_id=rec["dbId"], server_name=rec["serverName"], hostname=rec["hostname"],
        ip_address=rec["ipAddress"], resource_ancestry=rec["resourceAncestry"],
        alarm_id=rec["alarmId"], severity=rec["severity"], alarm_status="",
        resource_type=rec["resourceType"], resource_name=rec["resourceName"],
        alarm_name=rec["alarmName"], alarm_time=REF, conditions=rec["conditions"],
        condition_log=rec["conditionLog"], is_clear=(rec["severity"] == 0), raw_payload=rec,
    )


class _Redis:
    async def xack(self, *a, **k):
        return 1

    async def xadd(self, *a, **k):
        return b"1-0"

    async def get(self, key):
        return None

    async def set(self, *a, **k):
        return True


class _Graph:
    def __init__(self) -> None:
        self.states: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        return state


def _worker(*, apm_on: bool, gate_on: bool = True) -> tuple[AlarmWorker, _Graph]:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=gate_on,
            repeat_interval_seconds=14400,
            suppress_max_severity=2,
            self_heal_window_seconds=300,
            apm_noise_policy_enabled=apm_on,
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )
    w = AlarmWorker(cfg)
    g = _Graph()
    w._graph = g
    w._apm_noise_ctx = w._build_apm_noise_ctx()
    return w, g


class _Clock:
    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


async def _feed(w: AlarmWorker, rec: dict, n: int = 1) -> None:
    fields = {b"data": json.dumps(rec, ensure_ascii=False).encode("utf-8")}
    await w._process(_Redis(), "alarm:raw", "g", f"{n}-1".encode(), fields, {})


# ═════════════════════════════════════════════════════════════════════════════
# A. 지속 조건 재판정(계획 이탈 1건)
# ═════════════════════════════════════════════════════════════════════════════


class TestReevaluationDeviation:
    def test_provider_not_built_when_gate_off_or_flag_off(self):
        assert _worker(apm_on=False)[0]._apm_noise_ctx is None
        assert _worker(apm_on=True, gate_on=False)[0]._apm_noise_ctx is None
        assert _worker(apm_on=True)[0]._apm_noise_ctx is not None

    async def test_flag_off_never_enters_policy_signal(self, monkeypatch, clock):
        w, g = _worker(apm_on=False)

        def _boom(*a, **k):  # pragma: no cover — 호출되면 실패
            raise AssertionError("플래그 off에서 _apm_policy_signal 호출")

        monkeypatch.setattr(w, "_apm_policy_signal", _boom)
        for i in range(3):
            clock.t += 30
            await _feed(w, _record(alarm_id=f"a{i}"), i)
        assert len(g.states) == 1
        assert w._apm_persist == {} and w._apm_persist_pending == {}

    async def test_polestar_never_enters_policy_signal(self, monkeypatch, clock):
        w, g = _worker(apm_on=True)

        def _boom(*a, **k):  # pragma: no cover
            raise AssertionError("폴스타 알람에서 _apm_policy_signal 호출")

        monkeypatch.setattr(w, "_apm_policy_signal", _boom)
        for i in range(3):
            clock.t += 30
            rec = _record("CPU 사용률", alarm_id=f"p{i}")
            rec.update(dbId="polestar_cm_gp", source="", resourceType="server.Server")
            rec.pop("apm")
            await _feed(w, rec, i)
        assert len(g.states) == 1  # 현행 지문 dedup 그대로
        assert g.states[0]["apm_policy"] is None

    async def test_reevaluation_does_not_touch_other_fingerprint_dedup(self, clock):
        w, g = _worker(apm_on=True)
        await _feed(w, _record("OUTOFMEMORY", kind="error", alarm_id="o1"), 1)
        other_fp = next(iter(w._gate_dedup))
        snapshot = dict(w._gate_dedup[other_fp])
        clock.t += 10
        await _feed(w, _record(alarm_id="m1"), 2)
        clock.t += 60
        await _feed(w, _record(alarm_id="m2"), 3)
        assert g.states[-1]["apm_policy"]["persistence"]["reevaluated"] is True
        assert w._gate_dedup[other_fp] == snapshot
        clock.t += 10
        await _feed(w, _record("OUTOFMEMORY", kind="error", alarm_id="o2"), 4)
        assert len(g.states) == 3  # 다른 지문은 여전히 dedup

    async def test_clear_never_sets_pending_and_reaches_graph(self, clock):
        w, g = _worker(apm_on=True)
        await _feed(w, _record(severity=0, alarm_id="c1"), 1)
        assert w._apm_persist_pending == {} and w._apm_persist == {}
        assert g.states[0]["apm_policy"]["persistence"] is None

    async def test_severity3_never_pending_and_stays_page(self, clock):
        w, g = _worker(apm_on=True)
        await _feed(w, _record(severity=3, alarm_id="s1"), 1)
        assert w._apm_persist_pending == {}
        st = g.states[0]
        ev = st["alarm_event"]
        p = w._apm_noise_ctx
        d = decide_notification(
            ev, None, None, p.fetch(ev),
            SimpleNamespace(suppress_max_severity=2, importance_value_map={}),
            apm_policy=st["apm_policy"],
        )
        assert d.tier == TIER_PAGE and d.stage == STAGE_SEVERITY3

    async def test_second_unmet_after_lull_reevaluates_matrix_once(self, clock):
        """첫 건(미달·DASHBOARD) 뒤 창 밖 공백 → 다시 짝이 차면 매트릭스 판정 1회(정상 기대)."""
        w, g = _worker(apm_on=True)
        await _feed(w, _record(alarm_id="a"), 1)  # 미달 → 그래프
        clock.t += 600
        await _feed(w, _record(alarm_id="b"), 2)  # 미달 · dedup으로 버려짐
        clock.t += 60
        await _feed(w, _record(alarm_id="c"), 3)  # 충족 → 재판정
        assert len(g.states) == 2
        assert g.states[1]["apm_policy"]["persistence"]["reevaluated"] is True

    async def test_reevaluation_is_once_per_dedup_window(self, clock):
        w, g = _worker(apm_on=True)
        await _feed(w, _record(alarm_id="a"), 1)   # t0: 미달 → 그래프(DASHBOARD)
        clock.t += 60
        await _feed(w, _record(alarm_id="b"), 2)   # 충족 → 재판정(TICKET) — 1회
        for cycle in range(3):                      # 10분마다 짝 반복(4h dedup 창 안)
            clock.t += 600
            await _feed(w, _record(alarm_id=f"x{cycle}"), 10 + cycle)
            clock.t += 60
            await _feed(w, _record(alarm_id=f"y{cycle}"), 20 + cycle)
        # 기대: 매트릭스 판정이 이미 나간 지문은 repeat_interval 동안 dedup(그래프 2건)
        assert len(g.states) == 2

    def test_pending_is_bounded_by_cap(self, monkeypatch):
        monkeypatch.setattr(worker_mod, "_APM_PERSIST_MAX_KEYS", 3)
        w, _ = _worker(apm_on=True)
        ev = _event_from(_record())
        m = w._apm_noise_ctx.match(ev)
        for i in range(10):
            w._apm_policy_signal(ev, f"fp-{i}", m, 1000.0 + 400 * i)  # 창(300초) 밖 간격
        assert len(w._apm_persist) <= 3
        assert len(w._apm_persist_pending) <= 3

    async def test_sev3_escalation_after_demoted_sev2_reaches_gate(self, clock):
        w, g = _worker(apm_on=True)
        await _feed(w, _record("WARNING_JVM_HEAP_MEM_HIGH", severity=2, alarm_id="w"), 1)
        clock.t += 30
        await _feed(w, _record("ERROR_JVM_HEAP_MEM_HIGH", severity=3, alarm_id="e"), 2)
        assert [s["alarm_event"].severity for s in g.states] == [2, 3]

    def test_pending_swept_after_repeat_interval(self):
        w, _ = _worker(apm_on=True)
        ev = _event_from(_record())
        m = w._apm_noise_ctx.match(ev)
        w._apm_policy_signal(ev, "fp-old", m, 1000.0)
        w._apm_policy_signal(ev, "fp-new", m, 1000.0 + 14400)
        assert "fp-old" not in w._apm_persist_pending
        assert "fp-new" in w._apm_persist_pending


# ═════════════════════════════════════════════════════════════════════════════
# B. 정책 YAML 로딩 보안
# ═════════════════════════════════════════════════════════════════════════════


class TestPolicyLoadingSecurity:
    def test_python_object_tag_is_rejected_not_executed(self, tmp_path: Path, caplog):
        marker = tmp_path / "pwned"
        bad = tmp_path / "p.yaml"
        bad.write_text(
            f"types: !!python/object/apply:os.system ['touch {marker}']\n", encoding="utf-8"
        )
        with caplog.at_level(logging.WARNING):
            ctx = ApmNoiseContext.load(bad)
        assert not ctx.available and ctx.error
        assert not marker.exists()
        assert "적재 실패" in caplog.text

    @pytest.mark.parametrize(
        "body",
        [
            "types: {default: {notify: page, extra_col: 1}}\n",          # 알 수 없는 열
            "types: {default: {notify: loud}}\n",                        # notify 어휘 밖
            "types: {default: {notify: page, importance: 최고}}\n",      # 중요도 어휘 밖
            "types: {X: {notify: page}}\n",                              # default 행 없음
            "types: {default: {notify: page, persistence: {window_seconds: 0, min_count: 2}}}\n",
            "- just\n- a list\n",                                        # 최상위 모양 오류
        ],
    )
    def test_schema_violations_are_unavailable(self, tmp_path: Path, body: str):
        p = tmp_path / "p.yaml"
        p.write_text(body, encoding="utf-8")
        ctx = ApmNoiseContext.load(p)
        assert not ctx.available
        assert ctx.fetch(_event_from(_record()))["source"] == "unavailable"

    def test_normalized_key_collision_is_rejected(self, tmp_path: Path):
        p = tmp_path / "p.yaml"
        p.write_text(
            "types:\n"
            "  ERROR_OUTOFMEMORY: {notify: page, importance: 높음}\n"
            "  OUTOFMEMORY: {notify: dashboard, importance: 낮음}\n"
            "  default: {notify: dashboard}\n",
            encoding="utf-8",
        )
        assert not ApmNoiseContext.load(p).available

    def test_default_path_is_fixed_repo_config(self):
        from noise_gate.infrastructure import apm_noise_context as mod

        assert mod.DEFAULT_POLICY_PATH == REPO / "config" / "apm_noise_policy.yaml"
        src = (REPO / "noise_gate/infrastructure/apm_noise_context.py").read_text("utf-8")
        assert "yaml.safe_load" in src and "yaml.load(" not in src and "os.getenv" not in src


# ═════════════════════════════════════════════════════════════════════════════
# C. enricher 공급자 예외
# ═════════════════════════════════════════════════════════════════════════════


class _RaisingCtx:
    def fetch(self, event):
        raise RuntimeError("boom")


async def test_enricher_provider_exception_is_unavailable_and_logged(caplog):
    ev = _event_from(_record())
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True, message_enrichment_enabled=False,
            dynamic_baseline_enabled=False,
        ),
        alarm=SimpleNamespace(
            history_enabled=False, enrich_timeout_seconds=5, process_enrich_enabled=False
        ),
    )
    with caplog.at_level(logging.ERROR):
        out = await alarm_context_enricher_node(
            {"alarm_event": ev},
            {"configurable": {
                "app_config": cfg, "apm_noise_ctx": _RaisingCtx(), "noise_repo": None,
                "history_repo": None, "process_client": None, "history_redis": None,
            }},
        )
    assert out["noise_context"]["source"] == "unavailable"
    assert "APM 노이즈 컨텍스트 조회 실패" in caplog.text


# ═════════════════════════════════════════════════════════════════════════════
# D. 경계 · 외부 호출
# ═════════════════════════════════════════════════════════════════════════════


_NOISE_GATE_CHANGED = [
    "noise_gate/application/alarm_worker.py",
    "noise_gate/application/nodes/alarm_context_enricher.py",
    "noise_gate/application/nodes/notification_gate.py",
    "noise_gate/domain/alarm.py",
    "noise_gate/domain/notification_policy.py",
    "noise_gate/infrastructure/apm_noise_context.py",
    "noise_gate/orchestration/alarm_graph.py",
    "noise_gate/scripts/mock_polestar_events.py",
    "noise_gate/scripts/replay_noise.py",
]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


@pytest.mark.parametrize("rel", _NOISE_GATE_CHANGED)
def test_no_apm_gateway_import(rel: str):
    assert not any(m.split(".")[0] == "apm_gateway" for m in _imports(REPO / rel))


def test_domain_alarm_imports_no_outer_layer():
    mods = _imports(REPO / "noise_gate/domain/alarm.py")
    assert not any(
        m.startswith(("noise_gate.application", "noise_gate.infrastructure", "src")) for m in mods
    )


def test_replay_harness_makes_no_network_calls(monkeypatch):
    from noise_gate.scripts import replay_noise

    def _no_socket(*a, **k):
        raise AssertionError("리플레이 하네스가 소켓을 열었다")

    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(socket, "create_connection", _no_socket)
    lines = replay_noise.load_jsonl(
        str(REPO / "noise_gate/testdata/cross_source/mock_events.jsonl")
    )
    rows, _ = replay_noise.replay(lines)
    assert rows
    banned = {"redis", "httpx", "requests", "aiohttp", "langchain_core", "openai"}
    mods = _imports(REPO / "noise_gate/scripts/replay_noise.py")
    assert not {m.split(".")[0] for m in mods} & banned


def test_replay_baseline_has_no_jennifer_non_page_except_resolved():
    from noise_gate.scripts import replay_noise

    lines = replay_noise.load_jsonl(
        str(REPO / "noise_gate/testdata/cross_source/mock_events.jsonl")
    )
    rows, _ = replay_noise.replay(lines)
    jen = [r for r in rows if r["source"] == "jennifer"]
    assert jen
    for r in jen:
        if r["severity"] >= 1:
            assert r["tier"] == TIER_PAGE, r
        else:
            assert r["stage"] == "resolved", r


# ═════════════════════════════════════════════════════════════════════════════
# E. host_key 경계값
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "raw, key",
    [
        ("  XSWEB02.example.local.  ", "xsweb02"),
        ("10.0.0.1.", "10.0.0.1"),
        ("FE80::1", "fe80::1"),
        ("", ""),
        ("   ", ""),
        (".", ""),
    ],
)
def test_host_key_edges(raw: str, key: str):
    assert normalize_host_key(raw) == key


def test_jennifer_override_with_empty_hostname_is_none():
    rec = _record()
    rec["hostname"] = ""
    rec["apm"]["match_reason"] = "override"
    ev = _event_from(rec)
    assert ev.host_key == "" and ev.host_key_strength == HOST_KEY_NONE


def test_polestar_and_jennifer_fqdn_same_key_strong():
    pol = _event_from(_record())
    pol.db_id, pol.raw_payload, pol.resource_type, pol.hostname = (
        "polestar_cm_gp", None, "server.Server", "xsweb02",
    )
    jen_rec = _record()
    jen_rec["hostname"] = "XSWEB02.example.local"
    jen = _event_from(jen_rec)
    assert pol.host_key == jen.host_key == "xsweb02"
    assert pol.host_key_strength == jen.host_key_strength == HOST_KEY_STRONG
    # 지문은 그대로(hostname 원문 아닌 server_name 사용 · host_key 미사용)
    assert compute_fingerprint(jen) == compute_fingerprint(_event_from(jen_rec))
