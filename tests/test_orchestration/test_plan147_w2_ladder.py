"""plans/147 W2 · D-322 — `run_apm_query` 소스 선택 사다리 배선(처리기 단위 · 모의 게이트웨이 세션).

고정하는 계약(가짜 LLM 0 · 실 게이트웨이·실 제니퍼 0 — 실프로세스 종단은 `test_plan147_w2_e2e.py`):
  1. §3 표 — 한 소스 단어(「김포」·「레거시」) = 그 소스만 + 좁힘 고지(의무·중립) · 여러 소스 단어
     (「은행존」)·지목 없음 = **조회 0**(세션을 열지 않는다) + 되묻기 페이로드 · 화면 선택 ·
     되묻기 답 ·
     「전체」 · 승계 · 승계 중 교체 · 지시어 후속(직전 행의 소스) · 미연결 = 안내만(넓히지 않음).
  2. 분해 `sources`보다 단어가 이긴다 · LLM만 냈으면 그 값 · 전부 무효 id는 사다리가 덮는다.
  3. 소스 <2 배포 = 종전 경로 그대로(되묻기 0 · 소스 칸·소스별 행 없음).
  4. 답 표시 — 행 `source_id` 원값 유지 + 바로 뒤 `source_label`(레지스트리 라벨) ·
     `source_status`에 소스별 행(조회 n건 · 실패 사유 코드 · 선택 밖) — 게이트웨이 사유
     문구(설정 소스 목록)는 옮기지 않는다. 첫 홉에서 빠진 소스도 실패(사유 코드) + 의무 고지.
  5. 오탐 반례 — 「운영체제」·「운영 중」·「운영자」·「DRM」은 소스 단어가 아니다(D-271 제외어 ·
     라틴 경계) · 「개발자」는 D-271 주의 ①대로 공동존으로 좁혀지고 고지로 드러난다(침묵 아님).
  6. AC-4 — 레지스트리 4번째 소스 한 행(단어 포함)만으로 단어 지목·되묻기 선택지·승계가 성립한다.
  7. 되묻기 감사 — 기존 어휘 `clarification_issued`(kind `apm_source`).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.domain import disclosure as disc
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from src.routing import apm_source_select as sel
from src.routing.registry import SourceSpec, get_registry
from tests.test_orchestration import apm_batch_mock

pytestmark = pytest.mark.apm_source_ladder

NOW = datetime(2026, 10, 8, 10, 0, 0)
ALL3 = ("bank", "common", "legacy")


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
    )


class _Gateway:
    """모의 게이트웨이 — `source_ids`(없으면 설정 전 소스)마다 행 1개 · 소스 상태 `sources[]` ·
    다건 대상(`targets`)은 `apm_batch_mock`이 항목별 단건 응답으로 합친다."""

    def __init__(self, configured: tuple[str, ...], failing: tuple[str, ...] = ()) -> None:
        self.configured = configured
        self.failing = failing
        self.calls: list[tuple[str, dict]] = []
        self.opened = 0

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        env = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(env))], isError=False)

    def _single(self, name: str, arguments: dict) -> dict:
        picked = arguments.get("source_ids") or list(self.configured)
        unknown = [s for s in picked if s not in self.configured]
        if unknown:
            env: dict[str, Any] = {
                "error": "invalid_argument", "tool": name,
                "reason": f"모르는 source_ids {unknown} — 설정된 소스: {list(self.configured)}"}
        else:
            ok = [s for s in picked if s not in self.failing]
            env = {"rows": [{"source_id": s, "instance_id": 10 + i, "instance_name": f"{s}-was",
                             "tps": 1.5} for i, s in enumerate(ok)],
                   "row_count": len(ok), "queried_at": "2026-10-08T10:00:00+09:00",
                   "source_kind": "apm_api", "source": "apm", "tool": name, "limits": [],
                   "sources": [
                       {"source_id": s, "status": "ok", "reason": ""} if s in ok else
                       {"source_id": s, "status": "unavailable",
                        "reason": f"source_unavailable: 설정된 소스: {list(self.configured)}"}
                       for s in picked]}
            if len(ok) < len(picked):
                env["partial"] = True
        return env

    def tools(self) -> list[str]:
        return [n for n, _ in self.calls]


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))

    def install(configured: tuple[str, ...] = ALL3, failing: tuple[str, ...] = ()) -> _Gateway:
        gw = _Gateway(configured, failing)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


@pytest.fixture
def audits(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    async def record(**kw):
        seen.append(kw)

    import src.security.audit_logger as audit

    monkeypatch.setattr(audit, "log_clarification", record)
    return seen


def _isolated(query: str, *, host: str | None = "web01", **extra: Any) -> dict:
    conditions = [{"field": "hostname", "op": "=", "value": host}] if host else []
    return {"parsed_requirements": {"filter_conditions": conditions, "time_range": None},
            "conversation_context": {}, "thread_id": "th-147", "user_id": "alice",
            "user_query": query, "original_user_query": query, **extra}


async def _run(query: str, isolated: dict | None = None, *, sub_query: str | None = None,
               **task: Any) -> dict:
    return await aq.run_apm_query(
        {"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health"],
         "sub_query": sub_query or query, **task},
        isolated or _isolated(query), llm=None, app_config=_cfg(), now=NOW)


def _texts(res: dict, kind: str) -> list[str]:
    return [d["text"] for d in res.get("disclosures") or [] if d["kind"] == kind]


def _sent(gw: _Gateway) -> list[Any]:
    return [args.get("source_ids") for _, args in gw.calls]


def _row_sources(res: dict) -> list[Any]:
    return [r.get("source_id") for r in res.get("query_results") or []]


def _asked(res: dict) -> dict:
    assert res["degraded_reason"] == "apm_unresolved_condition", res
    assert res["error"], "되묻기 task는 error를 남겨 재계획에서 빠진다"
    return res[aq.SOURCE_CLARIFICATION_KEY]


# ── 1. §3 표 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gimpo_queries_common_only_with_narrowing_notice(gateway) -> None:
    gw = gateway()
    res = await _run("김포 WAS 응답시간")
    assert _sent(gw) == [["common"]], "모든 게이트웨이 호출에 공동존만"
    assert _row_sources(res) == ["common"]
    (notice,) = _texts(res, disc.APM_UNTARGETED_SCOPE)
    assert notice == "공동존 제니퍼만 조회했습니다(근거: 「김포」)"
    sel_meta = res["apm_query"]["source_selection"]
    assert sel_meta["basis"] == "named" and sel_meta["evidence"] == ["김포"]
    assert sel_meta["scope"] == {"ids": ["common"], "basis": "named"}
    assert sel_meta["last_target_sources"] == ["common"]
    assert aq.SOURCE_CLARIFICATION_KEY not in res


@pytest.mark.asyncio
async def test_legacy_term_queries_legacy_only(gateway) -> None:
    gw = gateway()
    res = await _run("레거시 제니퍼 WAS 응답시간")
    assert _sent(gw) == [["legacy"]] and _row_sources(res) == ["legacy"]
    assert _texts(res, disc.APM_UNTARGETED_SCOPE) == [
        "레거시 제니퍼만 조회했습니다(근거: 「레거시 제니퍼」)"]


@pytest.mark.asyncio
async def test_shared_term_asks_without_querying(gateway, audits) -> None:
    gw = gateway()
    res = await _run("은행존 WAS 응답시간")
    assert gw.opened == 0 and gw.calls == [], "되묻기는 조회 0(세션도 열지 않는다)"
    ask = _asked(res)
    assert ask == {
        "question": "은행존 제니퍼 · 레거시 제니퍼 중 어느 것을 조회할까요?",
        "options": [{"key": "bank", "label": "은행존 제니퍼", "zone": "bankjon"},
                    {"key": "legacy", "label": "레거시 제니퍼", "zone": "bankjon"}],
        "allow_all": False, "multi": True, "original_query": "은행존 WAS 응답시간"}
    assert res["final_response"] == ask["question"], "답 본문에도 되묻기 문구가 남는다"
    assert res["source_status"] == [res["apm_query"]["source_status"]], "소스별 행 없음"
    assert res["source_status"][0]["status"] == "not_queried"
    assert res["apm_query"]["source_selection"]["scope"] is None, "되묻기는 승계를 바꾸지 않는다"
    assert audits == [{"kind": "apm_source", "axis": "apm_source", "option_count": 2,
                       "user_id": "alice", "thread_id": "th-147"}]


@pytest.mark.asyncio
async def test_no_designation_asks_every_source_plus_all(gateway, audits) -> None:
    gw = gateway()
    q = "WAS 응답시간 가장 느린 5개"
    res = await _run(q, _isolated(q, host=None))
    assert gw.opened == 0
    ask = _asked(res)
    assert [o["key"] for o in ask["options"]] == [*ALL3, sel.ALL_SOURCES]
    assert ask["options"][-1] == {"key": "*", "label": "전체", "zone": ""}
    assert ask["allow_all"] is True
    assert ask["question"] == ("은행존 제니퍼 · 공동존 제니퍼 · 레거시 제니퍼 · 전체 중 어느 것을"
                               " 조회할까요?")
    assert audits[0]["option_count"] == 4


@pytest.mark.asyncio
async def test_screen_selection_queries_without_asking(gateway) -> None:
    gw = gateway()
    q = "WAS 응답시간"
    res = await _run(q, _isolated(q, selected_apm_source_ids=["common"],
                                  apm_source_basis="selected"))
    assert _sent(gw) == [["common"]]
    assert _texts(res, disc.APM_UNTARGETED_SCOPE) == ["공동존 제니퍼만 조회했습니다(화면 선택)"]
    assert res["apm_query"]["source_selection"]["scope"] == {"ids": ["common"],
                                                            "basis": "selected"}


@pytest.mark.asyncio
async def test_button_answer_multi_pick_and_all(gateway) -> None:
    gw = gateway()
    q = "은행존 WAS 응답시간"
    res = await _run(q, _isolated(q, selected_apm_source_ids=["bank", "legacy"],
                                  apm_source_basis="answered"))
    assert _sent(gw) == [["bank", "legacy"]], "되묻기 답은 여러 개여도 되묻지 않는다(G-A)"
    assert _texts(res, disc.APM_UNTARGETED_SCOPE) == [
        "은행존 제니퍼 · 레거시 제니퍼만 조회했습니다(되묻기 답)"]
    gw = gateway()
    res = await _run("WAS 응답시간", _isolated("WAS 응답시간", selected_apm_source_ids=["*"],
                                             apm_source_basis="answered"))
    assert _sent(gw) == [list(ALL3)], "「전체」 = 사용 가능 소스 전부"
    assert not _texts(res, disc.APM_UNTARGETED_SCOPE), "전 소스면 좁힘 고지 없음"
    assert res["apm_query"]["source_selection"]["scope"] == {"ids": ["*"], "basis": "all"}


@pytest.mark.asyncio
async def test_inherited_choice_and_named_replacement(gateway) -> None:
    gw = gateway()
    scope = {"ids": ["common"], "basis": "answered", "last_target_sources": ["common"]}
    res = await _run("힙 사용률은?", _isolated("힙 사용률은?", apm_source_scope=scope))
    assert _sent(gw) == [["common"]]
    assert _texts(res, disc.APM_UNTARGETED_SCOPE) == [
        "공동존 제니퍼만 조회했습니다(이전 선택 승계)"]
    assert res["apm_query"]["source_selection"]["scope"] == {"ids": ["common"],
                                                            "basis": "answered"}
    gw = gateway()
    res = await _run("레거시는?", _isolated("레거시는?", apm_source_scope=scope))
    assert _sent(gw) == [["legacy"]], "승계 중 새 단어가 이긴다"
    assert res["apm_query"]["source_selection"]["scope"] == {"ids": ["legacy"], "basis": "named"}


@pytest.mark.asyncio
async def test_demonstrative_followup_uses_previous_row_sources(gateway) -> None:
    gw = gateway()
    scope = {"ids": ["*"], "basis": "all", "last_target_sources": ["legacy"]}
    q = "그 서버 응답시간 다시"
    res = await _run(q, _isolated(q, apm_source_scope=scope))
    assert _sent(gw) == [["legacy"]], "지시어 후속은 직전 대상 행의 소스"
    gw = gateway()
    res = await _run("응답시간 다시", _isolated("응답시간 다시", apm_source_scope=scope))
    assert _sent(gw) == [list(ALL3)], "지시어가 아니면 승계 값(전체)"
    assert not _texts(res, disc.APM_UNTARGETED_SCOPE)


@pytest.mark.asyncio
async def test_unavailable_source_only_named_is_notice_without_widening(gateway, audits) -> None:
    sel.set_available_apm_sources({"bank", "common"})
    gw = gateway()
    res = await _run("레거시 WAS 응답시간")
    assert gw.opened == 0, "미연결 소스만 걸리면 조회하지 않는다(다른 소스로 넓히지 않음)"
    assert res["final_response"] == "레거시 제니퍼는 연결되지 않아 조회하지 않았습니다."
    assert res["degraded_reason"] == "source_unavailable" and res["error"]
    assert aq.SOURCE_CLARIFICATION_KEY not in res and audits == []
    # 지목 없음 — 선택지에서 미연결 소스가 빠진다
    res = await _run("WAS 응답시간", _isolated("WAS 응답시간"))
    assert [o["key"] for o in _asked(res)["options"]] == ["bank", "common", "*"]
    # 「은행존」 — 미연결 legacy가 빠져 은행존 하나 = 조회 + 미연결 안내
    gw = gateway()
    res = await _run("은행존 WAS 응답시간")
    assert _sent(gw) == [["bank"]]
    assert _texts(res, disc.APM_UNRESOLVED_CONDITION) == [
        "레거시 제니퍼는 연결되지 않아 조회하지 않았습니다"]
    assert [r["source_id"] for r in res["source_status"][1:]] == ["bank", "common"], \
        "미연결 소스는 소스별 행도 없다"


# ── 2. 분해 sources와의 관계 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_term_beats_llm_sources_and_llm_used_alone(gateway) -> None:
    gw = gateway()
    await _run("김포 WAS 응답시간", sources=["bank"])
    assert _sent(gw) == [["common"]], "단어(결정적)가 분해 LLM sources를 이긴다"
    gw = gateway()
    res = await _run("WAS 응답시간", sources=["legacy"])
    assert _sent(gw) == [["legacy"]]
    assert _texts(res, disc.APM_UNTARGETED_SCOPE) == [
        "레거시 제니퍼만 조회했습니다(근거: 질문 해석)"]


@pytest.mark.asyncio
async def test_all_invalid_llm_ids_are_covered_by_the_ladder(gateway) -> None:
    gw = gateway()
    res = await _run("WAS 응답시간", sources=["zzz"])
    assert gw.opened == 0
    ask = _asked(res)
    assert [o["key"] for o in ask["options"]] == [*ALL3, "*"]
    assert "zzz" not in res["final_response"], "종전 「지목한 소스 못 찾음」 되묻기와 겹치지 않는다"
    assert res["final_response"] == ask["question"]
    gw = gateway()
    res = await _run("김포 WAS 응답시간", sources=["bank", "zzz"])
    assert _sent(gw) == [["common"]]
    assert not [t for t in _texts(res, disc.APM_UNRESOLVED_CONDITION) if "zzz" in t]


# ── 3. 소스 <2 배포 — 종전 경로 그대로 ────────────────────────────────────────

class _OneSource:
    def __init__(self) -> None:
        self._base = get_registry()

    def sources_of(self, system: str) -> tuple[SourceSpec, ...]:
        found = self._base.sources_of(system)
        return found[:1] if system == aq.APM_SYSTEM else found

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


@pytest.mark.asyncio
async def test_single_source_deployment_is_unchanged(gateway, monkeypatch) -> None:
    monkeypatch.setattr(aq, "get_registry", lambda: _OneSource())
    gw = gateway(("bank",))
    res = await _run("WAS 응답시간")
    assert _sent(gw) == [None], "소스를 고르지 않는다(종전)"
    assert aq.SOURCE_CLARIFICATION_KEY not in res and "source_selection" not in res["apm_query"]
    assert res["source_status"] == [res["apm_query"]["source_status"]]
    assert all(aq.SOURCE_LABEL_KEY not in r for r in res["query_results"])
    assert not _texts(res, disc.APM_UNTARGETED_SCOPE)


# ── 4. 답 표시 ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rows_carry_label_after_source_id_and_per_source_status(gateway) -> None:
    gw = gateway(failing=("legacy",))
    q = "WAS 응답시간"
    res = await _run(q, _isolated(q, selected_apm_source_ids=["bank", "legacy"],
                                  apm_source_basis="selected"))
    assert _sent(gw) == [["bank", "legacy"]]
    (row,) = res["query_results"]
    keys = list(row)
    assert row["source_id"] == "bank" and row[aq.SOURCE_LABEL_KEY] == "은행존 제니퍼"
    assert keys[keys.index("source_id") + 1] == aq.SOURCE_LABEL_KEY
    assert res["organized_data"]["rows"][0][aq.SOURCE_LABEL_KEY] == "은행존 제니퍼"
    system, *per_source = res["source_status"]
    assert system == res["apm_query"]["source_status"] and "source_id" not in system
    assert per_source == [
        {"system": "apm", "label": "은행존 제니퍼", "status": "ok", "rows": 1, "reason": "",
         "source_id": "bank"},
        {"system": "apm", "label": "공동존 제니퍼", "status": "not_selected", "rows": 0,
         "reason": "선택 밖", "source_id": "common"},
        {"system": "apm", "label": "레거시 제니퍼", "status": "unavailable", "rows": 0,
         "reason": "source_unavailable", "source_id": "legacy"},
    ]
    assert "설정된 소스" not in json.dumps(per_source, ensure_ascii=False), \
        "게이트웨이 사유 문구(설정 소스 목록)는 소스별 행에 옮기지 않는다"


def test_unknown_source_id_label_is_the_raw_id() -> None:
    specs = get_registry().sources_of(aq.APM_SYSTEM)
    out = aq._with_source_labels([{"source_id": "zz", "a": 1}, {"a": 2}], specs)
    assert out == [{"source_id": "zz", "source_label": "zz", "a": 1}, {"a": 2}]


# ── 5. 오탐 반례 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "운영체제 버전별 WAS 응답시간", "운영 중인 WAS 응답시간", "운영자 계정 WAS 응답시간",
    "운영팀 담당 WAS 응답시간", "DRM 서버 WAS 응답시간", "ADDRESS 칸 WAS 응답시간",
])
async def test_non_source_words_are_not_source_terms(gateway, query) -> None:
    gw = gateway()
    res = await _run(query)
    assert gw.opened == 0
    assert [o["key"] for o in _asked(res)["options"]] == [*ALL3, "*"], \
        "소스 단어가 아니다 — 좁히지 않고 지목 없음으로 되묻는다"


@pytest.mark.asyncio
async def test_developer_word_narrows_to_common_visibly(gateway) -> None:
    """「개발」은 D-271에서 제외어 없이 확정된 기준 용어다(주의 ①) — 좁혀지면 고지로 드러난다."""
    gw = gateway()
    res = await _run("개발자 담당 WAS 응답시간")
    assert _sent(gw) == [["common"]]
    assert _texts(res, disc.APM_UNTARGETED_SCOPE) == [
        "공동존 제니퍼만 조회했습니다(근거: 「개발」)"]


# ── 6. 복합 질의 — 원문 + task 질의 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_composite_task_reads_original_query_words(gateway) -> None:
    gw = gateway()
    q = "김포 서버 CPU와 WAS 응답시간"
    await _run(q, _isolated(q, is_composite=True, user_query="WAS 응답시간"),
               sub_query="WAS 응답시간")
    assert _sent(gw) == [["common"]], "task 질의에서 위치어가 빠져도 원문 단어를 쓴다(§4.2)"


@pytest.mark.asyncio
async def test_shared_word_text_answer_reasks_from_answer_text_only(gateway) -> None:
    gw = gateway()
    q = "WAS 응답시간"
    res = await _run(q, _isolated(q, apm_source_answer_text="은행존"))
    assert gw.opened == 0
    assert [o["key"] for o in _asked(res)["options"]] == ["bank", "legacy"]
    assert _asked(res)["original_query"] == q


# ── 7. AC-4 — 레지스트리 한 행으로 4번째 소스 ─────────────────────────────────

class _FourSources:
    EAST = SourceSpec(id="east", label="동부 제니퍼", zone="", terms=("동부", "동부 제니퍼"))

    def __init__(self) -> None:
        self._base = get_registry()

    def sources_of(self, system: str) -> tuple[SourceSpec, ...]:
        found = self._base.sources_of(system)
        return (*found, self.EAST) if system == aq.APM_SYSTEM else found

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


@pytest.mark.asyncio
async def test_fourth_source_by_registry_row_only(gateway, monkeypatch) -> None:
    monkeypatch.setattr(aq, "get_registry", lambda: _FourSources())
    monkeypatch.setattr(sel, "select_apm_sources", sel.select_apm_sources)
    gw = gateway((*ALL3, "east"))
    res = await _run("동부 WAS 응답시간")
    assert _sent(gw) == [["east"]] and _row_sources(res) == ["east"]
    assert _texts(res, disc.APM_UNTARGETED_SCOPE) == ["동부 제니퍼만 조회했습니다(근거: 「동부」)"]
    res = await _run("WAS 응답시간")
    assert [o["key"] for o in _asked(res)["options"]] == [*ALL3, "east", "*"]
    gw = gateway((*ALL3, "east"))
    scope = {"ids": ["east"], "basis": "named", "last_target_sources": ["east"]}
    await _run("힙은?", _isolated("힙은?", apm_source_scope=scope))
    assert _sent(gw) == [["east"]], "새 소스도 승계된다"


@pytest.mark.asyncio
async def test_source_failed_at_first_hop_is_failure_with_code_and_notice(gateway) -> None:
    """대상 미지정 첫 홉(부하 순위)에서 빠진 소스 — 「0건」이 아니라 실패(사유 코드) + 의무
    고지(W4 부하 측정에서 발견)."""
    gw = gateway(failing=("legacy",))
    q = "WAS 응답시간"
    res = await _run(q, _isolated(q, host=None, selected_apm_source_ids=["*"],
                                  apm_source_basis="answered"))
    assert gw.tools()[0] == "apm_fleet" and _sent(gw)[0] == list(ALL3)
    assert "legacy" not in _row_sources(res)
    system, *per_source = res["source_status"]
    assert system["status"] == "partial" and "일부 소스 조회 실패 1건" in system["reason"]
    assert [(r["source_id"], r["status"], r["reason"]) for r in per_source] == [
        ("bank", "ok", ""), ("common", "ok", ""), ("legacy", "unavailable", "source_unavailable")]
    assert _texts(res, disc.APM_PARTIAL_SOURCES)[-1] == (
        "레거시 제니퍼는 조회하지 못했습니다(source_unavailable) — 그 소스의 결과는 빠졌습니다")
    dumped = json.dumps(res, ensure_ascii=False, default=str)
    assert "설정된 소스" not in dumped and "_source_failures" not in dumped
