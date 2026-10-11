"""plans/147 W3 · D-322 ⑤ — 스코프 칩 「제니퍼 소스」 축 · 제니퍼 칩 × 해제 · 화면 배선.

고정하는 계약:
  1. `/scope/options` 축 `apm_source` — APM 활성 ∧ 사용 가능 소스 ≥2 ∧ 사용자 `apm` 권한일 때만
     붙는다. 옵션 = 사용 가능 소스(id·라벨·존) + 「전체」(`*`). 불성립이면 축이 없고 응답이
     종전과 같다(AC-6).
  2. 요청 칸 `reset_apm_source_scope` — 제니퍼 승계만 끊는다(DB 스코프 해제 표지는 세우지 않는다) ·
     다음 질문부터 다시 되묻는다(체크포인터 멀티턴 · JSON·SSE 두 경로).
  3. 화면(app.js·index.html) — 칩·되묻기 블록·요청 칸이 스트림·JSON 두 경로에 대칭으로 배선된다.
     브라우저 e2e는 별도 — 계약이 되는 지점만 정적으로 고정한다(`test_ui_zone_scope.py` 선례).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.routing import apm_source_select as sel
from src.routing.registry import SourceSpec
from tests.test_api.test_plan147_w2_route import (  # noqa: F401 — 픽스처 재사용
    ALL3,
    _config,
    _post,
    _thread_values,
    env,
)

pytestmark = pytest.mark.apm_source_ladder

USER: dict[str, Any] = {"sub": "u1", "role": "user"}
_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"


# ── 1. 축 조립(순수 함수) ─────────────────────────────────────────────────────

def _specs(*ids: str) -> tuple[SourceSpec, ...]:
    return tuple(SourceSpec(id=i, label=f"{i} 라벨", zone=f"{i}z") for i in ids)


def test_axis_lists_usable_sources_then_all() -> None:
    from src.routing.db_scope import apm_source_axis

    axis = apm_source_axis(_specs("a", "b", "c"))
    assert axis == {"axis": "apm_source", "exclusive": False, "options": [
        {"key": "a", "label": "a 라벨", "zone": "az", "db_ids": []},
        {"key": "b", "label": "b 라벨", "zone": "bz", "db_ids": []},
        {"key": "c", "label": "c 라벨", "zone": "cz", "db_ids": []},
        {"key": "*", "label": "전체", "zone": "", "db_ids": []},
    ]}


def test_axis_absent_below_two_usable_sources() -> None:
    from src.routing.db_scope import apm_source_axis

    assert apm_source_axis(_specs("a")) is None, "레지스트리 소스 1개"
    assert apm_source_axis(_specs("a", "b", "c"), available={"b"}) is None, "사용 가능 1개"
    assert apm_source_axis(_specs("a", "b", "c"), available=set()) is None
    axis = apm_source_axis(_specs("a", "b", "c"), available={"a", "c"})
    keys = [o["key"] for o in axis["options"]]
    assert keys == ["a", "c", "*"], "미연결 소스는 옵션에 없다"


# ── 2. /scope/options 라우트 ─────────────────────────────────────────────────

def _scope_client(cfg, user: dict | None = None) -> TestClient:
    from src.api.routes import scope as scope_routes

    app = FastAPI()
    app.state.config = cfg
    app.include_router(scope_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: dict(USER if user is None else user)
    return TestClient(app)


def _axes(cfg, user: dict | None = None) -> list[dict]:
    res = _scope_client(cfg, user).get("/api/v1/scope/options")
    assert res.status_code == 200, res.text
    return res.json()["axes"]


def _inactive_config():
    from src.config import AppConfig, DBHubConfig

    return AppConfig(_env_file=None, db_backend="direct", db_connection_string="",
                     log_level="WARNING", dbhub=DBHubConfig(_env_file=None))


def test_axis_present_when_apm_active_with_permission() -> None:
    axes = _axes(_config())
    assert [a["axis"] for a in axes] == ["zone_group", "apm_source"], "존 축은 늘 첫째"
    apm = axes[-1]
    assert apm["exclusive"] is False
    assert [o["key"] for o in apm["options"]] == [*ALL3, "*"]
    assert all(o["label"] for o in apm["options"])
    assert apm["options"][-1] == {"key": "*", "label": "전체", "zone": "", "db_ids": []}
    granted = {"sub": "u2", "role": "user", "allowed_sources": ["apm"]}
    assert [a["axis"] for a in _axes(_config(), granted)][-1] == "apm_source"


def test_axis_absent_without_apm_permission() -> None:
    user = {"sub": "u3", "role": "user", "allowed_sources": ["polestar"]}
    assert [a["axis"] for a in _axes(_config(), user)] == ["zone_group"]
    assert [a["axis"] for a in _axes(_config(), {**user, "allowed_sources": []})] == ["zone_group"]


def test_axis_absent_when_apm_inactive_and_payload_unchanged() -> None:
    from src.routing.db_scope import scope_axes_options

    cfg = _inactive_config()
    res = _scope_client(cfg).get("/api/v1/scope/options")
    expected = scope_axes_options(
        cfg.multi_db.get_active_db_ids(), allowed_db_ids=None,
        group_exclusive=getattr(cfg.multi_db, "zone_group_exclusive", True))
    assert res.json() == expected, "APM 비활성 — 종전 페이로드 그대로(AC-6)"
    assert [a["axis"] for a in res.json()["axes"]] == ["zone_group"]


def test_axis_absent_when_only_one_source_available() -> None:
    sel.set_available_apm_sources({"common"})  # 레지스트리 3 중 2개 미연결
    assert [a["axis"] for a in _axes(_config())] == ["zone_group"]


def test_unavailable_source_is_not_an_option() -> None:
    sel.set_available_apm_sources({"bank", "common"})
    apm = _axes(_config())[-1]
    assert [o["key"] for o in apm["options"]] == ["bank", "common", "*"]


def test_axis_absent_with_single_registry_source(monkeypatch) -> None:
    import src.routing.registry as registry_mod

    real = registry_mod.get_registry()
    one = real.sources_of("apm")[:1]
    proxy = SimpleNamespace(sources_of=lambda system: one if system == "apm" else ())
    monkeypatch.setattr(registry_mod, "get_registry", lambda: proxy)
    from src.routing.db_scope import apm_source_axis

    assert apm_source_axis(proxy.sources_of("apm")) is None
    # 라우트도 같은 접근자를 읽는다 — APM 활성 판정(apm_query)은 실 레지스트리 그대로
    from src.api.routes import scope as scope_routes

    assert scope_routes._apm_source_axis(_config(), USER) is None


# ── 3. 제니퍼 칩 × — reset_apm_source_scope ──────────────────────────────────

def test_request_field_defaults_false() -> None:
    from src.api.schemas import QueryRequest

    assert QueryRequest(query="x").reset_apm_source_scope is False


def test_followup_input_clears_only_apm_scope() -> None:
    from src.state import create_followup_input

    delta = create_followup_input("힙 사용률은?", reset_apm_source_scope=True)
    assert delta["apm_source_scope"] is None and delta["apm_source_pending"] is None
    assert delta["db_scope_reset"] is False
    assert "active_db_id" not in delta and "source_choice" not in delta, "DB 스코프는 그대로"
    plain = create_followup_input("힙 사용률은?")
    assert "apm_source_scope" not in plain, "기본 off — 승계 키를 건드리지 않는다"
    both = create_followup_input("힙 사용률은?", reset_db_scope=True)
    assert both["apm_source_scope"] is None, "DB 스코프 해제는 제니퍼 승계도 비운다(종전)"


@pytest.mark.parametrize("route", ("json", "sse"))
def test_reset_apm_source_scope_reasks_next_question(env, route) -> None:  # noqa: F811
    client, thread = env.client(), f"p147-w3-reset-{route}"
    out = _post(client, route, "김포 WAS 응답시간", thread)
    assert env.gw.take() == [["common"]]
    assert out["apm_source_scope"]["ids"] == ["common"]

    out = _post(client, route, "힙 사용률은?", thread, reset_apm_source_scope=True)
    assert env.gw.take() == [], "승계가 끊겨 조회 0 + 되묻기"
    assert [o["key"] for o in out["apm_source_clarification"]["options"]] == [*ALL3, "*"]
    assert not out.get("apm_source_scope")
    values = _thread_values(env, thread)
    assert values["apm_source_scope"] is None
    assert values["apm_source_pending"]["query"] == "힙 사용률은?"
    assert values["db_scope_reset"] is False, "DB 스코프 해제 표지는 세우지 않는다"

    out = _post(client, route, "힙 사용률은?", thread, selected_apm_source_ids=["legacy"])
    assert env.gw.take() == [["legacy"]], "되묻기 버튼 답 → 그 소스로"
    assert out["apm_source_scope"]["basis"] == "answered"


def test_reset_without_prior_scope_is_harmless(env) -> None:  # noqa: F811
    client, thread = env.client(), "p147-w3-reset-noop"
    _post(client, "json", "레거시 WAS 응답시간", thread, reset_apm_source_scope=True)
    assert env.gw.take() == [["legacy"]], "같은 턴의 지목은 그대로 성립한다"
    assert _thread_values(env, thread)["apm_source_scope"]["ids"] == ["legacy"]


# ── 4. 화면 배선(정적) ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app_js() -> str:
    return (_STATIC / "js" / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


def test_chip_markup_hidden_by_default(index_html) -> None:
    chip = index_html[index_html.index('id="apmScopeChip"') - 40:]
    assert '<div class="db-scope-chip" id="apmScopeChip" data-state="none" hidden>' in index_html
    for id_ in ("apmScopePick", "apmScopeText", "apmScopeClear", "apmScopePopover"):
        assert f'id="{id_}"' in chip
    assert index_html.index('id="dbScopeChip"') < index_html.index('id="apmScopeChip"') \
        < index_html.index('<div class="input-row">')
    css = (_STATIC / "css" / "style.css").read_text(encoding="utf-8")
    assert ".db-scope-chip[hidden] {\n    display: none;" in css, "flex가 hidden을 덮지 않게"


def test_chip_shows_only_with_axis(app_js) -> None:
    assert ('return (scopeAxes || []).filter(function (a) { return a.axis === "apm_source"; })[0]'
            in app_js)
    assert 'apmScopeChip.hidden = !axis && state === "none";' in app_js
    assert "loadScopeOptions().then(updateApmScopeChip);" in app_js
    # 축 로드는 인증 확정(revealApp) 뒤 — 미로그인 첫 방문은 로그인으로 넘어간 뒤 읽지 않는다
    auth = app_js[app_js.index("function checkAuthOnLoad()"):app_js.index("checkAuthOnLoad();")]
    assert auth.index("revealApp();\n") < auth.index("loadScopeOptions().then(updateApmScopeChip);")


def test_chip_texts(app_js) -> None:
    for s in ('"제니퍼: " + text', '" · 승계 중"', '" · 지목"', '" · 선택 중"',
              '"미지정 — 질문에서 확인"', '"다음 질문에서 다시 확인"'):
        assert s in app_js


def test_request_fields_on_both_text_routes(app_js) -> None:
    for body in ("streamBody", "queryBody"):
        assert f"{body}.selected_apm_source_ids = apmScope.ids;" in app_js
        assert f"{body}.reset_apm_source_scope = true;" in app_js
    assert 'formData.append("selected_apm_source_ids"' not in app_js, "파일 경로는 받지 않는다"
    assert ("await executeFallbackQuery(query, selectedDbIds, formMemoryDelete, resetDbScope, "
            "selectedSources, apmScope);") in app_js


def test_pending_consumed_once_and_old_call_kept(app_js) -> None:
    send = app_js[app_js.index("var sendDbIds = pendingDbIds;"):]
    send = send[:send.index("// ─── Render User Message ───")]
    assert "pendingApmIds = null;\n            pendingApmReset = false;" in send
    assert ("executeStreamingQuery(query, sendDbIds, undefined, undefined, undefined, sendReset, "
            "undefined, sendApm);") in send
    assert ("executeStreamingQuery(query, sendDbIds, undefined, undefined, undefined, sendReset);"
            in send)


def test_scope_and_clarification_mirrored_on_both_paths(app_js) -> None:
    assert "renderApmScopeChip(metaData.apm_source_scope, apmScope, resetDbScope);" in app_js
    assert "renderApmScopeChip(data.apm_source_scope, apmScope, resetDbScope);" in app_js
    for src in ("metaData", "data"):
        call = f"appendApmSourceClarificationToLastBubble({src}.apm_source_clarification);"
        assert app_js.count(call) == 1


def test_clarification_resends_original_query_with_selection(app_js) -> None:
    body = app_js[app_js.index("function renderApmSourceClarification(bubble, clar)"):]
    body = body[:body.index("function appendApmSourceClarificationToLastBubble(clar)")]
    assert ('executeStreamingQuery(clar.original_query || "", null, null, null, null, false, null,'
            ' { ids: ids });') in body
    assert '"선택: " + labels.join(", ")' in body, "존 역질문과 같은 에코 말풍선"
    assert 'class="zone-clarify apm-source-clarify"' in body, "새 질의 전송 시 자기정리 대상"
    assert "bindApmAllExclusive(checks" in body


def test_all_option_is_exclusive(app_js) -> None:
    body = app_js[app_js.index("function bindApmAllExclusive(checks, onChange, single)"):]
    body = body[:body.index("function closeApmScopePopover()")]
    assert 'var APM_ALL_KEY = "*";' in app_js
    assert "(single || isAll || x.value === APM_ALL_KEY)" in body


def test_reset_clears_mirror_and_restore_resets(app_js) -> None:
    body = app_js[app_js.index("function renderApmScopeChip(scope, sentApm, resetDbScope)"):]
    body = body[:body.index("function resetApmScopeChip()")]
    assert "} else if (resetDbScope || (sentApm && sentApm.reset)) {" in body
    restore = app_js[app_js.index("function showThreadTurns(threadId, turns)"):]
    restore = restore[:restore.index("function startNewChat()")]
    assert "resetApmScopeChip();" in restore
    clear = app_js[app_js.index("function setupApmScopeChip()"):]
    assert "pendingApmReset = true;\n            pendingApmIds = null;" in clear


# ── 5. 처리 현황 — 소스 되묻기는 「확인 필요」(오류 아님) ────────────────────────

def test_task_summary_marks_source_ask_and_keeps_error() -> None:
    from src.api.routes.query import _summarize_tasks

    tasks = [{"task_id": "t1", "order": 1, "agent": "apm_query", "status": "failed"},
             {"task_id": "t2", "order": 2, "agent": "apm_query", "status": "failed"}]
    results = {
        "t1": {"error": "어느 것을 조회할까요?", "degraded_reason": "apm_unresolved_condition",
               "apm_source_clarification": {"question": "어느 것을 조회할까요?", "options": []}},
        "t2": {"error": "레거시 제니퍼는 연결되지 않아 조회하지 않았습니다.",
               "degraded_reason": "source_unavailable"},
    }
    ask, unavailable = _summarize_tasks(tasks, results)
    assert ask["clarification"] is True and ask["error"] == "어느 것을 조회할까요?", "error 유지"
    assert "clarification" not in unavailable and unavailable["error"], "미연결 안내는 종전 표시"


def test_stream_node_complete_carries_ask_marker(env) -> None:  # noqa: F811
    import json

    r = env.client().post("/api/v1/query/stream",
                          json={"query": "WAS 응답시간 알려줘", "thread_id": "p147-w3-badge"})
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    (step,) = [e for e in events if e["type"] == "node_complete"
               and e["node"] == "agent_orchestrator"]
    (task,) = step["data"]["tasks"]
    assert task["clarification"] is True and task["status"] == "failed" and task["error"]
    js = (_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "if (t.clarification) {\n                // plans/147" in js
    assert 'step-data-badge--info">확인 필요</span>' in js
    assert ('markApmClarifyTasks(meta);   // plans/147 — 되묻기 task는 「확인 필요」\n'
            '        endStreamStatus("done", meta);') in js
    assert ': t.clarification ? "clarify"' in js, "상태줄 목록도 실패(✕)가 아니다"
