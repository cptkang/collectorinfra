"""실시간 사용률 데이터 평면 테스트 (Plan 71 — 게이트·클라이언트·노드 병합).

전부 mock — 네트워크·DB 미사용. 응답 shape는 Plan 70 §1.3-2 확정본을 그대로 고정한다.
"""

from types import SimpleNamespace

import pytest

from src.clients.polestar_measurement import (
    MeasurementResult,
    MeasurementRow,
    PolestarMeasurementClient,
    parse_measurement_payload,
)
from src.config import PolestarRestConfig
from src.utils.query_gen_common import is_realtime_usage_query


# Plan 70 §1.3-2 확정 응답 shape (사용자 교정본 그대로)
_FIXED_PAYLOAD = {
    "date": "2026-07-23 09:11:15",
    "data": {
        "measurement": [
            {"resourceId": 464399, "targetName": "CPU", "min": 0.55, "avg": 0.55,
             "targetId": 465050, "max": 0.55, "resourceName": "cob0-bnoapd05",
             "definition": "Utilization", "targetType": "server.Cpus", "time": 1784765400000},
            {"resourceId": 464541, "targetName": "CPU", "min": 2.066666666666667,
             "avg": 2.066666666666667, "targetId": 465274, "max": 2.066666666666667,
             "resourceName": "cob0-bnoapd07", "definition": "Utilization",
             "targetType": "server.Cpus", "time": 1784765460000},
        ]
    },
    "id": "dashboard - measurement",
}


def _cfg(**overrides) -> PolestarRestConfig:
    """테스트 config — 검증 대상 필드를 명시해 .env 누수 차단(Known Mistakes)."""
    defaults = dict(
        realtime_usage_enabled=True,
        base_urls_csv="polestar_b0=http://10.37.16.51:9010",
        measurement_timeout_seconds=1,
        measurement_chunk_size=2,
        stale_after_minutes=15,
    )
    defaults.update(overrides)
    return PolestarRestConfig(**defaults)


class TestRealtimeGate:
    """B안 라우팅 게이트 경계 (Plan 71 §4 표 고정)."""

    @pytest.mark.parametrize("query", [
        "은행존의 모든 서버들에 대해 실시간 CPU 사용률을 조회해줘",  # §2 버튼 문구
        "공동존 김포 현재 메모리 사용률",
        "서버 abc01 지금 CPU 얼마나 써?",
    ])
    def test_api_route(self, query):
        assert is_realtime_usage_query(query) is True

    @pytest.mark.parametrize("query", [
        "은행존 모든 서버들에 대해 CPU 사용률 현황을 조회해줘",   # "현황" 단독 — B안 비트리거
        "지난달 실시간 CPU 사용률 통계",                        # 기간 혼합 — 통계 우선
        "지난 3개월 현재 메모리 추이",                          # 기간 혼합
        "실시간 디스크 사용률",                                 # 지원 지표 외
        "모든 서버 목록",                                       # 지표어 없음
        "", None,
    ])
    def test_db_route(self, query):
        assert is_realtime_usage_query(query) is False


class TestMeasurementClient:
    """URL 조립(확정 파라미터)·응답 파싱·청크·부분 실패."""

    def test_url_has_confirmed_params(self):
        client = PolestarMeasurementClient(_cfg())
        url = client._build_url("http://10.37.16.51:9010", [464399, 464541], "cpu")
        assert "timeSelector=recent" in url          # 오타(timeSelecotr) 아님 — 실측 확정
        assert "count=1" in url                      # count=1 고정
        assert "definitions=Utilization" in url
        assert "type=server.Cpus" in url
        assert "resourceIds=464399,464541" in url
        mem_url = client._build_url("http://x", [1], "memory")
        assert "definitions=UsedPercent" in mem_url
        assert "type=server.Memory" in mem_url

    def test_parse_fixed_shape(self):
        rows = parse_measurement_payload(_FIXED_PAYLOAD)
        assert set(rows) == {464399, 464541}
        r = rows[464399]
        assert r.resource_name == "cob0-bnoapd05"
        assert r.avg == 0.55
        assert r.collected_at_ms == 1784765400000   # time = 수집 시각(Unix ms)
        assert r.target_id == 465050                # 1안 드릴다운용 리소스 ID

    @pytest.mark.parametrize("payload", [None, {}, {"data": {}}, {"data": {"measurement": "x"}}])
    def test_parse_defensive(self, payload):
        assert parse_measurement_payload(payload) == {}

    async def test_chunking_and_merge(self, monkeypatch):
        """chunk_size=2, 5대 → 3청크 병렬 호출·병합."""
        client = PolestarMeasurementClient(_cfg(measurement_chunk_size=2))
        calls: list[list[int]] = []

        async def fake_chunk(_http, _base, ids, _metric):
            calls.append(ids)
            return {i: MeasurementRow(i, f"srv{i}", 1.0, 1.0, 1.0, 1, None) for i in ids}

        monkeypatch.setattr(client, "_fetch_chunk", fake_chunk)
        res = await client.fetch_zone("polestar_b0", [1, 2, 3, 4, 5], "cpu")
        assert res is not None
        assert len(calls) == 3
        assert set(res.rows) == {1, 2, 3, 4, 5}
        assert res.failed_chunks == 0

    async def test_partial_failure_returns_partial(self, monkeypatch):
        client = PolestarMeasurementClient(_cfg(measurement_chunk_size=2))

        async def fake_chunk(_http, _base, ids, _metric):
            if 3 in ids:
                return None  # 한 청크 실패
            return {i: MeasurementRow(i, f"srv{i}", 1.0, 1.0, 1.0, 1, None) for i in ids}

        monkeypatch.setattr(client, "_fetch_chunk", fake_chunk)
        res = await client.fetch_zone("polestar_b0", [1, 2, 3, 4], "cpu")
        assert res is not None
        assert res.failed_chunks == 1
        assert set(res.rows) == {1, 2}
        # 실패 청크 소속 ID가 노출되어 호출부가 "조회 실패"/"미수집"을 구분할 수 있어야 함
        assert res.failed_ids == frozenset({3, 4})

    async def test_all_chunks_failed_returns_none(self, monkeypatch):
        client = PolestarMeasurementClient(_cfg())

        async def fake_chunk(_http, _base, _ids, _metric):
            return None

        monkeypatch.setattr(client, "_fetch_chunk", fake_chunk)
        assert await client.fetch_zone("polestar_b0", [1, 2], "cpu") is None

    async def test_unmapped_base_url_returns_none(self):
        client = PolestarMeasurementClient(_cfg(base_urls_csv="polestar_cm_gp=http://x"))
        assert await client.fetch_zone("polestar_b0", [1], "cpu") is None


class TestRealtimeLookup:
    """노드 병합 — 미수집·수집 지연 표기, 전 존 실패 폴백(None)."""

    def _patch(self, monkeypatch, servers_by_db, fetch_result):
        import src.nodes.realtime_usage as mod

        class _FakeDB:
            def __init__(self, rows):
                self._rows = rows

            async def execute_sql(self, _sql):
                return SimpleNamespace(rows=self._rows)

        class _FakeRegistry:
            def __init__(self, _cfg_arg):
                pass

            def get_client(self, db_id):
                rows = servers_by_db[db_id]

                class _Ctx:
                    async def __aenter__(self_inner):
                        return _FakeDB(rows)

                    async def __aexit__(self_inner, *a):
                        return False

                return _Ctx()

        class _FakeClient:
            def __init__(self, _cfg_arg):
                pass

            async def fetch_zone(self, db_id, ids, metric):
                return fetch_result(db_id, ids, metric)

        async def _noop_audit(**_kw):
            return None

        monkeypatch.setattr(mod, "DBRegistry", _FakeRegistry)
        monkeypatch.setattr(mod, "PolestarMeasurementClient", _FakeClient)
        monkeypatch.setattr(mod, "log_query_execution", _noop_audit)
        return mod

    async def test_merge_marks_uncollected(self, monkeypatch):
        import time as _time
        now_ms = int(_time.time() * 1000)
        servers = {"polestar_b0": [
            {"id": 1, "name": "s1", "hostname": "h1", "avail_status": 0},
            # API 응답 누락 → 미수집. avail_status=1(비정상) — Power off/에이전트 이슈 판독용
            {"id": 2, "name": "s2", "hostname": "h2", "avail_status": 1},
        ]}

        def fetch(_db, _ids, _metric):
            return MeasurementResult(
                rows={1: MeasurementRow(1, "s1", 12.345, 1.0, 20.0, now_ms, 99)},
                failed_chunks=0, total_chunks=1,
            )

        mod = self._patch(monkeypatch, servers, fetch)
        cfg = SimpleNamespace(polestar_rest=_cfg())
        out = await mod.realtime_usage_lookup(
            ["polestar_b0"], "은행존의 모든 서버들에 대해 실시간 CPU 사용률을 조회해줘", cfg,
        )
        assert out is not None and out["source"] == "realtime_api"
        rows = out["query_results"]
        assert len(rows) == 2
        ok = next(r for r in rows if r["서버명"] == "s1")
        missing = next(r for r in rows if r["서버명"] == "s2")
        assert ok["CPU 사용률(%)"] == 12.35 and ok["상태"] == "정상"
        assert missing["CPU 사용률(%)"] is None and missing["상태"] == "미수집"
        # 가용성 판독(2026-07-24 실측: 미수집=대부분 Power off/에이전트 통신 이슈)
        assert ok["가용성"] == "정상"
        assert missing["가용성"].startswith("비정상")
        # summary에 상태별 집계와 미수집 원인 안내가 포함돼야 함(폐쇄망 파악 가능성)
        assert "미수집 1대" in out["organized_data"]["summary"]
        assert "최근 수집값이 없는 서버" in out["organized_data"]["summary"]

    async def test_failed_chunk_marked_as_query_failure(self, monkeypatch):
        """API 청크 실패 서버는 '미수집'이 아니라 '조회 실패'로 구분 표기."""
        servers = {"polestar_b0": [
            {"id": 1, "name": "s1", "hostname": "h1"},
            {"id": 2, "name": "s2", "hostname": "h2"},
        ]}
        import time as _time
        now_ms = int(_time.time() * 1000)

        def fetch(_db, _ids, _metric):
            return MeasurementResult(
                rows={1: MeasurementRow(1, "s1", 3.0, 3.0, 3.0, now_ms, None)},
                failed_chunks=1, total_chunks=2, failed_ids=frozenset({2}),
            )

        mod = self._patch(monkeypatch, servers, fetch)
        cfg = SimpleNamespace(polestar_rest=_cfg())
        out = await mod.realtime_usage_lookup(["polestar_b0"], "지금 CPU", cfg)
        failed = next(r for r in out["query_results"] if r["서버명"] == "s2")
        assert failed["상태"] == "조회 실패"
        assert "조회 실패" in out["organized_data"]["summary"]

    async def test_source_field_for_progress_label(self, monkeypatch):
        """처리 현황 라벨 교체용 source 필드가 결과에 포함돼야 함 (2026-07-24 실측 수정)."""
        import time as _time
        servers = {"polestar_b0": [{"id": 1, "name": "s1", "hostname": "h1"}]}
        now_ms = int(_time.time() * 1000)

        def fetch(_db, _ids, _metric):
            return MeasurementResult(
                rows={1: MeasurementRow(1, "s1", 1.0, 1.0, 1.0, now_ms, None)},
                failed_chunks=0, total_chunks=1,
            )

        mod = self._patch(monkeypatch, servers, fetch)
        cfg = SimpleNamespace(polestar_rest=_cfg())
        out = await mod.realtime_usage_lookup(["polestar_b0"], "지금 CPU", cfg)
        assert out["source"] == "realtime_api"

    async def test_stale_flagged(self, monkeypatch):
        servers = {"polestar_b0": [{"id": 1, "name": "s1", "hostname": "h1"}]}
        old_ms = 1784765400000  # 과거 고정 시각 — 임계(15분) 초과

        def fetch(_db, _ids, _metric):
            return MeasurementResult(
                rows={1: MeasurementRow(1, "s1", 5.0, 5.0, 5.0, old_ms, None)},
                failed_chunks=0, total_chunks=1,
            )

        mod = self._patch(monkeypatch, servers, fetch)
        cfg = SimpleNamespace(polestar_rest=_cfg())
        out = await mod.realtime_usage_lookup(["polestar_b0"], "지금 CPU", cfg)
        assert out["query_results"][0]["상태"] == "수집 지연"
        assert out["query_results"][0]["수집 시각"]  # KST 문자열 표기

    async def test_all_zone_failure_returns_none(self, monkeypatch):
        servers = {"polestar_b0": [{"id": 1, "name": "s1", "hostname": "h1"}]}

        def fetch(_db, _ids, _metric):
            return None  # API 전면 실패

        mod = self._patch(monkeypatch, servers, fetch)
        cfg = SimpleNamespace(polestar_rest=_cfg())
        assert await mod.realtime_usage_lookup(["polestar_b0"], "지금 CPU", cfg) is None

    async def test_duplicate_registration_detected(self, monkeypatch):
        """동일 서버명 중복 등록(구행 잔존) 시 경고 + 리소스 ID 컬럼으로 대조 가능해야 함.

        2026-07-24 폐쇄망 실측 가설: 단건 수동 호출은 정상인데 파이프라인만 '미수집'
        → 우리 목록의 구행 id가 measurement에 데이터가 없는 케이스.
        """
        import time as _time
        now_ms = int(_time.time() * 1000)
        servers = {"polestar_cm_yd": [
            {"id": 100, "name": "dup01", "hostname": "dup01"},   # 구행 — 데이터 없음
            {"id": 200, "name": "dup01", "hostname": "dup01"},   # 신행 — 정상
        ]}

        def fetch(_db, _ids, _metric):
            return MeasurementResult(
                rows={200: MeasurementRow(200, "dup01", 7.0, 7.0, 7.0, now_ms, None)},
                failed_chunks=0, total_chunks=1,
            )

        mod = self._patch(monkeypatch, servers, fetch)
        cfg = SimpleNamespace(polestar_rest=_cfg(base_urls_csv="polestar_cm_yd=http://x"))
        out = await mod.realtime_usage_lookup(["polestar_cm_yd"], "지금 CPU", cfg)
        rows = out["query_results"]
        old = next(r for r in rows if r["리소스 ID"] == 100)
        new = next(r for r in rows if r["리소스 ID"] == 200)
        assert old["상태"] == "미수집" and new["상태"] == "정상"
        assert "동일 서버명 중복 등록" in out["organized_data"]["summary"]
        assert "dup01" in out["organized_data"]["summary"]

    def test_detect_metrics(self):
        from src.nodes.realtime_usage import detect_metrics

        assert detect_metrics("실시간 CPU 사용률") == ["cpu"]
        assert detect_metrics("현재 메모리 사용률") == ["memory"]
        assert detect_metrics("지금 CPU와 메모리") == ["cpu", "memory"]
        assert detect_metrics("지금 사용률") == ["cpu"]  # 기본값
