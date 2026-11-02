"""plans/201 W4 독립 검증(verifier) — 조사 결과 영역별 판정 `duty_view` (D-354).

구현자 테스트(`test_plan201_w4_duty_view.py`)와 독립으로 다음을 고정한다.
1. 플래그 off 비트 동일 — **기준선(a289818f) `briefing_builder` 원본**과 현재 빌더를 같은 실제 인자로 불러
   dict(키·순서·값)를 대조한다. on은 `duty_view` 하나만 더한다. off면 매핑 파일을 열지 않는다.
2. 근거 없는 normal 0건 — 실제 러너 산출 블록을 조합 변형(단계 상태·도구·판독 값·가설 상태)해 속성 단언.
3. 구현이 읽는 키가 실제 러너 산출 블록에 있다(mock 형태 아님).
4. 커넥션 가설 우선(rank 정렬 · 반박 제외 · db 계층 재적용 안 함) · external 꼬리표.
5. R-E other_process 프로세스 이름 → 영역.
지적 재현은 xfail(strict)로 고정한다.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from sre_agent.application import briefing_builder as bb_mod
from sre_agent.application import investigation_dispatcher as dispatcher_mod
from sre_agent.domain.duty_view import (
    ABNORMAL,
    NORMAL,
    STATUSES,
    SUSPECT,
    UNVERIFIED,
    build_duty_view,
)
from sre_agent.domain.engine_agreement import READING_LAYERS
from sre_agent.infrastructure import duty_area_loader
from sre_agent.infrastructure.duty_area_loader import load_duty_area_map

from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import FakeLLM, build, make_settings, run_job

_TOP = Path(__file__).resolve().parents[1]  # collectorinfra/sre_agent
_REPO = _TOP.parent
_PLAN200 = Path(__file__).resolve().parent / "fixtures" / "plan200"
BASE_SHA = "a289818f"
MAPPING = load_duty_area_map()

FIXTURES = [
    "host_cpu", "host_process", "case1_jdbc_acquire", "case2_ldap_external", "case3_cpu_loop",
    "case4_oom_periodic", "gc", "latency_external", "latency_sql", "normal", "partial_missing",
    "polestar_unresolved",
]


def _fixture(name: str) -> dict:
    path = _PLAN200 / f"{name}.json"
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


def _run(name: str, *, on: bool = True) -> Any:
    settings = make_settings(sre_duty_view_enabled=on)
    caller = FakeCaller(_fixture(name), strict=False)
    return run_job(build(settings, caller, llm=FakeLLM()), _alarm(name))


_BLOCKS: dict[str, dict] = {}


def _block(name: str) -> dict:
    """실제 러너(가짜 도구·가짜 LLM)가 만든 `playbook` 블록 — 깊은 사본."""
    if name not in _BLOCKS:
        _BLOCKS[name] = _run(name).briefing["playbook"]
    return copy.deepcopy(_BLOCKS[name])


def _rows(dv: dict) -> dict[str, dict]:
    return {r["code"]: r for r in dv["areas"]}


def _invariants(dv: dict, block: dict) -> None:
    """상태 4값 · normal은 ok 단계 도구 인용 필수 · abnormal도 인용 필수 · unverified 사유 필수."""
    secs = {s.get("step"): s for s in block.get("sections") or [] if isinstance(s, dict)}
    for row in dv["areas"]:
        assert row["status"] in STATUSES, row
        if row["status"] == NORMAL:
            assert row["signals"], row
            for sig in row["signals"]:
                assert "←" in sig, row
                cited = sig.rsplit("←", 1)[1].rstrip(")").strip()
                tools = [t.strip() for t in cited.split(",") if t.strip()]
                assert tools, row
                ok_tools = {
                    t for s in secs.values() if s.get("status") == "ok" for t in s.get("tools") or []
                }
                assert set(tools) <= ok_tools, (row, ok_tools)
            assert row["steps"], row
            assert any(secs.get(s, {}).get("status") == "ok" for s in row["steps"]), row
        if row["status"] == ABNORMAL:
            assert any("←" in s for s in row["signals"]), row
        if row["status"] == UNVERIFIED:
            assert isinstance(row.get("reason"), str) and row["reason"].strip(), row
    assert isinstance(dv["handoff"], str) and dv["handoff"]


# ── 1. 플래그 off 비트 동일(기준선 원본 대조) ──────────────────────────────────


def _baseline_builder() -> Any:
    try:
        src = subprocess.run(
            ["git", "show", f"{BASE_SHA}:sre_agent/sre_agent/application/briefing_builder.py"],
            cwd=_REPO, capture_output=True, check=True, timeout=30,
        ).stdout.decode("utf-8")
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git 기준선 조회 불가")
    spec = importlib.util.spec_from_loader("_w4v_baseline_briefing_builder", loader=None)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    exec(compile(src, "baseline_briefing_builder.py", "exec"), mod.__dict__)  # noqa: S102
    return mod


@pytest.mark.parametrize("name", FIXTURES)
def test_off_bit_identical_to_baseline_builder(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _baseline_builder()
    captured: list[tuple[dict, dict]] = []
    real = dispatcher_mod.build_playbook_briefing

    def spy(**kw: Any) -> dict:
        out = real(**kw)
        captured.append((copy.deepcopy(kw), out))
        return out

    monkeypatch.setattr(dispatcher_mod, "build_playbook_briefing", spy)
    for on in (False, True):
        captured.clear()
        _run(name, on=on)
        assert captured, "디스패처가 플레이북 브리핑을 만들지 않았다"
        for kw, out in captured:
            assert ("duty_areas" in kw) and ((kw["duty_areas"] is None) is (not on))
            kw.pop("duty_areas")
            expect = base.build_playbook_briefing(**copy.deepcopy(kw))
            got = out if not on else {k: v for k, v in out.items() if k != "duty_view"}
            # 키·순서·값 비트 동일(JSON 직렬화 문자열 대조)
            assert json.dumps(got, ensure_ascii=False, default=str) == json.dumps(
                expect, ensure_ascii=False, default=str
            )
            if on:
                keys = list(out)
                if "duty_view" in out:
                    assert keys.index("duty_view") == keys.index("summary") + 1


def test_off_does_not_open_mapping_file(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    real_read = Path.read_text

    def spy(self: Path, *a: Any, **kw: Any) -> str:
        if self.name == "duty_area_layers.yaml":
            opened.append(str(self))
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", spy)
    d = build(make_settings(sre_duty_view_enabled=False), FakeCaller(_fixture("latency_sql"), strict=False),
              llm=FakeLLM())
    assert opened == [] and d._duty_areas is None
    build(make_settings(sre_duty_view_enabled=True), FakeCaller(_fixture("latency_sql"), strict=False),
          llm=FakeLLM())
    assert len(opened) == 1


def test_env_flag_name_is_sre_duty_view_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SRE_DUTY_VIEW_ENABLED", "true")
    assert make_settings().sre_duty_view_enabled is True
    monkeypatch.setenv("SRE_DUTY_VIEW_ENABLED", "false")
    assert make_settings().sre_duty_view_enabled is False


def test_builder_none_mapping_is_identity() -> None:
    """빌더 직접 호출 — duty_areas=None과 인자 생략이 같은 결과(키 없음)."""
    block = _block("latency_sql")
    from sre_agent.domain.severity_signatures import ImportanceVerdict

    verdict = ImportanceVerdict(level="주의", confidence="medium", escalate=False)
    a = bb_mod.build_playbook_briefing(playbook=copy.deepcopy(block), verdict=verdict)
    b = bb_mod.build_playbook_briefing(playbook=copy.deepcopy(block), verdict=verdict, duty_areas=None)
    assert json.dumps(a, ensure_ascii=False, default=str) == json.dumps(b, ensure_ascii=False, default=str)
    assert "duty_view" not in a


# ── 3. 실제 산출 키 이름 대조 ──────────────────────────────────────────────


@pytest.mark.parametrize("name", FIXTURES)
def test_reader_keys_exist_in_real_runner_block(name: str) -> None:
    block = _block(name)
    secs = block["sections"]
    assert isinstance(secs, list) and secs
    for s in secs:
        assert {"step", "action", "status", "tools", "title"} <= set(s), s
    assert isinstance(block["readings"], dict)
    ev = block["readings_detail"]["evidence"]
    for reading, _table in READING_LAYERS:
        assert reading in block["readings"], reading
        assert reading in ev and "steps" in ev[reading] and "reason" in ev[reading], reading
    for h in block["synthesis"]["hypotheses"]:
        assert {"rank", "title", "status", "status_label", "evidence_steps", "rule_id"} <= set(h), h
    assert "scope" in block and isinstance(block["scope"], dict)
    if name != "polestar_unresolved":
        assert block["scope"].get("polestar_source"), block["scope"]
    else:
        assert not block["scope"].get("polestar_source")
    # 매핑의 판독·가설 이름이 실제 규칙표·판독표 어휘 안에 있다(오타로 인한 조용한 미적용 방지)
    from sre_agent.domain.playbook_hypotheses import HYPOTHESIS_RULES

    rule_ids = {r.id for r in HYPOTHESIS_RULES}
    assert set(MAPPING.connection_hypotheses) <= rule_ids
    assert set(MAPPING.hypothesis_areas) <= rule_ids
    assert MAPPING.external_hypotheses <= rule_ids
    reading_names = {n for n, _ in READING_LAYERS}
    assert set(MAPPING.reading_areas) <= reading_names
    assert set(MAPPING.reading_labels) >= reading_names


def test_top_process_name_path_matches_runner() -> None:
    """R-E 상위 프로세스 이름이 실제 증거 `readings_detail.evidence.cpu_owner.values.top_process.name`에 있다."""
    block = _block("case2_ldap_external")
    top = block["readings_detail"]["evidence"]["cpu_owner"]["values"]["top_process"]
    assert isinstance(top.get("name"), str) and top["name"]


# ── 2. 근거 없는 normal 0건(조합 생성) ─────────────────────────────────────


_SEC_STATUSES = ("ok", "missing", "skipped", "failed")
_HYP_STATUSES = ("supported", "inconclusive", "refuted")


def _reading_choices(name: str) -> list[Any]:
    table = dict(READING_LAYERS)[name]
    return [*table, "none", "normal", "other", "n/a", None]


def _mutate(block: dict, rng: random.Random) -> dict:
    b = copy.deepcopy(block)
    for s in b["sections"]:
        if s.get("action") == "synthesize":
            continue
        r = rng.random()
        if r < 0.25:
            s["status"] = rng.choice(_SEC_STATUSES[1:])
        if rng.random() < 0.25:
            s["tools"] = []
    for name, _ in READING_LAYERS:
        if rng.random() < 0.5:
            b["readings"][name] = rng.choice(_reading_choices(name))
    for h in b["synthesis"]["hypotheses"]:
        if rng.random() < 0.5:
            h["status"] = rng.choice(_HYP_STATUSES)
    if rng.random() < 0.3:
        rng.shuffle(b["synthesis"]["hypotheses"])
    if rng.random() < 0.2:
        b["scope"] = {k: v for k, v in b["scope"].items() if k != "polestar_source"}
    return b


@pytest.mark.parametrize("name", FIXTURES)
def test_no_uncited_normal_property(name: str) -> None:
    base = _block(name)
    dv0 = build_duty_view(base, MAPPING)
    assert dv0 is not None
    _invariants(dv0, base)
    rng = random.Random(f"w4v-{name}")
    for _ in range(300):
        b = _mutate(base, rng)
        dv = build_duty_view(b, MAPPING)
        if dv is None:
            continue
        _invariants(dv, b)


def test_all_tools_removed_no_normal_any_fixture() -> None:
    for name in FIXTURES:
        b = _block(name)
        for s in b["sections"]:
            s["tools"] = []
        dv = build_duty_view(b, MAPPING)
        assert dv is not None
        assert all(r["status"] not in (NORMAL, ABNORMAL) for r in dv["areas"]), name


def test_stepless_job_has_no_key() -> None:
    """수집 단계가 하나도 없으면(HolmesGPT 단독 등) 키 없음."""
    b = _block("latency_sql")
    b["sections"] = [s for s in b["sections"] if s.get("action") == "synthesize"]
    assert build_duty_view(b, MAPPING) is None


# ── 4. 커넥션 계열 우선 · external ───────────────────────────────────────────


def _set_hyps(block: dict, items: list[tuple[str, str, int]]) -> dict:
    """가설 목록을 (rule_id, status, rank)로 다시 쓴다 — 제목·인용 단계는 규칙표 값."""
    from sre_agent.domain.playbook_hypotheses import HYPOTHESIS_RULES, STATUS_LABELS

    rules = {r.id: r for r in HYPOTHESIS_RULES}
    block["synthesis"]["hypotheses"] = [
        {"rank": rank, "title": rules[rid].title, "status": st, "status_label": STATUS_LABELS[st],
         "evidence_steps": list(rules[rid].evidence_steps), "rule_id": rid}
        for rid, st, rank in items
    ]
    return block


def test_connection_top_by_rank_not_list_order() -> None:
    b = _set_hyps(_block("latency_sql"), [
        ("H-POOL-INSTANCE", "supported", 2),
        ("H-DB-SHARED", "supported", 1),
    ])
    rows = _rows(build_duty_view(b, MAPPING))
    assert rows["db"]["status"] == ABNORMAL
    assert rows["was_mw"]["status"] != ABNORMAL  # 2순위는 옮기지 않는다


def test_connection_refuted_only_falls_back_to_layers() -> None:
    b = _set_hyps(_block("latency_sql"), [
        ("H-DB-SHARED", "refuted", 1), ("H-DB-INSTANCE", "refuted", 2),
    ])
    assert b["readings"]["dominant"] == "sql"
    rows = _rows(build_duty_view(b, MAPPING))
    assert rows["db"]["status"] == SUSPECT  # 계층표(dominant=sql → db)
    assert any("응답 지배 구간 sql" in s for s in rows["db"]["signals"])
    assert rows["was_mw"]["status"] != SUSPECT or not any(
        "판단 보류" in s for s in rows["was_mw"]["signals"]
    )


def test_connection_used_skips_db_layer() -> None:
    b = _set_hyps(_block("latency_sql"), [("H-POOL-INSTANCE", "supported", 1)])
    b["readings"]["db_split"] = "acquire"
    rows = _rows(build_duty_view(b, MAPPING))
    assert rows["was_mw"]["status"] == ABNORMAL
    assert not any("응답 지배 구간" in s or "커넥션 획득" in s for s in rows["db"]["signals"])
    assert rows["db"]["status"] != SUSPECT


def test_connection_skipped_db_reason_is_not_uncited() -> None:
    b = _set_hyps(_block("latency_sql"), [("H-POOL-INSTANCE", "supported", 1)])
    b["readings"]["db_split"] = "acquire"
    rows = _rows(build_duty_view(b, MAPPING))
    assert rows["db"]["status"] == UNVERIFIED
    assert rows["db"]["reason"] != MAPPING.uncited_reason


def test_inconclusive_reason_not_claimed_when_db_split_known() -> None:
    b = _set_hyps(_block("latency_sql"), [
        ("H-DB-SHARED", "inconclusive", 1), ("H-DB-INSTANCE", "inconclusive", 2),
    ])
    b["readings"]["db_split"] = "execute"
    b["readings"]["scope"] = None
    rows = _rows(build_duty_view(b, MAPPING))
    assert rows["db"].get("reason") != MAPPING.connection_inconclusive_reason


def test_connection_inconclusive_both_suspect_with_reason() -> None:
    b = _set_hyps(_block("case1_jdbc_acquire"), [("H-POOL-INSTANCE", "inconclusive", 1)])
    rows = _rows(build_duty_view(b, MAPPING))
    for code in ("was_mw", "db"):
        assert rows[code]["status"] == SUSPECT
        assert rows[code]["reason"] == "획득·실행 구분 정보 없음"


def test_external_tag_only_on_was_and_label_prefix() -> None:
    for name in ("case2_ldap_external", "latency_external"):
        dv = build_duty_view(_block(name), MAPPING)
        rows = _rows(dv)
        assert rows["was_mw"].get("tag") == "외부 연계"
        assert rows["server_os"].get("tag") is None and rows["db"].get("tag") is None
        ext = [s for s in rows["was_mw"]["signals"] if "외부 연계 지연" in s or "external" in s]
        assert ext and all(s.startswith("외부 연계 — 상대 시스템 확인 필요") for s in ext)
        assert "(외부 연계)" in dv["handoff"]


def test_handoff_has_no_org_or_person_names() -> None:
    for name in FIXTURES:
        dv = build_duty_view(_block(name), MAPPING)
        h = dv["handoff"]
        for word in ("팀", "님", "담당자", "은행", "MSP", "통합"):
            assert word not in h, (name, h)


def test_cpu_owner_none_with_anomalous_host_not_normal() -> None:
    b = _block("host_cpu")
    b["readings"]["cpu_owner"] = "none"
    b["readings"]["host_saturation"] = "none"
    vals = b["readings_detail"]["evidence"]["cpu_owner"]["values"]
    vals.update({"host_cpu_anomalous": True, "host_cpu_peak": 75.0, "was_cpu_max": 60.0})
    b["synthesis"]["hypotheses"] = []
    rows = _rows(build_duty_view(b, MAPPING))
    assert rows["server_os"]["status"] != NORMAL, rows["server_os"]


# ── 5. R-E other_process ────────────────────────────────────────────────────


def _other_process(name: Any) -> dict:
    b = _block("case2_ldap_external")
    assert b["readings"]["cpu_owner"] == "other_process"
    top = b["readings_detail"]["evidence"]["cpu_owner"]["values"]
    if name is None:
        top.pop("top_process", None)
    else:
        top["top_process"]["name"] = name
    return b


@pytest.mark.parametrize("pname,kind", [
    ("db2sysc", "db2"), ("/usr/sbin/mysqld", "mysql"), ("mariadbd", "mariadb"),
    ("postgres", "postgresql"), ("/usr/pgsql-15/bin/postmaster", "postgresql"),
    ("ora_pmon_ORCL", "oracle"), ("oracleORCL", "oracle"), ("tbsvr", "tibero"),
])
def test_other_process_db_engines_to_db(pname: str, kind: str) -> None:
    rows = _rows(build_duty_view(_other_process(pname), MAPPING))
    assert rows["db"]["status"] in (ABNORMAL, SUSPECT)
    assert any(f"상위 프로세스 종류 {kind}" in s for s in rows["db"]["signals"])
    assert not any("WAS 밖 프로세스" in s for s in rows["server_os"]["signals"])


def test_other_process_db_engine_server_os_not_normal_by_cpu_owner() -> None:
    rows = _rows(build_duty_view(_other_process("db2sysc"), MAPPING))
    assert not (rows["server_os"]["status"] == NORMAL
                and any("CPU 귀속 other_process" in s for s in rows["server_os"]["signals"]))


@pytest.mark.parametrize("pname", ["java", "nginx", "mysqld_safe", "oracleasm", "psql", "postgres.exe"])
def test_other_process_non_db_to_server_os_unidentified(pname: str) -> None:
    rows = _rows(build_duty_view(_other_process(pname), MAPPING))
    assert rows["server_os"]["status"] in (ABNORMAL, SUSPECT)
    assert any(MAPPING.process_unidentified_note in s for s in rows["server_os"]["signals"])
    assert not any("상위 프로세스 종류" in s and "미식별" not in s for s in rows["db"]["signals"])


@pytest.mark.parametrize("pname", [None, ""])
def test_other_process_missing_name_note(pname: Any) -> None:
    rows = _rows(build_duty_view(_other_process(pname), MAPPING))
    assert rows["server_os"]["status"] in (ABNORMAL, SUSPECT)
    assert any(MAPPING.process_missing_note in s for s in rows["server_os"]["signals"])


def test_signals_do_not_carry_process_name_or_host() -> None:
    """근거 문자열에 프로세스 이름 원문(인자 포함 가능)·서버명이 실리지 않는다 — kind 이름만."""
    rows = _rows(build_duty_view(_other_process("/opt/secret-host01/bin/db2sysc"), MAPPING))
    text = json.dumps(rows, ensure_ascii=False)
    assert "secret-host01" not in text


# ── 로더 경로 ───────────────────────────────────────────────────────────────


def test_loader_default_path_is_package_config() -> None:
    assert duty_area_loader.DEFAULT_DUTY_AREA_LAYERS_PATH == _TOP / "config" / "duty_area_layers.yaml"
    assert duty_area_loader.DEFAULT_DUTY_AREA_LAYERS_PATH.is_file()
