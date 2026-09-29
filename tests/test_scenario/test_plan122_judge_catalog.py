"""카탈로그 쪽 판정 계약 (plans/122 K-1 · C-4b · H-6 · 로더 하위 키 검증 · 판정 계약 지문).

고정하는 계약:
  1. K-1 `kind: probe` — 로드된다 · 정상군 소비처(합격 가능 가드 ·
     `Catalog.select(kinds=["normal"])` · 벤치 `sweep.load_normal_catalog`)에서 자동으로 빠진다 ·
     R군 반복(3회)에 들지 않는다.
  2. C-4b `requires_sources` — 군 값 상속 · 시나리오 선언이 이긴다 · 레지스트리 db_id ∪ 비SQL id
     밖은 로더 거부.
  3. H-6 턴 `auth: none` · 시나리오 `upload_generate` 파싱·검증.
  4. `file`·`result`·`stream`·`period_covers` 의 정의 밖 하위 키와 새 단언 키의 틀린 모양을
     로드 시점에 거부한다(PLAN_KEYS 선례 — 조용한 통과 방지).
  5. `judgement_digest` — 재로드 안정 · 단언 변경에 반응 · 제외 칸(notes·requires_sources 등)에
     불변.
전부 무과금이다(LLM·DB·서버 0).
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario.catalog import (
    FILE_KEYS,
    JUDGEMENT_DIGEST_EXCLUDED,
    JUDGEMENT_DIGEST_GROUP_EXCLUDED,
    NON_SQL_SOURCES,
    UPLOAD_GENERATE_MAX_BYTES,
    Catalog,
    CatalogError,
    Group,
    Scenario,
    judgement_digest,
    known_source_ids,
    load_catalog,
)
from tests.test_scenario.conftest import GOOD_GROUP, write
from tests.test_scenario.test_assertion_coverage import _normal_closed, _runnable

REGISTRY_IDS = {
    "polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "polestar", "cloud_portal", "itsm", "itam",
}


def _group_file(scenarios: str, header_extra: str = "") -> str:
    return (
        "version: 1\ngroup:\n  id: T\n  name: \"테스트군\"\n  latency_target_ms: 10000\n"
        f"{header_extra}scenarios:\n{scenarios}"
    )


def _scenario_yaml(sid: str, *, extra: str = "", expect: str = "{status: completed}",
                   turn_extra: str = "") -> str:
    return (
        f"  - id: {sid}\n    plans: [122]\n    title: \"t\"\n{extra}"
        "    turns:\n      - send: {query: \"서버 목록\"}\n"
        f"        expect: {expect}\n{turn_extra}"
    )


def _by_id(catalog: Catalog, scenario_id: str) -> Scenario:
    scenario = catalog.by_id(scenario_id)
    assert scenario is not None, scenario_id
    return scenario


def _load(scenario_dir: Path, profiles_path: Path, text: str, **kwargs: Any) -> Catalog:
    write(scenario_dir / "t.yaml", text)
    return load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path, **kwargs)


def _errors(scenario_dir: Path, profiles_path: Path, text: str, **kwargs: Any) -> list[str]:
    with pytest.raises(CatalogError) as exc:
        _load(scenario_dir, profiles_path, text, **kwargs)
    return exc.value.errors


# --- 1. K-1 probe ----------------------------------------------------------------

def test_probe_loads_and_leaves_normal_consumers(scenario_dir: Path, profiles_path: Path) -> None:
    """probe 는 로드되고, 정상군만 고르는 소비처에서 빠진다(판정 어휘는 그대로)."""
    catalog = _load(scenario_dir, profiles_path, _group_file(
        _scenario_yaml("T-01") + _scenario_yaml("T-02", extra="    kind: probe\n"),
    ))
    probe = catalog.by_id("T-02")
    assert probe is not None and probe.kind == "probe"
    assert not probe.is_r_group                       # R군 반복 3회 대상이 아니다
    assert [s.id for s in catalog.select(kinds=["normal"])] == ["T-01"]
    assert [s.id for s in _normal_closed(catalog)] == ["T-01"]
    assert [s.id for s in _runnable(catalog)] == ["T-01"]
    assert {s.id for s in catalog.select()} == {"T-01", "T-02"}   # 시나리오 run 은 돈다(관측)


def test_probe_leaves_bench_normal_catalog(
    scenario_dir: Path, profiles_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """벤치 `load_normal_catalog`(kinds=["normal"])도 probe 를 고르지 않는다."""
    from scripts.bench import sweep
    from scripts.scenario import catalog as sc_catalog

    catalog = _load(scenario_dir, profiles_path, _group_file(
        _scenario_yaml("T-01", extra="    env: closed\n")
        + _scenario_yaml("T-02", extra="    kind: probe\n    env: closed\n"),
    ))
    monkeypatch.setattr(sc_catalog, "load_catalog", lambda: catalog)
    assert [s.id for s in sweep.load_normal_catalog(env="closed").scenarios] == ["T-01"]


def test_catalog_probe_set_is_frozen() -> None:
    """probe 집합이 정확히 K-1 세 건이다 — 새 probe 가 조용히 늘지 않게 막는 동결 가드.

    종전(`test_catalog_has_no_probe_yet`)은 「probe 0건」을 단언했다. 카탈로그 v2 이관
    (plans/122 단계 4 K-1 · A-09·C-08·D-06)이 작업 트리에 랜딩해 의미가 바뀌었다.
    D-276 ③(G-17)은 R2 가 끝날 때까지
    카탈로그 이관을 **커밋하지 않을 뿐** 작업은 허용하고(「작업은 해도 되지만 R2 서버에 실리면 안
    된다」), 동결의 대상은 R2 서버에 실리는 카탈로그다. 그래서 이 가드는 이관 결과를 고정한다 —
    probe 는 정상군 모집단(합격 가능 가드 · 벤치 `load_normal_catalog`)에서 빠지므로, 새 probe 를
    더하는 것은 J-2 분모를 줄이는 판단이다. 늘리려면 근거와 함께 이 목록을 다시 연다.
    """
    assert sorted(s.id for s in load_catalog().scenarios if s.kind == "probe") == [
        "A-09", "C-08", "D-06"]


# --- 2. C-4b requires_sources -----------------------------------------------------

def test_requires_sources_inherit_and_override(scenario_dir: Path, profiles_path: Path) -> None:
    """군 헤더 값을 물려받고, 시나리오 선언이 있으면 그것이 이긴다."""
    catalog = _load(scenario_dir, profiles_path, _group_file(
        _scenario_yaml("T-01")
        + _scenario_yaml("T-02", extra="    requires_sources: [polestar, prometheus]\n"),
        header_extra="  requires_sources: [itam]\n",
    ))
    assert catalog.groups["T"].requires_sources == ("itam",)
    assert _by_id(catalog, "T-01").requires_sources == ["itam"]
    assert _by_id(catalog, "T-02").requires_sources == ["polestar", "prometheus"]


def test_null_scenario_declaration_still_inherits(scenario_dir: Path, profiles_path: Path) -> None:
    """시나리오의 `requires_sources: null` 은 선언이 아니다 — 군 값을 조용히 지우지 않는다."""
    catalog = _load(scenario_dir, profiles_path, _group_file(
        _scenario_yaml("T-01", extra="    requires_sources: null\n"),
        header_extra="  requires_sources: [itam]\n",
    ))
    assert _by_id(catalog, "T-01").requires_sources == ["itam"]


def test_no_declaration_means_empty(scenario_dir: Path, profiles_path: Path) -> None:
    """선언이 없으면 빈 목록(D-216 ③대로 실행·보류) — 레지스트리도 읽지 않는다."""
    catalog = _load(scenario_dir, profiles_path, _group_file(_scenario_yaml("T-01")),
                    registry_path=scenario_dir / "없는_레지스트리.yaml")
    assert catalog.groups["T"].requires_sources == ()
    assert catalog.scenarios[0].requires_sources == []


@pytest.mark.parametrize("header, scenario_extra, needle", [
    ("  requires_sources: [itamm]\n", "", "군 'T': requires_sources 의 알 수 없는 소스 ['itamm']"),
    ("", "    requires_sources: [promethus]\n",
     "T-01: requires_sources 의 알 수 없는 소스 ['promethus']"),
    ("  requires_sources: []\n", "", "requires_sources 는 소스 id 의 비지 않은 목록"),
    ("", "    requires_sources: itam\n", "requires_sources 는 소스 id 의 비지 않은 목록"),
    ("", "    requires_sources: [itam, itam]\n", "중복 없음"),
])
def test_bad_requires_sources_rejected(
    scenario_dir: Path, profiles_path: Path, header: str, scenario_extra: str, needle: str,
) -> None:
    """모르는 id 는 어느 run 에서도 활성이 아니라 그 시나리오가 영영 선택되지 않는다.

    로더가 거부한다.
    """
    errors = _errors(scenario_dir, profiles_path,
                     _group_file(_scenario_yaml("T-01", extra=scenario_extra), header_extra=header))
    assert any(needle in error for error in errors), errors


def test_inherited_unknown_is_reported_once(scenario_dir: Path, profiles_path: Path) -> None:
    """군에서 물려받은 모르는 id 는 군에서 한 번만 알린다."""
    errors = _errors(scenario_dir, profiles_path, _group_file(
        _scenario_yaml("T-01") + _scenario_yaml("T-02"),
        header_extra="  requires_sources: [nope]\n",
    ))
    assert [e for e in errors if "nope" in e] == [
        e for e in errors if e.startswith("군 'T'")
    ]


def test_known_source_ids_are_registry_plus_non_sql() -> None:
    """허용 id = 레지스트리 DB 전체 ∪ 비SQL 시스템(Prometheus)."""
    known = known_source_ids()
    assert REGISTRY_IDS <= known
    assert NON_SQL_SOURCES <= known
    # 2026-09-29 레지스트리 실측(7 DB · 비SQL 선언 0).
    assert known == REGISTRY_IDS | NON_SQL_SOURCES


def test_registry_non_sql_solution_is_accepted(tmp_path: Path) -> None:
    """레지스트리에 비SQL 시스템(`solutions[].backend` ≠ sql)이 생기면 그 코드도 받는다."""
    registry = write(tmp_path / "reg.yaml", (
        "databases:\n  - db_id: x_db\nsolutions:\n"
        "  - {code: polestar, backend: sql}\n  - {code: apm, backend: rest}\n"
    ))
    assert known_source_ids(registry) == {"x_db", "apm"} | NON_SQL_SOURCES


def test_unreadable_registry_with_declaration_is_an_error(
    scenario_dir: Path, profiles_path: Path,
) -> None:
    """선언이 있는데 레지스트리를 못 읽으면 검증 불가로 거부한다(조용히 통과시키지 않는다)."""
    errors = _errors(scenario_dir, profiles_path,
                     _group_file(_scenario_yaml("T-01", extra="    requires_sources: [itam]\n")),
                     registry_path=scenario_dir / "없는_레지스트리.yaml")
    assert any("레지스트리를 읽지 못했다" in error for error in errors), errors


# --- 3. H-6 auth · upload_generate ------------------------------------------------

def test_turn_auth_none_is_parsed(scenario_dir: Path, profiles_path: Path) -> None:
    """턴 `auth: none` 은 파싱되고, 생략은 None(러너 토큰)이다."""
    catalog = _load(scenario_dir, profiles_path, _group_file(
        _scenario_yaml("T-01", expect="{http_status: 401}", turn_extra="        auth: none\n")
        + _scenario_yaml("T-02"),
    ))
    assert _by_id(catalog, "T-01").turns[0].auth == "none"
    assert _by_id(catalog, "T-02").turns[0].auth is None


def test_turn_auth_other_value_rejected(scenario_dir: Path, profiles_path: Path) -> None:
    errors = _errors(scenario_dir, profiles_path, _group_file(
        _scenario_yaml("T-01", turn_extra="        auth: expired\n")))
    assert any("auth 'expired' 는 정의 밖" in error for error in errors), errors


def test_upload_generate_parses_without_upload(scenario_dir: Path, profiles_path: Path) -> None:
    """러너 생성 업로드는 `upload` 없이 file 엔드포인트를 쓸 수 있다."""
    catalog = _load(scenario_dir, profiles_path, _group_file(_scenario_yaml(
        "T-01", extra="    endpoint: file_stream\n"
                      "    upload_generate: {ext: .csv, size_bytes: 12582912}\n")))
    assert catalog.scenarios[0].upload_generate == {"ext": ".csv", "size_bytes": 12582912}
    assert catalog.scenarios[0].upload is None


@pytest.mark.parametrize("extra, needle", [
    ("    endpoint: file_stream\n    upload: a.xlsx\n"
     "    upload_generate: {ext: .xlsx, size_bytes: 10}\n",
     "upload 는 함께 쓰지 않는다"),
    ("    endpoint: file_stream\n    upload_generate: {ext: .pdf, size_bytes: 10}\n", "ext '.pdf'"),
    ("    endpoint: file_stream\n    upload_generate: {ext: .xls, size_bytes: 0}\n", "size_bytes"),
    ("    endpoint: file_stream\n"
     f"    upload_generate: {{ext: .xls, size_bytes: {UPLOAD_GENERATE_MAX_BYTES + 1}}}\n",
     "size_bytes"),
    ("    endpoint: file_stream\n    upload_generate: {ext: .xls}\n", "두 키만"),
    ("    endpoint: file_stream\n"
     "    upload_generate: {ext: .xls, size_bytes: 1, name: x}\n", "두 키만"),
    ("    upload_generate: {ext: .xls, size_bytes: 1}\n", "file|file_stream"),
])
def test_bad_upload_generate_rejected(
    scenario_dir: Path, profiles_path: Path, extra: str, needle: str,
) -> None:
    errors = _errors(scenario_dir, profiles_path, _group_file(_scenario_yaml("T-01", extra=extra)))
    assert any("upload_generate" in error and needle in error for error in errors), errors


def test_file_endpoint_still_needs_some_upload(scenario_dir: Path, profiles_path: Path) -> None:
    """upload·upload_generate 둘 다 없으면 종전처럼 거부한다."""
    for extra in ("    endpoint: file\n", "    endpoint: file\n    upload_generate: null\n"):
        text = _group_file(_scenario_yaml("T-01", extra=extra))
        errors = _errors(scenario_dir, profiles_path, text)
        assert any("'upload'(양식 파일 경로)가 없다" in error for error in errors), (extra, errors)


# --- 4. 하위 키 · 새 단언 키 모양 --------------------------------------------------

def _load_expect(scenario_dir: Path, profiles_path: Path, expect: str) -> Catalog:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        "        expect: {status: completed}", f"        expect: {expect}"))
    return load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)


def test_full_new_assertions_load(scenario_dir: Path, profiles_path: Path) -> None:
    """새 단언 키를 모두 쓴 올바른 선언은 로드된다."""
    catalog = _load_expect(scenario_dir, profiles_path, (
        "{sql_executed: false, node_path_must_not: [query_generator], "
        "status_any: [clarification, completed], form_memory_panel: present, "
        "stream: {ttft_ms: {max: 8000}, max_event_gap_ms: {max: 15000}}, "
        "dependency_notes_contains: [gate, bridge], "
        "result: {columns: [hostname, [cpu_avg, avg_cpu]], filled_columns: [hostname], "
        "value_range: {cpu_avg: [0, 100]}, unique_by: [hostname], allow_empty: false}, "
        "file: {sheets: [S], columns: [a, b], filled_rows: {min: 1}, filled_columns: [a], "
        "optional_columns: [b], value_range: {a: [0, 1.5]}, unique_by: [a], "
        "columns_differ: [[a, b]], empty_columns: [c], column_equals: {d: 인프라팀}, "
        "style_preserved: true}, "
        "period_covers: {from: '2026-07-01', to: '2026-08-01'}}"
    ))
    assert catalog.scenarios[0].turns[0].expect["result"]["allow_empty"] is False


def test_docx_file_assertion_loads(scenario_dir: Path, profiles_path: Path) -> None:
    catalog = _load_expect(scenario_dir, profiles_path, (
        "{file: {docx: {no_placeholders: true, styles_preserved: true, "
        "tables: [{index: 0, min_rows: 6, first_row: [서버명, IP주소]}]}}}"
    ))
    assert catalog.scenarios[0].turns[0].expect["file"]["docx"]["tables"][0]["index"] == 0


@pytest.mark.parametrize("expect, needle", [
    ("{file: {column: [a]}}", "file: 정의 밖 키 ['column']"),
    ("{file: {filled_rows: {max: 1}}}", "filled_rows"),
    ("{file: {columns: a}}", "columns 는"),
    ("{file: {value_range: {a: [100, 0]}}}", "value_range.a"),
    ("{file: {value_range: {a: [0]}}}", "value_range.a"),
    # plans/122 H-4 보완 — 별칭 목록 형식 · 머리글 위치.
    ("{file: {value_range: [{columns: [a], range: [100, 0]}]}}", "value_range 목록 항목"),
    ("{file: {value_range: [{columns: [], range: [0, 1]}]}}", "value_range 목록 항목"),
    ("{file: {value_range: [{columns: [a], range: [0, 1], extra: 1}]}}", "value_range 목록 항목"),
    ("{file: {value_range: []}}", "매핑 또는"),
    ("{result: {value_range: [{column: [a], range: [0, 1]}]}}", "value_range 목록 항목"),
    ("{file: {header_row: 0}}", "header_row 는 1 이상"),
    ("{file: {header_rows: [6, 5]}}", "오름차순"),
    ("{file: {header_rows: []}}", "오름차순"),
    ("{file: {header_row: 5, header_rows: [5, 6]}}", "함께 쓰지 않는다"),
    ("{file: {columns_differ: [a, b]}}", "columns_differ"),
    ("{file: {column_equals: [a]}}", "column_equals"),
    ("{file: {style_preserved: false}}", "style_preserved 는 true 만"),
    ("{file: {docx: {no_placeholders: true}, columns: [a]}}", "함께 쓰지 않는다"),
    ("{file: {docx: {placeholders: true}}}", "docx"),
    ("{file: {docx: {tables: [{index: -1, min_rows: 1}]}}}", "index"),
    ("{file: {docx: {tables: [{index: 0}]}}}", "min_rows·first_row 중 하나"),
    ("{file: {docx: {tables: [{index: 0, rows: 3}]}}}", "키만 쓴다"),
    ("{file: {}}", "file: 비지 않은 매핑"),
    ("{result: {column: [a]}}", "result: 정의 밖 키 ['column']"),
    ("{result: {columns: []}}", "columns 는"),
    ("{result: {allow_empty: 1}}", "allow_empty"),
    ("{result: {}}", "result: 비지 않은 매핑"),
    ("{stream: {ttfb_ms: {max: 1}}}", "stream: 정의 밖 키 ['ttfb_ms']"),
    ("{stream: {ttft_ms: {min: 1}}}", "ttft_ms 는 {max: 양수 ms}"),
    ("{stream: {ttft_ms: 100}}", "ttft_ms 는 {max: 양수 ms}"),
    ("{period_covers: {from: '2026-07-01'}}", "from·to 는 둘 다"),
    ("{period_covers: {from: '2026-07-01', to: '2026-07-01'}}", "from 이 to 보다 앞서야"),
    # plans/122 H-2 랜딩으로 relative 는 정의 안 키다 — 오타 키 거부만 고정한다.
    ("{period_covers: {relativ: last_month}}", "period_covers: 정의 밖 키 ['relativ']"),
    ("{sql_executed: 'no'}", "sql_executed"),
    ("{node_path_must_not: []}", "node_path_must_not"),
    ("{status_any: [complete]}", "status_any"),
    ("{dependency_notes_contains: [gates]}", "dependency_notes_contains"),
    ("{form_memory_panel: shown}", "form_memory_panel"),
])
def test_bad_new_assertion_shapes_rejected(
    scenario_dir: Path, profiles_path: Path, expect: str, needle: str,
) -> None:
    """틀린 선언은 실행 전(로드 시점)에 사유와 함께 거부된다 — 평가기가 조용히 무시하지 않게."""
    with pytest.raises(CatalogError) as exc:
        _load_expect(scenario_dir, profiles_path, expect)
    assert any(needle in error for error in exc.value.errors), exc.value.errors


def test_catalog_file_subkeys_are_all_defined() -> None:
    """현 카탈로그가 쓰는 `file` 하위 키가 전부 FILE_KEYS 안이다(2026-09-29 실측 columns ·
    filled_rows · optional_columns) — 로더가 현 카탈로그를 거부하지 않는다."""
    used = {
        key for s in load_catalog().scenarios for t in s.turns
        if isinstance(t.expect.get("file"), dict) for key in t.expect["file"]
    }
    assert used and used <= FILE_KEYS, used


# --- 5. 판정 계약 지문 ------------------------------------------------------------

def _replace_scenario(catalog: Catalog, sid: str, **changes: Any) -> Catalog:
    return Catalog(
        groups=dict(catalog.groups),
        scenarios=[dataclasses.replace(s, **changes) if s.id == sid else s
                   for s in catalog.scenarios],
        profiles=catalog.profiles,
    )


def test_digest_is_stable_across_reloads() -> None:
    """같은 카탈로그를 다시 읽으면 지문이 같다. 16자 16진수."""
    first, second = judgement_digest(load_catalog()), judgement_digest(load_catalog())
    assert first == second
    assert re.fullmatch(r"[0-9a-f]{16}", first)


def test_digest_ignores_scenario_order() -> None:
    """파일·목록 순서는 판정이 아니다 — id 순으로 정규화한다."""
    catalog = load_catalog()
    shuffled = Catalog(groups=dict(reversed(list(catalog.groups.items()))),
                       scenarios=list(reversed(catalog.scenarios)), profiles=catalog.profiles)
    assert judgement_digest(shuffled) == judgement_digest(catalog)


def test_digest_changes_when_expect_changes() -> None:
    """단언이 바뀌면 지문이 바뀐다 — 계약이 다른 run 끼리 비교하지 않게."""
    catalog = load_catalog()
    base = judgement_digest(catalog)
    target = _by_id(catalog, "B-01")
    turn = dataclasses.replace(target.turns[0],
                               expect={**target.turns[0].expect, "status": "error"})
    changed = _replace_scenario(catalog, "B-01", turns=[turn, *target.turns[1:]])
    assert judgement_digest(changed) != base
    assert judgement_digest(_replace_scenario(catalog, "B-01", response_modes=["refuse"])) != base


@pytest.mark.parametrize("field_name, value", [
    ("notes", "바뀐 메모"),
    ("title", "바뀐 제목"),
    ("plans", [1]),
    ("source_file", "moved.yaml"),
    ("requires_sources", ["itam"]),
])
def test_digest_ignores_excluded_fields(field_name: str, value: Any) -> None:
    """제외 칸(선택 전용·표시용)을 바꿔도 지문은 같다."""
    catalog = load_catalog()
    assert field_name in JUDGEMENT_DIGEST_EXCLUDED
    changed = _replace_scenario(catalog, "B-01", **{field_name: value})
    assert judgement_digest(changed) == judgement_digest(catalog)


def test_digest_group_fields() -> None:
    """군 헤더 — 이름·출처·요구 소스는 빼고, 등급 정책 확정·성능 목표는 넣는다."""
    catalog = load_catalog()
    base = judgement_digest(catalog)

    def with_group(**changes: Any) -> Catalog:
        groups = dict(catalog.groups)
        groups["B"] = dataclasses.replace(groups["B"], **changes)
        return Catalog(groups=groups, scenarios=catalog.scenarios, profiles=catalog.profiles)

    assert judgement_digest(with_group(name="x", source="y", requires_sources=("itam",))) == base
    assert judgement_digest(with_group(policy_confirmed=True)) != base
    assert judgement_digest(with_group(latency_target_ms=1)) != base


def test_digest_exclusions_name_real_fields() -> None:
    """제외 목록의 이름이 실제 칸이다 — 이름이 틀리면 조용히 지문에 남는다."""
    assert set(JUDGEMENT_DIGEST_EXCLUDED) <= {f.name for f in dataclasses.fields(Scenario)}
    assert set(JUDGEMENT_DIGEST_GROUP_EXCLUDED) <= {f.name for f in dataclasses.fields(Group)}
