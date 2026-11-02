"""plans/199 W0 — 화면 픽스처(`tests/fixtures/plan199/`) 형상 검사.

픽스처는 sre_agent dispatcher(가짜 콜러블·가짜 LLM·가짜 HolmesGPT) 산출을 본체 조회 경로로 통과시킨
실제 뷰 봉투다(`make_fixtures.py`). 다음 Wave가 기대는 케이스별 핵심 키만 단언한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from noise_gate.infrastructure.investigation_store import PLAYBOOK_VIEW_KEYS, TRUNCATED_NOTE

_FIX = Path(__file__).resolve().parent / "fixtures" / "plan199"
_CASES = (
    "confirmed_same_layer", "confirmed_cpu_ke", "evidence_missing", "deep_skipped", "deep_failed",
    "missing_many", "different_layer", "non_playbook", "markdown_narrative", "episode_scope",
    "user_view_secret", "running_collect", "running_deep",
)
_ENVELOPE = {"status", "investigation_id", "kind", "episode_id", "briefing_lines", "briefing",
             "cross_source", "updated_at"}
#: sre_agent `investigation_dispatcher.DEEP_NARRATIVE_CLIPPED`의 실제 문구(양방향 import 0).
_SRE_CLIP_MARK = re.compile(r"\n…\(이하 생략 — 원문 \d+자 중 앞 8000자\)$")


def _load(name: str) -> dict[str, Any]:
    return json.loads((_FIX / f"{name}.json").read_text(encoding="utf-8"))


def _briefing(name: str, key: str = "view") -> dict[str, Any]:
    return _load(name)[key]["briefing"]


def _top_hypothesis(b: dict[str, Any]) -> dict[str, Any] | None:
    hyps = b["playbook"]["synthesis"]["hypotheses"]
    return hyps[0] if hyps else None


def _sections(b: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["step"]: s for s in b["playbook"]["sections"]}


@pytest.mark.parametrize("name", _CASES)
def test_fixture_loads_with_note_and_view_envelope(name: str) -> None:
    fx = _load(name)
    assert fx["_note"].strip()
    assert _ENVELOPE <= set(fx["view"]), name
    assert fx["view"]["investigation_id"] == f"inv-{name}"


def test_confirmed_same_layer_has_supported_top_and_same_layer() -> None:
    b = _briefing("confirmed_same_layer")
    ea = b["engine_agreement"]
    assert ea["result"] == "same_layer" and ea["playbook"]["layer"] == "gc"
    assert ea["playbook"]["layer"] in ea["holmesgpt"]["layers"]
    assert _top_hypothesis(b)["status"] == "supported"
    assert b["deep_dive"]["status"] == "done"


def test_confirmed_cpu_ke_has_cpu_layer_and_known_error_match() -> None:
    b = _briefing("confirmed_cpu_ke")
    assert b["engine_agreement"]["playbook"]["layer"] == "cpu"
    top = _top_hypothesis(b)
    assert top["status"] == "supported" and top["rule_id"] == "KE:KE-DEMO-012"
    ke = b["playbook"]["known_errors"][0]
    assert (ke["id"], ke["result"], ke["pinned"]) == ("KE-DEMO-012", "match", True)
    assert ke["title"] and ke["workaround"]


def test_evidence_missing_top_hypothesis_cites_missing_step() -> None:
    b = _briefing("evidence_missing")
    top = _top_hypothesis(b)
    secs = _sections(b)
    assert top["status"] == "supported"
    assert [s for s in top["evidence_steps"] if secs[s]["status"] == "missing"] == ["S6"]


@pytest.mark.parametrize("name, status", [("deep_skipped", "skipped"), ("deep_failed", "failed")])
def test_deep_not_done_cases(name: str, status: str) -> None:
    b = _briefing(name)
    assert b["deep_dive"]["status"] == status and b["deep_dive"]["reason"]
    assert b["engine_agreement"]["result"] == "not_comparable"
    assert "narrative" not in b["deep_dive"]


def test_missing_many_is_unresolved() -> None:
    b = _briefing("missing_many")
    missing = [s for s in b["playbook"]["sections"] if s["status"] == "missing"]
    assert len(missing) >= 2
    assert b["engine_agreement"]["playbook"]["layer"] == "unknown"
    assert _top_hypothesis(b) is None


def test_different_layer_has_badge_and_disjoint_layers() -> None:
    ea = _briefing("different_layer")["engine_agreement"]
    assert ea["result"] == "different_layer" and ea["badge"] == "엔진 간 불일치"
    assert ea["playbook"]["layer"] not in ea["holmesgpt"]["layers"]


def test_non_playbook_has_lines_without_playbook_key() -> None:
    view = _load("non_playbook")["view"]
    assert "playbook" not in view["briefing"]
    labels = [line["label"] for line in view["briefing_lines"]]
    assert {"요약", "원인", "권고"} <= set(labels)


def test_markdown_narrative_poll_and_stored_shapes() -> None:
    fx = _load("markdown_narrative")
    narr = fx["poll_view"]["briefing"]["deep_dive"]["narrative"]
    assert _SRE_CLIP_MARK.search(narr)
    assert len(narr) <= 8000 + 40
    assert len(re.findall(r"^### ", narr, re.M)) >= 3
    for token in ("\n- ", "\n1. ", "**", "`", "|---|"):
        assert token in narr, token
    # 저장소 32KB 상한을 지난 뷰 — 원문이 빠지고 잘림 표지가 한계에 남는다
    stored = fx["view"]["briefing"]
    assert "narrative" not in stored["deep_dive"]
    assert TRUNCATED_NOTE in stored["limitations"]


def test_episode_scope_sources_and_user_view() -> None:
    fx = _load("episode_scope")
    view, user = fx["view"], fx["user_view"]
    assert (view["kind"], view["episode_id"]) == ("episode", "ep-demo-1")
    assert view["cross_source"]["sources"] == {"polestar": 2, "jennifer": 1}
    assert view["alarm"]["alarm_count"] == 3 and len(view["alarm"]["related"]) == 2
    targets = view["briefing"]["playbook"]["scope"]["targets"]
    assert targets and all("verdict" in t for t in targets)
    # 사용자 뷰 — 최상위 kind·episode_id·cross_source는 실리고 브리핑은 표시용 키만
    assert (user["kind"], user["episode_id"]) == ("episode", "ep-demo-1")
    assert user["cross_source"]["sources"] == view["cross_source"]["sources"]
    assert set(user["briefing"]) <= set(PLAYBOOK_VIEW_KEYS)
    assert user["briefing"]["playbook"]["scope"]["targets"] == targets


def test_user_view_secret_pair_masks_raw_values() -> None:
    fx = _load("user_view_secret")
    raw = json.dumps(fx["view"], ensure_ascii=False)
    user = json.dumps(fx["user_view"], ensure_ascii=False)
    for secret in ("abc123", "eyJhbGciOiJIUzI1NiJ9"):
        assert secret in raw and secret not in user, secret
    assert set(fx["user_view"]["briefing"]) <= set(PLAYBOOK_VIEW_KEYS)


@pytest.mark.parametrize("name, deep_status", [("running_collect", "pending"),
                                               ("running_deep", "running")])
def test_running_snapshots(name: str, deep_status: str) -> None:
    view = _load(name)["view"]
    assert view["status"] == "running" and view["briefing"] is None
    progress = view["progress"]
    assert 0 < progress["done"] < progress["total"] and progress["current_step"]
    partial = view["partial_briefing"]
    assert partial["partial"] is True and partial["playbook"]["sections"]
    assert partial["deep_dive"]["status"] == deep_status
