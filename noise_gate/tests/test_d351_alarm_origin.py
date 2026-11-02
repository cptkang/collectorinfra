"""D-351 — 결정 이력 알람 출처(솔루션·존) 분류와 저장소 솔루션 필터.

검증 범위:
    A. `alarm_origin_for` — db_id → 솔루션(레지스트리 `solutions[].code`)·위치(존 약칭 + 사이트).
       제니퍼 소스 표가 사라진 레지스트리·레지스트리 장애에서도 `jennifer*`는 APM으로 남는다.
    B. `DecisionStore.list_decisions(solution=…, solution_of=…)` — 필터·`facets.solutions` 항등식,
       분류 함수 미주입이면 응답이 종전과 같다.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from noise_gate.application.server_identity import alarm_origin_for
from noise_gate.domain.notification_policy import (
    STAGE_FLAPPING,
    STAGE_MATRIX,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)
from noise_gate.infrastructure.decision_store import DecisionStore
from src.routing.registry import SolutionSpec

# ═════════════════════════════════════════════════════════════════════════════
# A. alarm_origin_for
# ═════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("db_id", "solution", "label", "location"),
    [
        ("polestar_b0", "polestar", "폴스타", "은행존"),
        ("polestar_cm_gp", "polestar", "폴스타", "공동존 김포"),
        ("polestar_cm_yd", "polestar", "폴스타", "공동존 여의도"),
        ("jennifer_b0", "apm", "제니퍼", "은행존"),
        ("jennifer_cm_prd", "apm", "제니퍼", "공동존 운영"),
        ("jennifer_cm_stg", "apm", "제니퍼", "공동존 개발/스테이징"),
        ("jennifer", "apm", "제니퍼", ""),
    ],
)
def test_registry_origin(db_id, solution, label, location):
    assert alarm_origin_for(db_id) == {
        "solution": solution, "solution_label": label, "location": location,
    }


@pytest.mark.parametrize("db_id", ["", "no_such_db"])
def test_unresolved_db_id_is_unclassified(db_id):
    assert alarm_origin_for(db_id) == {"solution": "", "solution_label": "", "location": ""}


def test_unknown_jennifer_source_keeps_solution_without_location():
    """소스 표에 없는 id — 솔루션은 family 접두로 남고 위치만 빈다."""
    assert alarm_origin_for("jennifer_zz") == {
        "solution": "apm", "solution_label": "제니퍼", "location": "",
    }


class _NoSourceRegistry:
    """제니퍼 소스 표가 덮어써져 사라진 레지스트리(폐쇄망 config 덮어쓰기 · 2026-10-13 사례)."""

    zones: tuple = ()

    def get(self, db_id):  # noqa: ANN001, ANN201
        return None

    def alarm_source(self, db_id):  # noqa: ANN001, ANN201
        return None

    def solutions(self):  # noqa: ANN201
        return (SolutionSpec(code="apm", label="제니퍼(APM)", family="jennifer"),)

    def location_db_hints(self):  # noqa: ANN201
        return {}


def test_missing_source_table_keeps_apm_solution(monkeypatch):
    import src.routing.registry as reg_mod

    monkeypatch.setattr(reg_mod, "get_registry", lambda: _NoSourceRegistry())
    assert alarm_origin_for("jennifer_cm_prd") == {
        "solution": "apm", "solution_label": "제니퍼", "location": "",
    }


def test_registry_failure_still_classifies_apm(monkeypatch, caplog):
    import src.routing.registry as reg_mod

    def _boom():
        raise RuntimeError("registry down")

    monkeypatch.setattr(reg_mod, "get_registry", _boom)
    with caplog.at_level(logging.WARNING):
        assert alarm_origin_for("jennifer_b0")["solution_label"] == "제니퍼"
        assert alarm_origin_for("polestar_cm_gp")["solution"] == ""
    assert any("알람 출처 솔루션 파생 실패" in r.getMessage() for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════
# B. list_decisions 솔루션 필터
# ═════════════════════════════════════════════════════════════════════════════

BASE = datetime.now(UTC) - timedelta(minutes=30)
_SOLUTION_OF = {"polestar_cm_gp": "polestar", "polestar_b0": "polestar", "jennifer_b0": "apm"}


def _classify(db_id: str) -> str:
    return _SOLUTION_OF.get(db_id, "unclassified")


def _rec(alarm_id: str, tier: str, stage: str, minutes: int, db_id: str | None) -> dict:
    rec = {
        "ts": (BASE + timedelta(minutes=minutes)).isoformat(),
        "alarm_id": alarm_id,
        "tier": tier,
        "stage": stage,
        "reason": "사유",
        "priority": 1,
        "fingerprint": f"fp-{alarm_id}",
        "signals": {"severity": 2},
    }
    if db_id is not None:
        rec["db_id"] = db_id
    return rec


@pytest.fixture()
def seeded(tmp_path) -> DecisionStore:
    store = DecisionStore(str(tmp_path / "decisions.jsonl"))
    records = [
        _rec("p1", TIER_PAGE, STAGE_MATRIX, 1, "polestar_cm_gp"),
        _rec("p2", TIER_TICKET, STAGE_MATRIX, 2, "polestar_b0"),
        _rec("j1", TIER_PAGE, STAGE_MATRIX, 3, "jennifer_b0"),
        _rec("j2", TIER_SUPPRESS, STAGE_FLAPPING, 4, "jennifer_b0"),
        _rec("old", TIER_SUPPRESS, STAGE_FLAPPING, 5, None),  # db_id 기록 이전 판단
    ]
    with store.path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return store


def test_without_classifier_response_is_unchanged(seeded):
    result = seeded.list_decisions(solution="apm")
    assert result["total"] == 5  # 분류 함수가 없으면 solution은 무시된다
    assert set(result["facets"]) == {"tiers", "stages"}


def test_solution_filter_and_facets(seeded):
    result = seeded.list_decisions(solution="apm", solution_of=_classify)
    assert [i["alarm_id"] for i in result["items"]] == ["j2", "j1"]
    assert result["facets"]["solutions"] == {"polestar": 2, "apm": 2, "unclassified": 1}
    # 티어·단계 칩은 솔루션 필터를 반영한다(건수와 칩 합이 맞는다)
    assert sum(result["facets"]["tiers"].values()) == result["total"] == 2
    assert result["facets"]["stages"] == {STAGE_MATRIX: 1, STAGE_FLAPPING: 1}


def test_unclassified_filter_keeps_old_records(seeded):
    result = seeded.list_decisions(solution="unclassified", solution_of=_classify)
    assert [i["alarm_id"] for i in result["items"]] == ["old"]


def test_solution_facets_respect_other_filters(seeded):
    result = seeded.list_decisions(tier=TIER_PAGE, solution="polestar", solution_of=_classify)
    assert result["total"] == 1
    # solutions 칩은 solution 필터만 뺀다 — tier 필터는 반영
    assert result["facets"]["solutions"] == {"polestar": 1, "apm": 1}


def test_classifier_called_once_per_db_id(seeded):
    calls: list[str] = []

    def _counting(db_id: str) -> str:
        calls.append(db_id)
        return _classify(db_id)

    seeded.list_decisions(solution_of=_counting)
    assert sorted(calls) == ["", "jennifer_b0", "polestar_b0", "polestar_cm_gp"]
