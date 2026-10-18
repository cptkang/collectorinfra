"""plans/173 T5 특성화 — `schema_analyzer` 노드의 현재 동작을 고정한다(동작 불변 안전망).

노드 함수를 단계 함수로 뽑기 **전에** 기존 노드 테스트가 덮지 않던 분기를 포함해 아래를 고정한다.
기대값은 「맞는 값」이 아니라 「현재 값」이다
(`tests/snapshots/plan173_t5/schema_analyzer_flow.json`).

- 반환 dict 전체와 최상위 키 순서
- 이 모듈 로거의 로그(수준·문구·순서) — 집합 인자는 정렬해 문자열화한다(해시 순서 비결정 제거)
- 캐시 매니저·DB 클라이언트·수동 프로필 로더·LLM 호출 순서

외부 I/O(DB·Redis·LLM·값 인덱스·유사어 로더)는 모두 가짜로 대체한다.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# `src.nodes` 패키지가 노드 함수를 같은 이름으로 재노출하므로 모듈은 importlib로 잡는다.
sa = importlib.import_module("src.nodes.schema_analyzer")

_SNAP = (
    Path(__file__).resolve().parents[1]
    / "snapshots" / "plan173_t5" / "schema_analyzer_flow.json"
)
DB_ID = "t5_db"

_ALARM3 = ["cmm_alarm", "cmm_alarm_def", "cmm_alarm_active"]
_FILLER = [f"t{i:02d}" for i in range(1, 9)]


def _table(name: str, *, sample: list | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "columns": [
            {"name": "id", "type": "int", "primary_key": True},
            {"name": "name", "type": "varchar"},
        ],
    }
    if sample is not None:
        data["sample_data"] = sample
    return data


def _schema(names: list[str], samples: dict[str, list] | None = None) -> dict[str, Any]:
    samples = samples or {}
    return {
        "tables": {n: _table(n, sample=samples.get(n)) for n in names},
        "relationships": [{"from": "items.id", "to": "owners.id"}],
    }


class _Cache:
    """schema_analyzer가 쓰는 캐시 매니저 표면 — 호출 순서를 기록하고 지정 메서드는 실패시킨다."""

    def __init__(
        self,
        calls: list,
        *,
        schema: dict,
        descriptions: dict,
        synonyms: dict,
        db_syns: dict | None = None,
        redis: bool = False,
        applied: dict | None = None,
        synced: int = 0,
        global_syns: dict | None = None,
        fail: tuple[str, ...] = (),
    ) -> None:
        self._calls = calls
        self._schema = schema
        self._descriptions = descriptions
        self._synonyms = synonyms
        self._db_syns = db_syns or {}
        self.redis_available = redis
        self.redis_cache = SimpleNamespace(name="fake-redis")
        self._applied = applied
        self._synced = synced
        self._global = global_syns or {}
        self._fail = set(fail)

    def _hit(self, name: str, *args: Any) -> None:
        self._calls.append(["cache", name, *[_jsonable(a) for a in args]])
        if name in self._fail:
            raise RuntimeError(f"{name} 실패")

    async def get_schema_or_fetch(self, client, db_id):
        self._hit("get_schema_or_fetch", db_id)
        return (
            json.loads(json.dumps(self._schema)), False, "DB 직접 조회",
            dict(self._descriptions), dict(self._synonyms),
        )

    async def get_synonyms(self, db_id):
        self._hit("get_synonyms", db_id)
        return dict(self._db_syns)

    async def save_structure_meta(self, db_id, meta):
        self._hit("save_structure_meta", db_id)
        return True

    async def sync_known_attributes_to_eav_synonyms(self, detail):
        self._hit("sync_known_attributes_to_eav_synonyms", detail)
        return self._synced

    async def get_applied_structure_meta(self, db_id):
        self._hit("get_applied_structure_meta", db_id)
        return self._applied

    async def load_resource_type_synonyms(self):
        self._hit("load_resource_type_synonyms")
        return {"server": ["서버"]}

    async def load_eav_name_synonyms(self):
        self._hit("load_eav_name_synonyms")
        return {"OSType": ["운영체제"]}

    async def load_global_synonyms(self):
        self._hit("load_global_synonyms")
        return dict(self._global)


class _LLM:
    def __init__(self, calls: list, answer: str) -> None:
        self._calls = calls
        self._answer = answer

    async def ainvoke(self, messages, *args, **kwargs):
        self._calls.append(["llm", len(messages)])
        return MagicMock(content=self._answer)


class _Client:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    async def get_sample_data(self, table, limit=5):
        self._calls.append(["sample", table, limit])
        return [{"t": table}]


class _Loader:
    """`SynonymLoader` 가짜 — 글로벌 유사어 자동 로드 분기를 고정한다."""

    calls: list = []

    def __init__(self, redis_cache) -> None:
        _Loader.calls.append(["loader.init", redis_cache.name])

    async def load_auto(self, path):
        _Loader.calls.append(["loader.load_auto", path])
        return SimpleNamespace(message="3건 로드")


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=_default))


def _default(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return repr(value)


def _norm_arg(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return value


class _LogCatcher(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[list[str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        args = record.args
        if isinstance(args, tuple):
            msg = str(record.msg) % tuple(_norm_arg(a) for a in args) if args else str(record.msg)
        else:
            msg = record.getMessage()
        self.lines.append([record.levelname, msg])


def _config(*, value_retrieval: bool = False, skip: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        synonym=SimpleNamespace(
            value_retrieval=value_retrieval,
            max_synonym_supplement_tables=15,
            fuzzy_match=False,
            match_confidence_min=0.85,
            semantic_match=False,
            semantic_confidence_min=0.65,
        ),
        text2sql=SimpleNamespace(schema_table_select_skip_enabled=skip),
    )


def _state(
    *, query: str, targets: list[str], intent: str | None, notes: list | None = None,
) -> dict:
    return {
        "parsed_requirements": {
            "query_targets": targets, "original_query": query, "output_format": "text",
        },
        "active_db_id": DB_ID,
        "routing_intent": intent,
        "dependency_notes": notes or [],
        "active_db_engine": "postgresql",
    }


_PROFILE_FULL = {
    "source": "manual",
    "allowed_tables": ["items", "owners"],
    "alarm_allowed_tables": ["cmm_alarm", "cmm_alarm_def", "cmm_resource"],
    "patterns": [
        {
            "type": "eav",
            "entity_table": "items",
            "config_table": "item_props",
            "known_attributes": ["OSType"],
            "known_attributes_detail": [{"name": "OSType", "synonyms": ["운영체제"]}],
        },
    ],
    "query_guide": "가이드",
}


def _case_spec(name: str) -> dict[str, Any]:
    """케이스별 입력 — 집합 순회 순서가 결과에 드러나지 않게 보충·제거 대상을 1개로 둔다."""
    base_tables = ["items", "owners", "item_props", "extra", "t01"]
    if name == "allowed_filter_full":
        return dict(
            state=_state(
                query="항목 추가항목 목록", targets=["항목"], intent="data_query",
                notes=[{"kind": "기존", "db_id": "other", "text": "기존 노트"}],
            ),
            schema=_schema(base_tables, samples={"items": [{"id": 1, "name": "a"}]}),
            descriptions={}, synonyms={"items.name": ["이름"]},
            db_syns={"extra.col": ["추가항목"]},
            profile=_PROFILE_FULL, llm="items, extra",
            redis=True, synced=2, value_retrieval=True,
        )
    if name == "failures_everywhere":
        return dict(
            state=_state(query="항목 추가항목 목록", targets=["항목"], intent="data_query"),
            schema=_schema(base_tables),
            descriptions={"items.id": "식별자"}, synonyms={},
            profile=_PROFILE_FULL, llm="items, extra, t01",
            redis=True, value_retrieval=True,
            fail=(
                "get_synonyms", "save_structure_meta", "sync_known_attributes_to_eav_synonyms",
                "load_resource_type_synonyms", "load_global_synonyms",
            ),
            domain_fail=True,
        )
    if name == "applied_meta_defaults":
        return dict(
            state=_state(query="항목 목록", targets=[], intent=None),
            schema=_schema(["items", "owners"]),
            descriptions={"items.id": "식별자"}, synonyms={},
            profile=None, llm="", applied={"patterns": [], "query_guide": "승인본"},
            redis=True, global_syns={"서버": ["server"]}, defaults=True,
        )
    if name == "applied_meta_fails":
        return dict(
            state=_state(query="항목 목록", targets=["항목"], intent=None),
            schema=_schema(["items", "owners"]),
            descriptions={}, synonyms={},
            profile=None, llm="items, nope",
            fail=("get_applied_structure_meta",),
        )
    if name == "alarm_allowed_filter":
        return dict(
            state=_state(query="알람 목록", targets=["알람"], intent="alarm_query"),
            schema=_schema(_ALARM3 + ["cmm_resource"] + _FILLER),
            descriptions={"x.y": "z"}, synonyms={},
            profile=_PROFILE_FULL, llm="cmm_alarm, cmm_alarm_def, cmm_resource, t01",
        )
    if name == "alarm_allowed_empty":
        return dict(
            state=_state(query="알람 목록", targets=["알람"], intent="alarm_query"),
            schema=_schema(["cmm_alarm", "cmm_resource"] + _FILLER),
            descriptions={"x.y": "z"}, synonyms={},
            profile=_PROFILE_FULL, llm="t01, t02",
        )
    if name == "alarm_llm_fallback":
        return dict(
            state=_state(query="알람 목록", targets=[], intent="alarm_query"),
            schema=_schema(["cmm_alarm", "cmm_resource"] + _FILLER + ["t09", "t10"]),
            descriptions={"x.y": "z"}, synonyms={},
            profile={"source": "manual", "patterns": []}, llm="",
        )
    if name == "q5_skip_reuses_signal":
        return dict(
            state=_state(query="항목 목록", targets=["항목"], intent="data_query"),
            schema=_schema(base_tables),
            descriptions={"x.y": "z"}, synonyms={},
            db_syns={"items.name": ["항목"]},
            profile=_PROFILE_FULL, llm="t01", skip=True,
        )
    if name == "schema_fetch_error":
        return dict(
            state=_state(query="항목 목록", targets=["항목"], intent=None),
            schema=_schema(["items"]),
            descriptions={}, synonyms={},
            profile=None, llm="items",
            fail=("get_schema_or_fetch",),
        )
    raise KeyError(name)


CASES = [
    "allowed_filter_full",
    "failures_everywhere",
    "applied_meta_defaults",
    "applied_meta_fails",
    "alarm_allowed_filter",
    "alarm_allowed_empty",
    "alarm_llm_fallback",
    "q5_skip_reuses_signal",
    "schema_fetch_error",
]


async def run_case(name: str) -> dict[str, Any]:
    """케이스를 구동해 반환 dict·로그·호출 순서를 JSON 가능한 기록으로 돌려준다."""
    spec = _case_spec(name)
    calls: list = []
    _Loader.calls = calls
    cache = _Cache(
        calls,
        schema=spec["schema"],
        descriptions=spec["descriptions"],
        synonyms=spec["synonyms"],
        db_syns=spec.get("db_syns"),
        redis=spec.get("redis", False),
        applied=spec.get("applied"),
        synced=spec.get("synced", 0),
        global_syns=spec.get("global_syns"),
        fail=spec.get("fail", ()),
    )
    client = _Client(calls)
    llm = _LLM(calls, spec["llm"])
    cfg = _config(value_retrieval=spec.get("value_retrieval", False), skip=spec.get("skip", False))
    profile = spec["profile"]

    def _profile(db_id: str):
        calls.append(["profile", db_id])
        return json.loads(json.dumps(profile)) if profile is not None else None

    @asynccontextmanager
    async def _db_ctx(*_a, **kw):
        calls.append(["db_client", kw.get("db_id")])
        yield client

    def _cache_mgr(*_a, **_kw):
        calls.append(["get_cache_manager"])
        return cache

    def _domain(db_id):
        calls.append(["get_domain_by_id", db_id])
        if spec.get("domain_fail"):
            raise RuntimeError("도메인 조회 실패")
        return SimpleNamespace(db_engine="postgresql")

    async def _value_index(db_id, structure_meta, cl, engine, *, redis_cache=None):
        calls.append([
            "value_index", db_id, sorted((structure_meta or {}).keys()),
            cl is client, engine, getattr(redis_cache, "name", None),
        ])
        return {"items.name": ["a", "b"]}

    catcher = _LogCatcher()
    logger = logging.getLogger(sa.__name__)
    old_level = logger.level
    logger.addHandler(catcher)
    logger.setLevel(logging.DEBUG)
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(sa, "get_db_client", side_effect=_db_ctx))
            stack.enter_context(patch.object(sa, "get_cache_manager", side_effect=_cache_mgr))
            stack.enter_context(patch.object(sa, "_load_manual_profile", side_effect=_profile))
            stack.enter_context(patch.object(sa, "hangul_identifiers_allowed", return_value=False))
            stack.enter_context(
                patch("src.routing.domain_config.get_domain_by_id", side_effect=_domain)
            )
            stack.enter_context(
                patch("src.schema_cache.value_index.load_or_build_value_index", new=_value_index)
            )
            stack.enter_context(patch("src.schema_cache.synonym_loader.SynonymLoader", new=_Loader))
            if spec.get("defaults"):
                stack.enter_context(patch.object(sa, "load_config", return_value=cfg))
                stack.enter_context(patch.object(sa, "create_llm", return_value=llm))
                result = await sa.schema_analyzer(spec["state"])
            else:
                result = await sa.schema_analyzer(spec["state"], llm=llm, app_config=cfg)
    finally:
        logger.removeHandler(catcher)
        logger.setLevel(old_level)
    return {
        "keys": list(result.keys()),
        "result": _jsonable(result),
        "logs": catcher.lines,
        "calls": calls,
    }


def _snapshot() -> dict[str, Any]:
    return json.loads(_SNAP.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", CASES)
def test_schema_analyzer_flow_matches_snapshot(name: str) -> None:
    """반환 dict·키 순서·로그 순서·호출 순서가 착수 시점 값과 같다."""
    got = asyncio.run(run_case(name))
    want = _snapshot()[name]
    assert got["keys"] == want["keys"]
    assert got["result"] == want["result"]
    assert got["logs"] == want["logs"]
    assert got["calls"] == want["calls"]


def test_snapshot_covers_all_cases() -> None:
    assert sorted(_snapshot()) == sorted(CASES)
