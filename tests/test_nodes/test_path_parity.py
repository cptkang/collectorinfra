"""단일/멀티 경로 대칭 옵트인 회귀 테스트 (Plan 69 P3-2, 계획서 §0.3-3).

`TEXT2SQL_PATH_PARITY`가 OFF면 양 경로의 프롬프트·호출 형태가 종전 그대로여야 하고
(바이트 불변은 `tests/test_prompt_render_matrix.py`가 sha256으로 고정), ON일 때만
갭 (a)~(d)가 발동해야 한다. 각 갭이 **실제로 주입됐는지**를 목 캡처로 실측한다
(Known Mistakes: "양쪽 실제 주입 실측" — 구현만 있고 배선이 없으면 무효).

- (a) 멀티에 어댑터 시스템 템플릿 디스패치
- (b) 단일에 스키마 한정 규칙(D-057)
- (c) 멀티에 값 인덱스 리터럴 · 폼필 피벗 프롬프트 블록
- (d) 멀티의 "prior_block이면 컴파일 우회"를 server_scope 결정적 전달로 교체(D-099)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from src.nodes import multi_db_executor as mdb
from src.nodes.query_generator import query_generator

_DB_ID = "polestar_cm_gp"
_QUERY = "서버별 OS 종류와 벤더를 조회해줘"

_SCHEMA_INFO: dict = {
    "tables": {
        "polestar.cmm_resource": {
            "columns": [
                {"name": "id", "type": "bigint", "primary_key": True, "nullable": False},
                {"name": "name", "type": "varchar(255)", "nullable": True},
                {"name": "hostname", "type": "varchar(255)", "nullable": True},
                {"name": "resource_type", "type": "varchar(255)", "nullable": True},
            ],
        },
        "polestar.core_config_prop": {
            "columns": [
                {"name": "configuration_id", "type": "bigint", "nullable": False},
                {"name": "name", "type": "varchar(255)", "nullable": True},
                {"name": "stringvalue_short", "type": "varchar(4000)", "nullable": True},
            ],
        },
    },
    "_structure_meta": {
        "query_guide": "서버 조회 시 dtime IS NULL 조건을 사용하세요.",
        "patterns": [
            {
                "type": "eav",
                "entity_table": "cmm_resource",
                "config_table": "core_config_prop",
                "attribute_column": "name",
                "value_column": "stringvalue_short",
                # 자식 리소스 속성 — (c) 폼필 피벗 판정을 태우기 위한 최소 정보
                "known_attributes": [
                    {"name": "LOGICALCORE", "resource_type": "server.Cpus"},
                ],
            }
        ],
    },
}

_PARSED_REQUIREMENTS: dict = {
    "original_query": _QUERY,
    "query_targets": ["서버"],
    "filter_conditions": [],
    "time_range": None,
    "output_format": "text",
    "aggregation": None,
    "limit": None,
}


class _CapturingLLM:
    """메시지를 기록하고 고정 SQL을 돌려주는 LLM 대역(실 호출 없음 — D-127)."""

    def __init__(self) -> None:
        self.messages: list = []

    async def ainvoke(self, messages):
        self.messages = list(messages)
        return SimpleNamespace(content="SELECT 1")


def _texts(llm: _CapturingLLM) -> dict[str, str]:
    roles = {SystemMessage: "system", HumanMessage: "human"}
    return {roles[type(m)]: m.content for m in llm.messages if type(m) in roles}


def _cfg(*, path_parity: bool, semantic_compose: bool = False) -> MagicMock:
    """검증 대상 플래그만 명시한 설정 대역(.env 누수 차단)."""
    cfg = MagicMock()
    cfg.query.default_limit = 1000
    cfg.get_polestar_db_ids.return_value = {_DB_ID}
    cfg.synonym.value_retrieval = False
    cfg.synonym.fuzzy_match = False
    cfg.synonym.match_confidence_min = 0.85
    cfg.text2sql.semantic_compose = semantic_compose
    cfg.text2sql.multi_candidate = False
    cfg.text2sql.complexity_gate = False
    cfg.text2sql.generic_llm_mapping = False
    cfg.text2sql.query_history_fewshot = False
    cfg.text2sql.stepwise_derivation = False
    cfg.text2sql.hypernym_ambiguity = False
    cfg.text2sql.path_parity = path_parity
    return cfg


def _pin_schema_prefix(monkeypatch, prefix: str = "polestar.") -> None:
    """스키마 접두사를 레지스트리 파일 대신 고정값으로(환경 비의존)."""
    monkeypatch.setattr("src.routing.db_schema.get_schema_prefix", lambda db_id: prefix)


async def _multi_prompts(monkeypatch, cfg, **kwargs) -> dict[str, str]:
    _pin_schema_prefix(monkeypatch)
    llm = _CapturingLLM()
    await mdb._generate_sql(
        llm, _PARSED_REQUIREMENTS, _SCHEMA_INFO, _QUERY, 1000,
        db_engine="postgresql", db_id=_DB_ID, app_config=cfg, **kwargs,
    )
    return _texts(llm)


class TestFlagDefault:
    """플래그 자체의 기본값·설정 대역 안전장치."""

    def test_default_is_off(self):
        from src.config import Text2SQLConfig

        assert Text2SQLConfig(_env_file=None).path_parity is False

    def test_magicmock_config_does_not_enable(self):
        """미정의 속성이 truthy인 설정 대역만으로는 켜지지 않는다(Known Mistakes)."""
        from src.nodes.prompt_blocks import path_parity_enabled

        assert path_parity_enabled(MagicMock()) is False
        assert path_parity_enabled(None) is False


class TestGapAAdapterTemplate:
    """(a) 멀티 경로 어댑터 시스템 템플릿 디스패치."""

    _FAKE_TEMPLATE = (
        "TEST_ADAPTER_TEMPLATE\n{schema}\n{default_limit}\n{structure_guide}\n{db_engine_hint}"
    )

    def _fake_adapter(self, monkeypatch, calls: list):
        def _system_template(routing_intent=None):
            calls.append(routing_intent)
            return self._FAKE_TEMPLATE

        monkeypatch.setattr(
            "src.db_adapters.get_adapter",
            lambda db_id, polestar_db_ids=None: SimpleNamespace(
                system_template=_system_template
            ),
        )

    async def test_off_keeps_common_template(self, monkeypatch):
        calls: list = []
        self._fake_adapter(monkeypatch, calls)
        prompts = await _multi_prompts(monkeypatch, _cfg(path_parity=False))
        assert "TEST_ADAPTER_TEMPLATE" not in prompts["system"]
        assert "인프라 DB에 대한 SQL 쿼리를 생성하는 전문가" in prompts["system"]
        assert calls == [], "OFF인데 어댑터 템플릿을 조회했다"

    async def test_on_uses_adapter_template(self, monkeypatch):
        calls: list = []
        self._fake_adapter(monkeypatch, calls)
        prompts = await _multi_prompts(monkeypatch, _cfg(path_parity=True))
        assert prompts["system"].startswith("TEST_ADAPTER_TEMPLATE")
        assert calls == [None], "멀티 경로는 routing_intent 없이 조회한다"
        # 템플릿만 바뀌고 나머지 재료(스키마·구조 가이드·엔진 힌트)는 그대로 채워진다
        assert "[스키마 한정 규칙]" in prompts["system"]
        assert "polestar.cmm_resource" in prompts["system"]

    async def test_on_falls_back_when_no_adapter(self, monkeypatch):
        monkeypatch.setattr(
            "src.db_adapters.get_adapter", lambda db_id, polestar_db_ids=None: None
        )
        prompts = await _multi_prompts(monkeypatch, _cfg(path_parity=True))
        assert "인프라 DB에 대한 SQL 쿼리를 생성하는 전문가" in prompts["system"]


class TestGapBSchemaPrefixRule:
    """(b) 단일 경로 스키마 한정 규칙(D-057) 주입."""

    def _state(self) -> dict:
        return {
            "user_query": _QUERY,
            "schema_info": _SCHEMA_INFO,
            "parsed_requirements": _PARSED_REQUIREMENTS,
            "active_db_id": _DB_ID,
            "active_db_engine": "postgresql",
            "retry_count": 0,
        }

    async def test_off_has_no_rule(self, monkeypatch):
        _pin_schema_prefix(monkeypatch)
        llm = _CapturingLLM()
        await query_generator(self._state(), llm=llm, app_config=_cfg(path_parity=False))
        assert "[스키마 한정 규칙]" not in _texts(llm)["system"]

    async def test_on_injects_rule(self, monkeypatch):
        _pin_schema_prefix(monkeypatch)
        llm = _CapturingLLM()
        await query_generator(self._state(), llm=llm, app_config=_cfg(path_parity=True))
        system = _texts(llm)["system"]
        assert "[스키마 한정 규칙]" in system
        assert "접두사 `polestar.`" in system

    async def test_on_no_schema_uses_bare_rule(self, monkeypatch):
        """접두사가 없는 DB는 멀티와 같은 '무스키마' 문구를 받는다."""
        _pin_schema_prefix(monkeypatch, prefix="")
        llm = _CapturingLLM()
        await query_generator(self._state(), llm=llm, app_config=_cfg(path_parity=True))
        assert "스키마 접두사 없이(무스키마)" in _texts(llm)["system"]


class TestGapCValueIndexAndPivot:
    """(c) 멀티 경로 값 인덱스 리터럴·폼필 피벗 블록 주입."""

    _VALUE_INDEX = {"cmm_resource.resource_type": ["server.Server", "server.Cpus"]}

    def _cfg_with_value_retrieval(self, *, path_parity: bool) -> MagicMock:
        cfg = _cfg(path_parity=path_parity)
        cfg.synonym.value_retrieval = True
        return cfg

    async def test_off_has_no_value_index_block(self, monkeypatch):
        prompts = await _multi_prompts(
            monkeypatch,
            self._cfg_with_value_retrieval(path_parity=False),
            value_index=self._VALUE_INDEX,
        )
        assert "server.Cpus" not in prompts["human"]

    async def test_on_injects_value_index_block(self, monkeypatch):
        monkeypatch.setattr(
            "src.schema_cache.value_index.search_value_index",
            lambda index, keywords, **kw: {"cmm_resource.resource_type": ["server.Server"]},
        )
        prompts = await _multi_prompts(
            monkeypatch,
            self._cfg_with_value_retrieval(path_parity=True),
            value_index=self._VALUE_INDEX,
        )
        assert "server.Server" in prompts["human"]

    async def test_on_retry_uses_pivot_prompt_block(self, monkeypatch):
        """재시도(결정적 SQL 실패)면 단일처럼 LLM 폴백 + 피벗 지침 블록으로 넘어간다."""
        _pin_schema_prefix(monkeypatch)
        llm = _CapturingLLM()
        sql = await mdb._generate_sql(
            llm, _PARSED_REQUIREMENTS, _SCHEMA_INFO, _QUERY, 1000,
            column_mapping={"코어 수": "EAV:LOGICALCORE"},
            error_context="컬럼 'x'가 존재하지 않습니다.",
            db_engine="postgresql", db_id=_DB_ID,
            app_config=_cfg(path_parity=True),
        )
        assert sql == "SELECT 1", "LLM 폴백을 타야 한다(결정적 SQL 즉시 반환 아님)"
        human = _texts(llm)["human"]
        assert "resource_type" in human and "LOGICALCORE" in human
        assert "## 이전 에러" in human

    async def test_off_retry_returns_deterministic_sql(self, monkeypatch):
        """OFF는 종전대로 재시도에도 결정적 피벗 SQL을 되돌려준다(동작 불변)."""
        _pin_schema_prefix(monkeypatch)
        llm = _CapturingLLM()
        sql = await mdb._generate_sql(
            llm, _PARSED_REQUIREMENTS, _SCHEMA_INFO, _QUERY, 1000,
            column_mapping={"코어 수": "EAV:LOGICALCORE"},
            error_context="컬럼 'x'가 존재하지 않습니다.",
            db_engine="postgresql", db_id=_DB_ID,
            app_config=_cfg(path_parity=False),
        )
        assert sql.upper().startswith("SELECT")
        assert sql != "SELECT 1"
        assert llm.messages == [], "OFF는 LLM을 호출하지 않는다"


class TestGapDPriorScope:
    """(d) 멀티 경로 선행 스코프 — 컴파일 우회 대신 server_scope 결정적 전달."""

    _PRIOR_BLOCK = "## 선행 작업 결과 서버 스코프 (필수 준수)\n'web-01'"
    _PRIOR_SCOPE = ("hostname", ["web-01", "web-02"])

    def _capture_compile(self, monkeypatch) -> list[dict]:
        captured: list[dict] = []

        async def _fake(llm, user_query, db_id, **kwargs):
            captured.append(kwargs)
            return None, None, None

        monkeypatch.setattr(mdb, "compile_from_nl", _fake)
        return captured

    async def test_off_skips_compile_when_prior_block(self, monkeypatch):
        captured = self._capture_compile(monkeypatch)
        await _multi_prompts(
            monkeypatch, _cfg(path_parity=False, semantic_compose=True),
            prior_block=self._PRIOR_BLOCK, prior_scope=self._PRIOR_SCOPE,
        )
        assert captured == [], "OFF는 prior_block이 있으면 컴파일을 우회한다"

    async def test_on_passes_server_scope(self, monkeypatch):
        captured = self._capture_compile(monkeypatch)
        await _multi_prompts(
            monkeypatch, _cfg(path_parity=True, semantic_compose=True),
            prior_block=self._PRIOR_BLOCK, prior_scope=self._PRIOR_SCOPE,
        )
        assert len(captured) == 1, "ON은 컴파일을 우회하지 않는다"
        assert captured[0]["server_scope"] == self._PRIOR_SCOPE

    async def test_node_computes_prior_scope_from_state(self):
        """노드가 단일 경로와 같은 공유 함수로 prior_rows에서 스코프를 뽑는다."""
        from src.nodes.prompt_blocks import prior_server_scope

        col, values = prior_server_scope(
            {"task_1": [{"hostname": "web-01"}, {"hostname": "web-02"}]}
        )
        assert col == "hostname"
        assert values == ["web-01", "web-02"]


@pytest.mark.parametrize("gap", ["(a)", "(b)", "(c)", "(d)"])
def test_gap_logs_are_wired(gap):
    """갭별 발동 로그가 소스에 실재한다(관측 없이는 ON 전환 판단이 불가능하다)."""
    from pathlib import Path

    sources = "".join(
        Path(f"src/nodes/{name}.py").read_text(encoding="utf-8")
        for name in ("query_generator", "multi_db_executor")
    )
    assert f"[경로대칭] {gap}" in sources
