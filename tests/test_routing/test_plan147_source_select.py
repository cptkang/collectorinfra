"""plans/147 W1 · D-322 — 제니퍼 소스 단어(`sources[].terms`)·소스 선택 사다리.

고정하는 계약:
  1. 단어 매칭 — `term_in_text` 규칙(라틴 단어 경계·대소문자 무시 · D-271 제외어) + 긴 단어 우선.
  2. 사다리(§4.1) — 화면 선택 → 단어(LLM 보조) → 승계(지시어 prior 우선) → 지목 없음, 첫 성립 단만.
     미연결은 넓히지 않는다(D-290 ⑥). 소스 <2 = 좁히지 않음.
  3. 레지스트리 로드 — terms 형식만 거부(빈 단어·행 안 중복·문자열 아님) · 여러 소스 공유 허용 ·
     terms 없는 행 하위호환.
  4. AC-8 — 소스 단어는 시스템 유사어·위치 힌트 파생값을 바꾸지 않는다(D-293).
"""

from __future__ import annotations

import copy

import pytest
import yaml

from src.routing.apm_source_select import (
    ALL_SOURCES,
    SourceScope,
    ask_question,
    match_source_terms,
    narrow_notice,
    select_apm_sources,
    unavailable_notice,
)
from src.routing.registry import (
    REGISTRY_PATH,
    RegistryError,
    SourceSpec,
    get_registry,
    parse_registry,
)
from src.utils.query_gen_common import term_in_text, term_spans

BANK = SourceSpec(
    id="bank", label="은행존 제니퍼", zone="bankjon",
    terms=("은행존", "은행", "K리전", "은행존 제니퍼"),
)
COMMON = SourceSpec(
    id="common", label="공동존 제니퍼", zone="gongjon",
    terms=("공동존", "공동", "김포", "여의도", "운영", "개발", "스테이징", "DR", "공동존 제니퍼"),
)
LEGACY = SourceSpec(
    id="legacy", label="레거시 제니퍼", zone="bankjon",
    terms=("은행존", "은행", "레거시", "레거시 제니퍼"),
)
THREE = (BANK, COMMON, LEGACY)
FIVE = (
    *THREE,
    SourceSpec(id="east", label="동부 제니퍼", terms=("동부",)),
    SourceSpec(id="west", label="서부 제니퍼", terms=("서부", "레거시")),
)


# ── 0. 공용 헬퍼 term_spans — term_in_text와 같은 규칙 ─────────────────────────


@pytest.mark.parametrize("term, text", [
    ("DR", "DR도 보여줘"), ("DR", "DRM 오류"), ("DR", "dr 서버"), ("운영", "운영체제 목록"),
    ("운영", "운영 중 서버"), ("운영", "운영 서버"), ("김포", "김포 WAS"), ("", "x"), ("x", ""),
])
def test_term_spans_agrees_with_term_in_text(term: str, text: str) -> None:
    assert bool(term_spans(term, text)) == term_in_text(term, text)


def test_term_spans_returns_all_positions() -> None:
    assert term_spans("은행", "은행 그리고 은행") == [(0, 2), (7, 9)]


# ── 1. 단어 매칭 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("text, ids", [
    ("김포 WAS 힙 추세", ("common",)),
    ("공동존 WAS 응답시간", ("common",)),
    ("레거시 제니퍼 이벤트", ("legacy",)),
    ("은행존 WAS 응답시간", ("bank", "legacy")),
    ("은행 WAS", ("bank", "legacy")),
    ("은행존 제니퍼 이벤트", ("bank",)),           # 긴 단어 우선 — 안의 「은행존」·「은행」 불산입
    ("은행존 제니퍼와 레거시 제니퍼", ("bank", "legacy")),
    ("K리전 WAS", ("bank",)),
    ("DR도 보여줘", ("common",)),
    ("dr 환경 WAS", ("common",)),                   # 대소문자 무시
    ("DRM 오류 WAS", ()),                          # 라틴 단어 경계
    ("운영체제별 WAS", ()),                         # D-271 제외어
    ("운영 중인 WAS", ()),
    ("운영중 WAS", ()),
    ("운영자 목록", ()),
    ("운영팀 WAS", ()),
    ("운영 WAS 응답시간", ("common",)),
    ("WAS 응답시간 가장 느린 5개", ()),
    ("", ()),
])
def test_match_source_terms(text: str, ids: tuple[str, ...]) -> None:
    assert match_source_terms(text, THREE).source_ids == ids


def test_match_evidence_in_text_order_and_long_term_wins() -> None:
    m = match_source_terms("공동존 제니퍼 김포 WAS", THREE)
    assert m.source_ids == ("common",)
    assert m.evidence == {"common": ("공동존 제니퍼", "김포")}


def test_short_term_counted_when_it_also_appears_outside_long_term() -> None:
    m = match_source_terms("은행존 제니퍼 말고 은행존 전체", THREE)
    assert m.source_ids == ("bank", "legacy")


def test_terms_without_terms_field_never_match() -> None:
    assert match_source_terms("은행존", (SourceSpec(id="a"), SourceSpec(id="b"))).source_ids == ()


# ── 2. 사다리 ───────────────────────────────────────────────────────────────


def test_fewer_than_two_sources_is_unscoped() -> None:
    for table in ((), (COMMON,)):
        for kwargs in ({}, {"text": "레거시"}, {"screen_ids": ["common"]}, {"llm_ids": ["x"]}):
            d = select_apm_sources(table, **kwargs)
            assert d.action == "unscoped"
            assert d.choices == () and d.notices == () and d.question == ""


def test_no_registry_source_available_is_unscoped() -> None:
    """게이트웨이 단일 설정(`default`) 등 레지스트리 소스가 하나도 연결 안 된 배포 = 소스 1개."""
    d = select_apm_sources(THREE, available={"default"}, text="김포 WAS")
    assert d.action == "unscoped"


def test_stage1_screen_selection_wins_over_terms() -> None:
    d = select_apm_sources(THREE, screen_ids=["legacy"], text="김포 WAS", llm_ids=["bank"])
    assert (d.action, d.source_ids, d.basis) == ("query", ("legacy",), "selected")
    assert d.notices == ("레거시 제니퍼만 조회했습니다(화면 선택)",)
    assert d.scope_update == SourceScope(ids=("legacy",), basis="selected")


def test_stage1_multiple_screen_ids_query_without_asking() -> None:
    d = select_apm_sources(THREE, screen_ids=["legacy", "bank"])
    assert (d.action, d.source_ids) == ("query", ("bank", "legacy"))
    assert d.notices == ("은행존 제니퍼 · 레거시 제니퍼만 조회했습니다(화면 선택)",)


def test_stage1_answered_basis() -> None:
    d = select_apm_sources(THREE, screen_ids=["common"], screen_basis="answered")
    assert (d.basis, d.scope_update) == ("answered", SourceScope(("common",), "answered"))
    assert d.notices == ("공동존 제니퍼만 조회했습니다(되묻기 답)",)


def test_stage1_all_answer_queries_every_available_source_and_is_inherited() -> None:
    d = select_apm_sources(THREE, screen_ids=[ALL_SOURCES], available={"bank", "common"})
    assert (d.action, d.source_ids, d.basis) == ("query", ("bank", "common"), "all")
    assert d.notices == ()
    assert d.scope_update == SourceScope(ids=(ALL_SOURCES,), basis="all")
    # 다음 턴 — 지목 없음이어도 되묻지 않는다
    nxt = select_apm_sources(THREE, inherited=d.scope_update, available={"bank", "common"})
    assert (nxt.action, nxt.source_ids, nxt.basis) == ("query", ("bank", "common"), "inherited")
    assert nxt.notices == ()


def test_stage1_unknown_screen_ids_are_dropped_and_ladder_continues() -> None:
    d = select_apm_sources(THREE, screen_ids=["nope"], text="김포")
    assert (d.action, d.source_ids, d.basis) == ("query", ("common",), "named")


def test_stage2_single_source_term() -> None:
    d = select_apm_sources(THREE, text="김포 WAS 힙 추세")
    assert (d.action, d.source_ids, d.basis, d.evidence) == (
        "query", ("common",), "named", ("김포",),
    )
    assert d.notices == ("공동존 제니퍼만 조회했습니다(근거: 「김포」)",)
    assert d.scope_update == SourceScope(ids=("common",), basis="named")


def test_stage2_shared_term_asks_with_matched_sources() -> None:
    d = select_apm_sources(THREE, text="은행존 WAS 응답시간")
    assert (d.action, d.source_ids, d.choices) == ("ask", (), ("bank", "legacy"))
    assert d.question == "은행존 제니퍼 · 레거시 제니퍼 중 어느 것을 조회할까요?"
    assert d.scope_update is None


def test_stage2_term_beats_llm() -> None:
    d = select_apm_sources(THREE, text="레거시 이벤트", llm_ids=["bank", "common"])
    assert (d.action, d.source_ids) == ("query", ("legacy",))


def test_stage2_llm_used_only_without_terms() -> None:
    d = select_apm_sources(THREE, text="WAS 응답시간", llm_ids=["common", "zzz"])
    assert (d.action, d.source_ids, d.basis, d.evidence) == ("query", ("common",), "named", ())
    assert d.notices == ("공동존 제니퍼만 조회했습니다(근거: 질문 해석)",)


def test_stage2_llm_two_sources_asks() -> None:
    d = select_apm_sources(THREE, text="WAS", llm_ids=["legacy", "bank"])
    assert (d.action, d.choices) == ("ask", ("bank", "legacy"))


def test_stage2_term_overrides_inheritance() -> None:
    d = select_apm_sources(THREE, text="레거시는?", inherited=SourceScope(("common",), "answered"))
    assert (d.action, d.source_ids, d.scope_update) == (
        "query", ("legacy",), SourceScope(("legacy",), "named"),
    )


def test_stage3_inherited() -> None:
    scope = SourceScope(("common",), "answered")
    d = select_apm_sources(THREE, text="힙 사용률은?", inherited=scope)
    assert (d.action, d.source_ids, d.basis) == ("query", ("common",), "inherited")
    assert d.notices == ("공동존 제니퍼만 조회했습니다(이전 선택 승계)",)
    assert d.scope_update is scope


def test_stage3_prior_targets_win_over_inherited() -> None:
    scope = SourceScope((ALL_SOURCES,), "all")
    d = select_apm_sources(
        THREE, text="그 인스턴스 힙", inherited=scope, prior_target_ids=["legacy"],
    )
    assert (d.action, d.source_ids, d.basis) == ("query", ("legacy",), "inherited")
    assert d.scope_update is scope   # 지시어 후속은 승계 값을 바꾸지 않는다


def test_stage3_inherited_unknown_ids_fall_to_stage4() -> None:
    d = select_apm_sources(THREE, inherited=SourceScope(("gone",), "named"))
    assert d.action == "ask"


def test_stage4_no_designation_asks_with_all() -> None:
    d = select_apm_sources(THREE, text="WAS 응답시간 가장 느린 5개")
    assert (d.action, d.source_ids, d.basis) == ("ask", (), "none")
    assert d.choices == ("bank", "common", "legacy", ALL_SOURCES)
    assert d.question == (
        "은행존 제니퍼 · 공동존 제니퍼 · 레거시 제니퍼 · 전체 중 어느 것을 조회할까요?"
    )
    assert d.scope_update is None


def test_stage4_single_available_source_queries_it() -> None:
    d = select_apm_sources(THREE, available={"common"}, text="WAS")
    assert (d.action, d.source_ids, d.notices) == ("query", ("common",), ())


def test_stage4_choices_exclude_unavailable() -> None:
    d = select_apm_sources(THREE, available={"bank", "legacy"})
    assert d.choices == ("bank", "legacy", ALL_SOURCES)


# 미연결 — 거른 뒤 0 / 1 / 2


def test_unavailable_only_named_source_does_not_widen() -> None:
    d = select_apm_sources(THREE, available={"bank", "common"}, text="레거시 제니퍼 이벤트")
    assert (d.action, d.source_ids) == ("unavailable", ())
    assert d.notices == ("레거시 제니퍼는 연결되지 않아 조회하지 않았습니다",)
    assert d.scope_update is None


def test_unavailable_one_left_queries_with_notice() -> None:
    d = select_apm_sources(THREE, available={"bank", "common"}, text="은행존 WAS")
    assert (d.action, d.source_ids, d.evidence) == ("query", ("bank",), ("은행존",))
    assert d.notices == (
        "은행존 제니퍼만 조회했습니다(근거: 「은행존」)",
        "레거시 제니퍼는 연결되지 않아 조회하지 않았습니다",
    )


def test_unavailable_two_left_asks() -> None:
    d = select_apm_sources(FIVE, available={"bank", "legacy", "common"}, text="레거시와 은행 WAS")
    assert (d.action, d.choices) == ("ask", ("bank", "legacy"))
    assert d.notices == ("서부 제니퍼는 연결되지 않아 조회하지 않았습니다",)


def test_unavailable_screen_selection_does_not_widen() -> None:
    d = select_apm_sources(THREE, available={"bank"}, screen_ids=["legacy", "common"])
    assert d.action == "unavailable"
    assert d.notices == ("공동존 제니퍼 · 레거시 제니퍼는 연결되지 않아 조회하지 않았습니다",)


def test_unavailable_inherited_does_not_widen() -> None:
    """단 3 승계가 전부 미연결이면 단 4로 넘기지 않고 안내만 한다(docstring 근거)."""
    d = select_apm_sources(
        THREE, available={"bank", "common"}, inherited=SourceScope(("legacy",), "named"),
    )
    assert d.action == "unavailable"


def test_five_sources_fixture() -> None:
    assert select_apm_sources(FIVE, text="동부 WAS").source_ids == ("east",)
    d = select_apm_sources(FIVE, text="레거시 WAS")
    assert (d.action, d.choices) == ("ask", ("legacy", "west"))
    d = select_apm_sources(FIVE)
    assert d.choices == ("bank", "common", "legacy", "east", "west", ALL_SOURCES)


# ── 문구 ─────────────────────────────────────────────────────────────────────


def test_phrases_use_label_or_id() -> None:
    table = (SourceSpec(id="a", label="가 제니퍼"), SourceSpec(id="b"))
    assert narrow_notice(["b"], table, basis="selected") == "b만 조회했습니다(화면 선택)"
    question = ask_question(["a", "b", ALL_SOURCES], table)
    assert question == "가 제니퍼 · b · 전체 중 어느 것을 조회할까요?"
    assert unavailable_notice(["a"], table) == "가 제니퍼는 연결되지 않아 조회하지 않았습니다"
    assert unavailable_notice(["x"], (SourceSpec(id="x", label="동부존"),)).startswith("동부존은 ")


# ── 3. 레지스트리 로드 ─────────────────────────────────────────────────────────


def _data(sources: list) -> dict:
    return {
        "zones": [{"code": "zone_a"}],
        "solutions": [{"code": "apm_x", "backend": "mcp", "family": "acme", "sources": sources}],
        "databases": [],
    }


def test_registry_declares_terms_for_three_jennifer_sources() -> None:
    by_id = {s.id: s.terms for s in get_registry().sources_of("apm")}
    assert by_id == {
        "bank": ("은행존", "은행", "K리전", "은행존 제니퍼"),
        "common": (
            "공동존", "공동", "김포", "여의도", "운영", "개발", "스테이징", "DR", "공동존 제니퍼",
        ),
        "legacy": ("은행존", "은행", "레거시", "레거시 제니퍼"),
    }


def test_terms_shared_across_sources_and_stripped() -> None:
    reg = parse_registry(_data([
        {"id": "a", "terms": [" 은행 ", "가"]}, {"id": "b", "terms": ["은행"]},
    ]))
    assert [s.terms for s in reg.sources_of("apm_x")] == [("은행", "가"), ("은행",)]


def test_rows_without_terms_load_as_empty() -> None:
    reg = parse_registry(_data([{"id": "a", "label": "A", "zone": "zone_a"}]))
    assert reg.sources_of("apm_x") == (SourceSpec(id="a", label="A", zone="zone_a"),)


@pytest.mark.parametrize("terms", [["가", " "], ["가", ""], ["가", "가"], ["가", " 가"], [1], "가"])
def test_bad_terms_rejected(terms: object) -> None:
    with pytest.raises(RegistryError):
        parse_registry(_data([{"id": "a", "terms": terms}]))


# ── 4. AC-8 — 소스 단어는 시스템 유사어·위치 힌트 파생값을 바꾸지 않는다 ─────────────


def test_source_terms_do_not_leak_into_alias_or_location_hints() -> None:
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    stripped = copy.deepcopy(raw)
    for sol in stripped["solutions"]:
        for src in sol.get("sources") or []:
            src.pop("terms", None)
    with_terms, without = parse_registry(raw), parse_registry(stripped)
    assert any(s.terms for s in with_terms.sources_of("apm"))
    for derive in (
        "source_alias_terms", "location_db_hints", "location_terms", "location_signal_terms",
        "new_db_signal_terms", "db_signal_terms", "excluding_region_terms",
    ):
        assert getattr(with_terms, derive)() == getattr(without, derive)(), derive
    aliases = set(with_terms.source_alias_terms())
    source_words = {"김포", "은행존", "레거시", "K리전", "공동존 제니퍼", "레거시 제니퍼"}
    assert source_words.isdisjoint(aliases)
