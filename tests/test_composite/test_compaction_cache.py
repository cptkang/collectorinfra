"""결정적 2단 축약 · 단기 조사 캐시 (Plan 78 W2-7·8 / Plan 80 WU-17 · SPEC M7 · **Tier 2**).

> **Tier 2 착수 조건은 W6(M2) 완료다**(78 §4.6.2) — 지표 없이 최적화를 쌓지 않는다.
> 이 파일 첫 테스트가 그 전제를 실제로 확인한다.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from src.config import load_config
from src.observability import investigation_metrics as metrics
from src.orchestration import process_query as pq
from src.orchestration.investigation_cache import InvestigationCache, freshness_note


def _raw(name, pid, cpu):
    return {"name": name, "pid": pid, "ppid": 1, "user": "root",
            "p100cpu": cpu, "pcpu": cpu, "pmem": 1.0, "rss": 100, "args": "java"}


class _Result:
    def __init__(self, procs):
        self.processes, self.captured_at = procs, "2026-08-27T12:00:00"


class _Client:
    calls: list = []

    def __init__(self, _cfg):
        pass

    async def list_by_hostname(self, db_id, hostname):
        _Client.calls.append((db_id, hostname))
        return _Result([_raw(f"p{i}", i, 90 - i) for i in range(8)])


async def _noop_resolver(db_id, value, app_config):
    return None


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    _Client.calls = []
    metrics.reset()
    monkeypatch.setattr(pq, "PolestarProcessApiClient", _Client)
    monkeypatch.setattr(pq, "_resolve_db_id", lambda *a, **k: "polestar_gimpo")
    monkeypatch.setattr(pq, "_resolve_canonical_hostname", _noop_resolver)
    pq._inflight_locks.clear()
    pq._snapshot_cache_instance = None
    pq._snapshot_cache_ttl = None
    yield
    load_config.cache_clear()
    pq._snapshot_cache_instance = None
    pq._snapshot_cache_ttl = None
    metrics.reset()


def _cfg(top_n=4):
    return SimpleNamespace(alarm=SimpleNamespace(
        get_process_api_base_url=lambda db_id: "http://proc.local",
        process_top_n=top_n,
    ))


def _iso(*hosts):
    return {"parsed_requirements": {"filter_conditions": [
        {"field": "hostname", "value": h} for h in hosts]}, "conversation_context": {}}


async def _run(*hosts, top_n=4):
    return await pq.run_process_query(
        {"sub_query": "프로세스 보여줘"}, _iso(*hosts), llm=None, app_config=_cfg(top_n)
    )


# ──────────────────────────────────────────────
# Tier 규율 (78 §4.6.2)
# ──────────────────────────────────────────────

def test_tier1_metrics_exist_before_tier2():
    """★ 압축·캐시의 이득을 잴 **지표가 먼저 있어야** Tier 2가 성립한다."""
    snap = metrics.snapshot()
    assert "compaction" in snap and "cache" in snap
    ok, _ = metrics.tier2_ready()
    assert ok is False          # 관측 0건에서는 착수 불가로 판정된다
    metrics.record_investigation(tokens=1, duration_ms=1.0)
    assert metrics.tier2_ready()[0] is True


# ──────────────────────────────────────────────
# 결정적 2단 축약 (W2-7)
# ──────────────────────────────────────────────

def test_per_host_limit_shrinks_with_host_count():
    """호스트가 늘수록 호스트당 노출을 줄인다 — 총 노출을 대략 `top_n × 3` 안으로 묶는다."""
    assert pq._compact_per_host_limit(4, 1) == 4      # 단일은 축소 없음
    assert pq._compact_per_host_limit(4, 3) == 4
    assert pq._compact_per_host_limit(4, 6) == 2
    assert pq._compact_per_host_limit(4, 100) == 1    # ★ 최소 1행 — 0이면 "조사 안 됨"으로 오인


def test_compaction_is_deterministic_no_llm():
    """LLM 압축은 미채택이다(§4.5-④) — 같은 입력이면 같은 결과여야 재현·감사가 된다."""
    assert all(
        pq._compact_per_host_limit(5, 7) == pq._compact_per_host_limit(5, 7)
        for _ in range(5)
    )


@pytest.mark.asyncio
async def test_truncated_rows_are_reported_per_host():
    """★ 절단이 발생하면 **호스트별 절단 행 수**가 결과에 존재한다(조용한 절단 금지)."""
    res = await _run(*[f"h{i}" for i in range(6)], top_n=4)
    comp = res["process_query"]["compaction"]
    assert comp["per_host_shown"] == 2
    assert all(v == 6 for v in comp["per_host_truncated"].values())   # 8행 중 2행만 노출
    assert len(comp["per_host_truncated"]) == 6


@pytest.mark.asyncio
async def test_full_rows_are_preserved():
    """★ **원문 전량 보존이 필수 조건**이다(§3.4.3-⑤).

    상위 N 선별은 정밀도 우선이라 문서의 재현율 우선 권고와 어긋난다 — 그 편차를 상쇄하는
    조건이 CSV·감사에 전량을 남겨 손실을 복구 가능하게 두는 것이다.
    """
    res = await _run("h1", "h2", "h3", "h4", "h5", "h6", top_n=4)
    assert len(res["query_results"]) == 48          # 6 호스트 × 8행 전량
    assert res["process_query"]["compaction"]["rows_preserved"] == 48
    assert len(res["organized_data"]["rows"]) == 12  # 채팅은 6 × 2행


@pytest.mark.asyncio
async def test_compaction_loss_is_recorded_in_metrics():
    """압축 손실이 Tier 2 지표에 남는다 — 이득을 대조할 재료다(W6-4)."""
    await _run("h1", "h2", "h3", "h4", "h5", "h6", top_n=4)
    snap = metrics.snapshot()["compaction"]
    assert snap["rows_truncated"] == 36
    assert len(snap["per_host_truncated"]) == 6


# ──────────────────────────────────────────────
# 단기 조사 캐시 (W2-8)
# ──────────────────────────────────────────────

def test_cache_is_off_by_default():
    """★ 회귀 0 — 기본은 **꺼져 있다**. "실시간" 조회가 조용히 캐시되면 안 된다(SPEC C-6)."""
    load_config.cache_clear()
    assert load_config().composite.snapshot_ttl_seconds == 0
    assert pq._snapshot_cache() is None


@pytest.mark.asyncio
async def test_ttl_hit_skips_the_collector(monkeypatch):
    """★ TTL 내 재조회는 **수집기를 호출하지 않는다**."""
    monkeypatch.setenv("COMPOSITE_SNAPSHOT_TTL_SECONDS", "60")
    load_config.cache_clear()
    pq._snapshot_cache_instance = None
    await _run("h1")
    assert len(_Client.calls) == 1
    await _run("h1")
    assert len(_Client.calls) == 1, "TTL 내인데 수집기를 다시 불렀다"


@pytest.mark.asyncio
async def test_cache_hit_is_visible_to_the_user(monkeypatch):
    """★ 히트 시 **수집 시각을 응답에 명시**한다 — 실시간 오인 방지(침묵 금지)."""
    monkeypatch.setenv("COMPOSITE_SNAPSHOT_TTL_SECONDS", "60")
    load_config.cache_clear()
    pq._snapshot_cache_instance = None
    await _run("h1")
    res = await _run("h1")
    assert "스냅샷 재사용" in res["organized_data"]["summary"]
    assert "2026-08-27T12:00:00" in res["organized_data"]["summary"]


@pytest.mark.asyncio
async def test_expired_entry_is_recollected(monkeypatch):
    """TTL 경과 후에는 재수집한다."""
    monkeypatch.setenv("COMPOSITE_SNAPSHOT_TTL_SECONDS", "1")
    load_config.cache_clear()
    pq._snapshot_cache_instance = None
    cache = pq._snapshot_cache()
    await _run("h1")
    assert len(_Client.calls) == 1
    cache.clear()                     # TTL 경과와 동등한 상태
    await _run("h1")
    assert len(_Client.calls) == 2


def test_expired_keys_are_swept_not_just_ignored():
    """★ in-memory dict는 값 bound뿐 아니라 **키 만료 sweep**도 필요하다(Known Mistakes).

    읽을 때만 지우면 다시 조회되지 않는 키가 영원히 남아 dict가 단조 증가한다.
    """
    c = InvestigationCache(ttl_seconds=0.02)
    for i in range(50):
        c.put("db", f"h{i}", "cpu", i)
    assert len(c) == 50
    time.sleep(0.03)
    c.put("db", "새키", "cpu", 1)      # 다른 키를 넣는 것만으로 만료분이 정리된다
    assert len(c) == 1


def test_entry_count_is_bounded():
    """항목 상한을 넘으면 가장 오래된 것부터 버린다."""
    c = InvestigationCache(ttl_seconds=60, max_entries=10)
    for i in range(50):
        c.put("db", f"h{i}", "cpu", i)
    assert len(c) == 10


def test_cache_metrics_record_hit_age():
    """히트율만 보면 "오래된 값 재사용"과 "TTL 적정"이 구분되지 않는다 — 나이를 함께 센다."""
    metrics.reset()
    c = InvestigationCache(ttl_seconds=60)
    c.put("db", "h", "cpu", 1, captured_at="2026-08-27T12:00:00")
    assert c.get("db", "h", "cpu") is not None
    assert c.get("db", "nope", "cpu") is None
    snap = metrics.snapshot()["cache"]
    assert snap["hits"] == 1 and snap["misses"] == 1
    metrics.reset()


def test_freshness_note_states_age_and_time():
    c = InvestigationCache(ttl_seconds=60)
    c.put("db", "h", "cpu", 1, captured_at="2026-08-27T12:00:00")
    note = freshness_note(c.get("db", "h", "cpu"))
    assert "수집 시각" in note and "스냅샷 재사용" in note


def test_monotonic_clock_is_used():
    """벽시계는 NTP 보정으로 뒤로 갈 수 있다 — TTL 판정이 뒤집히면 안 된다."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/orchestration/investigation_cache.py").read_text())
    used = {
        n.func.attr for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "monotonic" in used
    assert "time" not in used, "time.time()은 뒤로 갈 수 있다"
