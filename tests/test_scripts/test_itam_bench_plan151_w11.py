"""ITAM 벤치 P1 자산 누락 감지 (plans/151 W11 · F13).

여기서 고정하는 것:
- 리포트 §0 「승인 프로필 P1 자산」 줄(관계 수 · 코드값 칸 수 · 쿼리 규칙 수 — 이름·수만 · D-301)
- P1 초안 승인 · 관계 0 · 코드값 칸 0이면 §0 경고 1줄(고정 문구) — 6회차 꼴은 경고 없음
- `p1`·`approved_profile`이 없으면 줄·경고 모두 생략
- `--run` 시작 콘솔 경고가 리포트와 같은 판정 함수를 쓴다

입력은 최소 dict 다(반출 원본 results/ 미사용).
"""

from __future__ import annotations

from typing import Any

from scripts.itam_bench import __main__ as bench_main
from scripts.itam_bench import report as rp

_P1 = {"draft_id": "d1", "status": "approved"}
_DEFS = {"tcdmsif72": {"manages": "가", "origin": "import"}}


def _confirm_run(**over: Any) -> dict[str, Any]:
    """확인 run 꼴 — 승인 · 관계 0 · `code_values` 키 없음 · 식별 키 없음."""
    approved = {
        "relationships": [],
        "counts": {"query_rules": 3, "query_examples": 5, "patterns": 0, "query_guide_chars": 10},
        "entity_keys": None,
        "table_definitions": _DEFS,
    }
    approved.update(over)
    return {
        "source": "structure_store",
        "assets": {},
        "p1": dict(_P1),
        "approved_profile": approved,
    }


def _run6() -> dict[str, Any]:
    """6회차 꼴 — 관계 다수 · 코드값 칸(칸 → 값 수) 다수 · 식별 키 테이블 있음."""
    return _confirm_run(
        relationships=[{"from": "a.x", "to": "b.x"}] * 64,
        counts={"query_rules": 183, "code_values": {"t.c1": 2, "t.c2": 5, "t.c3": 1}},
        entity_keys={"entity": "server", "table": "tcdmsif76", "keys": []},
    )


def _section0(catalog: dict[str, Any]) -> str:
    text = rp.render_report({"run_id": "r"}, catalog, [])
    return text.split("## 0.")[1].split("## 1.")[0]


def test_confirm_run_shape_warns() -> None:
    catalog = _confirm_run()
    head = _section0(catalog)
    assert "| 승인 프로필 P1 자산 | 관계 0 · 코드값 칸 0 · 쿼리 규칙 3 |" in head
    assert f"> 경고 — {rp.P1_ASSETS_MISSING_WARNING}." in head
    assert head.count("교차 질의 식별 키 테이블") == 1
    assert "| 교차 질의 식별 키 테이블 | — |" in head
    assert rp.p1_assets_missing(catalog)


def test_run6_shape_has_row_without_warning() -> None:
    catalog = _run6()
    head = _section0(catalog)
    assert "| 승인 프로필 P1 자산 | 관계 64 · 코드값 칸 3 · 쿼리 규칙 183 |" in head
    assert "| 교차 질의 식별 키 테이블 | tcdmsif76 |" in head
    assert rp.P1_ASSETS_MISSING_WARNING not in head
    assert not rp.p1_assets_missing(catalog)


def test_identifier_row_added_when_no_definitions() -> None:
    """정의 행이 없으면 식별 키 테이블 행을 P1 자산 줄과 함께 단다(중복 없이 1줄)."""
    head = _section0(_confirm_run(table_definitions=None, entity_keys={"table": "tcdmsif76"}))
    assert "테이블 정의 출처" not in head
    assert head.count("| 교차 질의 식별 키 테이블 | tcdmsif76 |") == 1
    assert "승인 프로필 P1 자산" in head


def test_without_p1_row_and_warning_omitted() -> None:
    catalog = _confirm_run()
    del catalog["p1"]
    head = _section0(catalog)
    assert "승인 프로필 P1 자산" not in head
    assert rp.P1_ASSETS_MISSING_WARNING not in head
    assert rp.p1_asset_counts(catalog) is None
    assert not rp.p1_assets_missing(catalog)


def test_without_approved_profile_is_safe() -> None:
    catalog = {"source": "structure_store", "assets": {}, "p1": dict(_P1), "approved_profile": None}
    assert rp.p1_asset_counts(catalog) is None
    assert not rp.p1_assets_missing(catalog)
    assert "승인 프로필 P1 자산" not in rp.render_report({"run_id": "r"}, catalog, [])


def test_not_approved_draft_no_warning() -> None:
    catalog = _confirm_run()
    catalog["p1"]["status"] = "draft"
    assert not rp.p1_assets_missing(catalog)
    assert "승인 프로필 P1 자산 | 관계 0" in _section0(catalog)


def test_relationships_only_or_code_values_only_no_warning() -> None:
    assert not rp.p1_assets_missing(_confirm_run(relationships=[{"from": "a", "to": "b"}]))
    assert not rp.p1_assets_missing(_confirm_run(counts={"code_values": {"t.c": 1}}))


def test_console_uses_same_judgment() -> None:
    """`--run` 시작 콘솔 `[주의]`는 리포트와 같은 판정 함수·문구를 쓴다."""
    assert bench_main.p1_assets_missing is rp.p1_assets_missing
    assert bench_main.P1_ASSETS_MISSING_WARNING == rp.P1_ASSETS_MISSING_WARNING
    assert bench_main.p1_assets_missing(_confirm_run())
    assert not bench_main.p1_assets_missing(_run6())
