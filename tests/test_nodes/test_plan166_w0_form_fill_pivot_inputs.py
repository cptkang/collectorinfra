"""폼필 피벗 조립기 입력 인자 골든 — 단일/멀티 경로 나란히 (plans/166 W0 · 스멜 N1).

plans/166 W0 특성화 스냅샷 — 단계 2(plans/163)만 갱신 가능, 단계 3 이후 갱신 금지
(갱신이 필요하면 리팩터가 아니다).

공유 조립기 `build_form_fill_pivot_sql`에 두 경로가 넘기는 **입력 인자**를 고정한다.

- 단일 경로: `query_generator._try_build_form_fill_pivot_sql`
- 멀티 경로: `multi_db_executor._generate_sql`(form_intent · llm=None — LLM에 도달하면 실패)
  → `_build_mapping_user_parts` → `_multi_resource_pivot_result`

각 모듈 네임스페이스의 `build_form_fill_pivot_sql`을 「인자 기록 후 원 함수 호출」 래퍼로 바꿔
구동한다. 기록은 원 함수 시그니처로 바인딩(기본값 채움)한 뒤 정규화한다 — 호출부가 위치/키워드
인자를 바꾸거나 기본값을 생략해도 값이 같으면 같은 골든이다. 같은 케이스는 같은 양식 매핑을
두 경로의 입력 형태로 바꿔 넣는다(멀티 caller와 같은 규칙: 매핑 None 필드 → `unmapped_fields`).
`identical`은 단일/멀티 입력이 이미 같은지(W3 N1(a) 공통화 후 동일성 확인용)를 기록한다.

호출 지점이 다른 모듈로 옮겨지면 patch 대상(`_PATCH_TARGETS`)만 호출 지점을 따라가고
스냅샷 데이터는 바꾸지 않는다.

재생성: ``UPDATE_PLAN162_SNAPSHOT=1 python -m pytest <이 파일>``
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib
import inspect
import json
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import src.db_adapters.polestar.assembler as assembler_mod
import src.utils.query_gen_common as qgc_mod
from src.config import AppConfig, Text2SQLConfig
from src.domain.query_time import QueryTime, resolve_query_time
from src.utils.query_gen_common import template_context_text
from tests.test_nodes.test_form_month_series import _EAV_META, _cpu_form_mapping

# `src.nodes` 패키지가 노드 함수 `query_generator`를 재노출해 `import … as`가 함수를 잡는다 —
# 서브모듈은 import_module로 받는다.
single_mod = importlib.import_module("src.nodes.query_generator")
multi_mod = importlib.import_module("src.nodes.multi_db_executor")

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent
    / "snapshots" / "plan166_w0" / "form_fill_pivot_inputs.json"
)

_TODAY = date(2026, 7, 28)  # 상대 기간(「지난달」)·월 시리즈 기본 앵커의 결정적 기준일
_KST = timezone(timedelta(hours=9))
_NOW = datetime(2026, 9, 29, 10, 0, tzinfo=_KST)  # QueryTime 해석 기준 시각

_ORIGINAL = assembler_mod.build_form_fill_pivot_sql
_PATCH_TARGETS = (single_mod, multi_mod)

_TABLES = {
    "cmm_resource": {"columns": [
        {"name": "name", "type": "text"},
        {"name": "hostname", "type": "text"},
        {"name": "ipaddress", "type": "text"},
        {"name": "install_date", "type": "text"},
    ]},
}

_MIXED_META = {
    "_structure_meta": {
        "patterns": [{
            "type": "eav",
            "entity_table": "cmm_resource",
            "config_table": "core_config_prop",
            "attribute_column": "name",
            "value_column": "stringvalue_short",
            "direct_join": {
                "entity_column": "resource_conf_id",
                "config_column": "configuration_id",
            },
            "known_attributes": [
                {"name": "OSVerson", "description": "OS 버전 [resource_type: server.Server]"},
                {"name": "LOGICALCORE", "description": "논리 코어 [resource_type: server.Cpus]"},
                {"name": "TotalSize", "description": "메모리 용량 [resource_type: server.Memory]"},
            ],
        }]
    }
}


class _FixedDate(date):
    """`date.today()`만 고정한 date 대역."""

    @classmethod
    def today(cls) -> _FixedDate:
        return cls(_TODAY.year, _TODAY.month, _TODAY.day)


def _memory_form_mapping() -> dict[str, Any]:
    mapping = _cpu_form_mapping()
    del mapping["처리능력|(TPMC)"]
    mapping["처리능력|(GB)"] = None
    return mapping


def _cases() -> dict[str, dict[str, Any]]:
    """케이스명 → 공통 입력(양식 매핑·출처·스키마·질의·엔진·db_id·시간 해석)."""
    usage_query = "최근 3개월 서버 사용률 양식 채워줘"
    return {
        # 월 시리즈 있음 + Vendor+Model 결합(제조사(모델명)) + 비고 규칙
        "cpu_month_series_concat": {
            "mapping": _cpu_form_mapping(),
            "sources": {},
            "schema_info": {"tables": _TABLES, **_EAV_META},
            "query": "2026년 6월 기준 CPU 양식 채워줘",
            "title": "나. 주요 업무 CPU 사용현황",
            "engine": "postgresql",
            "db_id": "",
            "query_time": None,
        },
        # 월 시리즈 있음 + 처리능력(GB) 용량 규칙(자식 리소스 server.Memory)
        "memory_month_series_capacity": {
            "mapping": _memory_form_mapping(),
            "sources": {},
            "schema_info": {"tables": _TABLES, **_EAV_META},
            "query": "2026년 6월 기준 메모리 양식 채워줘",
            "title": "다. 주요 업무 주기억장치 사용현황",
            "engine": "postgresql",
            "db_id": "",
            "query_time": None,
        },
        # 월 시리즈 없음 + EAV 문자(서버)·숫자(자식 Cpus/Memory) 섞임 + 사용률 필드 +
        # llm_inferred 강등 + 스키마에 없는 환각 칼럼 + 상대 기간(「지난달」)
        "no_month_mixed_eav": {
            "mapping": {
                "서버 이름": "cmm_resource.name",
                "IP 주소": "cmm_resource.ipaddress",
                "OS 버전": "EAV:OSVerson",
                "CPU 코어 수": "EAV:LOGICALCORE",
                "메모리 용량": "EAV:TotalSize",
                "CPU 평균 사용률": None,
                "도입일자": "cmm_resource.install_date",
                "용도": "cmm_resource.category",
                "구분": None,
            },
            "sources": {
                "서버 이름": "synonym",
                "IP 주소": "synonym",
                "도입일자": "llm_inferred",
            },
            "schema_info": {"tables": _TABLES, **_MIXED_META},
            "query": "지난달 서버 현황 양식 채워줘",
            "title": "서버 현황",
            "engine": "postgresql",
            "db_id": "",
            "query_time": None,
        },
        # DB2 등록 DB(스키마 한정) + 월 시리즈 없는 결합 + 어댑터 지표 분류 + 요청 시간 해석 period
        "db2_b0_query_time": {
            "mapping": {
                "서버명": "cmm_resource.name",
                "호스트명": "cmm_resource.hostname",
                "제조사(모델명)": "EAV:Model",
                "CPU 평균 사용률": None,
                "메모리 최대 사용률": None,
            },
            "sources": {},
            "schema_info": {"tables": _TABLES, **_EAV_META},
            "query": usage_query,
            "title": "서버 사용률 현황",
            "engine": "db2",
            "db_id": "polestar_b0",
            "query_time": resolve_query_time(usage_query, _NOW),
        },
    }


def _normalize(value: Any) -> Any:
    """기록 인자를 JSON 직렬화 가능한 결정적 형태로 바꾼다."""
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_normalize(v) for v in value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            "__type__": type(value).__name__,
            **{f.name: _normalize(getattr(value, f.name)) for f in dataclasses.fields(value)},
        }
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"__type__": type(value).__name__, "repr": repr(value)}


@pytest.fixture
def recorder(monkeypatch) -> list[dict[str, Any]]:
    """두 경로의 `build_form_fill_pivot_sql`을 기록 래퍼로 바꾸고 날짜를 고정한다."""
    calls: list[dict[str, Any]] = []
    sig = inspect.signature(_ORIGINAL)

    def _record(*args: Any, **kwargs: Any) -> str:
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        sql = _ORIGINAL(*args, **kwargs)
        calls.append({"inputs": _normalize(dict(bound.arguments)), "sql": sql})
        return sql

    for mod in _PATCH_TARGETS:
        monkeypatch.setattr(mod, "build_form_fill_pivot_sql", _record)
    monkeypatch.setattr(assembler_mod, "date", _FixedDate)
    monkeypatch.setattr(qgc_mod, "date", _FixedDate)
    return calls


def _single_state(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "column_mapping": dict(case["mapping"]),
        "mapping_sources": dict(case["sources"]),
        "schema_info": case["schema_info"],
        "template_structure": {"sheets": [{"title_text": case["title"], "name": "Sheet1"}]},
        "active_db_engine": case["engine"],
        "active_db_id": case["db_id"],
    }


def _adapter_ids(case: dict[str, Any]) -> set[str]:
    return {case["db_id"]} if case["db_id"] else set()


def _app_config(adapter_ids: set[str]) -> AppConfig:
    """멀티 경로용 실 `AppConfig` — 검증 대상 필드를 명시하고 `.env` 누수를 막는다.

    중첩 설정은 `default_factory`로 만들어질 때 상대 경로 `.env`를 각자 읽는다 —
    빈 임시 디렉터리에서 만들어 저장소 `.env`가 어느 중첩 설정에도 들어오지 않게 한다.
    """
    with tempfile.TemporaryDirectory() as empty, pytest.MonkeyPatch.context() as mp:
        mp.chdir(empty)
        return AppConfig(
            _env_file=None,  # type: ignore[call-arg]
            polestar_db_ids=",".join(sorted(adapter_ids)),
            text2sql=Text2SQLConfig(
                _env_file=None,  # type: ignore[call-arg]
                semantic_compose=False,
                generic_llm_mapping=False,
                path_parity=False,
            ),
        )


async def _run_single(case: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    start = len(calls)
    result = single_mod._try_build_form_fill_pivot_sql(
        _single_state(case), 100_000, case["query"],
        adapter_db_ids=_adapter_ids(case) or None,
        query_time=case["query_time"],
    )
    assert result is not None and result.get("sql"), "단일 경로가 결정적 조립을 하지 않았다"
    new = calls[start:]
    assert len(new) == 1, f"단일 경로 조립기 호출 수가 1이 아니다: {len(new)}"
    assert new[0]["sql"] == result["sql"]
    return new[0]


async def _run_multi(case: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    start = len(calls)
    ids = _adapter_ids(case)
    template = {"sheets": [{"title_text": case["title"], "name": "Sheet1"}]}
    sql = await multi_mod._generate_sql(
        # 결정적 경로 — LLM에 도달하면 실패해야 한다
        llm=None,  # type: ignore[arg-type]
        parsed_requirements={"original_query": case["query"]},
        schema_info=case["schema_info"],
        sub_query_context="양식 채우기",
        default_limit=100_000,
        column_mapping={f: c for f, c in case["mapping"].items() if c is not None},
        db_engine=case["engine"],
        db_id=case["db_id"],
        unmapped_fields=[f for f, c in case["mapping"].items() if c is None],
        app_config=_app_config(ids),
        form_context_text=template_context_text(template),
        form_fill_out={},
        form_intent=True,
        mapping_sources=dict(case["sources"]),
        query_time=case["query_time"],
    )
    new = calls[start:]
    assert len(new) == 1, f"멀티 경로 조립기 호출 수가 1이 아니다: {len(new)}"
    assert new[0]["sql"] == sql
    return new[0]


async def _collect(calls: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, case in _cases().items():
        single = await _run_single(case, calls)
        multi = await _run_multi(case, calls)
        out[name] = {
            "single": single["inputs"],
            "multi": multi["inputs"],
            "identical": single["inputs"] == multi["inputs"],
            "single_sql_sha256": hashlib.sha256(single["sql"].encode()).hexdigest(),
            "multi_sql_sha256": hashlib.sha256(multi["sql"].encode()).hexdigest(),
        }
    return out


def _dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


async def test_form_fill_pivot_inputs_match_snapshot(recorder):
    """단일/멀티 경로가 조립기에 넘기는 입력 인자가 스냅샷과 같다."""
    current = await _collect(recorder)

    if os.environ.get("UPDATE_PLAN162_SNAPSHOT") == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(_dumps(current), encoding="utf-8")
        pytest.skip(f"스냅샷을 갱신했다: {SNAPSHOT_PATH}")

    assert SNAPSHOT_PATH.exists(), (
        f"스냅샷이 없다: {SNAPSHOT_PATH} — UPDATE_PLAN162_SNAPSHOT=1로 최초 채록하라"
    )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert sorted(current) == sorted(expected), "케이스 집합이 달라졌다"
    for name in expected:
        assert current[name] == expected[name], f"폼필 피벗 입력이 달라졌다: {name}"


async def test_query_time_case_carries_period(recorder):
    """QueryTime 케이스가 실제로 period를 넘겨야 이 골든이 period 축을 덮는다(전제 확인)."""
    case = _cases()["db2_b0_query_time"]
    assert isinstance(case["query_time"], QueryTime)
    single = await _run_single(case, recorder)
    multi = await _run_multi(case, recorder)
    assert single["inputs"]["period"] is not None
    assert multi["inputs"]["period"] is not None
