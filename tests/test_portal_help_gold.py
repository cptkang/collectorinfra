"""plans/142 W5 — 포탈 도움말 골든셋 M-1(검색 회수율 recall@4 · 결정적 · LLM 0).

골드 `testdata/routing_gold/portal_help.yaml`을 **실 색인**
(`src/static/manual/help_index.json`)으로 채점한다. 양성은 역할 필터대로(관리자 = 두 권 · 사용자 =
사용자 매뉴얼만) 검색해 상위 `TOP_K` 안에 기대 절이 하나라도 있으면 회수다(기준 ≥ 90% · 계획서
§7). 음성은 `redirect: false`인 항목만 G-5 되돌림 불성립을 단언한다(역할 `role`이 있으면 그 역할 ·
없으면 두 권 — V-2). 라우팅(M-2)은 실 LLM
측정이라 여기 두지 않는다(`scripts/eval_portal_help.py`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from src.doc_qa import portal_help as ph

GOLD = Path(__file__).resolve().parents[1] / "testdata" / "routing_gold" / "portal_help.yaml"
#: M-1 기준(계획서 §7) — 양성 recall@4.
RECALL_MIN = 0.9


def _items() -> list[dict[str, Any]]:
    return yaml.safe_load(GOLD.read_text(encoding="utf-8"))["items"]


ITEMS = _items()
POSITIVES = [it for it in ITEMS if it["polarity"] == "positive"]
NEGATIVES = [it for it in ITEMS if it["polarity"] == "negative"]


@pytest.fixture(scope="module")
def index() -> ph.HelpIndex:
    loaded = ph.load_help_index()
    assert loaded is not None, ph.help_index_status()[1]
    return loaded


def _recall_hit(item: dict[str, Any], index: ph.HelpIndex) -> tuple[bool, list[str]]:
    hits = ph.search_help(item["query"], index=index, is_admin=item["role"] == "admin",
                          k=ph.TOP_K)
    got = [f"{h.chunk.manual}:{h.chunk.section_id}" for h in hits]
    return any(s in got for s in item["expect"]["sections"]), got


def test_gold_shape_matches_plan(index: ph.HelpIndex) -> None:
    """골드 구성(§7) — 양성 20(사용자 14 · 관리자 6 · 2턴 2) · 음성 14(V-2 사용자 역할 4 포함) ·
    기대 절은 색인에 있다."""
    ids = [it["id"] for it in ITEMS]
    assert len(ids) == len(set(ids))
    assert sum(1 for it in POSITIVES if it["role"] == "user") == 14
    assert sum(1 for it in POSITIVES if it["role"] == "admin") == 6
    assert sum(1 for it in POSITIVES if it.get("prior")) == 2
    assert len(NEGATIVES) == 14
    assert sum(1 for it in NEGATIVES if it.get("role") == "user") == 4
    assert {it["category"] for it in NEGATIVES} == {
        "data_lookup", "real_registration", "general_concept"}
    known = {f"{c.manual}:{c.section_id}" for c in index.chunks}
    for it in POSITIVES:
        assert it["expect"]["sections"], it["id"]
        for sec in it["expect"]["sections"]:
            assert sec in known, f"{it['id']}: 색인에 없는 절 {sec}"
            manual = sec.split(":", 1)[0]
            assert it["role"] == "admin" or manual == "user", (
                f"{it['id']}: 사용자 역할 양성의 기대 절은 사용자 매뉴얼이어야 한다")


def test_m1_recall_at_4(index: ph.HelpIndex) -> None:
    """M-1 — 양성 recall@4 ≥ 90%(실패 문항은 메시지에 남긴다)."""
    misses: list[str] = []
    for it in POSITIVES:
        ok, got = _recall_hit(it, index)
        if not ok:
            misses.append(f"{it['id']} {it['query']!r} 기대={it['expect']['sections']} 상위={got}")
    recall = (len(POSITIVES) - len(misses)) / len(POSITIVES)
    assert recall >= RECALL_MIN, f"recall@4 {recall:.2f} < {RECALL_MIN}: " + " | ".join(misses)


@pytest.mark.parametrize("item", [it for it in NEGATIVES if it.get("redirect") is False],
                         ids=lambda it: it["id"])
def test_negative_no_redirect(item: dict[str, Any], index: ph.HelpIndex) -> None:
    """음성 — G-5 되돌림이 성립하지 않는다.

    역할이 있으면 그 역할로(런타임 `portal_redirect`가 호출자 역할로 매칭 — V-2), 없으면 두 권
    (더 넓은 쪽)으로 판정한다.
    """
    is_admin = item.get("role", "admin") == "admin"
    assert ph.portal_redirect_match(item["query"], is_admin=is_admin, index=index) is None
