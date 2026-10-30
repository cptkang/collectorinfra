"""plans/201 W7 — 「사례로 배우기」(U-82~U-84) 별칭이 포탈 도움말 되돌림·검색에 주는 영향 (LLM 0).

실 색인(`src/static/manual/help_index.json`)으로 `portal_redirect_match`·`search_help`를 그대로 부른다
(`tests/test_portal_help_gold.py`와 같은 함수 · 사용자 역할).

- 데이터 질의(알람 이력·사용률 조회)는 되돌림이 성립하지 않는다 — 통과해야 한다.
- 재발 방지: 세 사례 절이 공용 별칭(「알람 오면」)을 나눠 갖지 않고, 구체 별칭(힙·응답 지연·CPU 알람)이
  자기 절로 1위·되돌림된다(W7 교정 — 공용 별칭 동점으로 U-82가 늘 1위였고 CPU 질문이 힙 사례로 갔던 결함).
- 잔여(단언하지 않음): 「CPU 알람이 오면 어떻게 대응해?」는 0.47로 되돌림 기준(0.5) 미달.
"""

from __future__ import annotations

import pytest

from src.doc_qa import portal_help as ph


@pytest.fixture(scope="module")
def index() -> ph.HelpIndex:
    loaded = ph.load_help_index()
    assert loaded is not None, ph.help_index_status()[1]
    return loaded


def _top(query: str, index: ph.HelpIndex) -> str | None:
    hits = ph.search_help(query, index=index, is_admin=False)
    return hits[0].chunk.section_id if hits else None


@pytest.mark.parametrize("query", [
    "김포 서버 파일시스템 알람 이력 보여줘",
    "파일시스템 알람 이력 보여줘",
    "서버 A 파일시스템 사용률 보여줘",
    "최근 1시간 응답 지연 알람 몇 건이야?",
    "힙 알람 많은 WAS 보여줘",
])
def test_data_queries_do_not_redirect(query: str, index: ph.HelpIndex) -> None:
    assert ph.portal_redirect_match(query, is_admin=False, index=index) is None


@pytest.mark.parametrize(("query", "section"), [
    ("파일시스템 알람 오면 어떻게 해?", "U-82"),
    ("DB 인계는 어떻게 해?", "U-84"),
    ("사례로 배우기가 뭐야?", "U-82"),
])
def test_case_questions_redirect(query: str, section: str, index: ph.HelpIndex) -> None:
    m = ph.portal_redirect_match(query, is_admin=False, index=index)
    assert m is not None and m.section_id == section


@pytest.mark.parametrize(("query", "section"), [
    ("힙 알람 오면 어떻게 해?", "U-83"),
    ("힙 알람이 오면 어떻게 해?", "U-83"),
    ("응답 지연 알람 오면 어떻게 해?", "U-84"),
    ("CPU 알람 오면 어떻게 해?", "U-82"),
    ("CPU 알람이 오면 어떻게 해?", "U-82"),
])
def test_specific_alias_redirects_to_its_case(query: str, section: str,
                                              index: ph.HelpIndex) -> None:
    """재발 방지 — 구체 별칭이 자기 사례 절로 1위·되돌림(공용 별칭 동점 금지)."""
    assert _top(query, index) == section
    m = ph.portal_redirect_match(query, is_admin=False, index=index)
    assert m is not None and m.section_id == section


def test_no_shared_alias_across_cases(index: ph.HelpIndex) -> None:
    """재발 방지 — 「사례로 배우기」·「알람 대응 사례」(장 이름) 밖의 별칭은 세 사례 절이 나눠 갖지 않는다."""
    chapter = {"사례로 배우기", "알람 대응 사례"}
    owners: dict[str, set[str]] = {}
    for c in index.chunks:
        if c.manual == "user" and c.section_id in {"U-82", "U-83", "U-84"}:
            for a in c.aliases:
                if a not in chapter:
                    owners.setdefault(a, set()).add(c.section_id)
    shared = {a: s for a, s in owners.items() if len(s) > 1}
    assert not shared, shared
    assert "알람 오면" not in owners


def test_bare_alarm_question_not_redirected_to_a_case(index: ph.HelpIndex) -> None:
    """영역 없는 「알람 오면 어떻게 해?」는 특정 사례로 되돌리지 않는다(공용 별칭 제거 결과 고정)."""
    m = ph.portal_redirect_match("알람 오면 어떻게 해?", is_admin=False, index=index)
    assert m is None or m.section_id not in {"U-82", "U-83", "U-84"}


@pytest.mark.parametrize("query", ["김포 서버 CPU 알람 이력 보여줘", "CPU 알람 많은 서버 보여줘"])
def test_cpu_data_queries_do_not_redirect(query: str, index: ph.HelpIndex) -> None:
    assert ph.portal_redirect_match(query, is_admin=False, index=index) is None
