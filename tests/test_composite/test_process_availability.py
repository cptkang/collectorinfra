"""프로세스 조회 가용성 사전 판정 배선 (Plan 81 T4~T6 · D-175).

고정하는 것:
    ① **차단 시 프로세스 API가 호출되지 않는다** — 성공할 수 없는 호출을 생략하는 것이 목적이다.
    ② **오안내 제거** — 가용성 비정상 응답에 "잠시 후 다시 시도"가 없다(§1.1 ① 직접 수정).
    ③ **거짓 정상 서술 차단** — `is_sufficient=False`로 반환된다(§1.1 ②).
    ④ **단일·fan-out 대칭** — 두 경로 모두 판정하고 같은 문구 규약을 쓴다.
    ⑤ **판정 off = 비트 동일** — 플래그를 끄면 종전 반환 형태·동작 그대로다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from noise_gate.infrastructure.polestar_hostname_resolver import HostLookup
from src.config import load_config
from src.domain.host_availability import judge_availability
from src.orchestration import process_query as pq


def _raw_process(name, pid, cpu) -> dict:
    return {"name": name, "pid": pid, "ppid": 1, "user": "root",
            "p100cpu": cpu, "pcpu": cpu, "pmem": 1.0, "rss": 100,
            "args": "java -jar app.jar"}


class _FakeResult:
    def __init__(self, procs, captured_at="2026-08-28T12:00:00"):
        self.processes, self.captured_at = procs, captured_at


class _FakeClient:
    calls: list[tuple[str, str]] = []

    def __init__(self, _alarm_cfg):
        pass

    async def list_by_hostname(self, db_id, hostname):
        _FakeClient.calls.append((db_id, hostname))
        return _FakeResult([_raw_process("java", 1, 91.0), _raw_process("nginx", 2, 12.0)])


#: 대상별 판정 각본 — hostname → HostLookup
_SCRIPT: dict[str, HostLookup] = {}
#: 배치 조회 호출 횟수(왕복 수 단언용)
_BATCH_CALLS: list[list[str]] = []


def _lookup_for(value: str) -> HostLookup:
    return _SCRIPT.get(value, HostLookup(value, value, judge_availability(avail_status=0)))


@pytest.fixture(autouse=True)
def wired(monkeypatch):
    _FakeClient.calls = []
    _SCRIPT.clear()
    _BATCH_CALLS.clear()

    async def _fake_resolve(db_id, value, app_config):
        return _lookup_for(value)

    async def _fake_batch(db_id, values, app_config):
        _BATCH_CALLS.append(list(values))
        return {v: _lookup_for(v) for v in values}

    monkeypatch.setattr(pq, "PolestarProcessApiClient", _FakeClient)
    monkeypatch.setattr(pq, "_resolve_db_id", lambda *a, **k: "polestar_gimpo")
    monkeypatch.setattr(pq, "_resolve_target_lookup", _fake_resolve)
    monkeypatch.setattr(pq, "_lookup_targets", _fake_batch)
    pq._inflight_locks.clear()
    load_config.cache_clear()
    yield
    load_config.cache_clear()


@pytest.fixture
def precheck_off(monkeypatch):
    monkeypatch.setenv("COMPOSITE_AVAILABILITY_PRECHECK_ENABLED", "false")
    load_config.cache_clear()
    yield
    load_config.cache_clear()


@pytest.fixture
def block_off(monkeypatch):
    monkeypatch.setenv("COMPOSITE_AVAILABILITY_BLOCK_ON_UNAVAILABLE", "false")
    load_config.cache_clear()
    yield
    load_config.cache_clear()


def _cfg():
    return SimpleNamespace(
        alarm=SimpleNamespace(
            get_process_api_base_url=lambda db_id: "http://proc.local",
            process_top_n=5,
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


def _down(hostname="svweb001"):
    _SCRIPT[hostname] = HostLookup(
        hostname, hostname, judge_availability(avail_status=1, as_of="2026-08-28 09:00:00")
    )


# ──────────────────────────────────────────────
# 단일 대상 (T4·T6)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
class TestSingleTarget:
    async def test_가용성_비정상이면_프로세스_API를_부르지_않는다(self):
        _down()
        res = await _run("svweb001")
        assert _FakeClient.calls == [], "성공할 수 없는 호출을 생략하는 것이 이 기능의 목적이다"

    async def test_사유와_확인_시각을_사용자에게_제공한다(self):
        _down()
        summary = (await _run("svweb001"))["organized_data"]["summary"]
        assert "비정상(중지/통신이상)" in summary
        assert "2026-08-28 09:00:00" in summary
        assert "svweb001" in summary

    async def test_재시도_유도_오안내가_사라진다(self):
        """§1.1 ① — 전원이 꺼진 서버에 '잠시 후 다시 시도'는 오안내다."""
        _down()
        summary = (await _run("svweb001"))["organized_data"]["summary"]
        assert "잠시 후 다시 시도" not in summary

    async def test_거짓_정상_서술을_막는다(self):
        """§1.1 ② — 0건을 정상 결과로 서술하던 경로 차단."""
        res = await _run("svweb001")
        _down()
        res = await _run("svweb001")
        assert res["organized_data"]["is_sufficient"] is False
        assert res["query_results"] == []

    async def test_판정_메타가_결과에_실린다(self):
        _down()
        meta = (await _run("svweb001"))["process_query"]
        assert meta["availability"]["state"] == "unavailable"
        assert meta["availability"]["reason"] == "avail_status_down"
        assert meta["reason"] == "host_unavailable"

    async def test_점검_상태는_조회하되_경고를_앞에_붙인다(self):
        """G-5 확정 — 점검 중이어도 서버는 살아 있을 수 있다."""
        _SCRIPT["svweb001"] = HostLookup(
            "svweb001", "svweb001", judge_availability(avail_status=0, is_maintenance=1)
        )
        res = await _run("svweb001")
        assert _FakeClient.calls, "점검 상태는 차단하지 않는다"
        assert res["organized_data"]["summary"].startswith("'svweb001' 서버는 점검(maintenance)")
        assert res["organized_data"]["is_sufficient"] is True

    async def test_판정_불가는_조회를_막지_않는다(self):
        """fail-open — 조회 실패·미등록으로 정상 조회를 잃으면 안 된다."""
        _SCRIPT["svweb001"] = HostLookup(None, None, judge_availability(lookup_failed=True))
        res = await _run("svweb001")
        assert _FakeClient.calls
        assert res["organized_data"]["is_sufficient"] is True

    async def test_미등록_대상은_안내하되_조회는_시도한다(self):
        _SCRIPT["svweb001"] = HostLookup(None, None, judge_availability(found=False))
        res = await _run("svweb001")
        assert _FakeClient.calls
        assert "찾지 못했습니다" in res["organized_data"]["summary"]

    async def test_관찰_모드는_문구만_붙이고_조회한다(self, block_off):
        _down()
        res = await _run("svweb001")
        assert _FakeClient.calls, "BLOCK_ON_UNAVAILABLE=false면 차단하지 않는다"
        assert "비정상(중지/통신이상)" in res["organized_data"]["summary"]


# ──────────────────────────────────────────────
# 다대상 fan-out (T5·T6) — 단일 경로와 대칭
# ──────────────────────────────────────────────

@pytest.mark.asyncio
class TestFanout:
    async def test_대상이_여럿이어도_판정_조회는_한_번이다(self):
        res = await _run("svweb001", "svweb002")
        assert len(_BATCH_CALLS) == 1
        assert set(_BATCH_CALLS[0]) == {"svweb001", "svweb002"}
        assert res["process_query"]["succeeded_count"] == 2

    async def test_비정상_대상만_수집에서_빠진다(self):
        _down("svweb002")
        res = await _run("svweb001", "svweb002")
        called = [h for _db, h in _FakeClient.calls]
        assert called == ["svweb001"]
        assert res["process_query"]["succeeded_count"] == 1
        assert res["process_query"]["failed_count"] == 1

    async def test_실패가_사유별로_분해된다(self):
        """§1.1 ③ — 재시도 가치가 있는 실패와 없는 실패를 구분한다."""
        _down("svweb002")
        summary = (await _run("svweb001", "svweb002"))["organized_data"]["summary"]
        assert "실패 사유:" in summary
        assert "가용성 비정상(재시도해도 동일) 1건" in summary

    async def test_실패_항목에_사유_코드가_실린다(self):
        _down("svweb002")
        failed = (await _run("svweb001", "svweb002"))["process_query"]["failed"]
        assert failed[0]["reason"] == "host_unavailable"

    async def test_판정_메타가_대상별로_실린다(self):
        _down("svweb002")
        meta = (await _run("svweb001", "svweb002"))["process_query"]["availability"]
        assert meta["svweb001"]["state"] == "available"
        assert meta["svweb002"]["state"] == "unavailable"

    async def test_점검_안내는_수집_성공_요약에도_노출된다(self):
        _SCRIPT["svweb002"] = HostLookup(
            "svweb002", "svweb002", judge_availability(avail_status=0, is_maintenance=1)
        )
        summary = (await _run("svweb001", "svweb002"))["organized_data"]["summary"]
        assert "점검(maintenance)" in summary

    async def test_전_대상_비정상이면_수집_0건이지만_사유가_남는다(self):
        _down("svweb001")
        _down("svweb002")
        res = await _run("svweb001", "svweb002")
        assert _FakeClient.calls == []
        assert res["organized_data"]["is_sufficient"] is False
        assert "가용성 비정상(재시도해도 동일) 2건" in res["organized_data"]["summary"]


# ──────────────────────────────────────────────
# 판정 off = 회귀 0 (T3·T4)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
class TestPrecheckOffIsBitIdentical:
    async def test_off면_비정상_대상도_종전대로_조회한다(self, precheck_off):
        _down()
        res = await _run("svweb001")
        assert _FakeClient.calls, "판정을 끄면 종전 동작(호출 후 결과 판단) 그대로다"
        assert res["organized_data"]["is_sufficient"] is True

    async def test_off면_판정_키가_붙지_않는다(self, precheck_off):
        _down()
        meta = (await _run("svweb001"))["process_query"]
        assert set(meta) == {
            "db_id", "server_name", "hostname", "total_count", "shown_count",
            "captured_at", "metric",
        }

    async def test_off면_배치_판정_조회를_돌리지_않는다(self, precheck_off):
        await _run("svweb001", "svweb002")
        assert _BATCH_CALLS == [], "판정이 꺼져 있으면 대상별 해소가 종전 경로 그대로다"

    async def test_off면_fan_out_실패_요약에_사유_분해가_없다(self, precheck_off):
        res = await _run("svweb001", "svweb002")
        assert "실패 사유:" not in res["organized_data"]["summary"]
