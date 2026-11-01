"""plans/201 W4 — 조사 결과 영역별 판정(`duty_view`) · 인계 문구 (D-354).

실 디스패처(가짜 도구·가짜 LLM)로 플레이북을 돌려 브리핑 선택 키 `duty_view`를 검증한다.
- 플래그 off = 키 없음 · 브리핑 비트 동일(on은 키 하나만 더한다) · 매핑 파일을 읽지 않는다
- 단계 없는 잡(플레이북 미실행 · 수집 단계 0)은 키 없음
- host.cpu · was.heap · was.sql · 단계 실패 · 단계 생략 픽스처의 4상태 · 사유
- 근거 없는 「정상」 0건 · 「확인 안 됨」 사유 필수
- 커넥션 계열 가설 우선 · 외부 연계 → was_mw + 근거 문구 · R-E 상위 프로세스 종류 → 영역
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from sre_agent.application import investigation_dispatcher as dispatcher_mod
from sre_agent.domain.duty_view import (
    ABNORMAL,
    NORMAL,
    STATUSES,
    SUSPECT,
    UNVERIFIED,
    build_duty_view,
    parse_duty_area_map,
    process_kind,
)
from sre_agent.infrastructure.duty_area_loader import (
    DEFAULT_DUTY_AREA_LAYERS_PATH,
    load_duty_area_map,
)
from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import FakeLLM, _comparable, alarm_of, build, make_settings, run_job

PLAN200 = Path(__file__).resolve().parent / "fixtures" / "plan200"
MAPPING = load_duty_area_map()
CODES = [a.code for a in MAPPING.areas]


def _fixture(name: str) -> dict:
    path = PLAN200 / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return _load(name)


def _alarm(name: str) -> dict:
    fx = _fixture(name)
    alarm = copy.deepcopy(fx["alarm"])
    if not fx["expected"].get("polestar_unresolved"):
        alarm.setdefault("meta", {})["episode"] = {
            "episode_id": "ep", "infra_db_ids": ["polestar"], "members": [],
        }
    return alarm


def _sql_alarm() -> dict:
    """was.sql — latency_sql 알람의 이벤트 유형을 SQL 패턴으로 바꾼다."""
    alarm = alarm_of("latency_sql")
    alarm["event"]["alarmName"] = "SQL_TOOMANY_FETCH"
    alarm["meta"]["hints"]["event_type"] = "SQL_TOOMANY_FETCH"
    return alarm


def _run(name: str, *, on: bool = True, alarm: dict | None = None) -> Any:
    settings = make_settings(sre_duty_view_enabled=on)
    caller = FakeCaller(_fixture(name), strict=False)
    return run_job(build(settings, caller, llm=FakeLLM()), alarm or _alarm(name))


def _rows(job: Any) -> dict[str, dict]:
    dv = job.briefing["duty_view"]
    assert [r["code"] for r in dv["areas"]] == CODES  # 선언 순서
    return {r["code"]: r for r in dv["areas"]}


def _invariants(dv: dict) -> None:
    """모든 행의 공통 계약 — 4상태 · 정상은 인용 · 확인 안 됨은 사유."""
    for row in dv["areas"]:
        assert row["status"] in STATUSES
        assert row["status_label"] == MAPPING.status_labels[row["status"]]
        assert row["label"] == MAPPING.label(row["code"])
        if row["status"] == NORMAL:
            assert row["signals"], row
            assert all("←" in s for s in row["signals"]), row  # 도구 인용 표기
            assert row["steps"], row
        if row["status"] == UNVERIFIED:
            assert row.get("reason"), row
    assert isinstance(dv["handoff"], str) and dv["handoff"]


_TIMING_KEY = re.compile(r"(?:_s|_ms|elapsed\w*|duration\w*|^timings)$")


def _untimed(value: Any) -> Any:
    """실행마다 달라지는 소요 시간 값을 뺀다(구조·문구 비교용)."""
    if isinstance(value, dict):
        return {k: _untimed(v) for k, v in value.items() if not _TIMING_KEY.search(str(k))}
    if isinstance(value, list):
        return [_untimed(v) for v in value]
    return value


def _diff_paths(a: Any, b: Any, path: str = "") -> list[str]:
    """두 값이 다른 경로 목록(실패 메시지용)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) ^ set(b), key=str):
            out.append(f"{path}.{k}: 한쪽에만 있음")
        for k in sorted(set(a) & set(b), key=str):
            out += _diff_paths(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [p for i, (x, y) in enumerate(zip(a, b)) for p in _diff_paths(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"[:300]]


# ── 플래그 off · 키 없음 ────────────────────────────────────────────────────


def test_off_no_key_and_on_only_adds_key(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    real = dispatcher_mod.load_duty_area_map

    def spy(*a: Any, **kw: Any) -> Any:
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(dispatcher_mod, "load_duty_area_map", spy)
    off = _run("latency_sql", on=False)
    assert calls == []  # off는 매핑 파일을 읽지 않는다
    on = _run("latency_sql", on=True)
    assert len(calls) == 1
    assert "duty_view" not in off.briefing
    assert "duty_view" in on.briefing
    keys = list(on.briefing)
    assert keys.index("duty_view") == keys.index("summary") + 1  # 요약 바로 뒤(렌더·저장 축소 순서)
    s_off, s_on = _untimed(_comparable(off)), _untimed(_comparable(on))
    s_on["briefing"] = {k: v for k, v in s_on["briefing"].items() if k != "duty_view"}
    assert list(s_on["briefing"]) == list(s_off["briefing"])  # 키 순서 동일
    assert _diff_paths(s_on, s_off) == []


def test_default_setting_is_off() -> None:
    assert make_settings().sre_duty_view_enabled is False
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "SRE_DUTY_VIEW_ENABLED=false" in example


def test_no_playbook_run_no_key() -> None:
    settings = make_settings(sre_duty_view_enabled=True, sre_playbook_enabled=False)
    job = run_job(build(settings, FakeCaller(_load("latency_sql"))), alarm_of("latency_sql"))
    assert job.status == "done"
    assert "duty_view" not in job.briefing


def test_stepless_block_returns_none() -> None:
    block = {"sections": [{"step": "S9", "action": "synthesize", "status": "pending_synthesis"}]}
    assert build_duty_view(block, MAPPING) is None
    assert build_duty_view({"sections": []}, MAPPING) is None
    assert build_duty_view(None, MAPPING) is None


def test_loader_failure_drops_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_kw: Any) -> Any:
        raise ValueError("깨진 매핑")

    monkeypatch.setattr(dispatcher_mod, "load_duty_area_map", boom)
    job = _run("latency_sql")
    assert job.status == "done" and "duty_view" not in job.briefing


def test_parse_rejects_unknown_area_code() -> None:
    import yaml

    data = yaml.safe_load(DEFAULT_DUTY_AREA_LAYERS_PATH.read_text(encoding="utf-8"))
    bad = copy.deepcopy(data)
    bad["layers"]["db"] = "network"
    with pytest.raises(ValueError):
        parse_duty_area_map(bad)
    bad = copy.deepcopy(data)
    del bad["status_labels"]["unverified"]
    with pytest.raises(ValueError):
        parse_duty_area_map(bad)


# ── 픽스처별 4상태 ──────────────────────────────────────────────────────────


def test_host_cpu() -> None:
    job = _run("host_cpu")
    assert job.briefing["playbook"]["id"] == "host.cpu"
    rows = _rows(job)
    _invariants(job.briefing["duty_view"])
    assert rows["server_os"]["status"] == ABNORMAL
    assert rows["db"]["status"] == UNVERIFIED
    assert rows["db"]["reason"] == MAPPING.areas[CODES.index("db")].no_step_reason


def test_was_heap() -> None:
    job = _run("case4_oom_periodic")
    assert job.briefing["playbook"]["id"] == "was.heap"
    rows = _rows(job)
    _invariants(job.briefing["duty_view"])
    assert rows["was_mw"]["status"] == ABNORMAL
    assert any("S" in s for s in rows["was_mw"]["steps"])
    assert rows["db"]["status"] == UNVERIFIED


def test_was_sql_connection_inconclusive() -> None:
    job = _run("latency_sql", alarm=_sql_alarm())
    assert job.briefing["playbook"]["id"] == "was.sql"
    rows = _rows(job)
    _invariants(job.briefing["duty_view"])
    for code in MAPPING.connection_inconclusive_areas:
        assert rows[code]["status"] == SUSPECT
        assert rows[code]["reason"] == "획득·실행 구분 정보 없음"
    assert "2개 영역 담당의 확인이 필요합니다" in job.briefing["duty_view"]["handoff"]


def test_step_failure_reason() -> None:
    job = _run("partial_missing")
    rows = _rows(job)
    _invariants(job.briefing["duty_view"])
    assert rows["db"]["status"] == UNVERIFIED
    assert "수집 결손" in rows["db"]["reason"]


def test_step_skipped_reason() -> None:
    job = _run("normal")
    rows = _rows(job)
    _invariants(job.briefing["duty_view"])
    assert rows["db"]["status"] == UNVERIFIED
    assert rows["db"]["reason"].endswith("생략")
    assert rows["was_mw"]["status"] == NORMAL
    dv = job.briefing["duty_view"]
    assert dv["handoff"] == MAPPING.handoff["none_with_unverified"]


def test_polestar_unlinked_reason() -> None:
    job = _run("polestar_unresolved")
    rows = _rows(job)
    assert rows["server_os"]["status"] == UNVERIFIED
    assert rows["server_os"]["reason"] == "WAS 서버의 폴스타 연결 없음"


@pytest.mark.parametrize("name", [
    "host_cpu", "host_process", "case1_jdbc_acquire", "case2_ldap_external", "case3_cpu_loop",
    "case4_oom_periodic", "gc", "latency_external", "latency_sql", "normal", "partial_missing",
    "polestar_unresolved",
])
def test_invariants_all_fixtures(name: str) -> None:
    job = _run(name)
    dv = job.briefing.get("duty_view")
    assert dv is not None, name
    _invariants(dv)


def test_uncited_normal_becomes_unverified() -> None:
    """근거 단계의 도구 인용을 지우면 「정상」이 「확인 안 됨」(인용 없음 사유)으로 바뀐다."""
    job = _run("normal")
    block = copy.deepcopy(job.briefing["playbook"])
    for sec in block["sections"]:
        sec["tools"] = []
    dv = build_duty_view(block, MAPPING)
    assert dv is not None
    assert all(r["status"] != NORMAL for r in dv["areas"])
    assert any(r.get("reason") == MAPPING.uncited_reason for r in dv["areas"])
    _invariants(dv)


# ── 커넥션 계열 우선 · 외부 연계 · R-E 프로세스 ──────────────────────────────


def _with_top_hypothesis(block: dict, rule_id: str, status: str) -> dict:
    block = copy.deepcopy(block)
    hyps = block["synthesis"]["hypotheses"]
    target = next(h for h in hyps if h["rule_id"] == rule_id)
    target["status"] = status
    block["synthesis"]["hypotheses"] = [target] + [h for h in hyps if h is not target]
    for i, h in enumerate(block["synthesis"]["hypotheses"], 1):
        h["rank"] = i
    return block


def test_connection_supported_single_area() -> None:
    block = _run("latency_sql").briefing["playbook"]
    supported = build_duty_view(_with_top_hypothesis(block, "H-DB-SHARED", "supported"), MAPPING)
    assert supported is not None
    rows = {r["code"]: r for r in supported["areas"]}
    assert rows["db"]["status"] == ABNORMAL
    assert rows["was_mw"]["status"] != ABNORMAL
    assert "reason" not in rows["db"]
    held = _with_top_hypothesis(block, "H-DB-SHARED", "inconclusive")
    inconclusive = build_duty_view(held, MAPPING)
    assert inconclusive is not None
    rows = {r["code"]: r for r in inconclusive["areas"]}
    assert rows["was_mw"]["status"] == rows["db"]["status"] == SUSPECT


def test_connection_pool_instance_points_was() -> None:
    job = _run("case1_jdbc_acquire")
    block = job.briefing["playbook"]
    if not any(h["rule_id"] == "H-POOL-INSTANCE" for h in block["synthesis"]["hypotheses"]):
        pytest.skip("픽스처에 H-POOL-INSTANCE 없음")
    dv = build_duty_view(_with_top_hypothesis(block, "H-POOL-INSTANCE", "supported"), MAPPING)
    assert dv is not None
    rows = {r["code"]: r for r in dv["areas"]}
    assert rows["was_mw"]["status"] == ABNORMAL


def test_external_maps_to_was_with_label() -> None:
    job = _run("case2_ldap_external")
    rows = _rows(job)
    was = rows["was_mw"]
    assert was["status"] in (ABNORMAL, SUSPECT)
    assert was["tag"] == "외부 연계"
    assert all(s.startswith(MAPPING.external_label) for s in was["signals"] if "외부" in s)
    assert any(s.startswith("외부 연계 — 상대 시스템 확인 필요") for s in was["signals"])
    assert "WAS/미들웨어(외부 연계)" in job.briefing["duty_view"]["handoff"]


def _with_top_process(block: dict, name: str) -> dict:
    block = copy.deepcopy(block)
    block["readings_detail"]["evidence"]["cpu_owner"]["values"]["top_process"]["name"] = name
    return block


@pytest.mark.parametrize("name,area", [
    ("db2sysc", "db"), ("/opt/mysql/bin/mysqld", "db"), ("postgres", "db"), ("tbsvr", "db"),
    ("ldapd", "server_os"), ("java", "server_os"), ("backup.sh", "server_os"),
])
def test_other_process_kind_to_area(name: str, area: str) -> None:
    block = _run("case2_ldap_external").briefing["playbook"]
    assert block["readings"]["cpu_owner"] == "other_process"
    dv = build_duty_view(_with_top_process(block, name), MAPPING)
    assert dv is not None
    rows = {r["code"]: r for r in dv["areas"]}
    assert rows[area]["status"] in (ABNORMAL, SUSPECT)
    assert any("WAS 밖 프로세스" in s or "CPU 귀속" in s for s in rows[area]["signals"])
    other = "server_os" if area == "db" else None
    if other:
        assert not any("WAS 밖 프로세스" in s for s in rows[other]["signals"])


def test_process_kind_db_only() -> None:
    assert process_kind("db2sysc", MAPPING) == "db2"
    assert process_kind("mariadbd", MAPPING) == "mariadb"
    assert process_kind("ora_pmon_ORCL", MAPPING) == "oracle"
    assert process_kind("java", MAPPING) is None
    assert process_kind("nginx", MAPPING) is None  # 웹서버는 이름만으로 판정하지 않는다
    assert process_kind(None, MAPPING) is None


# ── 문구 · 개인정보 ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name", ["host_cpu", "case2_ldap_external", "latency_sql", "case4_oom_periodic"],
)
def test_no_host_or_ip_in_signals(name: str) -> None:
    job = _run(name)
    alarm = _alarm(name)
    host = alarm["event"].get("hostname") or ""
    text = json.dumps(job.briefing["duty_view"], ensure_ascii=False)
    if host:
        assert host not in text
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert "SELECT" not in text.upper()

