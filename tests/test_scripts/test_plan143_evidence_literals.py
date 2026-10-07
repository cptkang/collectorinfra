"""plans/143 fix3 작업 1 — 커밋되는 원천 파일에 근거 run 리터럴 거절(`evidence_literal`).

대조 집합: 근거 run 실행 SQL의 문자열·숫자 리터럴 · 반출 카탈로그 주석과 반출 테이블 정의 글의 코드
열거 값. 거절 메시지에는 위치만 싣는다(값 없음).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from scripts.itam_bench import build_assets as ba
from scripts.itam_bench import knowledge as kn
from src.api.routes.db_structure import asset_sql_checker
from src.domain import knowledge_assets as ka

_RUN_ID = "20261102-000000"
_SCHEMA = {
    "tables": {
        "zzab01": {
            "columns": [
                {"name": "서버호스트명", "type": "varchar"},
                {"name": "상태구분", "type": "char"},
                {"name": "기준년월일", "type": "char"},
            ]
        }
    }
}
_ALLOWED = ["zzab01"]


def _item(item_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": item_id, "origin": ka.ORIGIN, "evidence": _RUN_ID, "status": ka.STATUS_ACTIVE,
        **fields,
    }


def _write(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


class TestSqlLiterals:
    def test_strings_and_numbers(self) -> None:
        sql = (
            "SELECT a FROM t WHERE x = '20260923' AND y LIKE '%web01%' AND z > 1500 "
            "AND w = 'it''s'"
        )
        assert ka.sql_literals(sql) == ["20260923", "web01", "1500", "it's"]

    def test_row_limits_comments_identifiers_excluded(self) -> None:
        assert ka.sql_literals("SELECT * FROM t FETCH FIRST 500 ROWS ONLY") == []
        assert ka.sql_literals("SELECT c FROM tcdmsif90 LIMIT 10, 2000 OFFSET 300") == []
        assert ka.sql_literals("SELECT `x 777` FROM t -- 8888\n/* 9999 */ LIMIT 5") == []
        assert ka.sql_literals("SELECT TOP 100 c FROM t WHERE n = 4242") == ["4242"]

    def test_value_filter(self) -> None:
        found = ka.evidence_literal_values(
            ["01", "Y", "202609", "현행", "SELECT", "active", "yyyymmdd", "TCDMSIF90", "web01"],
            exempt=["tcdmsif90"],
        )
        assert found == {"202609", "web01"}


def _run(tmp_path: Path, *, definitions: Any = None) -> Path:
    run = tmp_path / "results" / _RUN_ID
    run.mkdir(parents=True)
    records = [
        {
            "prompt": "운영 서버 호스트 목록",
            "executed_sqls": [
                {"sql": "SELECT 서버호스트명 FROM zzab01 WHERE 기준년월일 = '20260923' "
                        "AND 서버호스트명 LIKE '%운영%' AND 상태구분 = '<가림>' "
                        "AND 서버호스트명 = '서버호스트명' LIMIT 10000"},
            ],
        },
        {"prompt": "x", "executed_sqls": [{"sql": "SELECT 1 FROM zzab01 WHERE n > 7731"}]},
    ]
    (run / kn.TRACE_FILE).write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8"
    )
    _write(run / ba.CATALOG_FILE, {
        "approved_profile": {"table_definitions": definitions or {}},
        "tables": {
            "zzab01": {
                "meaning": "서버 원장",
                "columns": [
                    {"name": "서버호스트명"},
                    {"name": "상태구분", "meaning": "상태 (RUN1:운영, STP2:중지)"},
                ],
            }
        },
    })
    return run


class TestRunLiterals:
    def test_sources_and_exclusions(self, tmp_path: Path) -> None:
        run = _run(tmp_path, definitions={
            "zzab01": {
                "kind": "현행", "manages": "서버 원장 — 구분 OPS9:운영, DEV9:개발",
                "key_columns": ["서버호스트명"],
            },
        })
        found = kn.run_literals(run)
        # SQL 문자열·숫자 · 주석 코드 열거 · 정의 글 코드 열거(라벨은 싣지 않는다)
        assert found == {"20260923", "7731", "RUN1", "STP2", "OPS9", "DEV9"}
        # 빠지는 것: LIMIT 수 · 가림 표지 · 질문에 있는 말 · 컬럼 이름 · 라벨
        assert not found & {"10000", "<가림>", "운영", "서버호스트명", "중지"}

    def test_load_union_and_missing_run(self, tmp_path: Path) -> None:
        _run(tmp_path)
        values, runs = kn.load_evidence_literals({_RUN_ID, "없는run"}, tmp_path / "results")
        assert runs == [_RUN_ID] and "20260923" in values


async def _validate(root: Path, literals: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    result = await kn.avalidate_dir(
        root, catalog=_SCHEMA, allowed=_ALLOWED, executor=None, sql_checker=asset_sql_checker,
        static_only=True, evidence_literals=literals,
    )
    return {(r["file"], r["id"]): r for r in result["results"]}


def _codes(found: dict[tuple[str, str], dict[str, Any]], key: tuple[str, str]) -> list[str]:
    return [i["code"] for i in found[key]["issues"]]


class TestValidate:
    async def test_rejects_with_location_only(self, tmp_path: Path) -> None:
        root = tmp_path / "k"
        _write(root / ka.GUIDE_FILE, {"version": 1, "items": [
            _item("g_hit", text="기준일은 `zzab01.기준년월일`로 '20260923'처럼 비교한다."),
            _item("g_sub", text="기준일은 `zzab01.기준년월일`로 120260923처럼 쓴다."),
            _item("g_ok", text="기준일은 `zzab01.기준년월일`로 같은 형식 문자열과 비교한다."),
        ]})
        _write(root / ka.DESCRIPTIONS_FILE, {"version": 1, "items": [
            _item("d_hit", table="zzab01", column="상태구분", text="RUN1이면 가동 중"),
        ]})
        found = await _validate(root, {"20260923", "RUN1"})
        assert _codes(found, (ka.GUIDE_FILE, "g_hit")) == [kn.EVIDENCE_LITERAL]
        assert found[(ka.GUIDE_FILE, "g_hit")]["ok"] is False
        assert _codes(found, (ka.GUIDE_FILE, "g_sub")) == []  # 토큰 경계 — 부분 문자열 아님
        assert _codes(found, (ka.GUIDE_FILE, "g_ok")) == []
        assert kn.EVIDENCE_LITERAL in _codes(found, (ka.DESCRIPTIONS_FILE, "d_hit"))
        text = repr(found)
        assert "20260923" not in text and "RUN1" not in text
        message = found[(ka.GUIDE_FILE, "g_hit")]["issues"][0]["message"]
        assert ka.GUIDE_FILE in message and "text" in message

    async def test_identifier_and_evidence_field_exempt(self, tmp_path: Path) -> None:
        root = tmp_path / "k"
        _write(root / ka.GUIDE_FILE, {"version": 1, "items": [
            _item("g_name", text="호스트는 `zzab01.서버호스트명`으로 찾는다."),
        ]})
        # 스키마 이름과 같은 값 · run ID와 같은 값은 거절 근거가 아니다
        found = await _validate(root, {"서버호스트명", _RUN_ID})
        assert _codes(found, (ka.GUIDE_FILE, "g_name")) == []

    async def test_withdrawn_item_also_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "k"
        _write(root / ka.GUIDE_FILE, {"version": 1, "items": [
            _item("g_old", status=ka.STATUS_WITHDRAWN, reason="'20260923' 조건이 틀렸다"),
            _item("g_old2", status=ka.STATUS_WITHDRAWN, reason="회귀"),
        ]})
        result = await kn.avalidate_dir(
            root, catalog=_SCHEMA, allowed=_ALLOWED, executor=None, static_only=True,
            evidence_literals={"20260923"},
        )
        assert [(r["id"], [i["code"] for i in r["issues"]]) for r in result["results"]] == [
            ("g_old", [kn.EVIDENCE_LITERAL])
        ]
        assert [w["id"] for w in result["withdrawn"]] == ["g_old2"]
        assert not kn.passed(result)

    def test_template_slot_names_exempt(self) -> None:
        item = {
            "id": "t1", "evidence": _RUN_ID,
            "sql": "SELECT 서버호스트명 FROM zzab01 WHERE 서버호스트명 = :host_kw "
                   "AND 기준년월일 BETWEEN :day_start AND :day_end",
            "slots": [
                {"name": "host_kw", "type": "hostname"},
                {"name": "day", "type": "date_range", "column": "zzab01.기준년월일"},
            ],
        }
        assert kn._evidence_literal_issues(
            ka.TEMPLATES_FILE, item, {"host_kw", "day_start", "day_end"}
        ) == []
        assert [i["code"] for i in kn._evidence_literal_issues(
            ka.TEMPLATES_FILE, {**item, "sql": item["sql"] + " AND 상태구분 = 'RUN1'"}, {"RUN1"}
        )] == [kn.EVIDENCE_LITERAL]


def test_run_validate_reads_evidence_run(tmp_path: Path, capsys: Any) -> None:
    _run(tmp_path)
    repo = tmp_path / "repo"
    _write(repo / ba.PROFILE_REL, {"allowed_tables": _ALLOWED, "table_definitions": {}})
    catalog = tmp_path / "schema.json"
    catalog.write_text(json.dumps(_SCHEMA, ensure_ascii=False), encoding="utf-8")
    root = tmp_path / "k"
    _write(root / ka.GUIDE_FILE, {"version": 1, "items": [
        _item("g_hit", text="숫자 7731을 넘는 행만 고른다."),
    ]})
    code = kn.run_validate(
        root, catalog_path=catalog, static_only=True, repo_root=repo,
        results_root=tmp_path / "results",
    )
    out = capsys.readouterr().out
    assert code == kn.EXIT_FAILED
    assert f"거절 {ka.GUIDE_FILE} g_hit: {kn.EVIDENCE_LITERAL}" in out
    assert f"근거 run 리터럴 대조: {_RUN_ID}" in out and "7731" not in out
