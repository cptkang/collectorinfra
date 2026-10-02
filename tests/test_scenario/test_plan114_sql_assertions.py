"""plans/114 M-6 — SQL 단언은 주석을 뺀 본문에 매칭한다 · G-02 턴 2 는 결과로 본다.

근거 run `20260922-112010`:
- A-01 `sql_must_not_match: (?i)여의도` 가 SQL 머리 주석 `-- 여의도 개발 서버들의 …` 에 걸렸다
  (그 턴은 타임아웃이라 분모 밖이었지만, 완주했다면 허위 불합격이었다).
- G-02 턴 2 는 `IN (…)` 대신 `(name AND hostname AND ip) OR …` 3조로 같은 3행을 조회했는데
  구문 단언 `(?i)\\bin\\s*\\(` 에 걸렸다.
"""

from __future__ import annotations

from scripts.scenario.assertions import Observation, evaluate_turn
from scripts.scenario.catalog import Group, Scenario, Turn, load_catalog

#: A-01 실측 SQL 의 머리(주석)와 WHERE 절 — 주석에만 `여의도` 가 있다.
A01_SQL = (
    "-- 여의도 개발 서버들의 CPU 사용률 (월간 통계, 직전월 기준)\n"
    "SELECT COALESCE(svr.name, svr.hostname) AS server_name\n"
    "FROM polestar.cmm_resource r\n"
    "JOIN polestar.cmm_resource svr ON svr.id = r.platform_resource_id\n"
    "WHERE r.resource_type = 'server.Cpus' AND r.dtime IS NULL /* 여의도 */\n"
    "LIMIT 10000;"
)

#: G-02 턴 2 실측 SQL(OR 등치 사슬 형태 · 머리 주석 포함).
G02_T2_OR_SQL = (
    "-- 지정된 서버들의 제조사(Vendor)와 모델(Model) 조회\n"
    "SELECT r.name AS server_name, r.hostname AS hostname\n"
    "FROM polestar.cmm_resource r\n"
    "WHERE r.resource_type = 'server.Server'\n"
    "  AND r.dtime IS NULL\n"
    "  AND (\n"
    "        (r.name = 'cocm-paaapp08' AND r.hostname = 'cocm-paaapp05'"
    " AND r.ipaddress = '10.136.7.94')\n"
    "     OR (r.name = 'com0-pc-cidsv01' AND r.hostname = 'pc-cidsv01'"
    " AND r.ipaddress = '10.136.154.148')\n"
    "     OR (r.name = 'ip-10-140-40-161' AND r.hostname = 'ip-10-140-40-161'"
    " AND r.ipaddress = '10.140.40.161')\n"
    "      )\n"
    "LIMIT 10000;"
)
G02_T2_IN_SQL = (
    "-- 서버별 제조사(Vendor)와 모델(Model) 조회\n"
    "SELECT r.name FROM polestar.cmm_resource r\n"
    "WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL\n"
    "  AND r.name IN ('a', 'b', 'c')\n"
    "LIMIT 10000;"
)
#: G-02 턴 3(전체 분포) 실측 형태 — 선행 서버로 좁히지 않았다.
G02_T3_SQL = (
    "-- 전체 서버 제조사 분포 조회\n"
    "SELECT cc_vendor.stringvalue_short AS vendor, COUNT(*) AS server_count\n"
    "FROM polestar.cmm_resource r\n"
    "LEFT JOIN polestar.core_config_prop cc_vendor\n"
    "  ON r.resource_conf_id = cc_vendor.configuration_id AND cc_vendor.name = 'Vendor'\n"
    "WHERE r.resource_type = 'server.Server' AND r.dtime IS NULL\n"
    "GROUP BY cc_vendor.stringvalue_short\n"
    "LIMIT 10000;"
)


def _group() -> Group:
    return Group(id="T", name="t", latency_target_ms=10000, policy_confirmed=False)


def _eval(expect: dict, *sqls: str, row_count: int | None = None):
    scenario = Scenario(id="T-01", group="T", plans=[114], title="t",
                        turns=[Turn(send={"query": "q"}, expect=expect)])
    obs = Observation(status="completed", row_count=row_count)
    obs.executed_sqls = list(sqls)
    return evaluate_turn(scenario, 1, scenario.turns[0], obs, _group())


# --- 주석 제외 매칭 ----------------------------------------------------------


def test_주석_속_위치어는_부정_단언에_걸리지_않는다() -> None:
    assert _eval({"sql_must_not_match": ["(?i)여의도"]}, A01_SQL).func == "pass"


def test_WHERE_절_리터럴은_여전히_잡는다() -> None:
    sql = A01_SQL.replace("AND r.dtime IS NULL", "AND r.dtime IS NULL AND svr.loc = '여의도'")
    verdict = _eval({"sql_must_not_match": ["(?i)여의도"]}, sql)
    assert verdict.func == "fail"
    # 실패 근거로는 원문 SQL 을 싣는다(사람이 읽는 값)
    assert verdict.failures[0].actual.startswith("-- 여의도")


def test_주석_속_테이블명은_긍정_단언을_통과시키지_못한다() -> None:
    sql = "-- cmm_alarm 은 스키마에 없다\nSELECT hostname FROM cmm_resource LIMIT 10;"
    assert _eval({"sql_must_match": ["(?i)cmm_alarm"]}, sql).func == "fail"


def test_리터럴_안의_이중_하이픈은_주석이_아니다() -> None:
    sql = "SELECT a FROM t WHERE c = 'x--여의도' LIMIT 10;"
    assert _eval({"sql_must_not_match": ["(?i)여의도"]}, sql).func == "fail"


# --- G-02 턴 2 ---------------------------------------------------------------


def _g02_turn(index: int) -> dict:
    scenario = next(s for s in load_catalog().scenarios if s.id == "G-02")
    return dict(scenario.turns[index].expect)


def test_G02_턴2는_OR_등치_사슬도_받는다() -> None:
    assert _eval(_g02_turn(1), G02_T2_OR_SQL, row_count=3).func == "pass"


def test_G02_턴2는_IN_형태도_받는다() -> None:
    assert _eval(_g02_turn(1), G02_T2_IN_SQL, row_count=3).func == "pass"


def test_G02_턴2는_좁히지_않은_SQL을_거부한다() -> None:
    verdict = _eval(_g02_turn(1), G02_T3_SQL, row_count=3)
    assert verdict.func == "fail"
    assert [f.key for f in verdict.failures] == ["sql_must_match"]


def test_G02_턴2는_행_수가_3이어야_한다() -> None:
    verdict = _eval(_g02_turn(1), G02_T2_IN_SQL, row_count=2)
    assert verdict.func == "fail"
    assert any(f.key.startswith("row_count") for f in verdict.failures)


def test_G02_턴3_부정_단언은_그대로다() -> None:
    """턴 3(전체 분포)의 `IN (` 부정 단언은 바꾸지 않았다 — OR 형태를 더하면 정상 SQL 의
    `(a = 'x' OR a = 'y')` 필터까지 위반으로 잡는다."""
    assert _g02_turn(2)["sql_must_not_match"] == ["(?i)\\bin\\s*\\("]
    assert _eval(_g02_turn(2), G02_T3_SQL).func == "pass"
