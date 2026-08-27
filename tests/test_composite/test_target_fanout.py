"""N-대상 fan-out (Plan 78 W2-1~6 / Plan 80 WU-12 · SPEC M3 · G3 해소).

**단일 대상 경로의 회귀 0**이 가장 중요한 수용 기준이다 — 요약 문구·반환 키가 바뀌면
`output_generator`·CSV 다운로드가 함께 깨진다(D-047 규약).

라우팅 결과·relevance_score·의도 분류는 단언하지 않는다(3-A 조건).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.config import load_config
from src.orchestration import process_query as pq


def _raw_process(name, pid, cpu) -> dict:
    """폴스타 프로세스 API의 `data.list` 원시 항목 형태.

    `select_top_processes`는 **dict 목록**을 받는다(객체가 아니다 — 실측 2026-08-27).
    """
    return {"name": name, "pid": pid, "ppid": 1, "user": "root",
            "p100cpu": cpu, "pcpu": cpu, "pmem": 1.0, "rss": 100,
            "args": "java -jar app.jar"}


class _FakeResult:
    def __init__(self, procs, captured_at="2026-08-27T12:00:00"):
        self.processes, self.captured_at = procs, captured_at


class _FakeClient:
    """`PolestarProcessApiClient` 대역 — 호스트별 응답·지연·실패를 각본으로 준다."""

    script: dict = {}
    calls: list[tuple[str, str]] = []

    def __init__(self, _alarm_cfg):
        pass

    async def list_by_hostname(self, db_id, hostname):
        _FakeClient.calls.append((db_id, hostname))
        entry = _FakeClient.script.get(hostname, "ok")
        if entry == "none":
            return None
        if entry == "boom":
            raise RuntimeError("수집 실패")
        if isinstance(entry, (int, float)):
            await asyncio.sleep(entry)
        return _FakeResult([_raw_process("java", 1, 91.0), _raw_process("nginx", 2, 12.0)])


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    """프로세스 API·hostname 해소·DB 선택을 결정적 대역으로 바꾼다."""
    _FakeClient.script = {}
    _FakeClient.calls = []
    monkeypatch.setattr(pq, "PolestarProcessApiClient", _FakeClient)
    monkeypatch.setattr(pq, "_resolve_db_id", lambda *a, **k: "polestar_gimpo")
    monkeypatch.setattr(pq, "_resolve_canonical_hostname", _noop_resolver)
    pq._inflight_locks.clear()
    yield
    load_config.cache_clear()


async def _noop_resolver(db_id, value, app_config):
    return None  # 입력 식별자를 그대로 hostname으로 쓴다


@pytest.fixture
def targets_on(monkeypatch):
    monkeypatch.setenv("COMPOSITE_PRIOR_TARGETS_ENABLED", "true")
    load_config.cache_clear()
    yield
    load_config.cache_clear()


def _cfg():
    cfg = load_config()
    return SimpleNamespace(
        alarm=SimpleNamespace(
            get_process_api_base_url=lambda db_id: "http://proc.local",
            process_top_n=1,
        )
    )


def _iso(*hostnames):
    return {
        "parsed_requirements": {
            "filter_conditions": [{"field": "hostname", "value": h} for h in hostnames]
        },
        "conversation_context": {},
    }


async def _run(*hostnames, sub_query="프로세스 보여줘"):
    return await pq.run_process_query(
        {"sub_query": sub_query}, _iso(*hostnames), llm=None, app_config=_cfg()
    )


# ──────────────────────────────────────────────
# G3 해소 (W2-1)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_three_targets_are_all_investigated():
    """★ T-G3 — 3개 입력 → **3개 조사**. 1개로 절단되지 않는다."""
    res = await _run("svweb001", "svweb002", "svbatch009")
    assert res["process_query"]["target_count"] == 3
    assert res["process_query"]["succeeded_count"] == 3
    assert {h for _, h in _FakeClient.calls} == {"svweb001", "svweb002", "svbatch009"}


@pytest.mark.asyncio
async def test_reduce_reports_counts_and_truncation():
    """W2-4 — 응답에 **대상 수 / 성공 / 실패 / 절단 여부·수**가 전부 있다."""
    res = await _run("h1", "h2")
    pqi = res["process_query"]
    for key in ("target_count", "succeeded_count", "failed_count",
                "truncated", "truncated_count"):
        assert key in pqi, f"{key}가 없으면 부분 결과를 전체로 오인한다"


@pytest.mark.asyncio
async def test_rows_carry_host_attribution():
    """병합 표의 각 행이 **어느 호스트의 것인지** 드러난다(N-대상 표의 최소 조건)."""
    res = await _run("h1", "h2")
    assert {r["hostname"] for r in res["query_results"]} == {"h1", "h2"}


# ──────────────────────────────────────────────
# 부분 실패 격리 · 타임아웃 (W2-2·3)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_one_failure_does_not_kill_the_rest():
    """★ 1개 실패 시 나머지 2개가 반환된다(개별 try/except)."""
    _FakeClient.script = {"h2": "boom"}
    res = await _run("h1", "h2", "h3")
    assert res["process_query"]["succeeded_count"] == 2
    assert res["process_query"]["failed_count"] == 1


@pytest.mark.asyncio
async def test_failure_reason_is_exposed_to_the_user():
    """★ 실패 사유가 **응답 요약에 노출**된다 — 침묵 폴백 금지(80 §5.4-④)."""
    _FakeClient.script = {"h2": "none"}
    res = await _run("h1", "h2")
    assert "h2" in res["organized_data"]["summary"]
    assert "실패 대상" in res["organized_data"]["summary"]
    assert res["process_query"]["failed"][0]["target"] == "h2"


@pytest.mark.asyncio
async def test_per_target_timeout_isolates_the_slow_host(monkeypatch):
    """대상별 타임아웃 — 느린 호스트 하나가 나머지를 붙들지 않는다."""
    monkeypatch.setenv("COMPOSITE_TARGET_TIMEOUT_SECONDS", "0.05")
    load_config.cache_clear()
    _FakeClient.script = {"slow": 5.0}
    res = await _run("fast", "slow")
    assert res["process_query"]["succeeded_count"] == 1
    assert "타임아웃" in res["process_query"]["failed"][0]["error"]


@pytest.mark.asyncio
async def test_total_timeout_guards_the_whole_fanout(monkeypatch):
    """★ 전체 타임아웃 — per-call 타임아웃만으로는 무력화된다(Known Mistakes)."""
    monkeypatch.setenv("COMPOSITE_TOTAL_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setenv("COMPOSITE_TARGET_TIMEOUT_SECONDS", "30")
    load_config.cache_clear()
    _FakeClient.script = {"a": 5.0, "b": 5.0}
    res = await _run("a", "b")
    assert res["process_query"]["succeeded_count"] == 0
    assert all("전체 타임아웃" in f["error"] for f in res["process_query"]["failed"])


@pytest.mark.asyncio
async def test_concurrency_is_bounded(monkeypatch):
    """동시 조사 수 상한이 실제로 걸린다 — 대상 호스트 부하 관점의 상한이기도 하다."""
    monkeypatch.setenv("COMPOSITE_FANOUT_CONCURRENCY", "2")
    load_config.cache_clear()
    peak = {"now": 0, "max": 0}
    orig = pq._collect_one_target

    async def _tracked(*args, **kwargs):
        peak["now"] += 1
        peak["max"] = max(peak["max"], peak["now"])
        try:
            await asyncio.sleep(0.02)
            return await orig(*args, **kwargs)
        finally:
            peak["now"] -= 1

    monkeypatch.setattr(pq, "_collect_one_target", _tracked)
    await _run("a", "b", "c", "d", "e")
    assert peak["max"] <= 2


# ──────────────────────────────────────────────
# 부하 가드 (W2-6)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_host_is_serialized():
    """★ 같은 호스트에 대한 동시 조사가 **1건으로 직렬화**된다.

    대표 시나리오가 *이미 포화된 서버*를 조사하는 것이다 — 중복 조사가 장애를 악화시키면
    계획의 목적 자체가 무너진다(78 W2-6 판단 근거).
    """
    overlap = {"now": 0, "max": 0}

    class _Tracking(_FakeClient):
        async def list_by_hostname(self, db_id, hostname):
            overlap["now"] += 1
            overlap["max"] = max(overlap["max"], overlap["now"])
            try:
                await asyncio.sleep(0.02)
                return await super().list_by_hostname(db_id, hostname)
            finally:
                overlap["now"] -= 1

    pq.PolestarProcessApiClient = _Tracking
    try:
        await asyncio.gather(_run("dup"), _run("dup"), _run("dup"))
    finally:
        pq.PolestarProcessApiClient = _FakeClient
    assert overlap["max"] == 1


def test_inflight_lock_dict_is_bounded():
    """in-memory dict는 값 bound뿐 아니라 **키 상한**도 필요하다(Known Mistakes)."""
    pq._inflight_locks.clear()
    for i in range(pq._MAX_INFLIGHT_KEYS + 200):
        pq._inflight_lock("db", f"host{i}")
    assert len(pq._inflight_locks) <= pq._MAX_INFLIGHT_KEYS + 1


# ──────────────────────────────────────────────
# 단일 대상 회귀 0 (W2-1 단서)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_target_keeps_legacy_shape():
    """★ 회귀 0 — 대상이 하나면 **종전 반환 키·요약 문구 그대로**다.

    `output_generator`·CSV 다운로드가 이 형태에 의존한다(D-047 규약).
    """
    res = await _run("svweb001")
    pqi = res["process_query"]
    assert set(pqi) == {
        "db_id", "server_name", "hostname", "total_count", "shown_count",
        "captured_at", "metric",
    }
    assert pqi["server_name"] == "svweb001"
    assert "서버 'svweb001'의 현재 실행 중 프로세스" in res["organized_data"]["summary"]
    # 채팅=상위 N, CSV=전량 (D-047)
    assert len(res["organized_data"]["rows"]) == 1
    assert len(res["query_results"]) == 2


@pytest.mark.asyncio
async def test_single_target_failure_keeps_legacy_message():
    """단일 대상 실패도 종전 안내 문구를 유지한다."""
    _FakeClient.script = {"svweb001": "none"}
    res = await _run("svweb001")
    assert "실시간 프로세스를 조회하지 못했습니다" in res["organized_data"]["summary"]
    assert res["organized_data"]["is_sufficient"] is False


@pytest.mark.asyncio
async def test_no_target_still_returns_graceful_guidance():
    """대상 미식별은 종전대로 안내로 끝난다(없는 테이블 조회로 폴백하지 않는다)."""
    res = await pq.run_process_query(
        {"sub_query": "프로세스"}, _iso(), llm=None, app_config=_cfg()
    )
    assert "서버명" in res["organized_data"]["summary"]
    assert _FakeClient.calls == []


# ──────────────────────────────────────────────
# 부하 가드 요구 전달 (W2-6 · W3-6 — 구현은 sre_agent 소관)
# ──────────────────────────────────────────────

def test_load_guard_requirements_are_declared_in_contract():
    """★ 78은 부하 가드를 **구현하지 않고 요구로 전달**한다(D-118 경계) — 계약이 존재해야 한다.

    문서가 없으면 "전달했다"는 주장이 검증 불가능해진다. 4항이 모두 명시돼 있는지 본다.
    """
    import pathlib

    doc = pathlib.Path("docs/25_host_investigation_load_guard.md").read_text()
    for token in ("nice", "timeout", "-n 1", "중복 동시 조사"):
        assert token in doc, f"부하 가드 요구 '{token}'이 계약 문서에 없다"
    # 구현 주체가 명시돼야 경계가 성립한다.
    assert "sre_agent" in doc


def test_load_guard_l4_is_actually_enforced_in_core():
    """L-4(동일 호스트 중복 조사 금지)는 본체도 지킨다 — 문서만 있고 코드가 없으면 무효다."""
    assert hasattr(pq, "_inflight_lock")
    assert pq._inflight_lock("db", "h") is pq._inflight_lock("db", "h")
    assert pq._inflight_lock("db", "h") is not pq._inflight_lock("db", "h2")
