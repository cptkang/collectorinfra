"""실 DB 읽기 전용 오라클 — 앵커 · 렌더 · 명세 검사 · 실행기 (plans/122 O-1 · O-3).

DB 에 붙지 않는다. 실행기는 `oracle._open_client` 를 가짜 클라이언트로 바꿔 연결 수명(열림 = 닫힘)·
타임아웃·실패 보류·마스킹 대칭·`oracle_log.jsonl`(행 원문 없음)을 고정한다. 정본 SQL 은 실제
`testdata/scenarios/oracles/` 파일을 로더 검사(`validate_oracle_spec`)와 정적 규칙으로 고정한다 —
로컬 샌드박스 실행 검증은 테스트 스위트 밖(네트워크 가드)에서 한다.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.scenario import oracle
from scripts.scenario.oracle import (
    ORACLE_DIR,
    ORACLE_SPEC_KEYS,
    anchor_values,
    render_sql,
    run_oracle,
    validate_oracle_spec,
)
from src.config import SecurityConfig

ALL_DBS = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
#: §9.1 「가능」 10건 + C-06 ①(G-3 판정 재료).
CANON = {
    "B-01": ALL_DBS, "B-03": ALL_DBS, "B-12": ALL_DBS, "C-01": ALL_DBS, "C-02": ALL_DBS,
    "C-06": ALL_DBS, "C-06-g3": ALL_DBS, "C-07": ALL_DBS, "C-09": ALL_DBS,
    "H-04": ["polestar_b0"], "H-17": ["polestar_b0"],
}
ANCHOR = "2026-09-29T10:00:00+09:00"


# --- 공개 API 계약 ---------------------------------------------------------------

def test_public_api_signatures_match_contract() -> None:
    assert ORACLE_DIR == oracle.REPO_ROOT / "testdata" / "scenarios" / "oracles"
    assert ORACLE_SPEC_KEYS == frozenset({
        "id", "source", "db_ids", "compare", "key", "value", "tol", "top", "match",
        "system", "snapshot", "fixture", "field",
    })
    params = {name: list(inspect.signature(fn).parameters) for name, fn in (
        ("validate_oracle_spec", oracle.validate_oracle_spec),
        ("anchor_values", oracle.anchor_values), ("render_sql", oracle.render_sql),
        ("run_oracle", oracle.run_oracle), ("evaluate_oracle", oracle.evaluate_oracle),
    )}
    assert params["validate_oracle_spec"] == ["spec", "scenario_id", "db_ids"]
    assert params["anchor_values"] == ["anchor_at"]
    assert params["render_sql"] == ["sql", "anchor_at", "run_id", "scenario_id"]
    assert params["run_oracle"] == ["spec", "db_ids", "anchor_at", "run_id", "scenario_id",
                                    "log_path", "timeout_sec", "phase", "cfg"]
    assert params["evaluate_oracle"][:4] == ["spec", "outcome", "result", "pre"]


# --- 앵커 (상대 기간 자리표) -------------------------------------------------------

def test_anchor_values_month_end_and_policy_table() -> None:
    values = anchor_values(ANCHOR)
    assert values["anchor_month"] == "202609"
    assert values["prev_month"] == values["month_minus_1"] == "202608"
    assert values["month_minus_3"] == "202606"
    assert values["today"] == "2026-09-29" and values["yesterday"] == "2026-09-28"
    assert values["month_start_ymd"] == "20260901" and values["yesterday_ymd"] == "20260928"
    assert values["next_month_start"] == "2026-10-01"


def test_anchor_values_year_wrap_and_first_day() -> None:
    values = anchor_values("2026-01-01T08:00:00+09:00")
    assert values["prev_month"] == "202512"
    assert values["month_minus_12"] == "202501"
    # 매월 1일 — 어제는 전월이다(이번 달 범위가 빈다 · D-201).
    assert values["yesterday_ymd"] == "20251231" and values["month_start_ymd"] == "20260101"


def test_anchor_is_converted_to_kst() -> None:
    # UTC 9/30 20:00 = KST 10/1 05:00 — 월이 바뀐다.
    assert anchor_values("2026-09-30T20:00:00Z")["anchor_month"] == "202610"
    # 시간대 없는 값은 KST 로 본다.
    assert anchor_values("2026-09-30T23:00:00")["anchor_month"] == "202609"
    with pytest.raises(ValueError):
        anchor_values("")
    with pytest.raises(ValueError):
        anchor_values("지난달")


# --- 렌더 -----------------------------------------------------------------------------

def test_render_sql_literal_placeholders_tag_and_comment_strip() -> None:
    sql = ("-- 머리 주석 :anchor_month 는 치환하지 않는다\n"
           "SELECT ROUND(AVG(s.avg_val)::numeric, 2) AS v, '10:30' AS t\n"
           "FROM x s WHERE s.stat_date BETWEEN :month_minus_3 AND :prev_month\nLIMIT 5;\n")
    out = render_sql(sql, anchor_at=ANCHOR, run_id="20260929-101010", scenario_id="C-02")
    assert out.splitlines()[0] == "/* scenario-oracle run=20260929-101010 scn=C-02 */"
    assert "BETWEEN '202606' AND '202608'" in out
    assert "::numeric" in out and "'10:30'" in out
    assert "머리 주석" not in out and not out.rstrip().endswith(";")


def test_render_sql_rejects_unknown_placeholder_and_sanitizes_tag() -> None:
    with pytest.raises(ValueError, match="모르는 자리표"):
        render_sql("SELECT :nope AS x LIMIT 1", anchor_at=ANCHOR, run_id="r", scenario_id="s")
    out = render_sql("SELECT 1 AS n LIMIT 1", anchor_at="", run_id="a*/DROP", scenario_id="b c")
    tag = out.splitlines()[0]
    assert tag.count("*/") == 1 and "DROP" in tag and " scn=b_c " in tag


# --- 정본 SQL (O-3) ---------------------------------------------------------------

@pytest.mark.parametrize("oracle_id", sorted(CANON))
def test_canon_passes_loader_check_for_all_target_engines(oracle_id: str) -> None:
    spec = {"id": oracle_id, "compare": "count"}
    assert validate_oracle_spec(spec, scenario_id=oracle_id, db_ids=CANON[oracle_id]) == []


def test_canon_pairs_and_dialect_rules() -> None:
    # MariaDB 정본(ITAM 질의 벤치 · plans/135)은 PG·DB2 짝 규칙 밖이다 —
    # `tests/test_scripts/test_itam_bench_catalog.py` 가 같은 머리·현재시각 금지 규칙으로 고정한다.
    files = sorted(p for p in ORACLE_DIR.glob("*.sql") if not p.name.endswith(".mariadb.sql"))
    ids = {path.name.split(".")[0] for path in files}
    assert ids == set(CANON)
    for oracle_id in ids:
        for suffix in ("pg", "db2"):
            text = (ORACLE_DIR / f"{oracle_id}.{suffix}.sql").read_text(encoding="utf-8")
            head = [line for line in text.splitlines() if line.startswith("--")]
            body = "\n".join(line for line in text.splitlines() if not line.startswith("--"))
            assert any("대상" in line for line in head), oracle_id
            assert any("검수:" in line for line in head), oracle_id
            assert any("근거:" in line for line in head), oracle_id
            # 상대 기간은 앵커 리터럴로만 — DB 현재시각 함수 금지(plans/122 §9.2).
            assert not re.search(r"(?i)current[_ ](date|timestamp)|\bnow\s*\(", body), oracle_id
            if suffix == "db2":
                assert "::" not in body and not re.search(r"(?i)\blimit\b", body), oracle_id
                assert "POLESTAR." in body and "polestar." not in body, oracle_id
                assert any("DB2: 미검수" in line for line in head), oracle_id
            else:
                assert "polestar." in body and "POLESTAR." not in body, oracle_id


def test_canon_column_aliases_match_between_engines() -> None:
    """전 DB 합산 비교는 열 별칭이 엔진 간 같아야 성립한다(DB2 결과 칼럼은 MCP 가 소문자화)."""
    alias = re.compile(r"(?i)\bAS\s+([a-z_][a-z0-9_]*)\s*(?:,|\n\s*FROM)")
    for oracle_id in CANON:
        pg = alias.findall((ORACLE_DIR / f"{oracle_id}.pg.sql").read_text(encoding="utf-8"))
        db2 = alias.findall((ORACLE_DIR / f"{oracle_id}.db2.sql").read_text(encoding="utf-8"))
        assert [a.lower() for a in pg] == [a.lower() for a in db2], oracle_id


# --- 명세 검사 (로더용) ------------------------------------------------------------

def _write(dir_: Path, name: str, text: str) -> None:
    (dir_ / name).write_text(text, encoding="utf-8")


def test_validate_rejects_unknown_keys_and_bad_shapes() -> None:
    errors = validate_oracle_spec(
        {"id": "B-01", "compare": "median", "typo": 1, "tol": -1, "top": 2, "match": "all"},
        scenario_id="B-01", db_ids=ALL_DBS)
    text = " | ".join(errors)
    assert "정의 밖 키 ['typo']" in text
    assert "compare 는" in text and "tol 은" in text
    assert "top 은 compare=argmax" in text and "match 는 compare=keyset" in text


def test_validate_requires_target_dbs_and_known_engines() -> None:
    assert any("대상 DB 가 없다" in e
               for e in validate_oracle_spec({"id": "B-01", "compare": "count"},
                                             scenario_id="B-01", db_ids=None))
    # spec db_ids 로도 된다.
    assert validate_oracle_spec({"id": "B-01", "compare": "count", "db_ids": ["polestar"]},
                                scenario_id="B-01", db_ids=None) == []
    errors = validate_oracle_spec({"id": "B-01", "compare": "count"}, scenario_id="B-01",
                                  db_ids=["itam", "no_such_db"])
    # mariadb(itam)는 plans/135 부터 오라클 대상 엔진이다 — B-01 정본이 없어 파일 부재로 거부된다.
    assert sum("오라클 대상 엔진이 아니다" in e for e in errors) == 1
    assert any("B-01.mariadb.sql" in e for e in errors)


def test_validate_engine_coverage_and_sql_guards(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oracle, "ORACLE_DIR", tmp_path)
    _write(tmp_path, "X-1.pg.sql", "SELECT COUNT(*) AS n FROM polestar.t LIMIT 1\n")
    spec = {"id": "X-1", "compare": "count"}
    assert validate_oracle_spec(spec, scenario_id="X", db_ids=["polestar_cm_gp"]) == []
    # DB2 대상이 섞이면 db2 정본이 없어서 거부 — 조용한 pass 누수 차단(D-275 주의 ③).
    assert any("정본 파일이 없다" in e
               for e in validate_oracle_spec(spec, scenario_id="X", db_ids=ALL_DBS))
    _write(tmp_path, "X-2.pg.sql", "SELECT COUNT(*) AS n FROM polestar.t\n")
    _write(tmp_path, "X-3.db2.sql", "SELECT COUNT(*) AS n FROM POLESTAR.t LIMIT 1\n")
    _write(tmp_path, "X-4.pg.sql", "DELETE FROM polestar.t LIMIT 1\n")
    _write(tmp_path, "X-5.pg.sql", "SELECT :tomorrow AS d LIMIT 1\n")
    _write(tmp_path, "X-6.pg.sql", "SELECT * FROM polestar.t LIMIT 50000\n")
    cases = {
        "X-2": (["polestar_cm_gp"], "마지막 절이 LIMIT n"),
        "X-3": (["polestar_b0"], "DB2 정본에 LIMIT"),
        "X-4": (["polestar_cm_gp"], "SELECT 전용"),
        "X-5": (["polestar_cm_gp"], "모르는 자리표"),
        "X-6": (["polestar_cm_gp"], "행 상한 50000"),
    }
    for oracle_id, (dbs, needle) in cases.items():
        errors = validate_oracle_spec({"id": oracle_id, "compare": "count"}, scenario_id="X",
                                      db_ids=dbs)
        assert any(needle in e for e in errors), (oracle_id, errors)


def test_validate_compare_specific_keys() -> None:
    def errs(**spec: Any) -> str:
        return " | ".join(validate_oracle_spec({"id": "B-12", **spec}, scenario_id="B-12",
                                               db_ids=["polestar"]))

    assert errs(compare="argmax", key=["server_name"], value="cpu_avg", top=3) == ""
    assert "key 가 필요하다" in errs(compare="keyset")
    assert "value 가 필요하다" in errs(compare="argmax", key=["server_name"])
    assert "key 를 쓰지 않는다" in errs(compare="rowset", key=["a"])
    assert "value 를 쓰지 않는다" in errs(compare="keyset", key=["a"], value="v")
    assert "열 참조" in errs(compare="keyset", key=["", ["ok"]])
    assert "pre_post" in errs(compare="rowset", snapshot="pre_post")
    assert "match=equal 만" in errs(compare="keyset", key=["a"], match="subset",
                                    snapshot="pre_post")
    assert errs(compare="keyset", key=[["server_name", "서버명"]], snapshot="pre_post") == ""
    assert "system 은 compare=count" in errs(compare="keyset", key=["a"], system="result")


def test_validate_fixture_source() -> None:
    good = {"id": "M-01", "source": "fixture", "compare": "keyset", "key": [["hostname"]],
            "field": "producer.expected"}
    assert validate_oracle_spec(good, scenario_id="M-01", db_ids=None) == []
    assert validate_oracle_spec({**good, "field": "consumer.present_by_attribute.avail_status",
                                 "id": "M-05"}, scenario_id="M-05", db_ids=None) == []
    text = " | ".join(validate_oracle_spec(
        {**good, "id": "M-99", "db_ids": ["polestar"], "compare": "rowset"},
        scenario_id="M-99", db_ids=None))
    assert "M-99" in text and "db_ids 는 source=fixture" in text and "compare 는" in text
    assert any("field 경로가 없다" in e for e in validate_oracle_spec(
        {**good, "field": "producer.nothing"}, scenario_id="M-01", db_ids=None))
    assert any("스칼라 목록" in e for e in validate_oracle_spec(
        {**good, "field": "producer"}, scenario_id="M-01", db_ids=None))
    assert any("저장소 밖" in e for e in validate_oracle_spec(
        {**good, "fixture": "../../etc/passwd"}, scenario_id="M-01", db_ids=None))


# --- 실행기 (O-1) ---------------------------------------------------------------------

class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]], truncated: bool = False) -> None:
        self.rows = rows
        self.truncated = truncated


class _FakeDb:
    """db_id 별 응답을 돌려주는 가짜 DB. 연결 열림·닫힘과 받은 SQL 을 센다."""

    def __init__(self, answers: dict[str, Any], delay: float = 0.0) -> None:
        self.answers = answers
        self.delay = delay
        self.opened = 0
        self.closed = 0
        self.sqls: list[tuple[str, str]] = []

    def factory(self, _cfg: Any, db_id: str) -> Any:
        db = self

        class _Client:
            async def execute_sql(self, sql: str) -> _FakeResult:
                db.sqls.append((db_id, sql))
                if db.delay:
                    await asyncio.sleep(db.delay)
                answer = db.answers[db_id]
                if isinstance(answer, Exception):
                    raise answer
                return answer if isinstance(answer, _FakeResult) else _FakeResult(answer)

        @asynccontextmanager
        async def _ctx() -> AsyncIterator[_Client]:
            db.opened += 1
            try:
                yield _Client()
            finally:
                db.closed += 1

        return _ctx()


def _cfg(**security: Any) -> SimpleNamespace:
    fields = {"sensitive_columns": ["password"], "mask_ip": False, "mask_email": False,
              "mask_pattern": "***MASKED***", **security}
    return SimpleNamespace(db_backend="dbhub", security=SecurityConfig(**fields))


def _run(spec: dict[str, Any], dbs: list[str], log: Path, **kwargs: Any) -> dict[str, Any]:
    return run_oracle(spec, db_ids=dbs, anchor_at=kwargs.pop("anchor_at", ANCHOR),
                      run_id="R1", scenario_id="S-1", log_path=log,
                      cfg=kwargs.pop("cfg", _cfg()), **kwargs)


def _log_lines(log: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_run_oracle_serial_ok_tag_log_without_rows(tmp_path: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDb({"polestar_cm_gp": [{"n": 120}], "polestar_b0": [{"n": 2338}]})
    monkeypatch.setattr(oracle, "_open_client", fake.factory)
    log = tmp_path / "oracle_log.jsonl"
    out = _run({"id": "B-01", "compare": "count"}, ["polestar_cm_gp", "polestar_b0"], log)
    assert out["status"] == "ok" and out["reason"] is None
    assert out["rows_by_db"] == {"polestar_cm_gp": [{"n": 120}], "polestar_b0": [{"n": 2338}]}
    assert out["limit_by_db"] == {"polestar_cm_gp": 1, "polestar_b0": 1}
    assert out["phase"] == "post" and out["elapsed_ms"] >= 0
    assert fake.opened == fake.closed == 2
    assert [db for db, _sql in fake.sqls] == ["polestar_cm_gp", "polestar_b0"]
    gp_sql, b0_sql = (sql for _db, sql in fake.sqls)
    assert gp_sql.startswith("/* scenario-oracle run=R1 scn=S-1 */\n")
    assert "polestar.cmm_resource" in gp_sql and "POLESTAR.cmm_resource" in b0_sql
    lines = _log_lines(log)
    assert [(line["db"], line["status"], line["rows"]) for line in lines] == [
        ("polestar_cm_gp", "ok", 1), ("polestar_b0", "ok", 1)]
    assert lines[0]["tag"] == "scenario-oracle run=R1 scn=S-1"
    # 행 원문 금지 — 기록 칸은 이것뿐이고 값(120·2338)은 어디에도 없다.
    assert set(lines[0]) == {"ts", "tag", "run", "scn", "oracle", "phase", "db", "engine",
                             "anchor_at", "sql", "rows", "ms", "status", "reason"}
    assert all(120 not in line.values() and 2338 not in line.values() for line in lines)


def test_run_oracle_renders_anchor_literals(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDb({"polestar_cm_yd": [{"cpu_avg": 1.0}]})
    monkeypatch.setattr(oracle, "_open_client", fake.factory)
    _run({"id": "C-01", "compare": "rowset"}, ["polestar_cm_yd"], tmp_path / "l.jsonl")
    sql = fake.sqls[0][1]
    assert "s.stat_date = '202608'" in sql and ":prev_month" not in sql


def test_run_oracle_masks_rows_like_system_csv(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDb({"polestar_cm_gp": [{"ipaddress": "10.61.0.3", "password": "x"}]})
    monkeypatch.setattr(oracle, "_open_client", fake.factory)
    out = _run({"id": "B-03", "compare": "rowset"}, ["polestar_cm_gp"], tmp_path / "l.jsonl",
               cfg=_cfg(mask_ip=True))
    assert out["rows_by_db"]["polestar_cm_gp"] == [
        {"ipaddress": "10.61.0.***", "password": "***MASKED***"}]


def test_run_oracle_direct_backend_is_disabled(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDb({})
    monkeypatch.setattr(oracle, "_open_client", fake.factory)
    log = tmp_path / "l.jsonl"
    cfg = _cfg()
    cfg.db_backend = "direct"
    out = _run({"id": "B-01"}, ["polestar_cm_gp"], log, cfg=cfg)
    assert out["status"] == "unavailable" and "direct" in out["reason"]
    assert fake.opened == 0
    assert _log_lines(log)[0]["status"] == "skipped"


def test_run_oracle_db_failure_is_unavailable_and_closes(tmp_path: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDb({"polestar_cm_gp": [{"n": 1}], "polestar_cm_yd": RuntimeError("SQL 오류")})
    monkeypatch.setattr(oracle, "_open_client", fake.factory)
    log = tmp_path / "l.jsonl"
    out = _run({"id": "B-01"}, ["polestar_cm_gp", "polestar_cm_yd"], log)
    assert out["status"] == "unavailable" and "polestar_cm_yd" in out["reason"]
    assert fake.opened == fake.closed == 2
    assert [line["status"] for line in _log_lines(log)] == ["ok", "error"]


def test_run_oracle_timeout_is_unavailable_and_closes(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDb({"polestar_cm_gp": [{"n": 1}]}, delay=5.0)
    monkeypatch.setattr(oracle, "_open_client", fake.factory)
    log = tmp_path / "l.jsonl"
    out = _run({"id": "B-01"}, ["polestar_cm_gp"], log, timeout_sec=0.05)
    assert out["status"] == "unavailable" and "타임아웃" in out["reason"]
    assert fake.opened == fake.closed == 1
    assert _log_lines(log)[0]["status"] == "timeout"


def test_run_oracle_rejects_before_any_connection(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDb({})
    monkeypatch.setattr(oracle, "_open_client", fake.factory)
    log = tmp_path / "l.jsonl"
    # gp 는 정본이 있지만 itam(mariadb)은 B-01 정본이 없다 → 아무 DB 도 열지 않는다.
    out = _run({"id": "B-01"}, ["polestar_cm_gp", "itam"], log)
    assert out["status"] == "unavailable" and "itam" in out["reason"]
    assert fake.opened == 0
    assert _log_lines(log)[0]["status"] == "rejected"


def test_run_oracle_truncated_result_is_unavailable(tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDb({"polestar_cm_gp": _FakeResult([{"n": 1}], truncated=True)})
    monkeypatch.setattr(oracle, "_open_client", fake.factory)
    out = _run({"id": "B-01"}, ["polestar_cm_gp"], tmp_path / "l.jsonl")
    assert out["status"] == "unavailable" and "max_rows" in out["reason"]


def test_run_oracle_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDb({"polestar_cm_gp": [{"n": 1}]})
    monkeypatch.setattr(oracle, "_open_client", fake.factory)
    log = tmp_path / "l.jsonl"
    for spec in (None, {"id": "../x"}, {"id": "B-01", "source": "fixture"}):
        assert _run(spec, ["polestar_cm_gp"], log)["status"] == "unavailable"  # type: ignore[arg-type]
    assert _run({"id": "B-01"}, [], log)["status"] == "unavailable"
    # 마스킹 설정이 없는 cfg — 비교 대칭을 못 지키므로 보류(예외 아님).
    broken = SimpleNamespace(db_backend="dbhub")
    out = _run({"id": "B-01"}, ["polestar_cm_gp"], log, cfg=broken)
    assert out["status"] == "unavailable" and "오라클 실행 오류" in out["reason"]
    # 로그 경로를 쓸 수 없어도 결과는 돌아온다(사유는 log_error).
    (tmp_path / "blocked").write_text("", encoding="utf-8")
    out = _run({"id": "B-01"}, ["polestar_cm_gp"], tmp_path / "blocked" / "y.jsonl")
    assert out["status"] == "ok" and "log_error" in out


def test_run_oracle_works_inside_running_loop(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeDb({"polestar_cm_gp": [{"n": 3}]})
    monkeypatch.setattr(oracle, "_open_client", fake.factory)

    async def inside() -> dict[str, Any]:
        return _run({"id": "B-01"}, ["polestar_cm_gp"], tmp_path / "l.jsonl")

    out = asyncio.run(inside())
    assert out["status"] == "ok" and fake.opened == fake.closed == 1
