"""plans/201 W7 — 사용자 매뉴얼 「사례로 배우기」(U-82·U-83·U-84) 판정 조합이 실제 판정 경로로 나오는지.

매뉴얼 원천 `scripts/manual/content/user.md` §11의 세 사례가 적은 영역별 점검 결과 조합·사유·인계 문구·
대응 제안을 실 디스패처(가짜 도구·가짜 LLM — W4 테스트와 같은 하네스)로 재현한다. 픽스처를 사례 조건에
맞게 최소로 고친다(값만 — 도구·스텝 구조는 그대로).

- U-82 host.fs: 서버/OS 이상(「파일시스템 포화 — 지지」) · WAS·DB 확인 안 됨 · 단일 인계 문구
- U-83 was.heap: WAS 이상 · 서버/OS 정상(호스트 자원 포화 none) · DB 확인 안 됨(SQL 구간 판독 없음)
- U-84 was.latency: WAS·DB 의심(「획득·실행 구분 정보 없음」) · 서버/OS 정상 · 2개 영역 인계 문구
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import FakeLLM, build, make_settings, run_job

from sre_agent.domain.duty_view import ABNORMAL, NORMAL, SUSPECT, UNVERIFIED
from sre_agent.infrastructure.duty_area_loader import load_duty_area_map

PLAN200 = Path(__file__).resolve().parent / "fixtures" / "plan200"
MAPPING = load_duty_area_map()


def _fx(name: str) -> dict:
    path = PLAN200 / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return copy.deepcopy(_load(name))


def _with_episode(alarm: dict) -> dict:
    alarm = copy.deepcopy(alarm)
    alarm.setdefault("meta", {})["episode"] = {
        "episode_id": "ep", "infra_db_ids": ["polestar"], "members": [],
    }
    return alarm


def _run(fx: dict, alarm: dict) -> Any:
    settings = make_settings(sre_duty_view_enabled=True)
    caller = FakeCaller(fx, strict=False)
    return run_job(build(settings, caller, llm=FakeLLM()), alarm)


def _rows(job: Any) -> dict[str, dict]:
    return {r["code"]: r for r in job.briefing["duty_view"]["areas"]}


def _scale_metric(fx: dict, kind: str, ceiling: float) -> None:
    """polestar_metric_trend 녹화 중 kind 행의 값을 ceiling 아래로 눌러 둔다(값만 바꾼다)."""
    for entry in fx["tool_responses"]["polestar_metric_trend"]:
        if entry["args"].get("kind") != kind:
            continue
        for row in entry["response"]["rows"]:
            for key in ("avg_val", "min_val", "max_val"):
                if isinstance(row.get(key), (int, float)):
                    row[key] = min(row[key], ceiling)


def _host_fs_fixture() -> tuple[dict, dict]:
    """host_cpu 픽스처를 파일시스템 경고 알람으로 — 파일시스템 추이 녹화를 92%로 만든다."""
    fx = _fx("host_cpu")
    trends = fx["tool_responses"]["polestar_metric_trend"]
    base = next(e for e in trends if e["args"].get("kind") == "cpu")
    fs = copy.deepcopy(base)
    fs["args"]["kind"] = "filesystem"
    fs["response"]["kind"] = "filesystem"
    for row in fs["response"]["rows"]:
        row.update(avg_val=80.0, min_val=78.0, max_val=80.0)
    for row in fs["response"]["rows"][-2:]:
        row.update(avg_val=91.0, min_val=90.5, max_val=92.0)
    trends[:] = [e for e in trends if e["args"].get("kind") != "filesystem"] + [fs]
    alarm = _with_episode(fx["alarm"])
    ev = alarm["event"]
    ev.update(alarmName="파일시스템 사용률 92%", resourceType="개별 파일시스템",
              conditions="파일시스템 사용률 > 90", conditionLog="파일시스템 사용률 92%",
              alarmId="polestar:w7_host_fs")
    alarm["decision"]["fingerprint"] = "fp-w7-host-fs"
    return fx, alarm


# ── U-82 서버/OS — 파일시스템 사용률 경고 ─────────────────────────────────────


def test_u82_host_fs_case() -> None:
    fx, alarm = _host_fs_fixture()
    job = _run(fx, alarm)
    assert job.briefing["playbook"]["id"] == "host.fs"
    rows = _rows(job)
    assert rows["server_os"]["status"] == ABNORMAL
    assert any(s.startswith("파일시스템 포화 — 지지 (S4 ←") for s in rows["server_os"]["signals"])
    assert rows["was_mw"]["status"] == UNVERIFIED
    assert rows["db"]["status"] == UNVERIFIED
    assert rows["db"]["reason"] == "이 조사는 SQL 구간 판독을 하지 않음"
    assert job.briefing["duty_view"]["handoff"] == (
        "이상 신호가 있는 영역은 서버/OS입니다 — 서버/OS 담당의 확인이 필요합니다."
    )


def test_u82_was_reason_is_not_no_step_reason() -> None:
    """관찰 고정: host.fs도 cpu_owner 판독(S4 담당)을 켜므로 WAS 사유는 「이 조사는 WAS 판독을 하지 않음」이
    아니라 R-E 사유(호스트 CPU 추이 결손)다 — 매뉴얼 본문이 WAS 사유를 단정하지 않는지 대조용."""
    fx, alarm = _host_fs_fixture()
    rows = _rows(_run(fx, alarm))
    assert rows["was_mw"]["reason"] == "호스트 CPU 추이 결손"


# ── U-83 WAS/미들웨어 — 힙 압박 이벤트 ───────────────────────────────────────


def test_u83_was_heap_case() -> None:
    fx = _fx("case4_oom_periodic")
    _scale_metric(fx, "memory", 70.0)   # 사례 조건: 호스트 메모리는 포화 기준(90%) 아래
    job = _run(fx, _with_episode(fx["alarm"]))
    assert job.briefing["playbook"]["id"] == "was.heap"
    rows = _rows(job)
    assert rows["was_mw"]["status"] == ABNORMAL
    assert rows["was_mw"]["signals"][0].startswith("주기성 적재(주기적 힙 급증) — 지지 (S2 ←")
    assert rows["server_os"]["status"] == NORMAL
    assert rows["server_os"]["signals"][0].startswith("호스트 자원 포화 none (S4 ←")
    assert rows["db"]["status"] == UNVERIFIED
    assert rows["db"]["reason"] == "이 조사는 SQL 구간 판독을 하지 않음"
    assert job.briefing["duty_view"]["handoff"] == (
        "이상 신호가 있는 영역은 WAS/미들웨어입니다 — WAS/미들웨어 담당의 확인이 필요합니다."
    )


def test_u83_recommendation_first_lines() -> None:
    """매뉴얼 「런북 대응 제안 첫 줄은 『재기동 전에 힙 덤프를 확보』」 대조 — 실제 첫 줄은 triage_first 힌트."""
    fx = _fx("case4_oom_periodic")
    job = _run(fx, _with_episode(fx["alarm"]))
    items = job.briefing["recommendation"]["items"]
    assert items[0] == "재기동 전에 힙 덤프를 확보한다"   # response_hints.triage_first
    assert items[1] == "재기동 전에 힙 덤프를 확보"       # runbook mitigation 첫 항목


# ── U-84 DB — 응답 지연인데 SQL 구간이 큰 경우 ───────────────────────────────


def test_u84_was_latency_sql_case() -> None:
    fx = _fx("latency_sql")
    job = _run(fx, _with_episode(fx["alarm"]))
    assert job.briefing["playbook"]["id"] == "was.latency"
    rows = _rows(job)
    for code in ("was_mw", "db"):
        assert rows[code]["status"] == SUSPECT
        assert rows[code]["reason"] == "획득·실행 구분 정보 없음"
        assert rows[code]["steps"] == ["S3", "S0"]
    assert rows["server_os"]["status"] == NORMAL
    assert rows["server_os"]["signals"][0].startswith("호스트 자원 포화 none (S4 ←")
    assert job.briefing["duty_view"]["handoff"] == (
        "의심 신호가 있는 영역은 WAS/미들웨어·DB입니다 — 2개 영역 담당의 확인이 필요합니다."
    )
