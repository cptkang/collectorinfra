"""plans/138 W6 — ITAM 벤치 보강(a~g) · 누출 관문(D-301 부기).

a) 설명·유사어 출처를 서버와 같은 순서(Redis → 파일)로 · b) §9 지연 문구 평면별 · c) closed 정책
카나리아 「해당 없음」 · d) 측정 연결점의 프롬프트 크기·선별 칸 · e) 되물음 종류 · f) 실패 분류
`backend_limit`·`selection_none` · g) 테이블 의미 ← 승인 `table_definitions.manages`.

LLM·DB·서버 0. Redis 는 인메모리 페이크(`tests/mocks/async_redis.py`)만 쓴다.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from scripts.itam_bench import POLICY_PATH, SCENARIOS_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench import report as rp
from scripts.itam_bench._serve import schema_context_record
from scripts.scenario.assertions import Observation
from src.schema_cache.persistent_cache import PersistentSchemaCache
from src.schema_cache.redis_cache import RedisSchemaCache
from tests.mocks.async_redis import attach_fake_redis

_FIXTURES = Path(__file__).parent / "fixtures"
_REDIS_DESC = {"TCDMSIF80.sevrHostName": "서버 호스트 이름(Redis)"}
_FILE_DESC = {"TCDMSIF80.sevrHostName": "서버 호스트 이름(파일)"}
_REDIS_SYN = {"TCDMSIF80.sevrHostName": ["서버명", "호스트명"]}
_FILE_SYN = {"TCDMSIF80.sevrHostName": ["장비명"]}


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


def _file_cache(directory: Path, *, descriptions: Any = None, synonyms: Any = None) -> Path:
    cache = PersistentSchemaCache(cache_dir=str(directory))
    schema = {
        "tables": {"TCDMSIF80": {"columns": [{"name": "sevrHostName", "type": "varchar"}]}},
        "relationships": [],
    }
    assert cache.save("itam", schema, fingerprint="fp")
    if descriptions:
        assert cache.save_descriptions("itam", descriptions)
    if synonyms:
        assert cache.save_synonyms("itam", synonyms)
    return directory


def _cfg(backend: str, cache_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        schema_cache=SimpleNamespace(backend=backend, cache_dir=str(cache_dir)), redis=MagicMock()
    )


def _fake_redis(*, descriptions: Any = None, synonyms: Any = None) -> RedisSchemaCache:
    redis = RedisSchemaCache(MagicMock())
    attach_fake_redis(redis)

    async def fill() -> None:
        if descriptions:
            assert await redis.save_descriptions("itam", descriptions)
        if synonyms:
            assert await redis.save_synonyms("itam", synonyms)

    asyncio.run(fill())
    return redis


class _DownRedis:
    """연결이 안 되는 Redis — 읽기를 부르면 안 된다."""

    def __init__(self, *, raise_on_connect: bool = False) -> None:
        self.raise_on_connect = raise_on_connect

    async def ensure_connected(self) -> bool:
        if self.raise_on_connect:
            raise ConnectionError("down")
        return False

    async def load_descriptions(self, db_id: str) -> dict[str, str]:
        raise AssertionError("연결 실패 뒤 읽기")

    load_synonyms = load_descriptions


# ── a) 설명·유사어 출처: Redis → 파일 ─────────────────────────────────────


class TestServerAnnotations:
    def test_redis_first(self, tmp_path: Path) -> None:
        directory = _file_cache(tmp_path, descriptions=_FILE_DESC, synonyms=_FILE_SYN)
        schema: dict[str, Any] = {"descriptions": dict(_FILE_DESC)}
        cat.apply_server_annotations(
            schema,
            cfg=_cfg("redis", directory),
            redis_cache=_fake_redis(descriptions=_REDIS_DESC, synonyms=_REDIS_SYN),
        )
        assert schema["descriptions"] == _REDIS_DESC
        assert schema["synonym_counts"] == {"TCDMSIF80.sevrHostName": 2}
        assert schema["annotation_sources"] == {
            "redis": "ok",
            "descriptions": "redis",
            "synonyms": "redis",
        }

    def test_empty_redis_falls_back_to_file_per_kind(self, tmp_path: Path) -> None:
        """서버처럼 설명·유사어를 따로 판단한다 — Redis 에 유사어만 있으면 설명은 파일."""
        directory = _file_cache(tmp_path, descriptions=_FILE_DESC, synonyms=_FILE_SYN)
        schema: dict[str, Any] = {}
        cat.apply_server_annotations(
            schema, cfg=_cfg("redis", directory), redis_cache=_fake_redis(synonyms=_REDIS_SYN)
        )
        assert schema["descriptions"] == _FILE_DESC
        assert schema["synonym_counts"] == {"TCDMSIF80.sevrHostName": 2}
        assert schema["annotation_sources"] == {
            "redis": "ok",
            "descriptions": "file",
            "synonyms": "redis",
        }

    @pytest.mark.parametrize("raise_on_connect", [False, True])
    def test_redis_unavailable_falls_back_to_file_and_records_it(
        self, tmp_path: Path, raise_on_connect: bool
    ) -> None:
        directory = _file_cache(tmp_path, descriptions=_FILE_DESC, synonyms=_FILE_SYN)
        schema: dict[str, Any] = {}
        cat.apply_server_annotations(
            schema,
            cfg=_cfg("redis", directory),
            redis_cache=_DownRedis(raise_on_connect=raise_on_connect),
        )
        assert schema["descriptions"] == _FILE_DESC
        assert schema["synonym_counts"] == {"TCDMSIF80.sevrHostName": 1}
        assert schema["annotation_sources"] == {
            "redis": "unavailable",
            "descriptions": "file",
            "synonyms": "file",
        }

    def test_file_backend_does_not_touch_redis(self, tmp_path: Path) -> None:
        directory = _file_cache(tmp_path, descriptions=_FILE_DESC)
        schema: dict[str, Any] = {}
        cat.apply_server_annotations(
            schema, cfg=_cfg("file", directory), redis_cache=_DownRedis(raise_on_connect=True)
        )
        assert schema["annotation_sources"] == {
            "redis": "off",
            "descriptions": "file",
            "synonyms": "none",
        }
        assert schema["synonym_counts"] == {}

    def test_missing_cache_dir_is_none_and_not_created(self, tmp_path: Path) -> None:
        missing = tmp_path / "nowhere"
        schema: dict[str, Any] = {"descriptions": {"a.b": "옛 값"}}
        cat.apply_server_annotations(schema, cfg=_cfg("file", missing))
        assert schema["descriptions"] == {}
        assert schema["annotation_sources"]["descriptions"] == "none"
        assert not missing.exists()

    def test_owned_redis_built_from_config_and_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        made: list[Any] = []

        class _Stub:
            def __init__(self, *, redis_config: Any, schema_cache_config: Any) -> None:
                self.config = (redis_config, schema_cache_config)
                self.closed = False
                made.append(self)

            async def ensure_connected(self) -> bool:
                return True

            async def load_descriptions(self, db_id: str) -> dict[str, str]:
                return dict(_REDIS_DESC)

            async def load_synonyms(self, db_id: str) -> dict[str, list[str]]:
                return {}

            async def disconnect(self) -> None:
                self.closed = True

        monkeypatch.setattr("src.schema_cache.redis_cache.RedisSchemaCache", _Stub)
        cfg = _cfg("redis", tmp_path)
        schema: dict[str, Any] = {}
        cat.apply_server_annotations(schema, cfg=cfg)
        assert len(made) == 1 and made[0].config == (cfg.redis, cfg.schema_cache)
        assert made[0].closed
        assert schema["annotation_sources"]["descriptions"] == "redis"

    def test_catalog_carries_sources_and_counts(
        self, tmp_path: Path, policy: cat.ColumnPolicy
    ) -> None:
        schema = cat.load_schema_source("transcript")
        cat.apply_server_annotations(
            schema,
            cfg=_cfg("redis", tmp_path / "none"),
            redis_cache=_fake_redis(descriptions=_REDIS_DESC, synonyms=_REDIS_SYN),
        )
        catalog = cat.build_schema_catalog(schema, policy, assets={}, repo_root=tmp_path)
        assert catalog["annotation_sources"] == {
            "redis": "ok",
            "descriptions": "redis",
            "synonyms": "redis",
        }
        column = {c["name"]: c for c in catalog["tables"]["TCDMSIF80"]["columns"]}["sevrHostName"]
        assert column["meaning_source"] == "cache_description"
        assert column["synonyms"] == 2
        assert catalog["summary"]["columns_with_meaning"] == 1
        assert "구별할 수 없다" in catalog["meaning_sources"]["cache_description"]
        # 유사어 낱말은 싣지 않는다
        assert "서버명" not in json.dumps(catalog, ensure_ascii=False)


# ── b) §9 지연 문구 · c) closed 카나리아 ──────────────────────────────────


def _trace_records() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (_FIXTURES / "itam_bench_trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]


def _run(**overrides: Any) -> dict[str, Any]:
    run = {
        "run_id": "20261006-120000",
        "env": "closed",
        "planes": {"worker": "mlx", "orchestrator": "mlx"},
        "scenarios": 1,
        "repeat": 1,
        "sql_observed_turns": 1,
        "judged": True,
        "canary_in_results": 0,
    }
    run.update(overrides)
    return run


def _report(run: dict[str, Any]) -> str:
    return rp.render_report(run, {"source": "schema_cache", "assets": {}}, _trace_records())


class TestReportWording:
    def test_mlx_planes_keep_mlx_wording(self) -> None:
        assert "로컬 MLX 값이라 성능 결론을 내지 않는다(D-240)." in _report(_run())

    def test_non_mlx_planes_name_the_planes(self) -> None:
        text = _report(_run(planes={"worker": "fabrix", "orchestrator": "vllm"}))
        section = text.split("## 9. 지연(참고)")[1]
        assert "평면 워커 `fabrix` / 오케스트레이터 `vllm`에서 잰 값(참고)." in section
        assert "MLX" not in section

    def test_mixed_planes_say_mlx_is_mixed(self) -> None:
        note = rp._latency_note(_run(planes={"worker": "fabrix", "orchestrator": "mlx"}))
        assert "`fabrix`" in note and "`mlx`" in note and "섞인" in note and "D-240" in note

    def test_missing_planes_are_neutral(self) -> None:
        note = rp._latency_note({})
        assert "MLX" not in note and "`—`" in note

    def test_closed_policy_canary_is_not_applicable(self) -> None:
        text = _report(_run(policy_scope="closed"))
        assert "**해당 없음**" in text
        assert "불성립" not in text

    def test_sandbox_without_canary_still_warns(self) -> None:
        text = _report(_run(policy_scope="sandbox", env="sandbox"))
        assert "누출 관문 시험 **불성립**" in text and "해당 없음" not in text

    def test_canary_seen_wins(self) -> None:
        text = _report(_run(policy_scope="closed", canary_in_results=2))
        assert "누출 관문 시험 성립" in text and "해당 없음" not in text


# ── d) 측정 연결점 · trace `schema_context.dbs` ─────────────────────────────


_SCHEMA_INFO = {
    "tables": {
        "TCDMSIF80": {
            "columns": [{"name": "sevrHostName"}, {"name": "rspblPsnEmnm"}],
            "sample_data": [{"sevrHostName": "svr-db-03"}],
        }
    }
}


def _single_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "thread_id": "t-1",
        "active_db_id": "itam",
        "schema_info": _SCHEMA_INFO,
        "column_descriptions": {"TCDMSIF80.sevrHostName": "서버 호스트 이름"},
        "prompt_budget": {
            "estimated_tokens": 18234,
            "budget": 24000,
            "stage": "samples",
            "table_count": 99,
            "samples": False,
        },
        "validation_result": {
            "passed": False,
            "reason": "입력 41234 토큰이 한도 32768 을 넘었다 — SELECT * FROM TCDMSIF99",
            "backend_error": {"kind": "token_limit", "given": 41234, "limit": 32768},
        },
        "table_selection": {
            "itam": {
                "mode": "definitions",
                "source": "lexical",
                "candidates": 99,
                "selected": ["TCDMSIF80", "TCDMSIF99"],
                "bridged": ["TCDMSIF98"],
            }
        },
        "regen_stop": {"reason": "backend_limit", "detail": "입력 41234 토큰 — 합성부서"},
    }
    state.update(overrides)
    return state


class TestPromptShape:
    def test_single_path_copies_numbers_and_enums_only(self) -> None:
        record = schema_context_record("task_pipeline_state", _single_state())
        entry = record["dbs"]["itam"]
        assert {k: entry[k] for k in entry if k not in ("tables", "structure_meta")} == {
            "prompt_tokens_est": 18234,
            "budget_stage": "samples",
            "backend_reported_tokens": 41234,
            "selection_source": "lexical",
            "selected_count": 2,
            "stop_reason": "backend_limit",
        }
        text = repr(record)
        # 선별 테이블 이름·사유 문구·한도 문장·표본 값은 옮기지 않는다
        for leaked in ("TCDMSIF99", "TCDMSIF98", "합성부서", "SELECT", "32768", "svr-db-03"):
            assert leaked not in text

    def test_absent_state_fields_are_null(self) -> None:
        state = _single_state(
            prompt_budget=None, validation_result=None, table_selection=None, regen_stop=None
        )
        entry = schema_context_record("task_pipeline_state", state)["dbs"]["itam"]
        assert [entry[k] for k in ("prompt_tokens_est", "budget_stage", "stop_reason")] == [
            None,
            None,
            None,
        ]
        assert entry["selection_source"] is None and entry["selected_count"] is None
        assert entry["backend_reported_tokens"] is None

    def test_off_contract_values_are_null(self) -> None:
        state = _single_state(
            prompt_budget={"estimated_tokens": True, "stage": "huge"},
            validation_result={"backend_error": {"kind": "token_limit", "given": -1}},
            table_selection={"itam": {"source": "magic", "selected": "TCDMSIF80"}},
            regen_stop={"reason": "입력이 너무 크다"},
        )
        entry = schema_context_record("task_pipeline_state", state)["dbs"]["itam"]
        assert all(
            entry[k] is None
            for k in (
                "prompt_tokens_est",
                "budget_stage",
                "backend_reported_tokens",
                "selection_source",
                "selected_count",
                "stop_reason",
            )
        )

    def test_multi_path_reads_per_db_selection_and_stops(self) -> None:
        state = {
            "thread_id": "t-2",
            "is_multi_db": True,
            "db_schemas": {"itam": _SCHEMA_INFO},
            # 멀티 상태의 단일 경로 칸은 DB 를 모른다 — 읽지 않는다
            "prompt_budget": {"estimated_tokens": 999, "stage": "exceeded"},
            "validation_result": {"backend_error": {"kind": "token_limit", "given": 5}},
            "table_selection": {
                "itam": {"mode": "definitions", "source": "none", "candidates": 99, "selected": []},
                "polestar": {"source": "llm", "selected": ["a", "b", "c"]},
            },
            "regen_stops": {"itam": {"reason": "selection_none", "detail": "후보 0개"}},
        }
        dbs = schema_context_record("task_pipeline_state", state)["dbs"]
        assert set(dbs) == {"itam", "polestar"}
        assert dbs["itam"]["selection_source"] == "none"
        assert dbs["itam"]["selected_count"] == 0
        assert dbs["itam"]["stop_reason"] == "selection_none"
        assert dbs["itam"]["prompt_tokens_est"] is None
        assert dbs["itam"]["backend_reported_tokens"] is None
        assert dbs["itam"]["tables"]["TCDMSIF80"]["with_meaning"] is None
        assert dbs["polestar"]["tables"] == {}
        assert dbs["polestar"]["selected_count"] == 3

    def test_turn_summary_aggregates_tasks(self) -> None:
        first = schema_context_record("task_pipeline_state", _single_state())
        second = schema_context_record(
            "task_pipeline_state",
            _single_state(
                prompt_budget={"estimated_tokens": 9000, "stage": "within"},
                validation_result={},
                table_selection={"itam": {"source": "none", "selected": []}},
                regen_stop={"reason": "selection_none"},
            ),
        )
        context = jd.schema_context(
            [first, second], db_id="itam", gold_tables=["TCDMSIF80"], key_refs=[]
        )
        assert context["dbs"] == {
            "itam": {
                "prompt_tokens_est": 18234,
                "budget_stage": "samples",
                "backend_reported_tokens": 41234,
                "selection_source": "none",
                "selected_count": 2,
                "stop_reasons": ["backend_limit", "selection_none"],
            }
        }


# ── e) 되물음 종류 ─────────────────────────────────────────────────────────


_SOURCE_SELECT = {
    "kind": "source_select",
    "question": "어느 데이터 소스에서 담당 부서를 찾을까요?",
    "options": [
        {"source": "itam", "label": "자산관리", "group": "itam"},
        {
            "db_ids": ["polestar_cm_gp", "polestar_cm_yd"],
            "label": "폴스타",
            "group": "polestar",
            "source": "polestar",
        },
    ],
    "original_query": "홍길동 담당 서버",
    "multi": True,
}


class TestClarificationRecord:
    def test_source_select_keeps_kind_chips_and_ids_only(self) -> None:
        record = cli.clarification_record(_SOURCE_SELECT, "clarification")
        assert record == {
            "kind": "source_select",
            "chips": True,
            "options": 2,
            "candidate_sources": ["itam", "polestar"],
        }
        for leaked in ("자산관리", "담당 부서", "홍길동", "폴스타"):
            assert leaked not in repr(record)

    def test_zone_options_without_source_use_db_ids(self) -> None:
        payload = {
            "kind": "zone_select",
            "options": [
                {"db_id": "polestar_cm_gp", "label": "김포"},
                {"db_ids": ["polestar_b0"], "label": "은행"},
                {"db_id": "이름 같은 값", "label": "x"},
            ],
        }
        assert cli.clarification_record(payload, "clarification")["candidate_sources"] == [
            "polestar_b0",
            "polestar_cm_gp",
        ]

    def test_question_without_options_has_no_chips(self) -> None:
        payload = {"kind": "되물음", "question": "어느?"}
        assert cli.clarification_record(payload, "clarification") == {
            "kind": None,
            "chips": False,
            "options": 0,
            "candidate_sources": [],
        }
        assert cli.clarification_record(None, "clarification")["chips"] is False

    def test_not_a_clarification_is_none(self) -> None:
        assert cli.clarification_record(_SOURCE_SELECT, "completed") is None

    def test_turn_record_carries_it(self, policy: cat.ColumnPolicy) -> None:
        class _AskClient:
            def send(self, endpoint: str, payload: dict[str, Any]) -> Observation:
                obs = Observation(http_status=200, status="clarification", response="어느 소스?")
                obs.clarification = _SOURCE_SELECT
                return obs

            def download_csv(self, query_id: str) -> dict[str, Any]:
                raise AssertionError("되물음 턴은 결과를 받지 않는다")

        class _NoTail:
            def mark(self) -> int:
                return 0

            def collect(self, since: int, thread_id: str) -> list[dict[str, Any]]:
                return []

        scenario = next(s for s in cat.load_scenarios(SCENARIOS_PATH, policy) if s.id == "ITAM-06")
        catalog_doc = cat.build_schema_catalog(
            cat.load_schema_source("transcript"), policy, assets={}
        )
        ctx = cli.RunContext(
            run_id="20261006-120000",
            tier="intent_orchestration",
            policy=policy,
            catalog=jd.CatalogFacts.from_catalog(catalog_doc),
            vault=rd.PiiVault.from_policy(policy),
            oracle=lambda spec, anchor, tag: {
                "status": "ok",
                "reason": None,
                "rows_by_db": {"itam": [{"n": 1}]},
                "elapsed_ms": 1.0,
                "phase": "post",
                "limit_by_db": {"itam": 100},
            },
        )
        records = cli.run_scenarios(
            [scenario],
            client=_AskClient(),
            audit=_NoTail(),
            capture=_NoTail(),
            ctx=ctx,
            progress=lambda _t: None,
        )
        assert records[0]["clarification"] == {
            "kind": "source_select",
            "chips": True,
            "options": 2,
            "candidate_sources": ["itam", "polestar"],
        }


# ── f) 실패 분류 ──────────────────────────────────────────────────────────


def _facts(dbs: dict[str, Any], **overrides: Any) -> jd.TurnFacts:
    base: dict[str, Any] = {
        "status": "error",
        "expected_db_ids": ["itam"],
        "observed_db_ids": ["itam"],
        "verdict": "fail",
        "schema_context": {"dbs": dbs},
    }
    base.update(overrides)
    return jd.TurnFacts(**base)


def _prompt_entry(**fields: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "prompt_tokens_est": None,
        "budget_stage": None,
        "backend_reported_tokens": None,
        "selection_source": None,
        "selected_count": None,
        "stop_reasons": [],
    }
    entry.update(fields)
    return entry


class TestStopClassification:
    @pytest.mark.parametrize(
        "fields, label",
        [
            ({"stop_reasons": ["backend_limit"]}, "backend_limit"),
            ({"budget_stage": "exceeded"}, "backend_limit"),
            ({"backend_reported_tokens": 41234}, "backend_limit"),
            ({"stop_reasons": ["selection_none"]}, "selection_none"),
            ({"selection_source": "none", "selected_count": 0}, "selection_none"),
        ],
    )
    def test_labels(self, fields: dict[str, Any], label: str) -> None:
        labels = jd.classify(_facts({"itam": _prompt_entry(**fields)}), {}, db_id="itam")
        assert label in labels

    def test_within_budget_and_selected_is_neither(self) -> None:
        entry = _prompt_entry(budget_stage="within", selection_source="llm", selected_count=4)
        labels = jd.classify(_facts({"itam": entry}), {}, db_id="itam")
        assert "backend_limit" not in labels and "selection_none" not in labels

    def test_other_db_does_not_count(self) -> None:
        dbs = {"polestar": _prompt_entry(stop_reasons=["backend_limit"])}
        assert "backend_limit" not in jd.classify(_facts(dbs), {}, db_id="itam")

    def test_observe_turn_keeps_them(self) -> None:
        facts = _facts(
            {"itam": _prompt_entry(stop_reasons=["backend_limit", "selection_none"])},
            verdict=None,
            observe={"what": "되묻는가", "no_data": False},
        )
        assert jd.classify(facts, {}, db_id="itam") == ["backend_limit", "selection_none"]

    def test_pass_has_no_labels(self) -> None:
        facts = _facts({"itam": _prompt_entry(stop_reasons=["backend_limit"])}, verdict="pass")
        assert jd.classify(facts, {}, db_id="itam") == []

    def test_taxonomy_has_fix_targets(self) -> None:
        assert {"backend_limit", "selection_none"} <= set(jd.TAXONOMY)


# ── g) 테이블 의미 ← 승인 table_definitions.manages ─────────────────────────


class TestTableMeaning:
    def test_manages_fills_table_meaning(self, tmp_path: Path, policy: cat.ColumnPolicy) -> None:
        profile = {
            "table_definitions": {
                "INST1.tcdmsif80": {"manages": "  서버 자산 현행 원장  ", "origin": "import"},
                "TCDMSIF79": {"manages": " ", "origin": "import"},
            }
        }
        catalog = cat.build_schema_catalog(
            cat.load_schema_source("transcript"),
            policy,
            assets={},
            profile=profile,
            repo_root=tmp_path,
        )
        assert catalog["tables"]["TCDMSIF80"]["meaning"] == "서버 자산 현행 원장"
        assert catalog["tables"]["TCDMSIF80"]["meaning_source"] == "table_definitions"
        assert catalog["tables"]["TCDMSIF79"]["meaning"] is None
        assert catalog["tables"]["TCDMSIF79"]["meaning_source"] == "none"
        assert catalog["summary"]["tables_with_meaning"] == 1
        assert "table_definitions" in catalog["meaning_sources"]

    def test_definition_wins_over_db_comment(
        self, tmp_path: Path, policy: cat.ColumnPolicy
    ) -> None:
        schema = {
            "source": "snapshot:x.json",
            "descriptions": {},
            "tables": {
                "T_A": {
                    "comment": "DB 주석",
                    "rows_estimate": None,
                    "primary_key": ["id"],
                    "columns": [
                        {"name": "id", "type": "int", "nullable": False, "is_key": True},
                    ],
                    "foreign_keys": [],
                },
                "T_B": {
                    "comment": "B 주석",
                    "rows_estimate": None,
                    "primary_key": ["id"],
                    "columns": [
                        {"name": "id", "type": "int", "nullable": False, "is_key": True},
                    ],
                    "foreign_keys": [],
                },
            },
        }
        profile = {"table_definitions": {"t_a": {"manages": "승인 정의", "origin": "manual"}}}
        catalog = cat.build_schema_catalog(
            schema, policy, assets={}, profile=profile, repo_root=tmp_path
        )
        table_a = catalog["tables"]["T_A"]
        assert (table_a["meaning"], table_a["meaning_source"]) == ("승인 정의", "table_definitions")
        assert catalog["tables"]["T_B"]["meaning_source"] == "db_comment"
        assert catalog["summary"]["tables_with_meaning"] == 2


# ── 누출 관문 — 새 필드가 실린 산출물도 통과 ───────────────────────────────


def test_leak_gate_passes_with_new_fields(tmp_path: Path, policy: cat.ColumnPolicy) -> None:
    records = _trace_records()
    context = jd.schema_context(
        [schema_context_record("task_pipeline_state", _single_state())],
        db_id="itam",
        gold_tables=["TCDMSIF80"],
        key_refs=[],
    )
    for record in records:
        record["schema_context"] = context
        record["clarification"] = cli.clarification_record(_SOURCE_SELECT, "clarification")
    schema = cat.load_schema_source("transcript")
    cat.apply_server_annotations(
        schema,
        cfg=_cfg("redis", tmp_path / "none"),
        redis_cache=_fake_redis(descriptions=_REDIS_DESC, synonyms=_REDIS_SYN),
    )
    catalog_doc = cat.build_schema_catalog(
        schema,
        policy,
        assets={},
        profile={"table_definitions": {"TCDMSIF80": {"manages": "서버 자산", "origin": "import"}}},
        repo_root=tmp_path,
    )
    staged = cli.build_artifacts(
        run_meta=_run(policy_scope="closed", planes={"worker": "fabrix", "orchestrator": "vllm"}),
        catalog_doc=catalog_doc,
        records=records,
    )
    gate = rd.LeakGate(
        policy=policy, vault=rd.PiiVault.from_policy(policy), user_values={"login_id": "5488923"}
    )
    ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
    assert ok and violations == []
    trace = (tmp_path / "run" / "trace.jsonl").read_text(encoding="utf-8")
    assert '"stop_reasons": ["backend_limit"]' in trace
    assert "TCDMSIF99" not in trace and "합성부서" not in trace
