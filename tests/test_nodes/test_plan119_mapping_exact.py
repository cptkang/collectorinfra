"""폼필 매핑 Layer 1 — 필드명 정규화 후 완전 일치 (plans/119 Q-4).

run `20260923-103638` 서버 로그 실측: 결정적 폼필 피벗(D-146)은 SELECT alias를 양식 필드명
그대로 붙인다(`AS "상태"`). 그런데 Layer 1(`build_resolved_mapping`)은 **매핑 값**
(`EAV:STATUS`)만 결과 키와 비교해 이 행을 풀지 못했고, 52회 전부 Layer 2 LLM(평균 6.4초)으로
넘어갔다. LLM이 돌려준 209건 중 189건은 필드명과 같은 키, 10건은 대소문자만 다른 키
(DB2 결과 칼럼 소문자화 — `OS버전`→`os버전`), 10건은 `서버명`→`호스트명`(두 필드가 같은 매핑 값
`EAV:Hostname`을 가져 LLM 답이 겹친 오매핑 — SQL에는 `"서버명"` 칼럼이 따로 있었다)이었다.

여기서 고정하는 것:
- 로그의 입력 shape(PG 원형 · DB2 소문자 · 복합 필드명 `그룹|서브`)가 Layer 1에서 풀린다.
- 같은 매핑 값을 공유하는 두 필드가 각자 자기 이름의 칼럼으로 풀린다.
- **다른 필드끼리는 잇지 않는다** — 부분 일치·공백 차이·편집 거리 1·대소문자 모호는 미해결로 남는다.
- 매핑 값으로 풀리던 필드는 종전 결과 그대로다(우선순위 유지).
- 매핑이 None인 필드(D-149 공란 불변식)는 같은 이름 칼럼이 있어도 None이다.
- `result_organizer`는 전부 풀리면 Layer 2 LLM을 부르지 않고, 남은 필드만 넘긴다.

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.nodes.result_organizer import result_organizer
from src.utils.column_matcher import build_resolved_mapping, match_field_name_key

# ── 로그 실측 shape (server-baseline.log 3548~3599 · 3797~3798 · 5186~5187) ──

#: PG(cm_gp) 원형 — 매핑은 전부 EAV 유사어, 결과 키는 필드명 alias 그대로.
_PG_MAPPING: dict[str, str | None] = {
    "상태": "EAV:STATUS",
    "IP주소": "EAV:IPaddress",
    "제조사": "EAV:Vendor",
    "CPU코어수": "EAV:LOGICALCORE",
    "OS종류": "EAV:OSType",
    "호스트명": "EAV:Hostname",
}
_PG_KEYS = {"IP주소", "제조사", "OS종류", "호스트명", "상태", "CPU코어수"}

#: DB2(b0) — 결과 칼럼의 라틴 문자가 소문자로 돌아온다. `서버명`·`호스트명`이 같은 매핑 값을 쓴다.
_DB2_MAPPING: dict[str, str | None] = {
    "OS버전": "EAV:OSVerson",
    "메모리용량": "EAV:TotalSize",
    "IP": "EAV:IPaddress",
    "호스트명": "EAV:Hostname",
    "서버명": "EAV:Hostname",
    "비고": None,  # 조립기가 스키마 밖 매핑이라 제외하고 None으로 만든 필드
}
_DB2_KEYS = {"서버명", "os버전", "ip", "호스트명", "메모리용량"}


class TestLoggedShapes:
    """로그에서 Layer 2로 넘어갔던 입력이 Layer 1에서 풀린다."""

    def test_pg_shape_resolves_every_field_by_name(self) -> None:
        """PG 원형은 전부 필드명 키로 풀린다."""
        resolved, unresolved = build_resolved_mapping(_PG_MAPPING, _PG_KEYS)

        assert unresolved == []
        assert resolved == {f: f for f in _PG_MAPPING}

    def test_db2_lowercased_keys_resolve_by_case_fold(self) -> None:
        """DB2 소문자 키는 대소문자 접기로 풀린다."""
        resolved, unresolved = build_resolved_mapping(_DB2_MAPPING, _DB2_KEYS)

        assert unresolved == []
        assert resolved["OS버전"] == "os버전"
        assert resolved["IP"] == "ip"
        assert resolved["메모리용량"] == "메모리용량"
        assert resolved["비고"] is None

    def test_fields_sharing_a_mapping_value_keep_their_own_column(self) -> None:
        """종전 Layer 2는 매핑 값 기준으로 답을 받아 `서버명`도 `호스트명` 칼럼을 가리켰다."""
        resolved, _ = build_resolved_mapping(_DB2_MAPPING, _DB2_KEYS)

        assert resolved["서버명"] == "서버명"
        assert resolved["호스트명"] == "호스트명"

    def test_composite_group_sub_field_names_resolve(self) -> None:
        """복합 필드명 `그룹|서브`도 이름째로 풀린다(D-145)."""
        mapping = {
            "처리능력|(TPMC)": "EAV:TotalSize",
            "제조사(모델명)": "EAV:Vendor",
            "구분|분류": "entity.dtype",
        }
        keys = {"처리능력|(tpmc)", "제조사(모델명)", "구분|분류"}

        resolved, unresolved = build_resolved_mapping(mapping, keys)

        assert unresolved == []
        assert resolved == {
            "처리능력|(TPMC)": "처리능력|(tpmc)",
            "제조사(모델명)": "제조사(모델명)",
            "구분|분류": "구분|분류",
        }


class TestNoFalseMatch:
    """이름이 다른 필드끼리는 잇지 않는다 — 풀지 못하면 종전대로 Layer 2로 간다."""

    @pytest.mark.parametrize(
        ("field", "keys"),
        [
            ("상태", {"상태코드", "리소스상태"}),   # 부분 일치
            ("서버명", {"서버 명"}),               # 공백 차이
            ("서버명", {"서버명칭"}),              # 편집 거리 1
            ("IP", {"IP주소"}),                   # 접두 일치
            ("Ip", {"IP", "ip"}),                 # 대소문자만 다른 후보가 둘
        ],
    )
    def test_non_identical_names_stay_unresolved(self, field: str, keys: set[str]) -> None:
        """필드명 일치가 아니면 판정하지 않는다."""
        assert match_field_name_key(field, keys) is None

        resolved, unresolved = build_resolved_mapping({field: "EAV:Unknown"}, keys)
        assert unresolved == [field]
        assert resolved[field] == "EAV:Unknown", "원본 값 유지(Layer 3 폴백용) — 종전 계약"

    def test_exact_match_beats_case_folded_candidate(self) -> None:
        """정확 일치가 대소문자 후보보다 앞선다."""
        assert match_field_name_key("IP", {"IP", "ip"}) == "IP"

    def test_mapping_value_match_keeps_precedence(self) -> None:
        """매핑 값 매칭이 먼저다 — 필드명 칼럼이 함께 있어도 종전 키를 고른다."""
        resolved, unresolved = build_resolved_mapping(
            {"호스트명": "hosts.hostname"}, {"hostname", "호스트명"},
        )

        assert unresolved == []
        assert resolved["호스트명"] == "hostname"

    def test_none_mapping_stays_none_even_with_same_name_key(self) -> None:
        """D-149 — 채우지 않기로 한 필드를 이름 일치로 되살리지 않는다."""
        resolved, unresolved = build_resolved_mapping({"비고": None}, {"비고"})

        assert resolved["비고"] is None
        assert unresolved == []

    def test_empty_input(self) -> None:
        assert match_field_name_key("", {"a"}) is None
        assert match_field_name_key("a", set()) is None


# ── result_organizer 배선 ──


def _form_state(sample_state: dict, mapping: dict, rows: list[dict]) -> dict:
    state = dict(sample_state)
    state["query_results"] = rows
    state["column_mapping"] = mapping
    state["template_structure"] = {
        "file_type": "xlsx",
        "sheets": [{"name": "Sheet1", "headers": list(mapping)}],
    }
    state["parsed_requirements"] = {
        **state["parsed_requirements"],
        "output_format": "xlsx",
    }
    return state


@pytest.mark.asyncio
async def test_organizer_skips_layer2_when_all_resolved(sample_state, mock_config) -> None:
    """전부 풀리면 Layer 2 LLM을 부르지 않는다."""
    rows = [{k: f"v-{k}" for k in _PG_KEYS}]
    state = _form_state(sample_state, _PG_MAPPING, rows)
    layer2 = AsyncMock(return_value=None)

    sufficiency = AsyncMock(return_value=True)
    with patch("src.nodes.result_organizer._check_data_sufficiency", sufficiency), \
         patch("src.nodes.result_organizer._resolve_unmatched_via_llm", layer2):
        out = await result_organizer(state, llm=AsyncMock(), app_config=mock_config)

    layer2.assert_not_awaited()
    assert out["organized_data"]["resolved_mapping"] == {f: f for f in _PG_MAPPING}


@pytest.mark.asyncio
async def test_organizer_passes_only_leftover_fields_to_layer2(
    sample_state, mock_config,
) -> None:
    """남은 필드만 Layer 2로 넘긴다."""
    mapping = {"상태": "EAV:STATUS", "업무내용": "entity.description"}
    rows = [{"상태": "UP", "resource_desc": "웹"}]
    state = _form_state(sample_state, mapping, rows)
    layer2 = AsyncMock(return_value={"업무내용": "resource_desc"})

    sufficiency = AsyncMock(return_value=True)
    with patch("src.nodes.result_organizer._check_data_sufficiency", sufficiency), \
         patch("src.nodes.result_organizer._resolve_unmatched_via_llm", layer2):
        out = await result_organizer(state, llm=AsyncMock(), app_config=mock_config)

    layer2.assert_awaited_once()
    assert layer2.await_args.kwargs["unresolved_fields"] == ["업무내용"]
    assert out["organized_data"]["resolved_mapping"] == {
        "상태": "상태", "업무내용": "resource_desc",
    }
