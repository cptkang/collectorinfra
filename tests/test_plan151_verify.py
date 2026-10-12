"""plans/151 W1~W7 독립 검증 보강 (verifier · LLM 0 · DB 0).

구현자 테스트(`test_plan151_*`)가 다루지 않는 경계만 둔다.

- W2: 식별자마다 백틱을 친 규칙 줄(`` `t`.`c` ``)은 테이블을 읽지 못해 싣는다(침묵 누락 방지) ·
  선별 출처 `none`·결과 없음은 None · 정의 없는 DB 바이트 동일.
- W3: 레지스트리 적재 실패(`RegistryError`)는 고지 생략 · 존 DB + 존 미배정 DB 혼합 조회는
  종전 고지.
- W4: `NOT LIKE`·함수 감싼 칸(`UPPER(칸) LIKE`) 미발동 · `ILIKE` 발동 · 같은 칸 LIKE 두 번이면
  힌트 칸 목록 중복 없음(지적 Minor) · 재생성 SQL(핵심어 LIKE)은 같은 판정에 다시 안 걸린다.
- W5: `PARTITION BY 별칭`·`ORDER BY 별칭`은 식이 아니다 · AS 생략 별칭은 그대로 풀린다.
- W1: 승인 결과 알림 — 보존 알림 직후 무조건 성공 알림이 덮어쓴다(지적 Major · xfail strict).
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from src.nodes.name_match_retry import (
    build_name_match_hint,
    find_name_likes,
    name_match_columns,
    name_match_retry_hint,
)
from src.nodes.prompt_blocks import build_profile_rules_block, profile_rule_tables
from src.routing.registry import RegistryError

og = importlib.import_module("src.nodes.output_generator")

REPO = Path(__file__).resolve().parents[1]

# ── W2 ───────────────────────────────────────────────────────────────────────

_META = {
    "query_rules": [
        "`tcdmsam01.사용여부`는 플래그 값 'Y', 'N' 중 하나다.",
        "`tcdmsif72`.`활성화여부`는 플래그 값 'Y', 'N' 중 하나다.",  # 식별자별 백틱 — 못 읽음
        "`INST1.tcdmsif72.등록일자`는 'YYYYMMDD' 형식의 문자열 날짜다.",  # 스키마 한정
    ],
    "code_values": {"tcdmsam01.사용여부": ["Y", "N"], "INST1.tcdmsif72.활성화여부": ["Y", "N"]},
}


def test_w2_per_identifier_backtick_rule_is_kept_and_schema_ref_matches() -> None:
    block = build_profile_rules_block(_META, ["TCDMSIF72"])
    assert "`tcdmsam01.사용여부`" not in block
    assert "`tcdmsif72`.`활성화여부`" in block  # 테이블을 못 읽은 줄은 싣는다
    assert "`INST1.tcdmsif72.등록일자`" in block
    assert "INST1.tcdmsif72.활성화여부" in block and "tcdmsam01.사용여부: " not in block


@pytest.mark.parametrize(
    "selection",
    [
        None,
        {"source": "none", "selected": []},
        {"source": "llm"},  # selected 누락
        {"source": "llm", "selected": "tcdmsif72"},  # 모양 오류
        {"source": "unknown", "selected": ["tcdmsif72"]},
    ],
)
def test_w2_rule_tables_none_unless_definition_selection(selection) -> None:
    assert profile_rule_tables(selection) is None
    assert build_profile_rules_block(_META, profile_rule_tables(selection)) == (
        build_profile_rules_block(_META)
    )


def test_w2_lexical_fallback_narrows_to_lexical_picks() -> None:
    sel = {"source": "lexical", "selected": ["tcdmsam01"], "bridged": [], "reason": "llm_error"}
    assert profile_rule_tables(sel) == ["tcdmsam01"]


# ── W3 ───────────────────────────────────────────────────────────────────────


def test_w3_registry_error_skips_disclosure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise RegistryError("적재 실패")

    monkeypatch.setattr(og, "get_registry", _boom)
    state = {"user_query": "전체 서버 수 알려줘"}
    assert og.scope_partial_text(
        state, queried_db_ids=["polestar_cm_gp"], db_origin=None,
        active_db_ids=["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"],
    ) is None


def test_w3_mixed_zoned_and_unzoned_query_still_discloses() -> None:
    state = {"user_query": "전체 서버의 자산 정보와 CPU"}
    text = og.scope_partial_text(
        state, queried_db_ids=["itam", "polestar_cm_gp"], db_origin=None,
        active_db_ids=["polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "itam"],
    )
    assert text is not None


# ── W4 ───────────────────────────────────────────────────────────────────────

_DESC = {
    "tcdmsif72.그룹경로내용": "서비스 그룹 경로 자유 기재 — 핵심어로 부분 일치 조회한다.",
    "tcdmsif72.서버호스트명": "서버 호스트 이름.",
}
_COLS = name_match_columns(_DESC)


def _sql(where: str) -> str:
    return f"SELECT s.`서버호스트명` FROM `tcdmsif72` AS s WHERE {where} LIMIT 100"


@pytest.mark.parametrize(
    ("where", "hit"),
    [
        ("s.`그룹경로내용` NOT LIKE '%통합인증 서비스%'", False),
        ("UPPER(s.`그룹경로내용`) LIKE '%통합인증 서비스%'", False),  # 함수 감쌈 — 등호와 같은 한계
        ("s.`그룹경로내용` ILIKE '%통합인증 서비스%'", True),
        ("s.`그룹경로내용` like '%통합인증 서비스%'", True),
        ("s.`그룹경로내용` LIKE '%통합인증%'", False),
        ("s.`서버호스트명` LIKE '%통합인증 서비스%'", False),  # 이름 칸 아님
        ("s.`그룹경로내용` LIKE '%서비스%'", False),  # 후보 없음
    ],
)
def test_w4_like_boundaries(where: str, hit: bool) -> None:
    assert bool(find_name_likes(_sql(where), _COLS)) is hit


def _state(sql: str) -> dict:
    return {
        "query_results": [], "generated_sql": sql, "active_db_id": "itam",
        "is_multi_db": False, "retry_count": 0,
        "query_attempts": [{"sql": sql, "success": True, "error": None, "row_count": 0}],
    }


@pytest.fixture
def knowledge_root(tmp_path: Path) -> Path:
    import yaml

    d = tmp_path / "itam"
    d.mkdir()
    (d / "column_descriptions.yaml").write_text(
        yaml.safe_dump({"version": 1, "origin": "claude_code", "descriptions": _DESC},
                       allow_unicode=True),
        encoding="utf-8",
    )
    return tmp_path


def test_w4_regenerated_keyword_sql_does_not_fire_again(knowledge_root: Path) -> None:
    first = _sql("s.`그룹경로내용` LIKE '%통합인증 서비스%'")
    hint = name_match_retry_hint(_state(first), max_retry=3, knowledge_root=knowledge_root)
    assert hint is not None and "통합인증" in hint
    regen = _sql("s.`그룹경로내용` LIKE '%통합인증%'")
    assert name_match_retry_hint(_state(regen), max_retry=3, knowledge_root=knowledge_root) is None


def test_w4_same_column_twice_listed_once(knowledge_root: Path) -> None:
    """같은 칸에 LIKE 두 번(OR) — 힌트 칸 목록에 그 칸이 한 번만 나와야 한다(지적 Minor)."""
    sql = _sql("s.`그룹경로내용` LIKE '%통합인증 서비스%' OR s.`그룹경로내용` LIKE '%메일 서비스%'")
    hint = name_match_retry_hint(_state(sql), max_retry=3, knowledge_root=knowledge_root)
    assert hint is not None
    expected_head = build_name_match_hint(
        ["`그룹경로내용`"], None, like_only=True
    ).split(" 그대로")[0]
    assert hint.startswith(expected_head), hint.split(" 그대로")[0]


# ── W5 ───────────────────────────────────────────────────────────────────────


def test_w5_by_reference_skipped_as_omitted_alias_resolved() -> None:
    from scripts.itam_bench import redact as rd

    known = {"그룹경로내용", "x", "y", "b"}
    sql = (
        "SELECT ROW_NUMBER() OVER (PARTITION BY x ORDER BY y) rn, a.b nm, "
        "a.`그룹경로내용` grp\nFROM t a GROUP BY nm, grp\nORDER BY grp"
    )
    out = rd.resolve_result_columns(["rn", "nm", "grp"], [sql], known, set())
    assert out["nm"] == ["b"]
    assert out["grp"] == ["그룹경로내용"]
    assert not any(str(s).startswith("?") for v in out.values() for s in v), out


def test_w5_104_shape_with_paren_comment_and_group_by_alias() -> None:
    from scripts.itam_bench import redact as rd

    sql = (
        "-- 서비스(그룹 경로)별 서버 수를 센다.\nSELECT \n"
        "    COALESCE(s.`그룹경로내용`, s.`구성항목설명내용`) AS service_name,\n"
        "    COUNT(*) AS server_count\nFROM `tcdmsif72` AS s\nGROUP BY service_name\n"
        "ORDER BY server_count DESC\nLIMIT 10000;"
    )
    known = {"그룹경로내용", "구성항목설명내용"}
    out = rd.resolve_result_columns(
        ["service_name", "server_count"], [sql], known, known | {"s", "tcdmsif72"}
    )
    assert out["service_name"] == ["그룹경로내용", "구성항목설명내용"]


# ── W1 (JS 알림) ─────────────────────────────────────────────────────────────


def test_w1_kept_notice_not_overwritten_by_success_notice() -> None:
    """`showSuccess`는 같은 알림 칸 textContent를 바꾼다(admin.js) — 보존 알림 뒤 같은 동기 흐름에서
    `showSuccess`/`showError`가 다시 불리면 보존 알림은 보이지 않는다."""
    js = (REPO / "src/static/js/admin-db-structure.js").read_text(encoding="utf-8")
    start = js.index("더 높은 출처 정의를 보존했습니다")
    tail = js[start: js.index("refreshAfterChange(source);", start)]
    # 보존 알림 다음에 다른 알림 호출이 무조건 이어지는지(if/else if/else 3갈래 모두 알림)
    later = re.findall(r"api\.show(?:Success|Error)\(", tail[tail.index("}") :])
    assert not later
