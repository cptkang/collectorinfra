"""plans/186 Wave 1-B — 행동 변경 3건(K5 · J4 · P3).

  K5  재계획 뒤 필터가 끝내도 계획 검증(plan_validation) 노트가 반환에 남는다
      (`_ReplanCtx.stop` · 호출부가 넘긴 `dependency_notes`는 그대로).
  J4  3단 라우터 소유 판정이 전역 `load_config()`가 아니라 넘긴 설정을 따른다
      (`capability_ownership_on(app_config)` · 미지정이면 전역 설정).
  P3  수동 프로필 YAML 파싱을 경로·mtime·크기 기준으로 캐시한다(반환은 깊은 복사).

LLM·DB 0 — LLM 은 모의 객체·대본 객체다.
"""

from __future__ import annotations

import importlib
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from src.config import AppConfig, MultiDBConfig, RouterConfig
from src.routing import capability_ownership as own
from src.routing.domain_config import DB_DOMAINS
from src.state import create_initial_state
from src.utils.prior_dependency import NOTE_OWNERSHIP

_rp = importlib.import_module("src.orchestration.replanner")
# 패키지 `__init__`가 동명 함수를 re-export해 모듈을 가린다 — importlib만 모듈을 준다.
sr = importlib.import_module("src.routing.semantic_router")
sa = importlib.import_module("src.nodes.schema_analyzer")


# ── K5 ──────────────────────────────────────────────────────────────────────


def _llm(payload: dict[str, Any]) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=json.dumps(payload, ensure_ascii=False))
    return llm


def _rstate() -> dict[str, Any]:
    state = create_initial_state(user_query="김포 서버 CPU 상위 10건")
    state["task_plan"] = [{
        "task_id": "t1", "agent": "data_query", "sub_query": "김포 서버 CPU 상위 10건",
        "depends_on": [], "input_from": [], "order": 1, "status": "completed",
    }]
    state["task_results"] = {"t1": {"query_results": [], "organized_data": {"rows": []},
                                    "target_db_ids": ["polestar_cm_gp"]}}
    return state


def _rcfg(mock_config: AppConfig) -> AppConfig:
    mock_config.server.answer_reserve_sec = 0
    mock_config.replan_budget_prompt_enabled = False
    mock_config.composite.plan_dag_validation_enabled = True
    return mock_config


def _ctx(mock_config: AppConfig, plan_notes: list[dict[str, Any]]) -> Any:
    ctx = _rp._ReplanCtx(
        state=_rstate(), app_config=mock_config, replan_count=0, replan_history=[],
        tasks_now=[],
    )
    ctx.plan_notes = plan_notes
    ctx.replan_notes = [{"kind": "prior"}, *plan_notes]
    return ctx


class TestK5PlanNotesSurviveFilterStop:
    async def test_later_filter_stop_keeps_plan_validation_note(self, mock_config):
        """★ 계획 검증이 한 task를 빼고 남은 일반 안내 후속을 뒤 필터가 끊어도 노트가 남는다."""
        llm = _llm({"needs_followup": True, "reason": "보강", "new_tasks": [
            {"agent": "data_query", "sub_query": "메모리 조회", "depends_on": ["t99"],
             "input_from": ["t99"], "supersedes": []},
            {"agent": "general_inference", "sub_query": "사용법 안내", "depends_on": [],
             "input_from": [], "supersedes": []},
        ]})
        out = await _rp.replanner(_rstate(), llm=llm, app_config=_rcfg(mock_config))
        assert out["needs_replan"] is False
        assert "task_plan" not in out  # 증분 추가가 아니라 종료 반환이다
        notes = [n for n in out["dependency_notes"] if n.get("reason") == "replan_task_invalid"]
        assert len(notes) == 1

    def test_stop_without_explicit_notes_carries_replan_notes(self, mock_config):
        note = {"kind": "decompose", "reason": "replan_task_invalid"}
        ctx = _ctx(mock_config, [note])
        out = ctx.stop(replan_stop_notice="x")
        assert out["dependency_notes"] == [{"kind": "prior"}, note]
        assert out["replan_stop_notice"] == "x" and out["needs_replan"] is False

    def test_explicit_dependency_notes_are_respected(self, mock_config):
        ctx = _ctx(mock_config, [{"kind": "decompose"}])
        given = [{"kind": "given"}]
        assert ctx.stop(dependency_notes=given)["dependency_notes"] is given

    def test_no_plan_notes_keeps_prior_return(self, mock_config):
        """계획 검증 앞 단계(plan_notes 빈 목록)는 반환이 종전과 같다 — 키 없음."""
        ctx = _ctx(mock_config, [])
        assert ctx.stop() == {
            "needs_replan": False, "replan_history": [], "current_node": "replanner",
        }


# ── J4 ──────────────────────────────────────────────────────────────────────

_POLESTAR_ZONES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_ACTIVE = [*_POLESTAR_ZONES, "itam"]
_HEADING = "## 답변 영역 소유표"

_RAW = json.dumps({
    "intent": "data_query",
    "chain": ["server_usage"],
    "databases": [{"db_id": "polestar_b0", "relevance_score": 0.9, "reason": "r",
                   "sub_query_context": "q", "user_specified": False,
                   "capabilities": ["server_usage"]}],
}, ensure_ascii=False)


def _cfg(*, ownership: bool, two_stage: bool = False) -> AppConfig:
    """검증 대상 필드를 명시한 설정(.env 누수 차단)."""
    return AppConfig(
        multi_db=MultiDBConfig(active_db_ids_csv=",".join(_ACTIVE)),
        router=RouterConfig(
            capability_ownership_enabled=ownership, two_stage_enabled=two_stage,
            unknown_enabled=False, early_stop_enabled=False,
        ),
        enable_semantic_routing=True,
    )


def _domains() -> list:
    return [d for d in DB_DOMAINS if d.db_id in _ACTIVE]


class _ScriptedLLM:
    """호출 순서대로 정해진 응답 원문을 돌려준다. 받은 시스템 프롬프트를 기록한다."""

    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.prompts: list[str] = []

    async def ainvoke(self, messages):
        self.prompts.append(messages[0].content)
        return type("R", (), {"content": self._responses.pop(0)})()


@pytest.fixture
def global_cfg(monkeypatch):
    """전역 `load_config()`를 고정한다 — 넘긴 설정과 반대 값으로 둬서 어느 쪽을 읽는지 가른다."""

    def _set(cfg: AppConfig) -> AppConfig:
        monkeypatch.setattr(sr, "load_config", lambda: cfg)
        return cfg

    def _boom(*_a, **_kw):
        raise RuntimeError("cache disabled in test")

    monkeypatch.setattr("src.schema_cache.cache_manager.get_cache_manager", _boom)
    return _set


class TestJ4OwnershipFollowsPassedConfig:
    @pytest.mark.parametrize("passed", [True, False])
    async def test_classify_follows_passed_config(self, global_cfg, passed):
        global_cfg(_cfg(ownership=not passed))
        llm = _ScriptedLLM(_RAW)
        got = await sr._llm_classify(llm, "q", _domains(), app_config=_cfg(ownership=passed))
        assert ("chain" in got) is passed
        assert (_HEADING in llm.prompts[0]) is passed

    async def test_classify_without_config_reads_global(self, global_cfg):
        """미지정 호출(예: `classify_dbs`)은 종전처럼 전역 설정을 읽는다."""
        global_cfg(_cfg(ownership=True))
        got = await sr._llm_classify(_ScriptedLLM(_RAW), "q", _domains())
        assert got["chain"] == ["server_usage"]

    @pytest.mark.parametrize("passed", [True, False])
    async def test_two_stage_follows_passed_config(self, global_cfg, passed):
        global_cfg(_cfg(ownership=not passed, two_stage=True))
        llm = _ScriptedLLM('data_query\n{"confidence": 0.9}', _RAW)
        got = await sr._llm_classify(
            llm, "q", _domains(), app_config=_cfg(ownership=passed, two_stage=True),
        )
        assert ("chain" in got) is passed
        assert (_HEADING in llm.prompts[1]) is passed

    @pytest.mark.parametrize("passed", [True, False])
    def test_prompt_slots_follow_passed_config(self, global_cfg, passed):
        global_cfg(_cfg(ownership=not passed))
        prompt = sr._build_router_prompt(_domains(), app_config=_cfg(ownership=passed))
        assert (_HEADING in prompt) is passed

    async def test_router_node_corrects_with_passed_config(self, global_cfg, monkeypatch):
        """★ 노드 — 전역은 off·넘긴 설정은 on이면 소유 교정이 일어난다."""
        global_cfg(_cfg(ownership=False))
        on = _cfg(ownership=True)

        async def _fake_classify(*_a, **_kw):
            return {"intent": "data_query", "dropped": [], "chain": [], "databases": [{
                "db_id": "itam", "relevance_score": 0.9, "sub_query_context": "q",
                "user_specified": False, "reason": "r", "capabilities": ["server_usage"],
            }]}

        monkeypatch.setattr(sr, "_llm_classify", _fake_classify)
        st = create_initial_state(user_query="질의")
        st["parsed_requirements"] = {"query_targets": ["서버"], "original_query": "질의"}
        out = await sr.semantic_router(st, llm=object(), app_config=on)
        assert [t["db_id"] for t in out["target_databases"]] == _POLESTAR_ZONES
        notes = [n for n in out["dependency_notes"] if n["kind"] == NOTE_OWNERSHIP]
        assert notes and notes[0]["reason"] == own.REASON_OWNER_CORRECTED

    def test_global_ownership_helper_removed(self):
        assert not hasattr(sr, "_ownership_enabled")


# ── P3 ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    """상대 경로 `config/db_profiles`를 임시 디렉터리로 돌리고 캐시를 앞뒤로 비운다."""
    d = tmp_path / "config" / "db_profiles"
    d.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    sa._clear_manual_profile_cache()
    yield d
    sa._clear_manual_profile_cache()


def _write(path, doc: dict[str, Any]) -> None:
    path.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")


_DOC = {"source": "manual", "patterns": [
    {"known_attributes": [{"name": "OSType", "description": "운영체제"}]},
]}


@pytest.fixture
def safe_load_spy(monkeypatch):
    calls: list[int] = []
    real = yaml.safe_load

    def _spy(stream):
        calls.append(1)
        return real(stream)

    monkeypatch.setattr(yaml, "safe_load", _spy)
    return calls


class TestP3ManualProfileCache:
    def test_same_file_parsed_once(self, profile_dir, safe_load_spy):
        _write(profile_dir / "p3db.yaml", _DOC)
        a = sa._load_manual_profile("p3db")
        b = sa._load_manual_profile("p3db")
        assert a == b and a is not b
        assert a["patterns"][0]["known_attributes"] == ["OSType"]
        assert len(safe_load_spy) == 1

    def test_caller_mutation_does_not_leak(self, profile_dir):
        _write(profile_dir / "p3db.yaml", _DOC)
        a = sa._load_manual_profile("p3db")
        a["patterns"][0]["known_attributes"].append("오염")
        a["source"] = "변형"
        b = sa._load_manual_profile("p3db")
        assert b["source"] == "manual"
        assert b["patterns"][0]["known_attributes"] == ["OSType"]
        assert b["patterns"][0]["known_attributes_detail"] == [
            {"name": "OSType", "description": "운영체제"}
        ]

    def test_file_change_is_reread(self, profile_dir, safe_load_spy):
        path = profile_dir / "p3db.yaml"
        _write(path, _DOC)
        assert sa._load_manual_profile("p3db")["patterns"][0]["known_attributes"] == ["OSType"]
        st = os.stat(path)
        _write(path, {"source": "manual", "patterns": [{"known_attributes": ["CPU", "MEM"]}]})
        # 크기·mtime 둘 다 바뀌도록 mtime을 확실히 민다(파일시스템 해상도와 무관하게)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        assert sa._load_manual_profile("p3db")["patterns"][0]["known_attributes"] == ["CPU", "MEM"]
        assert len(safe_load_spy) == 2

    def test_source_change_to_auto_returns_none(self, profile_dir):
        path = profile_dir / "p3db.yaml"
        _write(path, _DOC)
        assert sa._load_manual_profile("p3db") is not None
        st = os.stat(path)
        _write(path, {"source": "auto"})
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        assert sa._load_manual_profile("p3db") is None

    def test_parse_failure_is_not_cached(self, profile_dir):
        """읽기 실패는 None(종전) — 고친 파일은 다음 호출이 바로 읽는다."""
        path = profile_dir / "p3db.yaml"
        path.write_text("source: [manual\n", encoding="utf-8")  # 깨진 YAML
        assert sa._load_manual_profile("p3db") is None
        st = os.stat(path)
        _write(path, _DOC)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        assert sa._load_manual_profile("p3db") is not None

    def test_missing_file_returns_none(self, profile_dir):
        assert sa._load_manual_profile("p3_missing") is None

    def test_json_fallback_is_cached(self, profile_dir, monkeypatch):
        (profile_dir / "p3json.json").write_text(json.dumps(_DOC), encoding="utf-8")
        calls: list[int] = []
        real = json.load

        def _spy(fp):
            calls.append(1)
            return real(fp)

        monkeypatch.setattr(json, "load", _spy)
        a = sa._load_manual_profile("p3json")
        b = sa._load_manual_profile("p3json")
        assert a == b and a["patterns"][0]["known_attributes"] == ["OSType"]
        assert len(calls) == 1

    def test_cache_key_is_absolute_path(self, tmp_path, monkeypatch):
        """같은 상대 경로라도 cwd가 다르면 다른 파일이다 — 캐시가 섞이지 않는다."""
        sa._clear_manual_profile_cache()
        try:
            for name, attr in (("a", "A1"), ("b", "B1")):
                d = tmp_path / name / "config" / "db_profiles"
                d.mkdir(parents=True)
                _write(d / "p3db.yaml",
                       {"source": "manual", "patterns": [{"known_attributes": [attr]}]})
            monkeypatch.chdir(tmp_path / "a")
            assert sa._load_manual_profile("p3db")["patterns"][0]["known_attributes"] == ["A1"]
            monkeypatch.chdir(tmp_path / "b")
            assert sa._load_manual_profile("p3db")["patterns"][0]["known_attributes"] == ["B1"]
        finally:
            sa._clear_manual_profile_cache()
