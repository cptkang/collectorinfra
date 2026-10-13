"""plans/161 · D-329 독립 검증 — 운영 확정 제니퍼 소스(`b0`·`cm_prd`·`cm_stg`)를 실 레지스트리로 판정.

고정하는 계약(D-329 ①~④):
  1. 실 레지스트리 소스 = b0(은행존) · cm_prd · cm_stg(공동존) — 선언 순서·라벨·단어 그대로.
  2. 「공동존」·「공동」 단독 = 지목 없음 → 되묻기(선택지 = 3소스 + 「전체」).
  3. 「은행」·「은행존」·「레거시」 = b0 단일 조회(공유 단어 0).
  4. 「김포랑 여의도」 = 두 소스 단어 → 되묻기(선택지 = 걸린 두 소스 · 「전체」 없음).
  5. 「DR」 = cm_prd(D-271 DB 존 용어와 별개 — D-329 ④).
  6. 벤치 자리표 `answers` 문구는 각각 그 소스 하나로 읽힌다(D-322 부기 ①).
  7. (결함 고정 · xfail strict) 제외 꼬리(「운영체제」·「운영팀」·「운영 중」)는 복합 단어
     「공동존 운영」에도 적용돼야 한다 — D-329 ④ 「제외어는 D-322 ②대로 그대로 쓴다」.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.routing.apm_source_select import ALL_SOURCES, match_source_terms, select_apm_sources
from src.routing.registry import get_registry

REPO = Path(__file__).resolve().parents[2]
ALL3 = ("b0", "cm_prd", "cm_stg")


@pytest.fixture(scope="module")
def sources():
    return get_registry().sources_of("apm")


def _decide(sources, text: str):
    # available=None — 기동 점검 전역값에 기대지 않는다(전 소스 사용 가능으로 명시)
    return select_apm_sources(sources, available=None, text=text)


def test_registry_matches_d329_table(sources) -> None:
    assert [(s.id, s.label, s.zone, tuple(s.terms)) for s in sources] == [
        ("b0", "은행 제니퍼", "bankjon", ("은행존", "은행", "은행 제니퍼", "레거시", "레거시 제니퍼")),
        ("cm_prd", "공동존 운영 제니퍼", "gongjon",
         ("공동존 운영", "김포", "운영", "DR", "공동존 운영 제니퍼")),
        ("cm_stg", "공동존 개발/스테이징 제니퍼", "gongjon",
         ("공동존 개발", "개발", "스테이징", "여의도", "공동존 개발/스테이징 제니퍼")),
    ]


def test_no_term_shared_between_sources(sources) -> None:
    seen: dict[str, str] = {}
    for spec in sources:
        for term in spec.terms:
            assert term not in seen, f"「{term}」이 {seen.get(term)}·{spec.id} 공유"
            seen[term] = spec.id


@pytest.mark.parametrize("text", ["공동존 WAS 응답시간", "공동 WAS 상태", "WAS 응답시간 알려줘"])
def test_gongjon_alone_asks_all(sources, text: str) -> None:
    d = _decide(sources, text)
    assert d.action == "ask" and d.source_ids == ()
    assert d.choices == (*ALL3, ALL_SOURCES)


@pytest.mark.parametrize("text", ["은행 WAS 상태", "은행존 WAS 응답시간", "레거시 WAS 목록",
                                  "레거시 제니퍼의 WAS 목록 보여줘", "은행 제니퍼만 web01 응답시간"])
def test_bank_terms_query_b0_only(sources, text: str) -> None:
    d = _decide(sources, text)
    assert d.action == "query" and d.source_ids == ("b0",) and d.basis == "named"


def test_two_gongjon_terms_ask_between_them(sources) -> None:
    d = _decide(sources, "김포랑 여의도 was01 응답시간 30분")
    assert d.action == "ask" and d.choices == ("cm_prd", "cm_stg")


@pytest.mark.parametrize("text,expected", [
    ("DR 제니퍼 WAS 상태", "cm_prd"),
    ("김포 was01 최근 1시간 이벤트", "cm_prd"),
    ("공동존 운영 제니퍼에서 was01 응답시간", "cm_prd"),
    ("공동존 개발 제니퍼에서 was01 응답시간", "cm_stg"),
    ("공동존 개발/스테이징 제니퍼 WAS 목록", "cm_stg"),
    ("스테이징 WAS 상태", "cm_stg"),
])
def test_single_source_terms(sources, text: str, expected: str) -> None:
    d = _decide(sources, text)
    assert d.action == "query" and d.source_ids == (expected,)


def test_operating_system_alone_is_not_a_source_term(sources) -> None:
    """「운영」 제외 꼬리(D-271 · D-322 ②)는 단독 단어에서 지켜진다."""
    d = _decide(sources, "운영체제별 WAS 수")
    assert d.action == "ask"


def test_bench_answers_each_resolve_to_one_source(sources) -> None:
    from scripts.apm_bench import driver as dv

    ph = dv.load_placeholders(REPO / "testdata" / "apm_bench" / "placeholders.e1.yaml")
    assert set(ph.answers) == set(ALL3)
    for sid, answer in ph.answers.items():
        assert match_source_terms(answer, sources).source_ids == (sid,), (sid, answer)


@pytest.mark.xfail(strict=True, reason=(
    "결함(plans/161 검증): 제외 꼬리 _TERM_EXCLUDED_TAILS가 단어 「운영」에만 걸려 복합 단어 "
    "「공동존 운영」은 「공동존 운영체제」·「공동존 운영팀」·「공동존 운영 중」에서도 cm_prd로 좁힌다 "
    "— D-329 ③(「공동존」 단독은 되묻기)·④(제외어 그대로)와 어긋남"))
@pytest.mark.parametrize("text", ["공동존 운영체제별 WAS 수", "공동존 운영팀 WAS 목록",
                                  "공동존 운영 중인 WAS 응답시간"])
def test_compound_term_respects_excluded_tails(sources, text: str) -> None:
    d = _decide(sources, text)
    assert d.action == "ask", (d.action, d.source_ids, d.evidence)
