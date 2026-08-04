"""프롬프트 렌더 sha256 골든 매트릭스 (Plan 69 P1-1).

P2~P5(죽은 코드 정리·공유 빌더 추출·거대 함수 분해)가 **동작 불변**인지 판정하는 기준이다.
단일 경로(`query_generator`)와 멀티 경로(`multi_db_executor._generate_sql`)가 LLM에 보내는
SystemMessage/HumanMessage 본문의 sha256을 시나리오별로 채록해
`tests/snapshots/prompt_render_sha256.json`과 대조한다.

의도한 프롬프트 변경이 있을 때만 사유와 함께 스냅샷을 갱신한다:

    UPDATE_PROMPT_SNAPSHOT=1 python -m pytest tests/test_prompt_render_matrix.py

결정성 확보(비결정 입력 고정):
    - 질의에 기간 표현을 넣지 않고 `time_range`도 None으로 둬 오늘 날짜 의존(stat_month)을 제거
    - 폴스타 지식 렌더 플래그(`TEXT2SQL_PROMPT_KNOWLEDGE_RENDER`)는 `.env`가 아니라 테스트가
      명시 고정 — OFF/ON 두 모드를 모두 채록한다(계획 P1-1 v4의 렌더 모드 × 경로 매트릭스)
    - 멀티 경로의 스키마 접두사는 DB 레지스트리 파일 대신 고정값을 주입
    - 나머지 옵트인 플래그는 전부 명시 OFF(MagicMock 속성은 기본 truthy라 누수 위험)
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from src.nodes import multi_db_executor as mdb
from src.nodes.query_generator import query_generator

SNAPSHOT_PATH = Path(__file__).resolve().parent / "snapshots" / "prompt_render_sha256.json"

_DB_ID = "polestar_cm_gp"
_QUERY = "서버별 OS 종류와 벤더를 조회해줘"

#: 폴스타 EAV 프로필을 흉내 낸 고정 스키마 — 구조 가이드·EAV 블록까지 렌더 경로에 태운다.
_SCHEMA_INFO: dict = {
    "tables": {
        "polestar.cmm_resource": {
            "columns": [
                {"name": "id", "type": "bigint", "primary_key": True, "nullable": False},
                {"name": "name", "type": "varchar(255)", "nullable": True},
                {"name": "hostname", "type": "varchar(255)", "nullable": True},
                {"name": "resource_type", "type": "varchar(255)", "nullable": True},
                {"name": "dtime", "type": "timestamp", "nullable": True},
            ],
            "sample_data": [{"id": 1, "name": "web-01", "hostname": "web-01"}],
        },
        "polestar.core_config_prop": {
            "columns": [
                {"name": "configuration_id", "type": "bigint", "nullable": False},
                {"name": "name", "type": "varchar(255)", "nullable": True},
                {"name": "stringvalue_short", "type": "varchar(4000)", "nullable": True},
            ],
        },
        "polestar.cmm_metric_stat_m": {
            "columns": [
                {"name": "resource_id", "type": "bigint", "nullable": False},
                {"name": "avg_val", "type": "numeric", "nullable": True},
                {"name": "stat_date", "type": "varchar(8)", "nullable": True},
            ],
        },
    },
    "relationships": [
        {"from": "polestar.cmm_metric_stat_m.resource_id", "to": "polestar.cmm_resource.id"},
    ],
    "_structure_meta": {
        "query_guide": "서버 조회 시 dtime IS NULL 조건을 사용하세요.",
        "patterns": [
            {
                "type": "eav",
                "entity_table": "cmm_resource",
                "config_table": "core_config_prop",
                "attribute_column": "name",
                "value_column": "stringvalue_short",
                "value_joins": [
                    {
                        "eav_attribute": "Hostname",
                        "eav_value_column": "stringvalue_short",
                        "entity_column": "hostname",
                    }
                ],
                "excluded_join_columns": [
                    {
                        "table": "cmm_resource",
                        "column": "resource_conf_id",
                        "reason": "운영 DB에서 NULL",
                    }
                ],
            }
        ],
        "query_examples": [
            {
                "question": "서버 목록을 보여줘",
                "sql": "SELECT name FROM polestar.cmm_resource WHERE dtime IS NULL",
                "explanation": "삭제되지 않은 서버만 조회",
            }
        ],
    },
}

_PARSED_REQUIREMENTS: dict = {
    "original_query": _QUERY,
    "query_targets": ["서버", "OS", "벤더"],
    "filter_conditions": [],
    "time_range": None,
    "output_format": "text",
    "aggregation": None,
    "limit": None,
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _CapturingLLM:
    """메시지를 기록하고 고정 SQL을 돌려주는 LLM 대역(실 호출 없음 — D-127)."""

    def __init__(self) -> None:
        self.messages: list = []

    async def ainvoke(self, messages):
        self.messages = list(messages)
        return SimpleNamespace(content="SELECT 1")


def _cfg() -> MagicMock:
    """검증 대상 플래그만 명시한 설정 대역(.env 누수 차단)."""
    cfg = MagicMock()
    cfg.query.default_limit = 1000
    cfg.get_polestar_db_ids.return_value = {_DB_ID}
    cfg.synonym.value_retrieval = False
    cfg.synonym.fuzzy_match = False
    cfg.synonym.match_confidence_min = 0.85
    cfg.text2sql.semantic_compose = False       # LLM 폴백(프롬프트) 경로로 진입
    cfg.text2sql.multi_candidate = False
    cfg.text2sql.complexity_gate = False
    cfg.text2sql.generic_llm_mapping = False
    cfg.text2sql.query_history_fewshot = False
    cfg.text2sql.stepwise_derivation = False
    cfg.text2sql.hypernym_ambiguity = False
    return cfg


def _single_state(**overrides) -> dict:
    state = {
        "user_query": _QUERY,
        "schema_info": _SCHEMA_INFO,
        "parsed_requirements": _PARSED_REQUIREMENTS,
        "active_db_id": _DB_ID,
        "active_db_engine": "postgresql",
        "column_descriptions": {"polestar.cmm_resource.name": "서버 등록명"},
        "column_synonyms": {"polestar.cmm_resource.name": ["서버명", "서버 이름"]},
        "resource_type_synonyms": {"server.Server": ["서버"]},
        "eav_name_synonyms": {"OSType": ["OS 종류", "운영체제"]},
        "retry_count": 0,
    }
    state.update(overrides)
    return state


def _pin_knowledge_render(monkeypatch, enabled: bool) -> None:
    """폴스타 프롬프트 지식 렌더 플래그를 `.env`와 무관하게 고정한다.

    렌더 결과 캐시도 비운다 — 같은 세션의 다른 테스트가 채운 값에 결과가 좌우되면
    스냅샷이 실행 순서에 의존하게 된다.
    """
    from src.db_adapters.polestar import prompts as polestar_prompts

    monkeypatch.setattr(polestar_prompts, "_rendered_cache", {})
    monkeypatch.setattr(polestar_prompts, "knowledge_render_enabled", lambda: enabled)


def _texts(llm: _CapturingLLM) -> dict[str, str]:
    """캡처된 메시지에서 (system, human) 본문을 뽑는다."""
    assert [type(m) for m in llm.messages] == [SystemMessage, HumanMessage], (
        f"메시지 구성이 바뀌었다: {[type(m).__name__ for m in llm.messages]}"
    )
    return {"system": llm.messages[0].content, "human": llm.messages[1].content}


async def _collect_prompts(monkeypatch) -> dict[str, dict[str, str]]:
    """시나리오별 프롬프트 본문을 채록한다."""
    prompts: dict[str, dict[str, str]] = {}

    # (a) 단일 경로 일반 질의 — 지식 렌더 OFF/ON 두 모드
    for enabled, key in ((False, "single_basic"), (True, "single_basic__knowledge_render_on")):
        _pin_knowledge_render(monkeypatch, enabled)
        llm = _CapturingLLM()
        await query_generator(_single_state(), llm=llm, app_config=_cfg())
        prompts[key] = _texts(llm)

    _pin_knowledge_render(monkeypatch, False)

    # (b) 단일 경로 재시도 — 이전 SQL·에러 컨텍스트 주입
    llm = _CapturingLLM()
    await query_generator(
        _single_state(
            error_message="컬럼 'os_name'이 존재하지 않습니다.",
            generated_sql="SELECT os_name FROM polestar.cmm_resource LIMIT 1000;",
            retry_count=1,
        ),
        llm=llm,
        app_config=_cfg(),
    )
    prompts["single_retry"] = _texts(llm)

    # (c) 단일 경로 prior_rows — 선행 작업 서버 스코프 블록 주입
    llm = _CapturingLLM()
    await query_generator(
        _single_state(
            prior_rows={"task_1": [{"hostname": "web-01"}, {"hostname": "web-02"}]}
        ),
        llm=llm,
        app_config=_cfg(),
    )
    prompts["single_prior_rows"] = _texts(llm)

    # 멀티 경로: 스키마 접두사는 레지스트리 파일 대신 고정값(환경 비의존)
    monkeypatch.setattr("src.routing.db_schema.get_schema_prefix", lambda db_id: "polestar.")

    # (d) 멀티 경로 일반 질의
    llm = _CapturingLLM()
    await mdb._generate_sql(
        llm, _PARSED_REQUIREMENTS, _SCHEMA_INFO, _QUERY, 1000,
        db_engine="postgresql", db_id=_DB_ID, app_config=_cfg(),
    )
    prompts["multi_basic"] = _texts(llm)

    # (e) 멀티 경로 폼필(column_mapping + 미매핑 필드)
    llm = _CapturingLLM()
    await mdb._generate_sql(
        llm, _PARSED_REQUIREMENTS, _SCHEMA_INFO, _QUERY, 1000,
        column_mapping={
            "서버 이름": "cmm_resource.name",
            "CPU 평균": "cmm_metric_stat_m.avg_val",
        },
        unmapped_fields=["메모리 최고"],
        db_engine="postgresql", db_id=_DB_ID, app_config=_cfg(),
    )
    prompts["multi_column_mapping"] = _texts(llm)

    return prompts


def _hashes(prompts: dict[str, dict[str, str]]) -> dict[str, str]:
    return {
        f"{scenario}.{role}": _sha(text)
        for scenario, roles in prompts.items()
        for role, text in sorted(roles.items())
    }


async def test_prompt_render_matches_snapshot(monkeypatch):
    """시나리오별 프롬프트 sha256이 스냅샷과 일치한다(동작 불변 판정 기준)."""
    current = _hashes(await _collect_prompts(monkeypatch))

    if os.environ.get("UPDATE_PROMPT_SNAPSHOT") == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        pytest.skip(f"스냅샷을 갱신했다: {SNAPSHOT_PATH}")

    assert SNAPSHOT_PATH.exists(), (
        f"스냅샷이 없다: {SNAPSHOT_PATH} — UPDATE_PROMPT_SNAPSHOT=1로 최초 채록하라"
    )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert set(current) == set(expected), (
        "시나리오 집합이 달라졌다 "
        f"(신규: {sorted(set(current) - set(expected))}, "
        f"소실: {sorted(set(expected) - set(current))})"
    )
    changed = sorted(k for k in current if current[k] != expected[k])
    assert not changed, (
        f"프롬프트 바이트가 바뀌었다: {changed}\n"
        "의도한 변경이면 사유를 남기고 UPDATE_PROMPT_SNAPSHOT=1로 갱신하라."
    )


async def test_scenario_anchors(monkeypatch):
    """각 해시가 무엇을 덮는지 내용 앵커로 고정한다(해시 불일치 시 원인 좁히기용)."""
    prompts = await _collect_prompts(monkeypatch)

    # 단일 경로는 폴스타 어댑터 전용 템플릿을 쓴다(공통 템플릿이 아니다)
    assert "POLESTAR 인프라 모니터링 DB" in prompts["single_basic"]["system"]
    # 구조 가이드·EAV 조인 규칙·few-shot 예시가 시스템 프롬프트에 실린다
    for marker in ("## EAV 테이블 조인 규칙", "[값 기반 조인 (value-based join)]",
                   "[금지 JOIN 컬럼]", "서버 목록을 보여줘"):
        assert marker in prompts["single_basic"]["system"], marker

    # 지식 렌더 ON은 같은 시나리오라도 시스템 프롬프트가 달라진다(2모드 축의 의미)
    assert (prompts["single_basic__knowledge_render_on"]["system"]
            != prompts["single_basic"]["system"])
    assert (prompts["single_basic__knowledge_render_on"]["human"]
            == prompts["single_basic"]["human"]), "사용자 프롬프트는 렌더 모드와 무관하다"

    # 재시도: 이전 SQL·에러가 사용자 프롬프트에 실린다
    assert "## 이전 시도 (실패)" in prompts["single_retry"]["human"]
    assert "os_name" in prompts["single_retry"]["human"]
    assert "## 이전 시도 (실패)" not in prompts["single_basic"]["human"]

    # prior_rows: 선행 스코프 강제 블록
    assert "## 선행 작업 결과 서버 스코프 (필수 준수)" in prompts["single_prior_rows"]["human"]
    assert "'web-01', 'web-02'" in prompts["single_prior_rows"]["human"]

    # 멀티 경로: 공통 템플릿 + 스키마 한정 규칙(단일에는 없는 비대칭 — §1.3)
    assert "인프라 DB에 대한 SQL 쿼리를 생성하는 전문가" in prompts["multi_basic"]["system"]
    assert "[스키마 한정 규칙]" in prompts["multi_basic"]["system"]
    assert "[스키마 한정 규칙]" not in prompts["single_basic"]["system"]

    # 멀티 폼필: 매핑 강제·성능 지표 안내·미매핑 필드 블록
    human = prompts["multi_column_mapping"]["human"]
    assert "## 양식 필드 매핑 (반드시 SELECT에 포함)" in human
    assert "## 성능 지표 필드 (사용률)" in human
    assert "## 자동 매핑 실패 필드 (스키마에서 직접 조회 필요)" in human
    assert '"메모리 최고"' in human


async def test_prompts_are_deterministic_across_runs(monkeypatch):
    """같은 입력을 두 번 렌더하면 동일 바이트다(스냅샷 성립 조건)."""
    first = _hashes(await _collect_prompts(monkeypatch))
    second = _hashes(await _collect_prompts(monkeypatch))
    assert first == second
