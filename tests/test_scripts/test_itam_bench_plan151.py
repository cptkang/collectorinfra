"""ITAM 벤치 반출·판독 보강 (plans/151 W5 · W6).

여기서 고정하는 것:
- W5 (1) 결과 열 출처: `GROUP BY 별칭`·`ORDER BY 별칭`을 「AS 생략 별칭」으로 읽지 않는다 — 앞 주석
  (괄호 포함)·문자열 리터럴 안 `--`·`/*`가 해석을 흔들지 않는다(F6 — 6회차 104 꼴)
- W5 (2) 측정 수신 칸: 선별 다리 테이블 이름·폴백 사유 코드(모양 밖은 뺀다) → 턴 칸 합집합
- W5 (3) 리포트 §0 「테이블 정의 출처」(출처별 수 · 시드 대비 하향 · 경고)·
  「교차 질의 식별 키 테이블」
- W5 (4) 리포트 §3 「선별 밖 사용 턴」 계수·목록
- W6 closed 정책 92 사용량·사용률 6칸(general · 스키마 실존)

입력은 값 없는 재구성 입력이다(테이블·컬럼 이름만 — D-308 · 반출 원본 results/ 미사용).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.itam_bench import CLOSED_POLICY_PATH
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench import report as rp
from scripts.itam_bench._serve import schema_context_record

_ROOT = Path(__file__).resolve().parents[2]
_CLOSED = _ROOT / "testdata" / "itam_bench" / "closed"
_KNOWN = ["그룹경로내용", "구성항목설명내용"]
_SELECT_104 = (
    "SELECT \n    COALESCE(s.`그룹경로내용`, s.`구성항목설명내용`) AS service_name,\n"
    "    COUNT(*) AS server_count\nFROM `tcdmsif72` AS s\n"
)


# --- W5 (1) 결과 열 출처 ------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # 위임 재구성(값 0) — GROUP BY 위치 번호
        "-- 서비스(그룹) 기준 집계\nSELECT COALESCE(s.그룹경로내용, s.구성항목설명내용) "
        "AS service_name, COUNT(*) AS server_count FROM tcdmsif72 s GROUP BY 1",
        # 6회차 104 꼴 — 별칭으로 묶고 줄 끝에서 끝난다(가짜 3칸의 실제 원인)
        f"-- 서비스(그룹) 기준 집계\n{_SELECT_104}GROUP BY service_name\n"
        "ORDER BY server_count DESC\nLIMIT 10000;",
        f"/* 서비스(그룹) */\n{_SELECT_104}GROUP BY service_name",
        f"{_SELECT_104}GROUP BY service_name",
        f"{_SELECT_104}ORDER BY service_name, server_count",
    ],
)
def test_alias_reference_after_by_is_not_an_expression(sql: str) -> None:
    sources = rd.resolve_result_columns(["service_name", "server_count"], [sql], _KNOWN)
    assert sources == {"service_name": _KNOWN, "server_count": [rd.COMPUTED]}


def test_literal_dashes_and_comment_markers_are_kept() -> None:
    sql = (
        "SELECT s.그룹경로내용 AS path FROM tcdmsif72 s "
        "WHERE s.구성항목설명내용 LIKE '%--%' OR s.구성항목설명내용 = '/* x */' -- 끝(주석)"
    )
    assert rd.resolve_result_columns(["path"], [sql], _KNOWN) == {"path": ["그룹경로내용"]}


def test_as_omitted_alias_still_resolves() -> None:
    sql = "SELECT s.그룹경로내용 svc FROM tcdmsif72 s GROUP BY svc"
    assert rd.resolve_result_columns(["svc"], [sql], _KNOWN) == {"svc": ["그룹경로내용"]}


# --- W5 (2) 측정 수신 칸 ------------------------------------------------------------


def _state(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        "thread_id": "t-151",
        "active_db_id": "itam",
        "schema_info": {"tables": {}},
        "table_selection": {"itam": selection},
    }


def test_capture_carries_bridged_names_and_reason_code() -> None:
    selection = {
        "mode": "definitions", "source": "lexical", "candidates": 99,
        "selected": ["tcdmsif46", "tcdmsif71"], "bridged": ["tcdmsif71"], "reason": "zero_valid",
    }
    entry = schema_context_record("task_pipeline_state", _state(selection))["dbs"]["itam"]
    assert entry["selection_bridged"] == ["tcdmsif71"]
    assert entry["selection_reason"] == "zero_valid"


def test_capture_drops_off_shape_values() -> None:
    selection = {"source": "llm", "selected": [], "bridged": ["값 섞인 이름", 3, "app.delta"],
                 "reason": "LLM 호출 예외(RuntimeError: boom)"}
    entry = schema_context_record("task_pipeline_state", _state(selection))["dbs"]["itam"]
    assert entry["selection_bridged"] == ["app.delta"]
    assert entry["selection_reason"] is None


def test_turn_entry_merges_selection_and_omits_when_absent() -> None:
    first = {"dbs": {"itam": {"selection_bridged": ["tcdmsif71"], "selection_reason": None}}}
    second = {"dbs": {"itam": {"selection_bridged": ["tcdmsif46", "tcdmsif71"],
                               "selection_reason": "llm_error"}}}
    entry = jd.prompt_by_db([first, second])["itam"]
    assert entry["selection_bridged"] == ["tcdmsif71", "tcdmsif46"]
    assert entry["selection_reasons"] == ["llm_error"]
    bare = jd.prompt_by_db([{"dbs": {"itam": {"selection_bridged": [], "selection_reason": None}}}])
    assert "selection_bridged" not in bare["itam"] and "selection_reasons" not in bare["itam"]


def test_gate_passes_selection_fields() -> None:
    policy = cat.load_policy(CLOSED_POLICY_PATH)
    gate = rd.LeakGate(policy=policy, vault=rd.PiiVault.from_policy(policy), user_values={})
    record = {"id": "ITAM-101", "schema_context": {"dbs": {"itam": {
        "selection_bridged": ["tcdmsif71"], "selection_reasons": ["zero_valid"],
    }}}}
    assert gate.check({"trace.jsonl": json.dumps(record, ensure_ascii=False) + "\n"}) == []


# --- W5 (3) 리포트 §0 ---------------------------------------------------------------


def _catalog(definitions: dict[str, Any], entity_table: str | None = "tcdmsif76") -> dict[str, Any]:
    approved: dict[str, Any] = {"source": "manual", "table_definitions": definitions}
    if entity_table is not None:
        approved["entity_keys"] = {"entity": "server", "table": entity_table, "keys": []}
    return {"source": "schema_cache", "assets": {}, "approved_profile": approved}


_DEFS = {
    "tcdmsif72": {"manages": "가", "origin": "comment"},
    "tcdmsif79": {"manages": "나", "origin": "comment"},
    "tcdmsif80": {"manages": "다", "origin": "import"},
    "tcdmsxx01": {"manages": "라", "origin": "comment"},
}


def _section0(text: str) -> str:
    return text.split("## 0.")[1].split("## 1.")[0]


def test_section0_definition_origins_and_downgrade_warning() -> None:
    text = rp.render_report(
        {"run_id": "r"}, _catalog(_DEFS), [], seed_tables={"TCDMSIF72", "tcdmsif79", "tcdmsif80"}
    )
    head = _section0(text)
    assert "| 테이블 정의 출처 | comment 3 · import 1 · 시드 대비 하향 2 |" in head
    assert "| 교차 질의 식별 키 테이블 | tcdmsif76 |" in head
    assert "> 경고 — closed 시드에 정의가 있는 테이블 2개" in head


def test_section0_downgrade_matches_schema_qualified_keys() -> None:
    defs = {"ITAM.tcdmsif72": {"manages": "가", "origin": "comment"},
            "itam.TCDMSIF80": {"manages": "다", "origin": "import"}}
    head = _section0(rp.render_report(
        {"run_id": "r"}, _catalog(defs), [], seed_tables={"tcdmsif72", "other.tcdmsif80"}
    ))
    assert "| 테이블 정의 출처 | comment 1 · import 1 · 시드 대비 하향 1 |" in head
    assert "> 경고 — closed 시드에 정의가 있는 테이블 1개" in head


def test_section0_no_warning_when_nothing_downgraded() -> None:
    head = _section0(rp.render_report(
        {"run_id": "r"}, _catalog(_DEFS, entity_table=None), [], seed_tables={"tcdmsif80"}
    ))
    assert "시드 대비 하향 0" in head and "경고" not in head
    assert "| 교차 질의 식별 키 테이블 | — |" in head


def test_section0_without_seed_omits_downgrade() -> None:
    head = _section0(rp.render_report({"run_id": "r"}, _catalog(_DEFS), []))
    assert "| 테이블 정의 출처 | comment 3 · import 1 |" in head


def test_section0_absent_without_definitions() -> None:
    text = rp.render_report({"run_id": "r"}, {"source": "schema_cache", "assets": {}}, [])
    assert "## 0." not in text and "테이블 정의 출처" not in text


def test_seed_definition_tables_reads_closed_seed() -> None:
    names = rp.seed_definition_tables()
    assert names is not None and len(names) == 108 and "tcdmsif72" in names
    assert rp.seed_definition_tables(_ROOT / "no_such_seed.yaml") is None


# --- W5 (4) 리포트 §3 ---------------------------------------------------------------


def _record(scenario: str, used: list[str], presented: list[str] | None) -> dict[str, Any]:
    return {
        "id": scenario, "turn": 1, "repeat": 0, "status": "ok",
        "sql_analysis": {"tables": used},
        "schema_context": None if presented is None else {"presented_tables": presented},
    }


def test_section3_outside_selection_turns() -> None:
    records = [
        _record("ITAM-101", ["tcdmsif72"], ["tcdmsif46", "tcdmsif71"]),
        _record("ITAM-102", ["TCDMSIF80"], ["tcdmsif80"]),
        _record("ITAM-103", ["tcdmsif72", "tcdmsif79"], ["tcdmsif72"]),
        _record("ITAM-104", ["tcdmsif72"], None),  # 맥락 없음 — 세지 않는다
    ]
    section = rp.render_report({"run_id": "r"}, {}, records).split("## 3.")[1].split("## 4.")[0]
    assert "| 선별 밖 사용 턴 | 2 |" in section
    assert "- `ITAM-101` 턴 1 · 반복 0: `tcdmsif72`" in section
    assert "- `ITAM-103` 턴 1 · 반복 0: `tcdmsif79`" in section
    assert "ITAM-102" not in section and "ITAM-104" not in section


def test_section3_zero_has_no_list() -> None:
    section = rp.render_report(
        {"run_id": "r"}, {}, [_record("ITAM-102", ["tcdmsif80"], ["tcdmsif80"])]
    ).split("## 3.")[1].split("## 4.")[0]
    assert "| 선별 밖 사용 턴 | 0 |" in section and "선별 밖 사용 턴 —" not in section


# --- W6 closed 정책 92 6칸 ------------------------------------------------------------

_W6_92 = (
    "CPU사용량", "CPU사용률", "가상화서버메모리사용량", "메모리사용률", "스토리지사용량",
    "스토리지사용률",
)


def test_w6_92_usage_columns_general_and_exist() -> None:
    policy = cat.load_policy(CLOSED_POLICY_PATH)
    assert {c: policy.tables["tcdmsif92"][c] for c in _W6_92} == dict.fromkeys(_W6_92, "general")
    schema = json.loads((_CLOSED / "itam_schema.json").read_text(encoding="utf-8"))["schema"]
    assert set(_W6_92) <= {c["name"] for c in schema["tables"]["tcdmsif92"]["columns"]}
    assert sum(len(c) for c in policy.tables.values()) == 93
