"""표면어 해석 2단 폴백·경계 판정 테스트 (Plan 67 R3-(i)·(iii)).

- A1~A6: 정규식 1순위 유지 + 미매칭 시 `parsed_requirements`의 LLM 산출물
  (`time_range`/`limit`) 폴백. 신규 LLM 호출 0건(이미 계산된 값 재활용).
- A6/§6: "전체/모든/모두" 스코프 표면어의 조사·파생 경계 판정("전체적으로" 오탐 차단).
  LIMIT 상향(query_gen_common)과 LIMIT 자동 추가 스킵(query_validator)이 판정을 공유한다.

근거 문서: `docs/regex_llm_conversion_review.md` §4.2·§4.4·§6.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.utils.query_gen_common import (
    fallback_counters,
    has_all_scope_keyword,
    reset_fallback_counters,
    resolve_query_limit,
    resolve_stat_month_range,
)

_TODAY = date(2026, 7, 30)


@pytest.fixture(autouse=True)
def _reset_counters():
    reset_fallback_counters()
    yield
    reset_fallback_counters()


class TestStatMonthLLMFallback:
    """기간 표현(A1~A3) 2단 폴백."""

    @pytest.mark.parametrize(
        "query, want",
        [
            ("지난달 통계", ("202606", "202606")),
            ("2026년 6월 CPU 사용률", ("202606", "202606")),
            ("지난 3개월 통계", ("202604", "202606")),
            ("이번달 통계", ("202607", "202607")),
        ],
    )
    def test_regex_match_ignores_llm_value(self, query, want):
        """정규식이 매칭되면 LLM 산출물은 쓰이지 않는다(기존 동작 불변)."""
        got = resolve_stat_month_range(
            query, _TODAY, parsed_time_range={"start": "2020-01-01", "end": "2020-12-31"}
        )
        assert got == want
        assert fallback_counters() == {}

    def test_regex_miss_without_llm_value_stays_none(self):
        """폴백 값이 없으면 종전과 같이 None(전 기간)."""
        assert resolve_stat_month_range("CPU 사용률 현황", _TODAY) is None
        assert resolve_stat_month_range("지난 반년 CPU 사용률", _TODAY) is None

    def test_regex_miss_adopts_llm_range(self):
        """"지난 반년"류 미매칭 표현을 LLM 산출물로 회복한다(끝 월은 직전 완결 월로 절단)."""
        got = resolve_stat_month_range(
            "지난 반년 CPU 사용률",
            _TODAY,
            parsed_time_range={"start": "2026-01-30", "end": "2026-07-30"},
        )
        assert got == ("202601", "202606")
        assert fallback_counters()["interpret.time_range_llm_fallback"] == 1

    def test_current_month_only_request_is_not_truncated(self):
        """당월만 요구한 범위는 절단하지 않는다(시작 월 하한 유지)."""
        got = resolve_stat_month_range(
            "요번 달 데이터",
            _TODAY,
            parsed_time_range={"start": "2026-07-01", "end": "2026-07-15"},
        )
        assert got == ("202607", "202607")

    def test_single_side_range(self):
        """start/end 한쪽만 있으면 그 달 단일 범위로 본다."""
        assert resolve_stat_month_range(
            "언젠가", _TODAY, parsed_time_range={"start": "2026-05-01", "end": None}
        ) == ("202605", "202605")
        assert resolve_stat_month_range(
            "언젠가", _TODAY, parsed_time_range={"end": "2026-05-31"}
        ) == ("202605", "202605")

    @pytest.mark.parametrize(
        "bad",
        [
            {},
            {"start": "yesterday"},
            {"start": "2026-13-01"},
            {"start": 202606},
            "2026-06",
            None,
        ],
    )
    def test_malformed_llm_value_is_ignored(self, bad):
        """형식이 어긋난 LLM 산출물은 무시한다(잘못된 기간 필터 조립 방지)."""
        assert resolve_stat_month_range("CPU 사용률 현황", _TODAY, parsed_time_range=bad) is None


class TestMonthRangeExpressions:
    """절대 월 범위·반기 표현의 결정적 해석(D-185) — 정규식 1순위, LLM 산출물보다 우선.

    라이브 실측(2026-08-25): "1월부터 6월까지" 폼필이 어느 정규식에도 안 잡혀 기준월이
    지난달 기본값(2~7월)으로 침묵 폴백했다. 연도 있는 범위는 첫 월 단일로 오해석됐다.
    """

    _AUG = date(2026, 8, 25)

    @pytest.mark.parametrize(
        "query, want",
        [
            ("1월부터 6월까지의 데이터를 기준으로 양식을 채우시오", ("202601", "202606")),
            ("1월~6월 CPU 사용률", ("202601", "202606")),
            ("1월에서 6월", ("202601", "202606")),
            ("2026년 1월부터 6월까지", ("202601", "202606")),   # 종전: 1월 단일(첫 매치)
            ("2026년 1월~2026년 6월", ("202601", "202606")),
            ("2026-01~2026-06", ("202601", "202606")),
            ("11월부터 2월까지", ("202511", "202602")),           # 연말→연초: 시작은 전년
            ("2025년 11월부터 2월까지", ("202511", "202602")),
            ("상반기", ("202601", "202606")),
            ("하반기", ("202607", "202612")),                    # 8월: 진행 중 반기 허용
            ("작년 하반기", ("202507", "202512")),
            ("2025년 상반기", ("202501", "202506")),
        ],
    )
    def test_range_expressions(self, query, want):
        got = resolve_stat_month_range(
            query, self._AUG, parsed_time_range={"start": "2020-01-01", "end": "2020-12-31"}
        )
        assert got == want
        assert fallback_counters() == {}  # 정규식 매칭 → LLM 폴백 미발동

    @pytest.mark.parametrize(
        "query",
        ["상위 3-5개 서버", "1-6 서버", "2026-03-13 기준 현황", "CPU 사용률 현황"],
    )
    def test_numeric_ranges_are_not_months(self, query):
        """'월' 접미도 연도도 없는 숫자 범위·ISO 날짜는 월 범위로 오탐하지 않는다."""
        got = resolve_stat_month_range(query, self._AUG)
        assert got in (None, ("202603", "202603"))  # ISO 날짜는 종전 단일 월 해석 유지

    def test_single_month_paths_unchanged(self):
        """범위 표현이 없으면 종전 단일 월·상대 표현 해석이 그대로다."""
        assert resolve_stat_month_range("2026년 3월 기준으로", self._AUG) == ("202603", "202603")
        assert resolve_stat_month_range("지난 3개월", self._AUG) == ("202605", "202607")
        assert resolve_stat_month_range("지난달", self._AUG) == ("202607", "202607")


class TestQueryLimitLLMFallback:
    """건수 표현(A4~A6) 2단 폴백."""

    @pytest.mark.parametrize(
        "query, want",
        [
            ("알람 100건 조회해줘", 100),
            ("상위 10개 서버", 10),
            ("전체 서버 조회", 10_000),
        ],
    )
    def test_deterministic_match_ignores_llm_value(self, query, want):
        """결정적 판정이 잡으면 LLM 산출물은 쓰이지 않는다(기존 동작 불변)."""
        assert resolve_query_limit(query, 1000, parsed_limit=7) == want
        assert "interpret.limit_llm_fallback" not in fallback_counters()

    def test_regex_miss_adopts_llm_limit(self):
        """"100개만"류 미매칭 표현을 LLM 산출물로 회복한다."""
        assert resolve_query_limit("CPU 높은 서버 100개만", 1000, parsed_limit=100) == 100
        assert fallback_counters()["interpret.limit_llm_fallback"] == 1

    def test_llm_limit_is_capped(self):
        """LLM 산출 상한도 전체 조회 상한을 넘지 않는다."""
        assert resolve_query_limit("아주 많이", 1000, parsed_limit=10_000_000) == 10_000

    @pytest.mark.parametrize("bad", [None, 0, -5, True, "10", 1.5])
    def test_malformed_llm_limit_falls_back_to_default(self, bad):
        assert resolve_query_limit("서버 목록", 1000, parsed_limit=bad) == 1000

    def test_default_when_no_signal(self):
        assert resolve_query_limit("서버 목록", 1000) == 1000


class TestAllScopeKeywordBoundary:
    """전체 스코프 표면어 경계 판정 (A6 오탐 차단)."""

    @pytest.mark.parametrize(
        "query", ["전체 서버", "모든 서버 조회", "서버 모두 보여줘", "전체를 조회", "전체서버 조회"]
    )
    def test_scope_expressions_accepted(self, query):
        assert has_all_scope_keyword(query) is True

    @pytest.mark.parametrize("query", ["전체적으로 CPU 높은 서버", "전체적인 추이", "", None])
    def test_derivative_forms_rejected(self, query):
        assert has_all_scope_keyword(query) is False

    def test_false_positive_no_longer_raises_limit(self):
        """"전체적으로 CPU 높은 서버"가 LIMIT 상한(10000)으로 상향되지 않는다."""
        assert resolve_query_limit("전체적으로 CPU 높은 서버", 1000) == 1000

    def test_rejection_is_instrumented(self):
        """오탐 차단은 계측된다(R4 발동률 비교 재료)."""
        has_all_scope_keyword("전체적으로 CPU 높은 서버")
        assert fallback_counters()["interpret.all_scope_boundary_reject"] == 1

    def test_mixed_query_still_accepted(self):
        """파생형과 스코프 표현이 함께 있으면 스코프 지시를 인정한다."""
        assert has_all_scope_keyword("전체적으로 보면 모든 서버가") is True


class TestValidatorSharesBoundaryJudgement:
    """검증 코어의 LIMIT 자동 추가 스킵 판정이 공용 헬퍼를 쓴다(§6 인라인 튜플 제거).

    코어는 Plan 69 후속 2단계에서 `src.nodes.query_validator` → `src.sql_validation`으로
    이동했다(도구 계층의 nodes 역참조 해소). 판정 주체가 코어이므로 단언 대상도 코어다.
    """

    def test_validator_imports_shared_helper(self):
        import importlib

        core = importlib.import_module("src.sql_validation")
        assert core.has_all_scope_keyword is has_all_scope_keyword

    def test_no_inline_keyword_tuple_left(self):
        import inspect
        import importlib

        core = importlib.import_module("src.sql_validation")
        src = inspect.getsource(core)
        assert '("모든", "전체", "모두")' not in src


class TestQueryGeneratorWiring:
    """query_generator가 폴백 값을 **실제로 전달**하는지 (정의만으로는 무효 — 배선 실측)."""

    async def test_parsed_requirements_are_passed_to_resolvers(self, sample_state):
        import importlib
        from unittest.mock import AsyncMock, MagicMock

        qg = importlib.import_module("src.nodes.query_generator")
        seen: dict = {}

        def fake_limit(state, user_query, default_limit, parsed_limit=None):
            seen["parsed_limit"] = parsed_limit
            return default_limit

        def fake_month(user_query, today=None, *, parsed_time_range=None):
            seen["parsed_time_range"] = parsed_time_range
            return None

        original_limit = qg.resolve_effective_limit
        original_month = qg.resolve_stat_month_range
        qg.resolve_effective_limit = fake_limit
        qg.resolve_stat_month_range = fake_month
        try:
            sample_state["parsed_requirements"]["limit"] = 42
            sample_state["parsed_requirements"]["time_range"] = {
                "start": "2026-01-01", "end": "2026-06-30",
            }
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = MagicMock(
                content="```sql\nSELECT 1 FROM servers LIMIT 10;\n```"
            )
            mock_config = MagicMock()
            mock_config.query.default_limit = 1000
            mock_config.text2sql.semantic_compose = False

            await qg.query_generator(sample_state, llm=mock_llm, app_config=mock_config)
        finally:
            qg.resolve_effective_limit = original_limit
            qg.resolve_stat_month_range = original_month

        assert seen["parsed_limit"] == 42
        assert seen["parsed_time_range"] == {"start": "2026-01-01", "end": "2026-06-30"}


class TestMultiDbExecutorWiring:
    """멀티 DB 경로도 동일 폴백을 전달하는지 (D-066 대칭 — 단일 경로와 동형 실측).

    한쪽 경로만 폴백하면 공동존(gp+yd) 등 멀티 DB 질의가 단일 DB 질의와 다른 기간·상한으로
    나가므로, 네 호출 지점 전부를 실측으로 고정한다(:139 노드 LIMIT / :502 SMQ 컴파일 기간 /
    :621 LLM 폴백 프롬프트 기간 / :738 폼필 피벗은 **의도적 제외**).
    """

    async def test_node_passes_parsed_limit(self):
        import importlib
        from unittest.mock import AsyncMock, MagicMock

        mdb = importlib.import_module("src.nodes.multi_db_executor")
        seen: dict = {}

        def fake_limit(state, user_query, default_limit, parsed_limit=None):
            seen["parsed_limit"] = parsed_limit
            return default_limit

        original = mdb.resolve_effective_limit
        mdb.resolve_effective_limit = fake_limit
        try:
            app_config = MagicMock()
            app_config.query.default_limit = 1000
            await mdb.multi_db_executor(
                {
                    "target_databases": [],  # 루프 미진입 — LIMIT 해석은 루프 전에 수행된다
                    "parsed_requirements": {"limit": 55},
                    "user_query": "서버 목록 조회",
                },
                llm=AsyncMock(),
                app_config=app_config,
            )
        finally:
            mdb.resolve_effective_limit = original

        assert seen["parsed_limit"] == 55

    async def _run_generate_sql(self, *, semantic_compose: bool) -> list[dict]:
        """_generate_sql을 목으로 1회 실행하고 기간 해석 호출 인자를 수집한다."""
        import importlib
        from unittest.mock import AsyncMock, MagicMock

        mdb = importlib.import_module("src.nodes.multi_db_executor")
        calls: list[dict] = []

        def fake_month(user_query, today=None, *, parsed_time_range=None):
            calls.append({"query": user_query, "parsed_time_range": parsed_time_range})
            return None

        original = mdb.resolve_stat_month_range
        mdb.resolve_stat_month_range = fake_month
        try:
            mock_llm = AsyncMock()
            mock_llm.ainvoke.return_value = MagicMock(
                content="```sql\nSELECT 1 FROM cmm_resource WHERE dtime IS NULL LIMIT 10;\n```"
            )
            app_config = MagicMock()
            app_config.text2sql.semantic_compose = semantic_compose
            app_config.text2sql.generic_llm_mapping = False
            app_config.get_polestar_db_ids.return_value = set()
            await mdb._generate_sql(
                llm=mock_llm,
                parsed_requirements={
                    "original_query": "지난 반년 CPU 사용률",
                    "time_range": {"start": "2026-01-01", "end": "2026-06-30"},
                },
                schema_info={"tables": {}},
                sub_query_context="지난 반년 CPU 사용률",
                default_limit=1000,
                db_engine="postgresql",
                db_id="polestar_gp",
                app_config=app_config,
            )
        finally:
            mdb.resolve_stat_month_range = original
        return calls

    async def test_llm_fallback_prompt_path_passes_time_range(self):
        """LLM 폴백 프롬프트 경로(:621)가 폴백 인자를 전달한다(or 체인 마지막 호출)."""
        calls = await self._run_generate_sql(semantic_compose=False)
        assert calls, "기간 해석이 호출되지 않았다"
        assert any(c["parsed_time_range"] == {"start": "2026-01-01", "end": "2026-06-30"} for c in calls)

    async def test_semantic_compile_path_passes_time_range(self):
        """SMQ 결정적 컴파일 경로(:502)도 폴백 인자를 전달한다."""
        calls = await self._run_generate_sql(semantic_compose=True)
        first = calls[0]
        assert first["query"] == "지난 반년 CPU 사용률"
        assert first["parsed_time_range"] == {"start": "2026-01-01", "end": "2026-06-30"}

    # 폼필 피벗 EAV 픽스처 — 자식 리소스 속성(resource_type != server.Server)이 있어야
    # 다중 리소스 피벗 분기(결정적 조립)로 진입한다.
    _PIVOT_SCHEMA = {
        "tables": {
            "cmm_resource": {"columns": [{"name": "name", "type": "varchar"}]},
            "core_config_prop": {"columns": [{"name": "NAME", "type": "varchar"}]},
        },
        "_structure_meta": {
            "patterns": [
                {
                    "type": "eav",
                    "entity_table": "cmm_resource",
                    "config_table": "core_config_prop",
                    "attribute_column": "NAME",
                    "value_column": "VALUE",
                    "join_condition": "r.id = p.resource_id",
                    "known_attributes_detail": [
                        {"name": "LOGICALCORE", "resource_type": "server.Cpus"},
                    ],
                }
            ],
        },
    }
    _TIME_RANGE = {"start": "2026-01-01", "end": "2026-06-30"}

    async def test_form_fill_pivot_fallback_included_in_both_paths(self):
        """폼필 피벗 기간 해석도 두 경로 **모두** 폴백 인자를 전달한다(2026-07-30 결정 변경).

        제외하면 "지난 반년 + 양식 첨부"류가 stat_date 필터 없이 전 기간 평균으로 침묵 왜곡된다.
        한쪽만 포함하면 단일/멀티 폼필 SQL이 갈라지므로 포함도 대칭이어야 한다(D-066).
        두 경로 모두 **실제로 피벗 분기를 태워** 인자 전달을 확인한다(소스 문자열 검사 아님).
        """
        single = await self._capture_single_pivot_time_range()
        multi = await self._capture_multi_pivot_time_range()
        assert single == self._TIME_RANGE, f"단일 경로 폼필 피벗 폴백 미전달: {single!r}"
        assert multi == self._TIME_RANGE, f"멀티 경로 폼필 피벗 폴백 미전달: {multi!r}"

    async def _capture_single_pivot_time_range(self):
        """단일 경로 폼필 피벗의 기간 해석 인자를 캡처한다."""
        import importlib

        qg = importlib.import_module("src.nodes.query_generator")
        calls: list = []

        def fake_month(user_query, today=None, *, parsed_time_range=None):
            calls.append(parsed_time_range)
            return None

        original = qg.resolve_stat_month_range
        qg.resolve_stat_month_range = fake_month
        try:
            sql = qg._try_build_form_fill_pivot_sql(
                {
                    "column_mapping": {"CPU 코어 수": "EAV:LOGICALCORE"},
                    "schema_info": self._PIVOT_SCHEMA,
                    "active_db_id": "polestar_gp",
                    "parsed_requirements": {
                        "original_query": "지난 반년 CPU 코어 수",
                        "time_range": self._TIME_RANGE,
                    },
                },
                1000,
                "지난 반년 CPU 코어 수",
            )
        finally:
            qg.resolve_stat_month_range = original
        assert sql, "단일 경로 폼필 피벗이 조립되지 않았다(픽스처 확인 필요)"
        assert calls, "단일 경로 기간 해석이 호출되지 않았다"
        return calls[-1]

    async def _capture_multi_pivot_time_range(self):
        """멀티 경로 폼필 피벗의 기간 해석 인자를 캡처한다."""
        import importlib
        from unittest.mock import AsyncMock, MagicMock

        mdb = importlib.import_module("src.nodes.multi_db_executor")
        calls: list = []

        def fake_month(user_query, today=None, *, parsed_time_range=None):
            calls.append(parsed_time_range)
            return None

        original = mdb.resolve_stat_month_range
        mdb.resolve_stat_month_range = fake_month
        try:
            app_config = MagicMock()
            app_config.text2sql.semantic_compose = False
            app_config.text2sql.generic_llm_mapping = False
            app_config.get_polestar_db_ids.return_value = set()
            await mdb._generate_sql(
                llm=AsyncMock(),
                parsed_requirements={
                    "original_query": "지난 반년 CPU 코어 수",
                    "time_range": self._TIME_RANGE,
                },
                schema_info=self._PIVOT_SCHEMA,
                sub_query_context="지난 반년 CPU 코어 수",
                default_limit=1000,
                column_mapping={"CPU 코어 수": "EAV:LOGICALCORE"},
                db_engine="postgresql",
                db_id="polestar_gp",
                app_config=app_config,
            )
        finally:
            mdb.resolve_stat_month_range = original
        assert calls, "멀티 경로 폼필 피벗 분기가 실행되지 않았다(기간 해석 미호출)"
        return calls[-1]

    async def test_multi_form_fill_pivot_passes_time_range(self):
        """멀티 경로 폼필 피벗 분기를 실제로 태워 폴백 인자 전달을 확인한다(배선 실측)."""
        import importlib
        from unittest.mock import AsyncMock, MagicMock

        mdb = importlib.import_module("src.nodes.multi_db_executor")
        calls: list[dict] = []

        def fake_month(user_query, today=None, *, parsed_time_range=None):
            calls.append({"query": user_query, "parsed_time_range": parsed_time_range})
            return None

        schema_info = {
            "tables": {
                "cmm_resource": {"columns": [{"name": "name", "type": "varchar"}]},
                "core_config_prop": {"columns": [{"name": "NAME", "type": "varchar"}]},
            },
            "_structure_meta": {
                "patterns": [
                    {
                        "type": "eav",
                        "entity_table": "cmm_resource",
                        "config_table": "core_config_prop",
                        "attribute_column": "NAME",
                        "value_column": "VALUE",
                        "join_condition": "r.id = p.resource_id",
                        # 자식 리소스 속성(resource_type != server.Server) → 다중 리소스 피벗 진입
                        "known_attributes_detail": [
                            {"name": "LOGICALCORE", "resource_type": "server.Cpus"},
                        ],
                    }
                ],
            },
        }

        original = mdb.resolve_stat_month_range
        mdb.resolve_stat_month_range = fake_month
        try:
            app_config = MagicMock()
            app_config.text2sql.semantic_compose = False
            app_config.text2sql.generic_llm_mapping = False
            app_config.get_polestar_db_ids.return_value = set()
            await mdb._generate_sql(
                llm=AsyncMock(),
                parsed_requirements={
                    "original_query": "지난 반년 CPU 코어 수",
                    "time_range": {"start": "2026-01-01", "end": "2026-06-30"},
                },
                schema_info=schema_info,
                sub_query_context="지난 반년 CPU 코어 수",
                default_limit=1000,
                column_mapping={"CPU 코어 수": "EAV:LOGICALCORE"},
                db_engine="postgresql",
                db_id="polestar_gp",
                app_config=app_config,
            )
        finally:
            mdb.resolve_stat_month_range = original

        assert calls, "폼필 피벗 분기가 실행되지 않았다(기간 해석 미호출)"
        assert calls[-1]["parsed_time_range"] == {"start": "2026-01-01", "end": "2026-06-30"}
