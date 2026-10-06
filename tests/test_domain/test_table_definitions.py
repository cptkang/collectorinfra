"""plans/139 W3 — 테이블 정의 자산 형식·검증(`src.domain.table_definitions`) · 테이블 단위 병합
(`src.domain.profile_merge`) · 준비도 C11 경고(`src.domain.db_readiness`).

확인하는 계약:
- 정의 1건 검증: 필수 `manages`·`origin` · `kind` 허용 값 · `key_columns` 실존·상한 · `related` 상대
  실존·길이 · 금지 텍스트(중괄호 · 코드 펜스 · SQL 키워드 백틱 구간 · 제어문자) · 공백 정규화
- 가져오기 파서: 최상위 `tables:` · 알려진 필드만 · `allowed: false` 무시 · 출처 `import`
- 시드(내부망 108테이블 · G-7 git 추적)를 시드 스키마로 검증하면 108개 모두 유효 · 오류 0
- 병합: base `manual` 보존 · 초안 비-manual 항목은 base 비-manual 교체 · 초안에만 있는 테이블 추가 ·
  base에만 있는 테이블 유지 · 키가 없는 프로필(폴스타 4종 · test_db)은 병합 결과 불변
- C11: 조회 대상 > 10이고 정의 0 또는 80% 미만이면 경고(정보 항목 · 필수·권장 집계 불변)
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.domain import profile_merge
from src.domain.db_readiness import ReadinessInputs, evaluate_readiness
from src.domain.profile_merge import merge_profile, profile_field_diff
from src.domain.table_definitions import (
    KEY_COLUMNS_MAX,
    KINDS,
    MANAGES_MAX_CHARS,
    ORIGINS,
    PROFILE_KEY,
    defined_table_count,
    has_table_definitions,
    parse_import_document,
    validate_table_definitions,
)

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "testdata" / "itam_bench" / "closed"

COLUMNS: dict[str, list[str]] = {
    "app.t_srv": ["hostNm", "ipAddr", "useYn", "regYmd"],
    "app.t_hw": ["hostNm", "serial"],
    "t_code": ["codeCd", "codeNm"],
}


def _entry(**fields: Any) -> dict[str, Any]:
    return {"manages": "서버 목록을 관리한다.", "origin": "import", **fields}


def _check(entry: Any, table: str = "t_srv") -> tuple[dict[str, Any], list[str]]:
    valid, errors = validate_table_definitions({table: entry}, COLUMNS)
    return next(iter(valid.values()), {}), next(iter(errors.values()), [])


class TestValidateEntry:
    def test_normalizes_names_and_text(self):
        valid, errors = validate_table_definitions({
            "T_SRV": _entry(
                group=" 서버 ", kind="현행", manages="서버  목록을\n 관리한다.",
                key_columns=["HOSTNM", "ipAddr", "hostNm"],
                related={"t_hw": "hostNm으로 연결"}, notes="`regYmd`는 문자열 날짜",
            ),
        }, COLUMNS)

        assert errors == {}
        assert valid == {"app.t_srv": {
            "group": "서버", "kind": "현행", "manages": "서버 목록을 관리한다.",
            "key_columns": ["hostNm", "ipAddr"], "related": {"app.t_hw": "hostNm으로 연결"},
            "notes": "`regYmd`는 문자열 날짜", "origin": "import",
        }}
        assert list(valid["app.t_srv"]) == [
            "group", "kind", "manages", "key_columns", "related", "notes", "origin",
        ]

    def test_optional_fields_omitted(self):
        entry, errors = _check({"manages": "코드", "origin": "llm"}, "t_code")
        assert errors == [] and entry == {"manages": "코드", "origin": "llm"}

    @pytest.mark.parametrize(("fields", "fragment"), [
        ({"manages": ""}, "manages이(가) 비었습니다"),
        ({"manages": None}, "manages이(가) 비었습니다"),
        ({"manages": 3}, "문자열"),
        ({"manages": "가" * (MANAGES_MAX_CHARS + 1)}, f"{MANAGES_MAX_CHARS}자 이하"),
        ({"manages": "값 {placeholder}"}, "중괄호"),
        ({"manages": "```sql\nSELECT 1\n```"}, "코드 펜스"),
        ({"manages": "`hostNm 닫히지 않음"}, "닫히지 않은 백틱"),
        ({"manages": "`SELECT * FROM t` 로 조회"}, "SQL 키워드(SELECT)"),
        ({"notes": "`drop table x`"}, "SQL 키워드(DROP)"),
        ({"manages": "제어\x00문자"}, "제어문자"),
        ({"kind": "현재"}, "kind는"),
        ({"key_columns": ["nope"]}, "테이블에 없는 key_columns: nope"),
        ({"key_columns": "hostNm"}, "목록이어야"),
        ({"key_columns": [""]}, "비지 않은 문자열"),
        ({"related": {"t_ghost": "x"}}, "스키마에 없는 related 테이블: t_ghost"),
        ({"related": {"t_hw": "가" * 101}}, "100자 이하"),
        ({"related": ["t_hw"]}, "매핑이어야"),
        ({"notes": "가" * 301}, "300자 이하"),
        ({"origin": "human"}, "origin은"),
    ])
    def test_rejections(self, fields, fragment):
        entry, errors = _check(_entry(**fields))
        assert entry == {}
        assert any(fragment in e for e in errors), errors

    def test_key_columns_limit(self):
        columns = {"t_wide": [f"c{i}" for i in range(KEY_COLUMNS_MAX + 2)]}
        valid, errors = validate_table_definitions(
            {"t_wide": _entry(key_columns=columns["t_wide"])}, columns,
        )
        assert valid == {} and f"{KEY_COLUMNS_MAX}개 이하" in errors["t_wide"][0]

    def test_identifier_backticks_allowed(self):
        entry, errors = _check(_entry(notes="`update_time`·`selected_yn`은 감사 컬럼"))
        assert errors == [] and entry["notes"].startswith("`update_time`")

    def test_unknown_and_duplicate_tables(self):
        valid, errors = validate_table_definitions({
            "t_ghost": _entry(), "t_srv": _entry(), "APP.T_SRV": _entry(),
            "t_code": "문자열",
        }, COLUMNS)
        assert list(valid) == ["app.t_srv"]
        assert errors["t_ghost"] == ["스키마에 없는 테이블입니다"]
        assert "두 번 정의" in errors["APP.T_SRV"][0]
        assert errors["t_code"] == ["정의가 매핑이 아닙니다"]

    def test_kinds_and_origins_are_the_designed_lists(self):
        assert KINDS == (
            "현행", "수집적재", "수집이력", "변경이력", "신청·처리", "점검", "매핑", "기준·코드",
            "로그", "게시판", "통계", "설정",
        )
        assert ORIGINS == ("import", "llm", "comment", "manual")


class TestImportAndHelpers:
    def test_parse_keeps_known_fields_and_ignores_allowed(self):
        raw = parse_import_document({"db_id": "x", "groups": {"a": "b"}, "tables": {
            "t_srv": {"group": "서버", "manages": "서버", "allowed": False, "origin": "manual",
                      "extra": 1},
            "t_bad": "문자열",
        }})
        assert raw == {
            "t_srv": {"group": "서버", "manages": "서버", "origin": "import"},
            "t_bad": "문자열",
        }

    @pytest.mark.parametrize("document", [None, [], {"tables": []}, {"rows": {}}])
    def test_parse_requires_tables_mapping(self, document):
        with pytest.raises(ValueError, match="tables"):
            parse_import_document(document)

    def test_has_definitions_and_coverage(self):
        assert has_table_definitions({PROFILE_KEY: {"t": {"manages": "x"}}}) is True
        assert has_table_definitions({PROFILE_KEY: {}}) is False
        assert has_table_definitions({}) is False and has_table_definitions(None) is False
        defs = {"app.t_srv": {"manages": "x"}, "t_hw": {"manages": "y"}, "t_x": "bad"}
        assert defined_table_count(defs, ["t_srv", "T_HW", "t_x", "t_code"]) == 2
        assert defined_table_count(None, ["t_srv"]) == 0


def _seed_columns() -> dict[str, list[str]]:
    schema = json.loads((SEED_DIR / "itam_schema.json").read_text(encoding="utf-8"))
    return {t: [c["name"] for c in v["columns"]] for t, v in schema["schema"]["tables"].items()}


def test_seed_108_tables_validate_without_errors():
    """시드 정의(108테이블)를 시드 스키마로 검증하면 전부 유효 — 오류 0(G-7 실제 모양)."""
    document = yaml.safe_load((SEED_DIR / "table_definitions.yaml").read_text(encoding="utf-8"))
    raw = parse_import_document(document)
    valid, errors = validate_table_definitions(raw, _seed_columns())

    assert len(raw) == 108
    assert errors == {}
    assert len(valid) == 108
    assert all(item["origin"] == "import" for item in valid.values())
    assert sum(1 for t in document["tables"].values() if t.get("allowed") is False) == 9


# ─── 병합 ───────────────────────────────────────────────────────


def _def(manages: str, origin: str) -> dict[str, str]:
    return {"manages": manages, "origin": origin}


class TestMerge:
    def test_table_unit_merge_preserves_manual(self):
        base = {"source": "manual", "allowed_tables": ["t_a"], PROFILE_KEY: {
            "t_a": _def("사람 a", "manual"),
            "t_b": _def("가져온 b", "import"),
            "t_only_base": _def("base만", "llm"),
        }}
        draft = {PROFILE_KEY: {
            "T_A": _def("llm a", "llm"),
            "t_b": _def("llm b", "llm"),
            "t_new": _def("주석 new", "comment"),
        }}

        merged = merge_profile(base, draft, local_sandbox=False)

        assert merged[PROFILE_KEY] == {
            "t_a": _def("사람 a", "manual"),
            "t_b": _def("llm b", "llm"),
            "t_only_base": _def("base만", "llm"),
            "t_new": _def("주석 new", "comment"),
        }
        assert base[PROFILE_KEY]["t_b"]["manages"] == "가져온 b"  # 입력 불변
        diff = {d["path"]: d["change"] for d in profile_field_diff(base, merged)}
        assert diff == {f"{PROFILE_KEY}[t_b]": "changed", f"{PROFILE_KEY}[t_new]": "added"}

    def test_draft_manual_edit_replaces_base_manual(self):
        base = {PROFILE_KEY: {"t_a": _def("이전 사람 값", "manual")}}
        merged = merge_profile(
            base, {PROFILE_KEY: {"t_a": _def("검토 화면에서 고친 값", "manual")}},
            local_sandbox=False,
        )
        assert merged[PROFILE_KEY]["t_a"]["manages"] == "검토 화면에서 고친 값"

    def test_key_added_when_base_has_none_and_kept_when_draft_has_none(self):
        merged = merge_profile({"source": "manual"}, {PROFILE_KEY: {"t": _def("x", "llm")}},
                               local_sandbox=False)
        assert merged[PROFILE_KEY] == {"t": _def("x", "llm")}
        kept = merge_profile({PROFILE_KEY: {"t": _def("x", "manual")}}, {"patterns": []},
                             local_sandbox=False)
        assert kept[PROFILE_KEY] == {"t": _def("x", "manual")}

    def test_non_mapping_human_value_untouched(self):
        base = {PROFILE_KEY: "사람이 적은 메모"}
        merged = merge_profile(base, {PROFILE_KEY: {"t": _def("x", "llm")}},
                               local_sandbox=False)
        assert merged[PROFILE_KEY] == "사람이 적은 메모"


UNCHANGED_PROFILES = [
    "polestar.yaml", "polestar_b0.yaml", "polestar_cm_gp.yaml", "polestar_cm_yd.yaml",
    "test_db.yaml",
]


@pytest.mark.parametrize("name", UNCHANGED_PROFILES)
def test_profiles_without_key_merge_identically(name, monkeypatch):
    """키가 없는 프로필은 새 규칙이 있든 없든 병합 결과·직렬화·diff가 같다(비트 불변)."""
    base = yaml.safe_load((ROOT / "config" / "db_profiles" / name).read_text(encoding="utf-8"))
    assert PROFILE_KEY not in base
    drafts = [
        {},
        {k: base[k] for k in ("patterns", "code_values", "query_guide") if k in base},
        {"code_labels": {"x.y": {"1": "하나"}}, "relationships": [{"from": "a.b", "to": "c.d"}]},
    ]
    with_rule = [merge_profile(base, d, local_sandbox=False) for d in drafts]
    monkeypatch.setattr(profile_merge, "TABLE_UNIT_KEYS", ())
    without_rule = [merge_profile(base, d, local_sandbox=False) for d in drafts]

    for new, old in zip(with_rule, without_rule):
        assert PROFILE_KEY not in new
        assert yaml.safe_dump(new, allow_unicode=True, sort_keys=False) == yaml.safe_dump(
            old, allow_unicode=True, sort_keys=False
        )
        assert profile_field_diff(base, new) == profile_field_diff(base, old)


# ─── 준비도 C11 ──────────────────────────────────────────────────


def _inputs(**overrides: Any) -> ReadinessInputs:
    env = "http://mcp.test:9099/sse"
    base = ReadinessInputs(
        db_id="sample_db", mcp_available=True, mcp_listed=True, mcp_type="postgresql",
        mcp_health=True,
        registry_entry={"db_id": "sample_db", "enabled": True, "engine": "postgresql",
                        "description": "샘플 업무 DB", "db_schema": "", "zone": "zone_a"},
        cache_exists=True, cache_table_count=12, snapshot_table_count=12, cache_env=env,
        structure_source="approved", approved_env=env, current_env=env,
        description_scope_tables=12, description_applied_tables=10,
        description_excluded_tables=2, seed_loaded_count=0, llm_synonym_applied_count=5,
        default_allowed_db_ids=(),
    )
    return replace(base, **overrides)


def _c11(**overrides: Any) -> Any:
    report = evaluate_readiness(_inputs(**overrides))
    return next(item for item in report.items if item.code == "C11"), report


@pytest.mark.parametrize(("allowed", "defined", "ok"), [
    (10, 0, None),     # 조회 대상 10개 이하 — 해당 없음
    (11, 0, False),    # 정의 없음 → 경고
    (20, 15, False),   # 75% < 80% → 경고
    (20, 16, True),    # 80% → 충족
    (99, 99, True),
])
def test_c11_table_definition_warning(allowed, defined, ok):
    item, report = _c11(allowed_table_count=allowed, defined_allowed_count=defined)
    assert item.grade == "info" and item.ok is ok
    if ok is False:
        assert "경고만" in item.detail and "테이블 정의" in item.detail
    baseline = evaluate_readiness(_inputs())
    assert report.ready == baseline.ready
    assert report.to_dict()["summary"] == baseline.to_dict()["summary"]
    assert (report.required_total, report.recommended_total) == (7, 2)
