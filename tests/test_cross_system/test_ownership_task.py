"""답변 영역 소유 — 소유 검증 지점 ②(분해 task) · classify_dbs 폴백 표기.

plans/102 X-7 · X-T3 · X-T10 · X-T13.

3단 순차 러너는 2단 부품(`_llm_decompose` → `agent_orchestrator` →
`run_data_query_pipeline`)을 함수로 재사용하고, 서브질의마다 `classify_dbs`로 **한 번 더**
분류한다(X-T10). 그래서 소유 검증을 라우터 노드 한 곳에만 두면 여기서 경계가 샌다 —
task의 답변 영역(LLM 구조화 출력)으로 대상을 맞춘다.

단언하는 것:
    T1 off — task에 capability가 있어도 분류 경로·대상이 종전과 같다
    T2 단일 DB 시스템 소유 → `db_ids` 고정(classify_dbs 호출 0)
    T3 다중 존 시스템 소유 → 고정하지 않고 소유 DB 집합으로 제한만(비면 활성 전체 + 노트)
    T4 X-T13 — 원문 위치 힌트로 고정된 존(gp)만 남는다
    T5 이미 DB가 정해진 task·존 선택 재개 턴은 건드리지 않는다 · 비활성 소유자는 사유 노트
    T6 classify_dbs 폴백 표지 → 경과 노트(on) / 표지·노트 없음(off)
    T7 분해 — off 프롬프트·스키마 불변 · on에서 capability가 JSON 파싱 경로에서 보존·정제된다

실 LLM 호출 0(D-127).
"""

from __future__ import annotations

import importlib
import json

import pytest

from src.config import AppConfig, MultiDBConfig, RouterConfig
from src.orchestration.schemas import DecomposedPlan, OwnershipDecomposedPlan
from src.prompts.intent_planner import INTENT_PLANNER_SYSTEM_TEMPLATE
from src.routing import capability_ownership as own
from src.utils.prior_dependency import NOTE_OWNERSHIP, NOTE_ROUTING_FALLBACK

sa = importlib.import_module("src.orchestration.subagents")
ip = importlib.import_module("src.orchestration.intent_planner")

_POLESTAR_ZONES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_ACTIVE = [*_POLESTAR_ZONES, "itam"]


def _cfg(*, ownership: bool, active: list[str] | None = None) -> AppConfig:
    return AppConfig(
        multi_db=MultiDBConfig(
            active_db_ids_csv=",".join(active if active is not None else _ACTIVE)
        ),
        router=RouterConfig(
            capability_ownership_enabled=ownership, two_stage_enabled=False, unknown_enabled=False
        ),
        enable_semantic_routing=True,
    )


def _target(db_id: str, *, score: float = 0.9, user_specified: bool = False) -> dict:
    return {
        "db_id": db_id,
        "relevance_score": score,
        "sub_query_context": f"{db_id} 정제 질의",
        "user_specified": user_specified,
        "reason": "분류",
    }


@pytest.fixture
def pipeline(monkeypatch):
    """분류·실행부를 대역으로 바꾸고 호출 기록을 돌려준다(대상 결정 로직만 실제로 돈다)."""
    rec: dict = {"classify": 0, "classified": [], "executed": []}

    async def _classify(llm, sub_query, app_config):
        rec["classify"] += 1
        return [dict(t) for t in rec["classified"]]

    async def _single(s, llm, app_config):
        rec["executed"].append([t["db_id"] for t in s["target_databases"]])
        return {}

    async def _multi(s, llm=None, app_config=None):
        rec["executed"].append([t["db_id"] for t in s["target_databases"]])
        return {}

    async def _noop(*_a, **_kw):
        return {}

    async def _organize(s, llm=None, app_config=None):
        return {"organized_data": {"rows": []}}

    monkeypatch.setattr(sa, "classify_dbs", _classify)
    monkeypatch.setattr(sa, "_run_single_db_pipeline", _single)
    monkeypatch.setattr(sa, "multi_db_executor", _multi)
    monkeypatch.setattr(sa, "result_merger", _noop)
    monkeypatch.setattr(sa, "result_organizer", _organize)
    monkeypatch.setattr(sa, "emit_step", _noop)
    return rec


def _isolated(
    query: str = "조회", *, hints: list[str] | None = None, composite: bool = False, **extra
) -> dict:
    parsed = {"original_query": query, "query_targets": ["서버"], "filter_conditions": []}
    if hints is not None:
        parsed["target_db_hints"] = hints
    return {"user_query": query, "parsed_requirements": parsed, "is_composite": composite, **extra}


async def _run(cfg: AppConfig, task: dict, isolated: dict | None = None) -> dict:
    return await sa.run_data_query_pipeline(
        task,
        isolated or _isolated(task.get("sub_query", "")),
        llm=object(),
        app_config=cfg,
    )


def _notes(result: dict, kind: str) -> list[dict]:
    return [n for n in result.get("dependency_notes") or [] if n.get("kind") == kind]


# ──────────────────────────────────────────────
# T1 — off 비트 동일
# ──────────────────────────────────────────────


class TestOff:
    async def test_capability_is_ignored_when_off(self, pipeline):
        """★ off — task에 capability가 있어도 classify 결과 그대로.

        자산 영역인데 폴스타로 분류돼도 교정하지 않는다(종전 동작).
        """
        pipeline["classified"] = [_target("polestar_b0")]
        task = {
            "task_id": "t2",
            "agent": "data_query",
            "sub_query": "계약 만료일",
            "capability": "asset_contract",
        }
        res = await _run(_cfg(ownership=False), task)
        assert pipeline["classify"] == 1
        assert res["target_db_ids"] == ["polestar_b0"]
        assert res["db_origin"] == "classified"
        assert "dependency_notes" not in res


# ──────────────────────────────────────────────
# T2 · T3 · T4 · T5 — 소유 적용
# ──────────────────────────────────────────────


class TestTaskOwnership:
    async def test_single_db_system_is_pinned_without_reclassification(self, pipeline):
        pipeline["classified"] = [_target("polestar_b0")]
        task = {
            "task_id": "t2",
            "agent": "data_query",
            "sub_query": "선행 결과 서버들의 계약 만료일",
            "capability": "asset_contract",
        }
        res = await _run(_cfg(ownership=True), task)
        assert pipeline["classify"] == 0, "단일 DB 시스템 소유 task는 재분류하지 않는다"
        assert res["target_db_ids"] == ["itam"]
        assert res["db_origin"] == "planned"
        assert "dependency_notes" not in res

    async def test_multi_zone_system_is_restricted_not_pinned(self, pipeline):
        pipeline["classified"] = [_target("polestar_cm_gp"), _target("itam", score=0.6)]
        task = {
            "task_id": "t2",
            "agent": "alarm_query",
            "sub_query": "선행 결과 서버들의 현재 알람",
            "capability": "alarm",
        }
        res = await _run(_cfg(ownership=True), task)
        assert pipeline["classify"] == 1, "다중 존 시스템은 고정하지 않는다(분류는 그대로 돈다)"
        assert res["target_db_ids"] == ["polestar_cm_gp"]
        notes = _notes(res, NOTE_OWNERSHIP)
        assert len(notes) == 1
        assert notes[0]["task_id"] == "t2"
        assert notes[0]["from_db_ids"] == ["itam"] and notes[0]["to_db_ids"] == ["polestar_cm_gp"]

    async def test_multi_zone_empty_after_restriction_widens_to_active_owner_dbs(self, pipeline):
        pipeline["classified"] = [_target("itam")]
        task = {
            "task_id": "t1",
            "agent": "data_query",
            "sub_query": "사용률 높은 서버",
            "capability": "server_usage",
        }
        res = await _run(_cfg(ownership=True), task)
        assert res["target_db_ids"] == _POLESTAR_ZONES
        assert _notes(res, NOTE_OWNERSHIP)[0]["to_db_ids"] == _POLESTAR_ZONES
        assert pipeline["executed"] == [_POLESTAR_ZONES]

    async def test_x_t13_location_hint_zone_is_kept(self, pipeline):
        """★ X-T13 — 원문 위치 힌트로 고정된 gp만 남는다(전 존 팬아웃 금지)."""
        pipeline["classified"] = [_target("polestar_b0"), _target("itam", score=0.5)]
        task = {
            "task_id": "t1",
            "agent": "data_query",
            "sub_query": "김포 서버 CPU 사용률",
            "capability": "server_usage",
        }
        res = await _run(
            _cfg(ownership=True), task, _isolated("김포 서버 CPU 사용률", hints=["김포"])
        )
        assert res["target_db_ids"] == ["polestar_cm_gp"]
        assert res["db_origin"] == "hint"
        assert res.get("db_hint_pinning", {}).get("pinned") is True
        assert "dependency_notes" not in res  # 제한으로 뺀 DB가 없다

    async def test_task_with_db_ids_is_untouched(self, pipeline):
        task = {
            "task_id": "t1",
            "agent": "data_query",
            "sub_query": "q",
            "capability": "asset_contract",
            "db_ids": ["polestar_b0"],
        }
        res = await _run(_cfg(ownership=True), task)
        assert res["target_db_ids"] == ["polestar_b0"]
        assert "dependency_notes" not in res

    async def test_zone_selection_resume_turn_is_untouched(self, pipeline):
        task = {
            "task_id": "t1",
            "agent": "data_query",
            "sub_query": "q",
            "capability": "asset_contract",
        }
        iso = _isolated("q", selected_db_ids=["polestar_cm_yd"])
        res = await _run(_cfg(ownership=True), task, iso)
        assert res["target_db_ids"] == ["polestar_cm_yd"]
        assert "dependency_notes" not in res

    async def test_inactive_single_db_owner_is_reported_and_classified(self, pipeline):
        pipeline["classified"] = [_target("polestar_b0")]
        task = {
            "task_id": "t2",
            "agent": "data_query",
            "sub_query": "q",
            "capability": "asset_contract",
        }
        res = await _run(_cfg(ownership=True, active=_POLESTAR_ZONES), task)
        assert pipeline["classify"] == 1
        assert res["target_db_ids"] == ["polestar_b0"]
        assert [n["reason"] for n in _notes(res, NOTE_OWNERSHIP)] == [own.REASON_OWNER_INACTIVE]

    async def test_user_specified_entry_survives_restriction(self, pipeline):
        pipeline["classified"] = [_target("itam", user_specified=True), _target("polestar_b0")]
        task = {"task_id": "t1", "agent": "alarm_query", "sub_query": "q", "capability": "alarm"}
        res = await _run(_cfg(ownership=True), task)
        assert res["target_db_ids"] == ["itam", "polestar_b0"]


# ──────────────────────────────────────────────
# T6 — classify_dbs 폴백 표지(X-T3)
# ──────────────────────────────────────────────


class TestClassifyFallbackMarker:
    @pytest.fixture(autouse=True)
    def _no_cache(self, monkeypatch):
        def _boom(*_a, **_kw):
            raise RuntimeError("cache disabled in test")

        monkeypatch.setattr("src.schema_cache.cache_manager.get_cache_manager", _boom)

    @staticmethod
    def _llm_classify(monkeypatch, outcome):
        async def _fake(*_a, **_kw):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(sa, "_llm_classify", _fake)

    async def test_marker_only_when_on(self, monkeypatch):
        self._llm_classify(monkeypatch, {"intent": "data_query", "databases": []})
        off = await sa.classify_dbs(object(), "q", _cfg(ownership=False))
        on = await sa.classify_dbs(object(), "q", _cfg(ownership=True))
        assert "routing_fallback" not in off[0]
        assert on[0]["routing_fallback"]["reason"] == own.REASON_NO_CLASSIFICATION

    async def test_pipeline_turns_marker_into_note_and_strips_it(self, monkeypatch, pipeline):
        self._llm_classify(monkeypatch, RuntimeError("boom"))
        monkeypatch.setattr(sa, "classify_dbs", _REAL_CLASSIFY)  # 실제 classify_dbs(대역 LLM 분류)
        task = {"task_id": "t1", "agent": "data_query", "sub_query": "q"}
        res = await _run(_cfg(ownership=True), task)
        notes = _notes(res, NOTE_ROUTING_FALLBACK)
        assert [(n["reason"], n["task_id"], n["db_id"]) for n in notes] == [
            (own.REASON_LLM_ERROR, "t1", "polestar_b0")
        ]
        assert all("routing_fallback" not in t for t in res["source"])

    async def test_pipeline_off_has_no_fallback_note(self, monkeypatch, pipeline):
        self._llm_classify(monkeypatch, RuntimeError("boom"))
        monkeypatch.setattr(sa, "classify_dbs", _REAL_CLASSIFY)
        res = await _run(
            _cfg(ownership=False), {"task_id": "t1", "agent": "data_query", "sub_query": "q"}
        )
        assert res["target_db_ids"] == ["polestar_b0"]
        assert "dependency_notes" not in res


_REAL_CLASSIFY = sa.classify_dbs


# ──────────────────────────────────────────────
# T7 — 분해(`_llm_decompose`) — 3단 순차 러너가 재사용
# ──────────────────────────────────────────────


class _ScriptedLLM:
    def __init__(self, content: str):
        self._content = content
        self.system_prompts: list[str] = []

    async def ainvoke(self, messages):
        self.system_prompts.append(messages[0].content)
        return type("R", (), {"content": self._content})()


_PLAN = json.dumps(
    {
        "clarification_needed": None,
        "tasks": [
            {
                "task_id": "t1",
                "agent": "data_query",
                "sub_query": "a",
                "depends_on": [],
                "input_from": [],
                "order": 1,
                "capability": "server_usage",
            },
            {
                "task_id": "t2",
                "agent": "data_query",
                "sub_query": "b",
                "depends_on": ["t1"],
                "input_from": ["t1"],
                "order": 2,
                "capability": "not_a_code",
            },
        ],
    },
    ensure_ascii=False,
)


class TestDecomposition:
    @pytest.fixture(autouse=True)
    def _json_path(self, monkeypatch):
        async def _none(*_a, **_kw):
            return None

        monkeypatch.setattr(ip, "try_structured_call", _none)

    async def test_off_prompt_is_base_template_and_capability_not_kept(self):
        llm = _ScriptedLLM(_PLAN)
        plan = await ip._llm_decompose(llm, "질의", _cfg(ownership=False))
        assert llm.system_prompts[0] == INTENT_PLANNER_SYSTEM_TEMPLATE
        assert all("capability" not in t for t in plan["tasks"])

    async def test_on_prompt_inserts_ownership_and_keeps_sanitized_capability(self):
        llm = _ScriptedLLM(_PLAN)
        plan = await ip._llm_decompose(llm, "질의", _cfg(ownership=True))
        prompt = llm.system_prompts[0]
        assert "## 답변 영역(capability)" in prompt and "### 예시 3-2" in prompt
        assert "| asset_contract |" in prompt and "| alarm |" in prompt
        assert (
            "polestar_cm_gp"
            not in prompt.split("## 출력 형식")[0].split("## 답변 영역(capability)")[1]
        ), "분해 소유표에는 db_id를 싣지 않는다(DB는 고르지 않는다)"
        assert [t["capability"] for t in plan["tasks"]] == ["server_usage", ""]

    @pytest.mark.parametrize(
        "ownership,expected", [(False, DecomposedPlan), (True, OwnershipDecomposedPlan)]
    )
    async def test_structured_model_matches_flag(self, monkeypatch, ownership, expected):
        seen: list[type] = []

        async def _capture(llm, messages, response_model, **_kw):
            seen.append(response_model)
            return None

        monkeypatch.setattr(ip, "try_structured_call", _capture)
        await ip._llm_decompose(_ScriptedLLM(_PLAN), "질의", _cfg(ownership=ownership))
        assert seen[0] is expected

    def test_off_schema_unchanged(self):
        schema = json.dumps(DecomposedPlan.model_json_schema(), ensure_ascii=False)
        assert "capability" not in schema
        assert "capability" in json.dumps(
            OwnershipDecomposedPlan.model_json_schema(), ensure_ascii=False
        )
