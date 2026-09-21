"""답변 영역 소유 — 라우터 출력 계약 · 소유 검증 지점 ① · 분류 폴백 표기.

plans/102 X-7 · X-T3 · X-T4.

단언하는 것:
    O1 플래그 off — `_llm_classify` 반환·구조화 스키마·노드 출력이 종전과 같다(키 추가 0)
    O2 플래그 on  — `capabilities`(카탈로그 코드만)·`chain`이 실리고, 모르는 코드는
                   `dropped`로 보고된다
    O3 단일 호출·2단 분리 경로 대칭
    O4 3단 라우터 노드 소유 교정 — 단일 DB 시스템은 그 DB로, 다중 존 시스템은 이미 고른
       DB만/없으면 활성 전체
    O5 교정 제외 — 답변 영역 없음 · 사용자 직접 지정 · 선언 없는 DB · 비활성 소유자(사유 노트)
    O6 빈 분류·LLM 실패 폴백이 노트로 표기된다(on) / 표기되지 않는다(off)
    O7 D-004 — 소유 판정 함수는 질의 원문을 입력으로 받지 않는다
    O8 `description_locked` — 캐시 「상세」 덧붙이기를 끈다(기본 false = 종전)

실 LLM 호출 0(D-127) — 대본 LLM·함수 대역만 쓴다.
"""

from __future__ import annotations

import importlib
import inspect
import json

import pytest

from src.config import AppConfig, MultiDBConfig, RouterConfig
from src.routing import capability_ownership as own
from src.routing.domain_config import DB_DOMAINS
from src.routing.registry import get_registry, parse_registry
from src.routing.schemas import (
    DatabaseSelection,
    OwnershipDatabaseSelection,
    OwnershipRouterDecision,
    RouterDecision,
)
from src.state import create_initial_state
from src.utils.prior_dependency import NOTE_OWNERSHIP, NOTE_ROUTING_FALLBACK

# 패키지 `__init__`가 동명 함수를 re-export해 모듈을 가린다 — importlib만 모듈을 준다.
sr = importlib.import_module("src.routing.semantic_router")

_POLESTAR_ZONES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_ACTIVE = [*_POLESTAR_ZONES, "itam"]


def _cfg(*, ownership: bool, active: list[str] | None = None, two_stage: bool = False) -> AppConfig:
    """검증 대상 필드를 명시한 설정(.env 누수 차단)."""
    return AppConfig(
        multi_db=MultiDBConfig(
            active_db_ids_csv=",".join(active if active is not None else _ACTIVE)
        ),
        router=RouterConfig(
            capability_ownership_enabled=ownership,
            two_stage_enabled=two_stage,
            unknown_enabled=False,
            early_stop_enabled=False,
        ),
        enable_semantic_routing=True,
    )


def _domains(active: list[str] | None = None):
    ids = active if active is not None else _ACTIVE
    return [d for d in DB_DOMAINS if d.db_id in ids]


class _ScriptedLLM:
    """호출 순서대로 정해진 응답 원문을 돌려준다. 받은 시스템 프롬프트를 기록한다."""

    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def ainvoke(self, messages):
        self.prompts.append(messages[0].content)
        return type("R", (), {"content": self._responses.pop(0)})()


_RAW = json.dumps(
    {
        "intent": "data_query",
        "chain": ["server_usage", "asset_contract", "no_such_area"],
        "databases": [
            {
                "db_id": "polestar_b0",
                "relevance_score": 0.9,
                "reason": "r",
                "sub_query_context": "q1",
                "user_specified": False,
                "capabilities": ["server_usage", "bogus"],
            },
            {
                "db_id": "itam",
                "relevance_score": 0.85,
                "reason": "r",
                "sub_query_context": "q2",
                "user_specified": False,
                "capabilities": ["asset_contract"],
            },
        ],
    },
    ensure_ascii=False,
)


@pytest.fixture(autouse=True)
def _no_cache_manager(monkeypatch):
    """라우터 노드의 DB 설명 로드는 외부 캐시를 친다 — 실패 경로(디버그 로그 후 계속)로 고정한다."""

    def _boom(*_a, **_kw):
        raise RuntimeError("cache disabled in test")

    monkeypatch.setattr("src.schema_cache.cache_manager.get_cache_manager", _boom)


def _use(monkeypatch, cfg: AppConfig) -> AppConfig:
    monkeypatch.setattr(sr, "load_config", lambda: cfg)
    return cfg


# ──────────────────────────────────────────────
# O1 · O2 · O3 — `_llm_classify` 반환 계약
# ──────────────────────────────────────────────


class TestClassifyContract:
    async def test_off_contract_has_no_new_keys(self, monkeypatch):
        """★ off — LLM이 필드를 내도 반환에 실리지 않는다(종전 계약 그대로)."""
        _use(monkeypatch, _cfg(ownership=False))
        got = await sr._llm_classify(_ScriptedLLM(_RAW), "q", _domains())
        assert set(got) == {"intent", "databases", "dropped"}
        for entry in got["databases"]:
            assert set(entry) == {
                "db_id",
                "relevance_score",
                "sub_query_context",
                "user_specified",
                "reason",
            }
        assert got["dropped"] == []

    async def test_on_contract_carries_catalog_codes_only(self, monkeypatch):
        _use(monkeypatch, _cfg(ownership=True))
        got = await sr._llm_classify(_ScriptedLLM(_RAW), "q", _domains())
        assert set(got) == {"intent", "databases", "dropped", "chain"}
        by_id = {d["db_id"]: d for d in got["databases"]}
        assert by_id["polestar_b0"]["capabilities"] == ["server_usage"]
        assert by_id["itam"]["capabilities"] == ["asset_contract"]
        assert got["chain"] == ["server_usage", "asset_contract"]
        reasons = sorted((d["reason"], d["raw"]) for d in got["dropped"])
        assert reasons == [
            ("unknown_capability", "bogus"),
            ("unknown_chain_capability", "no_such_area"),
        ]

    async def test_on_unknown_capability_keeps_db_entry(self, monkeypatch):
        """E-2 항목 단위 격리 — 모르는 답변 영역 때문에 DB 항목을 버리지 않는다."""
        _use(monkeypatch, _cfg(ownership=True))
        raw = json.dumps(
            {
                "intent": "data_query",
                "databases": [{"db_id": "itam", "relevance_score": 0.9, "capabilities": ["nope"]}],
            }
        )
        got = await sr._llm_classify(_ScriptedLLM(raw), "q", _domains())
        assert [d["db_id"] for d in got["databases"]] == ["itam"]
        assert got["databases"][0]["capabilities"] == []
        assert got["chain"] == []

    async def test_on_empty_parse_contract(self, monkeypatch):
        _use(monkeypatch, _cfg(ownership=True))
        got = await sr._llm_classify(_ScriptedLLM("응답 불가"), "q", _domains())
        assert got == {"intent": "data_query", "databases": [], "dropped": [], "chain": []}

    async def test_off_empty_parse_contract_unchanged(self, monkeypatch):
        _use(monkeypatch, _cfg(ownership=False))
        got = await sr._llm_classify(_ScriptedLLM("응답 불가"), "q", _domains())
        assert got == {"intent": "data_query", "databases": []}

    async def test_two_stage_is_symmetric(self, monkeypatch):
        """O3 — 2단 분리 경로도 같은 반환 계약(capabilities·chain)을 낸다."""
        _use(monkeypatch, _cfg(ownership=True, two_stage=True))
        llm = _ScriptedLLM('data_query\n{"confidence": 0.9}', _RAW)
        got = await sr._llm_classify(llm, "q", _domains())
        assert got["chain"] == ["server_usage", "asset_contract"]
        assert {d["db_id"]: d["capabilities"] for d in got["databases"]} == {
            "polestar_b0": ["server_usage"],
            "itam": ["asset_contract"],
        }
        # 2단계 DB 선택 프롬프트에도 소유표가 실렸다(대칭)
        assert "## 답변 영역 소유표" in llm.prompts[1]

    async def test_two_stage_off_has_no_chain(self, monkeypatch):
        _use(monkeypatch, _cfg(ownership=False, two_stage=True))
        llm = _ScriptedLLM('data_query\n{"confidence": 0.9}', _RAW)
        got = await sr._llm_classify(llm, "q", _domains())
        assert "chain" not in got
        assert all("capabilities" not in d for d in got["databases"])
        assert "## 답변 영역 소유표" not in llm.prompts[1]

    async def test_two_stage_intent_without_databases_carries_empty_chain(self, monkeypatch):
        _use(monkeypatch, _cfg(ownership=True, two_stage=True))
        got = await sr._llm_classify(_ScriptedLLM("general_inference\n{}"), "q", _domains())
        assert got["chain"] == [] and got["databases"] == []


class TestStructuredSchema:
    """instructor 백엔드는 응답 모델 스키마를 프롬프트에 싣는다 — off 스키마가 바뀌면 안 된다."""

    def test_off_models_have_no_new_fields(self):
        for model in (RouterDecision, DatabaseSelection):
            schema = json.dumps(model.model_json_schema(), ensure_ascii=False)
            assert "capabilities" not in schema and "chain" not in schema

    def test_on_models_add_fields(self):
        for model in (OwnershipRouterDecision, OwnershipDatabaseSelection):
            schema = json.dumps(model.model_json_schema(), ensure_ascii=False)
            assert "capabilities" in schema and "chain" in schema

    @pytest.mark.parametrize(
        "ownership,expected", [(False, RouterDecision), (True, OwnershipRouterDecision)]
    )
    async def test_classify_passes_flag_matched_model(self, monkeypatch, ownership, expected):
        _use(monkeypatch, _cfg(ownership=ownership))
        seen: list[type] = []

        async def _capture(llm, messages, response_model, **kw):
            seen.append(response_model)
            return None  # 비활성 → 기존 파싱

        monkeypatch.setattr(sr, "try_structured_call", _capture)
        await sr._llm_classify(_ScriptedLLM(_RAW), "q", _domains())
        assert seen == [expected]


# ──────────────────────────────────────────────
# O4 · O5 · O6 — 3단 라우터 노드
# ──────────────────────────────────────────────


def _state(query: str = "질의"):
    st = create_initial_state(user_query=query)
    st["parsed_requirements"] = {"query_targets": ["서버"], "original_query": query}
    return st


def _db(
    db_id: str, caps: list[str] | None = None, *, score: float = 0.9, user_specified: bool = False
) -> dict:
    entry = {
        "db_id": db_id,
        "relevance_score": score,
        "sub_query_context": f"{db_id} 조회",
        "user_specified": user_specified,
        "reason": "r",
    }
    if caps is not None:
        entry["capabilities"] = caps
    return entry


async def _route(monkeypatch, cfg: AppConfig, classified, *, query: str = "질의") -> dict:
    _use(monkeypatch, cfg)

    async def _fake_classify(*_a, **_kw):
        if isinstance(classified, Exception):
            raise classified
        return classified

    monkeypatch.setattr(sr, "_llm_classify", _fake_classify)
    return await sr.semantic_router(_state(query), llm=object(), app_config=cfg)


class TestRouterNodeOwnership:
    async def test_asset_db_chosen_for_usage_is_corrected_to_active_polestar(self, monkeypatch):
        """★ ITAM이 사용률 영역을 받으면 폴스타 활성 DB 전체로 교정한다(존 한정은 이후 게이트)."""
        out = await _route(
            monkeypatch,
            _cfg(ownership=True),
            {
                "intent": "data_query",
                "databases": [_db("itam", ["server_usage"])],
                "dropped": [],
                "chain": [],
            },
        )
        assert [t["db_id"] for t in out["target_databases"]] == _POLESTAR_ZONES
        assert all(t["capabilities"] == ["server_usage"] for t in out["target_databases"])
        notes = [n for n in out["dependency_notes"] if n["kind"] == NOTE_OWNERSHIP]
        assert len(notes) == 1 and notes[0]["reason"] == own.REASON_OWNER_CORRECTED
        assert "server_usage" in notes[0]["detail"] and "ITAM DB" in notes[0]["detail"]
        assert out["required_capabilities"] == ["server_usage"]
        assert out["capability_chain"] == []

    async def test_already_chosen_polestar_zone_is_kept_x_t13(self, monkeypatch):
        """★ X-T13 — 이미 고른 폴스타 DB(gp)만 남긴다. 다른 존으로 넓히지 않는다."""
        out = await _route(
            monkeypatch,
            _cfg(ownership=True),
            {
                "intent": "data_query",
                "databases": [
                    _db("polestar_cm_gp", ["server_status"]),
                    _db("itam", ["server_usage"], score=0.8),
                ],
                "dropped": [],
                "chain": [],
            },
        )
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_cm_gp"]
        assert out["target_databases"][0]["capabilities"] == ["server_status", "server_usage"]
        assert out["required_capabilities"] == ["server_status", "server_usage"]

    async def test_polestar_chosen_for_contract_is_replaced_by_asset_db(self, monkeypatch):
        """단일 DB 시스템 소유 영역은 그 DB로 교체한다(체인 신호는 그대로 싣는다)."""
        out = await _route(
            monkeypatch,
            _cfg(ownership=True),
            {
                "intent": "data_query",
                "databases": [
                    _db("polestar_b0", ["server_usage"]),
                    _db("polestar_cm_gp", ["asset_contract"], score=0.7),
                ],
                "dropped": [],
                "chain": ["server_usage", "asset_contract"],
            },
        )
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_b0", "itam"]
        assert out["target_databases"][1]["capabilities"] == ["asset_contract"]
        assert out["is_multi_db"] is True
        assert out["capability_chain"] == ["server_usage", "asset_contract"]

    async def test_mixed_capabilities_split_without_dropping_owned_part(self, monkeypatch):
        out = await _route(
            monkeypatch,
            _cfg(ownership=True),
            {
                "intent": "data_query",
                "databases": [_db("polestar_b0", ["server_usage", "asset_lifecycle"])],
                "dropped": [],
                "chain": [],
            },
        )
        by_id = {t["db_id"]: t for t in out["target_databases"]}
        assert by_id["polestar_b0"]["capabilities"] == ["server_usage"]
        assert by_id["itam"]["capabilities"] == ["asset_lifecycle"]

    async def test_owner_inactive_is_reported_not_added(self, monkeypatch):
        cfg = _cfg(ownership=True, active=_POLESTAR_ZONES)
        out = await _route(
            monkeypatch,
            cfg,
            {
                "intent": "data_query",
                "databases": [_db("polestar_b0", ["asset_contract"])],
                "dropped": [],
                "chain": [],
            },
        )
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_b0"]
        notes = out["dependency_notes"]
        assert [n["reason"] for n in notes] == [own.REASON_OWNER_INACTIVE]

    @pytest.mark.parametrize(
        "entry",
        [
            _db("itam", []),  # 답변 영역을 비워 냈다
            _db("cloud_portal", ["server_usage"]),  # 답변 영역 선언이 없는 DB
            # 직접 지정이지만 그 DB가 **정본인** 영역 — 사유 노트도 없다(권고 C 경계).
            _db("itam", ["asset_contract"], user_specified=True),
        ],
    )
    async def test_excluded_entries_are_not_corrected(self, monkeypatch, entry):
        active = [*_ACTIVE, "cloud_portal"]
        out = await _route(
            monkeypatch,
            _cfg(ownership=True, active=active),
            {
                "intent": "data_query",
                "databases": [entry],
                "dropped": [],
                "chain": [],
            },
        )
        assert [t["db_id"] for t in out["target_databases"]] == [entry["db_id"]]
        assert "dependency_notes" not in out

    async def test_off_node_output_has_no_new_keys_and_no_correction(self, monkeypatch):
        """★ off — 같은 분류 결과여도 교정·노트·신규 키가 없다(종전 노드 출력)."""
        out = await _route(
            monkeypatch,
            _cfg(ownership=False),
            {
                "intent": "data_query",
                "databases": [_db("itam", ["server_usage"])],
                "dropped": [],
            },
        )
        assert [t["db_id"] for t in out["target_databases"]] == ["itam"]
        assert not {"required_capabilities", "capability_chain", "dependency_notes"} & set(out)

    async def test_llm_failure_fallback_is_reported_on(self, monkeypatch):
        out = await _route(monkeypatch, _cfg(ownership=True), RuntimeError("secret-url?token=x"))
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_b0"]
        notes = out["dependency_notes"]
        assert [(n["kind"], n["reason"]) for n in notes] == [
            (NOTE_ROUTING_FALLBACK, own.REASON_LLM_ERROR)
        ]
        assert "RuntimeError" in notes[0]["detail"]
        assert "token" not in notes[0]["detail"]  # 예외 원문은 로그에만

    async def test_empty_classification_fallback_is_reported_on(self, monkeypatch):
        out = await _route(
            monkeypatch,
            _cfg(ownership=True),
            {
                "intent": "data_query",
                "databases": [],
                "dropped": [],
                "chain": [],
            },
        )
        assert [(n["kind"], n["reason"]) for n in out["dependency_notes"]] == [
            (NOTE_ROUTING_FALLBACK, own.REASON_NO_CLASSIFICATION)
        ]

    @pytest.mark.parametrize(
        "classified", [RuntimeError("x"), {"intent": "data_query", "databases": []}]
    )
    async def test_fallbacks_are_silent_off(self, monkeypatch, classified):
        out = await _route(monkeypatch, _cfg(ownership=False), classified)
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_b0"]
        assert "dependency_notes" not in out

    async def test_existing_request_notes_are_preserved(self, monkeypatch):
        cfg = _use(monkeypatch, _cfg(ownership=True))

        async def _fake_classify(*_a, **_kw):
            return {
                "intent": "data_query",
                "databases": [_db("itam", ["alarm"])],
                "dropped": [],
                "chain": [],
            }

        monkeypatch.setattr(sr, "_llm_classify", _fake_classify)
        st = _state()
        st["dependency_notes"] = [{"kind": "decompose", "task_id": None, "detail": "앞 노트"}]
        out = await sr.semantic_router(st, llm=object(), app_config=cfg)
        assert out["dependency_notes"][0]["detail"] == "앞 노트"
        assert out["dependency_notes"][1]["kind"] == NOTE_OWNERSHIP


# ──────────────────────────────────────────────
# 순수 함수 성질 · O7 D-004
# ──────────────────────────────────────────────


class TestOwnershipPureFunctions:
    def test_enforce_does_not_mutate_input(self):
        targets = [_db("itam", ["server_usage"])]
        snapshot = json.loads(json.dumps(targets))
        own.enforce_target_ownership(targets, active_db_ids=_ACTIVE)
        assert targets == snapshot

    def test_rows_render_only_systems_with_active_dbs(self):
        rows = own.render_ownership_rows(_POLESTAR_ZONES, with_db_ids=True)
        assert "asset_contract" not in rows and "server_usage" in rows
        assert "polestar_cm_gp" in rows

    def test_rows_without_db_ids_for_decomposition(self):
        rows = own.render_ownership_rows(_ACTIVE, with_db_ids=False)
        assert "polestar_b0" not in rows and "itam" not in rows.replace("ITAM DB", "")

    def test_sanitize_capability_list_shapes(self):
        known = own.known_capability_codes()
        assert own.sanitize_capability_list("alarm", known) == (["alarm"], [])
        assert own.sanitize_capability_list(["alarm", "alarm", " server_usage "], known) == (
            ["alarm", "server_usage"],
            [],
        )
        assert own.sanitize_capability_list({"a": 1}, known) == ([], ["{'a': 1}"])
        assert own.sanitize_capability_list([3], known) == ([], ["3"])

    def test_d004_ownership_functions_take_no_query_text(self):
        """★ D-004 코드 리뷰 체크 — 소유 판정 함수의 입력에 질의 원문이 없다."""
        for fn in (
            own.enforce_target_ownership,
            own.resolve_capability_owner,
            own.restrict_targets_to_owner,
            own.sanitize_capability_list,
            own.sanitize_capability_code,
        ):
            params = set(inspect.signature(fn).parameters)
            assert not params & {"query", "user_query", "text", "question"}, fn.__name__

    @pytest.mark.parametrize(
        "targets",
        [[_db("polestar_cm_gp"), _db("itam")], [_db("itam")]],
    )
    def test_d004_task_restriction_ignores_sub_query_text(self, targets):
        """`sub_query`는 넓힐 때 채움값일 뿐 — 제한 판정(남는 db_id)은 질의 문장과 무관하다."""
        owner = own.resolve_capability_owner("alarm", active_db_ids=_ACTIVE)
        a, _ = own.restrict_targets_to_owner(targets, owner, sub_query="자산관리 계약 알람")
        b, _ = own.restrict_targets_to_owner(targets, owner, sub_query="아무 문장")
        assert [t["db_id"] for t in a] == [t["db_id"] for t in b]

    async def test_d004_node_correction_independent_of_query_text(self, monkeypatch):
        """같은 LLM 구조화 출력이면 질의 원문이 달라도 교정 결과가 같다."""
        classified = {
            "intent": "data_query",
            "databases": [_db("itam", ["alarm"])],
            "dropped": [],
            "chain": [],
        }
        a = await _route(monkeypatch, _cfg(ownership=True), classified, query="자산관리 DB 알람")
        b = await _route(monkeypatch, _cfg(ownership=True), classified, query="아무 문장")
        assert [t["db_id"] for t in a["target_databases"]] == [
            t["db_id"] for t in b["target_databases"]
        ]


# ──────────────────────────────────────────────
# O8 — description_locked (X-T4 렌더 쪽)
# ──────────────────────────────────────────────


class TestDescriptionLocked:
    def test_registry_field_defaults_false_and_parses(self):
        assert all(e.description_locked is False for e in get_registry().databases)
        reg = parse_registry(
            {"databases": [{"db_id": "x", "description_locked": True}, {"db_id": "y"}]}
        )
        assert reg.get("x").description_locked is True
        assert reg.get("y").description_locked is False

    def test_locked_db_skips_cached_detail(self, monkeypatch):
        domains = _domains()
        itam = next(d for d in domains if d.db_id == "itam")
        descs = {"itam": "CPU 사용률 포함 상세", "polestar_b0": "폴스타 상세"}
        unlocked = sr._render_db_list(domains, db_descriptions=descs)
        assert "CPU 사용률 포함 상세" in unlocked

        monkeypatch.setattr(sr, "_description_locked", lambda db_id: db_id == itam.db_id)
        locked = sr._render_db_list(domains, db_descriptions=descs)
        assert "CPU 사용률 포함 상세" not in locked
        assert "폴스타 상세" in locked
