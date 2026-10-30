"""plans/199 W1 — 조사 결과 「한눈에 보기」 카드 · D-352.

`BriefingView.summaryModel`(순수 모델)·`summaryCardHtml`(카드 HTML)·`renderBody`·`attach`를
검사한다. W0 화면 픽스처(`tests/fixtures/plan199/`)를 node로 공용 렌더에 넣어
원인 영역·확실성·결론 출처·꼭 확인을 단언한다. 단정 금지 케이스는 픽스처를 테스트 안에서
고쳐 만든다(픽스처 파일은 읽기만). 기능 조회 플래그(`NOISE_INVESTIGATION_SUMMARY_VIEW_ENABLED`)가
꺼지면 계획 시작 SHA의 렌더와 바이트 동일해야 한다(`git show`로 기준 원문을 임시 파일에 올려
같은 입력을 대조).
"""

from __future__ import annotations

import ast
import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.api.routes.alarm_schemas import AlarmCapabilitiesResponse
from src.config.alarm import NoiseGateConfig
from tests.test_api.test_alarm_capabilities import _client

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "static"
_BV_JS = _STATIC / "js" / "briefing-view.js"
_APP_JS = _STATIC / "js" / "app.js"
_NOISE_JS = _STATIC / "js" / "noise.js"
_DIAG_JS = _STATIC / "js" / "diagnosis-view.js"
_FIX = _ROOT / "tests" / "fixtures" / "plan199"
_W5B = _ROOT / "tests" / "fixtures" / "plan187_w5b"
_ENGINE_AGREEMENT = _ROOT / "sre_agent" / "sre_agent" / "domain" / "engine_agreement.py"
_BRIEFING_BUILDER = _ROOT / "sre_agent" / "sre_agent" / "application" / "briefing_builder.py"
#: plans/199 시작 SHA — 플래그 off 비트 동일의 기준 렌더
_BASE_SHA = "8b32b317147ef9f57ae7d6ab872e914dea6539c0"
_VIEW_KEYS = ("view", "user_view", "poll_view")


def _node(body: str, base: Path | None = None) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 순수 함수 실행 테스트 생략")
    lines = [f"var BV = require({json.dumps(str(_BV_JS))});"]
    if base is not None:
        lines.append(f"var BASE = require({json.dumps(str(base))});")
    lines += [
        "var out = null;",
        "function tick() { return new Promise(function (r) { setTimeout(r, 5); }); }",
        "(async function () {",
        body,
        "process.stdout.write(JSON.stringify(out));",
        "})().catch(function (e) { console.error(e); process.exit(1); });",
    ]
    done = subprocess.run(
        ["node", "-e", "\n".join(lines)], capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIX / f"{name}.json").read_text(encoding="utf-8"))


def _view(name: str, key: str = "view") -> dict[str, Any]:
    return copy.deepcopy(_fixture(name)[key])


def _model(view: dict[str, Any]) -> dict[str, Any]:
    return _node(f"out = BV.summaryModel({_js(view)});")


def _models(views: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _node(f"out = {_js(views)}.map(function (v) {{ return BV.summaryModel(v); }});")


def _card(view: dict[str, Any]) -> str:
    return _node(f"out = BV.summaryCardHtml(BV.summaryModel({_js(view)}));")


def _render(view: dict[str, Any], extra: dict[str, Any] | None) -> str:
    return _node(f"out = BV.renderBody({_js(view)}, {_js(extra)});")


def _layer_labels() -> dict[str, str]:
    return _node("out = BV.LAYER_LABELS;")


# ─── 1. W0 픽스처별 — 원인 영역 · 확실성 · 결론 출처 · 꼭 확인 ─────────────────

#: (픽스처, 키) → (원인 영역 계층, 확실성, 다른 분석 계층, 결론 출처, 꼭 확인 [(종류, 포함 문구)])
_EXPECT: dict[tuple[str, str], tuple[str, str, str | None, str | None, list[tuple[str, str]]]] = {
    # 1순위 「지지」 · 엔진 비교 불가 · 근거 단계 결손 없음 → 확정 · 알려진 문제 일치는 한 줄
    ("confirmed_cpu_ke", "view"): (
        "cpu", "확정", None, "playbook", [("info", "알려진 문제 KE-DEMO-012")]),
    ("confirmed_same_layer", "view"): ("gc", "확정", None, "playbook", []),
    # 심층 조사 실패·생략은 확실성·꼭 확인에 넣지 않는다(담당자용)
    ("deep_failed", "view"): ("external", "확정", None, "playbook", []),
    ("deep_skipped", "view"): ("external", "확정", None, "playbook", []),
    # 엔진 비교 different_layer → 추정 + 「다른 분석은 ○○ 영역」
    ("different_layer", "view"): ("external", "추정", "db", "playbook", []),
    # 1순위 판단 보류 → 추정
    ("episode_scope", "view"): ("db", "추정", None, "playbook", []),
    ("episode_scope", "user_view"): ("db", "추정", None, "playbook", []),
    # 결론 근거 단계(S6) 결손 → 추정 + 꼭 확인 ②
    ("evidence_missing", "view"): (
        "cpu", "추정", None, "playbook", [("warn", "S6 프로세스 CPU 상위 결손(결론 근거 단계)")]),
    ("markdown_narrative", "view"): ("db", "추정", None, "playbook", []),
    ("markdown_narrative", "poll_view"): ("db", "추정", None, "playbook", []),
    # 플레이북 계층 unknown + 가설 없음 → 미확정(영역은 HolmesGPT 계층으로 보이되 단정하지 않는다)
    ("missing_many", "view"): (
        "db", "미확정", None, "deep",
        [("warn", "S4 호스트 자원 시간대별 추이 결손 — 폴스타 서버 미확정"),
         ("warn", "S6 프로세스 CPU 상위 결손 — 폴스타 서버 미확정"),
         ("warn", "S8 DB 상태(DPM) 미연동")],
    ),
    # 비플레이북 — 「원인 가설」 줄이 없으면 미확정 · 결론은 「요약」 첫 줄
    ("non_playbook", "view"): ("unknown", "미확정", None, "deep", []),
    ("running_collect", "view"): (
        "unknown", "미확정", None, None, [("run", "10단계 중 4단계까지")]),
    ("running_deep", "view"): ("db", "추정", None, "playbook", [("run", "10단계 중 9단계까지")]),
    ("user_view_secret", "view"): ("external", "추정", "db", "playbook", []),
    ("user_view_secret", "user_view"): ("external", "추정", "db", "playbook", []),
}


def test_expectation_table_covers_every_fixture_view() -> None:
    """픽스처가 늘면 이 표도 같이 늘어야 한다(빠진 케이스 없이)."""
    present = {
        (p.stem, key)
        for p in _FIX.glob("*.json")
        for key in _VIEW_KEYS
        if key in json.loads(p.read_text(encoding="utf-8"))
    }
    assert present == set(_EXPECT)


def test_fixture_models_match_expectations() -> None:
    cases = sorted(_EXPECT)
    models = _models([_view(name, key) for name, key in cases])
    labels = _layer_labels()
    for (name, key), m in zip(cases, models, strict=True):
        layer, certainty, alt, source, must = _EXPECT[(name, key)]
        where = f"{name}/{key}"
        assert m["area"]["layer"] == layer, where
        assert m["area"]["label"] == labels[layer], where
        assert m["area"]["certainty"] == certainty, where
        assert (m["area"].get("alt") or {}).get("layer") == alt, where
        if alt:
            assert m["area"]["alt"]["label"] == labels[alt], where
        assert m["conclusion"]["source"] == source, where
        assert m["conclusion"]["text"], where
        assert [x["kind"] for x in m["mustSee"]] == [k for k, _ in must], (where, m["mustSee"])
        for item, (_, text) in zip(m["mustSee"], must, strict=True):
            assert text in item["text"], (where, item)
        assert len(m["actions"]) <= 2 and m["owner"] is None, where


def test_fixture_details_conclusion_target_scope() -> None:
    names = ["confirmed_cpu_ke", "episode_scope", "running_collect", "running_deep", "non_playbook"]
    cpu, episode, collect, deep, plain = _models([_view(n) for n in names])
    # 결론 = 1순위 가설 제목 · 대상 = 호스트·인스턴스 · 증상 · 벽시계 발생 시각(시간대 이동 없음)
    top = _view("confirmed_cpu_ke")["briefing"]["playbook"]["synthesis"]["hypotheses"][0]
    assert cpu["conclusion"]["text"] == top["title"]
    assert cpu["target"] == {
        "name": "wasdemo01 · A1", "symptom": "TX_BAD_RESPONSE", "at": "2026-10-19 10:00"}
    # 영향 범위 — 사건 종합 조사 · 같은 사건 알람 수(출처별) · 이상 인스턴스
    assert episode["scope"][0] == "사건 종합 조사 ep-demo-1"
    assert episode["scope"][1].startswith("같은 사건 알람 3건(폴스타 2 · 제니퍼 1)")
    assert episode["scope"][2] == "이상 인스턴스 3/3"
    # 진행 중 — 「지금까지: …」 + 진척 칩
    assert collect["conclusion"]["text"].startswith("지금까지: ")
    assert collect["conclusion"]["progress"] == "진행 중 S4/10"
    assert deep["conclusion"]["text"].startswith("지금까지: ")
    assert deep["conclusion"]["progress"] == "진행 중 S9/10"
    # 비플레이북 — 대상·영향 범위 줄 없음(재료 없음)
    assert plain["target"] is None and plain["scope"] == []


# ─── 2. 단정 금지 — 파생 입력 ───────────────────────────────────────────────


def _syn(view: dict[str, Any]) -> dict[str, Any]:
    return view["briefing"]["playbook"]["synthesis"]


def _derived() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    v = _view("confirmed_same_layer")
    _syn(v)["hypotheses"][0].update(status="inconclusive", status_label="판단 보류")
    out["top_inconclusive_status"] = v
    v = _view("confirmed_same_layer")
    # 코드는 지지 · 문구는 보류 — 하나라도 다르면 지지 아님
    _syn(v)["hypotheses"][0]["status_label"] = "판단 보류"
    out["label_mismatch"] = v
    v = _view("confirmed_same_layer")
    _syn(v)["top_inconclusive"] = True
    out["top_inconclusive_flag"] = v
    v = _view("confirmed_same_layer")
    v["briefing"]["engine_agreement"].update(result="different_layer")
    v["briefing"]["engine_agreement"]["holmesgpt"]["layers"] = ["db"]
    out["different_layer"] = v
    v = _view("confirmed_cpu_ke")
    for sec in v["briefing"]["playbook"]["sections"]:
        if sec["step"] == "S6":
            sec.update(status="missing", reason="polestar_process_snapshot: 테스트 결손 — 상세")
    out["evidence_gap"] = v
    v = _view("confirmed_same_layer")
    _syn(v)["hypotheses"] = []
    out["no_top_hypothesis"] = v
    v = _view("confirmed_same_layer")
    _syn(v)["hypotheses"] = []
    v["briefing"]["engine_agreement"]["playbook"]["layer"] = "unknown"
    v["briefing"]["engine_agreement"]["holmesgpt"]["layers"] = []
    out["no_layer_no_hypothesis"] = v
    v = _view("confirmed_same_layer")
    v["briefing"]["engine_agreement"]["playbook"]["layer"] = "unknown"
    out["unknown_layer_with_hypothesis"] = v
    v = _view("confirmed_same_layer")
    v["status"] = "timeout"
    out["timeout"] = v
    # 확정 뷰를 진행 중 부분 결과로 — 진행 중이면 확정 금지
    run = _view("running_deep")
    run["partial_briefing"] = {**_view("confirmed_same_layer")["briefing"], "partial": True}
    out["running_confirmed"] = run
    v = _view("confirmed_same_layer")
    v["briefing"]["engine_agreement"]["playbook"]["layer"] = "network"
    out["unknown_enum"] = v
    return out


#: 파생 케이스 → (확실성, 원인 영역 계층, 다른 분석 계층)
_DERIVED_EXPECT: dict[str, tuple[str, str, str | None]] = {
    "top_inconclusive_status": ("추정", "gc", None),
    "label_mismatch": ("추정", "gc", None),
    "top_inconclusive_flag": ("추정", "gc", None),
    "different_layer": ("추정", "gc", "db"),
    "evidence_gap": ("추정", "cpu", None),
    "no_top_hypothesis": ("추정", "gc", None),
    "no_layer_no_hypothesis": ("미확정", "unknown", None),
    "unknown_layer_with_hypothesis": ("추정", "gc", None),
    "timeout": ("미확정", "gc", None),
    "running_confirmed": ("추정", "gc", None),
    "unknown_enum": ("확정", "network", None),
}


def test_no_overclaim_on_derived_inputs() -> None:
    """확정은 1순위 「지지」 + 다른 조건 없음일 때만 — 하나라도 걸리면 추정·미확정."""
    derived = _derived()
    names = sorted(_DERIVED_EXPECT)
    models = dict(zip(names, _models([derived[n] for n in names]), strict=True))
    for name in names:
        certainty, layer, alt = _DERIVED_EXPECT[name]
        m = models[name]
        assert m["area"]["certainty"] == certainty, (name, m["area"])
        assert m["area"]["layer"] == layer, name
        assert (m["area"].get("alt") or {}).get("layer") == alt, name
    # 계층 표에 없는 값은 원문 그대로(단정하지 않고 숨기지도 않는다)
    assert models["unknown_enum"]["area"]["label"] == "network"
    # 근거 단계 결손 — 꼭 확인 ② 한 줄 · 사유는 첫 「 — 」 앞까지
    gap = [x for x in models["evidence_gap"]["mustSee"] if x["kind"] == "warn"]
    assert [x["text"] for x in gap] == [
        "S6 프로세스 CPU 상위 결손(결론 근거 단계) — polestar_process_snapshot: 테스트 결손"]
    # 1순위 가설 없음 — 결론은 심층 조사 원인(출처 「심층 조사」)
    assert models["no_top_hypothesis"]["conclusion"]["source"] == "deep"
    # 시간 초과 — 꼭 확인 ① 실패 줄이 맨 앞 · 진행 중 — ① 진행 줄
    assert models["timeout"]["mustSee"][0]["kind"] == "warn"
    assert models["running_confirmed"]["mustSee"][0] == {
        "kind": "run", "text": "조사 진행 중 — 10단계 중 9단계까지의 결과입니다"}
    assert models["running_confirmed"]["conclusion"]["text"].startswith("지금까지: ")


def test_non_playbook_hypothesis_line_is_presumed_and_failure_undetermined() -> None:
    gated = _node("out = BV.HUMAN_GATED_NOTE;")
    lines = [
        {"label": "요약", "text": "web-01 CPU 95%"},
        {"label": "원인 가설", "text": "[가설] 배치 겹침 CPU 포화\n[가설] 두 번째 가설"},
        {"label": "권고", "text": "배치 시각 조정\n스레드 덤프 확보\n세 번째\n" + gated},
    ]
    hypo, failed = _models([
        {"status": "done", "investigation_id": "i1", "briefing_lines": lines},
        {"status": "failed", "investigation_id": "i2", "briefing_lines": [],
         "error": "조사 서비스 오류"},
    ])
    assert hypo["area"] == {"layer": "unknown", "label": "원인 영역 미확정", "certainty": "추정"}
    assert hypo["conclusion"] == {"text": "배치 겹침 CPU 포화", "source": "deep", "progress": ""}
    assert hypo["actions"] == ["배치 시각 조정", "스레드 덤프 확보"]   # 최대 2 · 승인 표지 줄 제외
    assert failed["area"]["certainty"] == "미확정"
    assert failed["conclusion"]["source"] is None
    assert failed["conclusion"]["text"].startswith("원인 미확정")
    assert failed["mustSee"][0]["kind"] == "warn"


def test_must_see_caps_at_three_with_more_line() -> None:
    v = _view("missing_many")
    v["briefing"]["playbook"]["known_errors"] = [
        {"id": "KE-T-1", "title": "후보 1", "result": "candidate", "workaround": ["x"]},
    ]
    m = _model(v)
    assert [x["kind"] for x in m["mustSee"]] == ["warn", "warn", "warn", "more"]
    assert m["mustSee"][-1]["text"] == "외 1건"


# ─── 3. 원인 영역 문구 표 ↔ sre_agent `engine_agreement.LAYERS` ───────────────


def _sre_layers() -> set[str]:
    """sre_agent 판독 계층 상수 — import 없이 ast로 읽는다(양방향 import 0)."""
    tree = ast.parse(_ENGINE_AGREEMENT.read_text(encoding="utf-8"))
    consts: dict[str, Any] = {}
    layers: list[str] = []
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None)
        if not isinstance(target, ast.Name) or node.value is None:
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            consts[target.id] = node.value.value
        elif target.id == "LAYERS" and isinstance(node.value, ast.Tuple):
            layers = [consts[e.id] if isinstance(e, ast.Name) else e.value for e in node.value.elts]  # type: ignore[attr-defined]
    assert layers, "engine_agreement.LAYERS를 찾지 못했다"
    return set(layers) | {consts["LAYER_UNKNOWN"]}


def test_layer_labels_match_sre_agent_layers() -> None:
    labels = _layer_labels()
    assert set(labels) == _sre_layers()
    assert labels["unknown"] == "원인 영역 미확정"
    assert all(isinstance(v, str) and v for v in labels.values())


def test_no_action_marker_matches_sre_agent_literals() -> None:
    """「권고 없음」 고정 표지 — sre 생산 코드 리터럴이 전부 JS 접두로 시작한다(ast로 읽음)."""
    prefix = _node("out = BV.NO_ACTION_PREFIX;")
    tree = ast.parse(_BRIEFING_BUILDER.read_text(encoding="utf-8"))
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and "조치 권고 없음" in n.value
    ]
    # 심층 recommendation.items · 플레이북 최상위 recommendation.items
    assert len(literals) >= 2, literals
    assert all(text.startswith(prefix) for text in literals), (prefix, literals)


def test_no_action_marker_is_not_listed_as_action() -> None:
    """표지는 할 일이 아니다 — 다 빠지면 할 일 줄 자체를 그리지 않는다."""
    prefix = _node("out = BV.NO_ACTION_PREFIX;")
    names = ["missing_many", "confirmed_same_layer", "non_playbook"]
    models = _models([_view(n) for n in names])
    for name, m in zip(names, models, strict=True):
        assert not [a for a in m["actions"] if a.startswith(prefix)], (name, m["actions"])
    many, same, plain = models
    assert many["actions"] == [] and plain["actions"] == []
    assert same["actions"]   # 플레이북 대응 제안(실제 할 일)은 그대로
    for name in ("missing_many", "non_playbook"):
        assert ">할 일<" not in _card(_view(name)), name


# ─── 4. 카드 HTML — 배치 · 요약형 대체 · 이스케이프 ─────────────────────────


def test_card_rows_order_and_actions_slot() -> None:
    html = _card(_view("confirmed_cpu_ke"))
    assert html.startswith('<section class="bv-sum" data-bv-summary="1">')
    marks = ["한눈에 보기", ">원인 영역<", ">결론<", ">대상<",
             ">영향 범위<", ">할 일<", ">꼭 확인<"]
    idx = [html.index(m) for m in marks]
    assert idx == sorted(idx), list(zip(marks, idx, strict=True))
    assert '<div class="bv-sum-actions" data-bv-summary-actions></div></section>' in html
    assert "WAS 애플리케이션(CPU 처리)" in html and ">확정<" in html and ">플레이북<" in html
    assert _node("out = BV.HUMAN_GATED_NOTE;") in html   # 할 일에 승인 표지 유지
    assert "bv-sum-must-item--info" in html
    # 다른 분석 영역 병기
    assert "다른 분석은 DB(SQL·커넥션 풀) 영역" in _card(_view("different_layer"))


@pytest.mark.parametrize("mode", ["summary", "detail"])
def test_render_puts_card_right_under_head_when_on(mode: str) -> None:
    view = _view("confirmed_cpu_ke")
    on = _render(view, {"mode": mode, "summaryView": True})
    off = _render(view, {"mode": mode})
    assert on.count('data-bv-summary="1"') == 1 and 'data-bv-summary="1"' not in off
    first = "bv-pb-meta" if mode == "summary" else "bv-engine"
    assert on.index('data-bv-summary="1"') < on.index(first)
    # 요약형의 「종합(플레이북)」 블록은 카드로 대체 · 상세형은 그대로
    if mode == "summary":
        assert "종합(플레이북)" in off and "종합(플레이북)" not in on
    else:
        assert "종합(플레이북)" in on


def test_render_card_on_non_playbook_and_none_statuses() -> None:
    plain = _view("non_playbook")
    assert 'data-bv-summary="1"' in _render(plain, {"summaryView": True})
    for status in ("none", "episode_existing"):
        out = _render({"status": status, "previous": []}, {"summaryView": True})
        assert 'data-bv-summary' not in out, status


def test_card_escapes_untrusted_text() -> None:
    v = _view("confirmed_cpu_ke")
    _syn(v)["hypotheses"][0]["title"] = "<script>alert(1)</script>"
    v["briefing"]["playbook"]["scope"]["host"] = '"><img src=x onerror=alert(1)>'
    v["briefing"]["playbook"]["known_errors"][0]["title"] = "<img src=x onerror=alert(2)>"
    _syn(v)["actions"][0]["text"] = "<b onmouseover=alert(3)>x</b>"
    html = _card(v)
    for raw in ("<script", "<img", "<b ", "onerror=alert(1)>"):
        assert raw not in html, raw
    assert "&lt;script&gt;" in html and "&lt;img" in html and "&quot;&gt;" in html


def test_user_view_card_has_no_secret_values() -> None:
    """사용자 뷰(마스킹) 재료만 읽는다 — 원문 비밀값 꼴이 카드에 새지 않는다."""
    html = _card(_view("user_view_secret", "user_view"))
    assert "abc123" not in html and "Bearer ey" not in html


# ─── 5. 플래그 off — 계획 시작 SHA 렌더와 바이트 동일 ──────────────────────


@pytest.fixture(scope="module")
def base_bv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    try:
        src = subprocess.run(
            ["git", "-C", str(_ROOT), "show", f"{_BASE_SHA}:src/static/js/briefing-view.js"],
            check=True, capture_output=True, text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"기준 SHA {_BASE_SHA[:8]}의 briefing-view.js를 읽지 못함")
    path = tmp_path_factory.mktemp("plan199_base") / "briefing-view.base.js"
    path.write_text(src, encoding="utf-8")
    return path


def _w5b_views() -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for p in sorted(_W5B.glob("*.json")):
        snap = json.loads(p.read_text(encoding="utf-8"))
        if "briefing" in snap:
            views.append({"status": "done", "investigation_id": f"inv-{p.stem}", "kind": "alarm",
                          "briefing_lines": [], "briefing": snap["briefing"],
                          "updated_at": "2026-10-19T01:00:00+00:00"})
        if "partial_briefing" in snap:
            views.append({"status": "running", "investigation_id": "inv-run", "briefing_lines": [],
                          "progress": snap["progress"],
                          "partial_briefing": snap["partial_briefing"]})
    return views


def _all_views() -> list[dict[str, Any]]:
    views = [
        _view(p.stem, key)
        for p in sorted(_FIX.glob("*.json"))
        for key in _VIEW_KEYS
        if key in json.loads(p.read_text(encoding="utf-8"))
    ]
    views += _w5b_views()
    views += [
        {"status": "none", "previous": []},
        {"status": "episode_existing", "investigation_id": "inv-e"},
        {"status": "failed", "investigation_id": "inv-f", "briefing_lines": [], "error": "x"},
        {"status": "done", "investigation_id": "inv-p",
         "briefing_lines": [{"label": "요약", "text": "s"},
                            {"label": "원인 가설", "text": "[가설] c"}]},
    ]
    return views


_OFF_EXTRAS: list[Any] = [
    None,
    {},
    {"note": "n"},
    {"mode": "summary"},
    {"mode": "summary", "openConversation": True},
    {"mode": "detail", "engineChoice": True, "ui": {}},
    {"mode": "detail", "summaryView": False},
    {"summaryView": False},
]


def test_flag_off_render_is_bit_identical_to_base(base_bv: Path) -> None:
    views = _all_views()
    diffs = _node(
        f"""
var views = {_js(views)};
var extras = {_js(_OFF_EXTRAS)};
var bad = [];
views.forEach(function (v, i) {{
    extras.forEach(function (x, j) {{
        var a = BASE.renderBody(v, x === null ? undefined : x);
        var b = BV.renderBody(v, x === null ? undefined : x);
        if (a !== b) bad.push([i, v.investigation_id || v.status, j]);
    }});
}});
out = {{ n: views.length * extras.length, bad: bad }};
""",
        base=base_bv,
    )
    assert diffs["n"] >= 200 and diffs["bad"] == []


def test_flag_off_attach_is_bit_identical_to_base(base_bv: Path) -> None:
    """attach 경로 — summaryView 함수가 거짓이면 옵션이 없던 종전 화면과 같다."""
    views = [_view("confirmed_cpu_ke"), _view("non_playbook"), _view("different_layer", "view")]
    out = _node(
        f"""
function box() {{
    return {{ innerHTML: "", isConnected: true, addEventListener: function () {{}},
        contains: function () {{ return true; }}, querySelectorAll: function () {{ return []; }},
        querySelector: function () {{ return null; }} }};
}}
var views = {_js(views)};
var renders = [undefined, {{ mode: "summary" }}, {{ mode: "detail" }}];
var bad = [];
for (var i = 0; i < views.length; i++) {{
    for (var j = 0; j < renders.length; j++) {{
        var v = views[i];
        var a = box(), b = box();
        var oa = {{ initial: v, load: function () {{ return Promise.resolve(v); }} }};
        var ob = {{ initial: v, load: function () {{ return Promise.resolve(v); }},
            summaryView: function () {{ return false; }} }};
        if (renders[j]) {{ oa.render = renders[j]; ob.render = renders[j]; }}
        var pa = BASE.attach(a, oa), pb = BV.attach(b, ob);
        await pa.open(); await pb.open(); await tick();
        pa.close(); pb.close();
        if (a.innerHTML !== b.innerHTML || !a.innerHTML) bad.push([i, j]);
    }}
}}
out = bad;
""",
        base=base_bv,
    )
    assert out == []


# ─── 6. attach 옵션 · 화면 배선 ──────────────────────────────────────────


def test_attach_reads_summary_view_option_at_render_time() -> None:
    view = _view("confirmed_cpu_ke")
    out = _node(
        f"""
function box() {{
    return {{ innerHTML: "", isConnected: true, addEventListener: function () {{}},
        contains: function () {{ return true; }}, querySelectorAll: function () {{ return []; }},
        querySelector: function () {{ return null; }} }};
}}
var v = {_js(view)};
var res = {{}};
var cases = {{ fnOn: function () {{ return true; }}, fnOff: function () {{ return false; }},
    boolOn: true, absent: undefined }};
for (var k in cases) {{
    for (var r = 0; r < 2; r++) {{
        var c = box();
        var o = {{ initial: v, load: function () {{ return Promise.resolve(v); }} }};
        if (cases[k] !== undefined) o.summaryView = cases[k];
        if (r) o.render = {{ mode: "detail" }};
        var p = BV.attach(c, o);
        await p.open(); await tick(); p.close();
        res[k + (r ? "-render" : "")] = c.innerHTML.indexOf('data-bv-summary="1"') >= 0;
    }}
}}
// 기능 조회가 늦게 와도 다음 렌더부터 맞는다 — 함수는 렌더할 때마다 읽는다
var flag = false;
var c2 = box();
var p2 = BV.attach(c2, {{ initial: v, load: function () {{ return Promise.resolve(v); }},
    render: {{ mode: "summary" }}, summaryView: function () {{ return flag; }} }});
await p2.open(); await tick();
var before = c2.innerHTML.indexOf('data-bv-summary="1"') >= 0;
flag = true;
await p2.open(); await tick(); p2.close();
res.late = [before, c2.innerHTML.indexOf('data-bv-summary="1"') >= 0];
out = res;
"""
    )
    assert out == {
        "fnOn": True, "fnOn-render": True, "fnOff": False, "fnOff-render": False,
        "boolOn": True, "boolOn-render": True, "absent": False, "absent-render": False,
        "late": [False, True],
    }


def test_pages_pass_capability_to_shared_render() -> None:
    """포털·관제·장애 진단이 기능 조회 값을 공용 렌더 옵션으로 넘긴다(렌더 시점 평가)."""
    app = _APP_JS.read_text(encoding="utf-8")
    assert app.count("alarmCapabilities.investigation_summary_view_enabled === true") >= 5
    assert app.count("summaryView: function () {") >= 5
    noise = _NOISE_JS.read_text(encoding="utf-8")
    assert "summaryViewOn = caps.investigation_summary_view_enabled === true;" in noise
    assert noise.count("summaryView: summaryViewNow,") == 2
    diag = _DIAG_JS.read_text(encoding="utf-8")
    # (plans/199 W5a) 진행 중 카드는 중간 결과 칸이 아니라
    # 맨 위 자기 칸(`dv-sum-box`)에 1번만 그린다
    assert 'var key = optOn(opts.summaryView) ? (tab.done ? "done" : "run") : "off";' in diag
    assert "sum.innerHTML = bv.summaryCardHtml(bv.summaryModel(partView));" in diag
    assert "summaryView: opts.summaryView," in diag


# ─── 7. 기능 조회 플래그 ────────────────────────────────────────────────


def test_capability_schema_and_config_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """코드 기본 on(사용자 결정 2026-10-29 · D-352 부기 2) · env false로 끈다."""
    # 응답 스키마 기본값은 거짓 — 라우트가 늘 명시한다(investigation_manual_enabled 관례)
    field = AlarmCapabilitiesResponse.model_fields["investigation_summary_view_enabled"]
    assert field.default is False
    assert NoiseGateConfig.model_fields["investigation_summary_view_enabled"].default is True
    monkeypatch.delenv("NOISE_INVESTIGATION_SUMMARY_VIEW_ENABLED", raising=False)
    assert NoiseGateConfig(_env_file=None).investigation_summary_view_enabled is True
    monkeypatch.setenv("NOISE_INVESTIGATION_SUMMARY_VIEW_ENABLED", "false")
    assert NoiseGateConfig(_env_file=None).investigation_summary_view_enabled is False


def test_capability_route_reflects_flag() -> None:
    # 설정에 키가 없으면 config 기본값(on)을 따른다
    body = _client().get("/api/v1/alarm/capabilities").json()
    assert body["investigation_summary_view_enabled"] is True
    body = _client(investigation_summary_view_enabled=False).get(
        "/api/v1/alarm/capabilities"
    ).json()
    assert body["investigation_summary_view_enabled"] is False
    body = _client(investigation_summary_view_enabled=True).get("/api/v1/alarm/capabilities").json()
    assert body["investigation_summary_view_enabled"] is True
    # 게이트와 무관한 표시 방식 플래그
    body = _client(enable_noise_gate=False, investigation_summary_view_enabled=True).get(
        "/api/v1/alarm/capabilities"
    ).json()
    assert body["investigation_summary_view_enabled"] is True


# ─── 8. 대기열 — 카드 칩·꼭 확인 진행 줄을 머리 대기 표시와 같은 말로 (W1 교정) ─────

#: plans/194 W5 U3 `extra.queue(view)` 반환 모양 — `{chip, note?, runningText?}`
_QUEUE = {"chip": "대기 중 · 순번 2", "note": "앞 조사가 끝나면 자동으로 시작합니다.",
          "runningText": "조사 대기 중입니다 — 15초마다 다시 확인합니다."}
_PLAIN_RUNNING = {"status": "running", "investigation_id": "inv-plain-run", "briefing_lines": []}


def _queued_views() -> list[dict[str, Any]]:
    return [_view("running_collect"), _view("running_deep"), dict(_PLAIN_RUNNING)]


def _card_of(html: str) -> str:
    at = html.find('<section class="bv-sum"')
    return "" if at < 0 else html[at : html.index("</section>", at)]


def test_queued_card_uses_queue_wording() -> None:
    out = _node(f"""
var q = {_js(_QUEUE)};
out = {_js(_queued_views())}.map(function (v) {{
    return ["summary", "detail"].map(function (mode) {{
        return BV.renderBody(v, {{ mode: mode, summaryView: true,
            queue: function () {{ return q; }} }});
    }});
}});
""")
    must = _node("out = BV.SUM_TEXT.mustQueued;")
    assert must == "조사 대기 중 — 시작되면 결과가 채워집니다"
    for per_view in out:
        for html in per_view:
            card = _card_of(html)
            assert card, html[:200]
            assert 'bv-chip--hold">대기 중 · 순번 2</span>' in card   # 머리 칩과 같은 말
            assert ">조사 중</span>" not in card
            assert must in card and "조사 진행 중 —" not in card


def test_without_queue_card_is_unchanged() -> None:
    """queue 키가 없거나 null을 주면 종전 진행 중 카드 그대로(바이트 동일)."""
    out = _node(f"""
out = {_js(_queued_views())}.map(function (v) {{
    var a = BV.renderBody(v, {{ mode: "summary", summaryView: true }});
    var b = BV.renderBody(v, {{ mode: "summary", summaryView: true,
        queue: function () {{ return null; }} }});
    return [a, b];
}});
""")
    for absent, null in out:
        assert absent == null
        card = _card_of(absent)
        assert "조사 진행 중 —" in card and "bv-chip--hold" not in card


def test_queue_with_flag_off_is_bit_identical_to_base(base_bv: Path) -> None:
    out = _node(
        f"""
var q = {_js(_QUEUE)};
var bad = [];
{_js(_queued_views())}.forEach(function (v, i) {{
    var xs = [{{ mode: "summary" }}, {{ mode: "detail" }},
        {{ mode: "summary", summaryView: false }}];
    xs.forEach(function (x, j) {{
        x.queue = function () {{ return q; }};
        if (BASE.renderBody(v, x) !== BV.renderBody(v, x)) bad.push([i, j]);
    }});
}});
out = bad;
""",
        base=base_bv,
    )
    assert out == []


def test_queued_card_has_no_handoff_buttons() -> None:
    """대기 중 카드에는 전달문 버튼이 없다 — 아직 전달할 결과가 없다(대기 문구는 그대로).

    대기가 아닌 진행 중(queue가 null)은 전달문 버튼을 그대로 둔다(§3.4).
    """
    v = _view("running_deep")
    out = _node(f"""
var container = {{ innerHTML: "", isConnected: true,
    addEventListener: function () {{}},
    contains: function () {{ return true; }}, querySelectorAll: function () {{ return []; }},
    querySelector: function () {{ return null; }} }};
var v = {_js(v)};
async function show(q) {{
    var panel = BV.attach(container, {{ initial: v, load: function () {{ return v; }},
        render: {{ mode: "summary", queue: function () {{ return q; }} }},
        summaryView: function () {{ return true; }} }});
    await panel.open();
    var html = container.innerHTML;
    panel.close();
    return html;
}}
out = {{ queued: await show({_js(_QUEUE)}), running: await show(null) }};
""")
    must = "조사 대기 중 — 시작되면 결과가 채워집니다"
    queued = _card_of(out["queued"])
    assert 'bv-chip--hold">대기 중 · 순번 2</span>' in queued and must in queued
    assert 'data-bv-action="handoff-copy"' not in out["queued"]
    assert "bv-handoff" not in out["queued"]
    # 대기가 아닌 진행 중에는 전달문 버튼이 그대로(§3.4)
    assert 'data-bv-action="handoff-copy"' in out["running"]
