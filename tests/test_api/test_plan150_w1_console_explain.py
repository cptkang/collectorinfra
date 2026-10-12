"""plans/150 W1 — 관제 설명(R-3): 처리 안내 · AI 추정 응답 · 드로어 사유 요약 · 강등 필터.

세 갈래를 겨눈다:
    1. API — 판정 레코드의 `analysis`는 사용자 경로에서 마스킹·운영자 경로에서 원문이다.
       `condition_log`·`created_by`의 운영자 전용(fail-closed)은 그대로다
       (`summary.stages[].handling` 정본 대조는 `test_noise_dashboard_api`).
    2. JS 순수 함수 — `noise.js`의 템플릿 채우기·원래 티어·사유 요약·처리 안내를 node로
       실제 실행한다(문구 정본 `noise-help.js`를 함께 싣는다). 계획서 §4.8 예시 3종
       (크로스소스 강등 · 인히비션 ·
       묶기만)이 근거 값으로 채워지는지, 근거가 없는 옛 레코드에서 문장이 깨지지 않는지 본다.
    3. JS 정적 — SSE 피드·재동기화가 분석 필드를 버리지 않고, KPI 프로파일에 새 열이 기본으로
       실리고, 새 렌더 함수가 innerHTML로 값을 넣지 않는다(텍스트는 `el()` → textContent 경로).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_api.test_noise_dashboard_api import (
    _decision_record,
    _js_function_body,
    _make_client,
    _make_config,
    _make_user_client,
    _seed_decisions,
)

_ROOT = Path(__file__).resolve().parents[2]
_NOISE_JS = _ROOT / "src" / "static" / "js" / "noise.js"
_NOISE_HELP_JS = _ROOT / "src" / "static" / "js" / "noise-help.js"

MODES = ["admin", "user"]
SECRET = "sk-abcdefghijklmnopqrstuvwxyz012345"


def _mode_client(mode: str, config):  # noqa: ANN001, ANN202
    if mode == "admin":
        return _make_client(config), "/api/v1/admin/noise"
    return _make_user_client(config), "/api/v1/noise"


# ─── 1. API ──────────────────────────────────────────────────────────────


def _analysis_record(**over) -> dict:
    return _decision_record(
        alarm_id="an1",
        analysis={
            "summary": SECRET,
            "probable_cause": "배치 작업으로 CPU 포화 추정",
            "recommended_action": "상위 프로세스 확인",
            "failed": False,
        },
        condition_log="CPU 97% > 90%",
        stage_evidence={"inhibitor": "CPU", "created_by": "adm"},
        **over,
    )


@pytest.mark.parametrize("mode", MODES)
def test_analysis_masked_on_user_path_raw_on_operator_path(tmp_path, mode):
    _seed_decisions(tmp_path, [_analysis_record()])
    client, base = _mode_client(mode, _make_config(tmp_path))
    listed = client.get(f"{base}/decisions").json()["items"][0]
    traced = client.get(f"{base}/decisions/an1").json()["decision"]
    for item in (listed, traced):
        analysis = item["analysis"]
        assert analysis["probable_cause"] == "배치 작업으로 CPU 포화 추정"
        assert analysis["failed"] is False  # 불리언은 마스킹 대상이 아니다
        if mode == "admin":
            assert analysis["summary"] == SECRET  # 운영자 경로는 원문
            assert item["condition_log"] == "CPU 97% > 90%"
            assert item["stage_evidence"]["created_by"] == "adm"
        else:
            assert analysis["summary"] == "***"
            # 운영자 전용 키 fail-closed 유지(D-247)
            assert "condition_log" not in item
            assert "created_by" not in item["stage_evidence"]


@pytest.mark.parametrize("mode", MODES)
def test_record_without_analysis_has_no_key(tmp_path, mode):
    _seed_decisions(tmp_path, [_decision_record()])
    client, base = _mode_client(mode, _make_config(tmp_path))
    item = client.get(f"{base}/decisions").json()["items"][0]
    assert "analysis" not in item


def test_user_view_masking_does_not_mutate_tail_record():
    # 사용자 뷰가 원 레코드(related 해석에도 쓰는 tail)를 건드리지 않는다.
    from noise_gate.infrastructure.decision_store import DecisionStore

    store = DecisionStore("/nonexistent/never-written.jsonl")
    rec = _analysis_record()
    view = store._view(rec, lambda s: "***" if s == SECRET else s, operator_view=False)
    assert view["analysis"]["summary"] == "***"
    assert rec["analysis"]["summary"] == SECRET


# ─── 2. JS 순수 함수(node 실행) ─────────────────────────────────────────

_JS_FUNCTIONS = (
    "fillTemplate", "originalTier", "tierShift", "evidenceLookup", "reasonSummary",
    "handlingText", "handlingShort", "evidenceOf", "crossSource", "evidenceValue", "fmtDuration",
    "fmtTime", "causeShort", "analysisOf", "analysisFailed", "stageLabel", "stageMeta",
)


def _js_var_literal(src: str, name: str) -> str:
    """`var NAME = { … };` 객체 리터럴 선언을 통째로 잘라낸다(중괄호 짝 맞춤)."""
    start = src.index(f"var {name} = {{")
    depth = 0
    for index in range(src.index("{", start), len(src)):
        if src[index] == "{":
            depth += 1
        elif src[index] == "}":
            depth -= 1
            if depth == 0:
                return src[start : index + 1] + ";"
    raise AssertionError(f"{name} 리터럴을 찾지 못했다")


def _run_node(cases: dict[str, dict]) -> dict[str, dict]:
    """noise-help.js + noise.js 순수 함수 묶음으로 케이스별 산출을 계산한다."""
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 순수 함수 실행 테스트 생략")
    from noise_gate.domain.notification_policy import STAGE_LABELS
    from noise_gate.domain.stage_handling import STAGE_HANDLING

    src = _NOISE_JS.read_text(encoding="utf-8")
    stages = [
        {"stage": key, "label": STAGE_LABELS.get(key, key), "handling": table}
        for key, table in STAGE_HANDLING.items()
    ]
    script = "\n".join([
        "var window = {};",
        _NOISE_HELP_JS.read_text(encoding="utf-8"),
        "var HELP = window.NOISE_HELP;",
        _js_var_literal(src, "TIER_NAMES"),
        _js_var_literal(src, "TIER_RANK"),
        _js_var_literal(src, "EVIDENCE_VALUE_WORDS"),
        "var CAUSE_SHORT_MAX = 80;",
        f"var state = {{ stages: {json.dumps(stages, ensure_ascii=False)} }};",
        *(_js_function_body(src, name) for name in _JS_FUNCTIONS),
        f"var cases = {json.dumps(cases, ensure_ascii=False)};",
        "var out = {};",
        "Object.keys(cases).forEach(function (k) { var it = cases[k]; out[k] = {",
        "  summary: reasonSummary(it), handling: handlingText(it), short: handlingShort(it),",
        "  shift: tierShift(it), from: originalTier(it), cause: causeShort(it),",
        "  failed: analysisFailed(it) }; });",
        "process.stdout.write(JSON.stringify(out));",
    ])
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


_CROSS_SOURCE_DEMOTED = {
    "stage": "cross_source", "stage_label": "크로스소스 사건 상관", "tier": "dashboard",
    "stage_evidence": {
        "capped_from": "page", "episode_id": "ep-1",
        "cross_source": {
            "rule_id": "host_resource_to_was_latency", "cause_alarm_id": "P150-CPU-1",
            "host_key": "gpapp01", "lag_seconds": 270, "action": "demote", "enforce": True,
            "missing": [], "mode": "enforce", "applied": True,
        },
    },
}
_INHIBITION = {
    "stage": "inhibition", "stage_label": "인히비션", "tier": "suppress",
    "stage_evidence": {"inhibitor": "CPU 사용률", "inhibitor_severity": 3,
                       "age_seconds": 120, "window_seconds": 300},
}
_LINKED = {
    "stage": "matrix", "stage_label": "우선순위 매트릭스", "tier": "ticket",
    "stage_evidence": {
        "base_tier": "ticket", "promote": [], "demote": [],
        "cross_source": {
            "rule_id": "r1", "cause_alarm_id": "P-1", "host_key": "gpapp01", "lag_seconds": 30,
            "action": "link", "enforce": False, "missing": ["host_key_strength"],
            "mode": "enforce", "applied": False,
        },
    },
}


def test_reason_summary_examples_from_plan():
    out = _run_node({
        "cs": _CROSS_SOURCE_DEMOTED, "inh": _INHIBITION, "link": _LINKED,
    })
    assert out["cs"]["summary"] == (
        "이 알람은 「크로스소스 사건 상관」 단계에서 PAGE → DASHBOARD로 강등됐습니다. "
        "같은 호스트(gpapp01)의 원인 알람 P150-CPU-1이 통보된 채 해소되지 않아 원인으로 "
        "판단됐습니다. 이 알람은 그보다 4분 30초 늦게 발생했습니다. "
        "적용 규칙: host_resource_to_was_latency."
    )
    assert out["inh"]["summary"] == (
        "이 알람은 「인히비션」 단계에서 SUPPRESS로 판단됐습니다. 같은 서버의 상위 알람 "
        "「CPU 사용률」(심각도 3, 2분 전)이 진행 중이라 이 알람은 음소거됐습니다."
    )
    assert out["link"]["summary"].endswith(
        "같은 사건으로 묶였지만 강등하지 않았습니다 — 빠진 조건: 호스트 정합 약함."
    )


def test_old_record_without_evidence_keeps_sentences_whole():
    # 근거가 없는 옛 레코드 — 선택 구간이 빠지고 `{`·`[`·undefined가 새지 않는다.
    out = _run_node({
        "inh": {"stage": "inhibition", "tier": "suppress"},
        "cs": {"stage": "cross_source", "tier": "dashboard"},
        "storm": {"stage": "storm", "tier": "suppress", "stage_evidence": {}},
        "unknown": {"stage": "unknown", "stage_label": "unknown", "tier": "suppress"},
    })
    for key, row in out.items():
        for text in (row["summary"], row["handling"]):
            assert text and not re.search(r"[{}\[\]]|undefined|null", text), (key, text)
    assert out["inh"]["handling"].startswith("같은 서버의 상위 알람을 먼저 처리하세요.")
    assert out["unknown"]["summary"].startswith("최종 판단은 SUPPRESS입니다.")
    assert "「unknown」" not in out["unknown"]["summary"]


def test_handling_picks_tier_row_and_fills_evidence():
    out = _run_node({
        "dep_dash": {"stage": "dependency", "tier": "dashboard",
                     "stage_evidence": {"mode": "multi_hop", "root_resource_name": "SW-CORE"}},
        "dep_sup": {"stage": "dependency", "tier": "suppress",
                    "stage_evidence": {"mode": "one_hop"}},
        "matrix_page": {"stage": "matrix", "tier": "page",
                        "stage_evidence": {"base_tier": "ticket", "promote": ["변경 근접(원인성)"],
                                           "demote": []}},
        "cs": _CROSS_SOURCE_DEMOTED,
    })
    assert out["dep_dash"]["handling"].startswith("상위 자원 SW-CORE 상태를 먼저 확인하세요")
    assert out["dep_sup"]["handling"].startswith("상위 자원 장애를 먼저 처리하세요")
    assert out["matrix_page"]["handling"] == "즉시 확인 대상입니다."
    assert out["matrix_page"]["shift"] == 1  # 기본 티어 TICKET → PAGE 승격
    assert out["cs"]["handling"].startswith("원인 알람 P150-CPU-1을 먼저 처리하세요.")
    assert out["cs"]["short"] == "원인 알람 P150-CPU-1을 먼저 처리하세요"
    assert out["dep_sup"]["short"] == "상위 자원 장애를 먼저 처리하세요"


def test_tier_shift_uses_recorded_original_tier_only():
    out = _run_node({
        "capped": _CROSS_SOURCE_DEMOTED,
        "matrix_demoted": {"stage": "matrix", "tier": "dashboard",
                           "stage_evidence": {"base_tier": "ticket"}},
        "matrix_same": {"stage": "matrix", "tier": "ticket",
                        "stage_evidence": {"base_tier": "ticket"}},
        # base_tier는 매트릭스 단계 판단에서만 원래 티어로 본다
        "suppress_stage": {"stage": "storm", "tier": "suppress",
                           "stage_evidence": {"base_tier": "page"}},
        "none": {"stage": "flapping", "tier": "suppress"},
    })
    assert (out["capped"]["shift"], out["capped"]["from"]) == (-1, "page")
    assert out["matrix_demoted"]["shift"] == -1
    assert out["matrix_same"]["shift"] == 0
    assert (out["suppress_stage"]["shift"], out["suppress_stage"]["from"]) == (0, "")
    assert out["none"]["shift"] == 0


def test_cause_short_truncates_and_reports_failure():
    long_cause = "가" * 120
    out = _run_node({
        "ok": {"stage": "matrix", "tier": "ticket",
               "analysis": {"summary": "s", "probable_cause": long_cause,
                            "recommended_action": "r", "failed": False}},
        "failed": {"stage": "matrix", "tier": "ticket",
                   "analysis": {"summary": "", "probable_cause": "", "recommended_action": "",
                                "failed": True}},
        "legacy_failed": {"stage": "matrix", "tier": "ticket",
                          "stage_evidence": {"analysis": "AI 분석 실패 — 원문 알람으로 판단"}},
        "absent": {"stage": "matrix", "tier": "ticket"},
    })
    assert out["ok"]["cause"] == "가" * 80 + "…"
    assert out["failed"]["cause"] == "AI 분석 실패"
    assert out["failed"]["failed"] is True
    assert out["legacy_failed"]["failed"] is True and out["legacy_failed"]["cause"] == ""
    assert out["absent"]["failed"] is False and out["absent"]["cause"] == ""


# ─── 3. JS 정적 ──────────────────────────────────────────────────────────


def _src() -> str:
    return _NOISE_JS.read_text(encoding="utf-8")


def test_stream_and_resync_keep_analysis_fields():
    src = _src()
    on_message = _js_function_body(src, "onStreamMessage")
    assert "analysis: streamAnalysis(payload)" in on_message
    stream_analysis = _js_function_body(src, "streamAnalysis")
    for key in ("summary", "probable_cause", "recommended_action"):
        assert f"payload.{key}" in stream_analysis
    assert "analysis: item.analysis" in _js_function_body(src, "resyncFeed")
    assert "causeShort(item)" in _js_function_body(src, "renderFeed")


def test_suppress_and_dashboard_profiles_include_new_columns():
    src = _src()
    profiles = _js_var_literal(src, "PROFILES")
    for name in ("suppress", "dashboard"):
        block = re.search(name + r": \{.*?cols: \[(.*?)\]", profiles, re.S)
        assert block, name
        cols = block.group(1)
        assert '"cause"' in cols and '"handling"' in cols, name
    cols_literal = _js_var_literal(src, "COLS")
    assert re.search(r"cause: \{.*?render: causeShort", cols_literal, re.S)
    assert re.search(r"handling: \{.*?render: handlingShort", cols_literal, re.S)


def test_drawer_cause_section_order_and_texts_come_from_help():
    src = _src()
    render = _js_function_body(src, "renderCause")
    # ⓐ 처리 안내가 ⓑ AI 추정보다 먼저 그려진다(G-8 — 순서로 우선순위를 드러낸다)
    assert render.index("handlingText(item)") < render.index("analysisOf(item)")
    for key in ("handlingHead", "handlingBadge", "aiHead", "aiBadge", "aiLead", "aiNotStored",
                "aiFailed"):
        assert f"C.{key}" in render, key
    # ⓒ 조사 브리핑은 이 Wave에서 그리지 않는다(W3 몫)
    assert "investigation" not in render and "briefing" not in render
    help_src = _NOISE_HELP_JS.read_text(encoding="utf-8")
    assert "아래 AI 추정은 노이즈 판정 전에 알람 문구만으로 만든 것입니다" in help_src
    assert "이 판단이 기록될 때는 AI 추정을 저장하지 않았습니다" in help_src


@pytest.mark.parametrize("name", ["renderCause", "tierShiftChip", "demotedToggle", "reasonSummary"])
def test_new_renderers_insert_text_without_html(name):
    body = _js_function_body(_src(), name)
    assert not re.search(r"innerHTML\s*=(?!\s*\"\")", body)  # 비우기("") 외 innerHTML 대입 금지
    assert "insertAdjacentHTML" not in body


def test_evidence_label_for_analysis_marker():
    help_src = _NOISE_HELP_JS.read_text(encoding="utf-8")
    evidence = help_src[help_src.index("evidence: {"):help_src.index("summary: {")]
    assert re.search(r"^\s*analysis: \"AI 분석\",", evidence, re.M)


def test_demoted_filter_is_client_side_and_scoped_to_page():
    src = _src()
    table = _js_function_body(src, "renderPanelTable")
    assert "ps.demotedOnly" in table and "tierShift(it) < 0" in table
    # 서버 파라미터를 늘리지 않는다(D-245 — 응답 가산만)
    assert "demoted" not in _js_function_body(src, "panelParams")
