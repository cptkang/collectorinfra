"""plans/122 T-0 — 한국어 시간 표현 골드셋 채점(구간 정확 일치 · Laparra 2018 · §11).

골드: `testdata/time_gold/*.yaml` — policy(§10.3.1 표 × 다중 기준 시각) · catalog(시나리오 카탈로그
시간 표현 턴) · replay_20260923(§10.1 재현 턴 — 실제 run 시각 기준).

- 새 해석기(규칙 + 결정적 해석기)의 **결정적 부분 100%**를 단언한다(LLM 0)
- 카탈로그 출처 대조: 골드의 query가 현 카탈로그 원문과 같다(시나리오가 바뀌면 골드도 바꾼다)
- 월 투영 호환: 월 경계에 맞는 골드에서 새 `month_range()`와 현행 `resolve_stat_month_range`가
  다르면 그 차이는 아래 `_KNOWN_MONTH_DIFFS`에 사유와 함께 적힌 것뿐이어야 한다
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from src.domain.time_expr import CLARIFY_CODES, interpret, interpret_detail, recognize
from src.domain.time_spec import KST
from src.utils.query_gen_common import resolve_stat_month_range

ROOT = Path(__file__).resolve().parents[2]
GOLD_DIR = ROOT / "testdata" / "time_gold"
SCENARIO_DIR = ROOT / "testdata" / "scenarios"

_EXPECT_KINDS = ("unbounded", "unresolved", "no_period", "out_of_scope", "ambiguous", "clarify")


def _ts(value: str | None) -> datetime | None:
    if value is None:
        return None
    fmt = "%Y-%m-%d %H:%M" if " " in value else "%Y-%m-%d"
    return datetime.strptime(value, fmt).replace(tzinfo=KST)


def _load() -> list[tuple[str, dict[str, Any], datetime]]:
    out: list[tuple[str, dict[str, Any], datetime]] = []
    for path in sorted(GOLD_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        anchors = doc.get("anchors") or {}
        for case in doc["cases"]:
            anchor = _ts(anchors.get(case["anchor"], case["anchor"]))
            assert anchor is not None
            out.append((f"{path.stem}:{case['id']}", case, anchor))
    return out


CASES = _load()
SCORED = [c for c in CASES if "ambiguous" not in c[1]["expect"]]


def _kind(expect: dict[str, Any]) -> str:
    for kind in _EXPECT_KINDS:
        if kind in expect:
            return kind
    return "interval"


def test_gold_schema() -> None:
    ids = [cid for cid, _, _ in CASES]
    assert len(ids) == len(set(ids)), "골드 id 중복"
    for cid, case, _ in CASES:
        assert isinstance(case.get("query"), str) and case["query"], cid
        # 출처: 정책 행·근거(source) 또는 시나리오 id(카탈로그 · 재생 — 재생은 로그 줄까지)
        assert case.get("source") or case.get("scenario"), f"{cid}: 출처 없음"
        if cid.startswith("replay_"):
            assert case.get("log") and case.get("llm_time_range"), f"{cid}: 로그 출처 없음"
        expect = case["expect"]
        assert case.get("subject", "metric") in ("metric", "event"), cid
        if _kind(expect) == "clarify":
            assert expect["clarify"] in CLARIFY_CODES, cid
        if _kind(expect) == "interval":
            assert {"end", "grain", "completeness"} <= set(expect), cid
            assert expect["grain"] in ("hour", "day", "month"), cid


@pytest.mark.parametrize("cid, case, anchor", SCORED, ids=[c[0] for c in SCORED])
def test_gold_case(cid: str, case: dict[str, Any], anchor: datetime) -> None:
    """구간 정확 일치 — start·end·grain·completeness 전부, notes는 부분 집합."""
    query, expect = case["query"], case["expect"]
    kind = _kind(expect)
    specs = recognize(query)
    period_specs = [s for s in specs if s.relation != "none"]
    if kind == "out_of_scope":
        assert period_specs == [], f"{cid}: 「현재」류가 기간으로 해석됨 {period_specs}"
        return
    subject = case.get("subject", "metric")  # 알람 경로 예외(D-291)
    detail = interpret_detail(query, anchor, subject=subject)
    res = detail.resolution
    if kind == "unresolved":
        assert res is None, f"{cid}: 해석 불가여야 한다(되묻기 대상) — {res}"
        return
    if kind == "clarify":
        # 되묻기 사유까지 맞아야 한다(D-291 — 연도 명시 미래 · 존재하지 않는 날짜 · 미래 어휘)
        assert res is None and detail.clarify == expect["clarify"], (
            f"{cid}: 되묻기 {expect['clarify']} 기대 — 실제 {detail.clarify} · {res}")
        return
    assert res is not None, f"{cid}: 해석 실패"
    if kind == "no_period":
        assert period_specs == [] and res.source == "default", f"{cid}: {period_specs}"
        return
    if kind == "unbounded":
        assert res.unbounded and res.grain == expect["grain"], f"{cid}: {res}"
        return
    got = (res.start, res.end, res.grain, res.completeness)
    want = (_ts(expect.get("start")), _ts(expect["end"]), expect["grain"], expect["completeness"])
    assert got == want, f"{cid} {query!r} @ {anchor:%Y-%m-%d %H:%M}: {got} != {want}"
    missing = set(expect.get("notes") or []) - set(res.notes)
    assert not missing, f"{cid}: 고지 코드 누락 {missing} (실제 {res.notes})"


def test_deterministic_part_is_exact() -> None:
    """T-1 verify — 골드셋 결정적 부분 100%(요약 지표 한 줄)."""
    hits = 0
    for _, case, anchor in SCORED:
        try:
            test_gold_case("", case, anchor)
            hits += 1
        except AssertionError:
            pass
    assert hits == len(SCORED), f"정확 일치 {hits}/{len(SCORED)}"


def test_catalog_rule_coverage() -> None:
    """T-2 verify — 카탈로그 기간 표현 턴은 전부 규칙이 잡는다(LLM 슬롯 의존 0)."""
    catalog = [c for c in CASES if c[0].startswith("catalog:")]
    targets = [
        c for c in catalog
        if _kind(c[1]["expect"]) in ("interval", "unbounded")
        and "default_period" not in (c[1]["expect"].get("notes") or [])
    ]
    missed = [cid for cid, case, _ in targets if not recognize(case["query"])]
    assert not missed, f"규칙 미매칭: {missed}"
    # 기간 56 + unbounded 5(기본값 · 해석 불가 · 모호 · 현재류 · 되묻기 제외)
    # — C-067(R3-06 「2030년 1월」)은
    # 2026-09-30 되묻기로 바뀌었다(연도 명시 미래 · D-291)
    assert len(targets) == 61


def _scenario_queries() -> dict[tuple[str, int], str]:
    out: dict[tuple[str, int], str] = {}
    for path in sorted(SCENARIO_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for scn in doc.get("scenarios") or []:
            for i, turn in enumerate(scn.get("turns") or [], 1):
                query = ((turn or {}).get("send") or {}).get("query")
                if isinstance(query, str):
                    out[(scn["id"], i)] = query
    return out


def test_catalog_provenance_matches_scenarios() -> None:
    """골드 query = 현 카탈로그 원문(출처 대조 — 시나리오가 바뀌면 골드를 갱신한다)."""
    queries = _scenario_queries()
    stale = []
    for cid, case, _ in CASES:
        if not cid.startswith("catalog:"):
            continue
        key = (case["scenario"], int(case["turn"]))
        if queries.get(key) != case["query"]:
            stale.append((cid, key))
    assert not stale, f"카탈로그와 다른 골드: {stale}"


# 비교 키 — 카탈로그는 위치 번호 대신 시나리오 id(골드 재생성에도 안정)
def _stable(cid: str, case: dict[str, Any]) -> str:
    if cid.startswith("catalog:"):
        return f"catalog:{case['scenario']}#{case['turn']}"
    return cid


# 현행이 월 범위를 내는데 새 월 투영과 다른 케이스 — 정책 표·설계가 현행과 다르게 정한 것만.
_KNOWN_MONTH_DIFFS: dict[str, str] = {
    "catalog:C-09#1": "비교 질의 — 현행은 첫 월(5월)만, 새 해석은 5~6월을 덮는다(multiple_periods)",
    "catalog:SYN-I-02#1": "C-09 샌드박스 쌍 — 같은 사유",
    "policy:P-052": "「2026년 2분기」 — 현행 _ABS_MONTH_RE가 '월' 없이 「2026년 2」를 2월로 오독",
    "policy:P-061": "「지지난달」 — 현행은 부분 문자열 「지난달」로 오독(8월), 새 해석은 7월",
}
# 현행이 월 범위를 내는데 새 해석은 월 경계에 맞지 않는 구간(월 투영 None) — 입도가 월이 아닌 행.
_KNOWN_NON_MONTH: dict[str, str] = {
    "catalog:C-06#1": "이번 달 — 새 해석은 [1일, 오늘) 일 입도(D-201을 해석기가 직접 표현)",
    "catalog:R4-02#1": "이번 달",
    "policy:P-003": "이번 달",
    "policy:P-004": "당월",
    "policy:P-037": "진행 중 절대 월(2026년 9월) — 이번 달과 같게",
    "policy:P-039": "ISO 날짜(2026-09-15) — 현행은 그 달 전체, 새 해석은 그날",
    "policy:P-044": "지난달 대비 이번 달 — 현행은 지난달만, 새 해석은 덮는 구간(일 입도)",
    "policy:P-102": "이번 달(월말)",
    "policy:P-213": "이번 달(1월)",
    "policy:P-302": "이번 달(윤년)",
    "policy:P-313": "이번 달(매월 1일 — 빈 구간)",
    "policy:P-503": "이번 달(매월 1일 — 빈 구간)",
    "policy:P-601": "이번 달(매월 1일 — 빈 구간)",
    "policy:P-817": "이번 달(D-291 대조군 — 통계는 D-201 그대로)",
}


def _month_compat() -> tuple[dict[str, tuple[Any, Any]], dict[str, Any], list[str]]:
    differ: dict[str, tuple[Any, Any]] = {}
    non_month: dict[str, Any] = {}
    current_none: list[str] = []
    for cid, case, anchor in SCORED:
        if _kind(case["expect"]) != "interval" or case.get("subject") == "event":
            # 월 투영 호환은 통계(metric) 해석만 본다 — 알람 예외(D-291)는 현행 월 해석과
            # 대조 대상이 아니다
            continue
        res = interpret(case["query"], anchor)
        assert res is not None
        new = res.month_range()
        old = resolve_stat_month_range(case["query"], anchor.date())
        key = _stable(cid, case)
        if new is None:
            if old is not None:
                non_month[key] = old
            continue
        if old is None:
            current_none.append(key)
        elif old != new:
            differ[key] = (old, new)
    return differ, non_month, current_none


def test_month_projection_compat_with_current_resolver() -> None:
    """T-1 verify 보조 — 월 투영이 현행과 다른 곳은 정책·설계가 정한 곳뿐이다(목록화)."""
    differ, non_month, current_none = _month_compat()
    assert set(differ) == set(_KNOWN_MONTH_DIFFS), differ
    assert set(non_month) == set(_KNOWN_NON_MONTH), non_month
    # 현행 None(정규식 미매칭 → LLM 폴백 또는 기간 없음)은 새 규칙이 넓힌 범위다 — 건수만 고정한다
    # (반년 · 한글 수사 · 분기 · 연 · 연도 없는 단일 월 · 상대 연도+월 · N년 · N일 ·
    #  기간 없음 기본값 — 2026-09-30 대조군 P-818 1건 추가)
    assert len(current_none) == 41, sorted(current_none)
