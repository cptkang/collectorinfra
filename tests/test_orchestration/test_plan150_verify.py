"""plans/150 W1·W5 독립 검증(verifier) — 표본 승계 판정 경계 · 상태 오염 · 핵심어 후보 경계.

구현자 테스트(`test_plan150_sample_antecedent.py` ·
`test_nodes/test_plan150_name_hint_keyword.py`)가
고정한 수용 기준 밖의 경계를 본다. 결함으로 확인한 것은 `xfail(strict=True)`로 고정한다 —
고쳐지면 XPASS로 실패해 이 표지를 걷어 내게 한다.
"""

from __future__ import annotations

from typing import Any

from src.nodes.name_match_retry import (
    build_name_match_hint,
    find_name_literals,
    name_keyword,
    name_match_columns,
)
from src.orchestration.result_aggregator import _apply_empty_antecedent
from src.utils.empty_antecedent import (
    EMPTY_ANTECEDENT_KEY,
    SAMPLED_ANTECEDENT_KEY,
    SAMPLED_TURN_KEY,
    resolve_sampled_antecedent,
    sampled_antecedent_outcome,
    sampled_turn,
    scoped_to_sample,
)
from tests.test_orchestration.test_plan150_sample_antecedent import (
    NOTE_HEAD,
    T1_QUERY,
    T1_SQL,
    T2_CARRIED_SQL,
    _itam_rows,
    _os_rows,
    _run_tier2,
    _task_result,
)

# ── W1: 판정 함수 경계 ────────────────────────────────────────────────────────

#: 212 모양 — 앞 턴 조건이 날짜 함수뿐(문자열 조건 값 0)
DATE_T1_QUERY = "도입한 지 6년 넘은 서버 목록 보여줘"
DATE_T1_SQL = (
    "SELECT `서버호스트명`, `IP주소내용` FROM `tcdmsif80` "
    "WHERE `취득년월일` < DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 6 YEAR), '%Y%m%d') "
    "LIMIT 10000"
)


def _ante(sqls: list[str], sample: list[str], query: str = T1_QUERY) -> dict[str, Any]:
    return {"query": query, "sqls": sqls, "row_count": 85, "sample_values": sample}


IP_SAMPLE = [f"10.0.0.{i}" for i in range(20)]


class TestScopedToSampleBoundaries:
    def test_full_prior_in_list_is_not_partial(self) -> None:
        """조건 재조회 뒤 선행 결과 전체(85개)로 건 IN — 표본 밖 값이 섞여 고지 없음."""
        full = ", ".join(f"'10.0.0.{i}'" for i in range(85))
        sql = f"SELECT 1 FROM t WHERE `IP주소내용` IN ({full})"
        assert not scoped_to_sample(_ante([T1_SQL], IP_SAMPLE), [sql])

    def test_sample_in_with_carried_condition_is_partial(self) -> None:
        """앞 조건을 이었어도 표본 IN으로 좁혔으면 일부다(독스트링 계약)."""
        sql = (
            "SELECT 1 FROM t WHERE `그룹경로내용` LIKE '%통합인증%' "
            "AND `IP주소내용` IN ('10.0.0.1', '10.0.0.2')"
        )
        assert scoped_to_sample(_ante([T1_SQL], IP_SAMPLE), [sql])

    def test_empty_sample_never_partial(self) -> None:
        sql = "SELECT 1 FROM t WHERE `IP주소내용` IN ('10.0.0.1')"
        assert not scoped_to_sample(_ante([T1_SQL], []), [sql])

    def test_narrowed_requery_then_in_is_not_partial(self) -> None:
        """「그 서버들 중 AIX인 것」 — task1이 앞 조건 + AIX로 다시 조회해 1대, task2가 그 1대를 IN.

        앞 결과 전체 범위에서 정확히 좁힌 정상 승계다. 그 1대가 표본(앞쪽 20행) 안에 있을 확률은
        20/85 ≈ 24%라 이 오탐은 드물지 않다.
        """
        task1 = (
            "SELECT `IP주소내용` FROM t WHERE `그룹경로내용` LIKE '%통합인증%' "
            "AND `운영체제타입내용` = 'AIX'"
        )
        task2 = "SELECT `서버소프트웨어명` FROM s WHERE `IP주소내용` IN ('10.0.0.3')"
        assert sampled_antecedent_outcome(_ante([T1_SQL], IP_SAMPLE), [task1, task2], 1) is None

    def test_sample_hostnames_in_with_date_prior_is_flagged(self) -> None:
        sql = "SELECT 1 FROM `tcdmsif80` WHERE `서버호스트명` IN ('auth00', 'auth01', 'auth02')"
        note = sampled_antecedent_outcome(
            _ante([DATE_T1_SQL], IP_SAMPLE, DATE_T1_QUERY), [sql], 3
        )
        assert note is not None

    def test_widened_after_date_only_prior_is_flagged(self) -> None:
        wide = "SELECT COUNT(*) AS n FROM `tcdmsif80` WHERE `유지보수계약종료년월일` >= '20261008'"
        note = sampled_antecedent_outcome(
            _ante([DATE_T1_SQL], IP_SAMPLE, DATE_T1_QUERY), [wide], 1
        )
        assert note is not None

    def test_code_in_list_on_other_column_is_not_partial(self) -> None:
        sample = [str(i) for i in range(1, 21)]  # resource_id 표본
        sql = (
            "SELECT * FROM cmm_alarm a WHERE a.created_at >= CURRENT_DATE "
            "AND a.severity IN ('1', '2')"
        )
        assert not scoped_to_sample(_ante([T1_SQL], sample), [sql])


class TestResolveBoundaries:
    def test_non_demonstrative_after_sampled_is_none(self) -> None:
        prior = {SAMPLED_TURN_KEY: sampled_turn(T1_QUERY, [T1_SQL], 85)}
        assert resolve_sampled_antecedent(
            "자산관리에서 AIX 서버 목록", prior, [], entities_complete=False
        ) is None

    def test_global_before_phrase_is_none(self) -> None:
        prior = {SAMPLED_TURN_KEY: sampled_turn(T1_QUERY, [T1_SQL], 85)}
        assert resolve_sampled_antecedent(
            "전체 서버 중 그 서버들 빼고 알려줘", prior, [], entities_complete=False
        ) is None

    def test_record_shape_guard(self) -> None:
        for bad in ({SAMPLED_TURN_KEY: "x"}, {SAMPLED_TURN_KEY: {"sqls": []}}, None):
            assert resolve_sampled_antecedent(
                "그 서버들 OS", bad, [], entities_complete=False
            ) is None

    def test_sampled_turn_drops_empty_sql_and_caps(self) -> None:
        rec = sampled_turn("q", ["", "a", "b", "c", "d"], 21)
        assert rec == {"query": "q", "sqls": ["a", "b", "c"], "row_count": 21}
        assert sampled_turn("q", ["a"], 20) is None


# ── W1: 2단 체크포인터 — 다음다음 턴 오염 · 고지와 행 ───────────────────────────


class TestTier2State:
    async def test_record_does_not_survive_unrelated_turn(self) -> None:
        """t1 85행 → t2 지시어 없는 5행 → t3 「그 서버들」.

        t3는 표본 승계를 보지 않는다(바로 다음 턴만).
        """
        seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, _itam_rows(85)),
            ("자산관리에서 AIX 서버 목록 보여줘",
             "SELECT `서버호스트명`, `IP주소내용` FROM `t_server` WHERE `운영체제타입내용` = 'AIX'",
             _itam_rows(5)),
            ("그 서버들 OS 버전도 알려줘", T2_CARRIED_SQL, _os_rows(5)),
        ])
        assert SAMPLED_TURN_KEY in (outs[0]["conversation_context"] or {})
        assert SAMPLED_TURN_KEY not in (outs[1]["conversation_context"] or {})
        assert SAMPLED_ANTECEDENT_KEY not in (seen[2]["ctx"] or {})
        assert not outs[2]["final_response"].startswith(NOTE_HEAD)

    async def test_chained_sampled_turns_follow_latest(self) -> None:
        """t2(지시어 · 85행 · 조건 이음) → t3 「그 서버들」은 t2 기록을 잇는다(기록이 갱신된다)."""
        seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, _itam_rows(85)),
            ("그 서버들 OS 버전도 알려줘", T2_CARRIED_SQL, _os_rows(85)),
            ("그 서버들 담당 부서도 알려줘", T2_CARRIED_SQL, _os_rows(85)),
        ])
        rec = outs[1]["conversation_context"][SAMPLED_TURN_KEY]
        assert rec["query"] == "그 서버들 OS 버전도 알려줘"
        assert seen[2]["ctx"][SAMPLED_ANTECEDENT_KEY]["sqls"] == [T2_CARRIED_SQL]
        assert not outs[2]["final_response"].startswith(NOTE_HEAD)

    async def test_partial_notice_keeps_rows_and_row_count(self) -> None:
        """표본 IN 고지가 붙어도 query_results 행은 그대로다(숨기지 않음)."""
        sample_sql = (
            "SELECT `서버호스트명`, `OS명` FROM `t_server` "
            "WHERE `IP주소내용` IN ('10.0.0.0', '10.0.0.1', '10.0.0.2')"
        )
        _seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, _itam_rows(85)),
            ("그 서버들 OS 버전도 알려줘", sample_sql, _os_rows(3)),
        ])
        assert outs[1]["final_response"].startswith(NOTE_HEAD)
        assert len(outs[1]["query_results"]) == 3

    async def test_two_task_full_scope_requery_has_no_notice(self) -> None:
        """조건 재조회(85행) → 선행 결과 전체 IN(85행) — 고지 없음 · 다음 턴 기록은 이번 턴 SQL."""
        full_in = ", ".join(f"'10.0.0.{i}'" for i in range(85))
        dep_sql = f"SELECT `서버호스트명`, `OS명` FROM `t_server` WHERE `IP주소내용` IN ({full_in})"
        _seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, _itam_rows(85)),
            ("그 서버들 OS 버전도 알려줘", {
                "t1": _task_result(T1_SQL, _itam_rows(85)),
                "t2": _task_result(dep_sql, _os_rows(85)),
            }),
        ])
        assert not outs[1]["final_response"].startswith(NOTE_HEAD)
        rec = outs[1]["conversation_context"][SAMPLED_TURN_KEY]
        # 두 task 행 합(170)이 기록된다 — 다음 턴 고지 문구의 「N건」이 실제 대상 수의 배가 된다
        assert rec["row_count"] == 170


class TestZoneClarification:
    def test_zone_turn_with_sampled_ctx_does_not_raise(self) -> None:
        ctx = {SAMPLED_ANTECEDENT_KEY: {"query": T1_QUERY, "sqls": [T1_SQL],
                                        "row_count": 85, "sample_values": IP_SAMPLE}}
        result = {"zone_clarification": True, "final_response": "어느 존인가요?"}
        out = _apply_empty_antecedent(result, {"conversation_context": ctx})
        assert out["final_response"] == "어느 존인가요?"

    def test_zone_turn_carries_sampled_record(self) -> None:
        ante = {"query": T1_QUERY, "sqls": [T1_SQL], "row_count": 85, "sample_values": IP_SAMPLE}
        ctx = {SAMPLED_ANTECEDENT_KEY: ante}
        out = _apply_empty_antecedent(
            {"zone_clarification": True, "final_response": "어느 존인가요?"},
            {"conversation_context": ctx},
        )
        assert SAMPLED_TURN_KEY in (out.get("conversation_context") or {})

    def test_zero_row_zone_carry_unchanged(self) -> None:
        """146 경로 회귀 확인 — 0행 승계 중 존 역질문은 기록을 넘긴다."""
        ctx = {EMPTY_ANTECEDENT_KEY: {"query": "q", "sqls": ["s"]}}
        out = _apply_empty_antecedent(
            {"zone_clarification": True}, {"conversation_context": ctx}
        )
        assert out["conversation_context"]["zero_row_turn"] == {"query": "q", "sqls": ["s"]}


# ── W5: 핵심어 후보 경계 ──────────────────────────────────────────────────────

COLS = name_match_columns({"tcdmsif52.업무명": "업무 이름 자유 기재. LIKE 부분 일치로 건다."})


def _sql(where: str) -> str:
    return f"SELECT a.`호스트명` FROM `tcdmsif52` AS a WHERE {where} LIMIT 10000"


class TestNameKeyword:
    def test_in_list_with_escaped_quote_and_spaces(self) -> None:
        sql = _sql("a.`업무명` IN ( 'O''Neil 업무' ,  '그린뱅크 서비스' )")
        assert find_name_literals(sql, COLS) == ["O'Neil 업무", "그린뱅크 서비스"]
        assert name_keyword(find_name_literals(sql, COLS)) == "O'Neil"

    def test_literal_in_comment_or_string_not_extracted(self) -> None:
        sql = (
            "SELECT a.`호스트명` FROM `tcdmsif52` AS a /* a.`업무명` = '가짜값' */ "
            "WHERE a.`호스트명` = 'x 업무명 = ''y'''"
        )
        assert find_name_literals(sql, COLS) == []

    def test_particle_strip_keeps_two_chars(self) -> None:
        assert name_keyword(["법원공탁금의 업무"]) == "법원공탁금"
        assert name_keyword(["카드에 서버"]) == "카드"
        assert name_keyword(["콜에 서버"]) == "콜에"  # 떼면 1자라 그대로

    def test_english_words(self) -> None:
        # 일반어 목록은 한글뿐이라 영문 System(6자)이 가장 긴 낱말로 남는다
        assert name_keyword(["Green Bank System"]) == "System"

    def test_generic_only_returns_none_and_legacy_hint(self) -> None:
        assert name_keyword(["서비스 업무", "시스템", ""]) is None
        assert build_name_match_hint(["`업무명`"], None) == build_name_match_hint(["`업무명`"])

    def test_wildcard_in_equality_literal_stripped(self) -> None:
        sql = _sql("a.`업무명` = '%그린뱅크%'")
        assert name_keyword(find_name_literals(sql, COLS)) == "그린뱅크"

    def test_noun_ending_like_particle_not_stripped(self) -> None:
        assert name_keyword(["게이트웨이"]) == "게이트웨이"
