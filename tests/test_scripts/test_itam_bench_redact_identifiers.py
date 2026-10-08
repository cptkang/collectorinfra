"""ITAM 벤치 반출 SQL — 식별자 남김 · 리터럴 가림 공존 (plans/143 v1.5 3회차 준비 ② · D-301 부기).

사용자 결정(2026-10-07): 데이터만 가린다 — 테이블·컬럼 이름(백틱 · 카탈로그 실존 무관)과 별칭은
남긴다. 리터럴·주석 가림, 사람 값 수집 가림, 누출 관문은 그대로다. 사람·서술형 컬럼과 같은 술어에
놓인 비한정·비카탈로그 낱말만 「식별자 자리에 쓴 값」으로 보고 가린다.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from scripts.itam_bench import CLOSED_POLICY_PATH
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import redact as rd

M = rd.MASK
_PERSON = "김민수"  # 합성 성명 — 폐쇄망 정책은 카나리아가 비어 있다


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(CLOSED_POLICY_PATH)


@pytest.fixture()
def vault(policy: cat.ColumnPolicy) -> rd.PiiVault:
    return rd.PiiVault.from_policy(policy)


def _redact(
    sql: str,
    policy: cat.ColumnPolicy,
    vault: rd.PiiVault,
    *,
    catalog: tuple[str, ...] = (),
    prompt: str = "",
) -> str:
    return rd.redact_sql(sql, policy=policy, vault=vault, prompt=prompt, catalog_columns=catalog)


# ① 한글 백틱 칼럼(카탈로그 실존·비실존)은 남고, 근거 없는 리터럴은 가려진다
def test_korean_backtick_columns_kept_literal_masked(
    policy: cat.ColumnPolicy, vault: rd.PiiVault
) -> None:
    sql = (
        "SELECT s.`서버명`, s.`없는칼럼` FROM `tcdmsif72` AS s "
        "WHERE s.`용도` = '결제시스템' AND `지어낸칼럼` = 'abc'"
    )
    out = _redact(sql, policy, vault, catalog=("서버명", "용도"), prompt="서버 목록")
    assert out == (
        "SELECT s.`서버명`, s.`없는칼럼` FROM `tcdmsif72` AS s "
        f"WHERE s.`용도` = '{M}' AND `지어낸칼럼` = '{M}'"
    )


# ② AS 뒤 별칭(따옴표 세 종 · 무따옴표)은 이름으로 남는다
def test_aliases_kept(policy: cat.ColumnPolicy, vault: rd.PiiVault) -> None:
    sql = (
        "SELECT sevrHostName AS \"서버명\", iPCtnt AS '아이피', cPUCnt AS `씨피유`, "
        "oSTypzCtnt AS 운영체제 FROM TCDMSIF80"
    )
    assert _redact(sql, policy, vault) == sql


# ③ 한정 식별자는 사람 컬럼과 같은 술어여도 남는다
def test_qualified_identifier_kept(policy: cat.ColumnPolicy, vault: rd.PiiVault) -> None:
    sql = "SELECT * FROM TCDMSIF80 AS s WHERE s.rspblPsnEmnm = s.`없는칼럼`"
    assert _redact(sql, policy, vault) == sql
    plain = "SELECT * FROM TCDMSIF80 AS s WHERE s.rspblPsnEmnm = s.없는칼럼"
    assert _redact(plain, policy, vault) == plain


# ④ 주석은 여전히 가린다
def test_comments_still_masked(policy: cat.ColumnPolicy, vault: rd.PiiVault) -> None:
    sql = "-- 서버명 조회\nSELECT s.`서버명` /* 김포 운영 */ FROM `tcdmsif72` AS s # 끝"
    out = _redact(sql, policy, vault, catalog=("서버명",))
    assert out == f"-- {M}\nSELECT s.`서버명` /*{M}*/ FROM `tcdmsif72` AS s # {M}"


# ⑤ 사람 컬럼 술어의 비한정·비카탈로그 백틱/무따옴표 낱말은 값으로 보고 가린다
@pytest.mark.parametrize(
    "sql, catalog, expected",
    [
        (
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = `{_PERSON}`",
            (),
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = `{M}`",
        ),
        (
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = {_PERSON}",
            (),
            f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm = {M}",
        ),
        # 정책 밖 한글 칼럼도 사람 정보 휴리스틱(「담당자」)으로 pii — 카탈로그 실존 무관
        (
            f"SELECT * FROM `tcdmsif72` WHERE `담당자명` = `{_PERSON}`",
            (),
            f"SELECT * FROM `tcdmsif72` WHERE `담당자명` = `{M}`",
        ),
        (
            f"SELECT * FROM `tcdmsif72` WHERE 담당자명 = {_PERSON}",
            ("담당자명",),
            f"SELECT * FROM `tcdmsif72` WHERE 담당자명 = {M}",
        ),
        # 값을 별칭으로 쓴 꼴(test_value_aliases_are_not_recorded 의 SQL 쪽)
        (
            f"SELECT SUM(rspblPsnEmnm = '{_PERSON}') AS `{_PERSON}` FROM TCDMSIF80",
            (),
            f"SELECT SUM(rspblPsnEmnm = '{M}') AS `{M}` FROM TCDMSIF80",
        ),
        # 서술형 컬럼도 같다
        (
            f"SELECT * FROM TCDMSIF80 WHERE byCtrcName LIKE `{_PERSON}`",
            (),
            f"SELECT * FROM TCDMSIF80 WHERE byCtrcName LIKE `{M}`",
        ),
    ],
)
def test_value_in_identifier_slot_masked(
    policy: cat.ColumnPolicy,
    vault: rd.PiiVault,
    sql: str,
    catalog: tuple[str, ...],
    expected: str,
) -> None:
    out = _redact(sql, policy, vault, catalog=catalog)
    assert out == expected
    assert _PERSON not in out


# ⑥ 리터럴 규칙 불변 — 한글 칼럼 비교의 '0'은 지금처럼 남는다(카탈로그 실존 여부 무관)
@pytest.mark.parametrize("catalog", [(), ("활성화여부",)])
def test_short_number_literal_unchanged(
    policy: cat.ColumnPolicy, vault: rd.PiiVault, catalog: tuple[str, ...]
) -> None:
    sql = "SELECT * FROM `tcdmsif72` WHERE `활성화여부` = '0'"
    assert _redact(sql, policy, vault, catalog=catalog) == sql


_TRACE_SQL = """-- 통합인증 서비스 서버 수
SELECT
    a.`서비스명`        AS service_name,
    COUNT(s.`서버호스트명`)   AS server_count
FROM `tcdmsif72` AS s
JOIN `tcdmsif75` AS sw
  ON s.`그룹회사코드` = sw.`그룹회사코드`
 AND s.`서버호스트명` = sw.`서버호스트명`
 AND s.`자산번호`   = sw.`지어낸자산번호`
JOIN `tcdmsgt82` AS a
  ON ( s.`서버호스트명`   LIKE CONCAT('%', a.`서비스코드`, '%')
       OR s.`서버용도내용` LIKE CONCAT('%', a.`서비스코드`, '%') )
WHERE s.`활성화여부` = '0'
  AND s.`서버용도내용` LIKE '%통합인증 서비스%'
  AND s.`비고` = '결제 담당 문의'
GROUP BY a.`서비스명`
ORDER BY server_count DESC
LIMIT 10000;"""

_TRACE_CATALOG = (
    "서비스명",
    "서버호스트명",
    "그룹회사코드",
    "자산번호",
    "서비스코드",
    "서버용도내용",
    "활성화여부",
    "비고",
)


def _trace_redacted(policy: cat.ColumnPolicy, vault: rd.PiiVault) -> str:
    return _redact(
        _TRACE_SQL,
        policy,
        vault,
        catalog=_TRACE_CATALOG,
        prompt="자산관리에서 통합인증 서비스 서버 수 알려줘",
    )


# ⑦ 2회차 trace 모양 SQL — 결과 문자열 고정(와일드카드만인 `'%'`는 남는다 · plans/149 W4 (1))
def test_second_run_trace_shape(policy: cat.ColumnPolicy, vault: rd.PiiVault) -> None:
    assert _trace_redacted(policy, vault) == f"""-- {M}
SELECT
    a.`서비스명`        AS service_name,
    COUNT(s.`서버호스트명`)   AS server_count
FROM `tcdmsif72` AS s
JOIN `tcdmsif75` AS sw
  ON s.`그룹회사코드` = sw.`그룹회사코드`
 AND s.`서버호스트명` = sw.`서버호스트명`
 AND s.`자산번호`   = sw.`지어낸자산번호`
JOIN `tcdmsgt82` AS a
  ON ( s.`서버호스트명`   LIKE CONCAT('%', a.`서비스코드`, '%')
       OR s.`서버용도내용` LIKE CONCAT('%', a.`서비스코드`, '%') )
WHERE s.`활성화여부` = '0'
  AND s.`서버용도내용` LIKE '%통합인증 서비스%'
  AND s.`비고` = '{M}'
GROUP BY a.`서비스명`
ORDER BY server_count DESC
LIMIT 10000;"""


# ⑧ 남긴 식별자가 든 trace 레코드는 누출 관문을 통과한다(오탐 없음)
def test_kept_identifiers_pass_leak_gate(
    policy: cat.ColumnPolicy, vault: rd.PiiVault, tmp_path: Path
) -> None:
    vault.add("홍민지")  # 결과에서 모은 사람 값(이 SQL 에는 없다)
    record = {
        "executed_sqls": [
            {"sql": _trace_redacted(policy, vault), "success": True, "row_count": 3},
            {
                "sql": _redact(
                    "SELECT sevrHostName AS \"서버명\", cPUCnt AS 씨피유 FROM TCDMSIF80",
                    policy,
                    vault,
                ),
                "success": True,
                "row_count": 1,
            },
        ]
    }
    staged = {"trace.jsonl": json.dumps(record, ensure_ascii=False) + "\n"}
    gate = rd.LeakGate(policy=policy, vault=vault, user_values={})
    assert gate.check(staged) == []
    ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
    assert ok and violations == []
    assert "서비스명" in (tmp_path / "run" / "trace.jsonl").read_text("utf-8")


# ⑨ vault 에 모인 사람 값은 식별자 자리(한정·별칭)여도 가린다
def test_vault_values_masked_in_identifier_slots(
    policy: cat.ColumnPolicy, vault: rd.PiiVault
) -> None:
    vault.add(_PERSON)
    sql = f"SELECT s.`{_PERSON}` AS `{_PERSON}`, s.{_PERSON} FROM `tcdmsif72` AS s"
    out = _redact(sql, policy, vault)
    assert _PERSON not in out
    pii = rd.MASK_PII
    assert out == f"SELECT s.`{pii}` AS `{pii}`, s.{pii} FROM `tcdmsif72` AS s"


# ⑩ SELECT 목록의 일반 별칭은 같은 항목·목록에 pii 컬럼이 있어도 남는다
def test_select_list_items_judged_separately(
    policy: cat.ColumnPolicy, vault: rd.PiiVault
) -> None:
    sql = (
        's.`서버호스트명` AS "서버명", s.`IP주소` AS "IP", a.`담당자명` AS "담당자", '
        "s.`지어낸칼럼` FROM `tcdmsif72` AS s JOIN `tcdmsif41` a ON s.`자산번호` = a.`자산번호`"
    )
    out = _redact(
        "SELECT " + sql,
        policy,
        vault,
        catalog=("서버호스트명", "IP주소", "담당자명", "자산번호"),
    )
    # 항목마다 따로 본다 — 「서버명」은 남는다. 사람 정보 칼럼(휴리스틱 「주소」·「담당자」)
    # 하나만인 항목의 별칭은 값일 수 있어 가린다(규칙 C-e · 보수 쪽으로 수용)
    assert out == (
        f'SELECT s.`서버호스트명` AS "서버명", s.`IP주소` AS "{M}", a.`담당자명` AS "{M}", '
        "s.`지어낸칼럼` FROM `tcdmsif72` AS s JOIN `tcdmsif41` a ON s.`자산번호` = a.`자산번호`"
    )


# ⑪ 괄호 안 쉼표(IN 목록·함수 인자)는 경계가 아니다
def test_in_list_commas_are_not_boundaries(
    policy: cat.ColumnPolicy, vault: rd.PiiVault
) -> None:
    sql = "SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm IN (`박서준`, `김철수`)"
    out = _redact(sql, policy, vault)
    assert out == f"SELECT * FROM TCDMSIF80 WHERE rspblPsnEmnm IN (`{M}`, `{M}`)"


# ⑫ 값 별칭 항목은 가리고, 다음 항목의 별칭은 남긴다
def test_value_alias_item_masked_next_item_kept(
    policy: cat.ColumnPolicy, vault: rd.PiiVault
) -> None:
    sql = "SELECT SUM(rspblPsnEmnm = '박서준') AS `박서준`, COUNT(*) AS \"건수\" FROM TCDMSIF80"
    out = _redact(sql, policy, vault)
    assert out == f"SELECT SUM(rspblPsnEmnm = '{M}') AS `{M}`, COUNT(*) AS \"건수\" FROM TCDMSIF80"


# --- 보안 감사 교정(2026-10-07) — 정책 밖 한글 칼럼 곁의 값 · 이차 시간 -----------------------

_AUDIT_CATALOG = ("관리자", "비고", "담당")


@pytest.mark.parametrize(
    "sql, expected",
    [
        # 값 자리(비교 연산자 뒤) — 왼쪽 칼럼은 카탈로그 이름이라 남는다
        ("SELECT * FROM t WHERE 관리자 = 박서준", f"SELECT * FROM t WHERE 관리자 = {M}"),
        ("SELECT * FROM t WHERE 비고 = `박서준`", f"SELECT * FROM t WHERE 비고 = `{M}`"),
        # 모양(숫자로 시작) · IN 목록
        (
            "SELECT * FROM t WHERE 담당 IN (`1234567`, `2345678`)",
            f"SELECT * FROM t WHERE 담당 IN (`{M}`, `{M}`)",
        ),
        # 카탈로그 밖 칼럼(작성자·사용자)은 남고 비교 값만 가린다
        (
            "SELECT * FROM t WHERE 작성자 = 박서준 OR 사용자 = 김철수",
            f"SELECT * FROM t WHERE 작성자 = {M} OR 사용자 = {M}",
        ),
        # THEN · ELSE 뒤 — WHEN 바로 뒤의 카탈로그 밖 칼럼(담당자명)도 값 자리로 가려진다(수용)
        (
            "SELECT CASE WHEN 담당자명='x' THEN `박서준` ELSE `김철수` END FROM t",
            f"SELECT CASE WHEN {M}='{M}' THEN `{M}` ELSE `{M}` END FROM t",
        ),
        # 상수 하나짜리 항목의 별칭(규칙 C-d) — ASCII 별칭 a 는 그대로
        (
            "SELECT 담당자명 AS a, 1 AS `박서준` FROM t",
            f"SELECT 담당자명 AS a, 1 AS `{M}` FROM t",
        ),
        # 가린 리터럴과 겹치는 별칭(규칙 C-a)
        (
            "SELECT COUNT(CASE WHEN 담당자명='박서준' THEN 1 END) AS 박서준 FROM t",
            f"SELECT COUNT(CASE WHEN {M}='{M}' THEN 1 END) AS {M} FROM t",
        ),
        # 공백 든 값(모양) · 값 자리
        ("SELECT * FROM t WHERE 담당 = `박 서준`", f"SELECT * FROM t WHERE 담당 = `{M}`"),
        # BETWEEN · LIKE 뒤
        (
            "SELECT * FROM t WHERE 비고 BETWEEN 가나 AND 다라",
            f"SELECT * FROM t WHERE 비고 BETWEEN {M} AND {M}",
        ),
        ("SELECT * FROM t WHERE 비고 LIKE `박서준`", f"SELECT * FROM t WHERE 비고 LIKE `{M}`"),
        # 재점검 1 — 값 하위 질의의 SELECT 목록
        (
            "SELECT * FROM t WHERE 담당 = ANY (SELECT 박서준 FROM u)",
            f"SELECT * FROM t WHERE 담당 = ANY (SELECT {M} FROM u)",
        ),
        (
            "SELECT * FROM t WHERE 담당 IN (SELECT 박서준)",
            f"SELECT * FROM t WHERE 담당 IN (SELECT {M})",
        ),
        # 재점검 2 — 단순 CASE 의 WHEN 비교값
        (
            "SELECT CASE 담당 WHEN 박서준 THEN 1 END FROM t",
            f"SELECT CASE 담당 WHEN {M} THEN 1 END FROM t",
        ),
        # 재점검 5 — 사람 칼럼 하나만인 항목의 별칭(c161bfd 에서도 가려졌다)
        ("SELECT 담당자명 AS '박서준' FROM t", f"SELECT 담당자명 AS '{M}' FROM t"),
        # 재점검 6 — 숫자 꼬리까지 한 낱말로 가린다
        ("SELECT * FROM t WHERE 담당 = 박서준1234567", f"SELECT * FROM t WHERE 담당 = {M}"),
    ],
)
def test_audit_values_beside_unreviewed_columns_masked(
    policy: cat.ColumnPolicy, vault: rd.PiiVault, sql: str, expected: str
) -> None:
    out = _redact(sql, policy, vault, catalog=_AUDIT_CATALOG, prompt="서버 목록")
    assert out == expected
    for value in ("박서준", "김철수", "1234567", "2345678", "박 서준", "가나", "다라"):
        assert value not in out


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 서버명 AS 박서준 FROM t",
        # 선택 목록 · ORDER BY 의 따옴표 없는 낱말(값 자리 아님 · 조각에 위험 칼럼 없음)
        "SELECT 서버명, 박서준 FROM t",
        "SELECT 서버명 FROM t ORDER BY 박서준",
    ],
)
def test_accepted_residual_kept(
    policy: cat.ColumnPolicy, vault: rd.PiiVault, sql: str
) -> None:
    """수용 잔여(D-301 부기) — 값 근거가 없는 별칭·선택 목록·ORDER BY 낱말은 이름으로 남는다.

    사용자 결정 「별칭은 이름」 때문이다. 결과에서 수집한 값(vault)이면 ⑨처럼 가려진다.
    """
    assert _redact(sql, policy, vault, catalog=("서버명",)) == sql


def test_defined_alias_references_kept(policy: cat.ColumnPolicy, vault: rd.PiiVault) -> None:
    """재점검 3 — SELECT 에서 정의해 남긴 별칭의 GROUP BY·ORDER BY·HAVING 참조는 남는다."""
    sql = (
        "SELECT 서버명, COUNT(*) AS `건수` FROM t WHERE t.`용도` LIKE '%통합인증%' "
        "GROUP BY `서버명` HAVING `건수` > 1 ORDER BY `건수` DESC"
    )
    out = _redact(sql, policy, vault, catalog=("서버명", "용도"), prompt="통합인증 서버 수")
    assert out == sql


def test_function_wrapped_person_column_alias_kept(
    policy: cat.ColumnPolicy, vault: rd.PiiVault
) -> None:
    """규칙 C-e 는 칼럼 하나만인 항목에만 — 함수로 감싼 항목의 별칭은 남는다."""
    sql = "SELECT COUNT(담당자명) AS 건수, MAX(rspblPsnEmnm) AS `최근담당` FROM t"
    assert _redact(sql, policy, vault) == sql


@pytest.mark.parametrize(
    "sql, catalog",
    [
        # 폐쇄망 모양 — 별칭·한정 식별자·'0' 이 남는다
        (
            'SELECT s.`서버호스트명` AS "서버명", s.`지어낸칼럼` FROM `tcdmsif72` AS s '
            "WHERE s.`활성화여부` = '0'",
            ("서버호스트명", "활성화여부"),
        ),
        # 왼쪽 자리 · 조각에 다른 칼럼 없음 — 지어낸 칼럼명도 남는다
        ("SELECT * FROM `tcdmsif72` WHERE `지어낸칼럼` = '0'", ()),
        # 함수 항목의 별칭은 상수 항목이 아니다
        ('SELECT COUNT(*) AS 건수, MAX(s.`서버명`) AS "최대" FROM `tcdmsif72` AS s', ()),
    ],
)
def test_identifiers_still_kept(
    policy: cat.ColumnPolicy, vault: rd.PiiVault, sql: str, catalog: tuple[str, ...]
) -> None:
    assert _redact(sql, policy, vault, catalog=catalog) == sql


def test_many_plain_words_linear_time(policy: cat.ColumnPolicy, vault: rd.PiiVault) -> None:
    sql = "SELECT " + " ".join(f"한글낱말{i}" for i in range(2500))
    assert len(sql) > rd.INPUT_MAX  # 20KB 위 — 상한까지 전부 판정한다
    warm = "SELECT 1 FROM t WHERE a = '010-1234-5678'"
    rd.redact_sql(warm, policy=policy, vault=vault)  # 지연 import 를 시간 밖으로
    started = time.perf_counter()
    rd.redact_sql(sql, policy=policy, vault=vault, catalog_columns=_AUDIT_CATALOG)
    assert time.perf_counter() - started < 0.5


def test_alias_word_is_not_counted_as_column(policy: cat.ColumnPolicy, vault: rd.PiiVault) -> None:
    """별칭 자리 낱말(「서버이름」 — 「이름」 휴리스틱)은 조각 위험 칼럼으로 세지 않는다."""
    sql = "SELECT 호스트명 AS 서버이름 FROM t LIMIT 10"
    assert _redact(sql, policy, vault) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM t WHERE EXISTS (SELECT 박서준 FROM u)",
        "SELECT * FROM (SELECT 박서준 FROM u) x",
    ],
)
def test_accepted_residual_non_value_subquery_kept(
    policy: cat.ColumnPolicy, vault: rd.PiiVault, sql: str
) -> None:
    """수용 잔여(D-301 부기) — 값 자리가 아닌 하위 질의(EXISTS · FROM 파생 테이블)의 선택 목록은
    바깥 선택 목록과 같이 이름으로 남는다(값 하위 질의 `= ANY (SELECT …)`·`IN (SELECT …)`만 가린다).
    """
    assert _redact(sql, policy, vault, catalog=("담당",)) == sql
