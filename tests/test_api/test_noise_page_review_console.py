"""plans/162 W3 — 노이즈 관제 화면의 PAGE 재판정(`page_review` · D-330)·수집 실패 R4 반영.

겨누는 것:
    1. `/summary` — 모드 off면 `page_review` 키가 없다(종전 키 집합 그대로) ·
       shadow/enforce면 재판정
       전/후·했을 PAGE를 한 레코드 한 번만 빼서 센다 · 퍼널 항등식 `Σ terminated == raw` 유지(강등은
       원 단계로 센다 — 앞 단계 residual이 부풀지 않는다) · 단계 활성은 게이트 on AND 모드 ≠ off.
    2. 결정 추적 — 강등은 원 단계가 decided, `page_review`는 reviewed · 대표 PAGE에 묶인 알림 수.
    3. 화면(JS 순수 함수, node) — KPI·퍼널 문구 · 재판정 근거 문장 · 판단 경로 · 메타경보 문장.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_api.test_noise_dashboard_api import (
    _decision_record,
    _make_client,
    _make_config,
    _make_user_client,
    _seed_decisions,
)
from tests.test_api.test_plan153_w1_console_explain import (
    _js_function_body,
    _js_var_literal,
)

_ROOT = Path(__file__).resolve().parents[2]
_NOISE_JS = _ROOT / "src" / "static" / "js" / "noise.js"
_NOISE_HELP_JS = _ROOT / "src" / "static" / "js" / "noise-help.js"
MODES = ["admin", "user"]

_BASE_SUMMARY_KEYS = {
    "raw", "tiers", "suppress_ratio", "actionable_ratio", "stages", "range", "gate_enabled",
}


def _mode_client(mode: str, config):  # noqa: ANN001, ANN202
    if mode == "admin":
        return _make_client(config), "/api/v1/admin/noise"
    return _make_user_client(config), "/api/v1/noise"


def _config(tmp_path, mode: str, *, gate: bool = True):  # noqa: ANN001, ANN202
    config = _make_config(tmp_path, gate=gate)
    config.noise_gate.page_review_mode = mode
    return config


def _at(minutes: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=60 - minutes)).isoformat()


def _block(**over) -> dict:
    block = {"mode": "enforce", "rule": None, "would_demote": False, "keep": None}
    block.update(over)
    return block


def _enforce_records() -> list[dict]:
    """enforce 창 — 대표 PAGE p1 · 그 반복 강등 r1·r2 · 유지된 PAGE p2 · 매트릭스 TICKET t1."""
    return [
        _decision_record(alarm_id="p1", tier="page", stage="severity3", ts=_at(1),
                         reason="심각도3 — 즉시 PAGE", stage_evidence={"page_review": _block()}),
        _decision_record(
            alarm_id="r1", tier="ticket", stage="page_review", ts=_at(2),
            reason="PAGE 재판정 — 진행 중 사건 반복(최초 PAGE 09:12)",
            stage_evidence={
                "page_review": _block(rule="R1", would_demote=True, page_alarm_id="p1",
                                      last_page_alarm_id="p1", first_page_ts=_at(1),
                                      repeat_count=1),
                "original_stage": "severity3", "original_reason": "심각도3 — 즉시 PAGE",
            },
        ),
        _decision_record(
            alarm_id="r2", tier="ticket", stage="page_review", ts=_at(3),
            reason="PAGE 재판정 — 동시 발생 3대 중 대표 외",
            stage_evidence={
                "page_review": _block(rule="R3", would_demote=True, fanout_hosts=3,
                                      representative_alarm_id="p1"),
                "original_stage": "matrix", "original_reason": "매트릭스(…) → 최종 page",
            },
        ),
        _decision_record(alarm_id="p2", tier="page", stage="matrix", ts=_at(4),
                         stage_evidence={"page_review": _block(
                             rule="R2", keep="severity_rise", host_page_alarm_id="p1",
                             host_page_ts=_at(1))}),
        _decision_record(alarm_id="t1", tier="ticket", stage="matrix", ts=_at(5)),
    ]


def _shadow_records() -> list[dict]:
    """shadow 창 — 새 사건 s1 · 했을 강등 s2 · s4(R4 page) ·
    수집 실패 s3(재판정·R4 둘 다 — 한 번만 뺀다)."""
    sh = {"mode": "shadow"}
    return [
        _decision_record(alarm_id="s1", tier="page", stage="severity3", ts=_at(1),
                         stage_evidence={"page_review": _block(**sh)}),
        _decision_record(alarm_id="s2", tier="page", stage="severity3", ts=_at(2),
                         stage_evidence={"page_review": _block(
                             **sh, rule="R1", would_demote=True, page_alarm_id="s1",
                             first_page_ts=_at(1), repeat_count=2)}),
        _decision_record(
            alarm_id="s3", tier="page", stage="collection_failed", ts=_at(3),
            stage_evidence={
                "page_review": _block(**sh, rule="R2", would_demote=True,
                                      host_page_alarm_id="s1", host_page_ts=_at(1)),
                "collection_failed_sev3_only": {"mode": "shadow", "would_tier": "ticket"},
            },
        ),
        _decision_record(
            alarm_id="s4", tier="page", stage="collection_failed", ts=_at(4),
            stage_evidence={
                "page_review": _block(**sh),
                "collection_failed_sev3_only": {"mode": "shadow", "would_tier": "page"},
            },
        ),
        _decision_record(alarm_id="s5", tier="suppress", stage="flapping", ts=_at(5)),
    ]


# ─── 1. /summary ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
def test_off_mode_summary_has_no_page_review_key(tmp_path, mode):
    _seed_decisions(tmp_path, [_decision_record(alarm_id="a1", tier="page", stage="severity3")])
    for config in (_make_config(tmp_path), _config(tmp_path, "off")):
        client, base = _mode_client(mode, config)
        body = client.get(f"{base}/summary").json()
        assert set(body) == _BASE_SUMMARY_KEYS
        row = {s["stage"]: s for s in body["stages"]}["page_review"]
        assert row["enabled"] is False and row["enable_key"] == "NOISE_PAGE_REVIEW_MODE"
        assert (row["residual"], row["terminated"], row["cut"]) == (0, 0, 0)
        assert set(body["stages"][0]) == {
            "stage", "label", "residual", "terminated", "cut", "description", "enabled",
            "enable_key", "handling",
        }


@pytest.mark.parametrize("mode", MODES)
def test_enforce_summary_counts_pre_and_post_review(tmp_path, mode):
    _seed_decisions(tmp_path, _enforce_records())
    client, base = _mode_client(mode, _config(tmp_path, "enforce"))
    body = client.get(f"{base}/summary").json()
    assert body["tiers"]["page"] == 2
    assert body["page_review"] == {
        "mode": "enforce", "reviewed": 4, "pre_review_page": 4, "demoted": 2,
        "would_demote": 0, "would_demote_collection_failed": 0, "would_page": 2,
    }


@pytest.mark.parametrize("mode", MODES)
def test_shadow_summary_would_page_subtracts_each_record_once(tmp_path, mode):
    _seed_decisions(tmp_path, _shadow_records())
    client, base = _mode_client(mode, _config(tmp_path, "shadow"))
    body = client.get(f"{base}/summary").json()
    review = body["page_review"]
    assert body["tiers"]["page"] == 4
    assert review["mode"] == "shadow" and review["demoted"] == 0
    assert review["would_demote"] == 2 and review["would_demote_collection_failed"] == 1
    assert review["would_page"] == 2  # s3은 두 사유 모두지만 한 번만 뺀다
    assert review["pre_review_page"] == 4 and review["reviewed"] == 4


def test_funnel_identity_and_demoted_counted_at_original_stage(tmp_path):
    from noise_gate.domain.notification_policy import STAGE_ORDER

    _seed_decisions(tmp_path, _enforce_records())
    body = _make_client(_config(tmp_path, "enforce")).get("/api/v1/admin/noise/summary").json()
    stages = {s["stage"]: s for s in body["stages"]}
    assert sum(s["terminated"] for s in body["stages"]) == body["raw"] == 5
    assert stages["severity3"]["terminated"] == 2   # p1 + 강등 r1(원 단계)
    assert stages["matrix"]["terminated"] == 3      # p2 + t1 + 강등 r2(원 단계)
    # 강등 r1이 severity3 뒤 단계의 도달 수를 부풀리지 않는다 — matrix 도달 = matrix 종결 수
    assert stages["matrix"]["residual"] == 3
    review_row = stages["page_review"]
    assert review_row["terminated"] == 0 and review_row["cut"] == 0
    assert review_row["residual"] == 4  # 재판정을 거친 레코드 수
    assert [s["stage"] for s in body["stages"]][: len(STAGE_ORDER)] == list(STAGE_ORDER)


def test_store_funnel_keeps_identity_for_corrupt_review_record(tmp_path):
    from noise_gate.infrastructure.decision_store import DecisionStore

    # 원 단계를 못 읽는 강등 레코드는 page_review에서 종결된다 — 버리지 않고 항등식도 유지한다.
    _seed_decisions(tmp_path, [_decision_record(alarm_id="x", tier="ticket", stage="page_review",
                                                stage_evidence={"original_stage": "없는단계"})])
    funnel = DecisionStore(str(tmp_path / "decisions.jsonl")).funnel()
    row = {s["stage"]: s for s in funnel["stages"]}["page_review"]
    assert row["terminated"] == 1 and sum(s["terminated"] for s in funnel["stages"]) == 1
    assert funnel["page_review"]["demoted"] == 1


@pytest.mark.parametrize(("review_mode", "gate", "enabled"), [
    ("off", True, False), ("shadow", True, True),
    ("enforce", True, True), ("enforce", False, False),
    ("", True, False),
])
def test_page_review_stage_enabled_follows_gate_and_mode(tmp_path, review_mode, gate, enabled):
    body = _make_client(_config(tmp_path, review_mode, gate=gate)).get(
        "/api/v1/admin/noise/summary").json()
    row = {s["stage"]: s for s in body["stages"]}["page_review"]
    assert row["enabled"] is enabled and row["enable_key"] == "NOISE_PAGE_REVIEW_MODE"


# ─── 2. 결정 추적 · 묶인 알림 ─────────────────────────────────────────────


def _statuses(body: dict) -> dict[str, str]:
    return {row["stage"]: row["status"] for row in body["timeline"]}


@pytest.mark.parametrize("mode", MODES)
def test_trace_of_demoted_record_decides_original_stage(tmp_path, mode):
    _seed_decisions(tmp_path, _enforce_records())
    client, base = _mode_client(mode, _config(tmp_path, "enforce"))
    body = client.get(f"{base}/decisions/r1").json()
    statuses = _statuses(body)
    assert statuses["severity3"] == "decided"
    assert statuses["non_alarm"] == "passed"
    assert statuses["matrix"] == "short_circuited"
    assert statuses["page_review"] == "reviewed"
    assert list(statuses.values()).count("decided") == 1
    # 강등 근거의 대표 PAGE 포인터는 그대로 실린다(링크 대상)
    assert body["decision"]["stage_evidence"]["page_review"]["page_alarm_id"] == "p1"


def test_trace_of_shadow_record_marks_review_after_decision(tmp_path):
    _seed_decisions(tmp_path, _shadow_records())
    body = _make_client(_config(tmp_path, "shadow")).get("/api/v1/admin/noise/decisions/s2").json()
    statuses = _statuses(body)
    assert statuses["severity3"] == "decided" and statuses["page_review"] == "reviewed"


def test_trace_without_review_block_omits_page_review(tmp_path):
    """재판정 블록 없는 레코드는 page_review 칸이 없다 — 구 레코드·mode off 응답 비트 동일."""
    from noise_gate.domain.notification_policy import STAGE_ORDER

    _seed_decisions(tmp_path, [_decision_record(alarm_id="z", tier="page", stage="severity3")])
    body = _make_client(_make_config(tmp_path)).get("/api/v1/admin/noise/decisions/z").json()
    assert "page_review" not in _statuses(body)
    assert len(body["timeline"]) == len(STAGE_ORDER) - 1


def test_trace_with_review_block_keeps_page_review_reviewed(tmp_path):
    from noise_gate.domain.notification_policy import STAGE_ORDER

    _seed_decisions(tmp_path, _enforce_records())
    body = _make_client(_config(tmp_path, "enforce")).get(
        "/api/v1/admin/noise/decisions/p1").json()
    assert _statuses(body)["page_review"] == "reviewed"
    assert len(body["timeline"]) == len(STAGE_ORDER)


def test_js_timeline_omits_page_review_like_server(tmp_path):
    """JS `timelineFor`도 같은 규칙 — 블록 없음 = 칸 생략 · 블록 있음 = reviewed."""
    from noise_gate.domain.notification_policy import STAGE_ORDER

    records = _enforce_records()
    _seed_decisions(tmp_path, records)
    client = _make_client(_config(tmp_path, "enforce"))
    by_id = {rec["alarm_id"]: rec for rec in records}
    out = _run_node(
        "[timelineFor(" + json.dumps(by_id["t1"], ensure_ascii=False) + "),"
        " timelineFor(" + json.dumps(by_id["p1"], ensure_ascii=False) + ")]"
    )
    t1, p1 = out
    assert all(s["stage"] != "page_review" for s in t1)
    assert len(t1) == len(STAGE_ORDER) - 1
    assert {s["stage"]: s["status"] for s in p1}["page_review"] == "reviewed"
    for alarm_id, js in (("t1", t1), ("p1", p1)):
        server = client.get(f"/api/v1/admin/noise/decisions/{alarm_id}").json()["timeline"]
        assert [(s["stage"], s["status"]) for s in js] == [
            (s["stage"], s["status"]) for s in server
        ]


@pytest.mark.parametrize("mode", MODES)
def test_representative_page_carries_bundled_count(tmp_path, mode):
    _seed_decisions(tmp_path, _enforce_records())
    client, base = _mode_client(mode, _config(tmp_path, "enforce"))
    traced = client.get(f"{base}/decisions/p1").json()["decision"]
    assert traced["page_review_bundled"] == 2  # r1(R1 최초 PAGE) + r2(R3 대표)
    # p2는 p1을 가리키지만 유지(would_demote 아님)라 묶이지 않는다 — p1 수에 안 들어간다
    items = client.get(f"{base}/decisions?tier=page").json()["items"]
    listed = {it["alarm_id"]: it for it in items}
    assert listed["p1"]["page_review_bundled"] == 2
    assert "page_review_bundled" not in listed["p2"]
    for other in ("r1", "t1"):
        other_decision = client.get(f"{base}/decisions/{other}").json()["decision"]
        assert "page_review_bundled" not in other_decision


def test_shadow_would_demote_also_counts_as_bundled(tmp_path):
    _seed_decisions(tmp_path, _shadow_records())
    traced = _make_client(_config(tmp_path, "shadow")).get(
        "/api/v1/admin/noise/decisions/s1").json()["decision"]
    assert traced["page_review_bundled"] == 2  # s2(R1) + s3(R2)


# ─── 3. 화면 ─────────────────────────────────────────────────────────────


def test_both_pages_have_page_kpi_sub_line():
    for page in ("src/static/noise.html", "src/static/admin/noise.html"):
        html = (_ROOT / page).read_text(encoding="utf-8")
        assert 'id="kpiPageSub"' in html, page
    assert 'getElementById("kpiPageSub")' in _NOISE_JS.read_text(encoding="utf-8")


def test_help_keys_for_page_review_exist_in_settings_catalog():
    import re

    from src.api.settings_catalog import field_index

    help_src = _NOISE_HELP_JS.read_text(encoding="utf-8")
    keys = set(re.findall(r'"(NOISE_(?:PAGE_REVIEW|COLLECTION_FAILED|META_ALERT_COLLECTION)\w*)"',
                          help_src))
    assert "NOISE_PAGE_REVIEW_MODE" in keys and "NOISE_COLLECTION_FAILED_PAGE_SEV3_ONLY" in keys
    assert "NOISE_META_ALERT_COLLECTION_FAILED_RATIO" in keys
    assert keys - set(field_index()) == set()


_JS_FUNCTIONS = (
    "fillTemplate", "fmtInt", "fmtPct", "fmtTime", "pad2", "fmtShortTime", "fmtDuration",
    "evidenceOf", "evidenceLookup", "evidenceValue", "originalTier", "tierShift", "stageLabel",
    "reasonSummary", "crossSource", "reviewBlock", "reviewBasis", "reviewStepNote",
    "pageReviewKpiText", "pageReviewRowText", "timelineFor", "metaAlertText",
)


def _run_node(expr: str, stages: list[str] | None = None) -> object:
    """noise-help.js + noise.js 순수 함수로 식 하나를 계산한다."""
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 순수 함수 실행 테스트 생략")
    from noise_gate.domain.notification_policy import STAGE_LABELS, STAGE_ORDER

    src = _NOISE_JS.read_text(encoding="utf-8")
    order = stages or list(STAGE_ORDER)
    state_stages = [{"stage": k, "label": STAGE_LABELS.get(k, k)} for k in order]
    script = "\n".join([
        "var window = {};",
        _NOISE_HELP_JS.read_text(encoding="utf-8"),
        "var HELP = window.NOISE_HELP;",
        _js_var_literal(src, "TIER_NAMES"),
        _js_var_literal(src, "TIER_RANK"),
        _js_var_literal(src, "EVIDENCE_VALUE_WORDS"),
        f"var state = {{ stages: {json.dumps(state_stages, ensure_ascii=False)} }};",
        *(_js_function_body(src, name) for name in _JS_FUNCTIONS),
        f"process.stdout.write(JSON.stringify({expr}));",
    ])
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_kpi_and_funnel_texts_by_mode():
    enforce = {"mode": "enforce", "reviewed": 4, "pre_review_page": 4, "demoted": 2,
               "would_demote": 0, "would_page": 2}
    shadow = {"mode": "shadow", "reviewed": 4, "pre_review_page": 4, "demoted": 0,
              "would_demote": 2, "would_page": 2}
    out = _run_node(
        f"[pageReviewKpiText(null), pageReviewKpiText({json.dumps(enforce)}),"
        f" pageReviewKpiText({json.dumps(shadow)}), pageReviewRowText({json.dumps(enforce)}),"
        f" pageReviewRowText({json.dumps(shadow)}), pageReviewRowText(null)]"
    )
    assert out == [
        "즉시 통보",
        "재판정 후 · 재판정 전 4(강등 2)",
        "즉시 통보 · 했을 PAGE 2(기록만)",
        "강등 2 / 재판정 4",
        "했을 강등 2 / 재판정 4",
        "",
    ]


def test_review_basis_sentences_do_not_carry_alarm_ids():
    records = {rec["alarm_id"]: rec for rec in _enforce_records() + _shadow_records()}
    out = _run_node(
        "(function (r) { var o = {}; Object.keys(r).forEach(function (k) {"
        " o[k] = [reviewBasis(r[k]), reviewStepNote(r[k])]; }); return o; })("
        + json.dumps(records, ensure_ascii=False) + ")"
    )
    assert out["r1"][0].startswith("진행 중 사건 반복 — 최초 PAGE ")
    assert out["r1"][0].endswith(" · 반복 1회 → TICKET으로 강등")
    assert out["r1"][1] == "재판정: TICKET으로 강등"
    assert out["r2"][0] == "동시 발생 3대 중 대표 외 → TICKET으로 강등"
    assert out["p2"][0].startswith("같은 서버의 다른 사건 진행 중 — 먼저 통보 ")
    assert out["p2"][0].endswith("→ PAGE 유지(심각도 상승)")
    assert out["p1"] == ["새 사건의 첫 PAGE → PAGE 유지", "재판정: PAGE 유지"]
    assert out["s2"][0].endswith("→ 기록만: 강제였다면 TICKET으로 강등")
    assert out["s2"][1] == "재판정(기록만): 강등했을 판단"
    assert out["t1"] == ["", "재판정: PAGE 유지"]  # 근거 없는 레코드는 문장 없음
    for basis, _ in out.values():
        assert "p1" not in basis and "s1" not in basis


def test_demoted_summary_and_timeline_in_js_match_server(tmp_path):
    records = _enforce_records()
    _seed_decisions(tmp_path, records)
    server = _make_client(_config(tmp_path, "enforce")).get(
        "/api/v1/admin/noise/decisions/r1").json()
    out = _run_node(
        "[reasonSummary("
        + json.dumps({**records[1], "stage_label": "PAGE 재판정"}, ensure_ascii=False)
        + "), timelineFor("
        + json.dumps(records[1], ensure_ascii=False)
        + ")]"
    )
    assert out[0].startswith("이 알람은 「PAGE 재판정」 단계에서 PAGE → TICKET로 강등됐습니다.")
    assert [(s["stage"], s["status"]) for s in out[1]] == [
        (s["stage"], s["status"]) for s in server["timeline"]
    ]


def test_shadow_summary_adds_would_demote_sentence():
    rec = {**_shadow_records()[1], "stage_label": "심각도3 단락"}
    out = _run_node("reasonSummary(" + json.dumps(rec, ensure_ascii=False) + ")")
    assert out.endswith(
        "PAGE 재판정을 강제로 켰다면 TICKET으로 내렸을 판단입니다(R1 진행 중 사건 반복)."
    )


def test_collection_failed_meta_alert_text():
    alert = {"type": "collection_failed_ratio", "value": 0.125, "threshold": 0.05,
             "window_seconds": 3600, "count": 30, "total": 240}
    out = _run_node(
        "[metaAlertText(" + json.dumps(alert) + "), metaAlertText({type: 'new_kind'})]"
    )
    assert out == ["수집 실패율 12.5%(30/240건) — 임계 5% 초과(창 60분)", "new_kind"]
