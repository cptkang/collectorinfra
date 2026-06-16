"""영향 프로세스 보강 — enricher 통합 + 그래프 + analyzer 주입(Plan 47-1) 테스트.

게이팅(비대상 알람/해소/미매핑/미주입), API 실패 시 process_snapshot=None으로 분석 계속,
history와 동시 실행, hostname 사용(server_name 아님), analyzer process_section 렌더·주입,
프로세스 API 클라이언트(URL 인코딩, 비200/네트워크 degradation, captured_at 파싱)를 검증한다.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from src.alarm.application.nodes.alarm_context_enricher import (
    alarm_context_enricher_node,
    enrich_processes,
)
from src.alarm.domain.alarm import AlarmAnalysisResult, ProcessInfo, ProcessSnapshot
from src.alarm.infrastructure.polestar_process_api import (
    ProcessApiResult,
)
from src.config import AlarmConfig
from tests.test_alarm_enricher import FakeRepo, make_alarm_cfg
from tests.test_alarm_pattern import REF, make_entry, make_event


# ─── Fakes ────────────────────────────────────────────────────────────────────

class FakeProcessClient:
    """PolestarProcessApiClient 대역 — base_url 매핑 + 호출 인자 기록."""

    def __init__(self, result=None, mapped=True, delay=0.0, error=None):
        self.result = result
        self.mapped = mapped
        self.delay = delay
        self.error = error
        self.calls = []  # (db_id, hostname)

    def get_base_url(self, db_id):
        return "http://polestar.kbonecloud.com" if self.mapped else None

    async def list_by_hostname(self, db_id, hostname):
        self.calls.append((db_id, hostname))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


def _result(captured_at=None, procs=None):
    return ProcessApiResult(captured_at=captured_at, processes=procs or [])


def _proc_raw(name, pid, pmem=0.0, p100cpu=0.0, args=""):
    return {
        "name": name, "pid": pid, "ppid": 1, "user": "appusr",
        "pmem": pmem, "p100cpu": p100cpu, "pcpu": p100cpu / 4.0, "rss": 1024, "args": args,
    }


def lc_config(cfg, repo=None, process_client=None):
    return {
        "configurable": {
            "app_config": cfg,
            "history_repo": repo,
            "history_redis": None,
            "process_client": process_client,
        }
    }


# ─── enrich_processes 게이팅 ────────────────────────────────────────────────────

class TestProcessGating:
    async def test_disabled_returns_none(self):
        cfg = make_alarm_cfg(process_enrich_enabled=False)
        client = FakeProcessClient(result=_result())
        snap = await enrich_processes(make_event(), cfg, client)
        assert snap is None
        assert client.calls == []

    async def test_no_client_returns_none(self):
        snap = await enrich_processes(make_event(), make_alarm_cfg(), None)
        assert snap is None

    async def test_clear_alarm_skips(self):
        client = FakeProcessClient(result=_result())
        snap = await enrich_processes(
            make_event(severity=0, is_clear=True), make_alarm_cfg(), client
        )
        assert snap is None
        assert client.calls == []

    async def test_disk_alarm_skips(self):
        client = FakeProcessClient(result=_result())
        ev = make_event(resource_type="server.Disks", alarm_name="디스크 사용률")
        snap = await enrich_processes(ev, make_alarm_cfg(), client)
        assert snap is None
        assert client.calls == []

    async def test_unmapped_db_id_skips(self):
        client = FakeProcessClient(result=_result(), mapped=False)
        snap = await enrich_processes(make_event(), make_alarm_cfg(), client)
        assert snap is None
        assert client.calls == []

    async def test_api_returns_none_degrades(self):
        client = FakeProcessClient(result=None)  # 비200/네트워크 오류 등
        snap = await enrich_processes(make_event(), make_alarm_cfg(), client)
        assert snap is None


class TestProcessSnapshotBuild:
    async def test_uses_hostname_not_server_name(self):
        # ★ 조회 키는 hostname (server_name 아님) — Plan 47-1 §2
        client = FakeProcessClient(result=_result(procs=[_proc_raw("java", 1, pmem=30.0)]))
        ev = make_event(server_name="cop0-aisapd02", hostname="saisvd01")
        await enrich_processes(ev, make_alarm_cfg(), client)
        assert client.calls == [(ev.db_id, "saisvd01")]

    async def test_memory_snapshot_top_n_and_metric(self):
        procs = [
            _proc_raw("java", 1, pmem=38.0, p100cpu=12.0),
            _proc_raw("python3", 2, pmem=9.0),
            _proc_raw("postgres", 3, pmem=7.0),
        ]
        client = FakeProcessClient(
            result=_result(captured_at=datetime(2026, 6, 5, 9, 33, 59), procs=procs)
        )
        ev = make_event(resource_type="server.Server", alarm_name="메모리 사용률 임계 초과")
        snap = await enrich_processes(ev, make_alarm_cfg(process_top_n=2), client)
        assert snap is not None
        assert snap.alarm_kind == "memory"
        assert snap.total_count == 3
        assert len(snap.top) == 2
        assert snap.top[0].name == "java"
        assert snap.captured_at == datetime(2026, 6, 5, 9, 33, 59)
        assert snap.source_host == ev.hostname

    async def test_cpu_snapshot_uses_p100cpu(self):
        procs = [_proc_raw("a", 1, p100cpu=10.0), _proc_raw("b", 2, p100cpu=90.0)]
        client = FakeProcessClient(result=_result(procs=procs))
        ev = make_event(resource_type="server.Cpus", alarm_name="CPU 사용률 임계 초과")
        snap = await enrich_processes(ev, make_alarm_cfg(), client)
        assert snap.alarm_kind == "cpu"
        assert snap.top[0].name == "b"

    async def test_args_masked_in_snapshot(self):
        procs = [_proc_raw("db", 1, pmem=50.0, args="--password=leakme -jar a.jar")]
        client = FakeProcessClient(result=_result(procs=procs))
        ev = make_event(resource_type="server.Server", alarm_name="메모리 사용률")
        snap = await enrich_processes(ev, make_alarm_cfg(), client)
        assert "leakme" not in snap.top[0].args
        assert "***" in snap.top[0].args


# ─── 노드 통합: history ∥ process 동시 실행 + 독립 degradation ──────────────────

class TestEnricherNodeProcessIntegration:
    def _app_cfg(self, **kw):
        return SimpleNamespace(alarm=make_alarm_cfg(**kw))

    async def test_node_returns_both_keys(self):
        procs = [_proc_raw("java", 1, p100cpu=80.0)]
        client = FakeProcessClient(result=_result(procs=procs))
        repo = FakeRepo(entries=[make_entry(REF - timedelta(hours=24 * i)) for i in range(1, 4)])
        ev = make_event(resource_type="server.Cpus", alarm_name="CPU 사용률")
        out = await alarm_context_enricher_node(
            {"alarm_event": ev}, lc_config(self._app_cfg(), repo, client)
        )
        assert out["history_stats"] is not None
        assert out["process_snapshot"] is not None
        assert out["process_snapshot"].alarm_kind == "cpu"

    async def test_process_failure_does_not_block_history(self):
        client = FakeProcessClient(error=RuntimeError("API down"))
        repo = FakeRepo(entries=[make_entry(REF - timedelta(hours=24 * i)) for i in range(1, 4)])
        ev = make_event(resource_type="server.Cpus", alarm_name="CPU 사용률")
        out = await alarm_context_enricher_node(
            {"alarm_event": ev}, lc_config(self._app_cfg(), repo, client)
        )
        assert out["history_stats"] is not None  # 이력은 정상
        assert out["process_snapshot"] is None    # 프로세스만 degradation

    async def test_history_failure_does_not_block_process(self):
        client = FakeProcessClient(result=_result(procs=[_proc_raw("java", 1, p100cpu=50.0)]))
        repo = FakeRepo(error=RuntimeError("DB down"))
        ev = make_event(resource_type="server.Cpus", alarm_name="CPU 사용률")
        out = await alarm_context_enricher_node(
            {"alarm_event": ev}, lc_config(self._app_cfg(), repo, client)
        )
        assert out["history_stats"] is None       # 이력만 degradation
        assert out["process_snapshot"] is not None  # 프로세스는 정상

    async def test_concurrent_execution(self):
        # history와 process가 동시 실행되면 총 지연 ≈ max(둘) (합산 아님)
        client = FakeProcessClient(result=_result(procs=[_proc_raw("a", 1, p100cpu=10.0)]), delay=0.3)
        repo = FakeRepo(entries=[make_entry(REF - timedelta(hours=1))], delay=0.3)
        ev = make_event(resource_type="server.Cpus", alarm_name="CPU 사용률")
        start = time.perf_counter()
        out = await alarm_context_enricher_node(
            {"alarm_event": ev}, lc_config(self._app_cfg(enrich_timeout_seconds=5), repo, client)
        )
        elapsed = time.perf_counter() - start
        assert elapsed < 0.55  # 0.6(합산)보다 충분히 작음 → 동시 실행 확인
        assert out["history_stats"] is not None
        assert out["process_snapshot"] is not None


# ─── analyzer 프로세스 섹션 렌더/주입 ──────────────────────────────────────────

class FakeLLM:
    def __init__(self, content):
        self._content = content
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self._content)


def _snapshot(kind="memory", top=None):
    return ProcessSnapshot(
        alarm_kind=kind,
        captured_at=datetime(2026, 6, 5, 9, 33, 59),
        top=top or [
            ProcessInfo("java", 12345, 1, "appusr", 12.0, 3.0, 38.1, 2048, "-Xmx8g -jar app.jar"),
        ],
        total_count=142,
        source_host="saisvd01",
    )


class TestAnalyzerProcessSection:
    def test_render_process_section(self):
        from src.alarm.application.nodes.alarm_analyzer import _render_process_section

        text = _render_process_section(_snapshot())
        assert "[영향 프로세스 — 메모리 상위" in text
        assert "2026-06-05 09:33:59 기준" in text
        assert "전체 142개" in text
        assert "java" in text and "pid 12345" in text

    def test_render_masked_args_only(self):
        from src.alarm.application.nodes.alarm_analyzer import _render_process_section

        top = [ProcessInfo("db", 1, 0, "pg", 1.0, 0.2, 50.0, 0, "***(마스킹)")]
        text = _render_process_section(_snapshot(top=top))
        assert "***" in text

    async def test_process_section_injected(self, monkeypatch):
        import json
        import src.alarm.application.nodes.alarm_analyzer as analyzer_mod

        llm = FakeLLM(json.dumps({
            "severity_label": "심각", "summary": "s",
            "probable_cause": "java(pid 12345) 메모리 점유",
            "recommended_action": "a",
        }, ensure_ascii=False))
        monkeypatch.setattr(analyzer_mod, "create_llm", lambda cfg: llm)

        cfg = SimpleNamespace(alarm=make_alarm_cfg())
        ev = make_event(resource_type="server.Server", alarm_name="메모리 사용률")
        out = await analyzer_mod.alarm_analyzer_node(
            {"alarm_event": ev, "history_stats": None, "process_snapshot": _snapshot()},
            {"configurable": {"app_config": cfg}},
        )
        user_msg = llm.messages[1]["content"]
        assert "[영향 프로세스 — 메모리 상위" in user_msg
        assert out["analysis_result"].summary == "s"

    async def test_no_process_section_when_none(self, monkeypatch):
        import json
        import src.alarm.application.nodes.alarm_analyzer as analyzer_mod

        llm = FakeLLM(json.dumps({
            "severity_label": "경고", "summary": "s",
            "probable_cause": "c", "recommended_action": "a",
        }, ensure_ascii=False))
        monkeypatch.setattr(analyzer_mod, "create_llm", lambda cfg: llm)

        cfg = SimpleNamespace(alarm=make_alarm_cfg())
        out = await analyzer_mod.alarm_analyzer_node(
            {"alarm_event": make_event(), "history_stats": None, "process_snapshot": None},
            {"configurable": {"app_config": cfg}},
        )
        assert "[영향 프로세스" not in llm.messages[1]["content"]
        assert out["analysis_result"].summary == "s"


# ─── 프로세스 API 클라이언트 (httpx 모킹) ──────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code=200, payload=None, raise_exc=None):
        self.status_code = status_code
        self._payload = payload or {}
        self._raise = raise_exc

    def json(self):
        if self._raise:
            raise self._raise
        return self._payload


class _FakeAsyncClient:
    """httpx.AsyncClient 대역 — get URL 기록 + 지정 응답/예외 반환."""

    last_url = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        _FakeAsyncClient.last_url = url
        resp = _FakeAsyncClient.next_resp
        if isinstance(resp, Exception):
            raise resp
        return resp


class TestProcessApiClient:
    def _client(self):
        from src.alarm.infrastructure.polestar_process_api import PolestarProcessApiClient

        return PolestarProcessApiClient(AlarmConfig())

    async def test_unmapped_db_id_returns_none(self):
        client = self._client()
        out = await client.list_by_hostname("polestar", "h1")
        assert out is None

    async def test_hostname_url_encoded(self, monkeypatch):
        import src.alarm.infrastructure.polestar_process_api as mod

        _FakeAsyncClient.next_resp = _FakeResp(
            payload={"date": "2026-06-05 09:33:59", "data": {"list": [{"name": "x", "pid": 1}]}}
        )
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        out = await self._client().list_by_hostname("polestar_cm_gp", "host name/../x")
        # 인코딩되어 공백·슬래시가 raw로 들어가지 않음 (쿼리 인젝션 방지)
        assert "host name" not in _FakeAsyncClient.last_url
        assert "host%20name" in _FakeAsyncClient.last_url or "host+name" not in _FakeAsyncClient.last_url
        assert out.captured_at == datetime(2026, 6, 5, 9, 33, 59)
        assert len(out.processes) == 1

    async def test_non_200_returns_none(self, monkeypatch):
        import src.alarm.infrastructure.polestar_process_api as mod

        _FakeAsyncClient.next_resp = _FakeResp(status_code=500)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        out = await self._client().list_by_hostname("polestar_cm_gp", "h1")
        assert out is None

    async def test_network_error_returns_none(self, monkeypatch):
        import src.alarm.infrastructure.polestar_process_api as mod

        _FakeAsyncClient.next_resp = ConnectionError("refused")
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        out = await self._client().list_by_hostname("polestar_cm_gp", "h1")
        assert out is None

    async def test_missing_list_returns_empty(self, monkeypatch):
        import src.alarm.infrastructure.polestar_process_api as mod

        _FakeAsyncClient.next_resp = _FakeResp(payload={"date": "2026-06-05 09:33:59"})
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        out = await self._client().list_by_hostname("polestar_cm_gp", "h1")
        assert out is not None
        assert out.processes == []


# ─── Phase 3: 출력 채널 (notifier workb 표 + webhook payload) ───────────────────

def _make_result(**kw):
    defaults = dict(
        alarm_event=make_event(),
        severity_label="심각",
        summary="요약", probable_cause="원인", recommended_action="조치",
        notification_channels=["workb"],
    )
    defaults.update(kw)
    return AlarmAnalysisResult(**defaults)


def _mem_snapshot():
    return ProcessSnapshot(
        alarm_kind="memory",
        captured_at=datetime(2026, 6, 5, 9, 33, 59),
        top=[
            ProcessInfo("java", 12345, 1, "appusr", 12.0, 3.0, 38.1, 2048, "-Xmx8g"),
            ProcessInfo("postgres", 3401, 1, "pgsql", 0.4, 0.1, 7.8, 1024, "***"),
        ],
        total_count=142,
        source_host="saisvd01",
    )


class TestNotifierProcessOutput:
    def test_workb_body_includes_process_table(self):
        from src.alarm.application.nodes.alarm_notifier import build_workb_body

        body = build_workb_body(_make_result(), _mem_snapshot())
        assert "영향 프로세스 — 메모리 상위" in body
        assert "전체 142개" in body
        assert "java (pid 12345)" in body
        assert "메모리 38.1%" in body

    def test_workb_body_omits_process_when_none(self):
        from src.alarm.application.nodes.alarm_notifier import build_workb_body

        body = build_workb_body(_make_result(), None)
        assert "영향 프로세스" not in body

    def test_workb_body_masked_args_not_leaked(self):
        from src.alarm.application.nodes.alarm_notifier import build_workb_body

        snap = ProcessSnapshot(
            alarm_kind="memory", captured_at=None,
            top=[ProcessInfo("db", 1, 0, "pg", 1.0, 0.2, 50.0, 0, "***")],
            total_count=1, source_host="h",
        )
        body = build_workb_body(_make_result(), snap)
        # workb 표는 수치 위주 — 평문 비밀번호가 들어갈 여지 없음
        assert "password" not in body.lower()

    def test_webhook_payload_includes_process(self):
        from src.alarm.application.nodes.alarm_notifier import _process_payload

        payload = _process_payload(_mem_snapshot())
        assert payload["alarm_kind"] == "memory"
        assert payload["total_count"] == 142
        assert payload["top"][0]["name"] == "java"
        assert payload["top"][1]["args"] == "***"  # 마스킹된 값만

    def test_webhook_payload_none_when_no_snapshot(self):
        from src.alarm.application.nodes.alarm_notifier import _process_payload

        assert _process_payload(None) is None


# ─── Phase 3: API 헬퍼 (_process_to_dict / _resolve_process_snapshot) ───────────

class TestApiProcessHelpers:
    def test_process_to_dict(self):
        from src.api.routes.alarm import _process_to_dict

        d = _process_to_dict(_mem_snapshot())
        assert d["alarm_kind"] == "memory"
        assert d["captured_at"] == "2026-06-05T09:33:59"
        assert d["total_count"] == 142
        assert d["source_host"] == "saisvd01"
        assert len(d["top"]) == 2
        assert d["top"][0]["pid"] == 12345

    async def test_resolve_simulated_processes_masks_args(self):
        from src.api.routes.alarm import _resolve_process_snapshot

        cfg = SimpleNamespace(alarm=make_alarm_cfg())
        ev = make_event(resource_type="server.Server", alarm_name="메모리 사용률")
        snap = await _resolve_process_snapshot(
            cfg, ev, False,
            [{"name": "db", "pid": 1, "pmem": 90.0,
              "args": "--password=leakme -jar a.jar"}],
        )
        assert snap is not None
        assert snap.alarm_kind == "memory"
        assert "leakme" not in snap.top[0].args
        assert "***" in snap.top[0].args

    async def test_resolve_simulated_skips_non_cpu_mem(self):
        from src.api.routes.alarm import _resolve_process_snapshot

        cfg = SimpleNamespace(alarm=make_alarm_cfg())
        ev = make_event(resource_type="server.Disks", alarm_name="디스크 사용률")
        snap = await _resolve_process_snapshot(
            cfg, ev, False, [{"name": "x", "pid": 1, "pmem": 5.0}]
        )
        assert snap is None

    async def test_resolve_none_when_no_params(self):
        from src.api.routes.alarm import _resolve_process_snapshot

        cfg = SimpleNamespace(alarm=make_alarm_cfg())
        ev = make_event(resource_type="server.Cpus", alarm_name="CPU 사용률")
        snap = await _resolve_process_snapshot(cfg, ev, False, None)
        assert snap is None
