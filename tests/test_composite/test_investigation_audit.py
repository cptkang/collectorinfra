"""조사 관측·감사 (Plan 78 W6 / Plan 80 WU-14 · SPEC M2 · **Tier 1**).

W6는 번호가 마지막이지만 **순서는 Tier 2(W2-7·8 · W3-2·3)보다 앞**이다 —
*측정 없이 하네스를 쌓지 않는다*(78 §4.6.2). 여기 지표가 없으면 압축·캐시의 이득을 판정할 수 없다.
"""

from __future__ import annotations

import json

import pytest

from src.config import load_config
from src.observability import investigation_metrics as metrics
from src.security import audit_logger
from src.security.audit_logger import log_investigation


@pytest.fixture(autouse=True)
def clean_metrics():
    metrics.reset()
    yield
    metrics.reset()


@pytest.fixture
def captured(monkeypatch):
    """감사 파일 쓰기를 가로채 레코드를 그대로 받는다(파일 I/O 없이 스키마를 본다)."""
    records: list[dict] = []

    async def _fake_write(entry):
        records.append(json.loads(entry.to_json()))

    monkeypatch.setattr(audit_logger, "_write_audit_file", _fake_write)
    return records


# ──────────────────────────────────────────────
# 감사 레코드 스키마 (W6-1 · 계약 C-B v2)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_carries_full_investigation_path(captured):
    """실패한 조사의 **전체 경로를 재구성**할 수 있다(W6 수용 기준 1항).

    request_id로 실패 트레이스와 대조하고, 대상·경로·종료코드·소요시간으로 어디서 끊겼는지 읽는다.
    """
    await log_investigation(
        request_id="req-1",
        entry_point="chat",
        targets=[{"hostname": "svweb001", "db_id": "polestar_gimpo"}],
        outcome="failed",
        backend="sre_agent",
        rc=124,
        duration_ms=45000.0,
        profile="vm",
    )
    rec = captured[0]
    assert rec["event"] == "host_investigation"
    for key in ("request_id", "entry_point", "targets", "target_count",
                "outcome", "backend", "rc", "duration_ms", "profile"):
        assert key in rec, f"{key}가 레코드에 없다 — 경로 재구성 불가"
    assert rec["target_count"] == 1


@pytest.mark.asyncio
async def test_record_has_authz_slot(captured):
    """★ W6-5 — 인가 판정 결과가 레코드에 포함된다.

    문서의 지적: *"관측성 추적은 신원과 권한 상태가 같은 세밀도로 포착될 때만 거버넌스
    증거가 된다."* 이 슬롯을 **W3-5(M5)가 채운다**.
    """
    await log_investigation(
        request_id="req-2",
        targets=[{"hostname": "svweb001"}],
        outcome="denied",
        authz={"allowed": False, "mode": "admin_only", "principal": "user",
               "reason": "role_not_admin"},
    )
    rec = captured[0]
    assert rec["authz"]["allowed"] is False
    assert rec["authz"]["reason"] == "role_not_admin"


@pytest.mark.asyncio
async def test_stdout_is_masked(captured):
    """수집 원문은 **마스킹 후** 저장한다(D-117 §18.5 계승)."""
    await log_investigation(
        request_id="req-3",
        targets=[{"hostname": "h1"}],
        stdout="java -jar app.jar --password=hunter2 --token=sk-" + "A" * 24,
    )
    out = captured[0]["stdout"]
    assert "hunter2" not in out
    assert "sk-AAAA" not in out
    # 키는 보존한다 — 무엇이 가려졌는지 알 수 있어야 진단에 쓸 수 있다.
    assert "password=" in out


@pytest.mark.asyncio
async def test_commands_are_masked(captured):
    """수집 명령의 접속문자열 비밀번호도 가린다."""
    await log_investigation(
        request_id="req-4",
        targets=[{"hostname": "h1"}],
        commands=["psql postgresql://svc:s3cr3tpw@db:5432/app -c 'select 1'"],
    )
    assert "s3cr3tpw" not in captured[0]["commands"][0]


@pytest.mark.asyncio
async def test_schema_is_extensible_without_breaking(captured):
    """★ 계약 C-B v2 — 79 트랙 C가 신뢰도 필드를 **추가**해도 기존 레코드가 깨지지 않는다.

    `AuditEntry(**kwargs)` + None 제거 구조라 필드 추가가 곧 확장이다.
    """
    await log_investigation(
        request_id="req-5",
        targets=[{"hostname": "h1"}],
        routing_confidence=0.82,  # 79가 나중에 붙일 필드
    )
    rec = captured[0]
    assert rec["routing_confidence"] == 0.82
    assert rec["request_id"] == "req-5"


@pytest.mark.asyncio
async def test_degraded_reasons_are_structured(captured):
    """강등·폴백은 **구조화된 사유**로 남는다(침묵 폴백 금지 — 80 §5.4-④)."""
    await log_investigation(
        request_id="req-6",
        targets=[{"hostname": "h1"}],
        outcome="partial",
        degraded=[{"stage": "fanout", "reason": "target_timeout", "target": "h2"}],
    )
    assert captured[0]["degraded"][0]["reason"] == "target_timeout"


# ──────────────────────────────────────────────
# Tier 2 지표 4종 (W6-4)
# ──────────────────────────────────────────────

def test_all_four_metric_axes_are_recorded():
    """★ 지표 4종이 실제로 남는다 — **Tier 2 착수 가능 판정의 근거**다."""
    metrics.record_compaction(host="h1", rows_truncated=7, tokens_before=900, tokens_after=300)
    metrics.record_cache(hit=True, age_seconds=15.0)
    metrics.record_cache(hit=False)
    metrics.record_investigation(tokens=1200, duration_ms=3400.0)
    metrics.record_investigation(denied_reason="role_not_admin")

    snap = metrics.snapshot()
    assert snap["compaction"]["rows_truncated"] == 7                 # 압축
    assert snap["compaction"]["per_host_truncated"]["h1"] == 7       # 호스트당 절단 행 수
    assert snap["compaction"]["tokens_before"] == 900               # 축약 전후 토큰
    assert snap["cache"] == {"hits": 1, "misses": 1, "hit_age_seconds_total": 15.0}  # 캐시·나이
    assert snap["routing"]["investigations"] == 1                    # 라우팅 진입
    assert snap["routing"]["denied_by_reason"]["role_not_admin"] == 1  # 거부 사유별
    assert snap["cost"] == {"investigations": 1, "tokens_total": 1200,
                            "duration_ms_total": 3400.0}             # 비용 귀속


def test_denied_is_not_counted_as_entry():
    """거부는 진입으로 세지 않는다 — 섞으면 진입률이 부풀어 경로 선택 판정이 어긋난다."""
    metrics.record_investigation(denied_reason="host_not_allowed")
    snap = metrics.snapshot()
    assert snap["routing"]["investigations"] == 0
    assert snap["cost"]["investigations"] == 0


def test_reason_keys_are_bounded():
    """in-memory dict는 값 bound뿐 아니라 **키 상한**도 필요하다(Known Mistakes)."""
    for i in range(200):
        metrics.record_investigation(denied_reason=f"reason_{i}")
    bucket = metrics.snapshot()["routing"]["denied_by_reason"]
    assert len(bucket) <= 65
    # 넘친 것을 조용히 버리지 않는다 — 총합은 보존된다.
    assert sum(bucket.values()) == 200


def test_snapshot_is_a_copy():
    """호출부가 스냅샷을 변형해도 내부가 오염되지 않는다."""
    metrics.record_investigation(denied_reason="x")
    snap = metrics.snapshot()
    snap["routing"]["denied_by_reason"]["x"] = 999
    assert metrics.snapshot()["routing"]["denied_by_reason"]["x"] == 1


def test_tier2_gate_blocks_without_observation():
    """★ 78 §4.6.2 — 관측 0건이면 Tier 2 착수 불가로 판정한다."""
    ok, reason = metrics.tier2_ready()
    assert ok is False
    assert "기준선" in reason

    metrics.record_investigation(tokens=1, duration_ms=1.0)
    ok, reason = metrics.tier2_ready()
    assert ok is True


# ──────────────────────────────────────────────
# 기동 로그 (W6-3 · P14)
# ──────────────────────────────────────────────

def test_startup_log_resolves_flags_once(caplog):
    """플래그는 **기동 시 1회** 해석되고 확정값이 로그에 남는다(78 P14)."""
    load_config.cache_clear()
    with caplog.at_level("INFO", logger="src.observability.investigation_metrics"):
        resolved = metrics.log_investigation_startup(load_config())
    assert resolved["prior_targets_enabled"] is False   # 기본 off
    assert resolved["host_authz_mode"] == "admin_only"
    line = " ".join(r.getMessage() for r in caplog.records)
    assert "호스트 조사 경로 확정" in line
    assert "investigation_enabled=False" in line


def test_startup_log_includes_path_availability():
    """플래그가 켜져 있어도 조사 경로가 off면 조사는 일어나지 않는다 — 둘 다 남긴다."""
    load_config.cache_clear()
    resolved = metrics.log_investigation_startup(load_config())
    assert "fault_diagnosis_enabled" in resolved
    assert "investigation_trigger_enabled" in resolved


def test_startup_log_is_wired_into_graph_build():
    """★ 정의만 있고 호출부가 없으면 무효다(Known Mistakes — 배선까지 grep으로 확인)."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/graph.py").read_text())
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "log_investigation_startup" in called
