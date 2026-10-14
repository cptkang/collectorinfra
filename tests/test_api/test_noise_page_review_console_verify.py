"""plans/162 W3 독립 검증 — 노이즈 관제 PAGE 재판정(`page_review` · D-330) 화면·API.

구현자 테스트(`test_noise_page_review_console.py`)와 겹치지 않게 다음을 본다:
    1. 퍼널 항등식 — enforce·shadow·비재판정 레코드를 섞어도, 강등 레코드를 「재판정이 없었다면」
       (원 단계에서 PAGE로 종결)로 되돌린 기준 퍼널과 page_review 행 밖의 모든 칸이 같다.
    2. `response_model_exclude_unset` — 단계 칸의 기본값 필드가 빠지지 않는다(모든 모드·게이트 off).
    3. 마스킹·권한 — 근거 안 원 사유는 사용자 경로에서 가리고, 운영자 전용 근거 키는 재판정
       블록이 있어도 사용자 경로에 싣지 않는다. 대표 PAGE 포인터 마스킹 상호작용.
    4. 수집 실패율 메타경보 — 세 모집단·초과(>) 경계·임계 0 = 끔, 두 경로 동일.
    5. 화면 — watchNote(innerHTML)에 들어가는 메타경보 문장에 HTML이 섞이지 않는다 · 재판정
       문장은 innerHTML로 쓰지 않는다.
    6. 문서 — docs/28 §4-C의 `.env` 줄(인라인 주석 없음 · 키 = NoiseGateConfig 필드).
"""

from __future__ import annotations

import json
import re
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
from tests.test_api.test_noise_page_review_console import _run_node

_ROOT = Path(__file__).resolve().parents[2]
_NOISE_JS = _ROOT / "src" / "static" / "js" / "noise.js"
_DOC28 = _ROOT / "docs" / "28_noise_cancellation_operator_guide.md"
MODES = ["admin", "user"]
_STAGE_KEYS = {
    "stage", "label", "residual", "terminated", "cut", "description", "enabled",
    "enable_key", "handling",
}


def _client(mode: str, config):  # noqa: ANN001, ANN202
    if mode == "admin":
        return _make_client(config), "/api/v1/admin/noise"
    return _make_user_client(config), "/api/v1/noise"


def _config(tmp_path, review_mode: str = "off", *, gate: bool = True, cf_ratio=None):  # noqa: ANN001, ANN202
    config = _make_config(tmp_path, gate=gate)
    config.noise_gate.page_review_mode = review_mode
    if cf_ratio is not None:
        config.noise_gate.meta_alert_collection_failed_ratio = cf_ratio
    return config


def _at(minutes_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


def _blk(mode: str, **over) -> dict:
    block = {"mode": mode, "rule": None, "would_demote": False, "keep": None}
    block.update(over)
    return block


def _demoted(alarm_id: str, original_stage: str, pointer: dict, *, ago: int) -> dict:
    return _decision_record(
        alarm_id=alarm_id, tier="ticket", stage="page_review", ts=_at(ago),
        reason="PAGE 재판정 — 진행 중 사건 반복",
        stage_evidence={
            "page_review": _blk("enforce", rule="R1", would_demote=True, **pointer),
            "original_stage": original_stage,
            "original_reason": f"{original_stage} 원 사유",
        },
    )


def _mixed_records() -> list[dict]:
    """enforce 강등(원 단계 3종) · shadow 했을 강등 · 유지 PAGE · 비재판정 레코드 혼합."""
    return [
        _decision_record(alarm_id="p1", tier="page", stage="severity3", ts=_at(50),
                         stage_evidence={"page_review": _blk("enforce")}),
        _demoted("d1", "severity3", {"page_alarm_id": "p1"}, ago=49),
        _demoted("d2", "collection_failed", {"host_page_alarm_id": "p1"}, ago=48),
        _demoted("d3", "matrix", {"representative_alarm_id": "p1"}, ago=47),
        _decision_record(alarm_id="s1", tier="page", stage="matrix", ts=_at(46),
                         stage_evidence={"page_review": _blk(
                             "shadow", rule="R2", would_demote=True, host_page_alarm_id="p1")}),
        _decision_record(alarm_id="f1", tier="suppress", stage="flapping", ts=_at(45)),
        _decision_record(alarm_id="m1", tier="dashboard", stage="maintenance", ts=_at(44)),
        _decision_record(alarm_id="c1", tier="dashboard", stage="correlation", ts=_at(43)),
        _decision_record(alarm_id="t1", tier="ticket", stage="matrix", ts=_at(42)),
        _decision_record(alarm_id="cf", tier="page", stage="collection_failed", ts=_at(41)),
    ]


def _as_if_not_reviewed(records: list[dict]) -> list[dict]:
    """강등 레코드를 「재판정이 없었다면」 모양(원 단계·PAGE·블록 없음)으로 되돌린다."""
    out = []
    for rec in records:
        rec = json.loads(json.dumps(rec))
        evidence = rec.get("stage_evidence") or {}
        if rec.get("stage") == "page_review":
            rec["stage"] = evidence["original_stage"]
            rec["tier"] = "page"
        rec.pop("stage_evidence", None)
        out.append(rec)
    return out


# ─── 1. 퍼널 항등식 ──────────────────────────────────────────────────────


def test_funnel_rows_equal_pre_review_baseline_except_page_review(tmp_path):
    from noise_gate.infrastructure.decision_store import DecisionStore

    records = _mixed_records()
    reviewed_dir = tmp_path / "reviewed"
    base_dir = tmp_path / "base"
    reviewed_dir.mkdir()
    base_dir.mkdir()
    _seed_decisions(reviewed_dir, records)
    _seed_decisions(base_dir, _as_if_not_reviewed(records))

    got = DecisionStore(str(reviewed_dir / "decisions.jsonl")).funnel()
    want = DecisionStore(str(base_dir / "decisions.jsonl")).funnel()

    assert got["raw"] == want["raw"] == len(records)
    assert sum(s["terminated"] for s in got["stages"]) == got["raw"]
    rows = {s["stage"]: s for s in got["stages"]}
    base_rows = {s["stage"]: s for s in want["stages"]}
    for stage, row in rows.items():
        if stage == "page_review":
            continue
        # 앞 단계 residual·terminated·cut이 강등 건으로 부풀거나 줄지 않는다
        assert (row["residual"], row["terminated"], row["cut"]) == (
            base_rows[stage]["residual"], base_rows[stage]["terminated"], base_rows[stage]["cut"]
        ), stage
    assert rows["page_review"]["terminated"] == 0 and rows["page_review"]["cut"] == 0
    assert rows["page_review"]["residual"] == 5  # 블록 보유: p1·d1·d2·d3·s1
    review = got["page_review"]
    assert review["demoted"] == 3 and review["would_demote"] == 1
    assert review["pre_review_page"] == got["tiers"]["page"] + 3
    # 티어 분포만 강등만큼 다르다(PAGE → TICKET)
    assert got["tiers"]["page"] == want["tiers"]["page"] - 3
    assert got["tiers"]["ticket"] == want["tiers"]["ticket"] + 3


def test_page_review_row_residual_not_below_terminated_for_corrupt_record(tmp_path):
    from noise_gate.infrastructure.decision_store import DecisionStore

    _seed_decisions(tmp_path, [_decision_record(alarm_id="x", tier="ticket", stage="page_review")])
    funnel = DecisionStore(str(tmp_path / "decisions.jsonl")).funnel()
    row = {s["stage"]: s for s in funnel["stages"]}["page_review"]
    assert row["residual"] >= row["terminated"]


# ─── 2. exclude_unset ───────────────────────────────────────────────────


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(("review_mode", "gate"), [
    ("off", True), ("shadow", True), ("enforce", True), ("enforce", False), ("off", False),
])
def test_summary_exclude_unset_keeps_every_stage_field(tmp_path, mode, review_mode, gate):
    _seed_decisions(tmp_path, _mixed_records())
    client, base = _client(mode, _config(tmp_path, review_mode, gate=gate))
    body = client.get(f"{base}/summary").json()
    for row in body["stages"]:
        assert set(row) == _STAGE_KEYS, row["stage"]
    expected = {"raw", "tiers", "suppress_ratio", "actionable_ratio", "stages", "range",
                "gate_enabled"}
    if review_mode != "off":
        expected.add("page_review")
        assert set(body["page_review"]) == {
            "mode", "reviewed", "pre_review_page", "demoted", "would_demote",
            "would_demote_collection_failed", "would_page",
        }
    assert set(body) == expected
    assert set(body["tiers"]) == {"page", "ticket", "dashboard", "suppress"}


def test_summary_page_review_is_same_on_user_and_operator_paths(tmp_path):
    _seed_decisions(tmp_path, _mixed_records())
    config = _config(tmp_path, "enforce")
    admin = _make_client(config).get("/api/v1/admin/noise/summary").json()
    user = _make_user_client(config).get("/api/v1/noise/summary").json()
    assert admin == user


# ─── 3. 마스킹 · 권한 ────────────────────────────────────────────────────

_SECRET = "sk-" + "A" * 24


def test_user_path_masks_original_reason_and_drops_operator_evidence(tmp_path):
    rec = _demoted("d1", "severity3", {"page_alarm_id": "p1"}, ago=10)
    rec["reason"] = _SECRET
    rec["stage_evidence"]["original_reason"] = _SECRET
    rec["stage_evidence"]["created_by"] = "ops-admin"
    _seed_decisions(tmp_path, [rec])
    config = _config(tmp_path, "enforce")

    user = _make_user_client(config).get("/api/v1/noise/decisions/d1").json()["decision"]
    assert user["reason"] == "***"
    assert user["stage_evidence"]["original_reason"] == "***"
    assert "created_by" not in user["stage_evidence"]
    assert user["stage_evidence"]["page_review"]["page_alarm_id"] == "p1"

    listed = _make_user_client(config).get("/api/v1/noise/decisions").json()["items"][0]
    assert listed["stage_evidence"]["original_reason"] == "***"
    assert "created_by" not in listed["stage_evidence"]

    # 결정 조회의 값 패턴 마스킹은 두 경로 공통이다 — 운영자 경로는 운영자 전용 키만 더 싣는다
    admin = _make_client(config).get("/api/v1/admin/noise/decisions/d1").json()["decision"]
    assert admin["stage_evidence"]["original_reason"] == "***"
    assert admin["stage_evidence"]["created_by"] == "ops-admin"


@pytest.mark.xfail(strict=True, reason=(
    "Minor: 근거 전체에 값 패턴 마스킹이 걸려 대표 PAGE 포인터(alarm_id)가 카드번호 패턴 등과 "
    "맞으면 사용자 경로에서 '***'가 된다 — 최상위 alarm_id는 가리지 않으므로 링크가 404"
))
def test_user_path_pointer_matches_representative_alarm_id(tmp_path):
    rep_id = "4111111111111"  # 13자리 · Visa 패턴과 일치하는 숫자형 알람 ID
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id=rep_id, tier="page", stage="severity3", ts=_at(20),
                         stage_evidence={"page_review": _blk("enforce")}),
        _demoted("d1", "severity3", {"page_alarm_id": rep_id}, ago=10),
    ])
    client = _make_user_client(_config(tmp_path, "enforce"))
    rep = client.get(f"/api/v1/noise/decisions/{rep_id}").json()["decision"]
    demoted = client.get("/api/v1/noise/decisions/d1").json()["decision"]
    assert demoted["stage_evidence"]["page_review"]["page_alarm_id"] == rep["alarm_id"]


@pytest.mark.parametrize("mode", MODES)
def test_stage_filter_page_review_lists_only_actual_demotions(tmp_path, mode):
    """퍼널 「PAGE 재판정」 행 클릭(= stage 필터)은 실제 강등만 연다 — shadow 했을 강등은 빠진다."""
    _seed_decisions(tmp_path, _mixed_records())
    client, base = _client(mode, _config(tmp_path, "enforce"))
    body = client.get(f"{base}/decisions?stage=page_review").json()
    assert {it["alarm_id"] for it in body["items"]} == {"d1", "d2", "d3"}
    assert body["total"] == 3


def test_bundled_count_is_scoped_to_page_records_and_both_paths(tmp_path):
    _seed_decisions(tmp_path, _mixed_records())
    config = _config(tmp_path, "enforce")
    for client, base in ((_make_client(config), "/api/v1/admin/noise"),
                         (_make_user_client(config), "/api/v1/noise")):
        p1 = client.get(f"{base}/decisions/p1").json()["decision"]
        assert p1["page_review_bundled"] == 4  # d1(R1) · d2(R2) · d3(R3) · s1(shadow R2)
        items = client.get(f"{base}/decisions").json()["items"]
        for item in items:
            if item["alarm_id"] != "p1":
                assert "page_review_bundled" not in item, item["alarm_id"]


# ─── 4. 수집 실패율 메타경보 ─────────────────────────────────────────────


def _cf_records() -> list[dict]:
    return [
        _decision_record(alarm_id="c1", tier="page", stage="collection_failed", ts=_at(5)),
        _decision_record(alarm_id="c2", tier="ticket", stage="matrix", ts=_at(5),
                         stage_evidence={"collection_failed": True}),
        _demoted("c3", "collection_failed", {"page_alarm_id": "c1"}, ago=5),
        _decision_record(alarm_id="o1", tier="suppress", stage="flapping", ts=_at(5)),
        _decision_record(alarm_id="o2", tier="ticket", stage="matrix", ts=_at(5)),
        _decision_record(alarm_id="o3", tier="page", stage="severity3", ts=_at(5)),
        # 창(3600초) 밖 — 분자·분모 모두 빠진다
        _decision_record(alarm_id="old", tier="page", stage="collection_failed", ts=_at(180)),
    ]


def test_collection_failed_ratio_counts_three_populations(tmp_path):
    _seed_decisions(tmp_path, _cf_records())
    config = _config(tmp_path, cf_ratio=0.3)
    # 메타경보(수집 실패 건수 포함)는 운영자 전용 경로다 — 사용자 경로에는 걸리지 않는다
    assert _make_user_client(config).get("/api/v1/noise/health").status_code == 404
    alerts = _make_client(config).get("/api/v1/admin/noise/health").json()["alerts"]
    cf = [a for a in alerts if a["type"] == "collection_failed_ratio"]
    assert len(cf) == 1
    assert (cf[0]["count"], cf[0]["total"]) == (3, 6)
    assert cf[0]["value"] == pytest.approx(0.5) and cf[0]["threshold"] == 0.3


@pytest.mark.parametrize("ratio", [0.0, 0.5])
def test_collection_failed_ratio_off_or_equal_threshold_has_no_alert(tmp_path, ratio):
    """임계 0 = 끔 · 비율이 임계와 같으면 초과가 아니다(> 판정)."""
    _seed_decisions(tmp_path, _cf_records())
    body = _make_client(_config(tmp_path, cf_ratio=ratio)).get("/api/v1/admin/noise/health").json()
    assert all(a["type"] != "collection_failed_ratio" for a in body["alerts"])


def test_health_without_ratio_field_matches_zero_threshold(tmp_path):
    """구 config(필드 부재)는 0과 같은 응답이다."""
    _seed_decisions(tmp_path, _cf_records())
    absent = _make_client(_make_config(tmp_path)).get("/api/v1/admin/noise/health").json()
    zero = _make_client(_config(tmp_path, cf_ratio=0.0)).get("/api/v1/admin/noise/health").json()
    for body in (absent, zero):
        body.pop("last_event_age_seconds", None)
    assert absent == zero


# ─── 5. 화면 XSS ────────────────────────────────────────────────────────


def test_meta_alert_text_into_innerhtml_never_carries_markup():
    hostile = {"type": "collection_failed_ratio", "value": "<img src=x onerror=alert(1)>",
               "threshold": "<b>", "window_seconds": "<script>", "count": "<i>",
               "total": "</strong><svg onload=alert(1)>"}
    out = _run_node("metaAlertText(" + json.dumps(hostile) + ")")
    assert "<" not in out and ">" not in out


def test_review_texts_are_not_written_through_innerhtml():
    src = _NOISE_JS.read_text(encoding="utf-8")
    assignments = re.findall(r"\.innerHTML\s*=\s*([^;]*);", src, flags=re.S)
    risky = ("reviewBasis", "reviewStepNote", "original_reason", "pageReviewKpiText",
             "pageReviewRowText", "reviewLinkButton")
    for rhs in assignments:
        for name in risky:
            assert name not in rhs, rhs
    # 유일한 메타경보 innerHTML 경로는 수집 실패율 유형으로 한정된다(모르는 유형 원문 금지)
    watch = re.search(r'getElementById\("watchNote"\)\.innerHTML\s*=([^;]*);', src, flags=re.S)
    assert watch and "metaAlertText(failed)" in watch.group(1)
    assert 'a.type === "collection_failed_ratio"' in src


# ─── 6. 문서 ────────────────────────────────────────────────────────────


def _doc28_section_4c() -> str:
    text = _DOC28.read_text(encoding="utf-8")
    start = text.index("## 4-C.")
    end = text.index("\n## 5.", start)
    return text[start:end]


def test_doc28_env_lines_have_no_inline_comment_and_real_keys():
    from src.config import NoiseGateConfig

    section = _doc28_section_4c()
    blocks = re.findall(r"```bash\n(.*?)```", section, flags=re.S)
    assert blocks
    prefix = NoiseGateConfig.model_config["env_prefix"]
    fields = NoiseGateConfig.model_fields
    seen = []
    for block in blocks:
        for line in block.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            assert "#" not in line, line
            key, value = line.split("=", 1)
            assert key.startswith(prefix), key
            name = key[len(prefix):].lower()
            assert name in fields, key
            NoiseGateConfig.model_validate({name: value})  # 값이 필드 타입으로 읽힌다
            seen.append(key)
    assert {"NOISE_PAGE_REVIEW_MODE", "NOISE_COLLECTION_FAILED_PAGE_SEV3_ONLY",
            "NOISE_META_ALERT_COLLECTION_FAILED_RATIO"} <= set(seen)


def test_doc28_and_admin_manual_noise_keys_exist():
    from src.config import NoiseGateConfig

    fields = NoiseGateConfig.model_fields
    admin = (_ROOT / "scripts" / "manual" / "content" / "admin.md").read_text(encoding="utf-8")
    texts = _doc28_section_4c() + admin
    pattern = r"NOISE_(?:PAGE_REVIEW|COLLECTION_FAILED|META_ALERT_COLLECTION)\w*"
    keys = set(re.findall(pattern, texts))
    assert keys
    missing = {k for k in keys if k[len("NOISE_"):].lower() not in fields}
    assert missing == set()
