"""process_query_node(Plan 48 §5.3) 단위 테스트.

서버명→hostname 해석, 직접 hostname 매칭, 미해석→직접 hostname 폴백,
모호→후보 안내(임의 선택 안 함), 비폴스타/0건, user_specified_db 시 ③ 폴백 스킵,
API 실패 graceful, process_query_enabled=false 게이팅, 마스킹 노출 안 됨을 검증한다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.config import AppConfig, ProcessQueryConfig
from src.infrastructure.polestar_host_resolver import HostResolution
from src.nodes.process_query_node import process_query_node


# ── Fakes ──────────────────────────────────────────────


class _FakeLLM:
    """ainvoke가 고정 텍스트를 .content로 반환하는 fake LLM. 받은 메시지 캡처."""

    def __init__(self, text="현황 분석 결과입니다."):
        self._text = text
        self.received = []

    async def ainvoke(self, messages):
        self.received = messages

        class _R:
            content = self._text

        return _R()


class _FakeResolver:
    """resolve(db_id, identifier) → 미리 정의된 HostResolution 반환."""

    def __init__(self, by_db):
        # by_db: {db_id: {identifier: HostResolution}}
        self._by_db = by_db
        self.calls = []

    async def resolve(self, db_id, identifier):
        self.calls.append((db_id, identifier))
        return self._by_db.get(db_id, {}).get(
            identifier, HostResolution()
        )


class _ApiResult:
    def __init__(self, processes, captured_at=None):
        self.processes = processes
        self.captured_at = captured_at


class _FakeProcessClient:
    """get_base_url / list_by_hostname 을 모사."""

    def __init__(self, base_urls, results, *, fail=False):
        # base_urls: {db_id: url}; results: {(db_id, host): _ApiResult}
        self._base_urls = base_urls
        self._results = results
        self._fail = fail
        self.calls = []

    def get_base_url(self, db_id):
        return self._base_urls.get(db_id)

    async def list_by_hostname(self, db_id, hostname):
        self.calls.append((db_id, hostname))
        if self._fail:
            raise RuntimeError("API 장애")
        return self._results.get((db_id, hostname))


def _app_config(enabled=True):
    cfg = AppConfig()
    cfg.process_query = ProcessQueryConfig(
        process_query_enabled=enabled,
        process_query_top_n=10,
        process_query_resolve_timeout_seconds=3,
    )
    return cfg


def _state(**over):
    base = {
        "user_query": "myserver 프로세스 보여줘",
        "active_db_id": "polestar_cm_gp",
        "process_query_target": {
            "identifier": "myserver",
            "metric": "both",
            "top_n": 10,
        },
        "user_specified_db": None,
        "file_type": None,
    }
    base.update(over)
    return base


_NOW = datetime(2026, 6, 16, 12, 0, 0)
_BASE_URLS = {"polestar_cm_gp": "http://gp", "polestar_cm_yd": "http://yd"}


# ── Tests ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestResolutionPaths:
    async def test_servername_to_hostname(self):
        resolver = _FakeResolver(
            {"polestar_cm_gp": {"myserver": HostResolution(hostname="saisvd01")}}
        )
        client = _FakeProcessClient(
            _BASE_URLS,
            {
                ("polestar_cm_gp", "saisvd01"): _ApiResult(
                    [{"name": "java", "pid": 1, "user": "app", "p100cpu": 50, "pmem": 20}],
                    _NOW,
                )
            },
        )
        out = await process_query_node(
            _state(),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        assert out["routing_intent"] == "process_query"
        assert out["process_overview"]["source_host"] == "saisvd01"
        assert out["process_overview"]["total_count"] == 1
        # API는 hostname으로 호출됨 (서버명 아님)
        assert ("polestar_cm_gp", "saisvd01") in client.calls

    async def test_direct_hostname_match(self):
        # DB 미해석이지만 identifier 자체가 hostname → ② 직접 폴백
        resolver = _FakeResolver({})  # 항상 빈 결과
        client = _FakeProcessClient(
            _BASE_URLS,
            {
                ("polestar_cm_gp", "myserver"): _ApiResult(
                    [{"name": "p", "pid": 2, "user": "root", "p100cpu": 1, "pmem": 1}],
                    _NOW,
                )
            },
        )
        out = await process_query_node(
            _state(),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        assert out["process_overview"]["source_host"] == "myserver"

    async def test_unresolved_falls_back_to_other_polestar_db(self):
        # 활성 db 미해석·직접 0건 → ③ 타 폴스타 db(yd)에서 해석
        resolver = _FakeResolver(
            {"polestar_cm_yd": {"myserver": HostResolution(hostname="ydhost")}}
        )
        client = _FakeProcessClient(
            _BASE_URLS,
            {
                ("polestar_cm_yd", "ydhost"): _ApiResult(
                    [{"name": "p", "pid": 3, "user": "u", "p100cpu": 9, "pmem": 9}],
                    _NOW,
                )
            },
        )
        out = await process_query_node(
            _state(),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        assert out["process_overview"]["source_host"] == "ydhost"
        assert ("polestar_cm_yd", "ydhost") in client.calls

    async def test_user_specified_db_skips_other_db_fallback(self):
        # user_specified_db 세팅 → ③ 폴백 스킵, 타 db resolve 호출 안 함
        resolver = _FakeResolver(
            {"polestar_cm_yd": {"myserver": HostResolution(hostname="ydhost")}}
        )
        client = _FakeProcessClient(_BASE_URLS, {})  # 활성 db 0건
        out = await process_query_node(
            _state(user_specified_db="polestar_cm_gp"),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        # 미해석 안내로 종료
        assert out["process_overview"] is None
        assert "특정하지 못했습니다" in out["final_response"]
        # 타 폴스타 db(yd)에 대한 resolve 호출이 없어야 함
        assert all(db != "polestar_cm_yd" for db, _ in resolver.calls)

    async def test_ambiguous_returns_candidates_no_autopick(self):
        resolver = _FakeResolver(
            {
                "polestar_cm_gp": {
                    "myserver": HostResolution(candidates=["hostA", "hostB"])
                }
            }
        )
        client = _FakeProcessClient(_BASE_URLS, {})
        out = await process_query_node(
            _state(),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        assert out["process_overview"] is None
        assert "hostA" in out["final_response"]
        assert "hostB" in out["final_response"]
        # 임의 선택하여 API 호출하지 않음
        assert client.calls == []


@pytest.mark.asyncio
class TestGatingAndGraceful:
    async def test_disabled_gating(self):
        out = await process_query_node(
            _state(),
            llm=_FakeLLM(),
            app_config=_app_config(enabled=False),
            host_resolver=_FakeResolver({}),
            process_client=_FakeProcessClient(_BASE_URLS, {}),
        )
        assert out["process_overview"] is None
        assert "비활성화" in out["final_response"]

    async def test_non_polestar_base_url_missing(self):
        # base_url 미매핑 db → 게이팅 종료
        out = await process_query_node(
            _state(active_db_id="itsm"),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=_FakeResolver({}),
            process_client=_FakeProcessClient(_BASE_URLS, {}),
        )
        assert out["process_overview"] is None
        assert "지원하지 않습니다" in out["final_response"]

    async def test_api_failure_graceful(self):
        resolver = _FakeResolver(
            {"polestar_cm_gp": {"myserver": HostResolution(hostname="h1")}}
        )
        client = _FakeProcessClient(_BASE_URLS, {}, fail=True)
        out = await process_query_node(
            _state(),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        assert out["process_overview"] is None
        assert "가져오지 못했습니다" in out["final_response"]

    async def test_zero_processes_builds_overview(self):
        resolver = _FakeResolver(
            {"polestar_cm_gp": {"myserver": HostResolution(hostname="h1")}}
        )
        client = _FakeProcessClient(
            _BASE_URLS, {("polestar_cm_gp", "h1"): _ApiResult([], _NOW)}
        )
        out = await process_query_node(
            _state(),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        assert out["process_overview"]["total_count"] == 0

    async def test_missing_injection_graceful(self):
        out = await process_query_node(
            _state(),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=None,
            process_client=None,
        )
        assert out["process_overview"] is None
        assert "처리할 수 없습니다" in out["final_response"]


@pytest.mark.asyncio
class TestMaskingNotLeaked:
    async def test_no_plaintext_secret_in_output(self):
        secret_args = "java --password=topsecret123 token=abctoken jdbc:db://u:plainpw@h/x"
        resolver = _FakeResolver(
            {"polestar_cm_gp": {"myserver": HostResolution(hostname="h1")}}
        )
        client = _FakeProcessClient(
            _BASE_URLS,
            {
                ("polestar_cm_gp", "h1"): _ApiResult(
                    [
                        {
                            "name": "svc",
                            "pid": 9,
                            "user": "app",
                            "p100cpu": 99,
                            "pmem": 99,
                            "args": secret_args,
                        }
                    ],
                    _NOW,
                )
            },
        )
        llm = _FakeLLM()
        out = await process_query_node(
            _state(),
            llm=llm,
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        # 평문 비밀이 final_response·overview·LLM 입력 어디에도 없어야 함
        ov_args = out["process_overview"]["top_by_cpu"][0]["args"]
        assert "topsecret123" not in ov_args
        assert "abctoken" not in ov_args
        assert "plainpw" not in ov_args
        assert "***" in ov_args

        haystack = out["final_response"] + str(out["process_overview"])
        # LLM에 주입된 메시지 내용도 점검
        for msg in llm.received:
            haystack += str(getattr(msg, "content", ""))
        for secret in ("topsecret123", "abctoken", "plainpw"):
            assert secret not in haystack


@pytest.mark.asyncio
class TestCsvAndSummaryFraming:
    """Plan 48 §10: CSV용 query_results 채움 + 현황 요약 프레이밍."""

    async def test_success_fills_query_results_with_all_processes(self):
        # 전체 프로세스가 query_results로 반환되어 기존 CSV 다운로드가 동작해야 함
        procs = [
            {"name": f"p{i}", "pid": i, "user": "u", "p100cpu": i, "pmem": 100 - i}
            for i in range(15)
        ]
        resolver = _FakeResolver(
            {"polestar_cm_gp": {"myserver": HostResolution(hostname="h1")}}
        )
        client = _FakeProcessClient(
            _BASE_URLS, {("polestar_cm_gp", "h1"): _ApiResult(procs, _NOW)}
        )
        out = await process_query_node(
            _state(),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        rows = out["query_results"]
        # top_n=10이지만 CSV에는 전체 15건이 포함되어야 함
        assert len(rows) == 15
        assert out["process_overview"]["total_count"] == 15
        # cpu/both 기준 → p100cpu 내림차순 정렬 (첫 행이 가장 큰 p100cpu)
        assert rows[0]["p100cpu"] >= rows[-1]["p100cpu"]
        assert set(rows[0].keys()) >= {"name", "pid", "user", "p100cpu", "pmem", "args"}

    async def test_early_return_has_no_query_results(self):
        # 미해석 안내 종료에는 query_results가 없어 CSV 버튼이 뜨지 않아야 함
        out = await process_query_node(
            _state(user_specified_db="polestar_cm_gp"),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=_FakeResolver({}),
            process_client=_FakeProcessClient(_BASE_URLS, {}),
        )
        assert out["process_overview"] is None
        assert "query_results" not in out

    async def test_csv_rows_args_masked(self):
        # CSV 행의 args도 마스킹되어 평문 비밀 비노출
        resolver = _FakeResolver(
            {"polestar_cm_gp": {"myserver": HostResolution(hostname="h1")}}
        )
        client = _FakeProcessClient(
            _BASE_URLS,
            {
                ("polestar_cm_gp", "h1"): _ApiResult(
                    [
                        {
                            "name": "svc",
                            "pid": 1,
                            "user": "app",
                            "p100cpu": 1,
                            "pmem": 1,
                            "args": "app --password=topsecret123",
                        }
                    ],
                    _NOW,
                )
            },
        )
        out = await process_query_node(
            _state(),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        csv_args = out["query_results"][0]["args"]
        assert "topsecret123" not in csv_args
        assert "***" in csv_args

    async def test_summary_text_frames_top_and_csv_to_llm(self):
        # LLM 입력 요약에 "전체 N건 중 상위" 프레이밍과 CSV 안내가 포함되어야 함
        resolver = _FakeResolver(
            {"polestar_cm_gp": {"myserver": HostResolution(hostname="h1")}}
        )
        client = _FakeProcessClient(
            _BASE_URLS,
            {
                ("polestar_cm_gp", "h1"): _ApiResult(
                    [{"name": "p", "pid": 1, "user": "u", "p100cpu": 5, "pmem": 5}],
                    _NOW,
                )
            },
        )
        llm = _FakeLLM()
        await process_query_node(
            _state(),
            llm=llm,
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        injected = "".join(str(getattr(m, "content", "")) for m in llm.received)
        assert "전체 1건 중" in injected
        assert "CSV" in injected


@pytest.mark.asyncio
class TestDocumentDelegation:
    async def test_xlsx_fills_organized_data(self):
        resolver = _FakeResolver(
            {"polestar_cm_gp": {"myserver": HostResolution(hostname="h1")}}
        )
        client = _FakeProcessClient(
            _BASE_URLS,
            {
                ("polestar_cm_gp", "h1"): _ApiResult(
                    [{"name": "p", "pid": 1, "user": "u", "p100cpu": 5, "pmem": 5}],
                    _NOW,
                )
            },
        )
        out = await process_query_node(
            _state(file_type="xlsx"),
            llm=_FakeLLM(),
            app_config=_app_config(),
            host_resolver=resolver,
            process_client=client,
        )
        assert "organized_data" in out
        assert len(out["organized_data"]["rows"]) == 1
        assert out["organized_data"]["rows"][0]["name"] == "p"
